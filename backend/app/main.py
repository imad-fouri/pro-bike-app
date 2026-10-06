"""CycleCoach API — app factory with versioned routing, security, logging."""

import logging
import time

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware

from app.api.v1 import v1
from app.core.config import settings
from app.core.errors import register_error_handlers
from app.core.logging import new_request_id, setup_logging
from app.core.metrics import record_request

setup_logging(settings.LOG_LEVEL, settings.ENVIRONMENT)


def _status_class(status_code: int) -> str:
    """Coarse outcome class. Metric dimensions stay small on purpose.

    The exact code is available in logs and traces; a metric wants the class so
    an operator asks "are errors increasing", not "which of the 40 codes".
    """
    return f"{status_code // 100}xx"


def _route_template(request: Request) -> str:
    """The MATCHED route pattern, never the concrete path.

    `/api/v1/rides/8f3a2b7c-...` is one metric series per ride, forever;
    `/api/v1/rides/{ride_id}` is one series. This is the single most important
    cardinality rule in the metrics design, and `route.path` must never be used
    instead — the UUID in it would become an unbounded label.

    Falls back to the raw path only when no route matched (a 404 at the root),
    which cannot carry an identifier because the router never matched it.
    """
    route = request.scope.get("route")
    pattern = getattr(route, "path", None)
    if isinstance(pattern, str) and pattern:
        return pattern
    return request.url.path or "unknown"


def create_app() -> FastAPI:
    # Fail closed before serving anything. Phase 10 widened this from the
    # SECRET_KEY-only check to the full production gate: an unsafe default
    # anywhere (dev email provider, cleartext CORS, DEBUG logging, the shipped
    # database URL, an unauthenticated remote Redis holding live positions) now
    # stops the process instead of serving production traffic.
    settings.validate_production()
    # Phase 10 security audit: `/openapi.json` is the complete route table —
    # every endpoint, parameter and schema — and needs no authentication. Left on
    # in production it hands an attacker the map of the API for free, including
    # the Phase 9 group-ride and location surfaces whose safety depends on nobody
    # knowing the shape in advance.
    #
    # Development and test keep both, because the contract tests read the schema
    # and losing it locally would be a real cost.
    is_production = settings.is_production
    app = FastAPI(
        title="CycleCoach API",
        description="Worldwide cycling platform — Phase 1 foundation.",
        version=settings.APP_VERSION,
        docs_url=None if is_production else "/docs",
        redoc_url=None if is_production else "/redoc",
        openapi_url=None if is_production else "/openapi.json",
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list(),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    if settings.is_production:
        app.add_middleware(TrustedHostMiddleware, allowed_hosts=["*.cyclecoach.app"])

    @app.middleware("http")
    async def correlation(request: Request, call_next):  # type: ignore[no-untyped-def]
        # Accept a client-supplied id only in a sanitized shape (our own ids
        # are 12 hex chars); otherwise mint one. The SAME id goes into the
        # logs and the response header, so a reported id is searchable.
        # ASCII-only: a non-ASCII or header-breaking value is dropped rather
        # than reflected back into a response header.
        raw = (request.headers.get("X-Request-ID") or "").strip()[:64]
        shape = raw.replace("-", "").replace("_", "")
        incoming = raw if shape.isascii() and shape.isalnum() else ""
        rid = new_request_id(incoming or None)
        # Published on the request as well as the response, so the unhandled-exception
        # handler can put the same id on a 500 — see app/core/errors.py.
        request.state.request_id = rid
        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            # An unhandled error becomes a 500 via ServerErrorMiddleware, which sits
            # outside this middleware. Counting it HERE is the only place the
            # duration and the outcome are both still known — by the time the error
            # handler runs, the route and the elapsed time are gone.
            _record_request(request, time.perf_counter() - started, "5xx")
            raise
        _record_request(
            request,
            time.perf_counter() - started,
            _status_class(response.status_code),
        )
        response.headers["X-Request-ID"] = rid
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    register_error_handlers(app)
    app.include_router(v1, prefix=settings.API_PREFIX)
    return app


def _record_request(request: Request, elapsed_seconds: float, status_class: str) -> None:
    """Record one request. Never raises into the request path.

    A metrics bug must not become a 500. `record_request` refuses a label it
    considers unbounded or identifying, and a refusal must cost a metric rather
    than a rider's response: losing a dashboard point is cheaper than failing a
    login over it.
    """
    try:
        record_request(
            method=request.method,
            route=_route_template(request),
            status_class=status_class,
            duration_ms=elapsed_seconds * 1000,
        )
    except Exception as exc:  # noqa: BLE001 - never break a request over a metric
        # Logged, not raised. A cardinality refusal is a CALL-SITE bug, and it has
        # to be visible: silently dropping it is how "the dashboard is empty"
        # becomes a multi-day mystery. The message names no label value, so a
        # refusal cannot itself leak an identifier into the log.
        logging.getLogger("cyclecoach").warning(
            "metrics_record_failed", extra={"error_type": type(exc).__name__}
        )


app = create_app()

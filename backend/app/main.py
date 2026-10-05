"""CycleCoach API — app factory with versioned routing, security, logging."""

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware

from app.api.v1 import v1
from app.core.config import settings
from app.core.errors import register_error_handlers
from app.core.logging import new_request_id, setup_logging

setup_logging(settings.LOG_LEVEL, settings.ENVIRONMENT)


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
        response = await call_next(request)
        response.headers["X-Request-ID"] = rid
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    register_error_handlers(app)
    app.include_router(v1, prefix=settings.API_PREFIX)
    return app


app = create_app()

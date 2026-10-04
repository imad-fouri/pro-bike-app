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
    # Fail closed before serving anything: a production process must never
    # sign tokens with a missing, placeholder, or short SECRET_KEY.
    settings.require_production_secrets()
    app = FastAPI(
        title="CycleCoach API",
        description="Worldwide cycling platform — Phase 1 foundation.",
        version=settings.APP_VERSION,
        docs_url="/docs",
        openapi_url="/openapi.json",
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
        response = await call_next(request)
        response.headers["X-Request-ID"] = rid
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    register_error_handlers(app)
    app.include_router(v1, prefix=settings.API_PREFIX)
    return app


app = create_app()

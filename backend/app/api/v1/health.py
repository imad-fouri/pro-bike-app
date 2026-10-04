"""Health endpoint — the only business route in Phase 1."""

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.core.config import settings
from app.db.session import check_db
from app.redis.client import check_redis

router = APIRouter(tags=["health"])


class HealthOut(BaseModel):
    status: str
    service: str
    version: str
    database: str
    redis: str


@router.get("/health", response_model=HealthOut)
async def health() -> HealthOut:
    """Liveness: the process answers. Always 200 — never gate traffic on it."""
    db = "up" if await check_db() else "down"
    rd = "up" if await check_redis() else "down"
    return HealthOut(
        status="ok",
        service=settings.APP_NAME,
        version=settings.APP_VERSION,
        database=db,
        redis=rd,
    )


@router.get("/ready")
async def ready() -> JSONResponse:
    """Readiness: 200 only when PostgreSQL answers, else 503.

    Point orchestrator readiness probes here, not at `/health`: a process
    that cannot reach PostgreSQL must stop receiving traffic, while `/health`
    stays a pure liveness signal.

    Redis is REPORTED but does not gate: no request path uses Redis today
    (health-check only), so a Redis blip must not pull a healthy API out of
    rotation. Revisit when the worker or a shared limiter depends on it.
    """
    db = "up" if await check_db() else "down"
    rd = "up" if await check_redis() else "down"
    ok = db == "up"
    return JSONResponse(
        status_code=200 if ok else 503,
        content={
            "status": "ready" if ok else "not_ready",
            "service": settings.APP_NAME,
            "version": settings.APP_VERSION,
            "database": db,
            "redis": rd,
        },
    )

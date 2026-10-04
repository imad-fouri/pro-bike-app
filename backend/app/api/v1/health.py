"""Health endpoint — the only business route in Phase 1."""

from fastapi import APIRouter
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
    db = "up" if await check_db() else "down"
    rd = "up" if await check_redis() else "down"
    return HealthOut(
        status="ok",
        service=settings.APP_NAME,
        version=settings.APP_VERSION,
        database=db,
        redis=rd,
    )

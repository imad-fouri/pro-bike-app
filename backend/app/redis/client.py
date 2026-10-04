"""Redis connection abstraction + health check. No caching logic in Phase 1."""

import redis.asyncio as aioredis

from app.core.config import settings

_client: aioredis.Redis | None = None


def get_redis() -> aioredis.Redis:
    global _client
    if _client is None:
        _client = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
    return _client


async def check_redis() -> bool:
    try:
        await get_redis().ping()
        return True
    except Exception:  # noqa: BLE001 - health check must never raise
        return False

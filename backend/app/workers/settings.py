"""ARQ worker settings — no business jobs in Phase 1 (stub proves wiring)."""

from typing import ClassVar


async def ping(ctx: dict) -> str:
    return "pong"


class WorkerSettings:
    functions: ClassVar[list] = [ping]
    redis_settings = None  # bound from REDIS_URL at runtime in later phases

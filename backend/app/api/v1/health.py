"""Health and readiness endpoints.

Two probes with deliberately different jobs, because conflating them is how a
recoverable dependency outage becomes an outage of the whole service:

* `/health` is **liveness**: it never fails because a dependency is down. An
  orchestrator restarts a container when liveness fails, so a probe that returned
  an error during a database blip would crash-loop every replica at once and
  spread the outage from one component to all of them. It does still *report*
  dependency status, which is the Phase 8.6 contract: an operator reading a probe
  wants to see `database: down` without the probe itself failing.
* `/ready` is **readiness**: "should this process receive traffic?" It returns
  503 when PostgreSQL is unreachable.

Phase 10 changed one thing here: both dependency checks are now bounded by
[app.api.v1.health._DEPENDENCY_CHECK_TIMEOUT_SECONDS]. Previously a database that
*HUNG* — reachable, not refusing — made the probe hang until the orchestrator's own
timeout, and an orchestrator that times out a liveness probe kills the process.
That turned a slow database into a restart of healthy processes, which is the exact
failure the split above exists to prevent. A dependency that hangs now reports
`timeout`, which is a different operator problem from `down` and is reported as
such.
"""

import asyncio

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.core.config import settings
from app.db.session import check_db
from app.redis.client import check_redis

router = APIRouter(tags=["health"])

#: Ceiling on a single dependency check.
#:
#: Without it, an unresponsive-but-not-refusing PostgreSQL makes both probes hang
#: until the orchestrator's own timeout, which is indistinguishable from a failed
#: probe — and a failed *liveness* probe is a restart.
_DEPENDENCY_CHECK_TIMEOUT_SECONDS = 2.0


class HealthOut(BaseModel):
    status: str
    service: str
    version: str
    database: str
    redis: str


async def _bounded(check) -> str:
    """Run a dependency check, reporting `timeout` instead of hanging.

    `check_db` and `check_redis` return bool, so a caller cannot tell "refused"
    from "hung" without supplying a deadline of its own. Keeping the deadline in
    one place means both probes agree on the bound and on the vocabulary.
    """
    try:
        result = await asyncio.wait_for(check(), timeout=_DEPENDENCY_CHECK_TIMEOUT_SECONDS)
        return "up" if result else "down"
    except TimeoutError:  # asyncio.TimeoutError is an alias of this on 3.11+
        return "timeout"
    except Exception:  # noqa: BLE001 — a probe reports, it never raises
        return "down"


@router.get("/health", response_model=HealthOut)
async def health() -> HealthOut:
    """Liveness: the process is running. Always 200, whatever the dependencies do.

    Dependency status is reported but never gates the status code. `timeout` and
    `down` are kept distinct because they are different failures to diagnose.
    """
    db = await _bounded(check_db)
    rd = await _bounded(check_redis)
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

    Point orchestrator readiness probes here, not at `/health`: a process that
    cannot reach PostgreSQL must stop receiving traffic, while `/health` stays a
    pure liveness signal.

    Redis is REPORTED but does not gate, and that is a deliberate decision rather
    than an oversight. Phase 9 gave Redis a real request path — the group-ride
    location endpoints — and those endpoints already answer 503 when Redis is
    unreachable, so a Redis outage degrades exactly the feature that depends on it
    instead of the whole API. Gating readiness on Redis would take every route,
    including ride recording, out of rotation for a dependency that only the live
    map needs. The rate limiter is in-process, so nothing else needs Redis either.

    An earlier version of this comment said "no request path uses Redis today
    (health-check only)". Phase 9 made that false; the reasoning above is the
    corrected version and the conclusion is unchanged.

    Revisit if a shared limiter or a worker ever depends on Redis.
    """
    db = await _bounded(check_db)
    rd = await _bounded(check_redis)
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

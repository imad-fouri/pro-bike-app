"""Phase 10 health/readiness contract (WS-N, WS-Q).

The distinction under test is the one that decides whether a dependency outage
stays one outage:

* `/health` is liveness. It must answer even when PostgreSQL and Redis are down,
  hanging, or raising, because an orchestrator that fails liveness RESTARTS the
  process — so a liveness probe that depends on the database converts a database
  blip into a simultaneous crash loop of every replica.
* `/ready` is readiness. It must report 503 when PostgreSQL is unreachable, and it
  must never hang, so a slow replica leaves rotation for the right reason.

Every test here drives the probe against a *broken* dependency. A test that only
ever sees a healthy system proves nothing about either contract.
"""

import asyncio

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.v1 import health as health_module


@pytest.fixture(autouse=True)
def restore_dependency_checks():
    """Undo the module-level patching this file performs.

    `health.py` imports `check_db`/`check_redis` into its own namespace, so the
    only way to script them is to rebind those names. Without restoring them, a
    later test that exercises the real `/ready` would inherit a fake and assert
    against fiction — the same event-loop leak that made Phase 9's Redis fixture
    order-dependent, and for the same reason.
    """
    original_db = health_module.check_db
    original_redis = health_module.check_redis
    try:
        yield
    finally:
        health_module.check_db = original_db
        health_module.check_redis = original_redis


def app_with_patched_health(db_result: str, redis_result: str = "up") -> FastAPI:
    """A minimal app whose `/ready` sees scripted dependency outcomes.

    `db_result`/`redis_result` of "raise" makes the check explode and "hang"
    makes it never return, which are the two failure shapes a liveness probe
    actually dies from.
    """

    async def fake_db() -> bool:
        if db_result == "raise":
            raise RuntimeError("postgres is on fire")
        if db_result == "hang":
            await asyncio.sleep(3600)
        return db_result == "up"

    async def fake_redis() -> bool:
        if redis_result == "raise":
            raise RuntimeError("redis is on fire")
        if redis_result == "hang":
            await asyncio.sleep(3600)
        return redis_result == "up"

    health_module.check_db = fake_db  # type: ignore[assignment]
    health_module.check_redis = fake_redis  # type: ignore[assignment]

    app = FastAPI()
    app.include_router(health_module.router)
    return app


async def call(app: FastAPI, path: str, timeout: float = 10.0):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        return await asyncio.wait_for(client.get(path), timeout=timeout)


# ---------------------------------------------------------------------------
# Liveness
# ---------------------------------------------------------------------------


async def test_health_is_ok_on_a_healthy_system():
    r = await call(app_with_patched_health("up"), "/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


async def test_health_never_flaps_on_a_failing_dependency():
    # The Phase 8.6 contract: `/health` REPORTS dependency status and still
    # answers 200. An orchestrator that fails liveness restarts the process, so a
    # database outage must not be able to crash-loop every replica at once.
    r = await call(app_with_patched_health("down"), "/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"
    assert r.json()["database"] == "down"


async def test_health_still_answers_when_the_database_raises():
    r = await call(app_with_patched_health("raise"), "/health")
    assert r.status_code == 200
    # A raising check is reported, not propagated: a probe must never 500.
    assert r.json()["database"] == "down"


async def test_health_does_not_hang_on_an_unresponsive_database():
    # The Phase 10 fix. A database that hangs — reachable, not refusing — used to
    # hang the probe until the orchestrator's own timeout, and a liveness probe
    # that times out gets the process KILLED. A slow database would therefore
    # restart healthy processes, which is the exact outcome the liveness/readiness
    # split exists to prevent.
    r = await call(
        app_with_patched_health("hang"),
        "/health",
        timeout=health_module._DEPENDENCY_CHECK_TIMEOUT_SECONDS + 3,
    )
    assert r.status_code == 200
    assert r.json()["status"] == "ok"
    # Reported as its own outcome, because "hung" and "refused" are different
    # operator problems.
    assert r.json()["database"] == "timeout"


async def test_health_does_not_hang_on_an_unresponsive_redis():
    r = await call(
        app_with_patched_health("up", redis_result="hang"),
        "/health",
        timeout=health_module._DEPENDENCY_CHECK_TIMEOUT_SECONDS + 3,
    )
    assert r.status_code == 200
    assert r.json()["redis"] == "timeout"


async def test_health_reports_dependency_status_on_a_healthy_system():
    r = await call(app_with_patched_health("up"), "/health")
    assert r.json()["database"] == "up"
    assert r.json()["redis"] == "up"


async def test_health_reports_service_identity():
    r = await call(app_with_patched_health("up"), "/health")
    body = r.json()
    assert body["service"]
    assert body["version"]


# ---------------------------------------------------------------------------
# Readiness
# ---------------------------------------------------------------------------


async def test_ready_is_200_when_postgres_answers():
    r = await call(app_with_patched_health("up"), "/ready")
    assert r.status_code == 200
    assert r.json()["status"] == "ready"


async def test_ready_is_503_when_postgres_is_down():
    # A process that cannot reach its database must stop receiving traffic.
    r = await call(app_with_patched_health("down"), "/ready")
    assert r.status_code == 503
    assert r.json()["status"] == "not_ready"


async def test_ready_is_503_when_postgres_raises():
    r = await call(app_with_patched_health("raise"), "/ready")
    assert r.status_code == 503


async def test_ready_does_not_hang_on_an_unresponsive_postgres():
    # Reported as a fast, definite "timeout" rather than blocking until the
    # orchestrator's own deadline expires.
    r = await call(
        app_with_patched_health("hang"),
        "/ready",
        timeout=health_module._DEPENDENCY_CHECK_TIMEOUT_SECONDS + 3,
    )
    assert r.status_code == 503
    assert r.json()["database"] == "timeout"


async def test_ready_does_not_hang_on_an_unresponsive_redis():
    r = await call(
        app_with_patched_health("up", redis_result="hang"),
        "/ready",
        timeout=health_module._DEPENDENCY_CHECK_TIMEOUT_SECONDS + 3,
    )
    assert r.status_code == 200
    assert r.json()["redis"] == "timeout"


async def test_ready_reports_a_failing_redis_without_gating_on_it():
    # Phase 9 gave Redis a real request path (the ride-location endpoints), and
    # those already answer 503 themselves. Gating readiness on Redis would take
    # ride recording out of rotation for a dependency only the live map needs.
    r = await call(app_with_patched_health("up", redis_result="down"), "/ready")
    assert r.status_code == 200
    assert r.json()["redis"] == "down"
    assert r.json()["status"] == "ready"


async def test_ready_reports_a_raising_redis_as_down_rather_than_503():
    r = await call(app_with_patched_health("up", redis_result="raise"), "/ready")
    assert r.status_code == 200
    assert r.json()["redis"] == "down"


async def test_ready_distinguishes_refused_from_hung():
    # "down" and "timeout" are different operator problems: one is a dead
    # service, the other is a saturated or network-partitioned one.
    refused = await call(app_with_patched_health("down"), "/ready")
    hung = await call(
        app_with_patched_health("hang"),
        "/ready",
        timeout=health_module._DEPENDENCY_CHECK_TIMEOUT_SECONDS + 3,
    )
    assert refused.json()["database"] == "down"
    assert hung.json()["database"] == "timeout"
    assert refused.json()["database"] != hung.json()["database"]


async def test_ready_carries_service_identity_like_health():
    r = await call(app_with_patched_health("up"), "/ready")
    body = r.json()
    assert body["service"]
    assert body["version"]


async def test_both_probes_answer_on_a_completely_broken_system():
    # Total dependency failure: liveness must stay up, readiness must go down.
    # Reporting both at once is the behaviour an orchestrator needs to make its
    # restart-versus-drain decision without guessing.
    app = app_with_patched_health("raise", redis_result="raise")
    live = await call(app, "/health")
    ready = await call(app, "/ready")
    assert live.status_code == 200
    assert ready.status_code == 503

"""Phase 8.6 hardening tests: concurrency, failure boundaries, observability.

Each test targets a production failure mode, not a happy path: concurrent
refresh rotation, stale reset tokens, concurrent duplicate writes, the
members-route 404 contract, limiter memory bounds, correlation identity,
formatter safety, and liveness/readiness separation.
"""

import asyncio
import logging
import uuid
from datetime import UTC, datetime, timedelta

import pytest

AUTH = "/api/v1/auth"
RIDES = "/api/v1/rides"
BIKES = "/api/v1/bikes"
SOCIAL = "/api/v1/social"
TEAMS = "/api/v1/teams"


def _reg(tag):
    return {
        "email": f"hard_{tag}_{uuid.uuid4().hex[:8]}@example.com",
        "password": "StrongPass123",
        "password_confirm": "StrongPass123",
        "display_name": f"Rider {tag.title()}",
    }


async def _user(client, tag="a"):
    data = _reg(tag)
    assert (await client.post(f"{AUTH}/register", json=data)).status_code == 201
    r = await client.post(
        f"{AUTH}/login", json={"email": data["email"], "password": data["password"]}
    )
    return {"Authorization": f"Bearer {r.json()['access_token']}"}, data


@pytest.fixture(autouse=True)
async def _fresh_module_engine():
    """Dispose the module-global engine between tests.

    `check_db`/`check_redis` use process-global clients whose pools bind to
    the creating event loop — and pytest-asyncio runs each test on a fresh
    loop. Without this, a health/ready probe in one test can reuse a pool
    bound to an earlier test's closed loop. Production runs a single loop
    forever, so this is harness-only, not an application defect.
    """
    yield
    from app.db.session import engine as _engine

    await _engine.dispose()


# ---------------------------------------------------------------------------
# Refresh rotation under concurrency
# ---------------------------------------------------------------------------


async def test_concurrent_refresh_yields_one_winner(client):
    """Two simultaneous presentations of one token: 200 + 401, never 200 + 200.

    Without the row lock both would read `revoked_at IS NULL` and each mint a
    live descendant; the second must instead trip reuse detection.
    """
    headers, data = await _user(client, "a")
    assert (await client.get(f"{AUTH}/me", headers=headers)).status_code == 200
    pair = (
        await client.post(
            f"{AUTH}/login", json={"email": data["email"], "password": data["password"]}
        )
    ).json()
    payload = {"refresh_token": pair["refresh_token"]}
    r1, r2 = await asyncio.gather(
        client.post(f"{AUTH}/refresh", json=payload),
        client.post(f"{AUTH}/refresh", json=payload),
    )
    assert sorted([r1.status_code, r2.status_code]) == [200, 401]
    winner = r1.json() if r1.status_code == 200 else r2.json()
    # The loser's replay burned the family: even the winner's token is dead.
    assert (
        await client.post(f"{AUTH}/refresh", json={"refresh_token": winner["refresh_token"]})
    ).status_code == 401


async def test_password_reset_invalidates_other_tokens(client, outbox):
    """A second outstanding reset token dies when the first is consumed."""
    _, data = await _user(client, "a")
    for _ in range(2):
        assert (
            await client.post(f"{AUTH}/password-reset/request", json={"email": data["email"]})
        ).status_code == 200
    tokens = [m.body.split(": ", 1)[1].strip() for m in outbox]
    assert len(tokens) == 2
    first, second = tokens
    assert (
        await client.post(
            f"{AUTH}/password-reset/confirm",
            json={
                "token": first,
                "new_password": "NewStrong123",
                "new_password_confirm": "NewStrong123",
            },
        )
    ).status_code == 200
    stale = await client.post(
        f"{AUTH}/password-reset/confirm",
        json={
            "token": second,
            "new_password": "AnotherStrong123",
            "new_password_confirm": "AnotherStrong123",
        },
    )
    assert stale.status_code == 400


# ---------------------------------------------------------------------------
# Concurrent duplicate writes
# ---------------------------------------------------------------------------


def _pt(seq):
    return {
        "client_point_uuid": str(uuid.uuid4()),
        "seq": seq,
        "lat": 33.0 + seq * 0.00009,
        "lon": -6.0,
        "recorded_at": (
            datetime(2026, 5, 1, 7, 0, tzinfo=UTC) + timedelta(seconds=seq * 5)
        ).isoformat(),
    }


async def test_concurrent_duplicate_chunks_never_500(client):
    """Two identical chunks racing: serialized by the ride row lock.

    Deterministic outcome — the loser sees the winner's rows and reports
    duplicates — and critically never a 500.
    """
    headers, _ = await _user(client, "a")
    bike = (
        await client.post(BIKES, json={"name": "Road", "category": "road"}, headers=headers)
    ).json()["id"]
    ride = (
        await client.post(
            RIDES, json={"bike_id": bike, "client_ride_uuid": str(uuid.uuid4())}, headers=headers
        )
    ).json()["id"]
    chunk = {"points": [_pt(0), _pt(1)]}
    r1, r2 = await asyncio.gather(
        client.post(f"{RIDES}/{ride}/points", json=chunk, headers=headers),
        client.post(f"{RIDES}/{ride}/points", json=chunk, headers=headers),
    )
    assert r1.status_code == 200 and r2.status_code == 200
    bodies = sorted([r1.json(), r2.json()], key=lambda b: b["accepted"], reverse=True)
    assert (bodies[0]["accepted"], bodies[0]["duplicates"]) == (2, 0)
    assert (bodies[1]["accepted"], bodies[1]["duplicates"]) == (0, 2)
    detail = (await client.get(f"{RIDES}/{ride}", headers=headers)).json()
    assert detail["summary"]["accepted_points"] == 2


async def test_concurrent_friend_requests_create_one_row(client):
    """Five simultaneous identical requests: one 200, four 409s, zero 500s."""
    ha, _ = await _user(client, "a")
    hb, _ = await _user(client, "b")
    me_b = await client.get(f"{SOCIAL}/profile/me", headers=hb)
    b_id = me_b.json()["user_id"]
    results = await asyncio.gather(
        *[
            client.post(f"{SOCIAL}/friend-requests", json={"user_id": b_id}, headers=ha)
            for _ in range(5)
        ]
    )
    codes = sorted(r.status_code for r in results)
    assert codes == [201, 409, 409, 409, 409], codes
    listing = await client.get(f"{SOCIAL}/friend-requests?direction=outgoing", headers=ha)
    assert listing.status_code == 200
    assert len(listing.json()["items"]) == 1


# ---------------------------------------------------------------------------
# Error contract: members route
# ---------------------------------------------------------------------------


async def test_members_private_and_missing_team_identical_404(client):
    """Live-verified defect: /members returned 500 where 404 is contractual."""
    owner, _ = await _user(client, "a")
    stranger, _ = await _user(client, "s")
    private = (
        await client.post(TEAMS, json={"name": "Secret", "visibility": "private"}, headers=owner)
    ).json()["id"]
    missing = str(uuid.uuid4())
    r1 = await client.get(f"{TEAMS}/{private}/members", headers=stranger)
    r2 = await client.get(f"{TEAMS}/{missing}/members", headers=stranger)
    assert r1.status_code == 404 and r2.status_code == 404
    assert r1.json() == r2.json()
    assert r1.json()["error"]["code"] == "TEAM_NOT_FOUND"


# ---------------------------------------------------------------------------
# Rate limiter memory bound
# ---------------------------------------------------------------------------


def test_limiter_memory_stays_bounded():
    from app.core import rate_limit

    rate_limit._buckets.clear()
    rate_limit._sweep_at = 0.0
    for batch in range(3):
        for i in range(6000):
            rate_limit.allow(f"evil:{batch}:{i}", 5, 60)
        rate_limit._sweep_at = 0.0  # force the amortized sweep
        rate_limit.allow("sweep-trigger", 5, 60)
    assert len(rate_limit._buckets) <= rate_limit._MAX_KEYS
    # Behavior preserved after eviction pressure.
    assert rate_limit.allow("mine", 2, 60) is True
    assert rate_limit.allow("mine", 2, 60) is True
    assert rate_limit.allow("mine", 2, 60) is False
    rate_limit._buckets.clear()


def test_limiter_keeps_per_key_windows():
    """A sweep triggered by a short-window key must not widen a long one."""
    from app.core import rate_limit

    rate_limit._buckets.clear()
    rate_limit._sweep_at = 0.0
    assert rate_limit.allow("long", 1, 3600) is True
    assert rate_limit.allow("long", 1, 3600) is False
    rate_limit._sweep_at = 0.0
    rate_limit.allow("short-trigger", 5, 60)
    assert rate_limit.allow("long", 1, 3600) is False
    rate_limit._buckets.clear()


# ---------------------------------------------------------------------------
# Correlation + formatter safety
# ---------------------------------------------------------------------------


async def test_request_id_echo_matches_log_record(client, caplog):
    """The id returned to the client is the id attached to its log records."""
    with caplog.at_level(logging.INFO, logger="cyclecoach"):
        r = await client.post(
            f"{AUTH}/login",
            json={"email": "ghost@example.com", "password": "WrongPass999"},
            headers={"X-Request-ID": "trace-abc-123"},
        )
    assert r.status_code == 401
    assert r.headers["X-Request-ID"] == "trace-abc-123"
    rids = {rec.request_id for rec in caplog.records if rec.name == "cyclecoach"}
    assert "trace-abc-123" in rids


async def test_malicious_request_id_is_not_reflected(client):
    """A header-breaking id is replaced, never echoed.

    Non-ASCII is also rejected by the middleware, but httpx refuses to put such
    a value on the wire at all, so it cannot be exercised from here.
    """
    for hostile in ["a\r\nb: evil", "id with spaces", "", "..", "a;b", "<script>"]:
        r = await client.get("/api/v1/health", headers={"X-Request-ID": hostile})
        echoed = r.headers["X-Request-ID"]
        assert echoed != hostile, hostile
        assert len(echoed) == 12, echoed
        assert echoed.isalnum(), echoed


async def test_oversized_but_valid_request_id_is_truncated_not_dropped(client):
    r = await client.get("/api/v1/health", headers={"X-Request-ID": "a" * 64})
    assert r.headers["X-Request-ID"] == "a" * 64


async def test_failed_login_logs_event_without_secrets(client, caplog):
    with caplog.at_level(logging.INFO, logger="cyclecoach"):
        await client.post(
            f"{AUTH}/login",
            json={"email": "someone@example.com", "password": "WrongPass999"},
        )
    assert "auth.login_failed" in caplog.text
    assert "WrongPass999" not in caplog.text
    assert "someone@example.com" not in caplog.text


def test_extra_formatter_renders_fields_and_masks_secrets():
    from app.core.logging import ExtraFormatter

    fmt = ExtraFormatter("%(name)s: %(message)s")
    rec = logging.LogRecord("cyclecoach", logging.INFO, __file__, 1, "auth.login_failed", (), None)
    rec.user_id = "u-1"
    rec.count = 3
    rec.refresh_token = "sekret"
    out = fmt.format(rec)
    assert "user_id=u-1" in out
    assert "count=3" in out
    assert "sekret" not in out
    assert "refresh_token=***" in out


# ---------------------------------------------------------------------------
# Liveness vs readiness
# ---------------------------------------------------------------------------


async def test_ready_200_when_healthy(client):
    r = await client.get("/api/v1/ready")
    assert r.status_code == 200
    assert r.json()["status"] == "ready"


async def test_ready_ignores_redis_but_reports_it(client, monkeypatch):
    """Redis serves no request path: a Redis blip must not fail readiness.

    The state is still reported, so an operator can see the degradation.
    """

    async def down():
        return False

    monkeypatch.setattr("app.api.v1.health.check_redis", down)
    r = await client.get("/api/v1/ready")
    assert r.status_code == 200
    assert r.json()["redis"] == "down"


async def test_ready_503_when_db_down(client, monkeypatch):
    async def down():
        return False

    monkeypatch.setattr("app.api.v1.health.check_db", down)
    r = await client.get("/api/v1/ready")
    assert r.status_code == 503
    assert r.json()["status"] == "not_ready"
    assert r.json()["database"] == "down"


async def test_health_stays_200_liveness(client, monkeypatch):
    """Liveness must not flap with dependencies; readiness carries that signal."""

    async def down():
        return False

    monkeypatch.setattr("app.api.v1.health.check_db", down)
    monkeypatch.setattr("app.api.v1.health.check_redis", down)
    r = await client.get("/api/v1/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"
    assert r.json()["database"] == "down"

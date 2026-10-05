"""Phase 9 live ride location tests (ADR-16 §6).

Live location is the part of this phase that can do irreversible harm to someone
who did nothing wrong, so these tests are mostly about what does NOT happen:

* nothing is shared until a rider explicitly shares it;
* nothing is retained past its TTL;
* a stale position is never presented as live;
* a Redis outage is a 503, never an empty map;
* a blocked pair never sees each other's position;
* coordinates are never logged.

Redis is real. The two outage tests patch the client to raise, because the
behaviour under test there is precisely that the failure is NOT swallowed — a
mock that "works" would prove nothing about it.

No sleeps anywhere. TTL and staleness are driven by writing an OLD timestamp
straight into the hash, which is deterministic, instead of waiting for a clock to
advance.
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.redis import client as redis_client
from app.services import ride_location_service
from app.services.ride_location_service import (
    LOCATION_TTL_SECONDS,
    STALE_AFTER_SECONDS,
    _key,
    _parse,
)

RIDES = "/api/v1/group-rides"
SOCIAL = "/api/v1/social"
AUTH = "/api/v1/auth"


def _reg(tag):
    return {
        "email": f"loc_{tag}_{uuid.uuid4().hex[:8]}@example.com",
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
    assert r.status_code == 200
    headers = {"Authorization": f"Bearer {r.json()['access_token']}"}
    me = await client.get(f"{SOCIAL}/profile/me", headers=headers)
    assert me.status_code == 200, me.text
    await client.patch(
        f"{SOCIAL}/profile",
        json={"username": f"loc_{tag}_{uuid.uuid4().hex[:6]}", "display_name": data["display_name"]},
        headers=headers,
    )
    return headers, me.json()["user_id"]


def _logged(caplog) -> str:
    """Everything the log actually says, including `extra` fields.

    `LogRecord.getMessage()` returns only the message, so a search over it would
    miss the `error_type` the service attaches — and would make the assertions
    below pass for the wrong reason.
    """
    parts: list[str] = []
    for record in caplog.records:
        parts.append(record.getMessage())
        parts.extend(
            str(value)
            for key, value in record.__dict__.items()
            if key not in ("msg", "args", "exc_info", "exc_text", "message", "asctime")
        )
    return " ".join(parts)


async def _started_ride(client):
    """A `started` ride with two joined riders — sharing is only legal here."""
    org_h, org_id = await _user(client, "org")
    guest_h, guest_id = await _user(client, "guest")
    ride = await client.post(f"{RIDES}", json={"title": "Coastal Spin"}, headers=org_h)
    assert ride.status_code == 201, ride.text
    ride_id = ride.json()["id"]
    inv = await client.post(
        f"{RIDES}/{ride_id}/invitations", json={"user_id": guest_id}, headers=org_h
    )
    assert inv.status_code == 201, inv.text
    joined = await client.post(
        f"{RIDES}/{ride_id}/respond", json={"accept": True}, headers=guest_h
    )
    assert joined.status_code == 200, joined.text
    started = await client.post(f"{RIDES}/{ride_id}/start", headers=org_h)
    assert started.status_code == 200, started.text
    return org_h, org_id, guest_h, guest_id, ride_id


@pytest.fixture
async def clean_live_ride():
    """Delete each ride's hash after the test, so nothing leaks between tests.

    Also drops the cached Redis client on BOTH sides of the test.
    `app.redis.client.get_redis` keeps ONE module-level client, and redis-py binds a
    pooled connection to the event loop that first used it. pytest-asyncio gives
    every test a fresh loop, so a client left over from an earlier test raises
    `RuntimeError: Event loop is closed` on the next one — which surfaces here as a
    confusing 503 rather than as the test-harness bug it is.

    Resetting only in teardown is not enough: the FIRST test in this file inherits
    whatever client an earlier test file left behind, on a different loop, and fails
    before its own teardown ever runs. That is an ordering-dependent flake, and the
    full suite is the only thing that triggers it — the file passes in isolation.
    Production runs one loop for the process lifetime, so the cache is correct there
    and resetting it here is not papering over anything.
    """
    written: list[uuid.UUID] = []

    async def _reset_cached_client() -> None:
        cached = redis_client._client
        # Drop the reference FIRST. If closing raises, the next `get_redis()` must
        # still build a fresh client rather than hand back the dead one.
        redis_client._client = None
        if cached is None:
            return
        try:
            await cached.aclose()
        except RuntimeError:
            # Expected, and the whole reason this helper exists: the pooled
            # connection belongs to a loop that pytest-asyncio has already closed.
            # The client is unusable by definition, so there is nothing to salvage
            # — but the RESET must not fail, or a test harness detail becomes a
            # reported error.
            pass

    await _reset_cached_client()
    try:
        yield written
    finally:
        if written:
            await redis_client.get_redis().delete(*[_key(r) for r in written])
        await _reset_cached_client()


# ---------------------------------------------------------------------------
# Rule 1: sharing is off until a rider shares
# ---------------------------------------------------------------------------


async def test_nothing_is_shared_before_anyone_publishes(client, clean_live_ride):
    """Rule 1. Being on a started ride is not consent to be tracked."""
    org_h, _, _, _, ride_id = await _started_ride(client)
    clean_live_ride.append(uuid.UUID(ride_id))

    seen = await client.get(f"{RIDES}/{ride_id}/location", headers=org_h)
    assert seen.status_code == 200, seen.text
    assert seen.json()["items"] == []


async def test_publishing_shows_only_to_joined_riders(client, clean_live_ride):
    """One rider's ping is visible to the ride, and to nobody else."""
    org_h, org_id, guest_h, _, ride_id = await _started_ride(client)
    clean_live_ride.append(uuid.UUID(ride_id))

    pub = await client.post(
        f"{RIDES}/{ride_id}/location",
        json={"latitude": 46.2001, "longitude": 6.1401, "accuracy_m": 8.0},
        headers=org_h,
    )
    assert pub.status_code == 200, pub.text
    assert pub.json()["status"] == "sharing"
    assert pub.json()["expires_in_seconds"] == LOCATION_TTL_SECONDS

    seen = await client.get(f"{RIDES}/{ride_id}/location", headers=guest_h)
    assert seen.status_code == 200, seen.text
    items = seen.json()["items"]
    assert len(items) == 1
    assert items[0]["user_id"] == org_id
    assert items[0]["latitude"] == pytest.approx(46.2001)
    assert items[0]["longitude"] == pytest.approx(6.1401)
    assert items[0]["is_self"] is False
    assert items[0]["age_seconds"] >= 0


async def test_a_rider_sees_themselves(client, clean_live_ride):
    """`is_self` lets a rider find their own dot without a second lookup."""
    org_h, org_id, _, _, ride_id = await _started_ride(client)
    clean_live_ride.append(uuid.UUID(ride_id))
    await client.post(
        f"{RIDES}/{ride_id}/location",
        json={"latitude": 1.0, "longitude": 2.0},
        headers=org_h,
    )
    mine = (await client.get(f"{RIDES}/{ride_id}/location", headers=org_h)).json()["items"]
    assert [i["is_self"] for i in mine] == [True]
    assert mine[0]["user_id"] == org_id


async def test_publishing_is_refused_when_not_joined(client, clean_live_ride):
    """No roster row, no sharing — an invite is not permission."""
    _, _, _, _, ride_id = await _started_ride(client)
    clean_live_ride.append(uuid.UUID(ride_id))
    stranger_h, _ = await _user(client, "stranger")

    r = await client.post(
        f"{RIDES}/{ride_id}/location",
        json={"latitude": 46.2, "longitude": 6.14},
        headers=stranger_h,
    )
    assert r.status_code == 404, r.text


async def test_invited_but_not_joined_cannot_share(client, clean_live_ride):
    """An `invited` rider has not accepted, so there is no consent to act on."""
    org_h, _, _, _, ride_id = await _started_ride(client)
    clean_live_ride.append(uuid.UUID(ride_id))
    # A second, still-`open` ride: an `invited` rider on it has not accepted, so
    # there is no consent to act on.
    later_h, later_id = await _user(client, "later")
    ride = await client.post(f"{RIDES}", json={"title": "Open"}, headers=org_h)
    assert ride.status_code == 201, ride.text
    open_id = ride.json()["id"]
    clean_live_ride.append(uuid.UUID(open_id))
    invited = await client.post(
        f"{RIDES}/{open_id}/invitations", json={"user_id": later_id}, headers=org_h
    )
    assert invited.status_code == 201, invited.text

    r = await client.post(
        f"{RIDES}/{open_id}/location",
        json={"latitude": 46.2, "longitude": 6.14},
        headers=later_h,
    )
    assert r.status_code == 404, r.text


# ---------------------------------------------------------------------------
# Rule 2: a stale position is never presented as live
# ---------------------------------------------------------------------------


async def test_a_stale_position_is_hidden(client, clean_live_ride):
    """Rule 2. The staleness check happens on READ.

    Written directly rather than by waiting: ageing the stored timestamp makes the
    assertion deterministic and instant, where a `sleep` would make it slow and
    still flaky.
    """
    org_h, org_id, guest_h, _, ride_id = await _started_ride(client)
    clean_live_ride.append(uuid.UUID(ride_id))
    await client.post(
        f"{RIDES}/{ride_id}/location",
        json={"latitude": 46.2, "longitude": 6.14},
        headers=org_h,
    )

    redis = redis_client.get_redis()
    old = (datetime.now(UTC) - timedelta(seconds=STALE_AFTER_SECONDS + 30)).timestamp()
    await redis.hset(_key(uuid.UUID(ride_id)), str(org_id), f"46.2|6.14||{old}")

    seen = await client.get(f"{RIDES}/{ride_id}/location", headers=guest_h)
    assert seen.status_code == 200, seen.text
    assert seen.json()["items"] == []
    assert seen.json()["stale_after_seconds"] == STALE_AFTER_SECONDS


async def test_a_future_timestamp_is_also_hidden(client, clean_live_ride):
    """A position stamped in the future means somebody's clock is wrong, which is
    another way of saying "we do not actually know where this is"."""
    org_h, org_id, guest_h, _, ride_id = await _started_ride(client)
    clean_live_ride.append(uuid.UUID(ride_id))
    await client.post(
        f"{RIDES}/{ride_id}/location",
        json={"latitude": 46.2, "longitude": 6.14},
        headers=org_h,
    )

    redis = redis_client.get_redis()
    ahead = (datetime.now(UTC) + timedelta(hours=1)).timestamp()
    await redis.hset(_key(uuid.UUID(ride_id)), str(org_id), f"46.2|6.14||{ahead}")

    seen = await client.get(f"{RIDES}/{ride_id}/location", headers=guest_h)
    assert seen.json()["items"] == []


async def test_a_fresh_position_is_shown_with_its_age(client, clean_live_ride):
    """The client gets the age so it can decide staleness for itself, rather than
    trusting a server-rendered "last seen 2 minutes ago" that goes stale in the
    widget."""
    org_h, _, guest_h, _, ride_id = await _started_ride(client)
    clean_live_ride.append(uuid.UUID(ride_id))
    await client.post(
        f"{RIDES}/{ride_id}/location",
        json={"latitude": 46.2, "longitude": 6.14, "accuracy_m": 5.0},
        headers=org_h,
    )
    items = (await client.get(f"{RIDES}/{ride_id}/location", headers=guest_h)).json()["items"]
    assert items[0]["age_seconds"] < STALE_AFTER_SECONDS
    assert items[0]["accuracy_m"] == pytest.approx(5.0)


# ---------------------------------------------------------------------------
# Rule 3: an outage is a 503, never an empty map
# ---------------------------------------------------------------------------


class _BrokenRedis:
    """Stands in for an unreachable Redis.

    Raises on every operation, which is what an outage looks like from here. The
    point of the test is what the SERVICE does with that, so the fake must fail
    honestly rather than return an empty result that would make the bug invisible.
    """

    async def hgetall(self, *_args, **_kwargs):
        raise ConnectionError("redis is down")

    async def hset(self, *_args, **_kwargs):
        raise ConnectionError("redis is down")

    async def hdel(self, *_args, **_kwargs):
        raise ConnectionError("redis is down")

    def pipeline(self, *_args, **_kwargs):
        raise ConnectionError("redis is down")


async def test_read_during_an_outage_is_503_not_an_empty_map(client, monkeypatch):
    """Rule 3, the single most important behaviour in the module.

    An empty map would be indistinguishable from "nobody is sharing", and a rider
    concluding the group has lost them is exactly the failure this must never
    produce.
    """
    org_h, _, _, _, ride_id = await _started_ride(client)
    monkeypatch.setattr(redis_client, "get_redis", lambda: _BrokenRedis())

    r = await client.get(f"{RIDES}/{ride_id}/location", headers=org_h)
    assert r.status_code == 503, r.text
    assert r.json()["error"]["code"] == "LOCATION_UNAVAILABLE"


async def test_publish_during_an_outage_is_503(client, monkeypatch):
    """Rule 3 on the write side too: a rider must never be told they are sharing
    when nothing was stored."""
    org_h, _, _, _, ride_id = await _started_ride(client)
    monkeypatch.setattr(redis_client, "get_redis", lambda: _BrokenRedis())

    r = await client.post(
        f"{RIDES}/{ride_id}/location",
        json={"latitude": 46.2, "longitude": 6.14},
        headers=org_h,
    )
    assert r.status_code == 503, r.text
    assert r.json()["error"]["code"] == "LOCATION_UNAVAILABLE"


async def test_stop_during_an_outage_is_503(client, monkeypatch):
    """Withdrawal must not silently fail either — a rider who asked to stop and
    got a success would believe they were still exposed."""
    org_h, _, _, _, ride_id = await _started_ride(client)
    monkeypatch.setattr(redis_client, "get_redis", lambda: _BrokenRedis())

    r = await client.delete(f"{RIDES}/{ride_id}/location", headers=org_h)
    assert r.status_code == 503, r.text


async def test_authorization_is_checked_before_touching_redis(client, monkeypatch):
    """A rider with no standing gets their 404 even when Redis is down — the
    authorization decision never depends on the availability of the store."""

    def _explode():  # pragma: no cover - must never be reached
        raise AssertionError("Redis was touched before authorization")

    _, _, _, _, ride_id = await _started_ride(client)
    monkeypatch.setattr(redis_client, "get_redis", _explode)
    stranger_h, _ = await _user(client, "stranger")

    r = await client.get(f"{RIDES}/{ride_id}/location", headers=stranger_h)
    assert r.status_code == 404, r.text


# ---------------------------------------------------------------------------
# Withdrawal removes a rider from the map immediately
# ---------------------------------------------------------------------------


async def test_withdrawing_from_a_ride_hides_a_published_position(client, clean_live_ride):
    """The roster is re-read on every read, so leaving takes effect at once rather
    than when the TTL happens to expire."""
    org_h, _, guest_h, guest_id, ride_id = await _started_ride(client)
    clean_live_ride.append(uuid.UUID(ride_id))
    await client.post(
        f"{RIDES}/{ride_id}/location",
        json={"latitude": 46.2, "longitude": 6.14},
        headers=guest_h,
    )
    before = (await client.get(f"{RIDES}/{ride_id}/location", headers=org_h)).json()["items"]
    assert [i["user_id"] for i in before] == [guest_id]

    assert (await client.post(f"{RIDES}/{ride_id}/leave", headers=guest_h)).status_code == 200

    after = (await client.get(f"{RIDES}/{ride_id}/location", headers=org_h)).json()["items"]
    assert after == []


async def test_an_organizer_removed_from_viewing_still_cannot_publish(client, clean_live_ride):
    """A rider who was removed from a live ride is refused, not merely hidden."""
    org_h, _, guest_h, guest_id, ride_id = await _started_ride(client)
    clean_live_ride.append(uuid.UUID(ride_id))

    removed = await client.delete(
        f"{RIDES}/{ride_id}/participants/{guest_id}", headers=org_h
    )
    assert removed.status_code == 200, removed.text

    r = await client.post(
        f"{RIDES}/{ride_id}/location",
        json={"latitude": 46.2, "longitude": 6.14},
        headers=guest_h,
    )
    assert r.status_code == 404, r.text


# ---------------------------------------------------------------------------
# Stopping
# ---------------------------------------------------------------------------


async def test_stopping_removes_the_position_immediately(client, clean_live_ride):
    """Consent is revocable per ride, without leaving the ride itself."""
    org_h, _, guest_h, _, ride_id = await _started_ride(client)
    clean_live_ride.append(uuid.UUID(ride_id))
    await client.post(
        f"{RIDES}/{ride_id}/location",
        json={"latitude": 46.2, "longitude": 6.14},
        headers=org_h,
    )
    assert (await client.get(f"{RIDES}/{ride_id}/location", headers=guest_h)).json()["items"]

    stopped = await client.delete(f"{RIDES}/{ride_id}/location", headers=org_h)
    assert stopped.status_code == 200, stopped.text
    assert stopped.json()["status"] == "stopped"

    after = await client.get(f"{RIDES}/{ride_id}/location", headers=guest_h)
    assert after.json()["items"] == []


async def test_stopping_is_allowed_on_a_cancelled_ride(client, clean_live_ride):
    """The moment a rider most wants to stop is the moment a ride is being torn
    down, so withdrawal must not depend on the ride still being active."""
    org_h, _, guest_h, _, ride_id = await _started_ride(client)
    clean_live_ride.append(uuid.UUID(ride_id))
    assert (await client.post(f"{RIDES}/{ride_id}/cancel", headers=org_h)).status_code == 200

    stopped = await client.delete(f"{RIDES}/{ride_id}/location", headers=guest_h)
    assert stopped.status_code == 200, stopped.text


async def test_republishing_replaces_rather_than_accumulates(client, clean_live_ride):
    """One hash field per rider: no history, no trail."""
    org_h, _, guest_h, _, ride_id = await _started_ride(client)
    clean_live_ride.append(uuid.UUID(ride_id))
    for lat in (46.2, 46.3, 46.4):
        await client.post(
            f"{RIDES}/{ride_id}/location",
            json={"latitude": lat, "longitude": 6.14},
            headers=org_h,
        )
    items = (await client.get(f"{RIDES}/{ride_id}/location", headers=guest_h)).json()["items"]
    assert len(items) == 1
    assert items[0]["latitude"] == pytest.approx(46.4)


# ---------------------------------------------------------------------------
# Terminal states share nothing
# ---------------------------------------------------------------------------


async def test_a_cancelled_ride_publishes_nothing(client, clean_live_ride):
    """A cancelled ride shares no live positions, which is also what makes the
    data disappear when a ride ends rather than lingering until the TTL."""
    org_h, _, guest_h, _, ride_id = await _started_ride(client)
    clean_live_ride.append(uuid.UUID(ride_id))
    assert (await client.post(f"{RIDES}/{ride_id}/cancel", headers=org_h)).status_code == 200

    r = await client.post(
        f"{RIDES}/{ride_id}/location",
        json={"latitude": 46.2, "longitude": 6.14},
        headers=org_h,
    )
    assert r.status_code == 409, r.text
    assert r.json()["error"]["code"] == "RIDE_CLOSED"

    read = await client.get(f"{RIDES}/{ride_id}/location", headers=guest_h)
    assert read.status_code == 409


# ---------------------------------------------------------------------------
# Rule 5: blocks remove visibility in both directions
# ---------------------------------------------------------------------------


async def test_a_blocked_pair_cannot_see_each_others_position(client, clean_live_ride):
    """Rule 5. A block is a personal boundary that ride membership does not dissolve."""
    org_h, org_id, guest_h, _, ride_id = await _started_ride(client)
    clean_live_ride.append(uuid.UUID(ride_id))

    blocked = await client.post(
        f"{SOCIAL}/blocks", json={"user_id": org_id}, headers=guest_h
    )
    assert blocked.status_code == 201, blocked.text

    await client.post(
        f"{RIDES}/{ride_id}/location",
        json={"latitude": 46.2, "longitude": 6.14},
        headers=org_h,
    )
    hidden = await client.get(f"{RIDES}/{ride_id}/location", headers=guest_h)
    assert hidden.status_code == 200, hidden.text
    assert hidden.json()["items"] == []

    # And the blocked rider cannot publish either.
    refused = await client.post(
        f"{RIDES}/{ride_id}/location",
        json={"latitude": 46.2, "longitude": 6.14},
        headers=guest_h,
    )
    assert refused.status_code == 403, refused.text
    assert refused.json()["error"]["code"] == "RIDE_BLOCKED"


# ---------------------------------------------------------------------------
# Rule 4: coordinates are never logged
# ---------------------------------------------------------------------------


async def test_coordinates_are_not_written_to_the_log(client, clean_live_ride, caplog):
    """Rule 4. Logs are routinely shipped and read by people who should not know
    where a rider lives, so the publish path logs ids and nothing else."""
    org_h, org_id, _, _, ride_id = await _started_ride(client)
    clean_live_ride.append(uuid.UUID(ride_id))

    with caplog.at_level("DEBUG"):
        await client.post(
            f"{RIDES}/{ride_id}/location",
            json={"latitude": 46.123456, "longitude": 6.654321, "accuracy_m": 4.0},
            headers=org_h,
        )

    haystack = _logged(caplog)
    assert "46.123456" not in haystack
    assert "6.654321" not in haystack
    # It did log something, so the assertion above is about content, not silence.
    assert "ride_location_published" in haystack
    del org_id


async def test_an_outage_does_not_log_the_coordinates_either(
    client, clean_live_ride, monkeypatch, caplog
):
    """A redis-py error string can echo the command and its arguments, which for
    this call would be the rider's coordinates. Only the error TYPE is logged."""
    org_h, _, _, _, ride_id = await _started_ride(client)
    monkeypatch.setattr(redis_client, "get_redis", lambda: _BrokenRedis())

    with caplog.at_level("DEBUG"):
        r = await client.post(
            f"{RIDES}/{ride_id}/location",
            json={"latitude": 46.999999, "longitude": 6.888888},
            headers=org_h,
        )
    assert r.status_code == 503

    haystack = _logged(caplog)
    assert "46.999999" not in haystack
    assert "6.888888" not in haystack
    # The error TYPE is logged, so the failure is diagnosable without the payload.
    assert "ConnectionError" in haystack


# ---------------------------------------------------------------------------
# Input validation and the storage format
# ---------------------------------------------------------------------------


async def test_coordinates_are_range_checked(client, clean_live_ride):
    """Out-of-range coordinates are a client bug, refused by the schema rather
    than stored and rendered somewhere nonsensical."""
    org_h, _, _, _, ride_id = await _started_ride(client)
    clean_live_ride.append(uuid.UUID(ride_id))
    for bad in ({"latitude": 91.0, "longitude": 6.0}, {"latitude": 46.0, "longitude": 181.0}):
        r = await client.post(f"{RIDES}/{ride_id}/location", json=bad, headers=org_h)
        assert r.status_code == 422, r.text
        assert r.json()["error"]["code"] == "VALIDATION_ERROR"


async def test_publish_stores_a_server_timestamp(client, clean_live_ride):
    """The timestamp is the server's, not the client's — a wrong device clock must
    not be able to make a position look fresh forever."""
    org_h, org_id, _, _, ride_id = await _started_ride(client)
    clean_live_ride.append(uuid.UUID(ride_id))
    await client.post(
        f"{RIDES}/{ride_id}/location",
        json={"latitude": 46.2, "longitude": 6.14},
        headers=org_h,
    )
    blob = await redis_client.get_redis().hget(_key(uuid.UUID(ride_id)), str(org_id))
    lat, lon, acc, ts = _parse(blob)
    assert lat == pytest.approx(46.2)
    assert lon == pytest.approx(6.14)
    assert acc is None
    assert ts is not None
    assert ts <= datetime.now(UTC)


async def test_the_hash_carries_a_ttl(client, clean_live_ride):
    """Rule 1's guarantee: the data is DELETED, not retained with a soft flag."""
    org_h, _, _, _, ride_id = await _started_ride(client)
    clean_live_ride.append(uuid.UUID(ride_id))
    await client.post(
        f"{RIDES}/{ride_id}/location",
        json={"latitude": 46.2, "longitude": 6.14},
        headers=org_h,
    )
    ttl = await redis_client.get_redis().ttl(_key(uuid.UUID(ride_id)))
    assert 0 < ttl <= LOCATION_TTL_SECONDS


@pytest.mark.parametrize(
    "blob",
    [
        "",
        "not-numbers",
        "46.2|6.14",
        "46.2|6.14|5|not-a-timestamp|extra",
        "abc|6.14||1700000000",
    ],
)
def test_a_malformed_entry_is_skipped_not_fatal(blob):
    """One rider's bad write must not blank the map for everyone else, and there is
    no repair path worth building for a value that expires in minutes."""
    assert _parse(blob) == (None, None, None, None)


def test_constants_separate_deletion_from_display():
    """The two ages are different questions and must not be collapsed.

    The TTL is about deleting data; staleness is about not lying to a rider about
    where somebody is. A single number for both would either keep data too long
    or hide riders too eagerly.
    """
    assert LOCATION_TTL_SECONDS > STALE_AFTER_SECONDS


async def test_the_location_key_is_namespaced_by_phase():
    """Keys are prefixed, so a future phase cannot collide with these."""
    assert _key(uuid.uuid4()).startswith("gr9:share:")
    assert ride_location_service.LOCATION_TTL_SECONDS == LOCATION_TTL_SECONDS

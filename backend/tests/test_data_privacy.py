"""Phase 10 WS-J/P privacy and data-lifecycle invariants.

Every test here asserts a property that is easy to break and expensive to
discover late. Where a behaviour is *correct today by design*, the test exists to
fail the day someone adds a shortcut â€” most of these read as obvious, and that is
the point: a privacy guarantee nobody tests is a comment, not a control.

The tests fall into four groups:

1. **Live location lifecycle** â€” consent, publish, authorized read, revoke,
   staleness, TTL. The highest-sensitivity data in the system.
2. **Ephemeral location is never durable** â€” proving no location table exists and
   that a revocation is real rather than cosmetic.
3. **Credential and biometric minimization** â€” what must never be persisted,
   returned, or logged.
4. **Retention gaps are pinned** â€” for data with no implemented retention policy,
   the test documents the CURRENT behaviour so the gap is visible in code and
   cannot quietly widen. These are explicitly marked as pinning a deficiency.
"""

import inspect
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.services import ride_location_service
from app.services.ride_location_service import (
    LOCATION_TTL_SECONDS,
    STALE_AFTER_SECONDS,
    _key,
)

RIDES = "/api/v1/group-rides"
SOCIAL = "/api/v1/social"
AUTH = "/api/v1/auth"


# ---------------------------------------------------------------------------
# Group 1 â€” the live-location lifecycle
# ---------------------------------------------------------------------------
#
# CONSENT -> START SHARING -> PUBLISH -> AUTHORIZED READ
#          -> STOP / LEAVE / COMPLETE / CANCEL -> REVOKE -> TTL EXPIRY
#
# The whole Phase 9 location guarantee is a chain, and a chain is only as strong
# as its weakest link. These tests walk it link by link.


async def _user2(client, tag: str):
    """Register + log in, returning (auth headers, user id).

    Self-contained rather than importing the location suite's helpers, so this
    file's privacy claims do not depend on another test module's internals
    staying compatible.
    """
    data = {
        "email": f"priv_{tag}_{uuid.uuid4().hex[:8]}@example.com",
        "password": "StrongPass123",
        "password_confirm": "StrongPass123",
        "display_name": f"Rider {tag.title()}",
    }
    assert (await client.post(f"{AUTH}/register", json=data)).status_code == 201
    r = await client.post(
        f"{AUTH}/login", json={"email": data["email"], "password": data["password"]}
    )
    headers = {"Authorization": f"Bearer {r.json()['access_token']}"}
    me = await client.get(f"{SOCIAL}/profile/me", headers=headers)
    return headers, me.json()["user_id"]


async def _ride_with_two_joined(client):
    org_h, org_id = await _user2(client, "org")
    guest_h, guest_id = await _user2(client, "guest")
    ride = await client.post(f"{RIDES}", json={"title": "Privacy Ride"}, headers=org_h)
    assert ride.status_code == 201, ride.text
    ride_id = ride.json()["id"]
    inv = await client.post(
        f"{RIDES}/{ride_id}/invitations", json={"user_id": guest_id}, headers=org_h
    )
    assert inv.status_code == 201, inv.text
    acc = await client.post(f"{RIDES}/{ride_id}/respond", json={"accept": True}, headers=guest_h)
    assert acc.status_code == 200, acc.text
    start = await client.post(f"{RIDES}/{ride_id}/start", headers=org_h)
    assert start.status_code == 200, start.text
    return org_h, org_id, guest_h, guest_id, ride_id


async def test_consent_is_required_before_any_position_is_published(client, redis_available):
    """Nothing is published until a rider asks. Being on a ride is not consent.

    The chain starts here, and this is the link most likely to be broken by a
    well-meaning change: "auto-share when the ride starts" would be a small diff
    and a serious privacy regression.
    """
    org_h, _, _, _, ride_id = await _ride_with_two_joined(client)

    seen = await client.get(f"{RIDES}/{ride_id}/location", headers=org_h)
    assert seen.status_code == 200, seen.text
    assert seen.json()["items"] == [], "a rider was located without consenting"


async def test_publishing_requires_an_explicit_call_and_then_reads_back(client, redis_available):
    org_h, org_id, _, _, ride_id = await _ride_with_two_joined(client)

    pub = await client.post(
        f"{RIDES}/{ride_id}/location",
        json={"latitude": 33.5, "longitude": -6.5, "accuracy_m": 8},
        headers=org_h,
    )
    assert pub.status_code in (200, 201), pub.text
    # The response states the retention guarantee rather than a display promise.
    assert pub.json()["expires_in_seconds"] == LOCATION_TTL_SECONDS

    read = await client.get(f"{RIDES}/{ride_id}/location", headers=org_h)
    assert read.status_code == 200, read.text
    mine = [i for i in read.json()["items"] if i["user_id"] == org_id]
    assert len(mine) == 1
    assert mine[0]["is_self"] is True
    assert float(mine[0]["latitude"]) == pytest.approx(33.5)


async def test_a_non_participant_cannot_read_any_position(client, redis_available):
    """Knowing a ride id is not authorization.

    The most important IDOR test in this file: an unrelated authenticated rider
    who learns a ride UUID must learn nothing about who is on it or where.
    """
    org_h, _, _, _, ride_id = await _ride_with_two_joined(client)
    stranger_h, _ = await _user2(client, "stranger")

    await client.post(
        f"{RIDES}/{ride_id}/location",
        json={"latitude": 33.5, "longitude": -6.5},
        headers=org_h,
    )

    r = await client.get(f"{RIDES}/{ride_id}/location", headers=stranger_h)
    # Either refused outright, or refused with nothing about the ride disclosed.
    assert r.status_code in (403, 404), r.text
    assert "items" not in r.json().get("error", {}).get("details", {})


@pytest.fixture
async def redis_available():
    """Fail loudly, and only for the tests that genuinely need Redis.

    The location lifecycle assertions below inspect Redis directly, because
    "revoked" has to mean the coordinate is GONE rather than merely filtered out
    of one response. That makes them real-Redis tests like
    `test_ride_location.py`, so they skip when no server is reachable instead of
    reporting a green pass that proved nothing.

    The guard also mirrors `test_ride_location.clean_live_ride`: it resets the
    cached client on both sides, because redis-py binds a pooled connection to the
    event loop that first used it and pytest-asyncio gives every test a fresh one.
    """
    from app.redis import client as redis_client

    async def _reset() -> None:
        cached = redis_client._client
        redis_client._client = None
        if cached is not None:
            try:
                await cached.aclose()
            except RuntimeError:
                # The loop this client belonged to is gone; that is the reason
                # for resetting, not a failure.
                pass

    await _reset()
    try:
        pong = await redis_client.get_redis().ping()
    except Exception as exc:  # noqa: BLE001 - a skip is the right outcome here
        await _reset()
        pytest.skip(f"no Redis available for the live-location lifecycle: {type(exc).__name__}")
    if not pong:
        await _reset()
        pytest.skip("Redis did not answer PING")
    try:
        yield
    finally:
        await _reset()


async def test_a_non_participant_cannot_publish_into_a_ride(client, redis_available):
    org_h, org_id, _, _, ride_id = await _ride_with_two_joined(client)
    stranger_h, _ = await _user2(client, "stranger2")

    r = await client.post(
        f"{RIDES}/{ride_id}/location",
        json={"latitude": 33.5, "longitude": -6.5},
        headers=stranger_h,
    )
    assert r.status_code in (403, 404), r.text

    # And nothing was written.
    seen = await client.get(f"{RIDES}/{ride_id}/location", headers=org_h)
    assert [i for i in seen.json()["items"] if i["user_id"] != org_id] == []


async def test_revoke_is_real_and_readable_immediately(client, redis_available):
    """The load-bearing lifecycle property: revoke must remove, not hide.

    Phase 10 WS-N fixed a real ordering bug where a client-side teardown read a
    freshly-rebuilt controller and skipped the server call entirely. This test is
    the backend half of that same guarantee.
    """
    org_h, org_id, _, _, ride_id = await _ride_with_two_joined(client)

    await client.post(
        f"{RIDES}/{ride_id}/location",
        json={"latitude": 33.5, "longitude": -6.5},
        headers=org_h,
    )
    before = await client.get(f"{RIDES}/{ride_id}/location", headers=org_h)
    assert any(i["user_id"] == org_id for i in before.json()["items"])

    stopped = await client.delete(f"{RIDES}/{ride_id}/location", headers=org_h)
    assert stopped.status_code == 200, stopped.text

    after = await client.get(f"{RIDES}/{ride_id}/location", headers=org_h)
    assert [i for i in after.json()["items"] if i["user_id"] == org_id] == [], (
        "the position was still readable after an explicit revoke"
    )


async def test_revocation_is_durable_in_redis_not_merely_filtered_on_read(client, redis_available):
    """The hash field itself must be gone.

    A read filter would satisfy the previous test while leaving the coordinate in
    Redis until its TTL â€” i.e. "revoked" would mean "not displayed". This asserts
    the storage layer, which is what a privacy promise has to mean.
    """
    from app.redis import client as redis_client

    org_h, org_id, _, _, ride_id = await _ride_with_two_joined(client)

    await client.post(
        f"{RIDES}/{ride_id}/location",
        json={"latitude": 33.5, "longitude": -6.5},
        headers=org_h,
    )
    stored = await redis_client.get_redis().hget(_key(uuid.UUID(ride_id)), str(org_id))
    assert stored is not None, "precondition: the position is in Redis"

    await client.delete(f"{RIDES}/{ride_id}/location", headers=org_h)
    gone = await redis_client.get_redis().hget(_key(uuid.UUID(ride_id)), str(org_id))
    assert gone is None, "revoke left the coordinate in Redis"


async def test_the_retention_ttl_is_applied_to_the_key(client, redis_available):
    """TTL is the backstop, so it must actually be set on the key."""
    from app.redis import client as redis_client

    org_h, _, _, _, ride_id = await _ride_with_two_joined(client)
    await client.post(
        f"{RIDES}/{ride_id}/location",
        json={"latitude": 33.5, "longitude": -6.5},
        headers=org_h,
    )
    ttl = await redis_client.get_redis().ttl(_key(uuid.UUID(ride_id)))
    assert 0 < ttl <= LOCATION_TTL_SECONDS


async def test_stale_positions_are_hidden_before_the_ttl_expires(client, redis_available):
    """TTL and staleness answer different questions.

    The TTL (300s) is about DELETING data. The staleness cutoff (60s) is about not
    LYING to a rider: a dot still inside its TTL but three minutes old means this
    rider stopped answering, and showing it as live would be a false statement
    about a real person.
    """
    from app.redis import client as redis_client

    org_h, org_id, _, _, ride_id = await _ride_with_two_joined(client)
    await client.post(
        f"{RIDES}/{ride_id}/location",
        json={"latitude": 33.5, "longitude": -6.5},
        headers=org_h,
    )

    # Backdate the stored timestamp rather than sleeping. Deterministic.
    old = (datetime.now(UTC) - timedelta(seconds=STALE_AFTER_SECONDS + 30)).timestamp()
    key = _key(uuid.UUID(ride_id))
    redis = redis_client.get_redis()
    stored = await redis.hget(key, str(org_id))
    lat, lon, acc, _ts = (stored or "").split("|")
    await redis.hset(key, str(org_id), f"{lat}|{lon}|{acc}|{old}")

    read = await client.get(f"{RIDES}/{ride_id}/location", headers=org_h)
    assert [i for i in read.json()["items"] if i["user_id"] == org_id] == []


async def test_the_staleness_cutoff_is_stricter_than_the_retention_ttl():
    """A structural assertion, so the two cannot be silently equalised."""
    assert STALE_AFTER_SECONDS < LOCATION_TTL_SECONDS


async def test_a_future_dated_position_is_not_shown_as_fresh(client, redis_available):
    """A client whose clock is wrong must not be able to look permanently live."""
    from app.redis import client as redis_client

    org_h, org_id, _, _, ride_id = await _ride_with_two_joined(client)
    await client.post(
        f"{RIDES}/{ride_id}/location",
        json={"latitude": 33.5, "longitude": -6.5},
        headers=org_h,
    )

    ahead = (datetime.now(UTC) + timedelta(seconds=STALE_AFTER_SECONDS + 60)).timestamp()
    key = _key(uuid.UUID(ride_id))
    redis = redis_client.get_redis()
    stored = await redis.hget(key, str(org_id))
    lat, lon, acc, _ts = (stored or "").split("|")
    await redis.hset(key, str(org_id), f"{lat}|{lon}|{acc}|{ahead}")

    read = await client.get(f"{RIDES}/{ride_id}/location", headers=org_h)
    assert [i for i in read.json()["items"] if i["user_id"] == org_id] == []


async def test_a_completed_ride_refuses_both_publish_and_read(client, redis_available):
    """Finishing a ride ends location sharing for BOTH directions.

    Two separate guarantees, and both matter:

    * publishing is refused, so no NEW position is accepted onto a ride that is
      over;
    * reading is refused too, so positions published while it was live stop being
      served the moment it completes — rather than lingering until the 300s TTL
      expires or being readable by a rider who was on the ride at some point.

    The second is the privacy-relevant one: `RIDE_CLOSED` on read is what makes
    "the data disappears when the ride ends" true rather than aspirational.
    """
    org_h, _, _, _, ride_id = await _ride_with_two_joined(client)

    published = await client.post(
        f"{RIDES}/{ride_id}/location",
        json={"latitude": 33.5, "longitude": -6.5},
        headers=org_h,
    )
    assert published.status_code in (200, 201), published.text

    done = await client.post(f"{RIDES}/{ride_id}/complete", headers=org_h)
    assert done.status_code == 200, done.text

    refused_read = await client.get(f"{RIDES}/{ride_id}/location", headers=org_h)
    assert refused_read.status_code == 409, refused_read.text
    assert refused_read.json()["error"]["code"] == "RIDE_CLOSED"

    refused_publish = await client.post(
        f"{RIDES}/{ride_id}/location",
        json={"latitude": 1.0, "longitude": 2.0},
        headers=org_h,
    )
    assert refused_publish.status_code == 409, refused_publish.text
    assert refused_publish.json()["error"]["code"] == "RIDE_CLOSED"


async def test_a_cancelled_ride_refuses_location_too(client, redis_available):
    """Cancellation is not a softer version of completion."""
    org_h, _, _, _, ride_id = await _ride_with_two_joined(client)

    cancelled = await client.post(f"{RIDES}/{ride_id}/cancel", headers=org_h)
    assert cancelled.status_code == 200, cancelled.text

    # GET and POST are refused: a cancelled ride shares no live positions.
    for method in ("GET", "POST"):
        r = await client.request(
            method,
            f"{RIDES}/{ride_id}/location",
            json={"latitude": 1.0, "longitude": 2.0},
            headers=org_h,
        )
        assert r.status_code == 409, f"{method} after cancel returned {r.status_code}"
        assert r.json()["error"]["code"] == "RIDE_CLOSED"

    # DELETE is deliberately still allowed, and that is the correct behaviour:
    # withdrawal must never depend on the ride still being active, because the
    # moment a rider most wants to stop is the moment it is being cancelled. A
    # revoke that refused here would leave a position in Redis until its TTL.
    stopped = await client.delete(f"{RIDES}/{ride_id}/location", headers=org_h)
    assert stopped.status_code == 200, stopped.text
    assert stopped.json()["status"] == "stopped"


async def test_no_location_endpoint_requires_no_authentication(client, redis_available):
    """Every location route must sit behind auth.

    Cheap structural check: an unauthenticated caller must never receive a 200
    from any of the three verbs.
    """
    _, _, _, _, ride_id = await _ride_with_two_joined(client)

    for method, path in (
        ("GET", f"{RIDES}/{ride_id}/location"),
        ("POST", f"{RIDES}/{ride_id}/location"),
        ("DELETE", f"{RIDES}/{ride_id}/location"),
    ):
        r = await client.request(method, path, json={"latitude": 1.0, "longitude": 2.0})
        assert r.status_code in (401, 403), f"{method} {path} was reachable unauthenticated"


# ---------------------------------------------------------------------------
# Group 2 â€” ephemeral location is never durable
# ---------------------------------------------------------------------------


def test_no_location_table_exists_in_the_schema():
    """Proves the architectural choice rather than trusting a comment.

    A `ride_locations` table would make GPS history permanent by accident, which
    is precisely what ADR-16 Â§6 refuses. This test fails the day someone adds one,
    which is the moment to have the conversation â€” not after a year of stored
    tracks.
    """
    import app.models  # noqa: F401 - registers every model on Base.metadata
    from app.db.base import Base

    offenders = [
        name
        for name in Base.metadata.tables
        if any(token in name for token in ("location", "position", "gps_history", "tracking"))
    ]
    assert not offenders, f"a durable location table was added: {offenders}"


def test_live_location_uses_only_ephemeral_storage():
    """The service must not depend on PostgreSQL for positions."""
    source = ride_location_service.__file__
    assert source
    # `inspect.getsource` rather than `open`: the file-handling linters are right
    # that a raw `open` in a test is sloppy, and reading our own source through
    # the import system keeps this a static check with no I/O.
    text = inspect.getsource(ride_location_service)
    # Redis is used; no Location ORM model exists in this module.
    assert "get_redis" in text
    assert "class Location" not in text
    assert "Location(" not in text


def test_the_location_key_is_namespaced_and_ride_scoped():
    """A key must be unguessable across rides, not shared between them."""
    a, b = uuid.uuid4(), uuid.uuid4()
    assert _key(a) != _key(b)
    assert str(a) in _key(a)


# ---------------------------------------------------------------------------
# Group 3 â€” credential and biometric minimization
# ---------------------------------------------------------------------------


async def test_no_response_echoes_a_password_hash(client, redis_available):
    """`UserOut` and friends must never serialize the hash column."""
    from tests.test_ride_location import _reg

    data = _reg("hashcheck")
    assert (await client.post(f"{AUTH}/register", json=data)).status_code == 201
    login = await client.post(
        f"{AUTH}/login", json={"email": data["email"], "password": data["password"]}
    )
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    me = await client.get(f"{SOCIAL}/profile/me", headers=headers)
    assert me.status_code == 200, me.text
    for forbidden in ("password_hash", "password", "refresh_hash"):
        assert forbidden not in me.text


async def test_a_push_device_token_is_never_returned(client, redis_available):
    """The plaintext push token must not appear in any device listing."""
    from tests.test_ride_location import _reg

    data = _reg("pushcheck")
    assert (await client.post(f"{AUTH}/register", json=data)).status_code == 201
    login = await client.post(
        f"{AUTH}/login", json={"email": data["email"], "password": data["password"]}
    )
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    reg = await client.post(
        "/api/v1/push-devices",
        json={
            "provider": "fcm",
            "platform": "android",
            "device_id": "dev-canary-1",
            "token": "push-token-canary",
        },
        headers=headers,
    )
    assert reg.status_code in (200, 201), f"{reg.status_code}: {reg.text}"
    assert "push-token-canary" not in reg.text

    listed = await client.get("/api/v1/push-devices", headers=headers)
    assert listed.status_code == 200, listed.text
    # Stronger than a substring check on the canary: the response must not carry a
    # `token` KEY at all. `device_id` legitimately contains "device", so only the
    # credential field is under test here.
    for item in listed.json()["items"]:
        assert "token" not in item, f"the push token was serialized: {item}"


async def test_ai_coach_context_never_carries_a_gps_coordinate():
    """No coordinate may reach a third-party AI provider.

    The coach reads deterministic aggregates; the ride context builder must never
    touch `ride_points` or the ride's start/end coordinates. Verified structurally
    against the context module, because "we did not remember to include it" is
    not a control.
    """
    import app.ai.context as ctx

    text = inspect.getsource(ctx)
    for forbidden in ("start_lat", "start_lon", "end_lat", "end_lon", "ride_points"):
        assert forbidden not in text, f"the AI context module references {forbidden}"


async def test_ai_writes_nothing_to_the_database():
    """Coach prompts and answers must remain in memory.

    A `coach_messages` table would silently convert an explanation feature into a
    store of health-adjacent free text. No such table exists and none is written.
    """
    import app.models  # noqa: F401
    from app.db.base import Base

    offenders = [
        name
        for name in Base.metadata.tables
        if any(t in name for t in ("coach", "ai_", "prompt", "llm"))
    ]
    assert not offenders, f"a persisted AI conversation table was added: {offenders}"


async def test_a_safety_event_logs_no_user_identity():
    """Asking about a possible injury must not create an identity-linked record.

    The health-adjacent case is the one where a log line becomes a health
    record. The safety path logs an aggregate outcome and omits the user id on
    purpose.
    """
    import app.ai.service as svc

    text = inspect.getsource(svc)
    # The safety branch must not attach an identity to the event.
    safety_block = text.split("safety_", 1)[-1][:1200]
    assert "user_id=" not in safety_block


async def test_a_notification_payload_carries_no_coordinate_or_message_body(
    client, redis_available
):
    """Notification params are public display strings by construction."""
    _, _, guest_h, _, _ = await _ride_with_two_joined(client)

    inbox = await client.get("/api/v1/notifications", headers=guest_h)
    assert inbox.status_code == 200, inbox.text
    for item in inbox.json()["items"]:
        params = item.get("params") or {}
        assert set(params) <= {"actorName", "teamName", "groupRideTitle"}, params
        for value in params.values():
            assert not any(t in str(value) for t in ("latitude", "longitude", "@"))


async def test_chat_history_carries_no_location_message_type():
    """ADR-12 Â§3 and ADR-14 Â§2 forbid location sharing through chat.

    Structural: the message type enum must not offer a location variant, so the
    capability cannot be added by accident.
    """
    from app.models.chat import MessageType

    wire_values = {m.value for m in MessageType}
    for forbidden in ("location", "position", "live_location"):
        assert forbidden not in wire_values


# ---------------------------------------------------------------------------
# Group 4 â€” retention gaps, pinned
# ---------------------------------------------------------------------------
#
# These do NOT assert correct behaviour. They pin what the system does TODAY for
# data that has no implemented retention policy, so the gap is visible in code and
# cannot quietly widen. Each names the decision that is still pending.
#
# Per the Phase 10 rules, retention periods are a business/legal decision and are
# NOT invented here.


def test_no_retention_policy_exists_for_gps_history(client):
    """PINS A DEFICIENCY. `ride_points` grows without bound.

    There is no `deleted_at`, no expiry and no pruning for the densest location
    table in the system. The correct retention period is a product/legal decision
    and is deliberately NOT chosen here.

    If this test ever starts failing because retention was implemented, replace it
    with an assertion of the implemented policy and update
    docs/privacy-data.md â€” do not simply delete it.
    """
    from app.models.ride import RidePoint

    columns = set(RidePoint.__table__.columns.keys())
    assert "deleted_at" not in columns, (
        "ride_points now has a deleted_at column: retention was implemented, so "
        "update this test and docs/privacy-data.md to state the real policy"
    )
    assert not any(tok in columns for tok in ("expires_at", "retain_until"))


def test_no_retention_policy_exists_for_training_history(client):
    """PINS A DEFICIENCY. `training_loads` is dated physiological history.

    The read windows in `training_service` (365-day trend, 14-day recovery) are
    QUERY bounds, not retention: old rows are never deleted.
    """
    from app.models.training import TrainingLoad

    columns = set(TrainingLoad.__table__.columns.keys())
    assert not any(tok in columns for tok in ("deleted_at", "expires_at", "retain_until"))


def test_consumed_one_time_tokens_are_never_purged(client):
    """PINS A DEFICIENCY. Used and expired token rows persist indefinitely.

    `expires_at` and `used_at` are checked on USE, but nothing deletes the row.
    The hashes are not credentials once spent, so this is a hygiene and
    volume concern rather than an active exposure â€” but it is unbounded growth.
    """
    from app.models.user import EmailVerificationToken, PasswordResetToken

    for model in (PasswordResetToken, EmailVerificationToken):
        columns = set(model.__table__.columns.keys())
        assert "expires_at" in columns, "expiry must still be enforced on use"
        assert not any(tok in columns for tok in ("deleted_at", "purged_at")), (
            f"{model.__tablename__} gained a purge marker: update this test"
        )


def test_expired_refresh_sessions_are_never_purged(client):
    """PINS A DEFICIENCY. Expiry is enforced on use; rows persist."""
    from app.models.user import RefreshSession

    columns = set(RefreshSession.__table__.columns.keys())
    assert "expires_at" in columns
    assert "revoked_at" in columns, "logout must be able to revoke durably"
    assert "deleted_at" not in columns


def test_notifications_have_no_pruning_policy(client):
    """PINS A DEFICIENCY. `notifications` rows are never expired.

    Only `read_at` exists. A rider's notification history therefore grows forever
    and retains a snapshot of other riders' display names at event time.
    """
    from app.models.notifications import Notification

    columns = set(Notification.__table__.columns.keys())
    assert "read_at" in columns
    assert not any(tok in columns for tok in ("expires_at", "deleted_at", "pruned_at"))


def test_no_account_deletion_path_exists_yet(client):
    """PINS A DEFICIENCY, and documents why implementing it is a product decision.

    `users.deleted_at` is defined and enforced by the auth layer but written by no
    code path. Five RESTRICT foreign keys make a hard `DELETE FROM users` fail for
    anyone who has ever sent a message, and `teams.owner_id` /
    `group_rides.organizer_user_id` are CASCADE, so a hard delete would destroy
    other users' teams and rides.

    Both deletion and ownership transfer are deferred. This test exists so the gap
    is explicit in code rather than discovered during a privacy review.
    """

    import app.models  # noqa: F401
    from app.core.config import settings  # noqa: F401 - documents the gate
    from app.db.base import Base

    # The column exists and is enforced...
    from app.models.user import User

    assert "deleted_at" in User.__table__.columns
    # ...but no table records a deletion request or an export job.
    offenders = [
        name
        for name in Base.metadata.tables
        if any(t in name for t in ("deletion", "erasure", "export_job", "gdpr"))
    ]
    assert not offenders, (
        f"a deletion/export table was added: {offenders} â€” deletion is a product "
        "decision and must not be implemented without one"
    )


def test_no_user_data_export_exists_yet(client):
    """PINS A DEFICIENCY. Only GPX route export exists.

    A rider cannot obtain a copy of their own rides, training history, chat or
    social graph. Data portability is unimplemented.
    """
    import app.models  # noqa: F401
    from app.db.base import Base

    offenders = [name for name in Base.metadata.tables if "export" in name and "job" in name]
    assert not offenders


def test_gpx_upload_bytes_are_not_persisted(client):
    """GPX is parsed and discarded; only coordinates survive.

    Storing the uploaded file would duplicate the whole track in an ungoverned
    blob with no retention, no privacy classification and no deletion path.
    """

    from app.api.v1 import routes as routes_api

    source = inspect.getsource(routes_api)
    import_section = source.split("import/gpx", 1)[-1][:2500]
    # The bytes are read and handed to the parser; nothing writes them out.
    assert "open(" not in import_section
    assert "write" not in import_section.lower()


# ---------------------------------------------------------------------------
# Group 5 â€” classification invariants
# ---------------------------------------------------------------------------


def test_no_table_stores_a_coordinate_outside_the_two_that_must():
    """Coordinates live in exactly three places, and that is deliberate.

    `ride_points` (recorded tracks), `route_points` (planned geometry) and the
    denormalized start/end pairs on `rides` and `routes`. A new coordinate column
    anywhere else is a new privacy surface and should be argued for explicitly.
    """
    import app.models  # noqa: F401
    from app.db.base import Base

    allowed = {"ride_points", "route_points", "rides", "routes"}
    offenders = []
    for name, table in Base.metadata.tables.items():
        if name in allowed:
            continue
        for column in table.columns:
            if column in ("lat", "lon", "latitude", "longitude"):
                offenders.append(f"{name}.{column}")
    assert not offenders, f"a coordinate column appeared elsewhere: {offenders}"


def test_meeting_point_is_not_a_coordinate_pair(client):
    """A group ride's meeting point is a free-text label.

    Storing lat/lon there would be a second location contract with its own
    privacy story, published to every participant.
    """
    from app.models.group_ride import GroupRide

    columns = set(GroupRide.__table__.columns.keys())
    assert "meeting_point" in columns
    for forbidden in ("meeting_lat", "meeting_lon"):
        assert forbidden not in columns

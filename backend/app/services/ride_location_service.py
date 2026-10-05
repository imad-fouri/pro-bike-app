"""Phase 9 live ride location: consented, ephemeral, Redis-only (ADR-16 §6).

This module exists instead of a database table, and that choice is the whole
design. Consider what a `ride_locations` table would mean: every row a retained
fact about where a person was, in every backup, subject to every future "just add
an index" request and every future analytics question. Location is consent-based
and time-limited, so it is stored with a TTL and nothing else. When the TTL
expires the position is gone — there is no `deleted_at`, no soft delete, and no
history to restore.

THE RULES, in the order they matter:

1. **SHARING IS OFF BY DEFAULT AND IS PER-RIDER, ALWAYS.** This module never
   infers consent from being on a ride. `publish` requires an explicit call from
   a rider who is currently JOINED on a ride that is `open` or `started`.
   Joining a ride grants no one the right to see where you are.

2. **A STALE POSITION IS NOT SHOWN AS A LIVE ONE.** Every read compares the
   server-side timestamp against `STALE_AFTER_SECONDS` and hides anything older.
   The check happens on READ rather than being left to the TTL because the TTL
   (300s) and the acceptable display age (60s) are different questions: the TTL is
   about deleting data, staleness is about not lying to a rider.

3. **AN OUTAGE IS A 503, NEVER AN EMPTY MAP.** If Redis is unavailable this
   module raises. Returning "no riders are visible" would be indistinguishable from
   "nobody is sharing", and a rider concluding the group has lost them is exactly
   the failure this must never produce. This is the single most important
   behaviour in the file.

4. **COORDINATES ARE NEVER LOGGED.** Not in errors, not in "empty map" paths, not
   in debug output. Logs are routinely shipped and read by people who should not
   know where a rider lives. `user_id` and counts are logged instead.

5. **A BLOCK REMOVES VISIBILITY IN BOTH DIRECTIONS** (ADR-16 §7). Two riders who
   have blocked each other never see each other's position and cannot publish to
   one another, even as fellow participants on the same ride. A block is a
   personal boundary that ride membership does not dissolve.

6. **NO HISTORY, NO TRAIL, NO "WHERE EVERYONE WENT".** One hash per ride holds
   the CURRENT position of each sharing rider. Nothing accumulates.
"""

import logging
import uuid
from collections.abc import Awaitable
from datetime import UTC, datetime
from typing import cast

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.group_ride import (
    GroupRide,
    GroupRideParticipant,
    GroupRideParticipantStatus,
    GroupRideStatus,
)
from app.models.social import UserBlock
from app.models.user import User
from app.services.chat_service import _joined_in_ride
from app.services.group_ride_service import RideError, _lock_ride, _ride_row

log = logging.getLogger("cyclecoach")

#: How long a published position is RETAINED. This is a deletion guarantee, not a
#: display guarantee: after this, the data is gone from Redis.
LOCATION_TTL_SECONDS = 300

#: How old a position may be and still be shown as live. Deliberately much
#: shorter than the TTL: a rider whose last ping was 90 seconds ago is not
#: "here now", and the client is told the age so it can grey the dot out.
STALE_AFTER_SECONDS = 60


#: Redis hash key for one ride's positions. The `gr9` prefix keeps this phase's
#: keys namespaced from anything a previous phase wrote.
def _key(ride_id: uuid.UUID) -> str:
    return f"gr9:share:{ride_id}"


def _now() -> datetime:
    return datetime.now(UTC)


# NOTE ON THE `cast` CALLS BELOW. redis-py 5.3 declares every
# `redis.asyncio.Redis` method as returning `Union[Awaitable[T], T]` so that one
# class can serve the sync and the async API. `get_redis()` always hands back the
# asyncio client, so only the awaitable branch ever occurs — but mypy cannot know
# that, and `await` on the union is an error. Each call site casts to the
# awaitable type once, which names the real return type at the same time.


async def _blocked_either_way(db: AsyncSession, a: uuid.UUID, b: uuid.UUID) -> bool:
    res = await db.execute(
        select(UserBlock.id).where(
            ((UserBlock.blocker_user_id == a) & (UserBlock.blocked_user_id == b))
            | ((UserBlock.blocker_user_id == b) & (UserBlock.blocked_user_id == a))
        )
    )
    return res.scalar_one_or_none() is not None


async def _require_shareable(db: AsyncSession, viewer: User, ride_id: uuid.UUID) -> GroupRide:
    """Gate for both publish and read.

    A rider must be JOINED on a ride that is `open` or `started`. The same gate
    serves both directions because the audience for a position IS the ride's
    joined roster: there is no separate "who may see me" list to get out of step
    with "who is on the ride".
    """
    ride = await _ride_row(db, ride_id)
    if ride is None:
        raise RideError("RIDE_NOT_FOUND", "Ride not found.", 404)
    if not await _joined_in_ride(db, ride.id, viewer.id):
        raise RideError("RIDE_NOT_FOUND", "Ride not found.", 404)
    if ride.status not in (GroupRideStatus.OPEN, GroupRideStatus.STARTED):
        # A completed or cancelled ride shares no live positions. This is also
        # what makes the data disappear when the ride ends, rather than lingering
        # until the TTL.
        raise RideError("RIDE_CLOSED", "This ride is not active.", 409)
    return ride


async def publish(
    db: AsyncSession,
    viewer: User,
    ride_id: uuid.UUID,
    *,
    latitude: float,
    longitude: float,
    accuracy_m: float | None,
) -> dict:
    """Publish or replace THIS rider's position. Explicit consent, every time.

    Rate limited at the router. The lock is the ride's, shared with the roster
    mutations, so a `start` or `cancel` cannot commit mid-publish and leave a
    position visible on a ride that just became terminal.
    """
    await _lock_ride(db, ride_id)
    ride = await _require_shareable(db, viewer, ride_id)

    if ride.organizer_user_id != viewer.id and await _blocked_either_way(
        db, viewer.id, ride.organizer_user_id
    ):
        raise RideError("RIDE_BLOCKED", "You cannot share your location.", 403)

    from app.redis.client import get_redis

    # The server's timestamp, not the client's (rule 2): a client whose clock is
    # wrong must not be able to make its own position look fresh forever.
    stamp = _now()
    key = _key(ride.id)
    redis = get_redis()
    try:
        pipe = redis.pipeline()
        pipe.hset(
            key,
            mapping={
                str(
                    viewer.id
                ): f"{latitude}|{longitude}|{accuracy_m if accuracy_m is not None else ''}|{stamp.timestamp()}",
            },
        )
        pipe.expire(key, LOCATION_TTL_SECONDS)
        await cast(Awaitable[list[object]], pipe.execute())
    except Exception as exc:  # noqa: BLE001
        # Rule 3: an outage must not be swallowed here either, or a rider would
        # believe they are sharing when nothing was stored. The exception TYPE is
        # not logged because a redis-py error string can echo the command and its
        # arguments — which are this rider's coordinates (rule 4).
        log.error(
            "ride_location_publish_failed",
            extra={"group_ride_id": str(ride.id), "error_type": type(exc).__name__},
        )
        raise RideError("LOCATION_UNAVAILABLE", "Location sharing is unavailable.", 503) from None

    # Coordinates deliberately absent from the log line and the return value.
    log.info("ride_location_published", extra={"group_ride_id": str(ride.id)})
    return {"status": "sharing", "expires_in_seconds": LOCATION_TTL_SECONDS}


async def stop_sharing(db: AsyncSession, viewer: User, ride_id: uuid.UUID) -> dict:
    """Stop sharing immediately. Always allowed, including on a closed ride.

    "Always" is the point: withdrawal must never depend on the ride still being
    in a state this module considers active, because the moment a rider most wants
    to stop is the moment the ride is being cancelled.
    """
    ride = await _ride_row(db, ride_id)
    if ride is None:
        raise RideError("RIDE_NOT_FOUND", "Ride not found.", 404)
    from app.redis.client import get_redis

    redis = get_redis()
    try:
        await cast(Awaitable[int], redis.hdel(_key(ride.id), str(viewer.id)))
    except Exception as exc:  # noqa: BLE001
        log.error(
            "ride_location_stop_failed",
            extra={"group_ride_id": str(ride.id), "error_type": type(exc).__name__},
        )
        raise RideError("LOCATION_UNAVAILABLE", "Location sharing is unavailable.", 503) from None
    log.info("ride_location_stopped", extra={"group_ride_id": str(ride.id)})
    return {"status": "stopped"}


async def list_locations(db: AsyncSession, viewer: User, ride_id: uuid.UUID) -> dict:
    """The riders currently sharing, as visible to THIS viewer.

    Filters, in this order: joined roster, not stale, not blocked either way. A
    blocked rider is not even counted as visible — not "hidden but present".
    """
    ride = await _require_shareable(db, viewer, ride_id)

    from app.redis.client import get_redis

    redis = get_redis()
    try:
        raw = await cast(Awaitable[dict[str, str]], redis.hgetall(_key(ride.id)))
    except Exception as exc:  # noqa: BLE001
        # Rule 3, verbatim: never answer with an empty map on an outage.
        log.error(
            "ride_location_read_failed",
            extra={"group_ride_id": str(ride.id), "error_type": type(exc).__name__},
        )
        raise RideError("LOCATION_UNAVAILABLE", "Location sharing is unavailable.", 503) from None

    # The joined roster is re-read from PostgreSQL rather than trusted from the
    # hash: the hash says who last published, this says who is actually allowed to
    # be seen. A rider removed an instant ago must vanish immediately, not when
    # their TTL runs out.
    roster_ids = await _joined_user_ids(db, ride.id)
    now = _now()
    visible: list[tuple[uuid.UUID, float, float, float | None, int]] = []
    for user_str, blob in raw.items():
        try:
            user_id = uuid.UUID(user_str)
        except ValueError:
            continue
        if user_id not in roster_ids:
            # Published at some point, no longer entitled to be seen.
            continue
        if user_id != viewer.id and await _blocked_either_way(db, viewer.id, user_id):
            continue
        lat, lon, acc, ts = _parse(blob)
        if lat is None or lon is None or ts is None:
            continue
        age = int((now - ts).total_seconds())
        if age > STALE_AFTER_SECONDS or age < -STALE_AFTER_SECONDS:
            # Older than the display limit, or timestamped in the future (a clock
            # problem upstream). Both are "do not present this as live".
            continue
        visible.append((user_id, lat, lon, acc, age))

    from app.services.group_ride_service import _identities

    identities = await _identities(db, [v[0] for v in visible])
    items = [
        {
            "user_id": str(user_id),
            "username": identities.get(user_id, {}).get("username", ""),
            "display_name": identities.get(user_id, {}).get("display_name"),
            "avatar_url": identities.get(user_id, {}).get("avatar_url"),
            "latitude": lat,
            "longitude": lon,
            "accuracy_m": acc,
            "age_seconds": age,
            "is_self": user_id == viewer.id,
        }
        for user_id, lat, lon, acc, age in visible
    ]
    return {
        "items": items,
        "stale_after_seconds": STALE_AFTER_SECONDS,
        "expires_in_seconds": LOCATION_TTL_SECONDS,
    }


async def _joined_user_ids(db: AsyncSession, ride_id: uuid.UUID) -> set[uuid.UUID]:
    res = await db.execute(
        select(GroupRideParticipant.user_id).where(
            GroupRideParticipant.group_ride_id == ride_id,
            GroupRideParticipant.status == GroupRideParticipantStatus.JOINED,
        )
    )
    return set(res.scalars())


def _parse(blob: str) -> tuple[float | None, float | None, float | None, datetime | None]:
    """Parse one stored position. Returns Nones rather than raising.

    A malformed entry is skipped, not fatal: one rider's bad write must not blank
    the map for everyone else, and there is no repair path worth building for a
    value that expires within minutes anyway.
    """
    parts = blob.split("|")
    if len(parts) != 4:
        return None, None, None, None
    try:
        lat = float(parts[0])
        lon = float(parts[1])
        acc = float(parts[2]) if parts[2] else None
        ts = datetime.fromtimestamp(float(parts[3]), tz=UTC)
    except ValueError:
        return None, None, None, None
    return lat, lon, acc, ts

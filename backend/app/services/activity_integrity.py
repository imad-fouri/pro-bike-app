"""WS-RC: what counts as a competitive activity, and the WS-AC seam.

Two engines consume this module - the challenge engine and the ranking
engine - so "qualifying activity" has exactly one definition in the codebase.

ANTI-CHEAT BOUNDARY
-------------------
Advanced anti-cheat/integrity evaluation is intentionally deferred to WS-AC.

The pipeline the future workstream plugs into already exists::

    eligible activity
            |
            v
    integrity evaluation          <- WS-RC: ``evaluate`` is ACCEPTED below
            |
            +---- accepted        -> aggregated, progress applied, points held
            +---- rejected        -> never aggregated (WS-AC)
            +---- pending review  -> aggregated as provisional (WS-AC decision)

``ActivityIntegrityStatus`` is that vocabulary. WS-RC always returns
``ACCEPTED`` because it uses the existing trusted ride state - a ride the
server itself finalised, from points the server itself accepted - and no
heuristic thresholds are invented here. WS-AC replaces the body of
``evaluate`` without touching either engine, and adds ``rejected`` /
``pending_review`` branches to ``QUALIFYING_RIDE``.

Nothing in this module reads a plan, a subscription, or an entitlement:
ranking and challenges are core product, never monetization-gated.
"""

import enum
import uuid

from sqlalchemy import and_
from sqlalchemy.sql import ColumnElement

from app.models.ride import Ride, RideStatus


class ActivityIntegrityStatus(str, enum.Enum):
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    PENDING_REVIEW = "pending_review"


#: The eligibility predicate every competitive aggregate is built on.
#:
#: * owned by a real ride row (ownership is the FK, never a request field)
#: * ``completed`` - discarded and in-progress rides are not competition
#: * ``ended_at`` present - a window needs an end instant to fall inside
#:
#: Visibility, blocking, tombstones and geography are *scope* concerns and
#: live in the engines, not here, because they differ per leaderboard.
QUALIFYING_RIDE = and_(
    Ride.status == RideStatus.COMPLETED,
    Ride.ended_at.is_not(None),
)


def qualifying_rides(user_id: uuid.UUID) -> ColumnElement[bool]:
    """Ownership plus the qualifying predicate, for per-rider queries."""
    return and_(Ride.user_id == user_id, QUALIFYING_RIDE)


async def evaluate(ride: Ride) -> ActivityIntegrityStatus:
    """Return the integrity verdict for one qualifying ride.

    Deliberately a coroutine with no I/O: WS-AC will need the database, a
    history of the rider's rides, and possibly a queue, and neither engine
    should have to change shape when it does.

    WS-RC always answers ``ACCEPTED``: the ride was finalised by the server
    from server-accepted points, which is the trust level this workstream is
    allowed to assume.
    """
    return ActivityIntegrityStatus.ACCEPTED

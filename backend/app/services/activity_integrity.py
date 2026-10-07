"""WS-AC: what counts as a competitive activity, and the integrity engine.

Two engines consume this module - the challenge engine and the ranking
engine - so "qualifying activity" has exactly one definition in the codebase:
a **completed** ride whose persisted integrity verdict is ``ACCEPTED``.

ANTI-CHEAT BOUNDARY
-------------------
Integrity evaluation is deliberately deterministic, explainable and total over
stored facts (docs/activity-integrity.md). For one ride it needs the ride row
and its accepted points - no history, no queue, no heuristics - so it runs
inline as the ride is finalised, atomically with the ``COMPLETED`` commit:

    eligible activity
            |
            v
    integrity evaluation          <- runs in ``ride_service._finalize``
            |
            +---- accepted        -> aggregated, progress applied, points held
            +---- suspicious      -> retained, NOT aggregated (WS-AC)
            +---- rejected        -> retained, never aggregated (WS-AC)

The verdict vocabulary is stored on the ride (``models/ride.IntegrityStatus``)
and ``QUALIFYING_RIDE`` requires ``accepted``. ``None`` (a ride that was never
evaluated, i.e. any in-progress ride) is NOT eligible: the failure mode of
"forgot to evaluate" is exclusion, never silent inclusion.

Nothing in this module reads a plan, a subscription, or an entitlement:
ranking and challenges are core product, never monetization-gated.
"""

import itertools
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import and_
from sqlalchemy.sql import ColumnElement

from app.models.ride import IntegrityStatus, Ride, RideStatus
from app.services import gps_engine
from app.services.gps_engine import DUPLICATE_DISTANCE_M, SPEED_MAX_M_S, Observation

#: The rule-set version that produced a verdict. Stored on the ride so a later
#: rule set ships as a new version with its own evaluation; the eligibility
#: predicate never silently re-rates rides under an older version.
CALCULATION_VERSION = "v1"

# --- determinism: rule ids are fixed, ordered, and greppable -----------------
# REJECT rules are structural impossibilities. The request schema (PointIn)
# and the engine already forbid them, so on the publicly reachable stack they
# are defensive: if any writes path, bug or back-door ever stores them, the
# ride is categorically excluded for audit, not aggregated.
GPS_COORDINATE_INVALID = "GPS_COORDINATE_INVALID"
GPS_POINT_ORDER_INVALID = "GPS_POINT_ORDER_INVALID"
GPS_TIME_SEQUENCE_INVALID = "GPS_TIME_SEQUENCE_INVALID"
GPS_SPEED_IMPLAUSIBLE = "GPS_SPEED_IMPLAUSIBLE"

# SUSPICIOUS rules are internally consistent but unverifiable-as-real riding.
# They keep the point data and the ride in history, and the ride out of the
# aggregate - exactly the "retained but not aggregated initially" mandate.
RIDE_NO_ACCEPTED_POINTS = "RIDE_NO_ACCEPTED_POINTS"
GPS_DISTANCE_MISMATCH = "GPS_DISTANCE_MISMATCH"

_REJECT_RULES = frozenset(
    {
        GPS_COORDINATE_INVALID,
        GPS_POINT_ORDER_INVALID,
        GPS_TIME_SEQUENCE_INVALID,
        GPS_SPEED_IMPLAUSIBLE,
    }
)
_SUSPICIOUS_RULES = frozenset({RIDE_NO_ACCEPTED_POINTS, GPS_DISTANCE_MISMATCH})

#: Physical coordinate bounds, identical to ``schemas.ride.PointIn`` and to the
#: engine. Kept alongside the rule so the three can never drift silently.
_LAT_BOUNDS = (-90.0, 90.0)
_LON_BOUNDS = (-180.0, 180.0)

#: Distance tolerance (m) for the recompute-vs-last-stored check. The engine
#: folds incrementally during ingest and re-folds once at finish; both round to
#: the centimetre, so a healthy ride differs by at most a few centimetres.
#: ``DUPLICATE_DISTANCE_M`` (1 m) is the repository's existing "same fix"
#: resolution constant and bounds legitimate rounding by two orders of degree
#: while still catching a real write-path tamper.
_DISTANCE_MISMATCH_TOLERANCE_M = Decimal(str(DUPLICATE_DISTANCE_M))


@dataclass(frozen=True)
class IntegrityResult:
    """One deterministic verdict. Never constructed by callers; returned by
    ``evaluate``."""

    status: IntegrityStatus
    #: Triggered rules in the documented emission order (stable output).
    rules_triggered: list[str] = field(default_factory=list)
    calculation_version: str = CALCULATION_VERSION
    evaluated_at: datetime | None = None

    @property
    def eligible(self) -> bool:
        return self.status == IntegrityStatus.ACCEPTED


#: The eligibility predicate every competitive aggregate is built on.
#:
#: * owned by a real ride row (ownership is the FK, never a request field)
#: * ``completed`` - discarded and in-progress rides are not competition
#: * ``ended_at`` present - a window needs an end instant to fall inside
#: * ``integrity_status == accepted`` - WS-AC: no verdict is not eligible.
#:
#: Visibility, blocking, tombstones and geography are *scope* concerns and
#: live in the engines, not here, because they differ per leaderboard.
QUALIFYING_RIDE = and_(
    Ride.status == RideStatus.COMPLETED,
    Ride.ended_at.is_not(None),
    Ride.integrity_status == IntegrityStatus.ACCEPTED,
)


def qualifying_rides(user_id: uuid.UUID) -> ColumnElement[bool]:
    """Ownership plus the qualifying predicate, for per-rider queries."""
    return and_(Ride.user_id == user_id, QUALIFYING_RIDE)


def _latlon_invalid(points: list[Observation]) -> bool:
    return any(
        not (
            _LAT_BOUNDS[0] <= p.lat <= _LAT_BOUNDS[1] and _LON_BOUNDS[0] <= p.lon <= _LON_BOUNDS[1]
        )
        for p in points
    )


def _seq_out_of_order(points: list[Observation]) -> bool:
    return any(b.seq <= a.seq for a, b in itertools.pairwise(points))


def _time_out_of_sequence(points: list[Observation]) -> bool:
    return any(b.recorded_at <= a.recorded_at for a, b in itertools.pairwise(points))


def evaluate(
    ride: Ride,
    accepted_points: list[Observation],
    *,
    pre_finalize_distance_m: Decimal | None = None,
) -> IntegrityResult:
    """Return the integrity verdict for one completed ride.

    Pure and total: no database, no history, no machine learning. The rules are
    the v1 set above, each documented in ``docs/activity-integrity.md``.
    ``accepted_points`` are the server-accepted observations, ordered by seq;
    ``pre_finalize_distance_m`` is the last stored cumulative total before the
    finish-path recompute and is only available on the finish path.

    Severity composes: any REJECT rule => REJECTED; else any SUSPICIOUS rule =>
    SUSPICIOUS; else ACCEPTED. ``rules_triggered`` reports every rule that
    fired, so the reason is observable without broadcasting GPS.
    """
    rules: list[str] = []

    if _latlon_invalid(accepted_points):
        rules.append(GPS_COORDINATE_INVALID)
    if _seq_out_of_order(accepted_points):
        rules.append(GPS_POINT_ORDER_INVALID)
    if _time_out_of_sequence(accepted_points):
        rules.append(GPS_TIME_SEQUENCE_INVALID)
    if ride.max_speed_m_s is not None and float(ride.max_speed_m_s) > SPEED_MAX_M_S:
        rules.append(GPS_SPEED_IMPLAUSIBLE)
    if not accepted_points:
        rules.append(RIDE_NO_ACCEPTED_POINTS)
    if pre_finalize_distance_m is not None:
        recomputed = gps_engine.recompute(accepted_points)["distance_m"]
        if abs(Decimal(str(recomputed)) - pre_finalize_distance_m) > _DISTANCE_MISMATCH_TOLERANCE_M:
            rules.append(GPS_DISTANCE_MISMATCH)

    if _REJECT_RULES & set(rules):
        status = IntegrityStatus.REJECTED
    elif _SUSPICIOUS_RULES & set(rules):
        status = IntegrityStatus.SUSPICIOUS
    else:
        status = IntegrityStatus.ACCEPTED

    return IntegrityResult(
        status=status,
        rules_triggered=rules,
        calculation_version=CALCULATION_VERSION,
        evaluated_at=datetime.now(UTC),
    )

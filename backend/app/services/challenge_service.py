"""WS-RC: challenges - creation, lifecycle, membership, and progress.

Server authority
----------------
There is no code path in this module that reads a score, a rank, a progress
value or a point amount from a request. Progress is *recomputed* from the
``rides`` table for a participant who already exists as a member, and points
are written only by the completion insert. A client can ask for a challenge to
be joined or left; it can never tell the engine what the answer is.

Idempotency
-----------
Three guarantees, in increasing order of strength:

1. ``uq_challenge_progress_challenge_ride`` - a ride is recorded against a
   challenge at most once, so a duplicated finalisation, a retried worker or a
   concurrent second attempt cannot apply twice.
2. ``uq_challenge_completions_pair`` - a participant is awarded at most once;
   the second ``INSERT ... ON CONFLICT DO NOTHING`` returns no row and the
   award is skipped.
3. Progress is recomputed from source rather than incremented, so even a crash
   between "recorded" and "applied" is repaired by the next run instead of
   leaving a stale total.

The membership row is also taken ``FOR UPDATE`` inside ``_sync_member`` so two
concurrent processors cannot interleave a read-modify-write of the progress
columns.

Anti-cheat
----------
``ParticipantState.DISQUALIFIED`` is reserved and never written here.
Advanced anti-cheat/integrity evaluation is intentionally deferred to WS-AC;
this engine consumes ``activity_integrity`` exactly as the ranking engine does.
"""

import enum
import logging
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import Date, and_, case, cast, delete, exists, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.metrics import record_competition_event
from app.models.challenge import (
    DEFAULT_CHALLENGE_POINTS,
    MAX_CHALLENGE_DAYS,
    MAX_CHALLENGE_POINTS,
    MIN_CHALLENGE_POINTS,
    Challenge,
    ChallengeCompletion,
    ChallengeMembership,
    ChallengeMetric,
    ChallengeProgressEvent,
    ChallengeScope,
    ChallengeStatus,
    ChallengeVisibility,
    ParticipantState,
)
from app.models.ride import Ride, RideStatus
from app.models.social import (
    FriendRelationship,
    RelationshipStatus,
    SocialProfile,
    UserBlock,
)
from app.models.team import TeamMembership, TeamMembershipStatus
from app.models.training import TrainingActivity
from app.models.user import User, UserProfile
from app.services import activity_integrity

logger = logging.getLogger("cyclecoach")

#: Participant states a live challenge may still be processed for.
_PROCESSABLE = (ParticipantState.JOINED, ParticipantState.ACTIVE)
#: Stored challenge states that still accept processing (draft never runs,
#: cancelled is void, completed is finalised).
_PROCESSABLE_STATUS = (ChallengeStatus.SCHEDULED, ChallengeStatus.ACTIVE)


class ChallengeAccess(str, enum.Enum):
    """How much of a challenge a caller may see.

    ``NONE``    not scope-eligible - 404, so ids cannot be probed.
    ``LIMITED`` scope-eligible but not inside a private challenge: the detail
                is shown so the rider can decide to join, the roster is not.
    ``FULL``    creator, participant, or any public challenge in scope.
    """

    NONE = "none"
    LIMITED = "limited"
    FULL = "full"


class ChallengeError(Exception):
    def __init__(self, code: str, message: str, status: int = 422):
        self.code = code
        self.message = message
        self.status = status
        super().__init__(message)


def utcnow() -> datetime:
    return datetime.now(UTC)


def _log(event: str, **fields: object) -> None:
    # Ids only: who did what to which challenge. Never titles, descriptions,
    # or a rider identity beyond an opaque id.
    logger.info("challenge", extra={"event": event, **fields})


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


def derive_state(challenge: Challenge, now: datetime) -> str:
    """The challenge's *effective* state, as pure data.

    The six vocabulary values are draft / scheduled / active / completed /
    cancelled / expired, where ``expired`` means "the window has passed but
    finalisation has not run yet". Read paths persist it (``_refresh_status``),
    so callers normally see ``completed`` instead of ``expired``.
    """
    if challenge.status == ChallengeStatus.DRAFT:
        return "draft"
    if challenge.status == ChallengeStatus.CANCELLED:
        return "cancelled"
    if challenge.status == ChallengeStatus.COMPLETED:
        return "completed"
    if now < challenge.start_at:
        return "scheduled"
    if now < challenge.end_at:
        return "active"
    return "expired"


async def _refresh_status(db: AsyncSession, challenge: Challenge) -> str:
    """Persist the derived state so the stored status never lies about the clock.

    Finalising on read is deliberate: this workstream ships no scheduler, and
    the only thing finalisation must do is stop joins and freeze results.
    """
    now = utcnow()
    state = derive_state(challenge, now)
    if state == "expired":
        challenge.status = ChallengeStatus.COMPLETED
        challenge.finalized_at = challenge.finalized_at or now
        state = "completed"
    elif state == "active" and challenge.status == ChallengeStatus.SCHEDULED:
        challenge.status = ChallengeStatus.ACTIVE
    elif state == "scheduled" and challenge.status == ChallengeStatus.ACTIVE:
        challenge.status = ChallengeStatus.SCHEDULED
    if challenge.status != ChallengeStatus.DRAFT:
        challenge.published_at = challenge.published_at or now
    challenge.updated_at = now
    await db.flush()
    return state


async def _refresh_statuses(db: AsyncSession) -> None:
    """Batch version of ``_refresh_status`` for list reads: two statements,
    not one per row. Drafts are never touched - a draft's window is
    irrelevant until someone publishes it."""
    now = utcnow()
    await db.execute(
        update(Challenge)
        .where(
            Challenge.status.in_((ChallengeStatus.SCHEDULED, ChallengeStatus.ACTIVE)),
            Challenge.end_at <= now,
        )
        .values(status=ChallengeStatus.COMPLETED, finalized_at=now, updated_at=now)
    )
    await db.execute(
        update(Challenge)
        .where(
            Challenge.status == ChallengeStatus.SCHEDULED,
            Challenge.start_at <= now,
            Challenge.end_at > now,
        )
        .values(status=ChallengeStatus.ACTIVE, updated_at=now)
    )


# ---------------------------------------------------------------------------
# Authorization helpers
# ---------------------------------------------------------------------------


async def _scope_eligible(db: AsyncSession, user_id, challenge: Challenge) -> bool:
    """May this rider see/join this challenge *at all*?

    Derived from stored facts - the creator id, the scope, the friendship
    table, the membership table - never from anything a request body carries.
    """
    if challenge.creator_user_id == user_id:
        return True
    if challenge.scope == ChallengeScope.GLOBAL:
        return True
    if challenge.scope == ChallengeScope.INDIVIDUAL:
        return False
    if challenge.scope == ChallengeScope.FRIENDS:
        row = await db.execute(
            select(FriendRelationship.id)
            .where(
                FriendRelationship.status == RelationshipStatus.ACCEPTED,
                or_(
                    and_(
                        FriendRelationship.user_a_id == challenge.creator_user_id,
                        FriendRelationship.user_b_id == user_id,
                    ),
                    and_(
                        FriendRelationship.user_a_id == user_id,
                        FriendRelationship.user_b_id == challenge.creator_user_id,
                    ),
                ),
            )
            .limit(1)
        )
        return row.first() is not None
    row = await db.execute(
        select(TeamMembership.id)
        .where(
            TeamMembership.team_id == challenge.team_id,
            TeamMembership.user_id == user_id,
            TeamMembership.status == TeamMembershipStatus.ACTIVE,
        )
        .limit(1)
    )
    return row.first() is not None


async def _membership(db: AsyncSession, challenge_id, user_id) -> ChallengeMembership | None:
    row = await db.execute(
        select(ChallengeMembership).where(
            ChallengeMembership.challenge_id == challenge_id,
            ChallengeMembership.user_id == user_id,
        )
    )
    return row.scalar_one_or_none()


async def access_for(db: AsyncSession, user_id, challenge: Challenge) -> ChallengeAccess:
    if not await _scope_eligible(db, user_id, challenge):
        return ChallengeAccess.NONE
    if challenge.creator_user_id == user_id:
        return ChallengeAccess.FULL
    if challenge.visibility == ChallengeVisibility.PUBLIC:
        return ChallengeAccess.FULL
    if await _membership(db, challenge.id, user_id) is not None:
        return ChallengeAccess.FULL
    return ChallengeAccess.LIMITED


async def _require(db: AsyncSession, user_id, challenge_id) -> tuple[Challenge, ChallengeAccess]:
    row = await db.execute(select(Challenge).where(Challenge.id == challenge_id))
    challenge = row.scalar_one_or_none()
    if challenge is None:
        raise ChallengeError("CHALLENGE_NOT_FOUND", "Challenge not found.", 404)
    access = await access_for(db, user_id, challenge)
    if access == ChallengeAccess.NONE:
        # The same answer as a challenge that does not exist: an id is not an
        # oracle for private or out-of-scope challenges.
        raise ChallengeError("CHALLENGE_NOT_FOUND", "Challenge not found.", 404)
    return challenge, access


# ---------------------------------------------------------------------------
# Metric computation
# ---------------------------------------------------------------------------


def _window(challenge: Challenge, membership: ChallengeMembership):
    """Half-open window ``[start_at, end_at)``, additionally gated on when the
    rider joined: there is no retroactive credit for rides completed before
    joining, which is what stops "wait until the end, then enter if you would
    have won"."""
    return and_(
        Ride.ended_at >= challenge.start_at,
        Ride.ended_at < challenge.end_at,
        Ride.ended_at >= membership.joined_at,
    )


async def _compute_value(
    db: AsyncSession, challenge: Challenge, membership: ChallengeMembership
) -> Decimal:
    """Recompute a participant's progress from source.

    Recomputation (rather than ``value = value + delta``) is what makes the
    engine safe to re-run: the answer depends only on the ride table, so a
    repeated run converges instead of compounding.
    """
    base = and_(
        activity_integrity.qualifying_rides(membership.user_id),
        _window(challenge, membership),
    )
    metric = challenge.metric

    stmt: Any
    if metric == ChallengeMetric.DISTANCE:
        stmt = select(func.coalesce(func.sum(Ride.distance_m), 0)).where(base)
    elif metric == ChallengeMetric.ELEVATION:
        stmt = select(func.coalesce(func.sum(Ride.elevation_gain_m), 0)).where(base)
    elif metric == ChallengeMetric.RIDES:
        stmt = select(func.count()).select_from(Ride).where(base)
    elif metric == ChallengeMetric.TRAINING:
        ride_ids = select(Ride.id).where(base)
        stmt = (
            select(func.count())
            .select_from(TrainingActivity)
            .where(TrainingActivity.ride_id.in_(ride_ids))
        )
    else:
        # STREAK: longest run of consecutive UTC calendar days on which the
        # rider has at least one qualifying ride inside the window. At most
        # MAX_CHALLENGE_DAYS distinct days, so the set is bounded and the run
        # is computed in Python where it can be read - and tested - directly.
        days = (
            await db.execute(
                select(cast(func.timezone("UTC", Ride.ended_at), Date())).where(base).distinct()
            )
        ).scalars()
        longest = run = 0
        previous = None
        for day in sorted(d for d in days if d is not None):
            run = run + 1 if previous is not None and (day - previous).days == 1 else 1
            previous = day
            longest = max(longest, run)
        return Decimal(longest)

    raw: object = (await db.execute(stmt)).scalar_one()
    try:
        return Decimal(str(raw)).quantize(Decimal("0.01"))
    except (InvalidOperation, TypeError):  # pragma: no cover - defensive
        return Decimal(0)


# ---------------------------------------------------------------------------
# The engine
# ---------------------------------------------------------------------------


async def _sync_member(
    db: AsyncSession, challenge: Challenge, membership: ChallengeMembership
) -> None:
    """Apply every not-yet-applied ride, recompute progress, award once.

    Serialised on the membership row so two concurrent processors cannot
    interleave the read-modify-write of ``progress_value``; the ledger and
    completion constraints remain the final arbiter if that lock is bypassed.
    """
    if membership.state not in _PROCESSABLE:
        return
    now = utcnow()
    locked = (
        await db.execute(
            select(ChallengeMembership)
            .where(ChallengeMembership.id == membership.id)
            .with_for_update()
        )
    ).scalar_one()
    if locked.state not in _PROCESSABLE:
        return

    ride_ids = list(
        (
            await db.execute(
                select(Ride.id).where(
                    activity_integrity.qualifying_rides(locked.user_id),
                    _window(challenge, locked),
                )
            )
        ).scalars()
    )
    for ride_id in ride_ids:
        # ON CONFLICT DO NOTHING: a duplicate is a no-op, not an error, and
        # not a rollback of the work that already happened.
        await db.execute(
            pg_insert(ChallengeProgressEvent)
            .values(
                challenge_id=challenge.id,
                user_id=locked.user_id,
                ride_id=ride_id,
                applied_at=now,
            )
            .on_conflict_do_nothing()
        )

    value = await _compute_value(db, challenge, locked)
    locked.progress_value = value
    locked.progress_rides = len(ride_ids)
    locked.updated_at = now

    if value >= challenge.target:
        awarded = await _record_completion(db, challenge, locked, ride_ids, now)
        locked.state = ParticipantState.COMPLETED
        locked.completed_at = locked.completed_at or now
        if awarded:
            _emit("completed", challenge.id, locked.user_id)
    elif locked.state == ParticipantState.JOINED and ride_ids:
        locked.state = ParticipantState.ACTIVE
    await db.flush()


async def _record_completion(
    db: AsyncSession,
    challenge: Challenge,
    membership: ChallengeMembership,
    ride_ids: list,
    now: datetime,
) -> bool:
    """Insert the award row. True only for the call that created it."""
    res = await db.execute(
        pg_insert(ChallengeCompletion)
        .values(
            challenge_id=challenge.id,
            user_id=membership.user_id,
            points_awarded=challenge.points,
            completed_at=membership.completed_at or now,
            source_ride_id=ride_ids[-1] if ride_ids else None,
        )
        .on_conflict_do_nothing()
        .returning(ChallengeCompletion.id)
    )
    awarded = res.first() is not None
    if awarded:
        record_competition_event(event="challenge_complete", outcome="ok")
    return awarded


def _emit(kind: str, challenge_id, user_id) -> None:
    """The notification seam.

    A domain event is recorded (bounded metric + id-only log) but no
    notification row is written: ``NotificationType`` is a Postgres enum and
    Postgres cannot DROP an enum value, so adding one would survive
    ``alembic downgrade -1`` and break the migration-cycle contract. The
    invitation flow is also not designed yet. Both are deferred decisions in
    docs/PHASE_WS_RC_REPORT.md; when they land, this is the one function that
    starts writing through ``notification_service``.
    """
    _log("challenge_event", kind=kind, challenge_id=challenge_id, user_id=user_id)


async def on_ride_completed(db: AsyncSession, ride: Ride) -> None:
    """Post-processing hook wired into ``ride_service.transition``.

    Best-effort by contract, exactly like ``training_service.sync_ride``: a
    derived metric must never be able to fail a ride. Any failure is logged
    with an error category (never an identity) and is repaired by the next
    read of the challenge, because progress is recomputed rather than
    incremented.
    """
    if ride.status != RideStatus.COMPLETED or ride.ended_at is None:
        return
    try:
        # Commit whatever the preceding hook left pending, so a failure below
        # can only ever roll back this work.
        await db.commit()
    except Exception:  # noqa: BLE001 - pragma: no cover, defensive
        await db.rollback()
        return
    try:
        rows = (
            await db.execute(
                select(Challenge, ChallengeMembership)
                .join(
                    ChallengeMembership,
                    ChallengeMembership.challenge_id == Challenge.id,
                )
                .where(
                    ChallengeMembership.user_id == ride.user_id,
                    ChallengeMembership.state.in_(_PROCESSABLE),
                    Challenge.status.in_(_PROCESSABLE_STATUS),
                    Challenge.start_at <= ride.ended_at,
                    Challenge.end_at > ride.ended_at,
                )
            )
        ).all()
        for challenge, membership in rows:
            await _sync_member(db, challenge, membership)
        if rows:
            await db.commit()
    except Exception as exc:  # noqa: BLE001 - deliberate: see docstring
        await db.rollback()
        _log(
            "challenge_sync_failed",
            error_category=type(exc).__name__,
            ride_id=ride.id,
        )


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


def _normalise_target(metric: ChallengeMetric, raw: object) -> Decimal:
    try:
        value = Decimal(str(raw))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ChallengeError("INVALID_TARGET", "Target must be a positive number.") from exc
    if not value.is_finite() or value <= 0:
        raise ChallengeError("INVALID_TARGET", "Target must be a positive number.")
    if metric == ChallengeMetric.STREAK:
        if value != value.to_integral_value():
            raise ChallengeError("INVALID_TARGET", "A streak target is a whole number of days.")
        if value > Decimal(MAX_CHALLENGE_DAYS):
            raise ChallengeError(
                "INVALID_TARGET", f"A streak target cannot exceed {MAX_CHALLENGE_DAYS} days."
            )
    if value > Decimal("99999999999999.99"):
        raise ChallengeError("INVALID_TARGET", "Target is out of range.")
    return value


async def create_challenge(db: AsyncSession, user: User, data: dict) -> Challenge:
    now = utcnow()
    start_at = data["start_at"]
    end_at = data["end_at"]
    if start_at.tzinfo is None or end_at.tzinfo is None:
        raise ChallengeError("INVALID_WINDOW", "Window instants must carry a timezone.")
    if end_at <= start_at:
        raise ChallengeError("INVALID_WINDOW", "The window must end after it starts.")
    if end_at <= now:
        raise ChallengeError("INVALID_WINDOW", "The window must end in the future.")
    if end_at - start_at > timedelta(days=MAX_CHALLENGE_DAYS):
        raise ChallengeError(
            "INVALID_WINDOW", f"A challenge cannot run longer than {MAX_CHALLENGE_DAYS} days."
        )

    scope = ChallengeScope(data["scope"])
    team_id = data.get("team_id")
    if scope == ChallengeScope.TEAM:
        if not team_id:
            raise ChallengeError("TEAM_REQUIRED", "A team challenge needs a team.")
        row = await db.execute(
            select(TeamMembership.id)
            .where(
                TeamMembership.team_id == team_id,
                TeamMembership.user_id == user.id,
                TeamMembership.status == TeamMembershipStatus.ACTIVE,
            )
            .limit(1)
        )
        if row.first() is None:
            raise ChallengeError("NOT_TEAM_MEMBER", "You are not a member of that team.", 403)
    elif team_id is not None:
        raise ChallengeError("TEAM_REQUIRED", "Only a team challenge carries a team id.")

    metric = ChallengeMetric(data["metric"])
    target = _normalise_target(metric, data["target"])
    raw_points = data.get("points")
    points = DEFAULT_CHALLENGE_POINTS if raw_points is None else raw_points
    if not isinstance(points, int) or not (MIN_CHALLENGE_POINTS <= points <= MAX_CHALLENGE_POINTS):
        raise ChallengeError(
            "INVALID_POINTS",
            f"Points must be between {MIN_CHALLENGE_POINTS} and {MAX_CHALLENGE_POINTS}.",
        )

    visibility = ChallengeVisibility(data.get("visibility", ChallengeVisibility.PUBLIC.value))
    publish = bool(data.get("publish", True))
    status = ChallengeStatus.DRAFT
    if publish:
        status = ChallengeStatus.SCHEDULED if start_at > now else ChallengeStatus.ACTIVE

    challenge = Challenge(
        creator_user_id=user.id,
        team_id=team_id if scope == ChallengeScope.TEAM else None,
        title=data["title"].strip(),
        description=(data.get("description") or "").strip() or None,
        metric=metric,
        target=target,
        points=points,
        scope=scope,
        visibility=visibility,
        status=status,
        start_at=start_at,
        end_at=end_at,
        created_at=now,
        updated_at=now,
        published_at=now if publish else None,
    )
    db.add(challenge)
    await db.flush()

    if publish and scope == ChallengeScope.INDIVIDUAL:
        # An individual challenge is the creator's own. Joining it for them
        # keeps "one participant" true by construction instead of by a second
        # round-trip the UI could forget to make.
        db.add(
            ChallengeMembership(
                challenge_id=challenge.id,
                user_id=user.id,
                state=ParticipantState.JOINED,
                progress_value=Decimal(0),
                progress_rides=0,
                joined_at=now,
                updated_at=now,
            )
        )
        await db.flush()

    record_competition_event(event="challenge_create", outcome="ok")
    _log("challenge_created", challenge_id=challenge.id, user_id=user.id)
    return challenge


async def publish_challenge(db: AsyncSession, user: User, challenge_id) -> Challenge:
    challenge, _ = await _require(db, user.id, challenge_id)
    if challenge.creator_user_id != user.id:
        raise ChallengeError("NOT_CREATOR", "Only the creator can publish.", 403)
    if challenge.status != ChallengeStatus.DRAFT:
        raise ChallengeError("NOT_A_DRAFT", "Only a draft can be published.", 409)
    now = utcnow()
    if challenge.end_at <= now:
        raise ChallengeError("WINDOW_CLOSED", "The window has already closed.", 409)
    challenge.status = (
        ChallengeStatus.SCHEDULED if challenge.start_at > now else ChallengeStatus.ACTIVE
    )
    challenge.published_at = now
    challenge.updated_at = now
    await db.flush()
    record_competition_event(event="challenge_publish", outcome="ok")
    return challenge


async def cancel_challenge(db: AsyncSession, user: User, challenge_id) -> Challenge:
    challenge, _ = await _require(db, user.id, challenge_id)
    if challenge.creator_user_id != user.id:
        raise ChallengeError("NOT_CREATOR", "Only the creator can cancel.", 403)
    if challenge.status in (ChallengeStatus.COMPLETED, ChallengeStatus.CANCELLED):
        raise ChallengeError("ALREADY_FINAL", "That challenge is already final.", 409)
    now = utcnow()
    challenge.status = ChallengeStatus.CANCELLED
    challenge.cancelled_at = now
    challenge.updated_at = now
    await db.flush()
    record_competition_event(event="challenge_cancel", outcome="ok")
    _log("challenge_cancelled", challenge_id=challenge.id, user_id=user.id)
    return challenge


async def join_challenge(db: AsyncSession, user: User, challenge_id) -> ChallengeMembership:
    challenge, _ = await _require(db, user.id, challenge_id)
    now = utcnow()
    state = derive_state(challenge, now)
    if state not in ("scheduled", "active"):
        raise ChallengeError("CHALLENGE_CLOSED", "That challenge is not open to join.", 409)
    if challenge.scope == ChallengeScope.INDIVIDUAL and challenge.creator_user_id != user.id:
        raise ChallengeError("NOT_ELIGIBLE", "This is an individual challenge.", 403)

    existing = await _membership(db, challenge.id, user.id)
    if existing is not None:
        if existing.state in _PROCESSABLE:
            # A duplicate join is a no-op, not an error: retries must be safe.
            return existing
        if existing.state == ParticipantState.COMPLETED:
            raise ChallengeError("ALREADY_COMPLETED", "You already finished it.", 409)
        # LEFT -> rejoin, starting clean. The new joined_at is what makes the
        # no-retroactive-credit rule true after a rejoin.
        existing.state = ParticipantState.JOINED
        existing.joined_at = now
        existing.left_at = None
        existing.completed_at = None
        existing.progress_value = Decimal(0)
        existing.progress_rides = 0
        existing.updated_at = now
        await _clear_ledger(db, challenge.id, user.id)
        await db.flush()
        record_competition_event(event="challenge_join", outcome="ok")
        return existing

    created = ChallengeMembership(
        challenge_id=challenge.id,
        user_id=user.id,
        state=ParticipantState.JOINED,
        progress_value=Decimal(0),
        progress_rides=0,
        joined_at=now,
        updated_at=now,
    )
    db.add(created)
    try:
        await db.flush()
    except Exception as exc:  # a concurrent duplicate join lost the race
        await db.rollback()
        _log(
            "challenge_join_conflict",
            challenge_id=challenge.id,
            error_category=type(exc).__name__,
        )
        recovered = await _membership(db, challenge.id, user.id)
        if recovered is None:
            raise ChallengeError("CHALLENGE_CLOSED", "That challenge is not open.", 409) from exc
        return recovered
    record_competition_event(event="challenge_join", outcome="ok")
    _log("challenge_joined", challenge_id=challenge.id, user_id=user.id)
    return created


async def leave_challenge(db: AsyncSession, user: User, challenge_id) -> ChallengeMembership:
    challenge, _ = await _require(db, user.id, challenge_id)
    membership = await _membership(db, challenge.id, user.id)
    if membership is None:
        raise ChallengeError("NOT_A_PARTICIPANT", "You have not joined.", 409)
    if membership.state == ParticipantState.LEFT:
        # A duplicate leave is a no-op, not an error.
        return membership
    if membership.state not in _PROCESSABLE:
        # completed / disqualified: the award stands.
        raise ChallengeError("ALREADY_COMPLETED", "You have already finished it.", 409)

    now = utcnow()
    membership.state = ParticipantState.LEFT
    membership.left_at = now
    membership.completed_at = None
    membership.progress_value = Decimal(0)
    membership.progress_rides = 0
    membership.updated_at = now
    await _clear_ledger(db, challenge.id, user.id)
    await db.flush()
    record_competition_event(event="challenge_leave", outcome="ok")
    _log("challenge_left", challenge_id=challenge.id, user_id=user.id)
    return membership


async def _clear_ledger(db: AsyncSession, challenge_id, user_id) -> None:
    """Leaving starts clean: the ledger goes with it, and because progress is
    recomputed (never incremented) re-processing after a rejoin cannot double
    count - the new ``joined_at`` gate excludes everything older."""
    await db.execute(
        delete(ChallengeProgressEvent).where(
            ChallengeProgressEvent.challenge_id == challenge_id,
            ChallengeProgressEvent.user_id == user_id,
        )
    )


# ---------------------------------------------------------------------------
# Queries
# ---------------------------------------------------------------------------


def _visibility_conditions(viewer):
    """`eligible` x `visible`, both purely relational - a list read is two
    statements no matter how long the list is."""
    eligible = or_(
        Challenge.creator_user_id == viewer,
        Challenge.scope == ChallengeScope.GLOBAL,
        and_(
            Challenge.scope == ChallengeScope.FRIENDS,
            exists().where(
                FriendRelationship.status == RelationshipStatus.ACCEPTED,
                or_(
                    and_(
                        FriendRelationship.user_a_id == Challenge.creator_user_id,
                        FriendRelationship.user_b_id == viewer,
                    ),
                    and_(
                        FriendRelationship.user_a_id == viewer,
                        FriendRelationship.user_b_id == Challenge.creator_user_id,
                    ),
                ),
            ),
        ),
        and_(
            Challenge.scope == ChallengeScope.TEAM,
            exists().where(
                TeamMembership.team_id == Challenge.team_id,
                TeamMembership.user_id == viewer,
                TeamMembership.status == TeamMembershipStatus.ACTIVE,
            ),
        ),
    )
    visible = or_(
        Challenge.visibility == ChallengeVisibility.PUBLIC,
        Challenge.creator_user_id == viewer,
        exists().where(
            ChallengeMembership.challenge_id == Challenge.id,
            ChallengeMembership.user_id == viewer,
        ),
    )
    return eligible, visible


async def _participant_count(db: AsyncSession, challenge_id) -> int:
    return (
        await db.execute(
            select(func.count())
            .select_from(ChallengeMembership)
            .where(
                ChallengeMembership.challenge_id == challenge_id,
                ChallengeMembership.state != ParticipantState.LEFT,
            )
        )
    ).scalar_one()


async def list_challenges(
    db: AsyncSession,
    user: User,
    *,
    scope: str | None = None,
    mine: bool = False,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[dict], int]:
    await _refresh_statuses(db)
    viewer = user.id
    eligible, visible = _visibility_conditions(viewer)
    conditions = [eligible, visible]
    if scope is not None:
        conditions.append(Challenge.scope == ChallengeScope(scope))
    if mine:
        conditions.append(
            or_(
                Challenge.creator_user_id == viewer,
                exists().where(
                    ChallengeMembership.challenge_id == Challenge.id,
                    ChallengeMembership.user_id == viewer,
                ),
            )
        )

    total = (
        await db.execute(select(func.count()).select_from(Challenge).where(*conditions))
    ).scalar_one()
    challenges = list(
        (
            await db.execute(
                select(Challenge)
                .where(*conditions)
                .order_by(
                    case(
                        (Challenge.status == ChallengeStatus.ACTIVE, 0),
                        (Challenge.status == ChallengeStatus.SCHEDULED, 1),
                        (Challenge.status == ChallengeStatus.DRAFT, 2),
                        (Challenge.status == ChallengeStatus.COMPLETED, 3),
                        else_=4,
                    ),
                    Challenge.end_at.desc(),
                    Challenge.id.asc(),
                )
                .limit(page_size)
                .offset((page - 1) * page_size)
            )
        ).scalars()
    )
    if not challenges:
        return [], total

    # Two batched lookups for the whole page rather than two per row.
    ids = [c.id for c in challenges]
    memberships = {
        m.challenge_id: m
        for m in (
            await db.execute(
                select(ChallengeMembership).where(
                    ChallengeMembership.challenge_id.in_(ids),
                    ChallengeMembership.user_id == viewer,
                )
            )
        ).scalars()
    }
    counts = dict(
        (
            await db.execute(
                select(ChallengeMembership.challenge_id, func.count())
                .where(
                    ChallengeMembership.challenge_id.in_(ids),
                    ChallengeMembership.state != ParticipantState.LEFT,
                )
                .group_by(ChallengeMembership.challenge_id)
            )
        ).all()
    )
    now = utcnow()
    return [
        challenge_view(
            user,
            c,
            derive_state(c, now),
            memberships.get(c.id),
            counts.get(c.id, 0),
        )
        for c in challenges
    ], total


def challenge_view(
    user: User,
    challenge: Challenge,
    state: str,
    membership: ChallengeMembership | None,
    participant_count: int | None,
) -> dict:
    """Everything a detail row carries. Progress and counts are *outputs*; no
    field here is ever read back in as an input.

    ``participant_count`` is ``None`` when the viewer may not see who else is
    in a private challenge, so the shape of the response still says nothing.
    """
    progress_value = membership.progress_value if membership else Decimal(0)
    target = challenge.target
    percent = Decimal("0.0")
    if target > 0:
        percent = (progress_value / target * 100).quantize(Decimal("0.1"))

    return {
        "id": challenge.id,
        "title": challenge.title,
        "description": challenge.description,
        "metric": challenge.metric,
        "target": challenge.target,
        "points": challenge.points,
        "scope": challenge.scope,
        "visibility": challenge.visibility,
        "state": state,
        "status": challenge.status,
        "team_id": challenge.team_id,
        "start_at": challenge.start_at,
        "end_at": challenge.end_at,
        "created_at": challenge.created_at,
        "is_creator": challenge.creator_user_id == user.id,
        "participant_count": participant_count,
        "viewer_state": membership.state if membership else None,
        "viewer_progress": {
            "value": progress_value,
            "target": target,
            "percent": percent,
            "rides": membership.progress_rides if membership else 0,
            "completed": membership is not None and membership.state == ParticipantState.COMPLETED,
            "joined_at": membership.joined_at if membership else None,
            "completed_at": membership.completed_at if membership else None,
        },
        "can_join": membership is None
        and state in ("scheduled", "active")
        and not (
            challenge.scope == ChallengeScope.INDIVIDUAL and challenge.creator_user_id != user.id
        ),
        "can_leave": membership is not None and membership.state in _PROCESSABLE,
    }


async def get_challenge(db: AsyncSession, user: User, challenge_id) -> dict:
    """Detail read, and the self-healing sync point for the caller's own
    progress.

    The sync recomputes from the ride table - it never accepts a value - so a
    hook that failed earlier repairs itself the next time the rider looks.
    """
    challenge, access = await _require(db, user.id, challenge_id)
    state = await _refresh_status(db, challenge)
    membership = await _membership(db, challenge.id, user.id)
    if membership is not None:
        try:
            await _sync_member(db, challenge, membership)
            await db.commit()
        except Exception as exc:  # noqa: BLE001 - a failed repair must not fail the read
            await db.rollback()
            _log(
                "challenge_sync_failed",
                error_category=type(exc).__name__,
                challenge_id=challenge.id,
            )
            challenge = (
                await db.execute(select(Challenge).where(Challenge.id == challenge_id))
            ).scalar_one()
            membership = await _membership(db, challenge.id, user.id)

    if access == ChallengeAccess.FULL:
        count: int | None = await _participant_count(db, challenge.id)
    else:
        count = None
    return challenge_view(user, challenge, state, membership, count)


async def leaderboard(
    db: AsyncSession, user: User, challenge_id, *, page: int = 1, page_size: int = 20
) -> tuple[list[dict], int]:
    """Ranked participants.

    Ranks are computed over the *whole* participant set, so every viewer sees
    the same number for the same rider; rows a viewer may not see are then
    dropped without renumbering, which is why ranks can contain gaps
    (docs/ranking-challenges.md 9).
    """
    challenge, access = await _require(db, user.id, challenge_id)
    await _refresh_status(db, challenge)
    if access != ChallengeAccess.FULL:
        raise ChallengeError("CHALLENGE_PRIVATE", "Join the challenge to see who is in it.", 403)

    ranked = (
        select(
            ChallengeMembership.user_id.label("user_id"),
            ChallengeMembership.progress_value.label("value"),
            ChallengeMembership.progress_rides.label("rides"),
            ChallengeMembership.state.label("state"),
            func.coalesce(SocialProfile.display_name, UserProfile.display_name).label(
                "display_name"
            ),
            SocialProfile.username.label("username"),
            SocialProfile.avatar_url.label("avatar_url"),
            func.rank().over(order_by=[ChallengeMembership.progress_value.desc()]).label("rank"),
        )
        .select_from(ChallengeMembership)
        .outerjoin(SocialProfile, SocialProfile.user_id == ChallengeMembership.user_id)
        .outerjoin(UserProfile, UserProfile.user_id == ChallengeMembership.user_id)
        .where(
            ChallengeMembership.challenge_id == challenge.id,
            ChallengeMembership.state.in_(
                (
                    ParticipantState.JOINED,
                    ParticipantState.ACTIVE,
                    ParticipantState.COMPLETED,
                )
            ),
        )
    ).subquery("challenge_ranked")

    hidden = exists().where(
        or_(
            and_(
                UserBlock.blocker_user_id == user.id,
                UserBlock.blocked_user_id == ranked.c.user_id,
            ),
            and_(
                UserBlock.blocker_user_id == ranked.c.user_id,
                UserBlock.blocked_user_id == user.id,
            ),
        )
    )
    total = (await db.execute(select(func.count()).select_from(ranked).where(~hidden))).scalar_one()
    rows = (
        (
            await db.execute(
                select(ranked)
                .where(~hidden)
                .order_by(ranked.c.value.desc(), ranked.c.user_id.asc())
                .limit(page_size)
                .offset((page - 1) * page_size)
            )
        )
        .mappings()
        .all()
    )
    return [dict(r) for r in rows], total

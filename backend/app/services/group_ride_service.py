"""Phase 9 group-ride service: one authoritative roster and lifecycle (ADR-16).

FIVE RULES HOLD EVERYWHERE IN THIS MODULE.

1. THE ORGANIZER IS THE ONLY AUTHORITY (ADR-16 §3). There is exactly one
   organizer per ride and no admin or co-organizer role, so "may this actor
   change the ride?" has exactly one answer: is this the organizer? Every
   lifecycle transition and every roster write except a rider's own withdrawal is
   gated on it. A shared-admin role would mean a second source of truth about
   who decides whether a ride happens.

2. MEMBERSHIP IS NOT TEAM MEMBERSHIP (ADR-16 §1). Nothing here reads or writes
   `team_memberships`. Leaving a team must not eject a rider from a ride in
   progress, and joining a team must not enroll them in somebody's ride — those
   are different facts and they get different rows.

3. AUTHORIZATION IS RE-DERIVED PER REQUEST, UNDER A LOCK. Every mutation takes
   the ride advisory lock FIRST and then re-reads the ride and the actor's roster
   row inside that lock. Nothing is trusted from a value the caller passed in, and
   nothing is trusted from a read taken before the lock was acquired — otherwise a
   request admitted just before a cancellation commits would still apply its
   write afterwards (the same rule ADR-14 §14 sets for chat).

4. WITHDRAWAL IS A CONSENT RIGHT AND IS NOT THE ORGANIZER'S TO GRANT. A rider may
   always leave, in any non-terminal state, and needs no permission. This is the
   deliberate asymmetry with §3: the organizer controls who may be ADDED, and
   never who may REMOVE THEMSELVES. A rider who feels trapped is exactly the
   situation a "you may not leave this ride" rule would create.

5. THE ROSTER FREEZES AT `started` (ADR-16 §3). Nobody may be added to a ride
   that is already rolling. Together with rule 4 that means a started ride can
   only ever lose participants, which is what makes the live-location view safe:
   the set of people who can see where others are only ever shrinks.

CONCURRENCY. One advisory transaction lock per ride (`gride:{id}`) serializes
every lifecycle transition and every roster mutation, so two simultaneous
accepts cannot both see `invited` and both transition, and a cancel racing an
invite cannot both win. The unique constraint on `(group_ride_id, user_id)` is
kept as the final arbiter, so a lost race re-reads the winning row instead of
creating a second roster entry.
"""

import logging
import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import redact
from app.models.group_ride import (
    GroupRide,
    GroupRideParticipant,
    GroupRideParticipantStatus,
    GroupRideRole,
    GroupRideStatus,
)
from app.models.route import Route, RouteVersion
from app.models.social import SocialProfile, UserBlock
from app.models.user import User, UserStatus
from app.services import notification_service

log = logging.getLogger("cyclecoach")

#: Terminal states. A ride in one of these accepts no roster and no lifecycle
#: change at all: it is a record, not an event.
TERMINAL = (GroupRideStatus.COMPLETED, GroupRideStatus.CANCELLED)


class RideError(Exception):
    """Business rejection, carrying the status the API should answer with."""

    def __init__(self, code: str, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


def _now() -> datetime:
    return datetime.now(UTC)


def _log(event: str, **fields: object) -> None:
    log.info(f"group_ride.{event}", extra=redact(dict(fields)))


async def _notify_safely(coro_factory, db: AsyncSession, **kwargs) -> None:
    """Run a notification hook, swallowing any failure.

    A notification is an accelerant, never a precondition. Every call site here is
    AFTER its own `db.commit()`, so the alternative to swallowing is worse than
    losing the notification: the rider would be shown a failure for an action that
    demonstrably succeeded, and would retry it. Retrying an invite is survivable
    (the roster pair is unique) but it re-opens a confirmation dialog over a
    roster that already grew, which reads as a bug.

    Same helper, same contract as `social_service._notify_safely` and
    `team_service._notify_safely`; `chat_service` inlines the same try/except.
    Duplicated four times on purpose — a shared base would mean a shared import
    edge between five services that are otherwise independent.
    """
    try:
        await coro_factory(db, **kwargs)
    except Exception as exc:  # noqa: BLE001 — never break the business action
        # `hook=` rather than `event=`: `_log`'s first positional parameter is
        # already named `event`, so passing `event=` as a field collides with it.
        _log(
            "notification_failed",
            hook=coro_factory.__name__,
            error_category=type(exc).__name__,
        )


# ---------------------------------------------------------------------------
# Locking
# ---------------------------------------------------------------------------


async def _lock_ride(db: AsyncSession, ride_id: uuid.UUID) -> None:
    await db.execute(select(func.pg_advisory_xact_lock(func.hashtext(f"gride:{ride_id}"))))


# ---------------------------------------------------------------------------
# Lookups and authorization
# ---------------------------------------------------------------------------


async def _ride_row(db: AsyncSession, ride_id: uuid.UUID) -> GroupRide | None:
    res = await db.execute(select(GroupRide).where(GroupRide.id == ride_id))
    return res.scalar_one_or_none()


async def _participant_row(
    db: AsyncSession, ride_id: uuid.UUID, user_id: uuid.UUID
) -> GroupRideParticipant | None:
    res = await db.execute(
        select(GroupRideParticipant).where(
            GroupRideParticipant.group_ride_id == ride_id,
            GroupRideParticipant.user_id == user_id,
        )
    )
    return res.scalar_one_or_none()


async def _roster(
    db: AsyncSession, ride_id: uuid.UUID
) -> list[GroupRideParticipant]:
    res = await db.execute(
        select(GroupRideParticipant)
        .where(GroupRideParticipant.group_ride_id == ride_id)
        # Organizer first, then invitation order. Stable ordering matters: this
        # list is what a rider sees, and "who is on this ride" answering in a
        # different order each call would read as a bug.
        .order_by(GroupRideParticipant.role.desc(), GroupRideParticipant.created_at)
    )
    return list(res.scalars())


async def _joined_ids(db: AsyncSession, ride_id: uuid.UUID) -> list[uuid.UUID]:
    res = await db.execute(
        select(GroupRideParticipant.user_id).where(
            GroupRideParticipant.group_ride_id == ride_id,
            GroupRideParticipant.status == GroupRideParticipantStatus.JOINED,
        )
    )
    return list(res.scalars())


async def _require_visible(db: AsyncSession, ride: GroupRide, viewer_id: uuid.UUID) -> GroupRide:
    """Refuse a ride the viewer is not currently ON, as 404.

    Visibility is the live roster STATUS, not the existence of a row (rule 1). A
    `declined`, `left` or `removed` row keeps existing so the rider's decision
    stays visible in their own ride list — but it grants no standing at all here,
    for the same reason a removed team member loses their team channel while
    keeping their membership row for attributability.

    The distinction is what makes withdrawal meaningful. If a `left` row still
    read the ride, a rider could not actually leave anything: they would keep
    seeing the roster, the meeting point and the route of a ride they have
    withdrawn from.
    """
    row = await _participant_row(db, ride.id, viewer_id)
    if row is None or row.status != GroupRideParticipantStatus.JOINED:
        # Same 404 as a ride that does not exist, so the endpoint cannot be used
        # to enumerate which ride ids are real, nor to learn that a particular
        # rider organized something.
        raise RideError("RIDE_NOT_FOUND", "Ride not found.", 404)
    return ride


async def _require_organizer(db: AsyncSession, ride: GroupRide, viewer_id: uuid.UUID) -> None:
    """Rule 1: only the organizer may act.

    Reads `organizer_user_id` rather than the roster row's role, because the ride
    row is the single authoritative statement of who organizes. If the two ever
    disagreed, this is the one that decides — the organizer row exists so the
    organizer can read and post like anyone else on the ride.
    """
    if ride.organizer_user_id != viewer_id:
        # 404, not 403. Confirming "this ride exists but is not yours" is exactly
        # the enumeration leak rule 1's 404 policy exists to prevent.
        raise RideError("RIDE_NOT_FOUND", "Ride not found.", 404)


async def _blocked_either_way(db: AsyncSession, a: uuid.UUID, b: uuid.UUID) -> bool:
    """Parenthesised explicitly; see the identical helper in notification_service."""
    res = await db.execute(
        select(UserBlock.id).where(
            ((UserBlock.blocker_user_id == a) & (UserBlock.blocked_user_id == b))
            | ((UserBlock.blocker_user_id == b) & (UserBlock.blocked_user_id == a))
        )
    )
    return res.scalar_one_or_none() is not None


async def _joinable(db: AsyncSession, ride: GroupRide, user_id: uuid.UUID) -> None:
    """Can this rider be on the ride at all?

    Two independent refusals, both checked here rather than at each call site:
    a blocked pair never shares a ride, and a DELETED rider is never invited.

    Note what is deliberately NOT refused: a ride whose `starts_at` has already
    passed. `starts_at` is a scheduled time, not the moment the ride began — the
    organizer is the only party who can say "we are rolling", and they say it by
    calling `start`. Blocking joins on a clock would mean a rider who accepted an
    invitation two hours before a late-running ride could be locked out of a ride
    that has not happened yet, with no rule about whether the organizer may still
    start it. Time-based refusal also cannot be enforced here anyway: the roster
    freeze at `started` is what actually stops anyone joining late, and that is a
    state, not a timestamp.
    """
    if ride.organizer_user_id != user_id and await _blocked_either_way(
        db, ride.organizer_user_id, user_id
    ):
        raise RideError("RIDE_BLOCKED", "You cannot join this ride.", 403)
    target = (
        await db.execute(
            select(User).where(User.id == user_id, User.deleted_at.is_(None))
        )
    ).scalar_one_or_none()
    if target is None or target.status != UserStatus.ACTIVE:
        raise RideError("RIDE_MEMBER_UNAVAILABLE", "That rider cannot be invited.", 400)


# ---------------------------------------------------------------------------
# Route pinning (ADR-16 §4)
# ---------------------------------------------------------------------------


async def _resolve_pin(
    db: AsyncSession, route_id: uuid.UUID | None, route_version: int | None
) -> tuple[uuid.UUID, int] | None:
    """Validate an immutable route pin, or refuse it.

    A pin names a version that MUST already exist. Defaulting to
    `route.current_version` is deliberately NOT done: the whole point of the pin
    is that the geometry is settled, and silently resolving "latest" would make a
    later edit of the route change what riders were invited to — which is
    precisely the failure ADR-09 §immutable-version exists to prevent.
    """
    if route_id is None and route_version is None:
        return None
    if route_id is None or route_version is None:
        # Both-or-neither is also a CHECK, but a clear message beats a constraint
        # violation here because this is a caller mistake, not a corrupted row.
        raise RideError("RIDE_ROUTE_INCOMPLETE", "Provide both route_id and route_version.", 400)
    route = (await db.execute(select(Route).where(Route.id == route_id))).scalar_one_or_none()
    if route is None or route.deleted_at is not None:
        raise RideError("ROUTE_NOT_FOUND", "Route not found.", 404)
    version = (
        await db.execute(
            select(RouteVersion).where(
                RouteVersion.route_id == route_id, RouteVersion.version_no == route_version
            )
        )
    ).scalar_one_or_none()
    if version is None:
        raise RideError("ROUTE_VERSION_NOT_FOUND", "Route version not found.", 404)
    return route_id, route_version


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------


async def _identities(db: AsyncSession, user_ids: list[uuid.UUID]) -> dict[uuid.UUID, dict]:
    """Public social projection. Reads `social_profiles`, never `users`.

    Same rule as chat: a roster is shown to everyone on the ride, so it must not
    be a way to read an email address or any other account-private column.
    """
    if not user_ids:
        return {}
    res = await db.execute(select(SocialProfile).where(SocialProfile.user_id.in_(user_ids)))
    return {
        p.user_id: {
            "username": p.username,
            "display_name": p.display_name,
            "avatar_url": p.avatar_url,
        }
        for p in res.scalars()
    }


async def _ride_view(
    db: AsyncSession, ride: GroupRide, *, viewer_id: uuid.UUID | None = None
) -> dict:
    roster = await _roster(db, ride.id)
    ids = [r.user_id for r in roster]
    identities = await _identities(db, ids)
    joined = [r.user_id for r in roster if r.status == GroupRideParticipantStatus.JOINED]
    return {
        "id": str(ride.id),
        "organizer_user_id": str(ride.organizer_user_id),
        "title": ride.title,
        "description": ride.description,
        "status": ride.status.value,
        "starts_at": ride.starts_at.isoformat() if ride.starts_at else None,
        "meeting_point": ride.meeting_point,
        "route_id": str(ride.route_id) if ride.route_id else None,
        "route_version": ride.route_version,
        "created_at": ride.created_at.isoformat(),
        "updated_at": ride.updated_at.isoformat(),
        "started_at": ride.started_at.isoformat() if ride.started_at else None,
        "completed_at": ride.completed_at.isoformat() if ride.completed_at else None,
        "cancelled_at": ride.cancelled_at.isoformat() if ride.cancelled_at else None,
        "participant_count": len(joined),
        "roster": [
            {
                "user_id": str(r.user_id),
                "role": r.role.value,
                "status": r.status.value,
                "invited_by_user_id": (
                    str(r.invited_by_user_id) if r.invited_by_user_id else None
                ),
                "message": r.message,
                "responded_at": r.responded_at.isoformat() if r.responded_at else None,
                "created_at": r.created_at.isoformat(),
                **identities.get(r.user_id, {}),
            }
            for r in roster
        ],
        # Only ever computed for a real viewer, and never cached in a payload
        # another rider receives: whether YOU may post or share location is your
        # own fact, not the ride's.
        "viewer": (
            {
                "is_organizer": ride.organizer_user_id == viewer_id,
                "is_joined": viewer_id in joined,
            }
            if viewer_id is not None
            else None
        ),
    }


# ---------------------------------------------------------------------------
# Creation
# ---------------------------------------------------------------------------


async def create_ride(
    db: AsyncSession,
    organizer: User,
    *,
    title: str,
    description: str | None,
    starts_at: datetime | None,
    meeting_point: str | None,
    route_id: uuid.UUID | None,
    route_version: int | None,
) -> dict:
    """Create a ride and the organizer's roster row, in ONE transaction.

    Both rows or neither. A ride whose organizer cannot read it would be a ride
    its own organizer gets a 404 on, which is the kind of inconsistency that gets
    discovered by a rider rather than by a test.
    """
    pin = await _resolve_pin(db, route_id, route_version)
    now = _now()
    ride = GroupRide(
        organizer_user_id=organizer.id,
        title=title,
        description=description,
        status=GroupRideStatus.OPEN,
        starts_at=starts_at,
        meeting_point=meeting_point,
        route_id=pin[0] if pin else None,
        route_version=pin[1] if pin else None,
        created_at=now,
        updated_at=now,
    )
    db.add(ride)
    await db.flush()
    db.add(
        GroupRideParticipant(
            group_ride_id=ride.id,
            user_id=organizer.id,
            role=GroupRideRole.ORGANIZER,
            status=GroupRideParticipantStatus.JOINED,
            invited_by_user_id=None,
            # responded_at IS NOT NULL for any non-`invited` status, per
            # ck_group_ride_participants_responded_at. The organizer joining is a
            # response to their own creation.
            responded_at=now,
            created_at=now,
            updated_at=now,
        )
    )
    await db.commit()
    await db.refresh(ride)
    return await _ride_view(db, ride, viewer_id=organizer.id)


# ---------------------------------------------------------------------------
# Roster
# ---------------------------------------------------------------------------


async def invite(
    db: AsyncSession,
    organizer: User,
    ride_id: uuid.UUID,
    invitee_id: uuid.UUID,
    message: str | None = None,
) -> dict:
    """Invite riders. Organizer-only (rule 1), roster frozen once started (rule 5).

    Inviting SELF is refused rather than silently ignored: it is either a
    confused client or a rider who believes they have not joined yet, and both are
    worth surfacing rather than absorbing into a no-op.
    """
    await _lock_ride(db, ride_id)
    ride = await _ride_row(db, ride_id)
    if ride is None:
        raise RideError("RIDE_NOT_FOUND", "Ride not found.", 404)
    await _require_organizer(db, ride, organizer.id)
    if ride.status != GroupRideStatus.OPEN:
        raise RideError("RIDE_ROSTER_FROZEN", "This ride no longer accepts riders.", 409)
    if invitee_id == ride.organizer_user_id:
        raise RideError("RIDE_ALREADY_MEMBER", "The organizer is already on this ride.", 400)
    await _joinable(db, ride, invitee_id)

    now = _now()
    created: list[GroupRideParticipant] = []
    for target in {invitee_id}:
        existing = await _participant_row(db, ride.id, target)
        if existing is None:
            row = GroupRideParticipant(
                group_ride_id=ride.id,
                user_id=target,
                role=GroupRideRole.PARTICIPANT,
                status=GroupRideParticipantStatus.INVITED,
                invited_by_user_id=organizer.id,
                message=message,
                responded_at=None,
                created_at=now,
                updated_at=now,
            )
            db.add(row)
            created.append(row)
        elif existing.status in (
            GroupRideParticipantStatus.DECLINED,
            GroupRideParticipantStatus.LEFT,
        ):
            # Re-invite by RESETTING the row rather than inserting a second one.
            # The table holds one row per pair for the life of the ride
            # (ADR-16 §2), so this is the only shape that preserves it.
            existing.status = GroupRideParticipantStatus.INVITED
            existing.invited_by_user_id = organizer.id
            existing.message = message
            existing.responded_at = None
            existing.updated_at = now
            created.append(existing)
        # `joined` and `invited` are already the best available state; a second
        # invitation would be noise, not an error.

    # The notification's dedupe key is captured BEFORE the commit, from the
    # timestamp this invitation stamped on the roster row. That timestamp is what
    # makes a re-invite a genuinely new event: the row id is stable for the life
    # of the ride, so keying on it alone would let the second invitation be
    # swallowed as a duplicate of the first, and the rider would be left waiting
    # for a notification that never arrives.
    notification_keys = [row.updated_at.isoformat() for row in created]
    try:
        await db.commit()
    except IntegrityError:
        # uq_group_ride_participants_pair lost the race with a concurrent invite.
        await db.rollback()
        raise RideError("RIDE_ALREADY_MEMBER", "That rider is already on this ride.", 409) from None

    for row, invitation_key in zip(created, notification_keys, strict=True):
        # Only genuinely NEW invitations notify. Re-inviting somebody who already
        # accepted must not claim they were invited again.
        if row.status == GroupRideParticipantStatus.INVITED and row.responded_at is None:
            await _notify_safely(
                notification_service.notify_group_ride_invitation,
                db,
                actor_id=organizer.id,
                group_ride_id=ride.id,
                title=ride.title,
                invitee_id=row.user_id,
                participant_id=row.id,
                invitation_key=invitation_key,
            )
    await db.refresh(ride)
    return await _ride_view(db, ride, viewer_id=organizer.id)


async def respond(
    db: AsyncSession,
    actor: User,
    ride_id: uuid.UUID,
    accept: bool,
) -> dict:
    """Accept or decline one's own invitation.

    The ONLY roster transition a non-organizer can make, and the only one that
    needs no permission check beyond holding an `invited` row — answering an
    invitation is a consent act and consent is not something a server grants or
    withholds on someone's behalf.
    """
    await _lock_ride(db, ride_id)
    ride = await _ride_row(db, ride_id)
    if ride is None:
        raise RideError("RIDE_NOT_FOUND", "Ride not found.", 404)
    row = await _participant_row(db, ride.id, actor.id)
    if row is None:
        raise RideError("RIDE_NOT_FOUND", "Ride not found.", 404)
    if row.status != GroupRideParticipantStatus.INVITED:
        # `joined` and `invited` are idempotent no-ops rather than errors: a
        # client retrying a tap that already succeeded should not see a failure,
        # and a double-accept creating a second roster row is impossible anyway.
        if row.status == GroupRideParticipantStatus.JOINED or (
            not accept and row.status == GroupRideParticipantStatus.DECLINED
        ):
            return await _ride_view(db, ride, viewer_id=actor.id)
        raise RideError("RIDE_NOT_INVITED", "You have no pending invitation.", 409)
    if ride.status != GroupRideStatus.OPEN:
        raise RideError("RIDE_ROSTER_FROZEN", "This ride no longer accepts riders.", 409)

    now = _now()
    row.status = (
        GroupRideParticipantStatus.JOINED if accept else GroupRideParticipantStatus.DECLINED
    )
    row.responded_at = now
    row.updated_at = now
    await db.commit()

    if accept:
        # The organizer is told an individual accepted; the roster is NOT fanned
        # out to. Knowing who joined is the organizer's business (they can see the
        # roster), but broadcasting it to the riders is not.
        await _notify_safely(
            notification_service.notify_group_ride_accepted,
            db,
            actor_id=actor.id,
            group_ride_id=ride.id,
            title=ride.title,
        )
    await db.refresh(ride)
    return await _ride_view(db, ride, viewer_id=actor.id)


async def leave(db: AsyncSession, actor: User, ride_id: uuid.UUID) -> dict:
    """Rule 4: withdraw, in any non-terminal state, needing no permission.

    Allowed from `started` as well as `open` — that is the consent right working
    as intended, and it is also what makes the live-location set only ever shrink.
    """
    await _lock_ride(db, ride_id)
    ride = await _ride_row(db, ride_id)
    if ride is None:
        raise RideError("RIDE_NOT_FOUND", "Ride not found.", 404)
    row = await _participant_row(db, ride.id, actor.id)
    if row is None:
        raise RideError("RIDE_NOT_FOUND", "Ride not found.", 404)
    if ride.status in TERMINAL:
        raise RideError("RIDE_CLOSED", "This ride is over.", 409)
    if row.role == GroupRideRole.ORGANIZER:
        # A one-organizer design has no way to hand the ride over, so the honest
        # answer is: cancel it, which notifies everyone.
        raise RideError("RIDE_ORGANIZER_CANNOT_LEAVE", "Cancel the ride instead.", 409)
    if row.status in (GroupRideParticipantStatus.LEFT, GroupRideParticipantStatus.REMOVED):
        return await _ride_view(db, ride, viewer_id=actor.id)

    row.status = GroupRideParticipantStatus.LEFT
    row.responded_at = _now()
    row.updated_at = row.responded_at
    await db.commit()
    await db.refresh(ride)
    return await _ride_view(db, ride, viewer_id=actor.id)


async def remove(
    db: AsyncSession, organizer: User, ride_id: uuid.UUID, target_id: uuid.UUID
) -> dict:
    """Organizer removes a rider. Available while `open` OR `started`.

    Available during `started` on purpose: a one-organizer ride with no way to
    eject someone is a ride nobody can stop, and rule 5's freeze is about ADDING
    riders, not about ejecting them.
    """
    await _lock_ride(db, ride_id)
    ride = await _ride_row(db, ride_id)
    if ride is None:
        raise RideError("RIDE_NOT_FOUND", "Ride not found.", 404)
    await _require_organizer(db, ride, organizer.id)
    if ride.status in TERMINAL:
        raise RideError("RIDE_CLOSED", "This ride is over.", 409)
    row = await _participant_row(db, ride.id, target_id)
    if row is None:
        raise RideError("RIDE_NOT_FOUND", "Ride not found.", 404)
    if row.role == GroupRideRole.ORGANIZER:
        raise RideError("RIDE_ORGANIZER_IMMUTABLE", "The organizer cannot be removed.", 409)
    if row.status == GroupRideParticipantStatus.REMOVED:
        return await _ride_view(db, ride, viewer_id=organizer.id)

    row.status = GroupRideParticipantStatus.REMOVED
    row.responded_at = _now()
    row.updated_at = row.responded_at
    await db.commit()
    await db.refresh(ride)
    return await _ride_view(db, ride, viewer_id=organizer.id)


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


async def start_ride(db: AsyncSession, organizer: User, ride_id: uuid.UUID) -> dict:
    """`open` → `started`, organizer-only.

    Refuses with nothing else to ride: a `started` ride with no participants has
    nobody to broadcast a location to, and the roster has just frozen, so it
    could never become a real one.
    """
    await _lock_ride(db, ride_id)
    ride = await _ride_row(db, ride_id)
    if ride is None:
        raise RideError("RIDE_NOT_FOUND", "Ride not found.", 404)
    await _require_organizer(db, ride, organizer.id)
    if ride.status == GroupRideStatus.STARTED:
        return await _ride_view(db, ride, viewer_id=organizer.id)
    if ride.status != GroupRideStatus.OPEN:
        raise RideError("RIDE_NOT_OPEN", "This ride cannot be started.", 409)
    if len(await _joined_ids(db, ride.id)) < 2:
        raise RideError("RIDE_NEEDS_RIDERS", "Invite at least one rider first.", 409)

    now = _now()
    ride.status = GroupRideStatus.STARTED
    ride.started_at = now
    ride.updated_at = now
    await db.commit()
    await _notify_safely(
        notification_service.notify_group_ride_started,
        db,
        actor_id=organizer.id,
        group_ride_id=ride.id,
        title=ride.title,
    )
    await db.refresh(ride)
    return await _ride_view(db, ride, viewer_id=organizer.id)


async def complete_ride(db: AsyncSession, organizer: User, ride_id: uuid.UUID) -> dict:
    """`started` → `completed`, organizer-only. No completion from `open`."""
    await _lock_ride(db, ride_id)
    ride = await _ride_row(db, ride_id)
    if ride is None:
        raise RideError("RIDE_NOT_FOUND", "Ride not found.", 404)
    await _require_organizer(db, ride, organizer.id)
    if ride.status == GroupRideStatus.COMPLETED:
        return await _ride_view(db, ride, viewer_id=organizer.id)
    if ride.status != GroupRideStatus.STARTED:
        raise RideError("RIDE_NOT_STARTED", "Start this ride before completing it.", 409)

    now = _now()
    ride.status = GroupRideStatus.COMPLETED
    ride.completed_at = now
    ride.updated_at = now
    await db.commit()
    await db.refresh(ride)
    return await _ride_view(db, ride, viewer_id=organizer.id)


async def cancel_ride(db: AsyncSession, organizer: User, ride_id: uuid.UUID) -> dict:
    """→ `cancelled`, from `open` or `started`. Idempotent."""
    await _lock_ride(db, ride_id)
    ride = await _ride_row(db, ride_id)
    if ride is None:
        raise RideError("RIDE_NOT_FOUND", "Ride not found.", 404)
    await _require_organizer(db, ride, organizer.id)
    if ride.status == GroupRideStatus.CANCELLED:
        return await _ride_view(db, ride, viewer_id=organizer.id)
    if ride.status == GroupRideStatus.COMPLETED:
        # A completed ride cannot be un-completed: it happened.
        raise RideError("RIDE_CLOSED", "This ride is already completed.", 409)

    # Deliberately NOT refusing when `started_at` is already set. Cancelling a
    # ride that has begun is a legitimate outcome — the group split up, the
    # weather turned — and blocking it would make the terminal state of a
    # started ride unreachable except by completing it, which would be a lie
    # about what happened. `ck_group_rides_cancelled_at` still requires the
    # timestamp, and `started_at` is left as the historical fact it is.
    now = _now()
    ride.status = GroupRideStatus.CANCELLED
    ride.cancelled_at = now
    ride.updated_at = now
    await db.commit()
    await db.refresh(ride)
    return await _ride_view(db, ride, viewer_id=organizer.id)


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------


async def get_ride(db: AsyncSession, viewer: User, ride_id: uuid.UUID) -> dict:
    ride = await _ride_row(db, ride_id)
    if ride is None:
        raise RideError("RIDE_NOT_FOUND", "Ride not found.", 404)
    await _require_visible(db, ride, viewer.id)
    return await _ride_view(db, ride, viewer_id=viewer.id)


async def list_my_rides(db: AsyncSession, viewer: User) -> dict:
    """Every ride the viewer has a roster row for, whatever that row's status.

    Includes `declined` and `removed` rows on purpose: a rider who declined
    still wants the ride to appear as declined, and silently dropping it is how a
    rider concludes the app lost their decision. Terminal rides are included too —
    a ride you once joined is history, not garbage.
    """
    res = await db.execute(
        select(GroupRide)
        .join(
            GroupRideParticipant,
            GroupRideParticipant.group_ride_id == GroupRide.id,
        )
        .where(GroupRideParticipant.user_id == viewer.id)
        .order_by(GroupRide.created_at.desc())
    )
    rides = list(res.scalars())
    return {"items": [await _ride_view(db, r, viewer_id=viewer.id) for r in rides]}


async def list_open_invitations(db: AsyncSession, viewer: User) -> dict:
    """Rides that have invited this rider and are still waiting for an answer.

    Reads `ix_group_ride_participants_pending_invitee`, and only ever `invited`
    rows — an invitation the rider already answered is not an invitation.
    """
    res = await db.execute(
        select(GroupRide, GroupRideParticipant)
        .join(GroupRideParticipant, GroupRideParticipant.group_ride_id == GroupRide.id)
        .where(
            GroupRideParticipant.user_id == viewer.id,
            GroupRideParticipant.status == GroupRideParticipantStatus.INVITED,
            GroupRide.status == GroupRideStatus.OPEN,
        )
        .order_by(GroupRideParticipant.created_at.desc())
    )
    rows = list(res.all())
    identities = await _identities(db, [r.organizer_user_id for r, _ in rows])
    return {
        "items": [
            {
                "participant_id": str(p.id),
                "group_ride_id": str(r.id),
                "title": r.title,
                "starts_at": r.starts_at.isoformat() if r.starts_at else None,
                "meeting_point": r.meeting_point,
                "message": p.message,
                "created_at": p.created_at.isoformat(),
                "organizer_user_id": str(r.organizer_user_id),
                **identities.get(r.organizer_user_id, {}),
            }
            for r, p in rows
        ]
    }

"""Group rides API (Phase 9, ADR-16).

Membership-derived authorization, never an oracle: every id that grants anything
comes from the JWT. Client-supplied ids may only name a *target* (a rider to
invite, a participant to remove) and that target is re-resolved server-side
against the caller's own roster row. A rider with no roster entry on a ride gets
the same 404 as a ride that does not exist, so these endpoints cannot be used to
probe which ride ids are real, nor to learn that a particular rider organized
something.

THE ORGANIZER IS NOT SPECIAL AT THE URL LEVEL. There is no `/organizer/...`
prefix. Authority is a property of the caller's roster row, checked in the
service, because a second URL namespace would invite a client to treat
"organizer endpoints" as permission and then get a 403 it did not expect. The one
place the distinction is visible is in the RESPONSE (`viewer.is_organizer`), which
is computed per request and never carried in a URL.

Rate limits are per rider and per ride. The location endpoints are limited per
RIDER, not per ride: a limit keyed on the ride would let one rider's rapid pings
lock everybody else out of their own map.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core.rate_limit import allow
from app.db.session import get_db
from app.models.user import User
from app.schemas.group_ride import (
    InviteBatch,
    InviteCreate,
    LocationPing,
    RespondRequest,
    RideCreate,
)
from app.services import chat_service, group_ride_service, ride_location_service
from app.services.chat_service import ChatError
from app.services.group_ride_service import RideError

router = APIRouter(prefix="/group-rides", tags=["group-rides"])


def _fail(exc: RideError | ChatError) -> HTTPException:
    return HTTPException(status_code=exc.status, detail={"code": exc.code, "message": exc.message})


def _limited(key: str, limit: int, window_s: int) -> None:
    if not allow(key, limit, window_s):
        raise HTTPException(status_code=429, detail="Too many requests.")


# ---------------------------------------------------------------------------
# Rides
# ---------------------------------------------------------------------------


@router.post("", status_code=201)
async def create_ride(
    body: RideCreate,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    _limited(f"ride-create:{user.id}", 20, 3600)
    try:
        return await group_ride_service.create_ride(
            db,
            user,
            title=body.title,
            description=body.description,
            starts_at=body.starts_at,
            meeting_point=body.meeting_point,
            route_id=body.route_id,
            route_version=body.route_version,
        )
    except RideError as exc:
        raise _fail(exc) from exc


@router.get("")
async def list_my_rides(
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    try:
        return await group_ride_service.list_my_rides(db, user)
    except RideError as exc:
        raise _fail(exc) from exc


@router.get("/invitations")
async def list_open_invitations(
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    """Pending invitations addressed to this rider. Separate from `/group-rides`
    because it is the ONE read a rider with no roster row on the ride is allowed
    to make — that row is the invitation itself."""
    try:
        return await group_ride_service.list_open_invitations(db, user)
    except RideError as exc:
        raise _fail(exc) from exc


@router.get("/{ride_id}")
async def get_ride(
    ride_id: uuid.UUID,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    try:
        return await group_ride_service.get_ride(db, user, ride_id)
    except RideError as exc:
        raise _fail(exc) from exc


# ---------------------------------------------------------------------------
# Roster
# ---------------------------------------------------------------------------


@router.post("/{ride_id}/invitations", status_code=201)
async def invite_user(
    ride_id: uuid.UUID,
    body: InviteCreate,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    _limited(f"ride-invite:{user.id}", 200, 3600)
    try:
        return await group_ride_service.invite(db, user, ride_id, body.user_id, body.message)
    except RideError as exc:
        raise _fail(exc) from exc


@router.post("/{ride_id}/invitations:batch", status_code=201)
async def invite_users(
    ride_id: uuid.UUID,
    body: InviteBatch,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    """Invite several riders.

    Sequential rather than concurrent on purpose: each invite takes the ride's
    advisory lock, so firing them in parallel would only queue behind that same
    lock and gain nothing. The response reports per-target outcomes rather than
    failing the whole batch — an organizer inviting ten riders should learn that
    nine were invited and one has blocked them, not get one opaque error.
    """
    _limited(f"ride-invite:{user.id}", 200, 3600)
    invited: list[str] = []
    rejected: list[dict] = []
    for target in dict.fromkeys(body.user_ids):
        try:
            await group_ride_service.invite(db, user, ride_id, target, body.message)
            invited.append(str(target))
        except RideError as exc:
            rejected.append({"user_id": str(target), "code": exc.code})
    return {"invited": invited, "rejected": rejected}


@router.post("/{ride_id}/respond")
async def respond_to_invitation(
    ride_id: uuid.UUID,
    body: RespondRequest,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    try:
        return await group_ride_service.respond(db, user, ride_id, body.accept)
    except RideError as exc:
        raise _fail(exc) from exc


@router.post("/{ride_id}/leave")
async def leave_ride(
    ride_id: uuid.UUID,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    """Withdrawal needs no permission and is never rate limited as a denial — a
    rider who wants to stop sharing who they are must not be throttled."""
    try:
        return await group_ride_service.leave(db, user, ride_id)
    except RideError as exc:
        raise _fail(exc) from exc


@router.delete("/{ride_id}/participants/{target_id}")
async def remove_participant(
    ride_id: uuid.UUID,
    target_id: uuid.UUID,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    _limited(f"ride-manage:{user.id}", 200, 3600)
    try:
        return await group_ride_service.remove(db, user, ride_id, target_id)
    except RideError as exc:
        raise _fail(exc) from exc


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


@router.post("/{ride_id}/start")
async def start_ride(
    ride_id: uuid.UUID,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    _limited(f"ride-manage:{user.id}", 200, 3600)
    try:
        return await group_ride_service.start_ride(db, user, ride_id)
    except RideError as exc:
        raise _fail(exc) from exc


@router.post("/{ride_id}/complete")
async def complete_ride(
    ride_id: uuid.UUID,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    _limited(f"ride-manage:{user.id}", 200, 3600)
    try:
        return await group_ride_service.complete_ride(db, user, ride_id)
    except RideError as exc:
        raise _fail(exc) from exc


@router.post("/{ride_id}/cancel")
async def cancel_ride(
    ride_id: uuid.UUID,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    _limited(f"ride-manage:{user.id}", 200, 3600)
    try:
        return await group_ride_service.cancel_ride(db, user, ride_id)
    except RideError as exc:
        raise _fail(exc) from exc


# ---------------------------------------------------------------------------
# Chat
# ---------------------------------------------------------------------------


@router.get("/{ride_id}/conversation")
async def ride_conversation(
    ride_id: uuid.UUID,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    """The ride's single channel, created on first open.

    Lives here rather than under `/chat/...` so the client never has to know a
    channel's id before it can display a ride's messages. Authorization is still
    re-derived from the live roster in `chat_service`, not from this route.
    """
    _limited(f"ride-channel:{user.id}", 120, 3600)
    try:
        return await chat_service.group_ride_conversation(db, user, ride_id)
    except ChatError as exc:
        raise _fail(exc) from exc


# ---------------------------------------------------------------------------
# Live location
# ---------------------------------------------------------------------------


@router.post("/{ride_id}/location", response_model=dict[str, object])
async def publish_location(
    ride_id: uuid.UUID,
    body: LocationPing,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    """Publish THIS rider's position. Per-rider limit, not per-ride.

    Sharing is off until a rider calls this. Being on a ride is not consent, and
    nothing in this phase publishes a position on a rider's behalf.
    """
    _limited(f"ride-location-publish:{user.id}", 120, 60)
    try:
        return await ride_location_service.publish(
            db,
            user,
            ride_id,
            latitude=body.latitude,
            longitude=body.longitude,
            accuracy_m=body.accuracy_m,
        )
    except RideError as exc:
        raise _fail(exc) from exc


@router.delete("/{ride_id}/location")
async def stop_sharing(
    ride_id: uuid.UUID,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    _limited(f"ride-location-stop:{user.id}", 120, 60)
    try:
        return await ride_location_service.stop_sharing(db, user, ride_id)
    except RideError as exc:
        raise _fail(exc) from exc


@router.get("/{ride_id}/location", response_model=dict[str, object])
async def list_locations(
    ride_id: uuid.UUID,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    """The riders currently sharing. Returns 503 on a Redis outage, never an
    empty list — see `ride_location_service` rule 3."""
    _limited(f"ride-location-read:{user.id}", 300, 60)
    try:
        return await ride_location_service.list_locations(db, user, ride_id)
    except RideError as exc:
        raise _fail(exc) from exc


__all__ = ["router"]

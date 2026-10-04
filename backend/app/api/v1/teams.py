"""Teams API (Phase 8.2). Membership-derived authorization, never an oracle.

Every id that matters comes from the JWT. Client-supplied ids may only name a
*target* (a member to remove, a rider to invite) and are re-resolved server-side
against the caller's own membership row. A caller with no standing in a team
gets the same 404 as a team that does not exist, so the endpoints cannot be used
to probe private teams (ADR-12 §4, ADR-13 §7).

Team membership and personal friendship are independent: nothing here reads or
writes `friend_relationships`.
"""

import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core.rate_limit import allow
from app.db.session import get_db
from app.models.team import TeamRole
from app.models.user import User
from app.schemas.team import (
    InvitationCreate,
    InvitationOut,
    InvitationPage,
    JoinRequestCreate,
    JoinRequestOut,
    JoinRequestPage,
    PublicTeamOut,
    TeamCreate,
    TeamMemberOut,
    TeamMemberPage,
    TeamOut,
    TeamPage,
    TeamUpdate,
)
from app.services import team_service
from app.services.team_service import TeamError

router = APIRouter(prefix="/teams", tags=["teams"])

MAX_LIST = 100


def _fail(exc: TeamError) -> HTTPException:
    return HTTPException(
        status_code=exc.status, detail={"code": exc.code, "message": exc.message}
    )


def _limited(key: str, limit: int, window_s: int) -> None:
    if not allow(key, limit, window_s):
        raise HTTPException(status_code=429, detail="Too many requests.")


def _manager_out(view: dict, role: TeamRole | None) -> PublicTeamOut:
    return PublicTeamOut.model_validate(view)


# ---------------------------------------------------------------------------
# Teams
# ---------------------------------------------------------------------------


@router.post("", status_code=201)
async def create_team(
    body: TeamCreate,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> TeamOut:
    _limited(f"team-create:{user.id}", 10, 3600)
    try:
        team, role = await team_service.create_team(
            db, user, body.model_dump(exclude_unset=True)
        )
        view = await team_service.team_view(db, user, team, role)
    except TeamError as exc:
        raise _fail(exc) from exc
    # TeamOut additionally carries owner_user_id, which only the creator sees.
    return TeamOut.model_validate({**view, "owner_user_id": team.owner_user_id})


@router.get("", response_model=TeamPage)
async def list_my_teams(
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=MAX_LIST),
    include_archived: bool = False,
) -> TeamPage:
    items, total = await team_service.list_my_teams(
        db, user, page, page_size, include_archived
    )
    return TeamPage(
        items=[_manager_out(v, None) for v in items],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/search", response_model=TeamPage)
async def search_teams(
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
    q: str = Query(min_length=2, max_length=64),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=MAX_LIST),
) -> TeamPage:
    _limited(f"team-search:{user.id}", 60, 60)
    items, total = await team_service.search_teams(db, user, q, page, page_size)
    return TeamPage(
        items=[_manager_out(v, None) for v in items],
        total=total,
        page=page,
        page_size=page_size,
    )


# The viewer's own outstanding asks and offers. Declared before /{team_id} so
# the literal paths are not captured as a team id.
@router.get("/my/join-requests", response_model=JoinRequestPage)
async def my_join_requests(
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=MAX_LIST),
) -> JoinRequestPage:
    try:
        items, total = await team_service.my_join_requests(db, user, page, page_size)
    except TeamError as exc:
        raise _fail(exc) from exc
    return JoinRequestPage(
        items=[JoinRequestOut.model_validate(v) for v in items],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/my/invitations", response_model=InvitationPage)
async def my_invitations(
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
    status: Literal["pending", "accepted", "declined", "revoked"] | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=MAX_LIST),
) -> InvitationPage:
    try:
        items, total = await team_service.my_invitations(
            db, user, status, page, page_size
        )
    except TeamError as exc:
        raise _fail(exc) from exc
    return InvitationPage(
        items=[InvitationOut.model_validate(v) for v in items],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.post("/invitations/{invitation_id}/accept")
async def accept_invitation(
    invitation_id: uuid.UUID,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    try:
        row = await team_service.accept_invitation(db, user, invitation_id)
    except TeamError as exc:
        raise _fail(exc) from exc
    return {"team_id": str(row.team_id), "status": "accepted"}


@router.post("/invitations/{invitation_id}/reject")
async def decline_invitation(
    invitation_id: uuid.UUID,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    try:
        await team_service.decline_invitation(db, user, invitation_id)
    except TeamError as exc:
        raise _fail(exc) from exc
    return {"status": "declined"}


@router.get("/{team_id}", response_model=PublicTeamOut)
async def get_team(
    team_id: uuid.UUID,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> PublicTeamOut:
    try:
        team, role = await team_service._visible_team(db, user, team_id)
        view = await team_service.team_view(db, user, team, role)
    except TeamError as exc:
        raise _fail(exc) from exc
    return _manager_out(view, role)


@router.patch("/{team_id}", response_model=PublicTeamOut)
async def update_team(
    team_id: uuid.UUID,
    body: TeamUpdate,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> PublicTeamOut:
    _limited(f"team-update:{user.id}", 60, 3600)
    try:
        team = await team_service.update_team(
            db, user, team_id, body.model_dump(exclude_unset=True)
        )
        role = await team_service._role_of(db, team_id, user.id)
        view = await team_service.team_view(db, user, team, role)
    except TeamError as exc:
        raise _fail(exc) from exc
    return _manager_out(view, role)


@router.delete("/{team_id}")
async def archive_team(
    team_id: uuid.UUID,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    _limited(f"team-archive:{user.id}", 10, 3600)
    try:
        await team_service.archive_team(db, user, team_id)
    except TeamError as exc:
        raise _fail(exc) from exc
    return {"status": "archived"}


# ---------------------------------------------------------------------------
# Members
# ---------------------------------------------------------------------------


@router.get("/{team_id}/members", response_model=TeamMemberPage)
async def list_members(
    team_id: uuid.UUID,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=MAX_LIST),
) -> TeamMemberPage:
    items, total = await team_service.list_members(db, user, team_id, page, page_size)
    return TeamMemberPage(
        items=[TeamMemberOut.model_validate(v) for v in items],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.delete("/{team_id}/members/{user_id}")
async def remove_member(
    team_id: uuid.UUID,
    user_id: uuid.UUID,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    _limited(f"team-member:{user.id}", 60, 3600)
    try:
        await team_service.remove_member(db, user, team_id, user_id)
    except TeamError as exc:
        raise _fail(exc) from exc
    return {"status": "removed"}


@router.patch("/{team_id}/members/{user_id}/role")
async def set_member_role(
    team_id: uuid.UUID,
    user_id: uuid.UUID,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
    role: Literal["admin", "member"] = Query(...),
) -> dict:
    _limited(f"team-member:{user.id}", 60, 3600)
    try:
        row = await team_service.set_member_role(
            db, user, team_id, user_id, TeamRole(role)
        )
    except TeamError as exc:
        raise _fail(exc) from exc
    return {"user_id": str(row.user_id), "role": row.role.value}


@router.delete("/{team_id}/membership")
async def leave_team(
    team_id: uuid.UUID,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    _limited(f"team-member:{user.id}", 60, 3600)
    try:
        await team_service.leave_team(db, user, team_id)
    except TeamError as exc:
        raise _fail(exc) from exc
    return {"status": "left"}


# ---------------------------------------------------------------------------
# Joining
# ---------------------------------------------------------------------------


@router.post("/{team_id}/join")
async def join_team(
    team_id: uuid.UUID,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    _limited(f"team-join:{user.id}", 20, 3600)
    try:
        result = await team_service.join_team(db, user, team_id)
    except TeamError as exc:
        raise _fail(exc) from exc
    return result


@router.post("/{team_id}/join-requests", status_code=201)
async def request_to_join(
    team_id: uuid.UUID,
    body: JoinRequestCreate,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    _limited(f"team-join:{user.id}", 20, 3600)
    try:
        result = await team_service.request_to_join(db, user, team_id, body.message)
    except TeamError as exc:
        raise _fail(exc) from exc
    return result


@router.get("/{team_id}/join-requests", response_model=JoinRequestPage)
async def list_join_requests(
    team_id: uuid.UUID,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=MAX_LIST),
) -> JoinRequestPage:
    try:
        items, total = await team_service.list_join_requests(
            db, user, team_id, page, page_size
        )
    except TeamError as exc:
        raise _fail(exc) from exc
    return JoinRequestPage(
        items=[JoinRequestOut.model_validate(v) for v in items],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.post("/{team_id}/join-requests/{request_id}/accept")
async def accept_join_request(
    team_id: uuid.UUID,
    request_id: uuid.UUID,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    try:
        result = await team_service.accept_join_request(db, user, team_id, request_id)
    except TeamError as exc:
        raise _fail(exc) from exc
    return result


@router.post("/{team_id}/join-requests/{request_id}/reject")
async def reject_join_request(
    team_id: uuid.UUID,
    request_id: uuid.UUID,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    try:
        await team_service.reject_join_request(db, user, team_id, request_id)
    except TeamError as exc:
        raise _fail(exc) from exc
    return {"status": "rejected"}


@router.delete("/{team_id}/join-requests/{request_id}")
async def cancel_join_request(
    team_id: uuid.UUID,
    request_id: uuid.UUID,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    try:
        await team_service.cancel_join_request(db, user, team_id, request_id)
    except TeamError as exc:
        raise _fail(exc) from exc
    return {"status": "cancelled"}


# ---------------------------------------------------------------------------
# Invitations
# ---------------------------------------------------------------------------


@router.post("/{team_id}/invitations", status_code=201, response_model=InvitationOut)
async def invite_user(
    team_id: uuid.UUID,
    body: InvitationCreate,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> InvitationOut:
    _limited(f"team-invite:{user.id}", 60, 3600)
    try:
        row = await team_service.invite_user(
            db, user, team_id, body.user_id, body.message
        )
        view = await team_service._invitation_view(db, row)
    except TeamError as exc:
        raise _fail(exc) from exc
    return InvitationOut.model_validate(view)


@router.get("/{team_id}/invitations", response_model=InvitationPage)
async def list_team_invitations(
    team_id: uuid.UUID,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=MAX_LIST),
) -> InvitationPage:
    try:
        items, total = await team_service.list_team_invitations(
            db, user, team_id, page, page_size
        )
    except TeamError as exc:
        raise _fail(exc) from exc
    return InvitationPage(
        items=[InvitationOut.model_validate(v) for v in items],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.delete("/{team_id}/invitations/{invitation_id}")
async def revoke_invitation(
    team_id: uuid.UUID,
    invitation_id: uuid.UUID,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    _limited(f"team-invite:{user.id}", 60, 3600)
    try:
        await team_service.revoke_invitation(db, user, team_id, invitation_id)
    except TeamError as exc:
        raise _fail(exc) from exc
    return {"status": "revoked"}
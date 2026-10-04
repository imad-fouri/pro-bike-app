"""Phase 8.2 team service: CRUD, discovery, membership, requests, invitations.

Three rules hold everywhere in this module.

1. AUTHORIZATION IS DERIVED, NEVER SUPPLIED. Every capability reads the viewer's
   own membership row out of the database. No function accepts an `actor_id`,
   `role`, or `is_admin` from a caller that could have taken it from a request
   body; the router passes only the authenticated `User`.

2. TEAMS AND FRIENDSHIPS ARE INDEPENDENT. Nothing here imports or touches
   `friend_relationships`. Leaving a team does not unfriend anybody, and joining
   one does not create a friendship.

3. BLOCKS ARE NON-CASCADING (ADR-13 §6). A block between two riders REFUSES new
   association actions (join, request, invite) at the moment they are attempted,
   and nothing else. It never deletes an existing membership and never deletes
   an existing friendship. Unblocking restores nothing that was removed while
   the block was in force; the rider must act again.

Concurrency follows the 8.1 pattern: one advisory transaction lock per
(team, user) pair — or per team for membership-mutating operations — so
check-then-act cannot interleave. The partial unique indexes remain the final
arbiter for inserts.
"""

import logging
import re
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy import String, and_, delete, func, literal_column, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.logging import redact
from app.models.social import UserBlock
from app.models.team import (
    Team,
    TeamInvitation,
    TeamInvitationStatus,
    TeamJoinRequest,
    TeamMembership,
    TeamRole,
    TeamStatus,
    TeamVisibility,
)
from app.models.user import User, UserStatus
from app.services import notification_service

log = logging.getLogger("cyclecoach")

_TEAM_HANDLE_RE = re.compile(r"^[a-z0-9_.]{3,30}$")


class TeamError(Exception):
    def __init__(self, code: str, message: str, status: int = 422) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


def _now() -> datetime:
    return datetime.now(UTC)


def _log(event: str, **fields: object) -> None:
    """Ids only: who did what to which team. Never names, handles, or messages."""
    log.info(f"team.{event}", extra=redact(dict(fields)))


async def _notify_safely(coro_factory, db: AsyncSession, **kwargs) -> None:
    """Run a notification hook, swallowing any failure.

    A notification is an accelerant, never a precondition: inviting a rider,
    removing a member, or archiving a team must all succeed even if the
    notification could not be written.
    """
    try:
        await coro_factory(db, **kwargs)
    except Exception as exc:  # noqa: BLE001 — never break the business action
        # `hook=` rather than `event=`: `_log`'s first positional parameter is
        # already named `event`, so passing `event=` as a field collides with it.
        _log("notification_failed", hook=coro_factory.__name__, error_category=type(exc).__name__)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def canonical_handle(raw: str) -> str:
    """Lowercase canonical form, or raise. Uniqueness is exact-match on this.

    Same shape as `social_service.canonical_username` (ADR-12 §2.1): a team
    handle is an opt-in public identifier, never derived from an email or a
    phone number.
    """
    v = raw.strip().lower()
    if not _TEAM_HANDLE_RE.match(v):
        raise TeamError(
            "TEAM_INVALID_HANDLE",
            "Handle must be 3-30 characters: lowercase letters, digits, underscore, dot.",
        )
    if not any(c.isalpha() for c in v):
        raise TeamError("TEAM_INVALID_HANDLE", "Handle must contain a letter.")
    if v.count(".") > 1:
        raise TeamError("TEAM_INVALID_HANDLE", "Handle may contain at most one dot.")
    if v[0] in "._" or v[-1] in "._":
        raise TeamError(
            "TEAM_INVALID_HANDLE", "Handle may not start or end with a dot or underscore."
        )
    if "@" in raw:
        raise TeamError("TEAM_INVALID_HANDLE", "Handle may not be an email address.")
    return v


# ---------------------------------------------------------------------------
# Lookups
# ---------------------------------------------------------------------------


async def _active_user(db: AsyncSession, user_id: uuid.UUID) -> User | None:
    # selectinload is a correctness requirement, not an optimisation: callers
    # may read user.profile (same trap as social_service._active_user).
    res = await db.execute(
        select(User)
        .where(User.id == user_id, User.status == UserStatus.ACTIVE, User.deleted_at.is_(None))
        .options(selectinload(User.profile))
    )
    return res.scalar_one_or_none()


async def _lock_team(db: AsyncSession, team_id: uuid.UUID) -> None:
    """Serialize membership mutations on one team.

    Without this, two admins acting on the same member can both read
    member_count=5 and both write 4. One advisory lock per team, held to
    transaction end, makes add/remove/leave mutually exclusive. Single lock per
    transaction: no ordering, no deadlock.
    """
    await db.execute(select(func.pg_advisory_xact_lock(func.hashtext(f"team:{team_id}"))))


async def _lock_pair(db: AsyncSession, team_id: uuid.UUID, user_id: uuid.UUID) -> None:
    """Serialize join/request/invite decisions for one (team, user) pair."""
    await db.execute(
        select(
            func.pg_advisory_xact_lock(func.hashtext(f"teampair:{team_id}:{user_id}")),
        )
    )


async def _team_row(
    db: AsyncSession, team_id: uuid.UUID, *, include_archived: bool = False
) -> Team:
    stmt = select(Team).where(Team.id == team_id)
    if not include_archived:
        stmt = stmt.where(Team.status == TeamStatus.ACTIVE)
    res = await db.execute(stmt)
    team = res.scalar_one_or_none()
    if team is None:
        raise TeamError("TEAM_NOT_FOUND", "Team not found.", 404)
    return team


async def membership_of(
    db: AsyncSession, team_id: uuid.UUID, user_id: uuid.UUID
) -> TeamMembership | None:
    res = await db.execute(
        select(TeamMembership).where(
            TeamMembership.team_id == team_id, TeamMembership.user_id == user_id
        )
    )
    return res.scalar_one_or_none()


async def _role_of(db: AsyncSession, team_id: uuid.UUID, user_id: uuid.UUID) -> TeamRole | None:
    m = await membership_of(db, team_id, user_id)
    return m.role if m is not None else None


async def _require_member(
    db: AsyncSession, team_id: uuid.UUID, user_id: uuid.UUID
) -> TeamMembership:
    m = await membership_of(db, team_id, user_id)
    if m is None:
        raise TeamError("TEAM_NOT_FOUND", "Team not found.", 404)
    return m


async def _require_manager(
    db: AsyncSession, team_id: uuid.UUID, user_id: uuid.UUID
) -> TeamMembership:
    """Owner or admin.

    Raises 404 rather than 403: a non-member must not be able to confirm that a
    private team exists by being told "forbidden" (ADR-12 §4).
    """
    m = await membership_of(db, team_id, user_id)
    if m is None or m.role not in (TeamRole.OWNER, TeamRole.ADMIN):
        raise TeamError("TEAM_NOT_FOUND", "Team not found.", 404)
    return m


async def _require_owner(
    db: AsyncSession, team_id: uuid.UUID, user_id: uuid.UUID
) -> TeamMembership:
    m = await membership_of(db, team_id, user_id)
    if m is None or m.role != TeamRole.OWNER:
        raise TeamError("TEAM_NOT_FOUND", "Team not found.", 404)
    return m


async def _blocked_either_way(db: AsyncSession, a: uuid.UUID, b: uuid.UUID) -> bool:
    """True when a block exists in EITHER direction.

    Used only to REFUSE a new association (join / request / invite). It is
    never used to delete anything — that asymmetry is the whole point of the
    non-cascading rule (ADR-13 §6).
    """
    res = await db.execute(
        select(UserBlock.id).where(
            or_(
                and_(UserBlock.blocker_user_id == a, UserBlock.blocked_user_id == b),
                and_(UserBlock.blocker_user_id == b, UserBlock.blocked_user_id == a),
            )
        )
    )
    return res.scalar_one_or_none() is not None


async def _visible_team(
    db: AsyncSession, viewer: User, team_id: uuid.UUID
) -> tuple[Team, TeamRole | None]:
    """Resolve a team the viewer is allowed to know exists.

    A member sees their team regardless of visibility (including archived, so
    a former member can still read it). A non-member sees it only when public.
    Everything else raises the same 404 as a nonexistent team.
    """
    team = await _team_row(db, team_id, include_archived=True)
    role = await _role_of(db, team_id, viewer.id)
    if role is None and (
        team.status != TeamStatus.ACTIVE or team.visibility != TeamVisibility.PUBLIC
    ):
        raise TeamError("TEAM_NOT_FOUND", "Team not found.", 404)
    return team, role


async def _counts(db: AsyncSession, team_id: uuid.UUID) -> tuple[int, int]:
    pending_reqs = (
        await db.execute(
            select(func.count())
            .select_from(TeamJoinRequest)
            .where(TeamJoinRequest.team_id == team_id)
        )
    ).scalar_one()
    pending_inv = (
        await db.execute(
            select(func.count())
            .select_from(TeamInvitation)
            .where(
                TeamInvitation.team_id == team_id,
                TeamInvitation.status == TeamInvitationStatus.PENDING,
            )
        )
    ).scalar_one()
    return pending_reqs, pending_inv


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------


def team_state(role: TeamRole | None, has_request: bool, is_invited: bool) -> str:
    """Server-authoritative viewer↔team state. Never computed client-side."""
    if role == TeamRole.OWNER:
        return "OWNER"
    if role == TeamRole.ADMIN:
        return "ADMIN"
    if role == TeamRole.MEMBER:
        return "MEMBER"
    if has_request:
        return "JOIN_REQUEST_PENDING"
    if is_invited:
        return "INVITED"
    return "NOT_AFFILIATED"


async def _viewer_flags(
    db: AsyncSession, team_id: uuid.UUID, viewer_id: uuid.UUID
) -> tuple[bool, bool]:
    has_request = (
        await db.execute(
            select(TeamJoinRequest.id).where(
                TeamJoinRequest.team_id == team_id, TeamJoinRequest.user_id == viewer_id
            )
        )
    ).scalar_one_or_none()
    is_invited = (
        await db.execute(
            select(TeamInvitation.id).where(
                TeamInvitation.team_id == team_id,
                TeamInvitation.invited_user_id == viewer_id,
                TeamInvitation.status == TeamInvitationStatus.PENDING,
            )
        )
    ).scalar_one_or_none()
    return has_request is not None, is_invited is not None


async def team_view(db: AsyncSession, viewer: User, team: Team, role: TeamRole | None) -> dict:
    """Managers get counters (they are the only ones who may act on them)."""
    has_request, is_invited = await _viewer_flags(db, team.id, viewer.id)
    base = {
        "id": team.id,
        "name": team.name,
        "handle": team.handle,
        "description": team.description,
        "avatar_url": team.avatar_url,
        "category": team.category,
        "visibility": team.visibility,
        "status": team.status,
        "member_count": team.member_count,
        "created_at": team.created_at,
        "updated_at": team.updated_at,
        "my_role": role,
        "state": team_state(role, has_request, is_invited),
    }
    if role in (TeamRole.OWNER, TeamRole.ADMIN):
        reqs, invs = await _counts(db, team.id)
        base["pending_requests_count"] = reqs
        base["pending_invitations_count"] = invs
    return base


async def _identity(db: AsyncSession, user_id: uuid.UUID) -> dict:
    """Public social projection for a member row.

    Deliberately reads `social_profiles` — the same public identity social
    already exposes — rather than `users`. That guarantees a team member list
    cannot leak an email address or any other account-private column.
    """
    from app.models.social import SocialProfile

    res = await db.execute(select(SocialProfile).where(SocialProfile.user_id == user_id))
    prof = res.scalar_one_or_none()
    return {
        "username": prof.username if prof else None,
        "display_name": (prof.display_name if prof else None),
        "avatar_url": prof.avatar_url if prof else None,
    }


# ---------------------------------------------------------------------------
# Team CRUD
# ---------------------------------------------------------------------------


async def create_team(db: AsyncSession, owner: User, data: dict) -> tuple[Team, TeamRole]:
    handle = None
    if data.get("handle") is not None:
        handle = canonical_handle(data["handle"])
    if handle is not None:
        clash = await db.execute(select(Team.id).where(Team.handle == handle))
        if clash.scalar_one_or_none() is not None:
            raise TeamError("TEAM_HANDLE_TAKEN", "That handle is taken.", 409)
    name = (data.get("name") or "").strip()
    if not name:
        raise TeamError("TEAM_INVALID_NAME", "Team name cannot be blank.", 422)
    now = _now()
    team = Team(
        owner_user_id=owner.id,
        name=name[:80],
        handle=handle,
        description=data.get("description"),
        avatar_url=_validate_avatar(data.get("avatar_url")),
        category=data.get("category"),
        visibility=TeamVisibility(data.get("visibility") or TeamVisibility.PUBLIC),
        status=TeamStatus.ACTIVE,
        member_count=1,
        created_at=now,
        updated_at=now,
    )
    db.add(team)
    await db.flush()
    # The OWNER membership and the team row are written in ONE transaction, so a
    # team can never exist without its owner, and the single-owner partial index
    # is satisfied immediately.
    db.add(
        TeamMembership(
            team_id=team.id,
            user_id=owner.id,
            role=TeamRole.OWNER,
            created_at=now,
            updated_at=now,
        )
    )
    await db.commit()
    await db.refresh(team)
    _log("created", team_id=str(team.id), owner_id=str(owner.id))
    return team, TeamRole.OWNER


def _validate_avatar(url: str | None) -> str | None:
    if url is None:
        return None
    if not url.startswith(("https://", "http://")):
        raise TeamError("TEAM_INVALID_AVATAR", "Avatar must be an http(s) URL.", 422)
    return url


async def update_team(db: AsyncSession, viewer: User, team_id: uuid.UUID, data: dict) -> Team:
    await _lock_team(db, team_id)
    team = await _team_row(db, team_id)
    role = await _role_of(db, team_id, viewer.id)
    if role is None:
        # A NON-MEMBER gets 404, not 403. A 403 would confirm the team exists to
        # somebody with no standing in it, turning PATCH into a probe for private
        # teams (ADR-12 §4, ADR-13 §7).
        raise TeamError("TEAM_NOT_FOUND", "Team not found.", 404)
    # OWNER edits identity and privacy. ADMIN may edit description and avatar
    # (the "limited team settings" of ADR-13 §4) but never name, handle, or
    # visibility — those change who the team is. A 403 here is correct: an admin
    # already knows the team exists, so nothing is leaked.
    is_owner = role == TeamRole.OWNER
    if not is_owner:
        forbidden = {"name", "handle", "visibility", "status"}
        if any(k in data for k in forbidden):
            raise TeamError("TEAM_FORBIDDEN", "Only the owner can change this.", 403)
    if "handle" in data:
        if data["handle"] is None:
            team.handle = None
        else:
            handle = canonical_handle(data["handle"])
            clash = await db.execute(
                select(Team.id).where(Team.handle == handle, Team.id != team.id)
            )
            if clash.scalar_one_or_none() is not None:
                raise TeamError("TEAM_HANDLE_TAKEN", "That handle is taken.", 409)
            team.handle = handle
    if "name" in data and data["name"] is not None:
        name = data["name"].strip()
        if not name:
            raise TeamError("TEAM_INVALID_NAME", "Team name cannot be blank.", 422)
        team.name = name[:80]
    if "description" in data:
        team.description = data["description"]
    if "avatar_url" in data:
        team.avatar_url = _validate_avatar(data["avatar_url"])
    if "category" in data:
        team.category = data["category"]
    if is_owner and "visibility" in data and data["visibility"] is not None:
        team.visibility = TeamVisibility(data["visibility"])
    team.updated_at = _now()
    await db.commit()
    await db.refresh(team)
    _log("updated", team_id=str(team.id), by=str(viewer.id))
    return team


async def archive_team(db: AsyncSession, viewer: User, team_id: uuid.UUID) -> None:
    await _lock_team(db, team_id)
    team = await _team_row(db, team_id)
    await _require_owner(db, team_id, viewer.id)
    now = _now()
    team.status = TeamStatus.ARCHIVED
    team.archived_at = now
    team.updated_at = now
    # Pending asks and offers die with the team: an archived team must not keep
    # collecting requests or holding invitations.
    await db.execute(delete(TeamJoinRequest).where(TeamJoinRequest.team_id == team_id))
    await db.execute(
        delete(TeamInvitation).where(
            TeamInvitation.team_id == team_id,
            TeamInvitation.status == TeamInvitationStatus.PENDING,
        )
    )
    await db.commit()
    _log("archived", team_id=str(team_id), by=str(viewer.id))
    # After the commit, and the members are read live AFTER the archive so the
    # fan-out reflects who is still on the team at the moment it was archived.
    await _notify_safely(
        notification_service.notify_team_archived,
        db,
        actor_id=viewer.id,
        team_id=team_id,
        team_name=team.name,
    )


async def list_my_teams(
    db: AsyncSession, viewer: User, page: int, page_size: int, include_archived: bool
) -> tuple[list[dict], int]:
    base = (
        select(Team)
        .join(TeamMembership, TeamMembership.team_id == Team.id)
        .where(TeamMembership.user_id == viewer.id)
    )
    if not include_archived:
        base = base.where(Team.status == TeamStatus.ACTIVE)
    base = base.order_by(Team.created_at.desc(), Team.id)
    total = (await db.execute(select(func.count()).select_from(base.subquery()))).scalar_one()
    rows = (await db.execute(base.offset((page - 1) * page_size).limit(page_size))).scalars().all()
    if not rows:
        return [], total
    roles = await _roles_for(db, viewer.id, [t.id for t in rows])
    items = []
    for team in rows:
        role = roles.get(team.id)
        items.append(await team_view(db, viewer, team, role))
    return items, total


async def _roles_for(
    db: AsyncSession, viewer_id: uuid.UUID, team_ids: list[uuid.UUID]
) -> dict[uuid.UUID, TeamRole]:
    if not team_ids:
        return {}
    res = await db.execute(
        select(TeamMembership.team_id, TeamMembership.role).where(
            TeamMembership.user_id == viewer_id, TeamMembership.team_id.in_(team_ids)
        )
    )
    return {row[0]: row[1] for row in res.all()}


async def search_teams(
    db: AsyncSession, viewer: User, q: str, page: int, page_size: int
) -> tuple[list[dict], int]:
    """Discovery over name/handle only.

    Private teams the viewer does not belong to are EXCLUDED, not redacted: a
    blanked row would still confirm the team exists and still leak its name.
    Archived teams never appear. Never matches on member names, and therefore
    can never be used to enumerate a team's roster.
    """
    pattern = f"%{_like_escape(q.strip())}%"
    my_team_ids = select(TeamMembership.team_id).where(TeamMembership.user_id == viewer.id)
    base = (
        select(Team)
        .where(
            Team.status == TeamStatus.ACTIVE,
            or_(
                Team.visibility == TeamVisibility.PUBLIC,
                Team.id.in_(my_team_ids),
            ),
            or_(Team.name.ilike(pattern), Team.handle.ilike(pattern)),
        )
        .order_by(Team.member_count.desc(), Team.name, Team.id)
    )
    total = (await db.execute(select(func.count()).select_from(base.subquery()))).scalar_one()
    rows = (await db.execute(base.offset((page - 1) * page_size).limit(page_size))).scalars().all()
    if not rows:
        return [], total
    roles = await _roles_for(db, viewer.id, [t.id for t in rows])
    items = []
    for team in rows:
        items.append(await team_view(db, viewer, team, roles.get(team.id)))
    return items, total


def _like_escape(q: str) -> str:
    return q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


# ---------------------------------------------------------------------------
# Members
# ---------------------------------------------------------------------------


async def list_members(
    db: AsyncSession, viewer: User, team_id: uuid.UUID, page: int, page_size: int
) -> tuple[list[dict], int]:
    # Visible teams only; a private team the viewer is not in raises 404 above.
    team, _role = await _visible_team(db, viewer, team_id)
    base = (
        select(TeamMembership)
        .where(TeamMembership.team_id == team.id)
        .order_by(
            # Owner first, then admins, then members; stable within a role.
            # array_position maps the enum label to the declared authority
            # order, so no extra column is needed for sorting.
            func.array_position(
                literal_column("ARRAY['owner','admin','member']"),
                TeamMembership.role.cast(String),
            ),
            TeamMembership.created_at,
            TeamMembership.id,
        )
    )
    total = (await db.execute(select(func.count()).select_from(base.subquery()))).scalar_one()
    rows = (await db.execute(base.offset((page - 1) * page_size).limit(page_size))).scalars().all()
    if not rows:
        return [], total
    identities = await _identities_for(db, [m.user_id for m in rows])
    items = [
        {
            "user_id": m.user_id,
            "role": m.role,
            "joined_at": m.created_at,
            **identities.get(
                m.user_id, {"username": None, "display_name": None, "avatar_url": None}
            ),
        }
        for m in rows
    ]
    return items, total


async def _identities_for(db: AsyncSession, user_ids: list[uuid.UUID]) -> dict[uuid.UUID, dict]:
    from app.models.social import SocialProfile

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


async def set_member_role(
    db: AsyncSession, viewer: User, team_id: uuid.UUID, target_id: uuid.UUID, role: TeamRole
) -> TeamMembership:
    """Owner-only. Cannot demote the owner, and cannot promote to owner."""
    await _lock_team(db, team_id)
    await _team_row(db, team_id)
    await _require_owner(db, team_id, viewer.id)
    target = await _require_member(db, team_id, target_id)
    if target.role == TeamRole.OWNER:
        # Owner transfer is explicitly out of scope (Phase 8.3), so the owner
        # row is immutable here rather than accidentally reassignable.
        raise TeamError("TEAM_OWNER_IMMUTABLE", "The owner role cannot be changed.", 409)
    if role == TeamRole.OWNER:
        raise TeamError("TEAM_OWNER_IMMUTABLE", "Ownership cannot be transferred here.", 409)
    if target.role == role:
        raise TeamError("TEAM_ROLE_UNCHANGED", "That member already has this role.", 409)
    target.role = role
    target.updated_at = _now()
    await db.commit()
    await db.refresh(target)
    _log("role_changed", team_id=str(team_id), target_id=str(target_id), role=role.value)
    return target


async def remove_member(
    db: AsyncSession, viewer: User, team_id: uuid.UUID, target_id: uuid.UUID
) -> None:
    """Admin or owner removes a member. The owner row cannot be removed."""
    await _lock_team(db, team_id)
    team = await _team_row(db, team_id)
    await _require_manager(db, team_id, viewer.id)
    target = await _require_member(db, team_id, target_id)
    if target.role == TeamRole.OWNER:
        raise TeamError("TEAM_OWNER_IMMUTABLE", "The owner cannot be removed.", 409)
    team_name = team.name
    await db.execute(delete(TeamMembership).where(TeamMembership.id == target.id))
    # member_count is maintained here, under the team lock, so concurrent
    # removals cannot both read the same value and write the same decrement.
    team.member_count = max(0, team.member_count - 1)
    team.updated_at = _now()
    await db.commit()
    _log("member_removed", team_id=str(team_id), target_id=str(target_id), by=str(viewer.id))
    # AFTER the membership row is deleted. This is the one notification that must
    # reach someone who no longer has standing in the team, so it names the team
    # and nothing else — the deep link lands on a profile that will 404 for them,
    # which is the correct outcome. The notification informs; it does not grant.
    await _notify_safely(
        notification_service.notify_team_member_removed,
        db,
        actor_id=viewer.id,
        team_id=team_id,
        team_name=team_name,
        member_id=target_id,
    )


async def leave_team(db: AsyncSession, viewer: User, team_id: uuid.UUID) -> None:
    """A member (including an admin) may leave. The owner may not.

    Leaving removes ONLY the membership row. It touches no friendship row: a
    team and a personal friendship are independent entities (ADR-13 §5).
    """
    await _lock_team(db, team_id)
    team = await _team_row(db, team_id)
    mine = await _require_member(db, team_id, viewer.id)
    if mine.role == TeamRole.OWNER:
        raise TeamError(
            "TEAM_OWNER_CANNOT_LEAVE",
            "The owner cannot leave. Archive the team instead.",
            409,
        )
    await db.execute(delete(TeamMembership).where(TeamMembership.id == mine.id))
    team.member_count = max(0, team.member_count - 1)
    team.updated_at = _now()
    await db.commit()
    _log("member_left", team_id=str(team_id), user_id=str(viewer.id))


# ---------------------------------------------------------------------------
# Joining
# ---------------------------------------------------------------------------


async def _add_member(
    db: AsyncSession, team: Team, user_id: uuid.UUID, role: TeamRole
) -> TeamMembership:
    now = _now()
    row = TeamMembership(
        team_id=team.id, user_id=user_id, role=role, created_at=now, updated_at=now
    )
    db.add(row)
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        raise TeamError("TEAM_ALREADY_MEMBER", "Already a member of this team.", 409) from None
    team.member_count += 1
    team.updated_at = now
    return row


async def join_team(db: AsyncSession, viewer: User, team_id: uuid.UUID) -> dict:
    """Public team → immediate membership. Private team → join request.

    This asymmetry is the whole visibility model: a public team is
    self-service, a private team always requires a human decision.
    """
    await _lock_pair(db, team_id, viewer.id)
    team = await _team_row(db, team_id)
    if await membership_of(db, team_id, viewer.id) is not None:
        raise TeamError("TEAM_ALREADY_MEMBER", "Already a member of this team.", 409)
    if team.owner_user_id == viewer.id:
        raise TeamError("TEAM_ALREADY_MEMBER", "Already a member of this team.", 409)
    # Non-cascading block: refuse the NEW association only.
    if await _blocked_either_way(db, viewer.id, team.owner_user_id):
        raise TeamError("TEAM_NOT_FOUND", "Team not found.", 404)
    if team.visibility == TeamVisibility.PUBLIC:
        await _lock_team(db, team_id)
        await db.refresh(team)
        await _add_member(db, team, viewer.id, TeamRole.MEMBER)
        await db.commit()
        _log("joined", team_id=str(team_id), user_id=str(viewer.id))
        return {"status": "joined", "team_id": str(team.id)}
    return await _create_join_request(db, team, viewer, None)


async def request_to_join(
    db: AsyncSession, viewer: User, team_id: uuid.UUID, message: str | None
) -> dict:
    """Explicit request. On a public team this is accepted immediately, so the
    client never has to branch on visibility to keep the UI honest."""
    await _lock_pair(db, team_id, viewer.id)
    team = await _team_row(db, team_id)
    if await membership_of(db, team_id, viewer.id) is not None:
        raise TeamError("TEAM_ALREADY_MEMBER", "Already a member of this team.", 409)
    if await _blocked_either_way(db, viewer.id, team.owner_user_id):
        raise TeamError("TEAM_NOT_FOUND", "Team not found.", 404)
    if team.visibility == TeamVisibility.PUBLIC:
        await _lock_team(db, team_id)
        await db.refresh(team)
        await _add_member(db, team, viewer.id, TeamRole.MEMBER)
        await db.commit()
        return {"status": "joined", "team_id": str(team.id)}
    return await _create_join_request(db, team, viewer, message)


async def _create_join_request(
    db: AsyncSession, team: Team, viewer: User, message: str | None
) -> dict:
    row = TeamJoinRequest(
        team_id=team.id,
        user_id=viewer.id,
        message=message,
        created_at=_now(),
        updated_at=_now(),
    )
    db.add(row)
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        raise TeamError("TEAM_REQUEST_PENDING", "A join request is already pending.", 409) from None
    await db.commit()
    _log("join_requested", team_id=str(team.id), user_id=str(viewer.id))
    # Only the private-team path creates a request, so only managers are told.
    # The public path joined immediately and needs no notification.
    await _notify_safely(
        notification_service.notify_team_join_request,
        db,
        actor_id=viewer.id,
        team_id=team.id,
        team_name=team.name,
        request_id=row.id,
    )
    return {"status": "requested", "request_id": str(row.id)}


async def list_join_requests(
    db: AsyncSession, viewer: User, team_id: uuid.UUID, page: int, page_size: int
) -> tuple[list[dict], int]:
    await _team_row(db, team_id)
    await _require_manager(db, team_id, viewer.id)
    base = (
        select(TeamJoinRequest)
        .where(TeamJoinRequest.team_id == team_id)
        .order_by(TeamJoinRequest.created_at, TeamJoinRequest.id)
    )
    total = (await db.execute(select(func.count()).select_from(base.subquery()))).scalar_one()
    rows = (await db.execute(base.offset((page - 1) * page_size).limit(page_size))).scalars().all()
    if not rows:
        return [], total
    identities = await _identities_for(db, [r.user_id for r in rows])
    items = [
        {
            "id": r.id,
            "team_id": r.team_id,
            "user_id": r.user_id,
            "message": r.message,
            "created_at": r.created_at,
            **identities.get(
                r.user_id, {"username": None, "display_name": None, "avatar_url": None}
            ),
        }
        for r in rows
    ]
    return items, total


async def my_join_requests(
    db: AsyncSession, viewer: User, page: int, page_size: int
) -> tuple[list[dict], int]:
    """The viewer's own outstanding asks, across every team."""
    base = (
        select(TeamJoinRequest)
        .where(TeamJoinRequest.user_id == viewer.id)
        .order_by(TeamJoinRequest.created_at.desc(), TeamJoinRequest.id)
    )
    total = (await db.execute(select(func.count()).select_from(base.subquery()))).scalar_one()
    rows = (await db.execute(base.offset((page - 1) * page_size).limit(page_size))).scalars().all()
    if not rows:
        return [], total
    teams = await _teams_for(db, [r.team_id for r in rows])
    items = []
    for r in rows:
        t = teams.get(r.team_id)
        items.append(
            {
                "id": r.id,
                "team_id": r.team_id,
                "user_id": r.user_id,
                "message": r.message,
                "created_at": r.created_at,
                "username": None,
                "display_name": t.name if t else None,
            }
        )
    return items, total


async def _teams_for(db: AsyncSession, team_ids: list[uuid.UUID]) -> dict[uuid.UUID, Team]:
    if not team_ids:
        return {}
    res = await db.execute(select(Team).where(Team.id.in_(team_ids)))
    return {t.id: t for t in res.scalars()}


async def _load_pending_request(
    db: AsyncSession, team_id: uuid.UUID, request_id: uuid.UUID
) -> TeamJoinRequest:
    res = await db.execute(
        select(TeamJoinRequest).where(
            TeamJoinRequest.id == request_id, TeamJoinRequest.team_id == team_id
        )
    )
    row = res.scalar_one_or_none()
    if row is None:
        raise TeamError("TEAM_REQUEST_NOT_FOUND", "Join request not found.", 404)
    return row


async def accept_join_request(
    db: AsyncSession, viewer: User, team_id: uuid.UUID, request_id: uuid.UUID
) -> dict:
    await _lock_pair(db, team_id, request_id)
    await _team_row(db, team_id)
    await _require_manager(db, team_id, viewer.id)
    row = await _load_pending_request(db, team_id, request_id)
    await _lock_team(db, team_id)
    team = await _team_row(db, team_id)
    # Re-check under the team lock: the applicant may have joined or left.
    if await membership_of(db, team_id, row.user_id) is not None:
        await db.execute(delete(TeamJoinRequest).where(TeamJoinRequest.id == row.id))
        await db.commit()
        raise TeamError("TEAM_ALREADY_MEMBER", "Already a member of this team.", 409)
    if await _blocked_either_way(db, row.user_id, team.owner_user_id):
        # A block landed while the request sat in the queue. The request is
        # dead; it is NOT a membership deletion, because none existed yet.
        await db.execute(delete(TeamJoinRequest).where(TeamJoinRequest.id == row.id))
        await db.commit()
        raise TeamError("TEAM_REQUEST_NOT_FOUND", "Join request not found.", 404)
    await _add_member(db, team, row.user_id, TeamRole.MEMBER)
    await db.execute(delete(TeamJoinRequest).where(TeamJoinRequest.id == row.id))
    await db.commit()
    _log("join_request_accepted", team_id=str(team_id), user_id=str(row.user_id))
    return {"status": "accepted", "user_id": str(row.user_id)}


async def reject_join_request(
    db: AsyncSession, viewer: User, team_id: uuid.UUID, request_id: uuid.UUID
) -> None:
    await _lock_pair(db, team_id, request_id)
    await _team_row(db, team_id)
    await _require_manager(db, team_id, viewer.id)
    row = await _load_pending_request(db, team_id, request_id)
    await db.execute(delete(TeamJoinRequest).where(TeamJoinRequest.id == row.id))
    await db.commit()
    _log("join_request_rejected", team_id=str(team_id), request_id=str(request_id))


# ---------------------------------------------------------------------------
# Invitations
# ---------------------------------------------------------------------------


async def invite_user(
    db: AsyncSession, viewer: User, team_id: uuid.UUID, target_id: uuid.UUID, message: str | None
) -> TeamInvitation:
    await _lock_pair(db, team_id, target_id)
    team = await _team_row(db, team_id)
    team_name = team.name
    await _require_manager(db, team_id, viewer.id)
    if target_id == viewer.id:
        raise TeamError("TEAM_CANNOT_TARGET_SELF", "You cannot invite yourself.", 422)
    target = await _active_user(db, target_id)
    if target is None:
        raise TeamError("TEAM_USER_NOT_FOUND", "That rider is not available.", 404)
    if await membership_of(db, team_id, target_id) is not None:
        raise TeamError("TEAM_ALREADY_MEMBER", "Already a member of this team.", 409)
    # Non-cascading block: refuse the NEW offer only.
    if await _blocked_either_way(db, target_id, viewer.id):
        raise TeamError("TEAM_USER_NOT_FOUND", "That rider is not available.", 404)
    existing = await db.execute(
        select(TeamInvitation).where(
            TeamInvitation.team_id == team_id,
            TeamInvitation.invited_user_id == target_id,
            TeamInvitation.status == TeamInvitationStatus.PENDING,
        )
    )
    prior = existing.scalar_one_or_none()
    if prior is not None:
        raise TeamError("TEAM_INVITE_PENDING", "An invitation is already pending.", 409)
    # An outstanding ask plus an offer is redundant: the rider already has a
    # way in. Collapse it so the applicant is not shown two competing paths.
    await db.execute(
        delete(TeamJoinRequest).where(
            TeamJoinRequest.team_id == team_id, TeamJoinRequest.user_id == target_id
        )
    )
    now = _now()
    row = TeamInvitation(
        team_id=team_id,
        invited_user_id=target_id,
        invited_by_user_id=viewer.id,
        status=TeamInvitationStatus.PENDING,
        message=message,
        created_at=now,
        updated_at=now,
    )
    db.add(row)
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        raise TeamError("TEAM_INVITE_PENDING", "An invitation is already pending.", 409) from None
    await db.commit()
    await db.refresh(row)
    _log("invited", team_id=str(team_id), target_id=str(target_id), by=str(viewer.id))
    # The invited rider is the only recipient — never the whole team. The block
    # check above already refused this offer if a block stands, so a blocked pair
    # can never reach this point.
    await _notify_safely(
        notification_service.notify_team_invitation,
        db,
        actor_id=viewer.id,
        team_id=team_id,
        team_name=team_name,
        invitee_id=target_id,
        invitation_id=row.id,
    )
    return row


async def list_team_invitations(
    db: AsyncSession, viewer: User, team_id: uuid.UUID, page: int, page_size: int
) -> tuple[list[dict], int]:
    """Manager view: every invitation this team has sent."""
    await _team_row(db, team_id)
    await _require_manager(db, team_id, viewer.id)
    return await _invitation_page(
        db,
        select(TeamInvitation)
        .where(TeamInvitation.team_id == team_id)
        .order_by(TeamInvitation.created_at.desc(), TeamInvitation.id),
        page,
        page_size,
    )


async def my_invitations(
    db: AsyncSession, viewer: User, status: str | None, page: int, page_size: int
) -> tuple[list[dict], int]:
    """The viewer's inbox. Only invitations addressed to them."""
    stmt = select(TeamInvitation).where(TeamInvitation.invited_user_id == viewer.id)
    if status:
        try:
            stmt = stmt.where(TeamInvitation.status == TeamInvitationStatus(status))
        except ValueError:
            raise TeamError("TEAM_INVALID_STATUS", "Unknown invitation status.", 422) from None
    stmt = stmt.order_by(TeamInvitation.created_at.desc(), TeamInvitation.id)
    return await _invitation_page(db, stmt, page, page_size)


async def _invitation_view(db: AsyncSession, inv: TeamInvitation) -> dict:
    """Serialize one invitation, decorating team and inviter names."""
    team = (await db.execute(select(Team).where(Team.id == inv.team_id))).scalar_one_or_none()
    inviter = await _identity(db, inv.invited_by_user_id)
    return {
        "id": inv.id,
        "team_id": inv.team_id,
        "team_name": team.name if team else None,
        "team_handle": team.handle if team else None,
        "invited_user_id": inv.invited_user_id,
        "invited_by_user_id": inv.invited_by_user_id,
        "invited_by_username": inviter.get("username"),
        "status": inv.status,
        "message": inv.message,
        "created_at": inv.created_at,
        "responded_at": inv.responded_at,
    }


async def _invitation_page(
    db: AsyncSession, stmt, page: int, page_size: int
) -> tuple[list[dict], int]:
    total = (await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
    rows: Sequence[TeamInvitation] = (
        (await db.execute(stmt.offset((page - 1) * page_size).limit(page_size))).scalars().all()
    )
    if not rows:
        return [], total
    items = [await _invitation_view(db, r) for r in rows]
    return items, total


async def _load_invitation(db: AsyncSession, invitation_id: uuid.UUID) -> TeamInvitation:
    res = await db.execute(select(TeamInvitation).where(TeamInvitation.id == invitation_id))
    row = res.scalar_one_or_none()
    if row is None:
        raise TeamError("TEAM_INVITATION_NOT_FOUND", "Invitation not found.", 404)
    return row


async def accept_invitation(
    db: AsyncSession, viewer: User, invitation_id: uuid.UUID
) -> TeamMembership:
    inv = await _load_invitation(db, invitation_id)
    # A third party must never be able to redeem somebody else's invitation.
    if inv.invited_user_id != viewer.id:
        raise TeamError("TEAM_INVITATION_NOT_FOUND", "Invitation not found.", 404)
    if inv.status != TeamInvitationStatus.PENDING:
        raise TeamError("TEAM_INVITATION_NOT_FOUND", "Invitation not found.", 404)
    await _lock_pair(db, inv.team_id, viewer.id)
    await _lock_team(db, inv.team_id)
    team = await _team_row(db, inv.team_id)
    if await membership_of(db, inv.team_id, viewer.id) is not None:
        inv.status = TeamInvitationStatus.ACCEPTED
        inv.responded_at = _now()
        await db.commit()
        raise TeamError("TEAM_ALREADY_MEMBER", "Already a member of this team.", 409)
    row = await _add_member(db, team, viewer.id, TeamRole.MEMBER)
    inv.status = TeamInvitationStatus.ACCEPTED
    inv.responded_at = _now()
    inv.updated_at = inv.responded_at
    await db.commit()
    await db.refresh(row)
    _log("invitation_accepted", team_id=str(inv.team_id), user_id=str(viewer.id))
    return row


async def decline_invitation(db: AsyncSession, viewer: User, invitation_id: uuid.UUID) -> None:
    inv = await _load_invitation(db, invitation_id)
    if inv.invited_user_id != viewer.id:
        raise TeamError("TEAM_INVITATION_NOT_FOUND", "Invitation not found.", 404)
    if inv.status != TeamInvitationStatus.PENDING:
        raise TeamError("TEAM_INVITATION_NOT_FOUND", "Invitation not found.", 404)
    inv.status = TeamInvitationStatus.DECLINED
    now = _now()
    inv.responded_at = now
    inv.updated_at = now
    await db.commit()
    _log("invitation_declined", team_id=str(inv.team_id), user_id=str(viewer.id))


async def revoke_invitation(
    db: AsyncSession, viewer: User, team_id: uuid.UUID, invitation_id: uuid.UUID
) -> None:
    """A manager withdraws an offer the rider has not yet answered."""
    await _lock_pair(db, team_id, invitation_id)
    await _team_row(db, team_id)
    await _require_manager(db, team_id, viewer.id)
    inv = await _load_invitation(db, invitation_id)
    if inv.team_id != team_id or inv.status != TeamInvitationStatus.PENDING:
        raise TeamError("TEAM_INVITATION_NOT_FOUND", "Invitation not found.", 404)
    inv.status = TeamInvitationStatus.REVOKED
    now = _now()
    inv.responded_at = now
    inv.updated_at = now
    await db.commit()
    _log("invitation_revoked", team_id=str(team_id), invitation_id=str(invitation_id))


async def cancel_join_request(
    db: AsyncSession, viewer: User, team_id: uuid.UUID, request_id: uuid.UUID
) -> None:
    """The applicant withdraws their own ask."""
    await _lock_pair(db, team_id, request_id)
    row = await _load_pending_request(db, team_id, request_id)
    if row.user_id != viewer.id:
        raise TeamError("TEAM_REQUEST_NOT_FOUND", "Join request not found.", 404)
    await db.execute(delete(TeamJoinRequest).where(TeamJoinRequest.id == row.id))
    await db.commit()
    _log("join_request_cancelled", team_id=str(team_id), request_id=str(request_id))

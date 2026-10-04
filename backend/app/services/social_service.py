"""Phase 8.1 social service: profiles, requests, friendships, blocks (ADR-12).

Ownership rule, repeated everywhere below: every id that matters comes from
the JWT (`viewer`), never from the client. Client-supplied ids name only the
*target* of an action, and every one of those is re-resolved server-side.

Concurrency rule: the canonical pair unique constraint is the arbiter. Two
writers racing on the same pair produce exactly one row; the loser re-reads
and gets a deterministic answer (409, or the now-current state). Row-level
locks (`FOR UPDATE`) serialize accept against concurrent mutation.
"""

import logging
import re
import uuid
from datetime import UTC, datetime

from sqlalchemy import and_, delete, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.logging import redact
from app.models.social import (
    FriendRelationship,
    FriendRequestsPolicy,
    RelationshipStatus,
    SearchVisibility,
    SocialProfile,
    UserBlock,
)
from app.models.user import User, UserStatus
from app.services import notification_service

log = logging.getLogger("cyclecoach")

_USERNAME_RE = re.compile(r"^[a-z0-9_.]{3,30}$")
_COUNTRY_RE = re.compile(r"^[A-Za-z]{2}$")


class SocialError(Exception):
    def __init__(self, code: str, message: str, status: int = 422) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


def _now() -> datetime:
    return datetime.now(UTC)


def _log(event: str, **fields: object) -> None:
    """Ids only: who acted on whom. Never tokens, emails, bios, or content."""
    log.info(f"social.{event}", extra=redact(dict(fields)))


def canonical_username(raw: str) -> str:
    """Lowercase canonical form, or raise. Uniqueness is exact-match on this
    value, so case variants collide by construction."""
    v = raw.strip().lower()
    if not _USERNAME_RE.match(v):
        raise SocialError(
            "SOCIAL_INVALID_USERNAME",
            "Username must be 3-30 characters: lowercase letters, digits, underscore, dot.",
        )
    if not any(c.isalpha() for c in v):
        # Phone numbers, numeric ids: must contain at least one letter.
        raise SocialError("SOCIAL_INVALID_USERNAME", "Username must contain a letter.")
    if v.count(".") > 1:
        # Three dot-separated base64-ish segments is a JWT, not a handle.
        raise SocialError("SOCIAL_INVALID_USERNAME", "Username may contain at most one dot.")
    if v[0] in "._" or v[-1] in "._":
        raise SocialError(
            "SOCIAL_INVALID_USERNAME", "Username may not start or end with a dot or underscore."
        )
    if "@" in raw:
        raise SocialError("SOCIAL_INVALID_USERNAME", "Username may not be an email address.")
    return v


def _canonical_pair(a: uuid.UUID, b: uuid.UUID) -> tuple[uuid.UUID, uuid.UUID]:
    if a == b:
        raise SocialError("SOCIAL_CANNOT_TARGET_SELF", "You cannot target yourself.", 422)
    return (a, b) if a < b else (b, a)


async def _active_user(db: AsyncSession, user_id: uuid.UUID) -> User | None:
    # selectinload(User.profile) is required, not an optimisation: callers pass
    # this User to ensure_profile(), which reads user.profile.display_name. A
    # lazy load there is sync IO on an async session and raises MissingGreenlet
    # (HTTP 500) the first time a rider who has never edited their own profile
    # is resolved as somebody else's target.
    res = await db.execute(
        select(User)
        .where(User.id == user_id, User.status == UserStatus.ACTIVE, User.deleted_at.is_(None))
        .options(selectinload(User.profile))
    )
    return res.scalar_one_or_none()


async def ensure_profile(db: AsyncSession, user: User) -> SocialProfile:
    """Lazily create the public projection, seeding the display name from the
    private account profile. Never invents a username."""
    res = await db.execute(select(SocialProfile).where(SocialProfile.user_id == user.id))
    profile = res.scalar_one_or_none()
    if profile is not None:
        return profile
    display = (user.profile.display_name if user.profile else None) or "Rider"
    profile = SocialProfile(
        user_id=user.id,
        username=None,
        display_name=display[:80],
        created_at=_now(),
        updated_at=_now(),
    )
    db.add(profile)
    await db.flush()
    return profile


async def _blocked_either_way(db: AsyncSession, a: uuid.UUID, b: uuid.UUID) -> bool:
    res = await db.execute(
        select(UserBlock.id).where(
            or_(
                and_(UserBlock.blocker_user_id == a, UserBlock.blocked_user_id == b),
                and_(UserBlock.blocker_user_id == b, UserBlock.blocked_user_id == a),
            )
        )
    )
    return res.scalar_one_or_none() is not None


async def _relationship(db: AsyncSession, a: uuid.UUID, b: uuid.UUID) -> FriendRelationship | None:
    low, high = _pair_or_none(a, b)
    if low is None:
        return None
    res = await db.execute(
        select(FriendRelationship).where(
            FriendRelationship.user_a_id == low, FriendRelationship.user_b_id == high
        )
    )
    return res.scalar_one_or_none()


def _pair_or_none(a: uuid.UUID, b: uuid.UUID) -> tuple[uuid.UUID | None, uuid.UUID | None]:
    if a == b:
        return None, None
    return (a, b) if a < b else (b, a)


async def _lock_pair(db: AsyncSession, a: uuid.UUID, b: uuid.UUID) -> None:
    """Serialize every mutation on one unordered pair.

    Unique constraints alone leave check-then-act races (a block landing
    between the blocked-check and the request INSERT produces a blocked pair
    with a live request). One advisory lock on the canonical pair key, held
    to transaction end, makes send/accept/remove/block mutually exclusive
    per pair. Single lock per transaction: no lock ordering, no deadlocks.
    """
    low, high = _canonical_pair(a, b)
    await db.execute(select(func.pg_advisory_xact_lock(func.hashtext(f"social:{low}:{high}"))))


def relationship_state(
    viewer_id: uuid.UUID,
    target_id: uuid.UUID,
    rel: FriendRelationship | None,
    i_blocked_them: bool,
    they_blocked_me: bool,
) -> str:
    if viewer_id == target_id:
        return "SELF"
    if i_blocked_them:
        return "BLOCKED"
    if they_blocked_me:
        return "BLOCKED_BY_USER"
    if rel is None:
        return "NONE"
    if rel.status == RelationshipStatus.ACCEPTED:
        return "FRIENDS"
    if rel.requested_by_user_id == viewer_id:
        return "OUTGOING_PENDING"
    return "INCOMING_PENDING"


async def _block_flags(db: AsyncSession, viewer: uuid.UUID, target: uuid.UUID) -> tuple[bool, bool]:
    res = await db.execute(
        select(UserBlock.blocker_user_id, UserBlock.blocked_user_id).where(
            or_(
                and_(UserBlock.blocker_user_id == viewer, UserBlock.blocked_user_id == target),
                and_(UserBlock.blocker_user_id == target, UserBlock.blocked_user_id == viewer),
            )
        )
    )
    i_blocked = they_blocked = False
    for blocker, blocked in res.all():
        if blocker == viewer:
            i_blocked = True
        if blocked == viewer:
            they_blocked = True
    return i_blocked, they_blocked


def _public_view(profile: SocialProfile, state: str, *, limited: bool) -> dict:
    if limited:
        return {
            "user_id": profile.user_id,
            "username": profile.username,
            "display_name": profile.display_name,
            "bio": None,
            "avatar_url": profile.avatar_url,
            "cycling_category": None,
            "country_code": None,
            "city": None,
            "relationship": state,
            "limited": True,
        }
    return {
        "user_id": profile.user_id,
        "username": profile.username,
        "display_name": profile.display_name,
        "bio": profile.bio,
        "avatar_url": profile.avatar_url,
        "cycling_category": profile.cycling_category,
        "country_code": profile.country_code,
        "city": profile.city,
        "relationship": state,
        "limited": False,
    }


async def view_profile(db: AsyncSession, viewer: User, target_id: uuid.UUID) -> dict:
    """Privacy-filtered profile. Missing/inactive users are 404 without
    distinguishing the reason; blocks never leak through this path."""
    target = await _active_user(db, target_id)
    if target is None:
        raise SocialError("SOCIAL_USER_NOT_FOUND", "User not found.", 404)
    profile = await ensure_profile(db, target)
    await db.commit()
    i_blocked, they_blocked = await _block_flags(db, viewer.id, target_id)
    rel = await _relationship(db, viewer.id, target_id)
    state = relationship_state(viewer.id, target_id, rel, i_blocked, they_blocked)
    if state == "SELF":
        # Owner sees everything; relationship is self by definition.
        return _public_view(profile, state, limited=False)
    if they_blocked:
        return _public_view(profile, state, limited=True)
    visibility = profile.profile_visibility
    if visibility == "public":
        return _public_view(profile, state, limited=False)
    if visibility == "friends":
        is_friend = rel is not None and rel.status == RelationshipStatus.ACCEPTED
        return _public_view(profile, state, limited=not is_friend)
    return _public_view(profile, state, limited=True)


async def update_profile(db: AsyncSession, user: User, data: dict) -> SocialProfile:
    profile = await ensure_profile(db, user)
    if "username" in data and data["username"] is not None:
        username = canonical_username(data["username"])
        clash = await db.execute(
            select(SocialProfile.user_id).where(
                SocialProfile.username == username, SocialProfile.user_id != user.id
            )
        )
        if clash.scalar_one_or_none() is not None:
            raise SocialError("SOCIAL_USERNAME_TAKEN", "That username is taken.", 409)
        profile.username = username
    elif "username" in data:
        profile.username = None
    if "display_name" in data and data["display_name"] is not None:
        name = data["display_name"].strip()
        if not name:
            raise SocialError("SOCIAL_INVALID_PROFILE", "Display name cannot be blank.", 422)
        profile.display_name = name[:80]
    if "bio" in data:
        profile.bio = data["bio"]
    if "avatar_url" in data:
        url = data["avatar_url"]
        if url is not None and not url.startswith(("https://", "http://")):
            raise SocialError("SOCIAL_INVALID_AVATAR", "Avatar must be an http(s) URL.", 422)
        profile.avatar_url = url
    if "cycling_category" in data:
        profile.cycling_category = data["cycling_category"]
    if "country_code" in data:
        cc = data["country_code"]
        if cc is not None and not _COUNTRY_RE.match(cc):
            raise SocialError("SOCIAL_INVALID_COUNTRY", "Country must be ISO-3166 alpha-2.", 422)
        profile.country_code = cc.upper() if cc else None
    if "city" in data:
        profile.city = data["city"]
    profile.updated_at = _now()
    await db.commit()
    await db.refresh(profile)
    return profile


async def update_privacy(db: AsyncSession, user: User, data: dict) -> SocialProfile:
    profile = await ensure_profile(db, user)
    if "profile_visibility" in data and data["profile_visibility"] is not None:
        profile.profile_visibility = data["profile_visibility"]
    if "allow_friend_requests" in data and data["allow_friend_requests"] is not None:
        profile.allow_friend_requests = FriendRequestsPolicy(data["allow_friend_requests"])
    if "search_visibility" in data and data["search_visibility"] is not None:
        profile.search_visibility = SearchVisibility(data["search_visibility"])
    profile.updated_at = _now()
    await db.commit()
    await db.refresh(profile)
    return profile


def _like_escape(q: str) -> str:
    return q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


async def search(
    db: AsyncSession, viewer: User, q: str, page: int, page_size: int
) -> tuple[list[dict], int]:
    """Privacy-aware discovery over username/display name only. Never email,
    never phone. Blocked pairs (either direction), hidden, and private
    profiles are excluded outright — not redacted, excluded."""
    pattern = f"%{_like_escape(q.strip())}%"
    base = (
        select(SocialProfile, User)
        .join(User, User.id == SocialProfile.user_id)
        .where(
            User.status == UserStatus.ACTIVE,
            User.deleted_at.is_(None),
            SocialProfile.user_id != viewer.id,
            SocialProfile.search_visibility == SearchVisibility.DISCOVERABLE,
            SocialProfile.profile_visibility != "private",
            or_(
                SocialProfile.username.ilike(pattern),
                SocialProfile.display_name.ilike(pattern),
            ),
            ~select(UserBlock.id)
            .where(
                or_(
                    and_(
                        UserBlock.blocker_user_id == viewer.id,
                        UserBlock.blocked_user_id == SocialProfile.user_id,
                    ),
                    and_(
                        UserBlock.blocker_user_id == SocialProfile.user_id,
                        UserBlock.blocked_user_id == viewer.id,
                    ),
                )
            )
            .exists(),
        )
        .order_by(SocialProfile.display_name, SocialProfile.user_id)
    )
    total = (await db.execute(select(func.count()).select_from(base.subquery()))).scalar_one()
    rows = (await db.execute(base.offset((page - 1) * page_size).limit(page_size))).all()
    if not rows:
        return [], total
    ids = [p.user_id for p, _ in rows]
    # One query for every relationship touching the viewer and this page:
    # no N+1 regardless of page size.
    rels = (
        await db.execute(
            select(FriendRelationship).where(
                or_(
                    and_(
                        FriendRelationship.user_a_id == viewer.id,
                        FriendRelationship.user_b_id.in_(ids),
                    ),
                    and_(
                        FriendRelationship.user_b_id == viewer.id,
                        FriendRelationship.user_a_id.in_(ids),
                    ),
                )
            )
        )
    ).scalars()
    by_other = {}
    for r in rels:
        other = r.user_b_id if r.user_a_id == viewer.id else r.user_a_id
        by_other[other] = r
    items = []
    for profile, _user in rows:
        rel = by_other.get(profile.user_id)
        state = relationship_state(viewer.id, profile.user_id, rel, False, False)
        limited = profile.profile_visibility != "public" and state != "FRIENDS"
        items.append(_public_view(profile, state, limited=limited))
    return items, total


async def send_request(
    db: AsyncSession, requester: User, target_id: uuid.UUID
) -> FriendRelationship:
    # Capture the id up front: after a rollback below, all ORM state is
    # expired and touching `requester.id` would trigger a sync lazy load.
    requester_id = requester.id
    if target_id == requester_id:
        raise SocialError("SOCIAL_CANNOT_TARGET_SELF", "You cannot friend yourself.", 422)
    target = await _active_user(db, target_id)
    if target is None:
        raise SocialError("SOCIAL_USER_NOT_FOUND", "User not found.", 404)
    # Either direction blocked: 404, no leak about who blocked whom.
    await _lock_pair(db, requester_id, target_id)
    if await _blocked_either_way(db, requester_id, target_id):
        raise SocialError("SOCIAL_USER_NOT_FOUND", "User not found.", 404)
    tprof = await ensure_profile(db, target)
    await ensure_profile(db, requester)
    if tprof.allow_friend_requests == FriendRequestsPolicy.NOBODY:
        raise SocialError(
            "SOCIAL_REQUESTS_NOT_ALLOWED", "This rider is not accepting requests.", 403
        )
    if tprof.profile_visibility == "private":
        raise SocialError(
            "SOCIAL_REQUESTS_NOT_ALLOWED", "This rider is not accepting requests.", 403
        )
    low, high = _canonical_pair(requester_id, target_id)
    row = FriendRelationship(
        user_a_id=low,
        user_b_id=high,
        requested_by_user_id=requester_id,
        status=RelationshipStatus.PENDING,
        created_at=_now(),
        updated_at=_now(),
    )
    db.add(row)
    try:
        await db.flush()
    except IntegrityError:
        # Lost the race (or retried): exactly one row exists; answer from it.
        # `requester_id` is a plain local because the rollback below expires
        # every ORM attribute, including the requester's own id.
        await db.rollback()
        existing = await _relationship(db, requester_id, target_id)
        if existing is None:  # pragma: no cover - defensive; constraint says it exists
            raise SocialError("SOCIAL_REQUEST_PENDING", "A request is already pending.", 409)
        if existing.status == RelationshipStatus.ACCEPTED:
            raise SocialError("SOCIAL_ALREADY_FRIENDS", "You are already friends.", 409)
        raise SocialError("SOCIAL_REQUEST_PENDING", "A request is already pending.", 409)
    await db.commit()
    await db.refresh(row)
    _log("request_created", requester_id=str(requester_id), target_id=str(target_id))
    # After the commit: a notification for a rolled-back transaction would tell a
    # rider about a request that does not exist.
    await _notify_safely(
        notification_service.notify_friend_request,
        db,
        actor_id=requester_id,
        target_id=target_id,
        request_id=row.id,
    )
    return row


async def _notify_safely(coro_factory, db: AsyncSession, **kwargs) -> None:
    """Run a notification hook, swallowing any failure.

    A notification is an accelerant, never a precondition: a rider's friend
    request must succeed even if the notification could not be written.
    """
    try:
        await coro_factory(db, **kwargs)
    except Exception as exc:  # noqa: BLE001 — never break the business action
        # `hook=` rather than `event=`: `_log`'s first positional parameter is
        # already named `event`, so passing `event=` as a field collides with it.
        _log("notification_failed", hook=coro_factory.__name__, error_category=type(exc).__name__)


def _request_or_404(
    row: FriendRelationship | None, me: uuid.UUID, *, must_be_recipient: bool
) -> FriendRelationship:
    if row is None or row.status != RelationshipStatus.PENDING:
        raise SocialError("SOCIAL_REQUEST_NOT_FOUND", "Request not found.", 404)
    if must_be_recipient and row.requested_by_user_id == me:
        # Your own outgoing request is not acceptable/rejectable by you here.
        raise SocialError("SOCIAL_REQUEST_NOT_FOUND", "Request not found.", 404)
    if not must_be_recipient and row.requested_by_user_id != me:
        raise SocialError("SOCIAL_REQUEST_NOT_FOUND", "Request not found.", 404)
    return row


async def accept_request(
    db: AsyncSession, viewer: User, request_id: uuid.UUID
) -> FriendRelationship:
    viewer_id = viewer.id
    peek = await db.execute(
        select(FriendRelationship.user_a_id, FriendRelationship.user_b_id).where(
            FriendRelationship.id == request_id
        )
    )
    pair = peek.one_or_none()
    if pair is None:
        raise SocialError("SOCIAL_REQUEST_NOT_FOUND", "Request not found.", 404)
    await _lock_pair(db, pair[0], pair[1])
    res = await db.execute(
        select(FriendRelationship).where(FriendRelationship.id == request_id).with_for_update()
    )
    row = res.scalar_one_or_none()
    if row is None or row.requested_by_user_id == viewer_id:
        raise SocialError("SOCIAL_REQUEST_NOT_FOUND", "Request not found.", 404)
    if viewer_id not in (row.user_a_id, row.user_b_id):
        raise SocialError("SOCIAL_REQUEST_NOT_FOUND", "Request not found.", 404)
    # A block landed while this was pending: the request is dead.
    if await _blocked_either_way(db, row.user_a_id, row.user_b_id):
        await db.execute(delete(FriendRelationship).where(FriendRelationship.id == row.id))
        await db.commit()
        raise SocialError("SOCIAL_REQUEST_NOT_FOUND", "Request not found.", 404)
    if row.status == RelationshipStatus.ACCEPTED:
        await db.commit()
        return row  # idempotent: simultaneous accepts converge here
    row.status = RelationshipStatus.ACCEPTED
    row.updated_at = _now()
    await db.commit()
    await db.refresh(row)
    _log("request_accepted", request_id=str(row.id), by=str(viewer.id))
    # The original requester is the one who wants to know. `viewer` is the
    # accepting party, so the recipient is the other end of the pair.
    await _notify_safely(
        notification_service.notify_friend_request_accepted,
        db,
        actor_id=viewer_id,
        target_id=row.requested_by_user_id,
        request_id=row.id,
    )
    return row


async def reject_request(db: AsyncSession, viewer: User, request_id: uuid.UUID) -> None:
    res = await db.execute(
        select(FriendRelationship)
        .where(
            FriendRelationship.id == request_id,
            FriendRelationship.status == RelationshipStatus.PENDING,
        )
        .with_for_update()
    )
    row = res.scalar_one_or_none()
    _request_or_404(row, viewer.id, must_be_recipient=True)
    assert row is not None
    if viewer.id not in (row.user_a_id, row.user_b_id):
        raise SocialError("SOCIAL_REQUEST_NOT_FOUND", "Request not found.", 404)
    await db.execute(delete(FriendRelationship).where(FriendRelationship.id == row.id))
    await db.commit()
    _log("request_rejected", request_id=str(request_id), by=str(viewer.id))


async def cancel_request(db: AsyncSession, viewer: User, request_id: uuid.UUID) -> None:
    res = await db.execute(
        select(FriendRelationship)
        .where(
            FriendRelationship.id == request_id,
            FriendRelationship.status == RelationshipStatus.PENDING,
        )
        .with_for_update()
    )
    row = res.scalar_one_or_none()
    _request_or_404(row, viewer.id, must_be_recipient=False)
    assert row is not None
    await db.execute(delete(FriendRelationship).where(FriendRelationship.id == row.id))
    await db.commit()
    _log("request_cancelled", request_id=str(request_id), by=str(viewer.id))


def _request_condition(viewer_id: uuid.UUID, direction: str):
    """Incoming: rows where I am a party but did not send. Outgoing: rows I sent."""
    if direction == "incoming":
        mine = or_(
            FriendRelationship.user_a_id == viewer_id,
            FriendRelationship.user_b_id == viewer_id,
        )
        return and_(mine, FriendRelationship.requested_by_user_id != viewer_id)
    return FriendRelationship.requested_by_user_id == viewer_id


async def list_requests(
    db: AsyncSession, viewer: User, direction: str, page: int, page_size: int
) -> tuple[list[dict], int]:
    cond = _request_condition(viewer.id, direction)
    base = (
        select(FriendRelationship)
        .where(FriendRelationship.status == RelationshipStatus.PENDING, cond)
        .order_by(FriendRelationship.created_at.desc(), FriendRelationship.id)
    )
    total = (await db.execute(select(func.count()).select_from(base.subquery()))).scalar_one()
    rows = (await db.execute(base.offset((page - 1) * page_size).limit(page_size))).scalars()
    items = []
    for row in rows:
        if direction == "incoming":
            other_id = row.requested_by_user_id
        elif row.user_a_id == viewer.id:
            other_id = row.user_b_id
        else:
            other_id = row.user_a_id
        prof = (
            await db.execute(select(SocialProfile).where(SocialProfile.user_id == other_id))
        ).scalar_one_or_none()
        items.append(
            {
                "id": row.id,
                "user_id": other_id,
                "username": prof.username if prof else None,
                "display_name": prof.display_name if prof else None,
                "avatar_url": prof.avatar_url if prof else None,
                "direction": direction,
                "status": "pending",
                "created_at": row.created_at,
            }
        )
    return items, total


async def list_friends(
    db: AsyncSession, viewer: User, page: int, page_size: int
) -> tuple[list[dict], int]:
    pair = or_(
        FriendRelationship.user_a_id == viewer.id,
        FriendRelationship.user_b_id == viewer.id,
    )
    base = (
        select(FriendRelationship)
        .where(pair, FriendRelationship.status == RelationshipStatus.ACCEPTED)
        .order_by(FriendRelationship.updated_at.desc(), FriendRelationship.id)
    )
    total = (await db.execute(select(func.count()).select_from(base.subquery()))).scalar_one()
    rows = (await db.execute(base.offset((page - 1) * page_size).limit(page_size))).scalars()
    items = []
    for row in rows:
        other_id = row.user_b_id if row.user_a_id == viewer.id else row.user_a_id
        prof = (
            await db.execute(select(SocialProfile).where(SocialProfile.user_id == other_id))
        ).scalar_one_or_none()
        items.append(
            {
                "user_id": other_id,
                "username": prof.username if prof else None,
                "display_name": prof.display_name if prof else None,
                "avatar_url": prof.avatar_url if prof else None,
                "friends_since": row.updated_at,
            }
        )
    return items, total


async def remove_friend(db: AsyncSession, viewer: User, friend_id: uuid.UUID) -> None:
    viewer_id = viewer.id
    if friend_id == viewer_id:
        raise SocialError("SOCIAL_FRIENDSHIP_NOT_FOUND", "Friendship not found.", 404)
    await _lock_pair(db, viewer_id, friend_id)
    rel = await _relationship(db, viewer_id, friend_id)
    if rel is None or rel.status != RelationshipStatus.ACCEPTED:
        raise SocialError("SOCIAL_FRIENDSHIP_NOT_FOUND", "Friendship not found.", 404)
    await db.execute(delete(FriendRelationship).where(FriendRelationship.id == rel.id))
    await db.commit()
    _log("friend_removed", by=str(viewer_id), friend_id=str(friend_id))


async def block_user(db: AsyncSession, viewer: User, target_id: uuid.UUID) -> UserBlock:
    # Plain local: the rollback below expires every ORM attribute.
    viewer_id = viewer.id
    if target_id == viewer_id:
        raise SocialError("SOCIAL_CANNOT_TARGET_SELF", "You cannot block yourself.", 422)
    target = await _active_user(db, target_id)
    if target is None:
        raise SocialError("SOCIAL_USER_NOT_FOUND", "User not found.", 404)
    await _lock_pair(db, viewer_id, target_id)
    existing = await db.execute(
        select(UserBlock).where(
            UserBlock.blocker_user_id == viewer_id, UserBlock.blocked_user_id == target_id
        )
    )
    row = existing.scalar_one_or_none()
    if row is not None:
        return row  # idempotent
    row = UserBlock(blocker_user_id=viewer_id, blocked_user_id=target_id, created_at=_now())
    db.add(row)
    try:
        await db.flush()
    except IntegrityError:
        # Concurrent block won the race: answer from the winning row.
        await db.rollback()
        reread = await db.execute(
            select(UserBlock).where(
                UserBlock.blocker_user_id == viewer_id,
                UserBlock.blocked_user_id == target_id,
            )
        )
        found = reread.scalar_one_or_none()
        if found is None:  # pragma: no cover - defensive
            raise SocialError("SOCIAL_BLOCK_FAILED", "Could not block user.", 409)
        return found
    # The wall goes up: no friendship or pending request may survive it.
    low, high = _canonical_pair(viewer_id, target_id)
    await db.execute(
        delete(FriendRelationship).where(
            FriendRelationship.user_a_id == low, FriendRelationship.user_b_id == high
        )
    )
    await db.commit()
    await db.refresh(row)
    _log("user_blocked", blocker_id=str(viewer_id), blocked_id=str(target_id))
    return row


async def unblock_user(db: AsyncSession, viewer: User, target_id: uuid.UUID) -> None:
    res = await db.execute(
        select(UserBlock).where(
            UserBlock.blocker_user_id == viewer.id, UserBlock.blocked_user_id == target_id
        )
    )
    row = res.scalar_one_or_none()
    if row is None:
        # Someone else's block, or nothing: identical 404, no leak.
        raise SocialError("SOCIAL_BLOCK_NOT_FOUND", "Block not found.", 404)
    await db.execute(delete(UserBlock).where(UserBlock.id == row.id))
    await db.commit()
    _log("user_unblocked", blocker_id=str(viewer.id), blocked_id=str(target_id))


async def list_blocks(
    db: AsyncSession, viewer: User, page: int, page_size: int
) -> tuple[list[dict], int]:
    base = (
        select(UserBlock)
        .where(UserBlock.blocker_user_id == viewer.id)
        .order_by(UserBlock.created_at.desc(), UserBlock.id)
    )
    total = (await db.execute(select(func.count()).select_from(base.subquery()))).scalar_one()
    rows = (await db.execute(base.offset((page - 1) * page_size).limit(page_size))).scalars()
    items = []
    for row in rows:
        prof = (
            await db.execute(
                select(SocialProfile).where(SocialProfile.user_id == row.blocked_user_id)
            )
        ).scalar_one_or_none()
        items.append(
            {
                "user_id": row.blocked_user_id,
                "username": prof.username if prof else None,
                "display_name": prof.display_name if prof else None,
                "blocked_at": row.created_at,
            }
        )
    return items, total

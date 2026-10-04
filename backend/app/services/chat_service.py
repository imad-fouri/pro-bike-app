"""Phase 8.3 chat service: conversations, messages, read state (ADR-14).

Four rules hold everywhere in this module.

1. AUTHORIZATION IS RE-DERIVED PER REQUEST. Nothing is trusted from the caller
   beyond the authenticated `User`. For a TEAM channel the live
   `team_memberships` row is re-read inside the transaction — the
   `conversation_members` row is a roster snapshot, never the authority. A
   rider removed from a team keeps their member row (so history stays
   attributable) and must therefore still be refused.

2. UNAUTHORIZED IS 404, NEVER 403. A caller with no standing gets the same
   answer as a conversation that does not exist, so no endpoint can be used to
   probe which conversation ids are real (ADR-14 §12).

3. BLOCKS BOUND DIRECT MESSAGES ONLY. A block in either direction refuses DM
   creation and DM sending. It does NOT touch a team channel: a block is a
   personal-interaction boundary, not a team membership or visibility change,
   and severance would broadcast the block to the whole team (ADR-14 §2).

4. NOTHING IS EVER HARD-DELETEd. A message row is only ever flagged
   (`edited_at`, `deleted_at`) so history stays attributable and ordered
   (ADR-14 §4, §6).

Concurrency: one advisory transaction lock per conversation serializes `seq`
allocation and every mutation inside it, and one per DM pair serializes
find-or-create. The unique constraints remain the final arbiter, so a lost
race re-reads and answers from the winning row rather than duplicating.
"""

import logging
import uuid
from datetime import UTC, datetime, timedelta

import sqlalchemy as sa
from sqlalchemy import and_, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased, selectinload

from app.core.logging import redact
from app.models.chat import (
    DELETED_PLACEHOLDER,
    EDIT_WINDOW_MINUTES,
    Conversation,
    ConversationKind,
    ConversationMember,
    Message,
    MessageType,
)
from app.models.social import SocialProfile, UserBlock
from app.models.team import Team, TeamMembership, TeamStatus
from app.models.user import User, UserStatus
from app.services import notification_service

log = logging.getLogger("cyclecoach")

MAX_BODY = 4000
#: Page size ceiling for message history.
MAX_HISTORY = 100
DEFAULT_HISTORY = 50


class ChatError(Exception):
    def __init__(self, code: str, message: str, status: int = 422) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


def _now() -> datetime:
    return datetime.now(UTC)


def _log(event: str, **fields: object) -> None:
    """Ids only. Never a body: `redact` would blank it, but the discipline is
    the point — message content must not reach the log pipeline at all."""
    log.info(f"chat.{event}", extra=redact(dict(fields)))


# ---------------------------------------------------------------------------
# Locks
# ---------------------------------------------------------------------------


async def _lock_conversation(db: AsyncSession, conversation_id: uuid.UUID) -> None:
    """Serialize everything inside one conversation.

    `seq` allocation is a read-modify-write on `conversations.next_seq`; without
    this lock two concurrent sends would both read 42 and both try to write 43,
    and one would lose the UNIQUE constraint and surface as a 500 instead of an
    idempotent answer. Edit and delete also take it, so an edit can never race a
    delete into an inconsistent state.

    One lock per transaction: no ordering, no deadlock.
    """
    await db.execute(select(func.pg_advisory_xact_lock(func.hashtext(f"chat:{conversation_id}"))))


async def _lock_dm_pair(db: AsyncSession, a: uuid.UUID, b: uuid.UUID) -> None:
    """Serialize DM find-or-create for one unordered pair (ADR-12 §2.2 shape)."""
    low, high = (a, b) if a < b else (b, a)
    await db.execute(select(func.pg_advisory_xact_lock(func.hashtext(f"dm:{low}:{high}"))))


# ---------------------------------------------------------------------------
# Lookups
# ---------------------------------------------------------------------------


async def _active_user(db: AsyncSession, user_id: uuid.UUID) -> User | None:
    res = await db.execute(
        select(User)
        .where(User.id == user_id, User.status == UserStatus.ACTIVE, User.deleted_at.is_(None))
        .options(selectinload(User.profile))
    )
    return res.scalar_one_or_none()


async def _team_membership(
    db: AsyncSession, team_id: uuid.UUID, user_id: uuid.UUID
) -> TeamMembership | None:
    """The LIVE team membership. This — not the conversation roster — is the
    authority for a team channel."""
    res = await db.execute(
        select(TeamMembership).where(
            TeamMembership.team_id == team_id, TeamMembership.user_id == user_id
        )
    )
    return res.scalar_one_or_none()


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


async def _member_row(
    db: AsyncSession, conversation_id: uuid.UUID, user_id: uuid.UUID
) -> ConversationMember | None:
    res = await db.execute(
        select(ConversationMember).where(
            ConversationMember.conversation_id == conversation_id,
            ConversationMember.user_id == user_id,
        )
    )
    return res.scalar_one_or_none()


async def _conversation_row(db: AsyncSession, conversation_id: uuid.UUID) -> Conversation:
    res = await db.execute(select(Conversation).where(Conversation.id == conversation_id))
    row = res.scalar_one_or_none()
    if row is None:
        raise ChatError("CHAT_CONVERSATION_NOT_FOUND", "Conversation not found.", 404)
    return row


async def _require_participant(
    db: AsyncSession, conversation: Conversation, viewer: User
) -> ConversationMember:
    """Authorize one read/write on a conversation.

    The two branches differ deliberately:

    * DIRECT — the participant row IS the authority, plus the block policy.
      A block in EITHER direction refuses **reading as well as sending**: the
      history stays *retained* on the server, but neither party may read the
      thread while the block stands. Unblocking restores the exact thread
      (ADR-14 §2.1). "Retained" and "readable" are deliberately different
      properties — the history is never deleted, so it is all still there when
      the block is lifted.
    * TEAM — the participant row gates *existence*, but the live team membership
      decides standing. A rider removed from a team keeps their roster row so
      history stays attributable, which means the roster alone would wrongly
      keep granting access.
    """
    member = await _member_row(db, conversation.id, viewer.id)
    if member is None:
        # No roster row: indistinguishable from a conversation that never existed.
        raise ChatError("CHAT_CONVERSATION_NOT_FOUND", "Conversation not found.", 404)

    if conversation.kind == ConversationKind.TEAM:
        assert conversation.team_id is not None  # guaranteed by CHECK
        team = (
            await db.execute(select(Team).where(Team.id == conversation.team_id))
        ).scalar_one_or_none()
        if team is None:
            raise ChatError("CHAT_CONVERSATION_NOT_FOUND", "Conversation not found.", 404)
        membership = await _team_membership(db, team.id, viewer.id)
        if membership is None:
            # Removed or never joined: history is not theirs to read.
            raise ChatError("CHAT_CONVERSATION_NOT_FOUND", "Conversation not found.", 404)
    else:
        # Direct: a block stops personal messaging in BOTH directions. The
        # refusal is deliberately not 403 — a distinct code would confirm the
        # conversation exists.
        peer = await _peer_of(db, conversation.id, viewer.id)
        if peer is not None and await _blocked_either_way(db, viewer.id, peer):
            raise ChatError("CHAT_CONVERSATION_NOT_FOUND", "Conversation not found.", 404)
    return member


async def _peer_of(
    db: AsyncSession, conversation_id: uuid.UUID, viewer_id: uuid.UUID
) -> uuid.UUID | None:
    res = await db.execute(
        select(ConversationMember.user_id).where(
            ConversationMember.conversation_id == conversation_id,
            ConversationMember.user_id != viewer_id,
        )
    )
    return res.scalar_one_or_none()


async def _assert_can_send(db: AsyncSession, conversation: Conversation, viewer: User) -> None:
    """Everything required to WRITE, beyond being a participant.

    Called inside the conversation lock and re-reading live state, so a request
    admitted just before a removal or archive commits is still refused
    (ADR-14 §14).
    """
    if conversation.kind == ConversationKind.DIRECT:
        peer = await _peer_of(db, conversation.id, viewer.id)
        if peer is not None and await _blocked_either_way(db, viewer.id, peer):
            raise ChatError("CHAT_BLOCKED", "You cannot message this rider.", 403)
        return

    assert conversation.team_id is not None
    team = (
        await db.execute(select(Team).where(Team.id == conversation.team_id))
    ).scalar_one_or_none()
    if team is None:
        raise ChatError("CHAT_TEAM_UNAVAILABLE", "This team is unavailable.", 404)
    if team.status != TeamStatus.ACTIVE:
        # An archived team keeps its history and accepts no new messages.
        raise ChatError("CHAT_TEAM_ARCHIVED", "This team is archived.", 403)
    if await _team_membership(db, team.id, viewer.id) is None:
        raise ChatError("CHAT_FORBIDDEN", "You cannot post in this team.", 403)


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------


async def _identities(db: AsyncSession, user_ids: list[uuid.UUID]) -> dict[uuid.UUID, dict]:
    """Public social projection for the given riders.

    Reads `social_profiles`, never `users`: a message list must not be able to
    surface an email address or any other account-private column.
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


def _message_view(
    row: Message,
    *,
    identities: dict[uuid.UUID, dict],
    viewer_id: uuid.UUID,
) -> dict:
    """One message for one viewer.

    The only per-viewer variation is `is_mine` / `can_edit`; the body is
    identical for everyone, which is what makes a cached page safe to share
    between a phone and a tablet.
    """
    deleted = row.deleted_at is not None
    who = identities.get(row.sender_user_id, {})
    # can_edit is computed server-side from the 15-minute window so the client
    # never has to re-derive it from a local clock (ADR-14 §9).
    can_edit = (
        row.sender_user_id == viewer_id
        and not deleted
        and row.created_at + timedelta(minutes=EDIT_WINDOW_MINUTES) > _now()
    )
    return {
        "id": row.id,
        "conversation_id": row.conversation_id,
        "seq": row.seq,
        "sender_user_id": row.sender_user_id,
        "sender_username": who.get("username"),
        "sender_display_name": who.get("display_name"),
        "message_type": row.message_type,
        # A soft-deleted message never exposes its original body.
        "body": DELETED_PLACEHOLDER if deleted else row.body,
        "is_deleted": deleted,
        "is_edited": row.edited_at is not None,
        "is_mine": row.sender_user_id == viewer_id,
        "can_edit": can_edit,
        "created_at": row.created_at,
        "edited_at": row.edited_at,
    }


async def _messages_page(
    db: AsyncSession,
    conversation: Conversation,
    viewer: User,
    *,
    before_seq: int | None,
    limit: int,
) -> tuple[list[dict], bool, int | None]:
    """Cursor page, newest first.

    Keyset on `seq` rather than offset: while a rider scrolls back, new
    messages land at the head, and offset pagination would shift rows under
    them and show duplicates or gaps. `seq` is unique per conversation, so the
    cursor is unambiguous (ADR-14 §8).
    """
    stmt = select(Message).where(Message.conversation_id == conversation.id)
    if before_seq is not None:
        stmt = stmt.where(Message.seq < before_seq)
    stmt = stmt.order_by(Message.seq.desc()).limit(limit + 1)
    rows = (await db.execute(stmt)).scalars().all()
    has_more = len(rows) > limit
    rows = rows[:limit]
    identities = await _identities(db, [r.sender_user_id for r in rows])
    items = [_message_view(r, identities=identities, viewer_id=viewer.id) for r in rows]
    next_cursor = rows[-1].seq if (rows and has_more) else None
    return items, has_more, next_cursor


# ---------------------------------------------------------------------------
# Conversations: discovery
# ---------------------------------------------------------------------------


async def list_conversations(
    db: AsyncSession, viewer: User, page: int, page_size: int
) -> tuple[list[dict], int]:
    """The viewer's inbox: every conversation they hold a member row for.

    Team channels the viewer has since lost standing for are filtered out at
    READ time rather than listed-then-failed: a 404 inbox row is a worse
    experience than an absent one, and the count stays honest.
    """
    base = (
        select(Conversation)
        .join(
            ConversationMember,
            and_(
                ConversationMember.conversation_id == Conversation.id,
                ConversationMember.user_id == viewer.id,
            ),
        )
        .order_by(Conversation.created_at.desc(), Conversation.id)
    )
    total = (await db.execute(select(func.count()).select_from(base.subquery()))).scalar_one()
    rows = (await db.execute(base.offset((page - 1) * page_size).limit(page_size))).scalars().all()
    if not rows:
        return [], total

    team_ids = [r.team_id for r in rows if r.kind == ConversationKind.TEAM]
    teams = await _teams_for(db, [t for t in team_ids if t is not None])
    # Live standing decides visibility: a removed rider's channel is not listed.
    standing: dict[uuid.UUID, TeamMembership | None] = {}
    for tid in {r.team_id for r in rows if r.kind == ConversationKind.TEAM and r.team_id}:
        standing[tid] = await _team_membership(db, tid, viewer.id)

    peers = [
        p
        for p in (await _peer_ids(db, [r.id for r in rows if r.kind == ConversationKind.DIRECT]))
        if p is not None
    ]
    identities = await _identities(db, peers)
    members = {
        m.conversation_id: m
        for m in (
            await db.execute(
                select(ConversationMember).where(
                    ConversationMember.conversation_id.in_([r.id for r in rows]),
                    ConversationMember.user_id == viewer.id,
                )
            )
        ).scalars()
    }

    items: list[dict] = []
    unread: dict[uuid.UUID, int] = {}
    for row in rows:
        if row.kind == ConversationKind.TEAM:
            assert row.team_id is not None
            if standing.get(row.team_id) is None:
                continue
        items.append(
            await _conversation_view(
                db,
                row,
                viewer,
                teams=teams,
                identities=identities,
                member=members.get(row.id),
                unread=unread,
            )
        )
    return items, total


async def _peer_ids(db: AsyncSession, conversation_ids: list[uuid.UUID]) -> list[uuid.UUID | None]:
    if not conversation_ids:
        return []
    res = await db.execute(
        select(ConversationMember.conversation_id, ConversationMember.user_id).where(
            ConversationMember.conversation_id.in_(conversation_ids)
        )
    )
    peers: dict[uuid.UUID, uuid.UUID] = {}
    for cid, uid in res.all():
        peers.setdefault(cid, uid)
    return list(peers.values())


async def _teams_for(db: AsyncSession, team_ids: list[uuid.UUID]) -> dict[uuid.UUID, Team]:
    if not team_ids:
        return {}
    res = await db.execute(select(Team).where(Team.id.in_(team_ids)))
    return {t.id: t for t in res.scalars()}


async def _last_messages(
    db: AsyncSession, conversation_ids: list[uuid.UUID]
) -> dict[uuid.UUID, Message]:
    """Latest message per conversation, in one round trip.

    A grouped subquery picks the highest `seq` per conversation — the same
    ordering the history uses — and the `ix_messages_conversation_seq` index
    serves both the grouping and the outer lookup.
    """
    if not conversation_ids:
        return {}
    newest = (
        select(
            Message.conversation_id.label("conversation_id"),
            func.max(Message.seq).label("seq"),
        )
        .where(Message.conversation_id.in_(conversation_ids))
        .group_by(Message.conversation_id)
        .subquery()
    )
    res = await db.execute(
        select(Message)
        .join(
            newest,
            and_(
                newest.c.conversation_id == Message.conversation_id,
                newest.c.seq == Message.seq,
            ),
        )
        .where(Message.conversation_id.in_(conversation_ids))
    )
    return {m.conversation_id: m for m in res.scalars()}


async def _conversation_view(
    db: AsyncSession,
    conversation: Conversation,
    viewer: User,
    *,
    teams: dict[uuid.UUID, Team],
    identities: dict[uuid.UUID, dict],
    member: ConversationMember | None,
    unread: dict[uuid.UUID, int],
) -> dict:
    last_row = (await _last_messages(db, [conversation.id])).get(conversation.id)
    preview = None
    if last_row is not None:
        preview = DELETED_PLACEHOLDER if last_row.deleted_at is not None else last_row.body[:120]
    last_read = member.last_read_seq if member is not None else 0
    count = 0
    if last_read < (conversation.next_seq - 1):
        count = (
            await db.execute(
                select(func.count())
                .select_from(Message)
                .where(
                    Message.conversation_id == conversation.id,
                    Message.seq > last_read,
                )
            )
        ).scalar_one()
    unread[conversation.id] = count

    # A "peer" is only meaningful for a DM, which by construction has exactly
    # one other member. A team channel has many, so querying it here would raise
    # MultipleResultsFound and 500 the whole view. Phase 8.3 shipped this bug
    # unexposed because its smoke only ever built two-member teams; a third
    # member is what makes it fire.
    is_team = conversation.kind == ConversationKind.TEAM
    peer = None if is_team else await _peer_of(db, conversation.id, viewer.id)
    team = teams.get(conversation.team_id) if conversation.team_id else None
    who = identities.get(peer, {}) if peer else {}
    return {
        "id": conversation.id,
        "kind": conversation.kind,
        "team_id": conversation.team_id,
        "team_name": team.name if team else None,
        "team_handle": team.handle if team else None,
        "team_visibility": team.visibility.value if team else None,
        "team_status": team.status.value if team else None,
        "peer_user_id": peer,
        "peer_username": who.get("username"),
        "peer_display_name": who.get("display_name"),
        "last_message_id": last_row.id if last_row else None,
        "last_message_seq": last_row.seq if last_row else None,
        "last_message_preview": preview,
        "last_message_at": last_row.created_at if last_row else None,
        "unread_count": count,
        "last_read_seq": last_read,
        "created_at": conversation.created_at,
    }


async def get_conversation(db: AsyncSession, viewer: User, conversation_id: uuid.UUID) -> dict:
    conversation = await _conversation_row(db, conversation_id)
    await _require_participant(db, conversation, viewer)
    return await _conversation_view(
        db,
        conversation,
        viewer,
        teams=await _teams_for(db, [conversation.team_id] if conversation.team_id else []),
        identities={},
        member=await _member_row(db, conversation.id, viewer.id),
        unread={},
    )


# ---------------------------------------------------------------------------
# Conversations: creation
# ---------------------------------------------------------------------------


async def open_direct(db: AsyncSession, viewer: User, target_id: uuid.UUID) -> dict:
    """Find-or-create the DM between the viewer and `target_id`.

    Friendship is deliberately NOT a precondition (ADR-14 §2): a DM is a
    personal-interaction right, not a social status, and gating on friendship
    would both exclude teammates who never friended each other and create an
    oracle for "are these two friends?".
    """
    if target_id == viewer.id:
        raise ChatError("CHAT_CANNOT_TARGET_SELF", "You cannot message yourself.", 422)
    await _lock_dm_pair(db, viewer.id, target_id)

    target = await _active_user(db, target_id)
    if target is None:
        raise ChatError("CHAT_USER_NOT_FOUND", "That rider is not available.", 404)
    if await _blocked_either_way(db, viewer.id, target_id):
        # 404, not 403: a distinct code would confirm the rider exists.
        raise ChatError("CHAT_USER_NOT_FOUND", "That rider is not available.", 404)

    existing = await _find_direct(db, viewer.id, target_id)
    if existing is not None:
        return await _conversation_view(
            db,
            existing,
            viewer,
            teams={},
            identities=await _identities(db, [target_id]),
            member=await _member_row(db, existing.id, viewer.id),
            unread={},
        )

    now = _now()
    low, high = (viewer.id, target_id) if viewer.id < target_id else (target_id, viewer.id)
    conversation = Conversation(
        kind=ConversationKind.DIRECT,
        team_id=None,
        created_by_user_id=viewer.id,
        created_at=now,
        next_seq=1,
    )
    db.add(conversation)
    await db.flush()
    db.add(ConversationMember(conversation_id=conversation.id, user_id=viewer.id, joined_at=now))
    db.add(ConversationMember(conversation_id=conversation.id, user_id=target_id, joined_at=now))
    try:
        await db.commit()
    except IntegrityError:
        # Lost the race: the winner's conversation is authoritative.
        await db.rollback()
        winner = await _find_direct(db, viewer.id, target_id)
        if winner is None:  # pragma: no cover - defensive
            raise ChatError("CHAT_CONFLICT", "Conversation conflict.", 409) from None
        conversation = winner
    await db.refresh(conversation)
    _log("direct_opened", conversation_id=str(conversation.id), by=str(viewer.id))
    return await _conversation_view(
        db,
        conversation,
        viewer,
        teams={},
        identities=await _identities(db, [target_id]),
        member=await _member_row(db, conversation.id, viewer.id),
        unread={},
    )


async def _find_direct(db: AsyncSession, a: uuid.UUID, b: uuid.UUID) -> Conversation | None:
    """The conversation whose members are exactly {a, b}.

    Matched by intersecting two membership rows rather than by loading every
    direct conversation and comparing sets in Python: the table grows with every
    DM the rider has ever opened, so the scan would degrade with use. The second
    join needs an alias — without one PostgreSQL is asked to name
    `conversation_members` twice in the same query.
    """
    partner = aliased(ConversationMember)
    res = await db.execute(
        select(Conversation)
        .join(
            ConversationMember,
            and_(
                ConversationMember.conversation_id == Conversation.id,
                ConversationMember.user_id == a,
            ),
        )
        .join(
            partner,
            and_(partner.conversation_id == Conversation.id, partner.user_id == b),
        )
        .where(
            Conversation.kind == ConversationKind.DIRECT,
            Conversation.team_id.is_(None),
        )
        .order_by(Conversation.created_at)
        .limit(1)
    )
    return res.scalar_one_or_none()


async def team_conversation(db: AsyncSession, viewer: User, team_id: uuid.UUID) -> dict:
    """The team's single channel, created on first open.

    Authorization is re-derived from the live `team_memberships` row, so a
    removed rider asking for the channel gets the same 404 as a stranger
    (ADR-14 §14).
    """
    team = (await db.execute(select(Team).where(Team.id == team_id))).scalar_one_or_none()
    if team is None:
        raise ChatError("CHAT_CONVERSATION_NOT_FOUND", "Conversation not found.", 404)
    if await _team_membership(db, team_id, viewer.id) is None:
        raise ChatError("CHAT_CONVERSATION_NOT_FOUND", "Conversation not found.", 404)

    return await _open_team_channel(db, viewer, team)


async def _team_channel_lock(db: AsyncSession, team_id: uuid.UUID) -> None:
    await db.execute(select(func.pg_advisory_xact_lock(func.hashtext(f"chatteam:{team_id}"))))


async def _open_team_channel(db: AsyncSession, viewer: User, team: Team) -> dict:
    await _team_channel_lock(db, team.id)
    res = await db.execute(
        select(Conversation).where(
            Conversation.kind == ConversationKind.TEAM,
            Conversation.team_id == team.id,
        )
    )
    row = res.scalar_one_or_none()
    if row is None:
        now = _now()
        row = Conversation(
            kind=ConversationKind.TEAM,
            team_id=team.id,
            created_by_user_id=viewer.id,
            created_at=now,
            next_seq=1,
        )
        db.add(row)
        await db.flush()
        db.add(ConversationMember(conversation_id=row.id, user_id=viewer.id, joined_at=now))
        try:
            await db.commit()
        except IntegrityError:
            # The partial unique index caught a concurrent creation.
            await db.rollback()
            row = (
                await db.execute(
                    select(Conversation).where(
                        Conversation.kind == ConversationKind.TEAM,
                        Conversation.team_id == team.id,
                    )
                )
            ).scalar_one()
            # Ensure the opener has a roster row even if they lost the race.
            if await _member_row(db, row.id, viewer.id) is None:
                db.add(
                    ConversationMember(
                        conversation_id=row.id,
                        user_id=viewer.id,
                        joined_at=_now(),
                    )
                )
                await db.commit()
        await db.refresh(row)
        _log("team_channel_opened", team_id=str(team.id), by=str(viewer.id))

    # Keep the roster current without letting it become the authority: every
    # read and write re-derives standing from team_memberships.
    await _sync_team_roster(db, row, team.id)
    return await _conversation_view(
        db,
        row,
        viewer,
        teams={team.id: team},
        identities={},
        member=await _member_row(db, row.id, viewer.id),
        unread={},
    )


async def _sync_team_roster(
    db: AsyncSession, conversation: Conversation, team_id: uuid.UUID
) -> None:
    """Add roster rows for members who joined since the channel opened.

    Best-effort: a channel remains fully usable for a rider who can already see
    it, because authorization does not depend on the roster for team channels.
    """
    res = await db.execute(select(TeamMembership.user_id).where(TeamMembership.team_id == team_id))
    wanted = set(res.scalars())
    res = await db.execute(
        select(ConversationMember.user_id).where(
            ConversationMember.conversation_id == conversation.id
        )
    )
    have = set(res.scalars())
    missing = wanted - have
    if not missing:
        return
    now = _now()
    for user_id in missing:
        db.add(ConversationMember(conversation_id=conversation.id, user_id=user_id, joined_at=now))
    try:
        await db.commit()
    except IntegrityError:  # pragma: no cover - roster is advisory
        await db.rollback()


# ---------------------------------------------------------------------------
# Messages
# ---------------------------------------------------------------------------


async def list_messages(
    db: AsyncSession,
    viewer: User,
    conversation_id: uuid.UUID,
    *,
    before_seq: int | None,
    limit: int,
) -> dict:
    conversation = await _conversation_row(db, conversation_id)
    await _require_participant(db, conversation, viewer)
    items, has_more, cursor = await _messages_page(
        db, conversation, viewer, before_seq=before_seq, limit=limit
    )
    return {"items": items, "has_more": has_more, "next_before_seq": cursor}


async def send_message(
    db: AsyncSession,
    viewer: User,
    conversation_id: uuid.UUID,
    body: str,
    client_message_id: uuid.UUID,
) -> tuple[dict, bool]:
    """Send, idempotently. Returns (message_view, duplicate).

    The idempotency check happens BEFORE the conversation lock: a retry of an
    already-stored message must return it even when the conversation has since
    been archived, because the rider's message WAS accepted (ADR-14 §7).
    """
    conversation = await _conversation_row(db, conversation_id)
    await _require_participant(db, conversation, viewer)

    existing = await db.execute(
        select(Message).where(
            Message.conversation_id == conversation_id,
            Message.sender_user_id == viewer.id,
            Message.client_message_id == client_message_id,
        )
    )
    prior = existing.scalar_one_or_none()
    if prior is not None:
        identities = await _identities(db, [prior.sender_user_id])
        return (
            _message_view(prior, identities=identities, viewer_id=viewer.id),
            True,
        )

    await _lock_conversation(db, conversation_id)
    await db.refresh(conversation)
    # Re-read live state under the lock so a membership removal or an archive
    # that committed while this request was queued is still honoured.
    await _assert_can_send(db, conversation, viewer)
    # Snapshot the conversation's identifying columns BEFORE the notification path
    # can commit and expire them. `kind` and `team_id` are immutable, so this
    # cannot go stale between here and the write below.
    conversation_kind = conversation.kind
    conversation_team_id = conversation.team_id

    row = Message(
        conversation_id=conversation_id,
        sender_user_id=viewer.id,
        body=body.strip(),
        message_type=MessageType.TEXT,
        client_message_id=client_message_id,
        seq=conversation.next_seq,
        created_at=_now(),
    )
    db.add(row)
    conversation.next_seq = conversation.next_seq + 1
    try:
        await db.commit()
    except IntegrityError:
        # Lost a race on either unique constraint: the winner is authoritative.
        await db.rollback()
        winner = await db.execute(
            select(Message).where(
                Message.conversation_id == conversation_id,
                Message.sender_user_id == viewer.id,
                Message.client_message_id == client_message_id,
            )
        )
        stored = winner.scalar_one_or_none()
        if stored is None:  # pragma: no cover - defensive
            raise ChatError("CHAT_CONFLICT", "Message conflict.", 409) from None
        identities = await _identities(db, [stored.sender_user_id])
        return (_message_view(stored, identities=identities, viewer_id=viewer.id), True)

    await db.refresh(row)
    identities = await _identities(db, [row.sender_user_id])
    _log(
        "message_sent",
        conversation_id=str(conversation_id),
        message_id=str(row.id),
        seq=row.seq,
    )
    # Build the response BEFORE emitting the notification. The notification path
    # commits, which expires every ORM object in this session — so any attribute
    # read afterwards (`row.seq`, `row.id`) becomes a lazy load outside a
    # greenlet, which is a 500 rather than a stale value. Computing the view first
    # is also honest: the response should not depend on a side effect running.
    view = _message_view(row, identities=identities, viewer_id=viewer.id)
    # AFTER the commit, and never before: a notification for a transaction that
    # rolled back would tell a rider about a message that does not exist.
    # Ids are passed explicitly rather than the ORM objects, so the notification
    # path cannot trigger a lazy load on an expired instance.
    await _emit_message_notification(
        db,
        conversation_id=conversation_id,
        kind=conversation_kind,
        team_id=conversation_team_id,
        message_id=row.id,
        sender_id=row.sender_user_id,
    )
    return view, False


async def _emit_message_notification(
    db: AsyncSession,
    *,
    conversation_id: uuid.UUID,
    kind: ConversationKind,
    team_id: uuid.UUID | None,
    message_id: uuid.UUID,
    sender_id: uuid.UUID,
) -> None:
    """Notify the recipients of a new message, honouring Phase 8.3 policy.

    The two conversation kinds take different paths, and the difference is the
    entire privacy story:

    * DIRECT — every other participant, minus anyone blocked either way. A block
      produces NO row at all, in both directions: a row that exists but is never
      pushed would still leak through the notification center, and suppressing
      only one direction would turn a missing notification into a block detector.
    * TEAM — current members of the TEAM, not the conversation roster. A removed
      rider keeps their roster row so history stays attributable, so the roster
      would keep notifying someone the team has expelled. A block does NOT apply
      here: the Phase 8.3 policy keeps a team channel open, and a push must not
      become a way around it.

    Takes plain ids rather than ORM objects on purpose: this runs after a commit
    that has expired the session, so touching an instance attribute here would
    be a lazy load outside a greenlet.

    Never raises: a notification failure must not fail a message send.
    """
    try:
        await notification_service.notify_chat_message(
            db,
            conversation_id=conversation_id,
            kind=kind,
            team_id=team_id,
            message_id=message_id,
            sender_id=sender_id,
        )
    except Exception as exc:  # noqa: BLE001 — notification must never break a send
        _log(
            "message_notification_failed",
            conversation_id=str(conversation_id),
            error_category=type(exc).__name__,
        )


async def edit_message(db: AsyncSession, viewer: User, message_id: uuid.UUID, body: str) -> dict:
    """Edit own message within the 15-minute window (ADR-14 §9).

    The window is enforced server-side. A client that computes it from its own
    clock would disagree with the server across device timezones.
    """
    row = await _load_message(db, message_id)
    conversation = await _conversation_row(db, row.conversation_id)
    await _require_participant(db, conversation, viewer)
    if row.sender_user_id != viewer.id:
        # Not the author's message: indistinguishable from someone else's.
        raise ChatError("CHAT_MESSAGE_NOT_FOUND", "Message not found.", 404)
    if row.deleted_at is not None:
        raise ChatError("CHAT_MESSAGE_DELETED", "Message not found.", 404)
    if row.created_at + timedelta(minutes=EDIT_WINDOW_MINUTES) <= _now():
        raise ChatError(
            "CHAT_EDIT_WINDOW_CLOSED",
            "The edit window for this message has closed.",
            409,
        )

    await _lock_conversation(db, row.conversation_id)
    await db.refresh(row)
    row.body = body.strip()
    row.edited_at = _now()
    await db.commit()
    await db.refresh(row)
    identities = await _identities(db, [row.sender_user_id])
    _log("message_edited", message_id=str(row.id))
    return _message_view(row, identities=identities, viewer_id=viewer.id)


async def delete_message(db: AsyncSession, viewer: User, message_id: uuid.UUID) -> dict:
    """Soft-delete own message. The row is never removed (ADR-14 §6)."""
    row = await _load_message(db, message_id)
    conversation = await _conversation_row(db, row.conversation_id)
    await _require_participant(db, conversation, viewer)
    if row.sender_user_id != viewer.id:
        raise ChatError("CHAT_MESSAGE_NOT_FOUND", "Message not found.", 404)

    await _lock_conversation(db, row.conversation_id)
    await db.refresh(row)
    if row.deleted_at is None:
        row.deleted_at = _now()
        await db.commit()
        await db.refresh(row)
    identities = await _identities(db, [row.sender_user_id])
    _log("message_deleted", message_id=str(row.id))
    return _message_view(row, identities=identities, viewer_id=viewer.id)


async def _load_message(db: AsyncSession, message_id: uuid.UUID) -> Message:
    res = await db.execute(select(Message).where(Message.id == message_id))
    row = res.scalar_one_or_none()
    if row is None:
        raise ChatError("CHAT_MESSAGE_NOT_FOUND", "Message not found.", 404)
    return row


async def mark_read(db: AsyncSession, viewer: User, conversation_id: uuid.UUID, seq: int) -> dict:
    """Advance the read high-water mark. Never moves backwards.

    The monotonicity is enforced by `GREATEST` inside the UPDATE rather than by
    a Python comparison. Two tabs, or a slow request racing a fast one, would
    otherwise let a stale reply re-open messages the rider has already read —
    and a read-modify-write in Python would only be safe while some lock
    happened to be held, which is a fragile thing for a privacy-adjacent
    guarantee to depend on.
    """
    conversation = await _conversation_row(db, conversation_id)
    await _require_participant(db, conversation, viewer)

    # Clamped so a client cannot claim to have read messages that do not exist.
    target = min(seq, conversation.next_seq - 1)
    result = await db.execute(
        select(ConversationMember)
        .where(
            ConversationMember.conversation_id == conversation_id,
            ConversationMember.user_id == viewer.id,
        )
        .with_for_update()
    )
    member = result.scalar_one()
    await db.execute(
        sa.update(ConversationMember)
        .where(ConversationMember.id == member.id)
        .values(last_read_seq=func.greatest(ConversationMember.last_read_seq, target))
    )
    await db.commit()
    current = (
        await db.execute(
            select(ConversationMember.last_read_seq).where(ConversationMember.id == member.id)
        )
    ).scalar_one()
    return {"conversation_id": conversation_id, "last_read_seq": current}

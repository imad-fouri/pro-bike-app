"""Phase 8.3 domain: conversations, members, messages (ADR-14).

Three tables serve both team channels and direct messages. A conversation's
`kind` plus a nullable `team_id` is the only difference between them, because
every other concern — authorization, ordering, idempotency, read state — is
identical. Separate `team_channels` + `direct_conversations` tables would
duplicate all of it.

Three load-bearing decisions:

1. ONE CHANNEL PER TEAM (ADR-14 §3). A partial unique index on `team_id` WHERE
   kind='team' makes a second channel for the same team impossible. Custom
   channels are deferred to the group-ride phase, which is where ride-specific
   conversations actually become necessary.

2. `seq` IS THE ORDER (ADR-14 §5). Messages are ordered and paged by
   `conversation_id, seq`, never by `created_at`, which concurrent sends can
   share. `conversations.next_seq` is the allocation counter; the advisory lock
   in `chat_service._lock_conversation` serializes allocation, and
   `UNIQUE(conversation_id, seq)` is the final arbiter.

3. AUTHORS ARE RETAINED (ADR-14 §4). `sender_user_id` has NO `ON DELETE
   CASCADE`, so message history is never cascade-deleted and remains
   attributable. `conversations.next_seq` likewise outlives any member.
"""

import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.user import _uuid, _values


class ConversationKind(str, enum.Enum):
    """`team` = the single channel of a team; `direct` = a rider pair."""

    TEAM = "team"
    DIRECT = "direct"


class MessageType(str, enum.Enum):
    """Only `text` and `system` ship in Phase 8.3.

    `ride`, `route`, `location`, `workout` and media types are deliberately
    absent: a rich message type is a rendering contract plus a payload schema,
    and adding the enum values now would create the appearance of support for
    location sharing, which ADR-12 §3 and ADR-14 §2 explicitly forbid treating
    as implicit.
    """

    TEXT = "text"
    SYSTEM = "system"


#: Soft-delete placeholder returned by the API. The row and its body stay on
#: the server; this string is what a client may render.
DELETED_PLACEHOLDER = "[deleted]"

#: Minutes after creation during which the author may still edit their message.
EDIT_WINDOW_MINUTES = 15


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    kind: Mapped[ConversationKind] = mapped_column(
        Enum(ConversationKind, name="conversation_kind", values_callable=_values),
        nullable=False,
    )
    # Set for kind='team', NULL for kind='direct'. Enforced by the CHECK below,
    # so a direct conversation can never be bound to a team and vice versa.
    team_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("teams.id", ondelete="CASCADE")
    )
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Allocation counter for message `seq`. A plain column rather than
    # MAX(seq)+1 so allocation is an O(1) update under the conversation lock.
    next_seq: Mapped[int] = mapped_column(BigInteger, default=1, nullable=False)

    __table_args__ = (
        # Exactly one channel per team. A channel taxonomy is deferred.
        Index(
            "uq_conversations_team_channel",
            "team_id",
            unique=True,
            postgresql_where=text("kind = 'team'"),
        ),
        Index("ix_conversations_team_id", "team_id"),
        Index("ix_conversations_created_at", "created_at"),
        CheckConstraint(
            "(kind = 'team' AND team_id IS NOT NULL) OR " "(kind = 'direct' AND team_id IS NULL)",
            name="ck_conversations_kind_team",
        ),
        CheckConstraint("next_seq >= 1", name="ck_conversations_next_seq"),
    )


class ConversationMember(Base):
    """One row per (conversation, user).

    For a DIRECT conversation this is the participant list and is the reason a
    DM cannot become a group chat by accident: adding a third member would
    require the conversation's own authorization story, which does not exist
    yet. For a TEAM conversation the rows are a roster snapshot; live team
    authorization is always re-derived from `team_memberships`.
    """

    __tablename__ = "conversation_members"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False
    )
    # RESTRICT, not CASCADE: deleting an account must not silently erase its
    # participation record while the messages it wrote stay behind (ADR-14 §4).
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    joined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # High-water mark for unread counts. A seq, not a timestamp, so it is immune
    # to clock skew and maps directly onto the ordering key. Never decreases.
    last_read_seq: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)

    __table_args__ = (
        UniqueConstraint("conversation_id", "user_id", name="uq_conversation_members_pair"),
        Index("ix_conversation_members_user_id", "user_id"),
        CheckConstraint("last_read_seq >= 0", name="ck_conversation_members_last_read_seq"),
    )


class Message(Base):
    """One message. Append-only; edit sets `edited_at`, delete sets `deleted_at`.

    Neither operation removes the row (ADR-14 §6). `sender_user_id` is RESTRICT
    so history is never cascade-deleted and every retained message stays
    attributable to its author.
    """

    __tablename__ = "messages"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False
    )
    sender_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    body: Mapped[str] = mapped_column(Text, nullable=False)
    message_type: Mapped[MessageType] = mapped_column(
        Enum(MessageType, name="message_type", values_callable=_values),
        default=MessageType.TEXT,
        nullable=False,
    )
    # Client-generated: this is what makes a retry after a network timeout
    # safe. Server-generated ids cannot survive the timeout they were meant to
    # cover (ADR-14 §7).
    client_message_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    seq: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    edited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        # Idempotency: the same client id in the same conversation by the same
        # sender is the same message, however many times it is retried.
        UniqueConstraint(
            "conversation_id",
            "sender_user_id",
            "client_message_id",
            name="uq_messages_client_id",
        ),
        # The ordering arbiter. Cursor pagination reads (conversation_id, seq).
        UniqueConstraint("conversation_id", "seq", name="uq_messages_conversation_seq"),
        Index("ix_messages_conversation_seq", "conversation_id", "seq"),
        Index("ix_messages_sender", "sender_user_id"),
        CheckConstraint("seq >= 1", name="ck_messages_seq"),
        CheckConstraint("length(body) > 0", name="ck_messages_body_nonempty"),
    )

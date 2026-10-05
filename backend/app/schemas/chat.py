"""Phase 8.3 chat API schemas (ADR-14).

Two output shapes: a conversation summary (what the inbox list needs) and a
message. Both are deliberately thin — a message carries ids, ordering, body,
timestamps and flags, and nothing else. No email, no phone, no coordinates, no
live location, no tokens (ADR-14 §2).

Message history uses a CURSOR envelope, not the project's usual
`items/total/page/page_size`. Offset pagination over an append-only table is
unstable while new messages arrive, and a `COUNT(*)` over full history on every
page load is an avoidable cost (ADR-14 §8).
"""

import uuid
from datetime import datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ConversationKind(str, Enum):
    TEAM = "team"
    DIRECT = "direct"
    #: Phase 9 (ADR-16 §5). A ride channel is a GROUP channel exactly like a
    #: team one: many members, no single peer. It is its own kind rather than a
    #: flavour of `team` because it binds to `group_ride_id` and authorizes from
    #: the ride roster, so a client cannot tell them apart by accident.
    GROUP_RIDE = "group_ride"


class MessageType(str, Enum):
    TEXT = "text"
    SYSTEM = "system"


class ConversationOut(BaseModel):
    """Inbox row. `unread_count` is derived from the viewer's `last_read_seq`."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    kind: ConversationKind
    team_id: uuid.UUID | None
    team_name: str | None = None
    team_handle: str | None = None
    team_visibility: str | None = None
    team_status: str | None = None
    #: Phase 9. Set for `group_ride` rows, null otherwise. Carried explicitly
    #: because the `team_*` fields are all null for a ride channel, so without
    #: this an inbox row has no way back to the ride it belongs to.
    group_ride_id: uuid.UUID | None = None
    #: Label for the inbox row. Not a copy the client owns: it is re-read live,
    #: so a renamed ride cannot leave a stale title in somebody's inbox.
    group_ride_title: str | None = None
    group_ride_status: str | None = None
    peer_user_id: uuid.UUID | None = None
    peer_username: str | None = None
    peer_display_name: str | None = None
    last_message_id: uuid.UUID | None = None
    last_message_seq: int | None = None
    last_message_preview: str | None = None
    last_message_at: datetime | None = None
    unread_count: int = 0
    last_read_seq: int = 0
    created_at: datetime


class ConversationPage(BaseModel):
    items: list[ConversationOut]
    total: int
    page: int
    page_size: int


class MessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    conversation_id: uuid.UUID
    seq: int
    sender_user_id: uuid.UUID
    sender_username: str | None = None
    sender_display_name: str | None = None
    message_type: MessageType
    #: Already `[deleted]` when `deleted_at` is set; the raw body never leaves
    #: the server through this field (ADR-14 §6).
    body: str
    #: True when the placeholder is being shown, so a client can style it
    #: without string-matching the placeholder.
    is_deleted: bool = False
    is_edited: bool = False
    #: True when the viewer sent it — the only per-viewer variation.
    is_mine: bool = False
    #: True while the viewer may still edit (ADR-14 §9).
    can_edit: bool = False
    created_at: datetime
    edited_at: datetime | None = None


class MessagePage(BaseModel):
    """Cursor envelope. Newest first; `next_before_seq` feeds the next page.

    No `total`: counting full history on every page is the wrong trade for a
    table that only grows.
    """

    items: list[MessageOut]
    has_more: bool
    next_before_seq: int | None


def _reject_blank(v: str) -> str:
    """Refuse a body that is whitespace only.

    `ck_messages_body_nonempty` would eventually catch it, but only as a
    constraint violation surfacing as a 500. Trimming is the service's job; this
    just rejects input that would trim to nothing, with a real 422.
    """
    if not v.strip():
        raise ValueError("Message body cannot be empty.")
    return v


class MessageCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    body: str = Field(min_length=1, max_length=4000)
    #: Client-generated UUID. Required, because a server-generated id cannot
    #: make a retry idempotent (ADR-14 §7).
    client_message_id: uuid.UUID

    _body_not_blank = field_validator("body")(_reject_blank)


class MessageUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    body: str = Field(min_length=1, max_length=4000)

    _body_not_blank = field_validator("body")(_reject_blank)


class SendResult(BaseModel):
    """Response of a send. `duplicate` distinguishes a retry from a new write."""

    model_config = ConfigDict(from_attributes=True)

    message: MessageOut
    duplicate: bool = False

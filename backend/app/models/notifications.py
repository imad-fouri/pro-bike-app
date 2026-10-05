"""Phase 8.4 domain: push devices and notifications (ADR-15).

Two tables. No `notification_preferences`: the project already keeps per-user
settings on `user_profiles`, and a third mechanism for one row per user would be
inconsistent with that. Detailed preferences are FUTURE (ADR-15 §4).

Four load-bearing decisions:

1. A DEVICE IS IDENTIFIED BY A CLIENT-GENERATED `device_id`, NOT BY ITS TOKEN.
   FCM and APNs both rotate tokens; using the token as the identity would insert
   a new row on every rotation and orphan the previous one forever. Rotation
   becomes an UPDATE instead. `UNIQUE(user_id, provider, device_id)` is what makes
   that safe under concurrency.

2. A TOKEN BELONGS TO EXACTLY ONE ACCOUNT (`UNIQUE(provider, token)`). Without
   this, a rider who signs out of A and into B on one phone would leave A's row
   live and A would keep pushing to a device now showing B's notifications.

3. IDEMPOTENCY IS A PARTIAL UNIQUE INDEX on `dedupe_key`. A plain unique would
   collide on repeated NULLs before PostgreSQL 15, so the `WHERE dedupe_key IS
   NOT NULL` predicate is what lets system notices coexist with deduplicated
   ones. The key is derived from an immutable business id, never a timestamp.

4. `actor_user_id` is RESTRICT, exactly like `messages.sender_user_id` in 0009.
   A notification must not vanish because its actor was removed, and account
   deletion/tombstone behaviour is deliberately undecided (ADR-15 §9).

The token is stored in plaintext by necessity — it must be PRESENTED to a
provider, so unlike `refresh_hash` it cannot be hashed. It is never logged, never
returned by any endpoint, and `redact()` blanks the column by name.
"""

import enum
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.user import _uuid, _values


class PushPlatform(str, enum.Enum):
    ANDROID = "android"
    IOS = "ios"


class PushProvider(str, enum.Enum):
    """The transport vendors Phase 8.4 does NOT implement.

    The enum names the eventual transport so a device row can record which
    ecosystem issued the token, while nothing in this phase ever contacts FCM or
    APNs (ADR-15 §3). FUTURE providers are added here; business services never
    reference this type.
    """

    FCM = "fcm"
    APNS = "apns"


class NotificationType(str, enum.Enum):
    """The notification taxonomy.

    Every value corresponds to an event that exists. The Phase 9 group-ride
    types arrived with ADR-16 §7, each with an authorization story in place
    first:

    * ``group_ride_invitation`` — the recipient is the INVITED roster row.
    * ``group_ride_accepted`` — the recipient is the ORGANIZER; it is the
      inverse direction of the invitation, which is why it is a separate type
      rather than the same one.
    * ``group_ride_started`` — recipients are the JOINED roster.

    Still deliberately absent, listed as markers for a later phase, NONE of
    which is emitted:

    * ``group_ride_reminder``, ``ride_starting`` (FUTURE — these are emitted by
      a scheduler, and Phase 9 ships no scheduler and no ride-clock job).
    * ``training_reminder`` (FUTURE — requires scheduled-workout automation).
    * ``ai_coach_event`` (FUTURE — representation only, no emission path).

    Adding a value to this enum is cheap; adding one that no authorization story
    exists for is not, which is why the remaining absences are decisions and not
    oversights.
    """

    FRIEND_REQUEST = "friend_request"
    FRIEND_REQUEST_ACCEPTED = "friend_request_accepted"
    TEAM_INVITATION = "team_invitation"
    TEAM_JOIN_REQUEST = "team_join_request"
    TEAM_MEMBER_REMOVED = "team_member_removed"
    TEAM_ARCHIVED = "team_archived"
    CHAT_MESSAGE = "chat_message"
    #: Distinct from CHAT_MESSAGE because its authorization is different: team
    #: channel recipients are live team members, whereas a DM recipient must
    #: also clear the block policy. Merging them would make the privacy rule
    #: unenforceable (ADR-15 §8).
    CHAT_MESSAGE_TEAM = "chat_message_team"
    #: A ride channel recipient must be a JOINED roster row, which is a third
    #: authorization basis again (ADR-16 §5).
    CHAT_MESSAGE_GROUP_RIDE = "chat_message_group_ride"
    GROUP_RIDE_INVITATION = "group_ride_invitation"
    GROUP_RIDE_ACCEPTED = "group_ride_accepted"
    GROUP_RIDE_STARTED = "group_ride_started"
    SYSTEM = "system"

    @property
    def l10n_key(self) -> str:
        """The stable localization key for this type.

        Keys are part of the storage contract — a notification row references
        its key, so renaming one would break rows already in a rider's history.
        """
        return f"notifications.type.{self.value}"


class PushDevice(Base):
    """One registered delivery target for one rider."""

    __tablename__ = "push_devices"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    # CASCADE: a deleted rider must leave zero live delivery targets.
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    platform: Mapped[PushPlatform] = mapped_column(
        Enum(PushPlatform, name="push_platform", values_callable=_values), nullable=False
    )
    provider: Mapped[PushProvider] = mapped_column(
        Enum(PushProvider, name="push_provider", values_callable=_values), nullable=False
    )
    device_id: Mapped[str] = mapped_column(String(128), nullable=False)
    token: Mapped[str] = mapped_column(Text, nullable=False)
    app_version: Mapped[str | None] = mapped_column(String(32))
    locale: Mapped[str | None] = mapped_column(String(8))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        UniqueConstraint("user_id", "provider", "device_id", name="uq_push_devices_user_device"),
        # A token is one device's address. Enforcing uniqueness across accounts
        # is what stops the previous rider receiving pushes after a re-login.
        UniqueConstraint("provider", "token", name="uq_push_devices_provider_token"),
        CheckConstraint("length(device_id) > 0", name="ck_push_devices_device_id"),
        CheckConstraint("length(token) > 0", name="ck_push_devices_token"),
        Index(
            "ix_push_devices_enabled_user", "user_id", "platform", postgresql_where=text("enabled")
        ),
    )

    def public_view(self) -> dict[str, Any]:
        """Device metadata for the owner.

        There is deliberately no `token` key here and no `asdict` call anywhere
        near this row: the token is a credential, and the only safe way to keep
        it out of a response is to never put it in one. `created_at` is omitted
        because nothing needs it and it narrows nothing.
        """
        return {
            "id": str(self.id),
            "platform": self.platform.value,
            "provider": self.provider.value,
            "device_id": self.device_id,
            "app_version": self.app_version,
            "locale": self.locale,
            "enabled": self.enabled,
            "last_seen_at": self.last_seen_at,
            "created_at": self.created_at,
        }


class Notification(Base):
    """One in-app notification for one recipient.

    Content is a localization KEY plus substitution parameters, never rendered
    copy: the client owns EN/FR/AR, and a server-rendered string can only ever be
    correct in one of them (ADR-15 §6).

    `params` holds an actor display name and possibly a team name — public social
    data already exposed by the chat and team surfaces. It must never hold a
    message body, a coordinate, an email, or a token; a push payload derived from
    this row carries the same restriction.
    """

    __tablename__ = "notifications"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    recipient_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    # RESTRICT — see the module docstring.
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT")
    )
    type: Mapped[NotificationType] = mapped_column(
        Enum(NotificationType, name="notification_type", values_callable=_values), nullable=False
    )
    entity_type: Mapped[str | None] = mapped_column(String(32))
    entity_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    l10n_key: Mapped[str] = mapped_column(String(64), nullable=False)
    params: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    deep_link: Mapped[str | None] = mapped_column(String(256))
    dedupe_key: Mapped[str | None] = mapped_column(String(160))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        # Partial: system notices carry no key and must not collide with each
        # other on NULL.
        Index(
            "uq_notifications_dedupe_key",
            "dedupe_key",
            unique=True,
            postgresql_where=text("dedupe_key IS NOT NULL"),
        ),
        Index("ix_notifications_recipient_created", "recipient_user_id", text("created_at DESC")),
        # Makes the unread count an index-only scan over unread rows.
        Index(
            "ix_notifications_recipient_unread",
            "recipient_user_id",
            postgresql_where=text("read_at IS NULL"),
        ),
        Index("ix_notifications_recipient_type", "recipient_user_id", "type"),
    )

    @property
    def is_unread(self) -> bool:
        return self.read_at is None

    def public_view(
        self,
        *,
        actor_username: str | None = None,
        actor_display_name: str | None = None,
        entity_name: str | None = None,
    ) -> dict[str, Any]:
        """The wire shape.

        `params` is echoed, which is safe precisely because it only ever holds
        public display strings — see the class docstring. A future field that
        would break that property must not be added here.
        """
        return {
            "id": str(self.id),
            "type": self.type.value,
            "entity_type": self.entity_type,
            "entity_id": str(self.entity_id) if self.entity_id else None,
            "l10n_key": self.l10n_key,
            "params": dict(self.params or {}),
            "deep_link": self.deep_link,
            "is_read": self.read_at is not None,
            "is_unread": self.read_at is None,
            "created_at": self.created_at,
            "read_at": self.read_at,
            "actor_user_id": str(self.actor_user_id) if self.actor_user_id else None,
            "actor_username": actor_username,
            "actor_display_name": actor_display_name,
            "entity_name": entity_name,
        }

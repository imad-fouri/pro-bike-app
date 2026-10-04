"""Phase 8.4 migration: push devices and notifications (ADR-15).

Two additive tables on top of `0009_chat`. No existing table is modified —
`users`, `social_profiles`, `teams`, `team_memberships`, `conversations`,
`conversation_members` and `messages` are untouched, so Phase 8.4 cannot regress
any earlier phase.

Deliberately NOT created here: a `notification_preferences` table. The project
already stores per-user settings on `user_profiles`; a third storage location for
a single row per user would be inconsistent with that convention, and nothing in
this phase needs per-type granularity (ADR-15 §4).

Four decisions are encoded here rather than left to application code:

* `push_devices` carries a `UNIQUE(user_id, provider, device_id)` so a re-login
  or a token rotation UPDATES one row instead of accumulating a dead row per app
  launch, and a `UNIQUE(provider, token)` so a token belongs to exactly one
  account. Without the second, a rider who signs out of account A and into B on
  one phone would leave A able to push to that phone (ADR-15 §5).
* `notifications.dedupe_key` is UNIQUE **partial** (`WHERE dedupe_key IS NOT
  NULL`). A plain unique would collide on repeated NULLs before PostgreSQL 15's
  `NULLS NOT DISTINCT`, so the predicate is what makes idempotency work without
  forcing every row to carry a key.
* `notifications.recipient_user_id` CASCADES: a deleted rider has no inbox.
* `notifications.actor_user_id` is RESTRICT, matching `messages.sender_user_id`
  in 0009 exactly, so a notification never vanishes because its actor was
  removed. Account deletion/tombstone behaviour is FUTURE and deliberately
  undecided (ADR-15 §9) — this phase must not foreclose it.

Enum creation follows the 0006/0007/0008/0009 precedent: `create_type=False`
plus an explicit `.create(checkfirst=True)` so concurrent migration runners
never race.
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0010_notifications"
down_revision = "0009_chat"

_platform = postgresql.ENUM("android", "ios", name="push_platform", create_type=False)
_provider = postgresql.ENUM("fcm", "apns", name="push_provider", create_type=False)
_type = postgresql.ENUM(
    "friend_request",
    "friend_request_accepted",
    "team_invitation",
    "team_join_request",
    "team_member_removed",
    "team_archived",
    "chat_message",
    "chat_message_team",
    "system",
    name="notification_type",
    create_type=False,
)


def upgrade() -> None:
    _platform.create(op.get_bind(), checkfirst=True)
    _provider.create(op.get_bind(), checkfirst=True)
    _type.create(op.get_bind(), checkfirst=True)

    # --- push_devices ---------------------------------------------------------
    op.create_table(
        "push_devices",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        # CASCADE: a deleted rider must leave zero live delivery targets behind.
        # Keeping a token would let a revoked account push to a device that can
        # no longer authenticate.
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("platform", _platform, nullable=False),
        sa.Column("provider", _provider, nullable=False),
        # Client-generated stable id. NOT the provider token: FCM and APNs both
        # rotate tokens, so using one as the identity would create a new row on
        # every rotation and orphan the old one forever (ADR-15 §5).
        sa.Column("device_id", sa.String(128), nullable=False),
        # Plaintext by necessity: the value must be PRESENTED to the provider, so
        # it cannot be hashed the way refresh_hash is. Never logged, never
        # returned by any endpoint, and redacted from logs by key name.
        sa.Column("token", sa.Text, nullable=False),
        sa.Column("app_version", sa.String(32)),
        # Used to pick a pre-rendered fallback string, because the OS renders
        # push text in a locale the server cannot otherwise observe.
        sa.Column("locale", sa.String(8)),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("last_seen_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("user_id", "provider", "device_id", name="uq_push_devices_user_device"),
        sa.UniqueConstraint("provider", "token", name="uq_push_devices_provider_token"),
        sa.CheckConstraint("length(device_id) > 0", name="ck_push_devices_device_id"),
        sa.CheckConstraint("length(token) > 0", name="ck_push_devices_token"),
    )
    op.create_index("ix_push_devices_user_id", "push_devices", ["user_id"])
    # Supports the fan-out query "every enabled device for these users" without
    # scanning disabled ones.
    op.create_index(
        "ix_push_devices_enabled_user",
        "push_devices",
        ["user_id", "platform"],
        postgresql_where=sa.text("enabled"),
    )

    # --- notifications --------------------------------------------------------
    op.create_table(
        "notifications",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "recipient_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # RESTRICT, matching messages.sender_user_id in 0009: a notification must
        # not disappear because its actor was removed. The actor is the one piece
        # of denormalized context here, and it is the piece that outlives the row.
        sa.Column(
            "actor_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
        ),
        sa.Column("type", _type, nullable=False),
        sa.Column("entity_type", sa.String(32)),
        sa.Column("entity_id", postgresql.UUID(as_uuid=True)),
        # A localization KEY, never rendered copy: a server-rendered string can
        # only ever be correct in one of EN/FR/AR. Stored as text rather than an
        # enum because the client owns the key vocabulary and must be able to
        # tolerate a key it does not know (ADR-15 §6).
        sa.Column("l10n_key", sa.String(64), nullable=False),
        # Substitution arguments only — an actor display name, a team name.
        # NEVER a message body, a coordinate, an email, or a token.
        sa.Column("params", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("deep_link", sa.String(256)),
        sa.Column("dedupe_key", sa.String(160)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("read_at", sa.DateTime(timezone=True)),
    )
    # Idempotency. Partial so that rows without a key (system notices, which are
    # never deduplicated) do not collide with each other on NULL.
    op.create_index(
        "uq_notifications_dedupe_key",
        "notifications",
        ["dedupe_key"],
        unique=True,
        postgresql_where=sa.text("dedupe_key IS NOT NULL"),
    )
    # Serves both the listing (newest first) and any per-recipient scan.
    op.create_index(
        "ix_notifications_recipient_created",
        "notifications",
        ["recipient_user_id", sa.text("created_at DESC")],
    )
    # The unread count, as an index-only scan over unread rows alone. Without
    # this the count degrades linearly with total history, and it is polled by
    # the app bar on every foreground (ADR-15 §7).
    op.create_index(
        "ix_notifications_recipient_unread",
        "notifications",
        ["recipient_user_id"],
        postgresql_where=sa.text("read_at IS NULL"),
    )
    op.create_index(
        "ix_notifications_recipient_type",
        "notifications",
        ["recipient_user_id", "type"],
    )


def downgrade() -> None:
    op.drop_table("notifications")
    op.drop_table("push_devices")
    _type.drop(op.get_bind(), checkfirst=True)
    _provider.drop(op.get_bind(), checkfirst=True)
    _platform.drop(op.get_bind(), checkfirst=True)

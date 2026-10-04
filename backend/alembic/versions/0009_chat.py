"""Phase 8.3 migration: conversations, conversation members, messages (ADR-14).

Three additive tables on top of `0008_teams`. No existing table is modified —
`users`, `social_profiles`, `teams`, `team_memberships` and every 8.1/8.2
constraint are untouched, so Phase 8.3 cannot regress Phase 8.1 or 8.2.

Two decisions are encoded here rather than left to application code:

* `messages.sender_user_id` and `conversation_members.user_id` are `ON DELETE
  RESTRICT`, not CASCADE. Message history is never cascade-deleted and every
  retained message stays attributable to its author (ADR-14 §4). The account
  deletion path is expected to tombstone the user instead.
* A partial unique index makes a second channel for one team impossible
  (ADR-14 §3). A channel taxonomy is deferred to the group-ride phase.

Enum creation follows the 0006/0007/0008 precedent: `create_type=False` plus an
explicit `.create(checkfirst=True)` so concurrent migration runners never race.
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0009_chat"
down_revision = "0008_teams"

_kind = postgresql.ENUM("team", "direct", name="conversation_kind", create_type=False)
_message_type = postgresql.ENUM("text", "system", name="message_type", create_type=False)


def upgrade() -> None:
    _kind.create(op.get_bind(), checkfirst=True)
    _message_type.create(op.get_bind(), checkfirst=True)

    # --- conversations -------------------------------------------------------
    op.create_table(
        "conversations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("kind", _kind, nullable=False),
        # CASCADE here is correct: a deleted TEAM takes its channel with it,
        # and a deleted team has no members left to read it (ADR-14 §10).
        sa.Column(
            "team_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("teams.id", ondelete="CASCADE"),
        ),
        sa.Column(
            "created_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("archived_at", sa.DateTime(timezone=True)),
        sa.Column("next_seq", sa.BigInteger, nullable=False, server_default="1"),
        sa.CheckConstraint(
            "(kind = 'team' AND team_id IS NOT NULL) OR " "(kind = 'direct' AND team_id IS NULL)",
            name="ck_conversations_kind_team",
        ),
        sa.CheckConstraint("next_seq >= 1", name="ck_conversations_next_seq"),
    )
    # Exactly one channel per team.
    op.create_index(
        "uq_conversations_team_channel",
        "conversations",
        ["team_id"],
        unique=True,
        postgresql_where=sa.text("kind = 'team'"),
    )
    op.create_index("ix_conversations_team_id", "conversations", ["team_id"])
    op.create_index("ix_conversations_created_at", "conversations", ["created_at"])

    # --- conversation_members -----------------------------------------------
    op.create_table(
        "conversation_members",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "conversation_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("conversations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # RESTRICT: an account deletion must not erase the participation record
        # while the messages it wrote remain.
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("joined_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_read_seq", sa.BigInteger, nullable=False, server_default="0"),
        sa.UniqueConstraint("conversation_id", "user_id", name="uq_conversation_members_pair"),
        sa.CheckConstraint("last_read_seq >= 0", name="ck_conversation_members_last_read_seq"),
    )
    op.create_index("ix_conversation_members_user_id", "conversation_members", ["user_id"])

    # --- messages ------------------------------------------------------------
    op.create_table(
        "messages",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "conversation_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("conversations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # RESTRICT: history is never cascade-deleted (ADR-14 §4).
        sa.Column(
            "sender_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("body", sa.Text, nullable=False),
        sa.Column("message_type", _message_type, nullable=False, server_default="text"),
        sa.Column("client_message_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("seq", sa.BigInteger, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("edited_at", sa.DateTime(timezone=True)),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
        # Idempotency: a retry of the same client id is the same message.
        sa.UniqueConstraint(
            "conversation_id",
            "sender_user_id",
            "client_message_id",
            name="uq_messages_client_id",
        ),
        # The ordering arbiter.
        sa.UniqueConstraint("conversation_id", "seq", name="uq_messages_conversation_seq"),
        sa.CheckConstraint("seq >= 1", name="ck_messages_seq"),
        sa.CheckConstraint("length(body) > 0", name="ck_messages_body_nonempty"),
    )
    # Cursor pagination reads exactly this index: WHERE conversation_id = ?
    # ORDER BY seq DESC.
    op.create_index("ix_messages_conversation_seq", "messages", ["conversation_id", "seq"])
    op.create_index("ix_messages_sender", "messages", ["sender_user_id"])


def downgrade() -> None:
    op.drop_table("messages")
    op.drop_table("conversation_members")
    op.drop_table("conversations")
    _message_type.drop(op.get_bind(), checkfirst=True)
    _kind.drop(op.get_bind(), checkfirst=True)

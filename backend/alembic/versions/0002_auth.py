"""Phase 2: users, profiles, refresh sessions, one-time tokens."""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0002_auth"
down_revision = "0001_foundation"

_user_status = postgresql.ENUM("active", "suspended", "deactivated", name="user_status")
_visibility = postgresql.ENUM("public", "friends", "private", name="visibility")
_measurement = postgresql.ENUM("metric", "imperial", name="measurement_system")


def upgrade() -> None:
    # Enum types are created implicitly by create_table (create_type default).
    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("password_hash", sa.Text, nullable=False),
        sa.Column("status", _user_status, nullable=False, server_default="active"),
        sa.Column("email_verified", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_login_at", sa.DateTime(timezone=True)),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_users_email", "users", ["email"], unique=True)

    op.create_table(
        "user_profiles",
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("display_name", sa.String(80), nullable=False),
        sa.Column("first_name", sa.String(80)),
        sa.Column("last_name", sa.String(80)),
        sa.Column("avatar_ref", sa.String(512)),
        sa.Column("country", sa.String(2)),
        sa.Column("city", sa.String(120)),
        sa.Column("preferred_language", sa.String(8), nullable=False, server_default="en"),
        sa.Column("timezone", sa.String(64), nullable=False, server_default="UTC"),
        sa.Column("measurement_system", _measurement, nullable=False, server_default="metric"),
        sa.Column("cycling_experience", sa.String(32)),
        sa.Column("disciplines", postgresql.ARRAY(sa.Text), nullable=False, server_default="{}"),
        sa.Column("training_goal", sa.Text),
        sa.Column("profile_visibility", _visibility, nullable=False, server_default="public"),
        sa.Column("activity_visibility", _visibility, nullable=False, server_default="friends"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "refresh_sessions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("family_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("refresh_hash", sa.String(64), nullable=False),
        sa.Column("device_label", sa.String(120)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column("replaced_by", postgresql.UUID(as_uuid=True)),
    )
    op.create_index("ix_refresh_sessions_user", "refresh_sessions", ["user_id"])
    op.create_index("ix_refresh_sessions_family", "refresh_sessions", ["family_id"])
    op.create_index("ix_refresh_sessions_hash", "refresh_sessions", ["refresh_hash"], unique=True)

    for table in ("password_reset_tokens", "email_verification_tokens"):
        op.create_table(
            table,
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column(
                "user_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("users.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("token_hash", sa.String(64), nullable=False),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("used_at", sa.DateTime(timezone=True)),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        )
        op.create_index(f"ix_{table}_user", table, ["user_id"])
        op.create_index(f"ix_{table}_hash", table, ["token_hash"], unique=True)


def downgrade() -> None:
    for table in (
        "email_verification_tokens",
        "password_reset_tokens",
        "refresh_sessions",
        "user_profiles",
        "users",
    ):
        op.drop_table(table)
    _measurement.drop(op.get_bind(), checkfirst=True)
    _visibility.drop(op.get_bind(), checkfirst=True)
    _user_status.drop(op.get_bind(), checkfirst=True)

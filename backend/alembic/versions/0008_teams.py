"""Phase 8.2 migration: teams, memberships, join requests, invitations (ADR-13).

Additive only. Four new tables; no existing table is altered, so 8.1 social
rows and 8.2 team rows cannot interfere.

create_type=False on every enum, then an explicit `.create(checkfirst=True)`,
following the 0006/0007 precedent: concurrent migration runners must not race
on CREATE TYPE.

Two partial unique indexes carry load-bearing invariants that no CHECK
constraint can express:
  * exactly one OWNER membership per team
  * at most one PENDING invitation per (team, invitee), while allowing a team
    to re-invite someone who previously declined
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0008_teams"
down_revision = "0007_social"

_visibility = postgresql.ENUM("public", "private", name="team_visibility", create_type=False)
_status = postgresql.ENUM("active", "archived", name="team_status", create_type=False)
_role = postgresql.ENUM("owner", "admin", "member", name="team_role", create_type=False)
_membership_status = postgresql.ENUM(
    "active", name="team_membership_status", create_type=False
)
_invitation_status = postgresql.ENUM(
    "pending",
    "accepted",
    "declined",
    "revoked",
    name="team_invitation_status",
    create_type=False,
)


def upgrade() -> None:
    _visibility.create(op.get_bind(), checkfirst=True)
    _status.create(op.get_bind(), checkfirst=True)
    _role.create(op.get_bind(), checkfirst=True)
    _membership_status.create(op.get_bind(), checkfirst=True)
    _invitation_status.create(op.get_bind(), checkfirst=True)

    # --- teams ---------------------------------------------------------------
    op.create_table(
        "teams",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "owner_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(80), nullable=False),
        sa.Column("handle", sa.String(30), unique=True),
        sa.Column("description", sa.String(500)),
        sa.Column("avatar_url", sa.String(512)),
        sa.Column("category", sa.String(32)),
        sa.Column(
            "visibility",
            _visibility,
            nullable=False,
            server_default="public",
        ),
        sa.Column("status", _status, nullable=False, server_default="active"),
        # Denormalized; maintained inside the membership transaction.
        sa.Column("member_count", sa.Integer, nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("archived_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("member_count >= 0", name="ck_teams_member_count"),
    )
    op.create_index("ix_teams_owner_user_id", "teams", ["owner_user_id"])
    op.create_index("ix_teams_visibility_status", "teams", ["visibility", "status"])
    op.create_index("ix_teams_name", "teams", ["name"])

    # --- team_memberships ----------------------------------------------------
    op.create_table(
        "team_memberships",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "team_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("teams.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("role", _role, nullable=False, server_default="member"),
        sa.Column(
            "status", _membership_status, nullable=False, server_default="active"
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("team_id", "user_id", name="uq_team_memberships_pair"),
    )
    # Exactly one OWNER per team.
    op.create_index(
        "uq_team_memberships_single_owner",
        "team_memberships",
        ["team_id"],
        unique=True,
        postgresql_where=sa.text("role = 'owner'"),
    )
    op.create_index("ix_team_memberships_team_id", "team_memberships", ["team_id"])
    op.create_index("ix_team_memberships_user_id", "team_memberships", ["user_id"])

    # --- team_join_requests --------------------------------------------------
    op.create_table(
        "team_join_requests",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "team_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("teams.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("message", sa.String(280)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("team_id", "user_id", name="uq_team_join_requests_pair"),
    )
    op.create_index(
        "ix_team_join_requests_team_id", "team_join_requests", ["team_id"]
    )
    op.create_index(
        "ix_team_join_requests_user_id", "team_join_requests", ["user_id"]
    )

    # --- team_invitations ----------------------------------------------------
    op.create_table(
        "team_invitations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "team_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("teams.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "invited_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "invited_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "status", _invitation_status, nullable=False, server_default="pending"
        ),
        sa.Column("message", sa.String(280)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("responded_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "invited_user_id != invited_by_user_id", name="ck_team_invitations_no_self"
        ),
    )
    # At most one PENDING invitation per (team, invitee). A decline frees the
    # pair, so a team may re-invite someone who previously said no.
    op.create_index(
        "uq_team_invitations_pending_pair",
        "team_invitations",
        ["team_id", "invited_user_id"],
        unique=True,
        postgresql_where=sa.text("status = 'pending'"),
    )
    op.create_index("ix_team_invitations_team_id", "team_invitations", ["team_id"])
    op.create_index(
        "ix_team_invitations_invited_user_id", "team_invitations", ["invited_user_id"]
    )
    op.create_index(
        "ix_team_invitations_invitee_status",
        "team_invitations",
        ["invited_user_id", "status"],
    )


def downgrade() -> None:
    op.drop_table("team_invitations")
    op.drop_table("team_join_requests")
    op.drop_table("team_memberships")
    op.drop_table("teams")
    _invitation_status.drop(op.get_bind(), checkfirst=True)
    _membership_status.drop(op.get_bind(), checkfirst=True)
    _role.drop(op.get_bind(), checkfirst=True)
    _status.drop(op.get_bind(), checkfirst=True)
    _visibility.drop(op.get_bind(), checkfirst=True)
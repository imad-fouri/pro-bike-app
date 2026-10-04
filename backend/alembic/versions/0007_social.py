"""Phase 8.1: social identity, friendships, blocks (ADR-12).

Additive only. Three tables, no changes to existing ones:

- `social_profiles`: public identity projection (username canonical unique,
  display name, bio, avatar URL, cycling category, country/city labels, and
  the three privacy settings). No coordinates anywhere by design.
- `friend_relationships`: one canonical row per unordered pair
  (`user_a_id < user_b_id`, DB-enforced), PENDING/ACCEPTED only.
- `user_blocks`: directional block pairs; blocking annihilates relationship
  rows in application transactions, enforced unique here.
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0007_social"
down_revision = "0006_training_foundation"

# create_type=False: created/dropped explicitly below so concurrent migration
# runners never race on CREATE TYPE (0006 precedent).
_friend_requests_policy = postgresql.ENUM(
    "everyone", "nobody", name="friend_requests_policy", create_type=False
)
_search_visibility = postgresql.ENUM(
    "discoverable", "hidden", name="search_visibility", create_type=False
)
_relationship_status = postgresql.ENUM(
    "pending", "accepted", name="relationship_status", create_type=False
)


def upgrade() -> None:
    _friend_requests_policy.create(op.get_bind(), checkfirst=True)
    _search_visibility.create(op.get_bind(), checkfirst=True)
    _relationship_status.create(op.get_bind(), checkfirst=True)

    # --- social_profiles (1:1 public projection) -----------------------------
    op.create_table(
        "social_profiles",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            unique=True,
            nullable=False,
        ),
        sa.Column("username", sa.String(30), unique=True),
        sa.Column("display_name", sa.String(80), nullable=False),
        sa.Column("bio", sa.String(500)),
        sa.Column("avatar_url", sa.String(512)),
        sa.Column("cycling_category", sa.String(32)),
        sa.Column("country_code", sa.String(2)),
        sa.Column("city", sa.String(120)),
        sa.Column("profile_visibility", sa.String(16), nullable=False, server_default="public"),
        sa.Column(
            "allow_friend_requests",
            _friend_requests_policy,
            nullable=False,
            server_default="everyone",
        ),
        sa.Column(
            "search_visibility", _search_visibility, nullable=False, server_default="discoverable"
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "profile_visibility IN ('public', 'friends', 'private')",
            name="ck_social_profiles_visibility",
        ),
    )
    op.create_index("ix_social_profiles_username", "social_profiles", ["username"])
    op.create_index("ix_social_profiles_user_id", "social_profiles", ["user_id"])

    # --- friend_relationships (canonical unordered pair) ---------------------
    op.create_table(
        "friend_relationships",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_a_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_b_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "requested_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("status", _relationship_status, nullable=False, server_default="pending"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("user_a_id", "user_b_id", name="uq_friend_relationships_pair"),
        sa.CheckConstraint("user_a_id != user_b_id", name="ck_friend_relationships_no_self"),
        sa.CheckConstraint("user_a_id < user_b_id", name="ck_friend_relationships_canonical"),
    )
    op.create_index(
        "ix_friend_relationships_user_a_status",
        "friend_relationships",
        ["user_a_id", "status"],
    )
    op.create_index(
        "ix_friend_relationships_user_b_status",
        "friend_relationships",
        ["user_b_id", "status"],
    )
    op.create_index(
        "ix_friend_relationships_requested_by",
        "friend_relationships",
        ["requested_by_user_id", "status"],
    )

    # --- user_blocks (directional) -------------------------------------------
    op.create_table(
        "user_blocks",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "blocker_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "blocked_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("blocker_user_id", "blocked_user_id", name="uq_user_blocks_pair"),
        sa.CheckConstraint("blocker_user_id != blocked_user_id", name="ck_user_blocks_no_self"),
    )
    op.create_index("ix_user_blocks_blocker", "user_blocks", ["blocker_user_id"])
    op.create_index("ix_user_blocks_blocked", "user_blocks", ["blocked_user_id"])


def downgrade() -> None:
    op.drop_table("user_blocks")
    op.drop_table("friend_relationships")
    op.drop_table("social_profiles")
    _relationship_status.drop(op.get_bind(), checkfirst=True)
    _search_visibility.drop(op.get_bind(), checkfirst=True)
    _friend_requests_policy.drop(op.get_bind(), checkfirst=True)

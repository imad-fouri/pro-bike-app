"""Phase 10 WS-RC migration: ranking and challenge foundation.

Four additive tables on top of ``0012_subscriptions``. No existing table is
modified, so WS-RC cannot regress Phases 1-10 by schema change, and no column
of ``users``, ``user_profiles``, ``rides``, ``social_profiles`` or the team
tables is touched.

Why these four and no more
--------------------------
``challenges``              what is competed for: metric, target, window,
                            scope, visibility, lifecycle.
``challenge_memberships``   who is in it, their participant state, and their
                            progress. Progress is a column family here rather
                            than a fifth table: one row per
                            (challenge, participant) already exists, and a 1:1
                            table would add a join with no lifecycle of its
                            own.
``challenge_progress_events`` the idempotency ledger. The UNIQUE constraint,
                            not an application-level existence check, is what
                            makes a duplicated ride or a retried worker a
                            no-op.
``challenge_completions``   the award record. UNIQUE(challenge_id, user_id)
                            is the "points are granted once" guarantee.

Deliberately absent:

* No leaderboard/leaderboard-entry table. Rankings are computed from ``rides``
  (and ``challenge_completions`` for the points metric) at read time with SQL
  aggregation, so a friendship change or a privacy change can never leave a
  stale materialised row behind. Materialisation is a documented extension
  point for when the read cost demands it (docs/ranking-challenges.md 11).
* No score, no rank, no client-supplied progress column anywhere. Nothing in
  these tables can be written by a request body.
* No coordinate, no location, no country or city copy. Ranking geography comes
  from the existing ``user_profiles`` at read time; nothing here can leak a
  position.
* No notification type. Postgres cannot DROP an enum value, so adding one
  would not be downgrade-safe; the challenge event seam is code-only in this
  workstream (see ``challenge_service``).
* ``participant_state`` reserves ``disqualified`` for WS-AC. It is never
  written here; anti-cheat/integrity evaluation is intentionally deferred.

Idempotency is storage, not comment:

* ``uq_challenge_memberships_pair``   one membership per participant.
* ``uq_challenge_progress_challenge_ride`` one application per (challenge,
  ride), which is what makes repeated ride finalisation a no-op.
* ``uq_challenge_completions_pair``   one award per participant.
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0013_rankings_challenges"
down_revision = "0012_subscriptions"

_metric = postgresql.ENUM(
    "distance", "elevation", "rides", "training", "streak", name="challenge_metric"
)
_scope = postgresql.ENUM(
    "individual", "friends", "team", "global", name="challenge_scope"
)
_visibility = postgresql.ENUM("public", "private", name="challenge_visibility")
_status = postgresql.ENUM(
    "draft", "scheduled", "active", "completed", "cancelled", name="challenge_status"
)
_participant_state = postgresql.ENUM(
    "joined", "active", "completed", "left", "disqualified", name="participant_state"
)


def upgrade() -> None:
    # The enum types are NOT created explicitly here: each is attached to a
    # column below, and ``op.create_table`` creates its column types as part of
    # the statement. Pre-creating them would make the table event try to create
    # them a second time, which raises "type ... already exists", so the
    # ownership is left to the table create. Downgrade drops them explicitly.
    # --- challenges ---------------------------------------------------------
    op.create_table(
        "challenges",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "creator_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "team_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("teams.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("title", sa.String(120), nullable=False),
        sa.Column("description", sa.String(1000)),
        sa.Column("metric", _metric, nullable=False),
        sa.Column("target", postgresql.NUMERIC(14, 2), nullable=False),
        sa.Column("points", sa.Integer, nullable=False),
        sa.Column("scope", _scope, nullable=False),
        sa.Column("visibility", _visibility, nullable=False),
        sa.Column("status", _status, nullable=False),
        sa.Column("start_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("end_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True)),
        sa.Column("finalized_at", sa.DateTime(timezone=True)),
        sa.Column("cancelled_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("end_at > start_at", name="ck_challenges_window"),
        sa.CheckConstraint("target > 0", name="ck_challenges_target"),
        sa.CheckConstraint("points >= 10 AND points <= 1000", name="ck_challenges_points"),
        sa.CheckConstraint(
            "end_at - start_at <= interval '366 days'", name="ck_challenges_span"
        ),
        sa.CheckConstraint(
            "(scope = 'team' AND team_id IS NOT NULL) OR (scope <> 'team' AND team_id IS NULL)",
            name="ck_challenges_team_scope",
        ),
    )
    op.create_index("ix_challenges_creator", "challenges", ["creator_user_id"])
    op.create_index("ix_challenges_status_end", "challenges", ["status", "end_at"])
    op.create_index(
        "ix_challenges_scope_status", "challenges", ["scope", "status", "end_at"]
    )
    op.create_index("ix_challenges_team", "challenges", ["team_id"])

    # --- challenge_memberships ---------------------------------------------
    op.create_table(
        "challenge_memberships",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "challenge_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("challenges.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("state", _participant_state, nullable=False),
        sa.Column("progress_value", postgresql.NUMERIC(14, 2), nullable=False),
        sa.Column("progress_rides", sa.Integer, nullable=False),
        sa.Column("joined_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("left_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("progress_value >= 0", name="ck_challenge_memberships_progress"),
        sa.CheckConstraint("progress_rides >= 0", name="ck_challenge_memberships_rides"),
        sa.UniqueConstraint("challenge_id", "user_id", name="uq_challenge_memberships_pair"),
    )
    op.create_index(
        "ix_challenge_memberships_user", "challenge_memberships", ["user_id", "challenge_id"]
    )
    op.create_index(
        "ix_challenge_memberships_challenge_state",
        "challenge_memberships",
        ["challenge_id", "state"],
    )

    # --- challenge_progress_events -----------------------------------------
    op.create_table(
        "challenge_progress_events",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column(
            "challenge_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("challenges.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "ride_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("rides.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "challenge_id", "ride_id", name="uq_challenge_progress_challenge_ride"
        ),
    )
    op.create_index(
        "ix_challenge_progress_member",
        "challenge_progress_events",
        ["challenge_id", "user_id"],
    )
    op.create_index("ix_challenge_progress_ride", "challenge_progress_events", ["ride_id"])

    # --- challenge_completions ---------------------------------------------
    op.create_table(
        "challenge_completions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "challenge_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("challenges.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("points_awarded", sa.Integer, nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "source_ride_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("rides.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.CheckConstraint("points_awarded > 0", name="ck_challenge_completions_points"),
        sa.UniqueConstraint("challenge_id", "user_id", name="uq_challenge_completions_pair"),
    )
    op.create_index(
        "ix_challenge_completions_user_time",
        "challenge_completions",
        ["user_id", "completed_at"],
    )
    op.create_index(
        "ix_challenge_completions_challenge", "challenge_completions", ["challenge_id"]
    )


def downgrade() -> None:
    op.drop_table("challenge_completions")
    op.drop_table("challenge_progress_events")
    op.drop_table("challenge_memberships")
    op.drop_table("challenges")
    _participant_state.drop(op.get_bind(), checkfirst=True)
    _status.drop(op.get_bind(), checkfirst=True)
    _visibility.drop(op.get_bind(), checkfirst=True)
    _scope.drop(op.get_bind(), checkfirst=True)
    _metric.drop(op.get_bind(), checkfirst=True)

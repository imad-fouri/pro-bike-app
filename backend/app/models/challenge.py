"""Phase 10 WS-RC domain: challenges, participants, progress, completions.

Ranking and Challenges are FIRST-CLASS CORE CycleCoach features, not a social
addon and not monetization: every capability here is available to every
authenticated rider. Nothing in this module reads a plan or an entitlement.

Shape (one challenge, one participant, one ledger, one award):

    challenges                     what is being competed for
      challenge_memberships        who is in it, their state, their progress
        challenge_progress_events  which rides have been applied (idempotency)
      challenge_completions        who finished, and how many points (once)

`challenge_progress` is deliberately a *column family* on
`challenge_memberships` rather than a fifth table: one row per
(challenge, participant) already exists, and a 1:1 table would add a join with
no independent lifecycle of its own.

CHALLENGE TYPE vs METRIC
------------------------
The product brief lists "challenge type" and "metric" as separate concepts.
In this design they are the same axis, and that is deliberate: a challenge's
type *is* the metric it targets (``challenges.metric``), and the "time-period
challenge" concept is expressed by ``start_at``/``end_at`` - every challenge
is time-period bounded. There is deliberately no free-form formula field a
client could populate; the metric vocabulary below is the whole taxonomy.

Units (server canonical, documented in docs/ranking-challenges.md):
    distance  metres        elevation  metres
    rides     rides         training   analysed rides
    streak    distinct consecutive UTC days
"""

import enum
import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import NUMERIC, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.user import _uuid, _values

#: Longest challenge a client may create. Bounds the streak recomputation and
#: keeps a "challenge" a season rather than a decade.
MAX_CHALLENGE_DAYS = 366

#: Points are an integer, server-clamped quantity - never a client formula.
MIN_CHALLENGE_POINTS = 10
MAX_CHALLENGE_POINTS = 1000
DEFAULT_CHALLENGE_POINTS = 100


class ChallengeMetric(str, enum.Enum):
    """The whole challenge-type taxonomy. Adding a member is a schema change,
    which is the point: there is no unbounded client-defined formula."""

    DISTANCE = "distance"
    ELEVATION = "elevation"
    RIDES = "rides"
    TRAINING = "training"
    STREAK = "streak"


class ChallengeScope(str, enum.Enum):
    """Who a challenge is *for* - drives both listing and join eligibility."""

    INDIVIDUAL = "individual"
    FRIENDS = "friends"
    TEAM = "team"
    GLOBAL = "global"


class ChallengeVisibility(str, enum.Enum):
    """`public` is listed inside its scope; `private` is unlisted and its
    roster is shown only to people already in it (docs/ranking-challenges.md
    7)."""

    PUBLIC = "public"
    PRIVATE = "private"


class ChallengeStatus(str, enum.Enum):
    """Stored lifecycle. `scheduled`/`active` are the two processing states;
    `expired` is DERIVED (see ``derive_state``) and is never stored - it means
    the window has passed but the challenge has not been finalised yet."""

    DRAFT = "draft"
    SCHEDULED = "scheduled"
    ACTIVE = "active"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class ParticipantState(str, enum.Enum):
    """Participant lifecycle, deliberately separate from challenge lifecycle.
    `disqualified` is reserved for WS-AC integrity work and is never written
    by this workstream."""

    JOINED = "joined"
    ACTIVE = "active"
    COMPLETED = "completed"
    LEFT = "left"
    DISQUALIFIED = "disqualified"


class Challenge(Base):
    __tablename__ = "challenges"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    creator_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    # Set only when scope=team. Written by the service, never trusted from a
    # body without an authorisation check against the caller's membership.
    team_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("teams.id", ondelete="SET NULL"), nullable=True
    )
    title: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(String(1000))
    metric: Mapped[ChallengeMetric] = mapped_column(
        Enum(ChallengeMetric, name="challenge_metric", values_callable=_values),
        nullable=False,
    )
    target: Mapped[Decimal] = mapped_column(NUMERIC(14, 2), nullable=False)
    points: Mapped[int] = mapped_column(Integer, nullable=False, default=DEFAULT_CHALLENGE_POINTS)
    scope: Mapped[ChallengeScope] = mapped_column(
        Enum(ChallengeScope, name="challenge_scope", values_callable=_values),
        nullable=False,
    )
    visibility: Mapped[ChallengeVisibility] = mapped_column(
        Enum(ChallengeVisibility, name="challenge_visibility", values_callable=_values),
        default=ChallengeVisibility.PUBLIC,
        nullable=False,
    )
    status: Mapped[ChallengeStatus] = mapped_column(
        Enum(ChallengeStatus, name="challenge_status", values_callable=_values),
        default=ChallengeStatus.DRAFT,
        nullable=False,
    )
    start_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    end_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finalized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint("end_at > start_at", name="ck_challenges_window"),
        CheckConstraint("target > 0", name="ck_challenges_target"),
        CheckConstraint(
            f"points >= {MIN_CHALLENGE_POINTS} AND points <= {MAX_CHALLENGE_POINTS}",
            name="ck_challenges_points",
        ),
        # Bounds the streak recomputation to something a request can do.
        CheckConstraint("end_at - start_at <= interval '366 days'", name="ck_challenges_span"),
        CheckConstraint(
            "(scope = 'team' AND team_id IS NOT NULL) OR (scope <> 'team' AND team_id IS NULL)",
            name="ck_challenges_team_scope",
        ),
        Index("ix_challenges_creator", "creator_user_id"),
        Index("ix_challenges_status_end", "status", "end_at"),
        Index("ix_challenges_scope_status", "scope", "status", "end_at"),
        Index("ix_challenges_team", "team_id"),
    )


class ChallengeMembership(Base):
    """One row per (challenge, participant). Progress lives here: it is the
    same lifecycle as the membership, and a separate 1:1 table would only add
    a join."""

    __tablename__ = "challenge_memberships"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    challenge_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("challenges.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    state: Mapped[ParticipantState] = mapped_column(
        Enum(ParticipantState, name="participant_state", values_callable=_values),
        default=ParticipantState.JOINED,
        nullable=False,
    )
    progress_value: Mapped[Decimal] = mapped_column(
        NUMERIC(14, 2), default=Decimal(0), nullable=False
    )
    progress_rides: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    joined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    left_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        UniqueConstraint("challenge_id", "user_id", name="uq_challenge_memberships_pair"),
        CheckConstraint("progress_value >= 0", name="ck_challenge_memberships_progress"),
        CheckConstraint("progress_rides >= 0", name="ck_challenge_memberships_rides"),
        Index("ix_challenge_memberships_user", "user_id", "challenge_id"),
        Index("ix_challenge_memberships_challenge_state", "challenge_id", "state"),
    )


class ChallengeProgressEvent(Base):
    """The idempotency ledger: one row per (challenge, ride) ever applied.

    The UNIQUE constraint - not an application-level "if not exists" - is what
    makes a duplicated ride, a retried worker, or a concurrent second attempt
    a no-op. Progress itself is recomputed from the ride table rather than
    incremented, so even a crash between "recorded" and "applied" repairs
    itself on the next run."""

    __tablename__ = "challenge_progress_events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    challenge_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("challenges.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    ride_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("rides.id", ondelete="CASCADE"), nullable=False
    )
    applied_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        UniqueConstraint("challenge_id", "ride_id", name="uq_challenge_progress_challenge_ride"),
        Index("ix_challenge_progress_member", "challenge_id", "user_id"),
        Index("ix_challenge_progress_ride", "ride_id"),
    )


class ChallengeCompletion(Base):
    """Who finished a challenge, and the points they were awarded - once.

    The UNIQUE(challenge_id, user_id) constraint is the "award points once"
    guarantee. `points_awarded` is a snapshot of the challenge's points at
    completion time, so an award can never be rewritten by a later edit."""

    __tablename__ = "challenge_completions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    challenge_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("challenges.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    points_awarded: Mapped[int] = mapped_column(Integer, nullable=False)
    completed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source_ride_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("rides.id", ondelete="SET NULL"), nullable=True
    )

    __table_args__ = (
        UniqueConstraint("challenge_id", "user_id", name="uq_challenge_completions_pair"),
        CheckConstraint("points_awarded > 0", name="ck_challenge_completions_points"),
        Index("ix_challenge_completions_user_time", "user_id", "completed_at"),
        Index("ix_challenge_completions_challenge", "challenge_id"),
    )

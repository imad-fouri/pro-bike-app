"""Phase 8.2 domain: teams, memberships, join requests, invitations (ADR-13).

A TEAM is a separate social entity from a personal friendship (ADR-12). The two
never touch each other's rows:

    A <--friendship--> B        (friend_relationships, Phase 8.1)
    A --member--> Team X        (team_memberships, here)
    B --member--> Team X

Nothing in this module reads or writes `friend_relationships`. Leaving a team
does not unfriend anyone; blocking someone does not evict them from a team. The
one coupling is a *refusal to start* a new association while a block exists
(`TeamError` TEAM_BLOCKED), which is checked at action time and never applied as
a cascade.

Design notes:

- `teams.owner_user_id` and the OWNER membership are both stored. The column
  makes "is this my team?" an index-only lookup and keeps ownership meaningful
  even if membership rows are audited; the membership row makes role checks
  uniform. They are written in ONE transaction, and a partial unique index
  guarantees exactly one OWNER row per team.
- Join requests are separate from invitations because their permissions are
  opposite: an applicant asks to be let in, an inviter offers entry. Merging
  them would force one awkward union of two authorization models.
- Join requests are DELETED on accept/reject (same reasoning as 8.1 friend
  requests: a rejected request is not a fact worth storing). Invitations are
  RETAINED because the recipient needs a durable inbox and both sides need a
  record of what was offered.
"""

import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.user import _uuid, _values


class TeamVisibility(str, enum.Enum):
    """Whether a team is discoverable and joinable without approval."""

    PUBLIC = "public"
    PRIVATE = "private"


class TeamStatus(str, enum.Enum):
    ACTIVE = "active"
    ARCHIVED = "archived"


class TeamRole(str, enum.Enum):
    """Ordered by authority. Owner transfer is out of scope (Phase 8.3)."""

    OWNER = "owner"
    ADMIN = "admin"
    MEMBER = "member"


class TeamMembershipStatus(str, enum.Enum):
    ACTIVE = "active"


class TeamInvitationStatus(str, enum.Enum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    DECLINED = "declined"
    REVOKED = "revoked"


#: Roles permitted to manage members, requests, and invitations.
MANAGER_ROLES = (TeamRole.OWNER, TeamRole.ADMIN)


class Team(Base):
    __tablename__ = "teams"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    # The creator. Always also holds the OWNER membership row; both are written
    # in one transaction (see team_service.create_team).
    owner_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    # Canonical lowercase handle, UNIQUE across all teams. NULL = unclaimed.
    # Same shape as social_profiles.username (ADR-12 §2.1).
    handle: Mapped[str | None] = mapped_column(String(30), unique=True)
    description: Mapped[str | None] = mapped_column(String(500))
    avatar_url: Mapped[str | None] = mapped_column(String(512))
    # Free-text label mirroring social_profiles.cycling_category; NOT a new enum.
    category: Mapped[str | None] = mapped_column(String(32))
    visibility: Mapped[TeamVisibility] = mapped_column(
        Enum(TeamVisibility, name="team_visibility", values_callable=_values),
        default=TeamVisibility.PUBLIC,
        nullable=False,
    )
    status: Mapped[TeamStatus] = mapped_column(
        Enum(TeamStatus, name="team_status", values_callable=_values),
        default=TeamStatus.ACTIVE,
        nullable=False,
    )
    # Denormalized count kept correct inside the same transaction as every
    # membership mutation. A viewer almost always wants the number, and
    # COUNT(*) per team row would be a correlated subquery on every list.
    member_count: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index("ix_teams_owner_user_id", "owner_user_id"),
        Index("ix_teams_visibility_status", "visibility", "status"),
        Index("ix_teams_name", "name"),
        CheckConstraint("member_count >= 0", name="ck_teams_member_count"),
    )


class TeamMembership(Base):
    """One row per (team, user). Membership is the ONLY authorization source."""

    __tablename__ = "team_memberships"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    team_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("teams.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[TeamRole] = mapped_column(
        Enum(TeamRole, name="team_role", values_callable=_values),
        default=TeamRole.MEMBER,
        nullable=False,
    )
    status: Mapped[TeamMembershipStatus] = mapped_column(
        Enum(TeamMembershipStatus, name="team_membership_status", values_callable=_values),
        default=TeamMembershipStatus.ACTIVE,
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        UniqueConstraint("team_id", "user_id", name="uq_team_memberships_pair"),
        # Exactly one OWNER per team. A partial unique index is the cheapest
        # way to make a second owner row impossible at the storage layer.
        Index(
            "uq_team_memberships_single_owner",
            "team_id",
            unique=True,
            postgresql_where=text("role = 'owner'"),
        ),
        Index("ix_team_memberships_team_id", "team_id"),
        Index("ix_team_memberships_user_id", "user_id"),
    )


class TeamJoinRequest(Base):
    """A rider asking to join a team. Deleted on accept or reject."""

    __tablename__ = "team_join_requests"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    team_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("teams.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    # Optional note from the applicant, shown to managers only.
    message: Mapped[str | None] = mapped_column(String(280))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        UniqueConstraint("team_id", "user_id", name="uq_team_join_requests_pair"),
        Index("ix_team_join_requests_team_id", "team_id"),
        Index("ix_team_join_requests_user_id", "user_id"),
    )


class TeamInvitation(Base):
    """An offer of entry. Retained so both sides have a durable record."""

    __tablename__ = "team_invitations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    team_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("teams.id", ondelete="CASCADE"), nullable=False
    )
    invited_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    invited_by_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[TeamInvitationStatus] = mapped_column(
        Enum(TeamInvitationStatus, name="team_invitation_status", values_callable=_values),
        default=TeamInvitationStatus.PENDING,
        nullable=False,
    )
    message: Mapped[str | None] = mapped_column(String(280))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    responded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        # One invitation per (team, invitee). A decline frees the pair: the
        # partial index below only covers PENDING, so a team may re-invite
        # someone who previously said no.
        Index(
            "uq_team_invitations_pending_pair",
            "team_id",
            "invited_user_id",
            unique=True,
            postgresql_where=text("status = 'pending'"),
        ),
        Index("ix_team_invitations_team_id", "team_id"),
        Index("ix_team_invitations_invited_user_id", "invited_user_id"),
        Index(
            "ix_team_invitations_invitee_status",
            "invited_user_id",
            "status",
        ),
        CheckConstraint("invited_user_id != invited_by_user_id", name="ck_team_invitations_no_self"),
    )
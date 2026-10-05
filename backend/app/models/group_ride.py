"""Phase 9 domain: group rides and one authoritative roster (ADR-16).

A GROUP RIDE is a separate aggregate from a team. It does not reuse
`team_memberships`, and membership does not cascade between the two in either
direction:

    A --organizes--> Group Ride R
    B --invited----> Group Ride R

Leaving a team must not eject a rider from a ride they are currently in, and
joining a team must not enroll them in somebody's ride. Reusing the team
membership row would make those two different facts the same row.

Five load-bearing decisions:

1. ONE TABLE, FIVE STATES (ADR-16 §2). `group_ride_participants` carries the
   roster AND the invitation in a single table. Teams need a separate
   invitations table because an applicant and an inviter have OPPOSITE
   permissions; a ride has one way in, so a second table would only create a
   second source of truth about who is on the ride. `status` makes every state
   explicit, and only `joined` grants any visibility.

2. `UNIQUE(group_ride_id, user_id)` FOR THE TABLE'S WHOLE LIFE (ADR-16 §2).
   Not a partial index over active states. "Am I on this ride" becomes an
   index-only lookup, and double-participation is structurally impossible
   rather than merely prevented in the service layer.

3. THE ROSTER FREEZES AT `started` (ADR-16 §3). Nobody may be added to a ride
   that is already rolling. A participant may always WITHDRAW, because
   withdrawal is a consent right and is never the organizer's to grant. That
   asymmetry is deliberate: you can always take yourself out, but nobody can be
   added late.

4. `route_id` + `route_version` ARE BOTH-OR-NEITHER (ADR-16 §4). The pair pins
   an IMMUTABLE version (ADR-09), so editing a route cannot silently change
   what the organizer invited people to ride. The FK covers the PAIR, against
   `route_versions(route_id, version_no)`, which is what makes the pin
   referentially honest: version 7 of route A is rejected unless that exact
   version exists, rather than merely being checked at the service layer.
   A composite `ON DELETE SET NULL` nulls both columns, so a deleted route
   degrades a ride to "no route" instead of leaving a dangling version number
   pointing at geometry that no longer exists.

5. **NO LOCATION TABLE EXISTS, ON PURPOSE** (ADR-16 §6). Live location is
   ephemeral, opt-in, and lives in Redis with a TTL. A table would make GPS
   history permanent by accident: every row a retained fact about where a
   person was, in every backup, subject to every future "just add an index"
   request. See `app/services/ride_location_service.py`.
"""

import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    ForeignKeyConstraint,
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


class GroupRideStatus(str, enum.Enum):
    """Four states, no reversal (ADR-16 §3).

    `open` is the only state in which the roster can grow. There is no `draft`:
    creating a ride produces an open ride, because a two-phase "draft then
    publish" would need a visibility concept this domain does not have.
    """

    OPEN = "open"
    STARTED = "started"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class GroupRideRole(str, enum.Enum):
    """Exactly one organizer. There is no admin or co-organizer (ADR-16 §3).

    A second role with ride authority would mean a second organizer, and the
    entire phase rests on there being one authoritative actor who decides
    whether a ride happens.
    """

    ORGANIZER = "organizer"
    PARTICIPANT = "participant"


class GroupRideParticipantStatus(str, enum.Enum):
    """Five explicit roster states (ADR-16 §2).

    `declined`, `left` and `removed` are three distinct facts, not one: a rider
    who declined was never in, a rider who left exercised consent, and a rider
    who was removed was acted upon. Collapsing them would make the notification
    and audit stories lie.
    """

    INVITED = "invited"
    JOINED = "joined"
    DECLINED = "declined"
    LEFT = "left"
    REMOVED = "removed"


#: The only status that grants ride visibility of any kind.
PARTICIPATING = GroupRideParticipantStatus.JOINED

#: Statuses in which a rider is or was on the ride. Used to build the recipient
#: list for a fan-out: a `joined` rider gets the cancellation notice, a
#: `removed` or `left` rider does not.
ACTIVE_OR_PAST = (
    GroupRideParticipantStatus.INVITED,
    GroupRideParticipantStatus.JOINED,
    GroupRideParticipantStatus.LEFT,
    GroupRideParticipantStatus.REMOVED,
)


class GroupRide(Base):
    __tablename__ = "group_rides"
    __table_args__ = (
        Index("ix_group_rides_organizer_created", "organizer_user_id", "created_at"),
        Index("ix_group_rides_status_starts", "status", "starts_at"),
        Index("ix_group_rides_route_id", "route_id"),
        # The pin is checked as a PAIR against the version it names, so version 7
        # of route A is rejected unless that exact version exists. A FK on
        # route_id alone would only prove the route exists and would happily
        # accept a version number that was never created.
        #
        # `ON DELETE SET NULL` on a composite FK nulls BOTH columns, which is
        # what `ck_group_rides_route_pin` demands: deleting a route (which
        # cascades to its versions) degrades the ride to "no route" rather than
        # failing on the CHECK or leaving a dangling version number.
        ForeignKeyConstraint(
            ["route_id", "route_version"],
            ["route_versions.route_id", "route_versions.version_no"],
            name="fk_group_rides_route_pin",
            ondelete="SET NULL",
        ),
        # Both-or-neither: a ride pins an immutable version or pins nothing.
        CheckConstraint(
            "(route_id IS NULL AND route_version IS NULL) OR "
            "(route_id IS NOT NULL AND route_version IS NOT NULL)",
            name="ck_group_rides_route_pin",
        ),
        CheckConstraint("route_version IS NULL OR route_version >= 1", name="ck_group_rides_route_version"),
        CheckConstraint("length(title) > 0", name="ck_group_rides_title_nonempty"),
        # A terminal state must carry its timestamp. `completed` with a NULL
        # completed_at is not a fact a rider can rely on, and that kind of drift
        # is what becomes a bug report.
        CheckConstraint(
            "(status = 'started' AND started_at IS NOT NULL) OR (status <> 'started')",
            name="ck_group_rides_started_at",
        ),
        CheckConstraint(
            "(status = 'completed' AND completed_at IS NOT NULL) OR (status <> 'completed')",
            name="ck_group_rides_completed_at",
        ),
        CheckConstraint(
            "(status = 'cancelled' AND cancelled_at IS NOT NULL) OR (status <> 'cancelled')",
            name="ck_group_rides_cancelled_at",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    # The authoritative organizer. Always also holds the ORGANIZER/JOINED roster
    # row; both are written in ONE transaction (see ride_service.create_ride).
    organizer_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(String(1000))
    status: Mapped[GroupRideStatus] = mapped_column(
        Enum(GroupRideStatus, name="group_ride_status", values_callable=_values),
        default=GroupRideStatus.OPEN,
        nullable=False,
    )
    starts_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # A free-text meeting point label. Deliberately NOT a coordinate pair: a
    # lat/lon here would be a second location contract with its own privacy
    # story, and Phase 9 needs no machine-readable start point. Navigation and
    # geocoding are explicit non-goals.
    meeting_point: Mapped[str | None] = mapped_column(String(160))
    # An immutable geometry pin, both-or-neither with route_version. The FK on
    # the pair lives in `__table_args__`; see the note there for why it is not a
    # single-column reference to `routes.id`.
    route_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    route_version: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class GroupRideParticipant(Base):
    """One row per (ride, user), for the life of the ride (ADR-16 §2)."""

    __tablename__ = "group_ride_participants"
    __table_args__ = (
        UniqueConstraint("group_ride_id", "user_id", name="uq_group_ride_participants_pair"),
        Index("ix_group_ride_participants_user_id", "user_id"),
        # Serves the roster listing and the `joined` filter that authorizes every
        # visibility decision in the phase.
        Index("ix_group_ride_participants_ride_status", "group_ride_id", "status"),
        # The invitation inbox: a partial scan of pending rows only, so it does
        # not degrade with a ride's answer history.
        Index(
            "ix_group_ride_participants_pending_invitee",
            "user_id",
            postgresql_where=text("status = 'invited'"),
        ),
        # A responder's timestamp is a fact; a non-responder's absence of one is
        # too. This stops a `joined` row being written without saying when.
        CheckConstraint(
            "status = 'invited' OR responded_at IS NOT NULL",
            name="ck_group_ride_participants_responded_at",
        ),
        # The organizer is authoritative and therefore never invitable: it
        # already holds the ORGANIZER/JOINED row.
        CheckConstraint(
            "invited_by_user_id IS NOT NULL OR role = 'organizer'",
            name="ck_group_ride_participants_inviter",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    group_ride_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("group_rides.id", ondelete="CASCADE"), nullable=False
    )
    # CASCADE, matching `team_memberships.user_id`: a deleted rider leaves no
    # roster rows. Contrast `messages.sender_user_id` (RESTRICT) — a roster row
    # is a current-state fact about a ride, not history someone authored.
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[GroupRideRole] = mapped_column(
        Enum(GroupRideRole, name="group_ride_role", values_callable=_values),
        default=GroupRideRole.PARTICIPANT,
        nullable=False,
    )
    status: Mapped[GroupRideParticipantStatus] = mapped_column(
        Enum(GroupRideParticipantStatus, name="group_ride_participant_status", values_callable=_values),
        nullable=False,
    )
    # NULL for the organizer's own row; set for every invitation.
    invited_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT")
    )
    message: Mapped[str | None] = mapped_column(String(280))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    responded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

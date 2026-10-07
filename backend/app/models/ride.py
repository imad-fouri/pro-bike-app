"""Phase 4 domain: rides + raw GPS observations (ADR-06: no PostGIS)."""

import enum
import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, NUMERIC, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.user import _uuid, _values


class RideStatus(str, enum.Enum):
    RECORDING = "recording"
    PAUSED = "paused"
    COMPLETED = "completed"
    DISCARDED = "discarded"
    # NOTE: syncing/sync_failed are LOCAL-ONLY mobile states (sync protocol
    # doc). The server enum intentionally excludes them.


class IntegrityStatus(str, enum.Enum):
    """WS-AC vocabulary for a *completed* ride's integrity verdict.

    ACCEPTED   eligible: aggregated, progress applied, points held.
    SUSPICIOUS retained for history, excluded from competition (not eligible).
    REJECTED   retained for audit, never aggregated, never eligible.

    ``None`` (no verdict yet) is the value for rides that have not been
    finalised; it is **not** eligible because the failure mode of "forgot to
    evaluate" must be exclusion, never silent inclusion.
    """

    ACCEPTED = "accepted"
    SUSPICIOUS = "suspicious"
    REJECTED = "rejected"


class Ride(Base):
    __tablename__ = "rides"
    __table_args__ = (
        UniqueConstraint("user_id", "client_ride_uuid", name="uq_rides_user_client_uuid"),
        Index("ix_rides_user_status", "user_id", "status"),
        Index("ix_rides_started", "started_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    bike_id: Mapped[uuid.UUID] = mapped_column(
        # RESTRICT: history must survive even if a bike row is force-deleted.
        UUID(as_uuid=True),
        ForeignKey("bikes.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    client_ride_uuid: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    # Optional route association (Phase 5). A ride may follow a route, partly
    # follow it, deviate, or have none. route_version pins the exact version
    # followed at association time and is never rewritten (§20).
    route_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("routes.id", ondelete="SET NULL"),
        nullable=True,
        index=True,  # "which rides followed route X" + FK integrity lookups
    )
    route_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[RideStatus] = mapped_column(
        Enum(RideStatus, name="ride_status", values_callable=_values),
        default=RideStatus.RECORDING,
        nullable=False,
    )
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    elapsed_seconds: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    moving_seconds: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    distance_m: Mapped[Decimal] = mapped_column(NUMERIC(12, 2), default=Decimal(0), nullable=False)
    elevation_gain_m: Mapped[Decimal] = mapped_column(
        NUMERIC(10, 2), default=Decimal(0), nullable=False
    )
    elevation_loss_m: Mapped[Decimal] = mapped_column(
        NUMERIC(10, 2), default=Decimal(0), nullable=False
    )
    average_speed_m_s: Mapped[Decimal] = mapped_column(
        NUMERIC(8, 3), default=Decimal(0), nullable=False
    )
    max_speed_m_s: Mapped[Decimal] = mapped_column(
        NUMERIC(8, 3), default=Decimal(0), nullable=False
    )
    start_lat: Mapped[Decimal | None] = mapped_column(NUMERIC(9, 6))
    start_lon: Mapped[Decimal | None] = mapped_column(NUMERIC(10, 6))
    end_lat: Mapped[Decimal | None] = mapped_column(NUMERIC(9, 6))
    end_lon: Mapped[Decimal | None] = mapped_column(NUMERIC(10, 6))
    # WS-AC: the persisted integrity verdict, written exactly once by
    # ``ride_service`` on finalisation and never by a request body. ``None``
    # for in-progress rides means "not yet evaluated", which the eligibility
    # predicate treats as not eligible (fail-closed). A recalc never rewrites
    # an old verdict: a new rule set ships as a new ``calculation_version``
    # with its own evaluation, so history keeps the version that produced it.
    integrity_status: Mapped[IntegrityStatus | None] = mapped_column(
        Enum(IntegrityStatus, name="integrity_status", values_callable=_values),
        nullable=True,
    )
    integrity_calculation_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    integrity_rules_triggered: Mapped[list[str] | None] = mapped_column(JSONB, nullable=True)
    integrity_evaluated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    points: Mapped[list["RidePoint"]] = relationship(
        back_populates="ride",
        cascade="all, delete-orphan",
        order_by="RidePoint.seq",
    )


class RidePoint(Base):
    """Every ingested observation is preserved (provenance). Rejected rows are
    flagged accepted=false with a reason and never metered."""

    __tablename__ = "ride_points"
    __table_args__ = (
        # Idempotency keys: retry-safe ingest (sync protocol doc).
        UniqueConstraint("ride_id", "client_point_uuid", name="uq_points_ride_client_uuid"),
        UniqueConstraint("ride_id", "seq", name="uq_points_ride_seq"),
        Index("ix_points_ride_seq", "ride_id", "seq"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    ride_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("rides.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    client_point_uuid: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    lat: Mapped[Decimal] = mapped_column(NUMERIC(9, 6), nullable=False)
    lon: Mapped[Decimal] = mapped_column(NUMERIC(10, 6), nullable=False)
    alt: Mapped[Decimal | None] = mapped_column(NUMERIC(8, 2))
    accuracy: Mapped[Decimal | None] = mapped_column(NUMERIC(6, 2))
    speed: Mapped[Decimal | None] = mapped_column(NUMERIC(6, 2))  # GPS-reported (raw)
    heading: Mapped[Decimal | None] = mapped_column(NUMERIC(5, 1))
    # Phase 6 (ADR-10 §6): optional sensor samples ride along with the fix. There
    # is no sensor hardware yet, so these stay NULL in the field; nothing is ever
    # estimated from GPS/speed. Rejected fixes never contribute to analysis.
    power_w: Mapped[Decimal | None] = mapped_column(NUMERIC(6, 1))
    hr_bpm: Mapped[Decimal | None] = mapped_column(NUMERIC(5, 1))
    cadence_rpm: Mapped[Decimal | None] = mapped_column(NUMERIC(5, 1))
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    accepted: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    reject_reason: Mapped[str | None] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    ride: Mapped[Ride] = relationship(back_populates="points")

"""Phase 6 domain: training profiles, FTP provenance, derived activity metrics,
daily load, and deterministic workout prescriptions.

Separation rules (docs/05, ADR-10 §5):
  * RIDE != TRAINING ACTIVITY. A training activity *references* a ride but owns
    its own derived metrics and provenance; rides are never widened to hold
    training columns.
  * WORKOUT is a prescription, not history. Training plans stay out of Phase 6.
  * Canonical units are metric (W, bpm, s, m); the client converts for display.
"""

import enum
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, NUMERIC, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.user import _uuid, _values


class FtpSource(str, enum.Enum):
    """Where an FTP value came from. Append-only history (ADR-10 §4)."""

    MANUAL = "manual"
    TEST_20MIN = "test_20min"
    TEST_RAMP = "test_ramp"
    IMPORTED = "imported"
    ESTIMATED = "estimated"


class WorkoutStatus(str, enum.Enum):
    DRAFT = "draft"
    ACTIVE = "active"
    ARCHIVED = "archived"


class WorkoutStepType(str, enum.Enum):
    WARMUP = "warmup"
    STEADY = "steady"
    INTERVAL = "interval"
    RECOVERY = "recovery"
    COOLDOWN = "cooldown"


class TrainingCalculationVersion(Base):
    """Read-only mirror of ``training_calc.CALCULATION_VERSIONS``.

    A drift test asserts this table and the code registry hold the same keys, so
    a formula cannot be introduced without being versioned and documented.
    """

    __tablename__ = "training_calculation_versions"

    version: Mapped[str] = mapped_column(String(48), primary_key=True)
    kind: Mapped[str] = mapped_column(String(24), nullable=False)
    title: Mapped[str] = mapped_column(String(120), nullable=False)
    summary: Mapped[str] = mapped_column(String(400), nullable=False)
    params: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class FtpRecord(Base):
    """One FTP value the rider supplied or a test produced. Never edited."""

    __tablename__ = "ftp_records"
    __table_args__ = (
        Index("ix_ftp_records_user_effective", "user_id", "effective_at"),
        Index("ix_ftp_records_user_created", "user_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    source: Mapped[FtpSource] = mapped_column(
        Enum(FtpSource, name="ftp_source", values_callable=_values), nullable=False
    )
    value_w: Mapped[Decimal] = mapped_column(NUMERIC(6, 1), nullable=False)
    # When this value became true for the rider, not when the row was written.
    effective_at: Mapped[date] = mapped_column(Date, nullable=False)
    # Protocol evidence, e.g. {"approximation": true} for a ramp test.
    evidence: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    @property
    def is_confirmed(self) -> bool:
        """An estimate never outranks something the rider or a test established."""
        return self.source is not FtpSource.ESTIMATED


class TrainingProfile(Base):
    """Per-rider calculation inputs. Unit preference is *not* duplicated here —
    it lives on ``user_profiles.measurement_system``."""

    __tablename__ = "training_profiles"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    ftp_w: Mapped[Decimal | None] = mapped_column(NUMERIC(6, 1))
    ftp_source: Mapped[FtpSource | None] = mapped_column(
        Enum(FtpSource, name="ftp_source", values_callable=_values)
    )
    ftp_record_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ftp_records.id", ondelete="SET NULL")
    )
    max_hr_bpm: Mapped[Decimal | None] = mapped_column(NUMERIC(5, 1))
    resting_hr_bpm: Mapped[Decimal | None] = mapped_column(NUMERIC(5, 1))
    # auto | hr_max | hrr — None means "decide from the available thresholds".
    hr_zone_model: Mapped[str | None] = mapped_column(String(8))
    # Overrides user_profiles.timezone when the rider travels; NULL = inherit.
    timezone: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class TrainingActivity(Base):
    """Derived metrics for one completed ride, with full provenance.

    Every nullable column is ``NULL`` — never 0 — when the underlying data was
    not available, so "no power meter" is always distinguishable from "zero
    watts" (ADR-10 §3).
    """

    __tablename__ = "training_activities"
    __table_args__ = (
        # One activity per ride. Partial: manual activities have no ride_id.
        Index(
            "uq_training_activities_user_ride",
            "user_id",
            "ride_id",
            unique=True,
            postgresql_where=text("ride_id IS NOT NULL"),
        ),
        Index("ix_training_activities_user_date", "user_id", "local_date"),
        Index("ix_training_activities_user_started", "user_id", "started_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    ride_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("rides.id", ondelete="CASCADE")
    )
    local_date: Mapped[date] = mapped_column(Date, nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    elapsed_seconds: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    moving_seconds: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    distance_m: Mapped[Decimal | None] = mapped_column(NUMERIC(12, 2))
    elevation_gain_m: Mapped[Decimal | None] = mapped_column(NUMERIC(10, 2))

    analysis_version: Mapped[str] = mapped_column(String(48), nullable=False)
    insufficient_data: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    sample_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    segment_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    has_power: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    has_heart_rate: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    has_cadence: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    analyzed_seconds: Mapped[Decimal] = mapped_column(
        NUMERIC(10, 1), default=Decimal(0), nullable=False
    )
    power_seconds: Mapped[Decimal] = mapped_column(
        NUMERIC(10, 1), default=Decimal(0), nullable=False
    )
    hr_seconds: Mapped[Decimal] = mapped_column(NUMERIC(10, 1), default=Decimal(0), nullable=False)
    np_seconds: Mapped[Decimal] = mapped_column(NUMERIC(10, 1), default=Decimal(0), nullable=False)

    average_power_w: Mapped[Decimal | None] = mapped_column(NUMERIC(7, 1))
    max_power_w: Mapped[Decimal | None] = mapped_column(NUMERIC(7, 1))
    normalized_power_w: Mapped[Decimal | None] = mapped_column(NUMERIC(7, 1))
    intensity_factor: Mapped[Decimal | None] = mapped_column(NUMERIC(6, 4))
    power_load: Mapped[Decimal | None] = mapped_column(NUMERIC(9, 1))

    average_hr_bpm: Mapped[Decimal | None] = mapped_column(NUMERIC(5, 1))
    max_hr_bpm: Mapped[Decimal | None] = mapped_column(NUMERIC(5, 1))
    average_cadence_rpm: Mapped[Decimal | None] = mapped_column(NUMERIC(5, 1))
    hr_load: Mapped[Decimal | None] = mapped_column(NUMERIC(9, 1))
    hr_zone_model: Mapped[str | None] = mapped_column(String(8))

    # The FTP this row was computed against — never re-resolved retroactively.
    effective_ftp_w: Mapped[Decimal | None] = mapped_column(NUMERIC(6, 1))
    ftp_source: Mapped[FtpSource | None] = mapped_column(
        Enum(FtpSource, name="ftp_source", values_callable=_values)
    )
    # measured | estimated | unavailable — how much to trust effective_ftp_w.
    ftp_basis: Mapped[str] = mapped_column(String(16), default="unavailable", nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    zones: Mapped[list["TrainingActivityZone"]] = relationship(
        back_populates="activity", cascade="all, delete-orphan"
    )


class TrainingActivityZone(Base):
    """Seconds spent in each zone, per zone model."""

    __tablename__ = "training_activity_zones"

    activity_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("training_activities.id", ondelete="CASCADE"),
        primary_key=True,
    )
    # power | hr
    kind: Mapped[str] = mapped_column(String(8), primary_key=True)
    zone: Mapped[int] = mapped_column(Integer, primary_key=True)
    seconds: Mapped[Decimal] = mapped_column(NUMERIC(10, 1), default=Decimal(0), nullable=False)

    activity: Mapped[TrainingActivity] = relationship(back_populates="zones")


class TrainingLoad(Base):
    """One row per rider per local date: the day's load and the running trend."""

    __tablename__ = "training_loads"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    local_date: Mapped[date] = mapped_column(Date, primary_key=True)
    power_load: Mapped[Decimal] = mapped_column(NUMERIC(9, 1), default=Decimal(0), nullable=False)
    hr_load: Mapped[Decimal] = mapped_column(NUMERIC(9, 1), default=Decimal(0), nullable=False)
    ctl: Mapped[Decimal] = mapped_column(NUMERIC(9, 2), default=Decimal(0), nullable=False)
    atl: Mapped[Decimal] = mapped_column(NUMERIC(9, 2), default=Decimal(0), nullable=False)
    tsb: Mapped[Decimal] = mapped_column(NUMERIC(9, 2), default=Decimal(0), nullable=False)
    load_version: Mapped[str] = mapped_column(String(48), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class Workout(Base):
    """A deterministic prescription. Versioned with optimistic locking, exactly
    like routes (ADR-04): a PATCH requires ``expected_version``."""

    __tablename__ = "workouts"
    __table_args__ = (Index("ix_workouts_user_status", "user_id", "status"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(String(2000))
    discipline: Mapped[str] = mapped_column(String(32), default="road", nullable=False)
    goal: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[WorkoutStatus] = mapped_column(
        Enum(WorkoutStatus, name="workout_status", values_callable=_values),
        default=WorkoutStatus.DRAFT,
        nullable=False,
    )
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    target_duration_s: Mapped[int | None] = mapped_column(Integer)
    target_load: Mapped[Decimal | None] = mapped_column(NUMERIC(8, 1))
    intensity_note: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    steps: Mapped[list["WorkoutStep"]] = relationship(
        back_populates="workout",
        cascade="all, delete-orphan",
        order_by="WorkoutStep.seq",
    )


class WorkoutStep(Base):
    __tablename__ = "workout_steps"
    __table_args__ = (
        UniqueConstraint("workout_id", "seq", name="uq_workout_steps_workout_seq"),
        Index("ix_workout_steps_workout_id", "workout_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    workout_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workouts.id", ondelete="CASCADE"),
        nullable=False,
    )
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    step_type: Mapped[WorkoutStepType] = mapped_column(
        Enum(WorkoutStepType, name="workout_step_type", values_callable=_values), nullable=False
    )
    label: Mapped[str] = mapped_column(String(120), nullable=False)
    duration_s: Mapped[int] = mapped_column(Integer, nullable=False)
    repeat_count: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    # Zone numbers refer to the rider's configured zone models; NULL = no target.
    target_zone: Mapped[int | None] = mapped_column(Integer)
    target_power_low_w: Mapped[Decimal | None] = mapped_column(NUMERIC(6, 1))
    target_power_high_w: Mapped[Decimal | None] = mapped_column(NUMERIC(6, 1))
    target_hr_low_bpm: Mapped[Decimal | None] = mapped_column(NUMERIC(5, 1))
    target_hr_high_bpm: Mapped[Decimal | None] = mapped_column(NUMERIC(5, 1))

    workout: Mapped[Workout] = relationship(back_populates="steps")

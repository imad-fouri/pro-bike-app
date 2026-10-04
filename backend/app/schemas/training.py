"""Training schemas. Canonical metric units; the client converts for display.

Provenance is explicit (§15, ADR-10 §3): every metric that could not be derived
is ``null``, and the FTP basis says how far the reference value can be trusted.
"""

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.models.training import FtpSource, WorkoutStatus, WorkoutStepType

FtpBasis = Literal["measured", "estimated", "unavailable"]
HrZoneModel = Literal["hr_max", "hrr"]


class TrainingProfileUpdate(BaseModel):
    """Every field is optional: this is a partial update of the rider's inputs.

    ``ftp_w`` is an append-only *event*: writing it creates an FTP record, and a
    value from history is never silently replaced.
    """

    ftp_w: float | None = Field(default=None, ge=50, le=1500)
    ftp_source: FtpSource | None = None
    effective_at: date | None = None
    max_hr_bpm: float | None = Field(default=None, ge=25, le=250)
    resting_hr_bpm: float | None = Field(default=None, ge=25, le=250)
    hr_zone_model: Literal["auto", "hr_max", "hrr"] | None = None
    timezone: str | None = Field(default=None, max_length=64)


class TrainingProfileOut(BaseModel):
    user_id: uuid.UUID
    ftp_w: Decimal | None
    ftp_source: FtpSource | None
    ftp_basis: FtpBasis = "unavailable"
    max_hr_bpm: Decimal | None
    resting_hr_bpm: Decimal | None
    hr_zone_model: str | None
    timezone: str | None
    effective_timezone: str


class FtpRecordIn(BaseModel):
    """Test protocols are computed server-side from ``ride_id`` — the client
    cannot assert a test result (ADR-10 §4)."""

    source: FtpSource
    value_w: float | None = Field(default=None, ge=50, le=1500)
    effective_at: date | None = None
    ride_id: uuid.UUID | None = None


class FtpRecordOut(BaseModel):
    id: uuid.UUID
    source: FtpSource
    value_w: Decimal
    effective_at: date
    confirmed: bool
    evidence: dict[str, Any] | None = None
    approximation: bool = False
    created_at: datetime


class FtpRecordList(BaseModel):
    items: list[FtpRecordOut]
    effective_ftp_w: Decimal | None
    effective_source: FtpSource | None
    effective_basis: FtpBasis = "unavailable"


class ZoneSeconds(BaseModel):
    kind: Literal["power", "hr"]
    zone: int
    seconds: Decimal


class TrainingActivityOut(BaseModel):
    id: uuid.UUID
    ride_id: uuid.UUID | None
    local_date: date
    started_at: datetime
    ended_at: datetime | None
    elapsed_seconds: int
    moving_seconds: int
    distance_m: Decimal | None
    elevation_gain_m: Decimal | None
    analysis_version: str
    insufficient_data: bool
    has_power: bool
    has_heart_rate: bool
    has_cadence: bool
    analyzed_seconds: Decimal
    power_seconds: Decimal
    hr_seconds: Decimal
    np_seconds: Decimal
    average_power_w: Decimal | None
    max_power_w: Decimal | None
    normalized_power_w: Decimal | None
    intensity_factor: Decimal | None
    power_load: Decimal | None
    average_hr_bpm: Decimal | None
    max_hr_bpm: Decimal | None
    average_cadence_rpm: Decimal | None
    hr_load: Decimal | None
    hr_zone_model: HrZoneModel | None
    effective_ftp_w: Decimal | None
    ftp_source: FtpSource | None
    ftp_basis: FtpBasis
    zones: list[ZoneSeconds] = Field(default_factory=list)


class TrainingActivityPage(BaseModel):
    items: list[TrainingActivityOut]
    total: int
    page: int
    page_size: int


class LoadOut(BaseModel):
    local_date: date
    power_load: Decimal
    hr_load: Decimal
    ctl: Decimal
    atl: Decimal
    tsb: Decimal
    load_version: str


class LoadList(BaseModel):
    items: list[LoadOut]
    version: str


class RecoveryOut(BaseModel):
    """A load-change observation with its evidence — never a readiness or
    medical claim."""

    version: str
    status: Literal["ok", "unavailable"]
    code: str
    signals: list[str]
    evidence: dict[str, Any]


class SuggestionOut(BaseModel):
    version: str
    status: Literal["ok", "unavailable"]
    target_load: Decimal | None
    reason: str
    evidence: dict[str, Any]


class TrainingSummaryOut(BaseModel):
    profile: TrainingProfileOut
    recent_activities: list[TrainingActivityOut]
    loads: list[LoadOut]
    recovery: RecoveryOut
    suggestion: SuggestionOut
    week_power_load: Decimal


class CalculationVersionOut(BaseModel):
    version: str
    kind: str
    title: str
    summary: str
    params: dict[str, Any]
    is_active: bool


class CalculationVersionList(BaseModel):
    items: list[CalculationVersionOut]


class WorkoutStepIn(BaseModel):
    step_type: WorkoutStepType = WorkoutStepType.STEADY
    label: str = Field(min_length=1, max_length=120)
    duration_s: int = Field(ge=1, le=86_400)
    repeat_count: int = Field(default=1, ge=1, le=100)
    target_zone: int | None = Field(default=None, ge=1, le=7)
    target_power_low_w: float | None = Field(default=None, ge=0, le=1500)
    target_power_high_w: float | None = Field(default=None, ge=0, le=1500)
    target_hr_low_bpm: float | None = Field(default=None, ge=25, le=250)
    target_hr_high_bpm: float | None = Field(default=None, ge=25, le=250)


class WorkoutStepOut(BaseModel):
    seq: int
    step_type: WorkoutStepType
    label: str
    duration_s: int
    repeat_count: int
    target_zone: int | None
    target_power_low_w: Decimal | None
    target_power_high_w: Decimal | None
    target_hr_low_bpm: Decimal | None
    target_hr_high_bpm: Decimal | None


class WorkoutCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str | None = Field(default=None, min_length=1, max_length=2000)
    discipline: str = Field(default="road", min_length=1, max_length=32)
    goal: str | None = Field(default=None, max_length=64)
    status: WorkoutStatus = WorkoutStatus.DRAFT
    target_duration_s: int | None = Field(default=None, ge=1, le=86_400)
    target_load: float | None = Field(default=None, ge=0, le=10_000)
    intensity_note: str | None = Field(default=None, max_length=200)
    steps: list[WorkoutStepIn] = Field(default_factory=list, max_length=50)


class WorkoutUpdate(BaseModel):
    expected_version: int = Field(ge=1)
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, min_length=1, max_length=2000)
    discipline: str | None = Field(default=None, min_length=1, max_length=32)
    goal: str | None = Field(default=None, max_length=64)
    status: WorkoutStatus | None = None
    target_duration_s: int | None = Field(default=None, ge=1, le=86_400)
    target_load: float | None = Field(default=None, ge=0, le=10_000)
    intensity_note: str | None = Field(default=None, max_length=200)
    steps: list[WorkoutStepIn] | None = Field(default=None, max_length=50)


class WorkoutStepDetail(WorkoutStepOut):
    id: int


class WorkoutOut(BaseModel):
    id: uuid.UUID
    name: str
    description: str | None
    discipline: str
    goal: str | None
    status: WorkoutStatus
    version: int
    target_duration_s: int | None
    target_load: Decimal | None
    intensity_note: str | None
    steps: list[WorkoutStepDetail] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class WorkoutPage(BaseModel):
    items: list[WorkoutOut]
    total: int
    page: int
    page_size: int

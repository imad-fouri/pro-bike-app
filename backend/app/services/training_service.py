"""Training service: profiles, FTP provenance, ride analysis, load aggregates,
recovery signals, and workout prescriptions.

Layering (ADR-10 §1): this module owns I/O, ownership and persistence. All
mathematics lives in :mod:`app.services.training_calc`, which stays pure. The
ride/GPS truth is *not* duplicated here — distance, elevation and moving time are
read from the canonical ride row produced by ``ride_service``/``gps_engine``.

Two rules that are easy to get wrong and are therefore enforced here:

* **A ride completion must never fail because of training analysis.** Analysis
  runs on finish for convenience, but a failure is logged and left to the
  explicit ``reanalyze`` endpoint, because losing a ride is far worse than
  losing a derived metric.
* **FTP records are the source of truth.** ``training_profiles.ftp_*`` is a
  materialized cache of the resolved value, written in the same code path and
  covered by an invariant test; analysis always re-resolves from the records.
"""

import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.ride import Ride, RidePoint, RideStatus
from app.models.training import (
    FtpRecord,
    FtpSource,
    TrainingActivity,
    TrainingActivityZone,
    TrainingCalculationVersion,
    TrainingLoad,
    TrainingProfile,
    Workout,
    WorkoutStatus,
    WorkoutStep,
    WorkoutStepType,
)
from app.models.user import UserProfile
from app.services import training_calc as calc
from app.services.training_calc import SensorSample

#: Bound on the window a load recompute walks. The 42-day constant is fully
#: converged long before this, so a rider with years of history does not force a
#: multi-year recompute on every new activity.
MAX_TREND_HISTORY_DAYS = 365

#: How far back the recovery signal looks for loads.
RECOVERY_WINDOW_DAYS = 14


class TrainingError(Exception):
    def __init__(self, code: str, message: str, status: int = 404) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


def _now() -> datetime:
    return datetime.now(UTC)


def _dec(value: float | None) -> Decimal | None:
    """Float -> Decimal via string, matching the rest of the codebase."""
    return None if value is None else Decimal(str(value))


# ---------------------------------------------------------------------------
# Profile
# ---------------------------------------------------------------------------


def _zoneinfo(name: str) -> ZoneInfo | None:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, KeyError):
        return None


async def get_timezone(db: AsyncSession, user_id: uuid.UUID) -> tuple[str, ZoneInfo]:
    """Rider's timezone: training profile override, else profile, else UTC."""
    res = await db.execute(
        select(TrainingProfile.timezone, UserProfile.timezone)
        .select_from(TrainingProfile)
        .join(UserProfile, UserProfile.user_id == TrainingProfile.user_id)
        .where(TrainingProfile.user_id == user_id)
    )
    row = res.one_or_none()
    for name in row if row else ():
        if not name:
            continue
        zone = _zoneinfo(name)
        if zone is not None:
            return name, zone
    return "UTC", ZoneInfo("UTC")


async def get_or_create_profile(db: AsyncSession, user_id: uuid.UUID) -> TrainingProfile:
    profile = await db.get(TrainingProfile, user_id)
    if profile is not None:
        return profile
    now = _now()
    profile = TrainingProfile(user_id=user_id, created_at=now, updated_at=now)
    db.add(profile)
    return profile


async def update_profile(
    db: AsyncSession, user_id: uuid.UUID, data: dict[str, Any]
) -> TrainingProfile:
    profile = await get_or_create_profile(db, user_id)
    if data.get("timezone") and _zoneinfo(data["timezone"]) is None:
        raise TrainingError("INVALID_TIMEZONE", "Unknown timezone.", 422)
    for field in (
        "max_hr_bpm",
        "resting_hr_bpm",
        "hr_zone_model",
        "timezone",
    ):
        if field in data:
            setattr(profile, field, _dec(data[field]) if field.endswith("_bpm") else data[field])
    # A manual FTP is an append-only event first, then a cache update.
    if data.get("ftp_w") is not None:
        await add_ftp_record(
            db,
            user_id,
            source=FtpSource(data.get("ftp_source") or FtpSource.MANUAL),
            value_w=float(data["ftp_w"]),
            effective_at=data.get("effective_at") or _now().date(),
        )
        profile = await get_or_create_profile(db, user_id)
    profile.updated_at = _now()
    await db.commit()
    await db.refresh(profile)
    return profile


def _apply_ftp_cache(profile: TrainingProfile, resolution: calc.FtpResolution) -> None:
    profile.ftp_w = _dec(resolution.ftp_w)
    profile.ftp_source = FtpSource(resolution.source) if resolution.source else None
    profile.ftp_record_id = resolution.record_id if resolution.confirmed else None
    profile.updated_at = _now()


# ---------------------------------------------------------------------------
# FTP records (append-only)
# ---------------------------------------------------------------------------


async def list_ftp_records(db: AsyncSession, user_id: uuid.UUID) -> list[FtpRecord]:
    res = await db.execute(
        select(FtpRecord)
        .where(FtpRecord.user_id == user_id)
        .order_by(FtpRecord.effective_at.desc(), FtpRecord.created_at.desc())
    )
    return list(res.scalars())


async def resolve_effective_ftp(db: AsyncSession, user_id: uuid.UUID) -> calc.FtpResolution:
    records = await list_ftp_records(db, user_id)
    return calc.resolve_effective_ftp(
        [
            {
                "id": r.id,
                "source": r.source.value,
                "value_w": float(r.value_w),
                "effective_at": r.effective_at,
                "created_at": r.created_at,
            }
            for r in records
        ]
    )


async def add_ftp_record(
    db: AsyncSession,
    user_id: uuid.UUID,
    *,
    source: FtpSource,
    value_w: float | None = None,
    effective_at: date | None = None,
    ride_id: uuid.UUID | None = None,
    evidence: dict[str, Any] | None = None,
) -> FtpRecord:
    """Append an FTP value. Test protocols are computed server-side.

    A client may not assert a number for a test-derived record: if ``ride_id``
    is supplied the value comes from the ride's own samples, so a forged 500 W
    "20 minute test" is impossible.
    """
    if source in (FtpSource.TEST_20MIN, FtpSource.TEST_RAMP):
        if ride_id is None:
            raise TrainingError(
                "RIDE_REQUIRED", "A test-derived FTP needs the ride it came from.", 422
            )
        value_w, derived = await _ftp_from_ride(db, user_id, ride_id, source)
        if value_w is None:
            raise TrainingError(
                "INSUFFICIENT_DATA",
                derived.get("reason", "This ride has too little power data for a test."),
                422,
            )
        evidence = {**derived, "ride_id": str(ride_id)}
    elif value_w is None:
        raise TrainingError("FTP_REQUIRED", "An FTP value is required.", 422)
    elif calc.valid_ftp(value_w) is None:
        raise TrainingError("FTP_OUT_OF_RANGE", "That FTP value is not plausible.", 422)

    record = FtpRecord(
        user_id=user_id,
        source=source,
        value_w=_dec(value_w) or Decimal(0),
        effective_at=effective_at or _now().date(),
        evidence=evidence,
        created_at=_now(),
    )
    db.add(record)
    await db.flush()
    profile = await get_or_create_profile(db, user_id)
    # ftp_records are the source of truth; the profile fields are a cache of the
    # resolved value, refreshed in the same transaction (ADR-10 §4).
    _apply_ftp_cache(profile, await resolve_effective_ftp(db, user_id))
    await db.commit()
    await db.refresh(record)
    return record


async def _ftp_from_ride(
    db: AsyncSession, user_id: uuid.UUID, ride_id: uuid.UUID, source: FtpSource
) -> tuple[float | None, dict[str, Any]]:
    from app.services.ride_service import RideError, get_owned

    try:
        ride = await get_owned(db, user_id, ride_id)
    except RideError as e:
        raise TrainingError(e.code, e.message, e.status) from e
    samples = await _ride_samples(db, ride)
    if source is FtpSource.TEST_20MIN:
        return calc.ftp_from_20min_test(samples)
    return calc.ftp_from_ramp_test(samples)


# ---------------------------------------------------------------------------
# Ride analysis
# ---------------------------------------------------------------------------


async def _ride_samples(db: AsyncSession, ride: Ride) -> list[SensorSample]:
    """Accepted points only, as sensor samples.

    Rejected fixes never contribute: analysis must agree with the canonical ride
    metrics, which also ignore them (ADR-10 §6).
    """
    res = await db.execute(
        select(
            RidePoint.recorded_at,
            RidePoint.power_w,
            RidePoint.hr_bpm,
            RidePoint.cadence_rpm,
        )
        .where(RidePoint.ride_id == ride.id, RidePoint.accepted.is_(True))
        .order_by(RidePoint.seq)
    )
    return [
        SensorSample(
            t=row.recorded_at.timestamp(),
            power_w=float(row.power_w) if row.power_w is not None else None,
            hr_bpm=float(row.hr_bpm) if row.hr_bpm is not None else None,
            cadence_rpm=float(row.cadence_rpm) if row.cadence_rpm is not None else None,
        )
        for row in res.all()
    ]


async def analyze_ride(
    db: AsyncSession, ride: Ride, resolution: calc.FtpResolution | None = None
) -> tuple[TrainingActivity, bool]:
    """Derive and store the training activity for a completed ride.

    Idempotent: re-running recomputes in place (new version, new FTP) and
    returns ``created=False``. Returns ``(activity, created)``.
    """
    if ride.status != RideStatus.COMPLETED:
        raise TrainingError("RIDE_NOT_COMPLETED", "Only a completed ride can be analyzed.", 409)

    resolution = resolution or await resolve_effective_ftp(db, ride.user_id)
    samples = await _ride_samples(db, ride)
    _, timezone_zone = await get_timezone(db, ride.user_id)
    profile = await get_or_create_profile(db, ride.user_id)
    result = calc.analyze_activity(
        samples,
        ftp_w=resolution.ftp_w,
        max_hr_bpm=float(profile.max_hr_bpm) if profile.max_hr_bpm is not None else None,
        resting_hr_bpm=float(profile.resting_hr_bpm)
        if profile.resting_hr_bpm is not None
        else None,
        hr_model_override=(
            profile.hr_zone_model if profile.hr_zone_model in ("hr_max", "hrr") else None
        ),
    )

    ended = ride.ended_at or _now()
    local_date = ride.started_at.astimezone(timezone_zone).date()
    now = _now()
    existing = (
        await db.execute(
            select(TrainingActivity).where(
                TrainingActivity.user_id == ride.user_id, TrainingActivity.ride_id == ride.id
            )
        )
    ).scalar_one_or_none()
    created = existing is None
    activity = existing or TrainingActivity(
        user_id=ride.user_id,
        ride_id=ride.id,
        created_at=now,
    )
    activity.local_date = local_date
    activity.started_at = ride.started_at
    activity.ended_at = ended
    activity.elapsed_seconds = ride.elapsed_seconds
    activity.moving_seconds = ride.moving_seconds
    activity.distance_m = ride.distance_m
    activity.elevation_gain_m = ride.elevation_gain_m

    activity.analysis_version = calc.ACTIVITY_ANALYSIS_VERSION
    activity.insufficient_data = not result["sufficient_data"]
    activity.sample_count = result["sample_count"]
    activity.segment_count = result["segment_count"]
    activity.has_power = result["has_power"]
    activity.has_heart_rate = result["has_heart_rate"]
    activity.has_cadence = result["has_cadence"]
    activity.analyzed_seconds = _dec(result["analyzed_seconds"]) or Decimal(0)
    activity.power_seconds = _dec(result["power_seconds"]) or Decimal(0)
    activity.hr_seconds = _dec(result["hr_seconds"]) or Decimal(0)
    activity.np_seconds = _dec(result["np_seconds"]) or Decimal(0)
    activity.average_power_w = _dec(result["average_power_w"])
    activity.max_power_w = _dec(result["max_power_w"])
    activity.normalized_power_w = _dec(result["normalized_power_w"])
    activity.intensity_factor = _dec(result["intensity_factor"])
    activity.power_load = _dec(result["power_load"])
    activity.average_hr_bpm = _dec(result["average_hr_bpm"])
    activity.max_hr_bpm = _dec(result["max_hr_bpm"])
    activity.average_cadence_rpm = _dec(result["average_cadence_rpm"])
    activity.hr_load = _dec(result["hr_load"])
    activity.hr_zone_model = result["hr_zone_model"]
    activity.effective_ftp_w = _dec(resolution.ftp_w)
    activity.ftp_source = FtpSource(resolution.source) if resolution.source else None
    activity.ftp_basis = (
        "unavailable"
        if resolution.ftp_w is None
        else ("measured" if resolution.confirmed else "estimated")
    )
    activity.updated_at = now

    db.add(activity)
    await db.flush()

    await db.execute(
        delete(TrainingActivityZone).where(TrainingActivityZone.activity_id == activity.id)
    )
    for kind, seconds_map in (
        ("power", result["power_zone_seconds"]),
        ("hr", result["hr_zone_seconds"]),
    ):
        for zone, seconds in seconds_map.items():
            db.add(
                TrainingActivityZone(
                    activity_id=activity.id, kind=kind, zone=zone, seconds=_dec(seconds) or 0
                )
            )

    await db.commit()
    await db.refresh(activity)
    await recompute_loads(db, ride.user_id, local_date)
    return activity, created


async def _profile_thresholds(
    db: AsyncSession, user_id: uuid.UUID
) -> tuple[Decimal | None, Decimal | None]:
    profile = await db.get(TrainingProfile, user_id)
    if profile is None:
        return None, None
    return profile.max_hr_bpm, profile.resting_hr_bpm


async def sync_ride(db: AsyncSession, ride: Ride) -> None:
    """Best-effort analysis triggered by ride completion.

    Never raises: a derived metric must not be able to fail a ride. The failure
    is logged and the explicit reanalyze endpoint repairs it.
    """
    if ride.status != RideStatus.COMPLETED:
        return
    try:
        await analyze_ride(db, ride)
    except Exception:  # deliberate: see docstring
        import logging

        logging.getLogger("cyclecoach").exception(
            "training analysis failed for ride %s; use reanalyze", ride.id
        )


# ---------------------------------------------------------------------------
# Load aggregates
# ---------------------------------------------------------------------------


async def daily_loads(
    db: AsyncSession, user_id: uuid.UUID, start: date, end: date
) -> dict[date, tuple[float, float]]:
    """Per-day (power, hr) totals derived from activities. NULL load counts 0."""
    res = await db.execute(
        select(
            TrainingActivity.local_date,
            func.coalesce(func.sum(TrainingActivity.power_load), 0),
            func.coalesce(func.sum(TrainingActivity.hr_load), 0),
        )
        .where(
            TrainingActivity.user_id == user_id,
            TrainingActivity.local_date >= start,
            TrainingActivity.local_date <= end,
        )
        .group_by(TrainingActivity.local_date)
    )
    return {row[0]: (float(row[1] or 0), float(row[2] or 0)) for row in res.all()}


async def recompute_loads(db: AsyncSession, user_id: uuid.UUID, upto: date) -> None:
    """Rebuild ``training_loads`` up to ``upto`` from the activity history.

    Load is an *aggregate*, not an event, so it is recomputed rather than
    appended: adding an activity changes the trend from that day forward, and
    the days before the first activity are all zeros either way.
    """
    first = (
        await db.execute(
            select(func.min(TrainingActivity.local_date)).where(TrainingActivity.user_id == user_id)
        )
    ).scalar_one()
    start = first or upto
    start = max(start, upto - timedelta(days=MAX_TREND_HISTORY_DAYS))
    if start > upto:
        return
    totals = await daily_loads(db, user_id, start, upto)
    power = [(day, values[0]) for day, values in totals.items()]
    hr = [(day, values[1]) for day, values in totals.items()]
    power_rows = calc.load_trend(power, start, upto)
    hr_rows = {row["date"]: row for row in calc.load_trend(hr, start, upto)}

    await db.execute(
        delete(TrainingLoad).where(
            TrainingLoad.user_id == user_id,
            TrainingLoad.local_date >= start,
            TrainingLoad.local_date <= upto,
        )
    )
    now = _now()
    for row in power_rows:
        db.add(
            TrainingLoad(
                user_id=user_id,
                local_date=row["date"],
                power_load=_dec(row["load"]) or Decimal(0),
                hr_load=_dec(hr_rows[row["date"]]["load"]) or Decimal(0),
                ctl=_dec(row["ctl"]) or Decimal(0),
                atl=_dec(row["atl"]) or Decimal(0),
                tsb=_dec(row["tsb"]) or Decimal(0),
                load_version=calc.EWMA_VERSION,
                updated_at=now,
            )
        )
    await db.commit()


async def list_loads(
    db: AsyncSession, user_id: uuid.UUID, start: date, end: date
) -> list[TrainingLoad]:
    res = await db.execute(
        select(TrainingLoad)
        .where(
            TrainingLoad.user_id == user_id,
            TrainingLoad.local_date >= start,
            TrainingLoad.local_date <= end,
        )
        .order_by(TrainingLoad.local_date)
    )
    return list(res.scalars())


async def recovery(db: AsyncSession, user_id: uuid.UUID) -> dict[str, Any]:
    _, zone = await get_timezone(db, user_id)
    today = datetime.now(zone).date()
    totals = await daily_loads(db, user_id, today - timedelta(days=RECOVERY_WINDOW_DAYS), today)
    loads = [(day, values[0]) for day, values in totals.items()]
    return calc.recovery_signals(loads, today)


# ---------------------------------------------------------------------------
# Activities
# ---------------------------------------------------------------------------


async def list_activities(
    db: AsyncSession,
    user_id: uuid.UUID,
    page: int,
    page_size: int,
    start: date | None = None,
    end: date | None = None,
) -> tuple[list[TrainingActivity], int]:
    filters = [TrainingActivity.user_id == user_id]
    if start is not None:
        filters.append(TrainingActivity.local_date >= start)
    if end is not None:
        filters.append(TrainingActivity.local_date <= end)
    count = (
        await db.execute(select(func.count()).select_from(TrainingActivity).where(*filters))
    ).scalar_one()
    res = await db.execute(
        select(TrainingActivity)
        .where(*filters)
        .order_by(TrainingActivity.started_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    return list(res.scalars()), count


async def get_activity(
    db: AsyncSession, user_id: uuid.UUID, activity_id: uuid.UUID
) -> TrainingActivity:
    res = await db.execute(
        select(TrainingActivity).where(
            TrainingActivity.id == activity_id, TrainingActivity.user_id == user_id
        )
    )
    activity = res.scalar_one_or_none()
    if activity is None:
        raise TrainingError("ACTIVITY_NOT_FOUND", "Training activity not found.", 404)
    return activity


async def get_activity_by_ride(
    db: AsyncSession, user_id: uuid.UUID, ride_id: uuid.UUID
) -> TrainingActivity:
    res = await db.execute(
        select(TrainingActivity).where(
            TrainingActivity.ride_id == ride_id, TrainingActivity.user_id == user_id
        )
    )
    activity = res.scalar_one_or_none()
    if activity is None:
        raise TrainingError("ACTIVITY_NOT_FOUND", "Training activity not found.", 404)
    return activity


async def activity_zones(db: AsyncSession, activity_id: uuid.UUID) -> list[TrainingActivityZone]:
    res = await db.execute(
        select(TrainingActivityZone)
        .where(TrainingActivityZone.activity_id == activity_id)
        .order_by(TrainingActivityZone.kind, TrainingActivityZone.zone)
    )
    return list(res.scalars())


async def list_calculation_versions(db: AsyncSession) -> list[TrainingCalculationVersion]:
    res = await db.execute(
        select(TrainingCalculationVersion).order_by(
            TrainingCalculationVersion.kind, TrainingCalculationVersion.version
        )
    )
    return list(res.scalars())


async def calculation_registry(db: AsyncSession) -> list[dict[str, Any]]:
    """The published calculation registry, code first.

    ``training_calc.CALCULATION_VERSIONS`` is the source of truth; the table
    records which entries are active so a version can be retired without
    rewriting stored history. Serving from the registry means this endpoint
    still answers correctly on a database that was built from models rather than
    migrated, and a migration that forgot to seed a version fails the drift test
    instead of silently hiding it from clients.
    """
    stored = {row.version: row for row in await list_calculation_versions(db)}
    return [
        {
            "version": version,
            "kind": entry["kind"],
            "title": entry["title"],
            "summary": entry["summary"],
            "params": dict(entry["params"]),
            "is_active": bool(stored[version].is_active) if version in stored else True,
        }
        for version, entry in sorted(calc.CALCULATION_VERSIONS.items())
    ]


# ---------------------------------------------------------------------------
# Workouts
# ---------------------------------------------------------------------------


async def _replace_steps(db: AsyncSession, workout: Workout, steps: list[dict[str, Any]]) -> None:
    """Replace a workout's steps, renumbering them from 1.

    The delete is issued as a statement and flushed *before* the new rows are
    inserted. Relying on the collection's ``delete-orphan`` cascade alone makes
    SQLAlchemy emit the inserts first, which violates
    ``uq_workout_steps_workout_seq`` as soon as a shortened step list reuses a
    ``seq`` the old rows still hold.
    """
    if workout.id is not None:
        await db.execute(delete(WorkoutStep).where(WorkoutStep.workout_id == workout.id))
        await db.flush()
        workout.steps.clear()
    for index, step in enumerate(steps, start=1):
        workout.steps.append(
            WorkoutStep(
                seq=index,
                step_type=WorkoutStepType(step.get("step_type") or WorkoutStepType.STEADY),
                label=step["label"],
                duration_s=int(step["duration_s"]),
                repeat_count=int(step.get("repeat_count") or 1),
                target_zone=step.get("target_zone"),
                target_power_low_w=_dec(step.get("target_power_low_w")),
                target_power_high_w=_dec(step.get("target_power_high_w")),
                target_hr_low_bpm=_dec(step.get("target_hr_low_bpm")),
                target_hr_high_bpm=_dec(step.get("target_hr_high_bpm")),
            )
        )


async def create_workout(db: AsyncSession, user_id: uuid.UUID, data: dict[str, Any]) -> Workout:
    now = _now()
    workout = Workout(
        user_id=user_id,
        name=data["name"],
        description=data.get("description"),
        discipline=data.get("discipline") or "road",
        goal=data.get("goal"),
        status=WorkoutStatus(data.get("status") or WorkoutStatus.DRAFT),
        version=1,
        target_duration_s=data.get("target_duration_s"),
        target_load=_dec(data.get("target_load")),
        intensity_note=data.get("intensity_note"),
        created_at=now,
        updated_at=now,
    )
    await _replace_steps(db, workout, data.get("steps") or [])
    db.add(workout)
    await db.commit()
    return await _reload(db, workout.id)


async def _reload(db: AsyncSession, workout_id: uuid.UUID) -> Workout:
    """Re-read a workout with its steps attached.

    Steps are always serialized, so they must be eager loaded: touching the
    collection on an expired instance emits IO outside the greenlet context and
    raises ``MissingGreenlet`` in async SQLAlchemy.
    """
    res = await db.execute(
        select(Workout).where(Workout.id == workout_id).options(selectinload(Workout.steps))
    )
    return res.scalar_one()


async def get_workout(
    db: AsyncSession, user_id: uuid.UUID, workout_id: uuid.UUID, *, deleted: bool = False
) -> Workout:
    res = await db.execute(
        select(Workout)
        .where(Workout.id == workout_id, Workout.user_id == user_id)
        .options(selectinload(Workout.steps))
    )
    workout = res.scalar_one_or_none()
    if workout is None or (workout.deleted_at is not None and not deleted):
        raise TrainingError("WORKOUT_NOT_FOUND", "Workout not found.", 404)
    return workout


async def list_workouts(
    db: AsyncSession, user_id: uuid.UUID, page: int, page_size: int, status: str | None
) -> tuple[list[Workout], int]:
    filters = [Workout.user_id == user_id, Workout.deleted_at.is_(None)]
    if status:
        filters.append(Workout.status == WorkoutStatus(status))
    count = (
        await db.execute(select(func.count()).select_from(Workout).where(*filters))
    ).scalar_one()
    res = await db.execute(
        select(Workout)
        .where(*filters)
        .options(selectinload(Workout.steps))
        .order_by(Workout.updated_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    return list(res.scalars()), count


async def update_workout(db: AsyncSession, workout: Workout, data: dict[str, Any]) -> Workout:
    expected = data.get("expected_version")
    if expected is None:
        raise TrainingError("VERSION_REQUIRED", "expected_version is required.", 422)
    if int(expected) != workout.version:
        raise TrainingError("VERSION_CONFLICT", "Workout was modified by someone else.", 409)
    for field in ("name", "description", "discipline", "goal", "intensity_note"):
        if field in data:
            setattr(workout, field, data[field])
    if data.get("status"):
        workout.status = WorkoutStatus(data["status"])
    if "target_duration_s" in data:
        workout.target_duration_s = data["target_duration_s"]
    if "target_load" in data:
        workout.target_load = _dec(data["target_load"])
    if data.get("steps") is not None:
        await _replace_steps(db, workout, data["steps"])
    workout.version += 1
    workout.updated_at = _now()
    await db.commit()
    return await _reload(db, workout.id)


async def delete_workout(db: AsyncSession, workout: Workout) -> Workout:
    """Soft delete; idempotent."""
    workout.deleted_at = workout.deleted_at or _now()
    workout.updated_at = _now()
    await db.commit()
    return workout

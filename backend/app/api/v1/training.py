"""Training API (Phase 6). Owner-only; every metric is versioned and provenance
is explicit (ADR-10).

Errors use the shared envelope; a foreign or private resource is 404, never 403,
so the endpoints are not an existence oracle.
"""

import uuid
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core.rate_limit import allow
from app.db.session import get_db
from app.models.training import FtpRecord, FtpSource, TrainingActivity, Workout
from app.models.user import User
from app.schemas.training import (
    CalculationVersionList,
    CalculationVersionOut,
    FtpRecordIn,
    FtpRecordList,
    FtpRecordOut,
    LoadList,
    LoadOut,
    RecoveryOut,
    SuggestionOut,
    TrainingActivityOut,
    TrainingActivityPage,
    TrainingProfileOut,
    TrainingProfileUpdate,
    TrainingSummaryOut,
    WorkoutCreate,
    WorkoutOut,
    WorkoutPage,
    WorkoutStepDetail,
    WorkoutUpdate,
    ZoneSeconds,
)
from app.services import training_calc as calc
from app.services import training_service
from app.services.training_service import TrainingError

router = APIRouter(prefix="/training", tags=["training"])

MAX_LIST = 100


def _limit(request: Request, user: User, key: str = "training", limit: int = 120) -> None:
    ident = request.client.host if request.client else "unknown"
    if not allow(f"{key}:{user.id}:{ident}", limit, 60):
        raise HTTPException(status_code=429, detail="Too many requests.")


def _err(e: TrainingError) -> HTTPException:
    return HTTPException(status_code=e.status, detail={"code": e.code, "message": e.message})


def _ftp_out(record: FtpRecord) -> FtpRecordOut:
    evidence = record.evidence or {}
    return FtpRecordOut(
        id=record.id,
        source=record.source,
        value_w=record.value_w,
        effective_at=record.effective_at,
        confirmed=record.is_confirmed,
        evidence=record.evidence,
        approximation=bool(evidence.get("approximation", False)),
        created_at=record.created_at,
    )


def _basis(source: FtpSource | None, value: Decimal | None) -> str:
    """How far the FTP reference can be trusted, from the cached resolution."""
    if value is None or source is None:
        return "unavailable"
    return "estimated" if source is FtpSource.ESTIMATED else "measured"


async def _profile_out(db: AsyncSession, user_id: uuid.UUID) -> TrainingProfileOut:
    profile = await training_service.get_or_create_profile(db, user_id)
    effective_timezone, _ = await training_service.get_timezone(db, user_id)
    return TrainingProfileOut(
        user_id=user_id,
        ftp_w=profile.ftp_w,
        ftp_source=profile.ftp_source,
        ftp_basis=_basis(profile.ftp_source, profile.ftp_w),  # type: ignore[arg-type]
        max_hr_bpm=profile.max_hr_bpm,
        resting_hr_bpm=profile.resting_hr_bpm,
        hr_zone_model=profile.hr_zone_model,
        timezone=profile.timezone,
        effective_timezone=effective_timezone,
    )


async def _activity_out(
    db: AsyncSession, activity: TrainingActivity, *, with_zones: bool = False
) -> TrainingActivityOut:
    zones: list[ZoneSeconds] = []
    if with_zones:
        rows = await training_service.activity_zones(db, activity.id)
        zones = [
            ZoneSeconds(kind=r.kind, zone=r.zone, seconds=r.seconds)  # type: ignore[arg-type]
            for r in rows
        ]
    return TrainingActivityOut(
        id=activity.id,
        ride_id=activity.ride_id,
        local_date=activity.local_date,
        started_at=activity.started_at,
        ended_at=activity.ended_at,
        elapsed_seconds=activity.elapsed_seconds,
        moving_seconds=activity.moving_seconds,
        distance_m=activity.distance_m,
        elevation_gain_m=activity.elevation_gain_m,
        analysis_version=activity.analysis_version,
        insufficient_data=activity.insufficient_data,
        has_power=activity.has_power,
        has_heart_rate=activity.has_heart_rate,
        has_cadence=activity.has_cadence,
        analyzed_seconds=activity.analyzed_seconds,
        power_seconds=activity.power_seconds,
        hr_seconds=activity.hr_seconds,
        np_seconds=activity.np_seconds,
        average_power_w=activity.average_power_w,
        max_power_w=activity.max_power_w,
        normalized_power_w=activity.normalized_power_w,
        intensity_factor=activity.intensity_factor,
        power_load=activity.power_load,
        average_hr_bpm=activity.average_hr_bpm,
        max_hr_bpm=activity.max_hr_bpm,
        average_cadence_rpm=activity.average_cadence_rpm,
        hr_load=activity.hr_load,
        hr_zone_model=activity.hr_zone_model,  # type: ignore[arg-type]
        effective_ftp_w=activity.effective_ftp_w,
        ftp_source=activity.ftp_source,
        ftp_basis=activity.ftp_basis,  # type: ignore[arg-type]
        zones=zones,
    )


def _load_out(row: Any) -> LoadOut:
    return LoadOut(
        local_date=row.local_date,
        power_load=row.power_load,
        hr_load=row.hr_load,
        ctl=row.ctl,
        atl=row.atl,
        tsb=row.tsb,
        load_version=row.load_version,
    )


def _recovery_out(payload: dict[str, Any]) -> RecoveryOut:
    return RecoveryOut(
        version=payload["version"],
        status=payload["status"],
        code=payload["code"],
        signals=payload["signals"],
        evidence=payload["evidence"],
    )


def _suggestion_out(payload: dict[str, Any]) -> SuggestionOut:
    return SuggestionOut(
        version=payload["version"],
        status=payload["status"],
        target_load=Decimal(str(payload["target_load"])) if payload["target_load"] else None,
        reason=payload["reason"],
        evidence=payload["evidence"],
    )


# ---------------------------------------------------------------------------
# Profile
# ---------------------------------------------------------------------------


@router.get("/profile", response_model=TrainingProfileOut)
async def get_profile(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> TrainingProfileOut:
    return await _profile_out(db, user.id)


@router.put("/profile", response_model=TrainingProfileOut)
async def put_profile(
    data: TrainingProfileUpdate,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> TrainingProfileOut:
    _limit(request, user)
    try:
        await training_service.update_profile(db, user.id, data.model_dump(exclude_unset=True))
    except TrainingError as e:
        raise _err(e) from e
    return await _profile_out(db, user.id)


# ---------------------------------------------------------------------------
# FTP records
# ---------------------------------------------------------------------------


@router.get("/ftp-records", response_model=FtpRecordList)
async def list_ftp_records(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> FtpRecordList:
    records = await training_service.list_ftp_records(db, user.id)
    resolution = await training_service.resolve_effective_ftp(db, user.id)
    effective = Decimal(str(resolution.ftp_w)) if resolution.ftp_w else None
    return FtpRecordList(
        items=[_ftp_out(r) for r in records],
        effective_ftp_w=effective,
        effective_source=resolution.source,  # type: ignore[arg-type]
        effective_basis=_basis(  # type: ignore[arg-type]
            FtpSource(resolution.source) if resolution.source else None, effective
        ),
    )


@router.post("/ftp-records", response_model=FtpRecordOut, status_code=201)
async def create_ftp_record(
    data: FtpRecordIn,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> FtpRecordOut:
    """Append an FTP value. Test protocols are computed from the ride server-side."""
    _limit(request, user, key="ftp")
    try:
        record = await training_service.add_ftp_record(
            db,
            user.id,
            source=data.source,
            value_w=data.value_w,
            effective_at=data.effective_at,
            ride_id=data.ride_id,
        )
    except TrainingError as e:
        raise _err(e) from e
    return _ftp_out(record)


# ---------------------------------------------------------------------------
# Activities
# ---------------------------------------------------------------------------


@router.get("/activities", response_model=TrainingActivityPage)
async def list_activities(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=MAX_LIST),
    start: date | None = None,
    end: date | None = None,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> TrainingActivityPage:
    if start and end and start > end:
        raise HTTPException(
            status_code=422, detail={"code": "INVALID_RANGE", "message": "start is after end."}
        )
    items, total = await training_service.list_activities(db, user.id, page, page_size, start, end)
    return TrainingActivityPage(
        items=[await _activity_out(db, a) for a in items],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/activities/{activity_id}", response_model=TrainingActivityOut)
async def get_activity(
    activity_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> TrainingActivityOut:
    try:
        activity = await training_service.get_activity(db, user.id, activity_id)
    except TrainingError as e:
        raise _err(e) from e
    return await _activity_out(db, activity, with_zones=True)


@router.post("/activities/{ride_id}/reanalyze", response_model=TrainingActivityOut)
async def reanalyze_activity(
    ride_id: uuid.UUID,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> TrainingActivityOut:
    """Explicit re-derivation, e.g. after a new FTP was recorded.

    Separate from the automatic pass because it overwrites derived values and
    must be a visible act (ADR-10 §6).
    """
    from app.services.ride_service import RideError, get_owned

    _limit(request, user, key="reanalyze")
    try:
        ride = await get_owned(db, user.id, ride_id)
    except RideError as e:
        raise _err(TrainingError(e.code, e.message, e.status)) from e
    try:
        activity, _ = await training_service.analyze_ride(db, ride)
    except TrainingError as e:
        raise _err(e) from e
    return await _activity_out(db, activity, with_zones=True)


# ---------------------------------------------------------------------------
# Load, recovery, summary
# ---------------------------------------------------------------------------


@router.get("/loads", response_model=LoadList)
async def get_loads(
    start: date | None = None,
    end: date | None = None,
    days: int = Query(default=28, ge=1, le=365),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> LoadList:
    if end is None:
        _, zone = await training_service.get_timezone(db, user.id)
        end = datetime.now(zone).date()
    if start is None:
        start = end - timedelta(days=days - 1)
    if start > end:
        raise HTTPException(
            status_code=422, detail={"code": "INVALID_RANGE", "message": "start is after end."}
        )
    rows = await training_service.list_loads(db, user.id, start, end)
    return LoadList(items=[_load_out(r) for r in rows], version=calc.EWMA_VERSION)


@router.get("/recovery", response_model=RecoveryOut)
async def get_recovery(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> RecoveryOut:
    return _recovery_out(await training_service.recovery(db, user.id))


@router.get("/summary", response_model=TrainingSummaryOut)
async def get_summary(
    days: int = Query(default=28, ge=1, le=365),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> TrainingSummaryOut:
    """One aggregate read for the dashboard instead of four round trips."""
    _, zone = await training_service.get_timezone(db, user.id)
    today = datetime.now(zone).date()
    start = today - timedelta(days=days - 1)
    items, _ = await training_service.list_activities(db, user.id, 1, 10, None, None)
    rows = await training_service.list_loads(db, user.id, start, today)
    week_start = today - timedelta(days=6)
    week = await training_service.daily_loads(db, user.id, week_start, today)
    suggestion = calc.suggest_intensity_target(
        float(rows[-1].power_load) if rows and rows[-1].power_load is not None else None,
        weekly_load=float(sum(v[0] for v in week.values())) or None,
    )
    return TrainingSummaryOut(
        profile=await _profile_out(db, user.id),
        recent_activities=[await _activity_out(db, a) for a in items],
        loads=[_load_out(r) for r in rows],
        recovery=_recovery_out(await training_service.recovery(db, user.id)),
        suggestion=_suggestion_out(suggestion),
        week_power_load=Decimal(str(round(sum(v[0] for v in week.values()), 1))),
    )


@router.get("/calculation-versions", response_model=CalculationVersionList)
async def get_calculation_versions(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> CalculationVersionList:
    """Which maths produced which number (ADR-10 §2)."""
    rows = await training_service.calculation_registry(db)
    return CalculationVersionList(items=[CalculationVersionOut(**row) for row in rows])


# ---------------------------------------------------------------------------
# Workouts
# ---------------------------------------------------------------------------


workout_router = APIRouter(prefix="/workouts", tags=["workouts"])


def _workout_out(workout: Workout) -> WorkoutOut:
    return WorkoutOut(
        id=workout.id,
        name=workout.name,
        description=workout.description,
        discipline=workout.discipline,
        goal=workout.goal,
        status=workout.status,
        version=workout.version,
        target_duration_s=workout.target_duration_s,
        target_load=workout.target_load,
        intensity_note=workout.intensity_note,
        steps=[
            WorkoutStepDetail(
                id=s.id,
                seq=s.seq,
                step_type=s.step_type,
                label=s.label,
                duration_s=s.duration_s,
                repeat_count=s.repeat_count,
                target_zone=s.target_zone,
                target_power_low_w=s.target_power_low_w,
                target_power_high_w=s.target_power_high_w,
                target_hr_low_bpm=s.target_hr_low_bpm,
                target_hr_high_bpm=s.target_hr_high_bpm,
            )
            for s in workout.steps
        ],
        created_at=workout.created_at,
        updated_at=workout.updated_at,
    )


@workout_router.post("", response_model=WorkoutOut, status_code=201)
async def create_workout(
    data: WorkoutCreate,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> WorkoutOut:
    _limit(request, user, key="workouts")
    try:
        workout = await training_service.create_workout(db, user.id, data.model_dump())
    except TrainingError as e:
        raise _err(e) from e
    return _workout_out(workout)


@workout_router.get("", response_model=WorkoutPage)
async def list_workouts(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=MAX_LIST),
    status: str | None = None,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> WorkoutPage:
    items, total = await training_service.list_workouts(db, user.id, page, page_size, status)
    return WorkoutPage(
        items=[_workout_out(w) for w in items], total=total, page=page, page_size=page_size
    )


@workout_router.get("/{workout_id}", response_model=WorkoutOut)
async def get_workout(
    workout_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> WorkoutOut:
    try:
        workout = await training_service.get_workout(db, user.id, workout_id)
    except TrainingError as e:
        raise _err(e) from e
    return _workout_out(workout)


@workout_router.patch("/{workout_id}", response_model=WorkoutOut)
async def update_workout(
    workout_id: uuid.UUID,
    data: WorkoutUpdate,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> WorkoutOut:
    """expected_version is required; mismatch -> 409 (optimistic locking)."""
    _limit(request, user, key="workouts")
    try:
        workout = await training_service.get_workout(db, user.id, workout_id)
        workout = await training_service.update_workout(
            db, workout, data.model_dump(exclude_unset=True)
        )
    except TrainingError as e:
        raise _err(e) from e
    return _workout_out(workout)


@workout_router.delete("/{workout_id}", response_model=WorkoutOut)
async def delete_workout(
    workout_id: uuid.UUID,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> WorkoutOut:
    _limit(request, user, key="workouts")
    try:
        workout = await training_service.get_workout(db, user.id, workout_id)
        workout = await training_service.delete_workout(db, workout)
    except TrainingError as e:
        raise _err(e) from e
    return _workout_out(workout)

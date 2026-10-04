"""Context assembly — the only place the Coach is allowed to learn anything.

Every value here is read from the deterministic services through their
owner-scoped accessors. Nothing is re-derived, re-averaged, or re-scaled, and no
GPS coordinate is ever read into the context (ADR-11 §1, §3).
"""

import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.training import TrainingActivity, TrainingLoad
from app.models.user import User
from app.schemas.coach import (
    CoachContextOut,
    CoachEntityRef,
    CoachIntent,
    CoachMetricRef,
)
from app.services import ride_service, route_service, training_service
from app.services import training_calc as calc

# ADR-10 §3 caps. Mirrored as data for the prompt; the authority for any
# prescription remains calc.suggest_intensity_target().
MAX_SESSION_INCREASE_PCT = Decimal(str(calc.MAX_SESSION_LOAD_DELTA * 100))
MAX_TARGET_SHARE_PCT = Decimal(30)


@dataclass
class BuiltContext:
    subject: CoachEntityRef
    context: CoachContextOut
    latest_load: float | None = None
    weekly_load: float | None = None
    extra_entities: list[CoachEntityRef] = field(default_factory=list)


def _num(value: Decimal | float | None) -> float | None:
    if value is None:
        return None
    number = float(value)
    return int(number) if number == int(number) else round(number, 2)


def _metric(
    key: str,
    value: Decimal | float | None,
    unit: str,
    *,
    version: str | None,
    source: str,
) -> CoachMetricRef:
    return CoachMetricRef(
        key=key,
        value=None if value is None else Decimal(str(_num(value))),
        unit=unit,
        version=version,
        source=source,
    )


def to_prompt_dict(built: BuiltContext, intent: CoachIntent) -> dict[str, Any]:
    return {
        "intent": intent.value,
        "subject": built.subject.model_dump(mode="json"),
        "metrics": [m.model_dump(mode="json") for m in built.context.metrics],
        "notes": list(built.context.notes),
        "unavailable": list(built.context.unavailable),
        "limits": {
            "max_session_load_increase_pct": float(MAX_SESSION_INCREASE_PCT),
            "max_target_share_of_weekly_load_pct": float(MAX_TARGET_SHARE_PCT),
        },
    }


def _add(metrics: list[CoachMetricRef], item: CoachMetricRef) -> None:
    if len(metrics) < 64:
        metrics.append(item)


def _ride_entity(ride: Any) -> CoachEntityRef:
    return CoachEntityRef(
        type="ride", id=str(ride.id), label=f"Ride on {ride.started_at.date().isoformat()}"
    )


async def build_ride_context(db: AsyncSession, user: User, ride_id: uuid.UUID) -> BuiltContext:
    """Owner-scoped ride context. A foreign ride raises, so it is a 404."""
    ride = await ride_service.get_owned(db, user.id, ride_id)
    subject = _ride_entity(ride)
    extra: list[CoachEntityRef] = []
    metrics: list[CoachMetricRef] = []
    notes: list[str] = []
    unavailable: list[str] = []
    version = calc.ACTIVITY_ANALYSIS_VERSION

    if ride.route_id is not None:
        try:
            route = await route_service.get_readable(db, user.id, ride.route_id)
            extra.append(CoachEntityRef(type="route", id=str(route.id), label=route.name[:120]))
        except route_service.RouteError:
            unavailable.append("The route linked to this ride is no longer available.")

    _add(
        metrics,
        _metric("ride.distance_m", ride.distance_m, "m", version=None, source="ride"),
    )
    _add(
        metrics,
        _metric("ride.elevation_gain_m", ride.elevation_gain_m, "m", version=None, source="ride"),
    )
    _add(
        metrics,
        _metric("ride.moving_seconds", ride.moving_seconds, "s", version=None, source="ride"),
    )
    _add(
        metrics,
        _metric("ride.elapsed_seconds", ride.elapsed_seconds, "s", version=None, source="ride"),
    )

    try:
        activity = await training_service.get_activity_by_ride(db, user.id, ride_id)
    except training_service.TrainingError:
        activity = None

    if activity is None:
        notes.append("This ride has not been analysed by the training engine yet.")
        unavailable.append("Training metrics for this ride are not available.")
        return BuiltContext(
            subject=subject,
            context=CoachContextOut(metrics=metrics, notes=notes, unavailable=unavailable),
            extra_entities=extra,
        )

    version = activity.analysis_version
    _activity_metrics(metrics, activity, version)
    _activity_notes(notes, unavailable, activity)
    await _zone_metrics(db, metrics, activity, version)

    return BuiltContext(
        subject=subject,
        context=CoachContextOut(metrics=metrics, notes=notes, unavailable=unavailable),
        extra_entities=extra,
    )


def _activity_metrics(
    metrics: list[CoachMetricRef], activity: TrainingActivity, version: str
) -> None:
    source = "training_activity"
    for key, value, unit in (
        ("ride.moving_seconds", activity.moving_seconds, "s"),
        ("ride.elapsed_seconds", activity.elapsed_seconds, "s"),
        ("ride.distance_m", activity.distance_m, "m"),
        ("ride.elevation_gain_m", activity.elevation_gain_m, "m"),
        ("power.average_w", activity.average_power_w, "w"),
        ("power.normalized_w", activity.normalized_power_w, "w"),
        ("power.max_w", activity.max_power_w, "w"),
        ("power.intensity_factor", activity.intensity_factor, "ratio"),
        ("power.load", activity.power_load, "load"),
        ("hr.average_bpm", activity.average_hr_bpm, "bpm"),
        ("hr.max_bpm", activity.max_hr_bpm, "bpm"),
        ("hr.load", activity.hr_load, "load"),
        ("cadence.average_rpm", activity.average_cadence_rpm, "rpm"),
        ("ftp.effective_w", activity.effective_ftp_w, "w"),
        ("analysis.powered_seconds", activity.power_seconds, "s"),
    ):
        _add(metrics, _metric(key, value, unit, version=version, source=source))


def _activity_notes(notes: list[str], unavailable: list[str], activity: TrainingActivity) -> None:
    if activity.insufficient_data:
        notes.append("The engine flagged this activity as having insufficient data.")
    if not activity.has_power:
        unavailable.append("No power data was recorded for this ride.")
    if not activity.has_heart_rate:
        unavailable.append("No heart-rate data was recorded for this ride.")
    if not activity.has_cadence:
        unavailable.append("No cadence data was recorded for this ride.")
    if activity.effective_ftp_w is None:
        unavailable.append(
            "No effective FTP is set, so intensity factor and power zones are unavailable."
        )
    if activity.power_load is not None:
        notes.append(
            f"Power load for this activity is {_fmt(activity.power_load)}. "
            "This is a CycleCoach score and is not TrainingPeaks TSS."
        )
    if activity.hr_load is not None:
        notes.append(f"Heart-rate load for this activity is {_fmt(activity.hr_load)}.")


async def _zone_metrics(
    db: AsyncSession,
    metrics: list[CoachMetricRef],
    activity: TrainingActivity,
    version: str,
) -> None:
    rows = await training_service.activity_zones(db, activity.id)
    for row in rows:
        kind = "power" if row.kind == "power" else "hr"
        _add(
            metrics,
            _metric(
                f"zones.{kind}.z{row.zone}_seconds",
                row.seconds,
                "s",
                version=version,
                source="training_activity_zone",
            ),
        )


def _fmt(value: Decimal | float | None) -> str:
    if value is None:
        return "unavailable"
    return f"{float(value):.1f}".rstrip("0").rstrip(".")


async def build_workout_context(
    db: AsyncSession, user: User, workout_id: uuid.UUID
) -> BuiltContext:
    workout = await training_service.get_workout(db, user.id, workout_id)
    subject = CoachEntityRef(type="workout", id=str(workout.id), label=workout.name[:120])
    metrics: list[CoachMetricRef] = []
    notes: list[str] = []
    unavailable: list[str] = []
    source = "workout"

    _add(
        metrics,
        _metric(
            "workout.target_duration_s", workout.target_duration_s, "s", version=None, source=source
        ),
    )
    _add(
        metrics,
        _metric("workout.target_load", workout.target_load, "load", version=None, source=source),
    )
    _add(
        metrics,
        _metric("workout.step_count", len(workout.steps), "count", version=None, source=source),
    )

    total = sum(step.duration_s * step.repeat_count for step in workout.steps)
    _add(metrics, _metric("workout.planned_seconds", total, "s", version=None, source=source))

    zones = [step.target_zone for step in workout.steps if step.target_zone is not None]
    if zones:
        _add(
            metrics,
            _metric("workout.lowest_target_zone", min(zones), "zone", version=None, source=source),
        )
        _add(
            metrics,
            _metric("workout.highest_target_zone", max(zones), "zone", version=None, source=source),
        )
    else:
        unavailable.append("This workout has no target zones set on its steps.")

    if workout.target_duration_s is not None and total and workout.target_duration_s != total:
        notes.append(
            "The stated target duration and the sum of the steps differ; both are reported as recorded."
        )
    if workout.goal:
        notes.append(f"The recorded goal is: {workout.goal[:200]}.")
    if workout.steps:
        first = workout.steps[0]
        notes.append(
            f"The first step is {first.label[:120]} in zone {first.target_zone}."
            if first.target_zone
            else f"The first step is {first.label[:120]}."
        )
    if not workout.steps:
        unavailable.append("This workout has no steps recorded.")

    return BuiltContext(
        subject=subject,
        context=CoachContextOut(metrics=metrics, notes=notes, unavailable=unavailable),
    )


async def build_weekly_context(db: AsyncSession, user: User) -> BuiltContext:
    today, week_power, activities, loads = await _week_window(db, user)
    metrics: list[CoachMetricRef] = []
    notes: list[str] = []
    unavailable: list[str] = []
    source = "training_load"

    _add(
        metrics,
        _metric("week.power_load", week_power, "load", version=None, source="training_activity"),
    )
    _add(
        metrics,
        _metric(
            "week.activity_count",
            len(activities),
            "count",
            version=None,
            source="training_activity",
        ),
    )
    moving = sum(float(a.moving_seconds or 0) for a in activities)
    _add(
        metrics,
        _metric(
            "week.moving_seconds", round(moving), "s", version=None, source="training_activity"
        ),
    )

    latest_load: TrainingLoad | None = None
    if loads:
        latest_load = loads[-1]
        _add(
            metrics,
            _metric(
                "load.ctl", latest_load.ctl, "load", version=latest_load.load_version, source=source
            ),
        )
        _add(
            metrics,
            _metric(
                "load.atl", latest_load.atl, "load", version=latest_load.load_version, source=source
            ),
        )
        _add(
            metrics,
            _metric(
                "load.tsb", latest_load.tsb, "load", version=latest_load.load_version, source=source
            ),
        )
        _add(
            metrics,
            _metric(
                "load.latest_day_power_load",
                latest_load.power_load,
                "load",
                version=latest_load.load_version,
                source=source,
            ),
        )
    if not activities and not loads:
        unavailable.append("No recorded activity in the last seven days.")

    recovery = await training_service.recovery(db, user.id)
    notes.append(f"Recovery signal code: {recovery.get('code', 'unavailable')}.")
    notes.append("Fitness and fatigue are load proxies, not medical or readiness measures.")

    current_load = (
        float(latest_load.power_load)
        if latest_load is not None and latest_load.power_load is not None
        else None
    )
    target = calc.suggest_intensity_target(current_load, weekly_load=float(week_power) or None)
    if target.get("target_load") is not None:
        _add(
            metrics,
            _metric(
                "target.next_session_load",
                target["target_load"],
                "load",
                version=target.get("version"),
                source="training_calc",
            ),
        )
        notes.append(
            f"The engine's bounded target for the next session is {_fmt(target['target_load'])} "
            f"({target.get('reason')})."
        )
    else:
        unavailable.append("There is not enough recorded load to suggest a next-session target.")

    return BuiltContext(
        subject=CoachEntityRef(type="week", id=today.isoformat(), label="Last 7 days"),
        context=CoachContextOut(metrics=metrics, notes=notes, unavailable=unavailable),
        latest_load=current_load,
        weekly_load=float(week_power) or None,
    )


async def _week_window(
    db: AsyncSession, user: User
) -> tuple[date, float, list[TrainingActivity], list[TrainingLoad]]:
    _, zone = await training_service.get_timezone(db, user.id)
    today = datetime.now(zone).date()
    start = today - timedelta(days=6)
    activities, _ = await training_service.list_activities(
        db, user.id, 1, settings.AI_MAX_CONTEXT_ACTIVITIES, start, today
    )
    loads = await training_service.list_loads(db, user.id, start, today)
    totals = await training_service.daily_loads(db, user.id, start, today)
    week_power = round(sum(v[0] for v in totals.values()), 1)
    return today, week_power, activities, loads

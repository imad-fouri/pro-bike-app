"""Ride service: lifecycle, ownership, chunk ingest, deterministic finish."""

import uuid
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.bike import Bike, BikeStatus
from app.models.ride import Ride, RidePoint, RideStatus
from app.schemas.ride import PointIn
from app.services import gps_engine, route_service
from app.services.gps_engine import EngineState, GpsConfig, Observation
from app.services.route_service import RouteError

# recording → paused → recording → completed|discarded
_TRANSITIONS: dict[RideStatus, set[RideStatus]] = {
    RideStatus.RECORDING: {RideStatus.PAUSED, RideStatus.COMPLETED, RideStatus.DISCARDED},
    RideStatus.PAUSED: {RideStatus.RECORDING, RideStatus.COMPLETED, RideStatus.DISCARDED},
    RideStatus.COMPLETED: set(),
    RideStatus.DISCARDED: set(),
}


def _now() -> datetime:
    return datetime.now(UTC)


class RideError(Exception):
    def __init__(self, code: str, message: str, status: int = 404) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


def _summary_of(ride: Ride, accepted_count: int) -> dict:
    avg = float(ride.distance_m) / ride.moving_seconds if ride.moving_seconds > 0 else 0.0
    return {
        "distance_m": ride.distance_m,
        "elevation_gain_m": ride.elevation_gain_m,
        "elevation_loss_m": ride.elevation_loss_m,
        "moving_seconds": ride.moving_seconds,
        "elapsed_seconds": ride.elapsed_seconds,
        "average_speed_m_s": Decimal(str(round(avg, 3))),
        "max_speed_m_s": ride.max_speed_m_s,
        "accepted_points": accepted_count,
    }


async def _accepted_count(db: AsyncSession, ride_id: uuid.UUID) -> int:
    res = await db.execute(
        select(func.count())
        .select_from(RidePoint)
        .where(RidePoint.ride_id == ride_id, RidePoint.accepted.is_(True))
    )
    return res.scalar_one()


async def get_owned(db: AsyncSession, user_id: uuid.UUID, ride_id: uuid.UUID) -> Ride:
    res = await db.execute(select(Ride).where(Ride.id == ride_id, Ride.user_id == user_id))
    ride = res.scalar_one_or_none()
    if ride is None:
        raise RideError("RIDE_NOT_FOUND", "Ride not found.", 404)
    return ride


async def resolve_route(
    db: AsyncSession,
    user_id: uuid.UUID,
    route_id: uuid.UUID,
    route_version: int | None = None,
) -> tuple[uuid.UUID, int]:
    """Readability rules from route_service apply (private foreign → 404).

    Returns (route_id, pinned_version_no); explicit version must exist.
    """
    try:
        route = await route_service.get_readable(db, user_id, route_id)
        version = await route_service.get_version(db, route, route_version or route.current_version)
    except RouteError as e:
        raise RideError(e.code, e.message, e.status) from e
    return route.id, version.version_no


async def create(
    db: AsyncSession,
    user_id: uuid.UUID,
    bike_id: uuid.UUID,
    client_uuid: uuid.UUID,
    route_id: uuid.UUID | None = None,
    route_version: int | None = None,
) -> tuple[Ride, bool]:
    """Returns (ride, created). Same client_uuid → existing ride (idempotent)."""
    existing = await db.execute(
        select(Ride).where(Ride.user_id == user_id, Ride.client_ride_uuid == client_uuid)
    )
    ride = existing.scalar_one_or_none()
    if ride is not None:
        return ride, False
    bike = await db.get(Bike, bike_id)
    if bike is None or bike.owner_id != user_id or bike.deleted_at is not None:
        raise RideError("BIKE_NOT_FOUND", "Bike not found.", 404)
    if bike.status != BikeStatus.ACTIVE:
        raise RideError("BIKE_ARCHIVED", "Restore the bike before starting a ride.", 409)
    pinned_route: uuid.UUID | None = None
    pinned_version: int | None = None
    if route_id is not None:
        pinned_route, pinned_version = await resolve_route(db, user_id, route_id, route_version)
    now = _now()
    ride = Ride(
        user_id=user_id,
        bike_id=bike_id,
        client_ride_uuid=client_uuid,
        status=RideStatus.RECORDING,
        started_at=now,
        created_at=now,
        updated_at=now,
        route_id=pinned_route,
        route_version=pinned_version,
    )
    db.add(ride)
    try:
        await db.commit()
    except IntegrityError as e:  # lost race on client_uuid → return winner
        await db.rollback()
        dup = await db.execute(
            select(Ride).where(Ride.user_id == user_id, Ride.client_ride_uuid == client_uuid)
        )
        ride = dup.scalar_one_or_none()
        if ride is None:
            raise RideError("CONFLICT", "Ride already exists.", 409) from e
        return ride, False
    await db.refresh(ride)
    return ride, True


async def transition(db: AsyncSession, ride: Ride, target: RideStatus) -> Ride:
    if target not in _TRANSITIONS[ride.status]:
        raise RideError(
            "INVALID_TRANSITION",
            f"Cannot move ride from {ride.status.value} to {target.value}.",
            409,
        )
    ride.status = target
    ride.updated_at = _now()
    if target in (RideStatus.COMPLETED, RideStatus.DISCARDED):
        ride.ended_at = ride.ended_at or _now()
        if target == RideStatus.COMPLETED:
            await _finalize(db, ride)
    await db.commit()
    await db.refresh(ride)
    if target == RideStatus.COMPLETED:
        # A completed ride automatically feeds the training engine (Phase 6)
        # and the challenge engine (WS-RC). Imported lazily to keep the domain
        # modules acyclic, and neither can ever fail the ride: both log and
        # defer repair, exactly like sync_ride's reanalyze endpoint.
        from app.services import challenge_service, training_service

        await training_service.sync_ride(db, ride)
        await challenge_service.on_ride_completed(db, ride)
    return ride


async def associate_route(
    db: AsyncSession,
    ride: Ride,
    route_id: uuid.UUID | None,
    route_version: int | None = None,
) -> Ride:
    """Link (or detach, route_id=None) a planned route. Historical rides are
    never rewritten: only the reference on this owned ride changes (§49)."""
    if ride.status == RideStatus.DISCARDED:
        raise RideError("RIDE_DISCARDED", "A discarded ride cannot be linked to a route.", 409)
    if route_id is None:
        ride.route_id = None
        ride.route_version = None
    else:
        ride.route_id, ride.route_version = await resolve_route(
            db, ride.user_id, route_id, route_version
        )
    ride.updated_at = _now()
    await db.commit()
    await db.refresh(ride)
    return ride


def _obs(p: PointIn) -> Observation:
    ts = (
        p.recorded_at.timestamp()
        if p.recorded_at.tzinfo
        else p.recorded_at.replace(tzinfo=UTC).timestamp()
    )
    return Observation(
        seq=p.seq,
        lat=p.lat,
        lon=p.lon,
        recorded_at=ts,
        alt=p.alt,
        accuracy=p.accuracy,
        speed=p.speed,
        heading=p.heading,
    )


async def ingest(
    db: AsyncSession, ride: Ride, chunk: list[PointIn], cfg: GpsConfig = GpsConfig()
) -> tuple[int, list[dict], int]:
    """Validate + store a chunk. Returns (accepted, rejected, duplicates).

    Dedupes by (ride_id, client_point_uuid) and (ride_id, seq): retries are
    safe. Points arriving while paused are rejected (client queues locally).
    """
    if ride.status == RideStatus.PAUSED:
        raise RideError("RIDE_PAUSED", "Ride is paused. Resume to upload points.", 409)
    if ride.status in (RideStatus.COMPLETED, RideStatus.DISCARDED):
        raise RideError("RIDE_FINISHED", "Ride is already finished.", 409)

    # Serialize concurrent uploads of the SAME ride on its row: two chunks
    # racing the read-then-write dedupe below would otherwise both pass and
    # one would die on the unique constraints. `populate_existing` refreshes
    # the caller's instance in place so later reads see committed state.
    locked = await db.execute(
        select(Ride)
        .where(Ride.id == ride.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    ride = locked.scalar_one()
    # Re-check on the locked row: the status may have changed between the
    # router's read and this lock (e.g. a concurrent pause/finish).
    if ride.status == RideStatus.PAUSED:
        raise RideError("RIDE_PAUSED", "Ride is paused. Resume to upload points.", 409)
    if ride.status in (RideStatus.COMPLETED, RideStatus.DISCARDED):
        raise RideError("RIDE_FINISHED", "Ride is already finished.", 409)

    # Existing keys for idempotent resume.
    res = await db.execute(
        select(RidePoint.client_point_uuid, RidePoint.seq).where(RidePoint.ride_id == ride.id)
    )
    seen_uuids = {r[0] for r in res.all()}
    res2 = await db.execute(select(RidePoint.seq).where(RidePoint.ride_id == ride.id))
    seen_seqs = set(res2.scalars())

    # Rebuild incremental state from last accepted point (cheap tail query).
    tail = await db.execute(
        select(RidePoint)
        .where(RidePoint.ride_id == ride.id, RidePoint.accepted.is_(True))
        .order_by(RidePoint.seq.desc())
        .limit(1)
    )
    last = tail.scalar_one_or_none()
    state = EngineState()
    if last is not None:
        # Anchor from the last accepted fix; full history recomputed at finish.
        state.last_lat, state.last_lon = float(last.lat), float(last.lon)
        state.last_time = last.recorded_at.timestamp()
        state.last_seq = last.seq
        state.first_time = ride.started_at.timestamp()
        state.distance_m, state.gain_m = float(ride.distance_m), float(ride.elevation_gain_m)
        state.loss_m, state.moving_s = float(ride.elevation_loss_m), float(ride.moving_seconds)
        state.max_speed_m_s = float(ride.max_speed_m_s)
        state.ele_baseline = float(last.alt) if last.alt is not None else None
    else:
        state.first_time = ride.started_at.timestamp()

    accepted, rejected, duplicates = 0, [], 0
    new_rows: list[RidePoint] = []
    for p in sorted(chunk, key=lambda x: x.seq):
        if p.client_point_uuid in seen_uuids or p.seq in seen_seqs:
            duplicates += 1
            continue
        verdict = gps_engine.process(state, _obs(p), cfg)
        row = RidePoint(
            ride_id=ride.id,
            seq=p.seq,
            client_point_uuid=p.client_point_uuid,
            lat=Decimal(str(p.lat)),
            lon=Decimal(str(p.lon)),
            alt=Decimal(str(p.alt)) if p.alt is not None else None,
            accuracy=Decimal(str(p.accuracy)) if p.accuracy is not None else None,
            speed=Decimal(str(p.speed)) if p.speed is not None else None,
            heading=Decimal(str(p.heading)) if p.heading is not None else None,
            power_w=Decimal(str(p.power_w)) if p.power_w is not None else None,
            hr_bpm=Decimal(str(p.hr_bpm)) if p.hr_bpm is not None else None,
            cadence_rpm=Decimal(str(p.cadence_rpm)) if p.cadence_rpm is not None else None,
            recorded_at=p.recorded_at,
            accepted=verdict.accepted,
            reject_reason=None if verdict.accepted else verdict.reason,
            created_at=_now(),
        )
        new_rows.append(row)
        seen_uuids.add(p.client_point_uuid)
        seen_seqs.add(p.seq)
        if verdict.accepted:
            accepted += 1
            if ride.start_lat is None:
                ride.start_lat, ride.start_lon = row.lat, row.lon
            ride.end_lat, ride.end_lon = row.lat, row.lon
        else:
            rejected.append({"seq": p.seq, "reason": verdict.reason})

    db.add_all(new_rows)
    ride.distance_m = Decimal(str(round(state.distance_m, 2)))
    ride.elevation_gain_m = Decimal(str(round(state.gain_m, 2)))
    ride.elevation_loss_m = Decimal(str(round(state.loss_m, 2)))
    ride.moving_seconds = int(state.moving_s)
    ride.max_speed_m_s = Decimal(str(round(state.max_speed_m_s, 3)))
    ride.updated_at = _now()
    try:
        await db.commit()
    except IntegrityError:
        # Backstop for the row lock above: a lost race surfaces here instead
        # of as a 500. Nothing from this chunk was stored (all-or-nothing), so
        # the client can safely re-read state and retry; report conflict, not
        # internal error.
        await db.rollback()
        raise RideError(
            "CHUNK_CONFLICT",
            "Chunk conflicts with a concurrent upload; retry with fresh state.",
            409,
        ) from None
    return accepted, rejected, duplicates


async def _finalize(db: AsyncSession, ride: Ride) -> None:
    """Deterministic summary from accepted points ordered by seq."""
    res = await db.execute(
        select(RidePoint)
        .where(RidePoint.ride_id == ride.id, RidePoint.accepted.is_(True))
        .order_by(RidePoint.seq)
    )
    obs = [
        Observation(
            seq=p.seq,
            lat=float(p.lat),
            lon=float(p.lon),
            recorded_at=p.recorded_at.timestamp(),
            alt=float(p.alt) if p.alt is not None else None,
            accuracy=float(p.accuracy) if p.accuracy is not None else None,
            speed=float(p.speed) if p.speed is not None else None,
        )
        for p in res.scalars()
    ]
    summary = gps_engine.recompute(obs)
    ride.distance_m = Decimal(str(summary["distance_m"]))
    ride.elevation_gain_m = Decimal(str(summary["elevation_gain_m"]))
    ride.elevation_loss_m = Decimal(str(summary["elevation_loss_m"]))
    ride.moving_seconds = summary["moving_seconds"]
    ride.max_speed_m_s = Decimal(str(summary["max_speed_m_s"]))
    ended = ride.ended_at or _now()
    ride.ended_at = ended
    ride.elapsed_seconds = max(0, int((ended - ride.started_at).total_seconds()))
    ride.updated_at = _now()


async def list_page(
    db: AsyncSession,
    user_id: uuid.UUID,
    page: int,
    page_size: int,
    status: RideStatus | None,
) -> tuple[list[Ride], int]:
    stmt = select(Ride).where(Ride.user_id == user_id)
    count_stmt = select(func.count()).select_from(Ride).where(Ride.user_id == user_id)
    if status is not None:
        stmt = stmt.where(Ride.status == status)
        count_stmt = count_stmt.where(Ride.status == status)
    total = (await db.execute(count_stmt)).scalar_one()
    stmt = stmt.order_by(Ride.started_at.desc()).offset((page - 1) * page_size).limit(page_size)
    rides: list[Ride] = list((await db.execute(stmt)).scalars())
    return rides, total


async def summarize(db: AsyncSession, ride: Ride) -> dict:
    count = await _accepted_count(db, ride.id)
    if ride.status == RideStatus.COMPLETED and ride.ended_at is not None:
        ride.elapsed_seconds = max(0, int((ride.ended_at - ride.started_at).total_seconds()))
    elif ride.ended_at is None:
        ride.elapsed_seconds = max(0, int((_now() - ride.started_at).total_seconds()))
    return _summary_of(ride, count)

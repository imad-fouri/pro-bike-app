"""Ride recording API. Owner-only; no social exposure in Phase 4."""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core.rate_limit import allow
from app.db.session import get_db
from app.models.ride import RideStatus
from app.models.user import User
from app.schemas.ride import (
    ChunkResult,
    PointsChunk,
    RejectedPoint,
    RideCreate,
    RideOut,
    RidePage,
    RideRouteLink,
    RideSummary,
)
from app.services import ride_service
from app.services.ride_service import RideError

router = APIRouter(prefix="/rides", tags=["rides"])
MAX_LIST = 100


def _limit(request: Request, user: User, key: str = "rides") -> None:
    ident = request.client.host if request.client else "unknown"
    if not allow(f"{key}:{user.id}:{ident}", 120, 60):
        raise HTTPException(status_code=429, detail="Too many requests.")


def _err(e: RideError) -> HTTPException:
    return HTTPException(status_code=e.status, detail={"code": e.code, "message": e.message})


async def _out(db: AsyncSession, ride: object) -> RideOut:
    from app.models.ride import Ride

    assert isinstance(ride, Ride)
    summary = await ride_service.summarize(db, ride)
    return RideOut(
        id=ride.id,
        bike_id=ride.bike_id,
        status=ride.status,
        started_at=ride.started_at,
        ended_at=ride.ended_at,
        summary=RideSummary(**summary),
        start_lat=ride.start_lat,
        start_lon=ride.start_lon,
        end_lat=ride.end_lat,
        end_lon=ride.end_lon,
        route_id=ride.route_id,
        route_version=ride.route_version,
    )


@router.post("", status_code=201)
async def create_ride(
    data: RideCreate,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> RideOut:
    _limit(request, user)
    try:
        ride, _ = await ride_service.create(
            db,
            user.id,
            data.bike_id,
            data.client_ride_uuid,
            route_id=data.route_id,
            route_version=data.route_version,
        )
    except RideError as e:
        raise _err(e) from e
    return await _out(db, ride)


@router.get("")
async def list_rides(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=MAX_LIST),
    status: RideStatus | None = None,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> RidePage:
    rides, total = await ride_service.list_page(db, user.id, page, page_size, status)
    return RidePage(
        items=[await _out(db, r) for r in rides], total=total, page=page, page_size=page_size
    )


@router.get("/{ride_id}")
async def get_ride(
    ride_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> RideOut:
    try:
        ride = await ride_service.get_owned(db, user.id, ride_id)
    except RideError as e:
        raise _err(e) from e
    return await _out(db, ride)


@router.post("/{ride_id}/route", response_model=RideOut)
async def set_ride_route(
    ride_id: uuid.UUID,
    data: RideRouteLink,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> RideOut:
    """Pin/detach a planned route on an owned ride (404 if route unreadable)."""
    _limit(request, user)
    try:
        ride = await ride_service.get_owned(db, user.id, ride_id)
        ride = await ride_service.associate_route(db, ride, data.route_id, data.route_version)
    except RideError as e:
        raise _err(e) from e
    return await _out(db, ride)


@router.post("/{ride_id}/points")
async def upload_points(
    ride_id: uuid.UUID,
    chunk: PointsChunk,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ChunkResult:
    _limit(request, user, "ride-points")
    try:
        ride = await ride_service.get_owned(db, user.id, ride_id)
        accepted, rejected, duplicates = await ride_service.ingest(db, ride, list(chunk.points))
    except RideError as e:
        raise _err(e) from e
    summary = await ride_service.summarize(db, ride)
    return ChunkResult(
        accepted=accepted,
        rejected=[RejectedPoint(seq=r["seq"], reason=r["reason"]) for r in rejected],
        duplicates=duplicates,
        summary=RideSummary(**summary),
    )


@router.post("/{ride_id}/pause")
async def pause_ride(
    ride_id: uuid.UUID,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> RideOut:
    _limit(request, user)
    try:
        ride = await ride_service.get_owned(db, user.id, ride_id)
        ride = await ride_service.transition(db, ride, RideStatus.PAUSED)
    except RideError as e:
        raise _err(e) from e
    return await _out(db, ride)


@router.post("/{ride_id}/resume")
async def resume_ride(
    ride_id: uuid.UUID,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> RideOut:
    _limit(request, user)
    try:
        ride = await ride_service.get_owned(db, user.id, ride_id)
        ride = await ride_service.transition(db, ride, RideStatus.RECORDING)
    except RideError as e:
        raise _err(e) from e
    return await _out(db, ride)


@router.post("/{ride_id}/finish")
async def finish_ride(
    ride_id: uuid.UUID,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> RideOut:
    _limit(request, user)
    try:
        ride = await ride_service.get_owned(db, user.id, ride_id)
        ride = await ride_service.transition(db, ride, RideStatus.COMPLETED)
    except RideError as e:
        raise _err(e) from e
    return await _out(db, ride)


@router.post("/{ride_id}/discard")
async def discard_ride(
    ride_id: uuid.UUID,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> RideOut:
    _limit(request, user)
    try:
        ride = await ride_service.get_owned(db, user.id, ride_id)
        ride = await ride_service.transition(db, ride, RideStatus.DISCARDED)
    except RideError as e:
        raise _err(e) from e
    return await _out(db, ride)

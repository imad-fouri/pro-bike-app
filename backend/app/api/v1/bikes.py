"""Bike CRUD. Ownership enforced server-side; foreign bikes → 404."""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core.rate_limit import allow
from app.db.session import get_db
from app.models.bike import BikeCategory, BikeStatus
from app.models.user import User
from app.schemas.bike import (
    BikeCreate,
    BikeOut,
    BikePage,
    BikeUpdate,
    SortField,
    SortOrder,
)
from app.services import bike_service
from app.services.bike_service import BikeError

router = APIRouter(prefix="/bikes", tags=["bikes"])


def _limit(request: Request, user: User) -> None:
    ident = request.client.host if request.client else "unknown"
    if not allow(f"bikes:{user.id}:{ident}", 120, 60):
        raise HTTPException(status_code=429, detail="Too many requests.")


def _err(e: BikeError) -> HTTPException:
    return HTTPException(status_code=e.status, detail={"code": e.code, "message": e.message})


def _out(bike: object) -> BikeOut:
    return BikeOut.model_validate(bike, from_attributes=True)


@router.post("", response_model=BikeOut, status_code=201)
async def create_bike(
    data: BikeCreate,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> BikeOut:
    _limit(request, user)
    bike = await bike_service.create(db, user.id, data)
    return _out(bike)


@router.get("", response_model=BikePage)
async def list_bikes(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    category: BikeCategory | None = None,
    status: BikeStatus | None = BikeStatus.ACTIVE,
    status_all: bool = Query(default=False),
    sort: SortField = "created_at",
    order: SortOrder = "desc",
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> BikePage:
    items, total = await bike_service.list_page(
        db,
        user.id,
        page,
        page_size,
        category,
        None if status_all else status,
        sort,
        order,
    )
    return BikePage(items=[_out(b) for b in items], total=total, page=page, page_size=page_size)


@router.get("/{bike_id}", response_model=BikeOut)
async def get_bike(
    bike_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> BikeOut:
    try:
        bike = await bike_service.get_owned(db, user.id, bike_id)
    except BikeError as e:
        raise _err(e) from e
    return _out(bike)


@router.patch("/{bike_id}", response_model=BikeOut)
async def update_bike(
    bike_id: uuid.UUID,
    data: BikeUpdate,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> BikeOut:
    _limit(request, user)
    try:
        bike = await bike_service.get_owned(db, user.id, bike_id)
        bike = await bike_service.update(db, bike, data)
    except BikeError as e:
        raise _err(e) from e
    return _out(bike)


@router.post("/{bike_id}/archive", response_model=BikeOut)
async def archive_bike(
    bike_id: uuid.UUID,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> BikeOut:
    _limit(request, user)
    try:
        bike = await bike_service.get_owned(db, user.id, bike_id)
        bike = await bike_service.archive(db, bike)
    except BikeError as e:
        raise _err(e) from e
    return _out(bike)


@router.post("/{bike_id}/restore", response_model=BikeOut)
async def restore_bike(
    bike_id: uuid.UUID,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> BikeOut:
    _limit(request, user)
    try:
        bike = await bike_service.get_owned(db, user.id, bike_id)
        bike = await bike_service.restore(db, bike)
    except BikeError as e:
        raise _err(e) from e
    return _out(bike)


@router.delete("/{bike_id}", response_model=BikeOut)
async def delete_bike(
    bike_id: uuid.UUID,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> BikeOut:
    """Soft delete (deleted_at). Idempotent; historical refs stay intact."""
    _limit(request, user)
    try:
        bike = await bike_service.get_owned(db, user.id, bike_id, include_deleted=True)
        bike = await bike_service.soft_delete(db, bike)
    except BikeError as e:
        raise _err(e) from e
    return _out(bike)

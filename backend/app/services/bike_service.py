"""Bike service. Ownership enforced on every operation (404 if foreign)."""

import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.bike import Bike, BikeCategory, BikeStatus
from app.schemas.bike import BikeCreate, BikeUpdate, SortField, SortOrder


def _now() -> datetime:
    return datetime.now(UTC)


class BikeError(Exception):
    def __init__(self, code: str, message: str, status: int = 404) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


def _base(owner_id: uuid.UUID):
    return select(Bike).where(Bike.owner_id == owner_id, Bike.deleted_at.is_(None))


async def get_owned(
    db: AsyncSession,
    owner_id: uuid.UUID,
    bike_id: uuid.UUID,
    include_deleted: bool = False,
) -> Bike:
    stmt = select(Bike).where(Bike.owner_id == owner_id, Bike.id == bike_id)
    if not include_deleted:
        stmt = stmt.where(Bike.deleted_at.is_(None))
    res = await db.execute(stmt)
    bike = res.scalar_one_or_none()
    if bike is None:
        raise BikeError("BIKE_NOT_FOUND", "Bike not found.", 404)
    return bike


async def create(db: AsyncSession, owner_id: uuid.UUID, data: BikeCreate) -> Bike:
    now = _now()
    bike = Bike(
        owner_id=owner_id,
        name=data.name.strip(),
        category=data.category,
        brand=data.brand,
        model=data.model,
        model_year=data.model_year,
        frame_size=data.frame_size,
        weight_kg=data.weight_kg,
        notes=data.notes,
        initial_distance_km=data.initial_distance_km,
        status=BikeStatus.ACTIVE,
        version=1,
        created_at=now,
        updated_at=now,
    )
    db.add(bike)
    await db.commit()
    await db.refresh(bike)
    return bike


async def list_page(
    db: AsyncSession,
    owner_id: uuid.UUID,
    page: int,
    page_size: int,
    category: BikeCategory | None,
    status: BikeStatus | None,
    sort: SortField,
    order: SortOrder,
) -> tuple[list[Bike], int]:
    stmt = _base(owner_id)
    count_stmt = (
        select(func.count())
        .select_from(Bike)
        .where(Bike.owner_id == owner_id, Bike.deleted_at.is_(None))
    )
    if category is not None:
        stmt = stmt.where(Bike.category == category)
        count_stmt = count_stmt.where(Bike.category == category)
    if status is not None:
        stmt = stmt.where(Bike.status == status)
        count_stmt = count_stmt.where(Bike.status == status)
    total = (await db.execute(count_stmt)).scalar_one()
    column = {"created_at": Bike.created_at, "updated_at": Bike.updated_at, "name": Bike.name}[sort]
    stmt = stmt.order_by(column.desc() if order == "desc" else column.asc())
    stmt = stmt.offset((page - 1) * page_size).limit(page_size)
    items: list[Bike] = list((await db.execute(stmt)).scalars())
    return items, total


async def update(db: AsyncSession, bike: Bike, data: BikeUpdate) -> Bike:
    if data.expected_version is not None and data.expected_version != bike.version:
        raise BikeError(
            "VERSION_CONFLICT",
            "Bike was modified elsewhere. Reload and try again.",
            409,
        )
    changes = data.model_dump(exclude_unset=True, exclude={"expected_version"})
    for field, value in changes.items():
        setattr(bike, field, value)
    bike.version += 1
    bike.updated_at = _now()
    await db.commit()
    await db.refresh(bike)
    return bike


async def archive(db: AsyncSession, bike: Bike) -> Bike:
    bike.status = BikeStatus.ARCHIVED  # idempotent
    bike.updated_at = _now()
    await db.commit()
    await db.refresh(bike)
    return bike


async def restore(db: AsyncSession, bike: Bike) -> Bike:
    bike.status = BikeStatus.ACTIVE
    bike.updated_at = _now()
    await db.commit()
    await db.refresh(bike)
    return bike


async def soft_delete(db: AsyncSession, bike: Bike) -> Bike:
    from app.models.ride import Ride

    refs = await db.execute(select(func.count()).select_from(Ride).where(Ride.bike_id == bike.id))
    if refs.scalar_one() > 0:
        # Phase 4 promise: history must survive. Archive instead of deleting.
        raise BikeError(
            "BIKE_HAS_RIDES",
            "Bike has ride history and cannot be deleted. Archive it instead.",
            409,
        )
    bike.deleted_at = bike.deleted_at or _now()  # idempotent
    bike.updated_at = _now()
    await db.commit()
    await db.refresh(bike)
    return bike

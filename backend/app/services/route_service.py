"""Route service: ownership, privacy, immutable versioning (docs/03, docs/06).

Rules enforced here:
- private routes: owner-only, foreign access → 404 (no existence oracle)
- `public` disabled until start/end masking ships (ADR-09 §5)
- every edit inserts a new immutable `route_versions` snapshot (points included)
- optimistic concurrency: expected_version mismatch → 409 VERSION_CONFLICT
"""

import uuid
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.route import (
    Route,
    RouteActivityType,
    RouteDifficulty,
    RoutePoint,
    RoutePrivacy,
    RouteSource,
    RouteStatus,
    RouteVersion,
)
from app.schemas.route import RouteCreate, RoutePointIn, RouteUpdate, SortField, SortOrder
from app.services import gpx as gpx_service
from app.services import route_metrics
from app.services.gpx import GpxError, ParsedGpx
from app.services.route_metrics import GeoPoint


def _now() -> datetime:
    return datetime.now(UTC)


class RouteError(Exception):
    def __init__(self, code: str, message: str, status: int = 404) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


def _ensure_public_allowed(privacy: RoutePrivacy) -> None:
    if privacy == RoutePrivacy.PUBLIC:
        raise RouteError(
            "PRIVACY_PUBLIC_DISABLED",
            "Public routes are disabled until start/end masking is available.",
            400,
        )


def _geo(points: list[RoutePointIn]) -> list[GeoPoint]:
    if len(points) > settings.ROUTE_MAX_POINTS:
        raise RouteError(
            "ROUTE_TOO_MANY_POINTS",
            f"Route exceeds {settings.ROUTE_MAX_POINTS} points.",
            400,
        )
    return [GeoPoint(lat=p.lat, lon=p.lon, ele=p.ele) for p in points]


def _dec(value: float, places: int) -> Decimal:
    return Decimal(f"{round(value, places):.{places}f}")


async def _points_of(db: AsyncSession, version_id: uuid.UUID) -> list[RoutePoint]:
    res = await db.execute(
        select(RoutePoint).where(RoutePoint.route_version_id == version_id).order_by(RoutePoint.seq)
    )
    return list(res.scalars())


async def _version_of(db: AsyncSession, route: Route, version_no: int) -> RouteVersion | None:
    res = await db.execute(
        select(RouteVersion).where(
            RouteVersion.route_id == route.id, RouteVersion.version_no == version_no
        )
    )
    return res.scalar_one_or_none()


async def _write_version(
    db: AsyncSession,
    route: Route,
    geo: list[GeoPoint],
    activity_type: RouteActivityType,
    changelog: str | None,
) -> RouteVersion:
    metrics = route_metrics.compute(geo, activity_type.value)
    version = RouteVersion(
        route_id=route.id,
        version_no=route.current_version,
        point_count=len(geo),
        distance_m=_dec(metrics.distance_m, 2),
        elevation_gain_m=_dec(metrics.elevation_gain_m, 2)
        if metrics.elevation_gain_m is not None
        else None,
        elevation_loss_m=_dec(metrics.elevation_loss_m, 2)
        if metrics.elevation_loss_m is not None
        else None,
        estimated_duration_s=metrics.estimated_duration_s,
        difficulty=RouteDifficulty(metrics.difficulty) if metrics.difficulty else None,
        elevation_profile=metrics.profile or None,
        changelog=changelog,
        created_at=_now(),
    )
    db.add(version)
    await db.flush()

    for seq, p in enumerate(geo):
        db.add(
            RoutePoint(
                route_version_id=version.id,
                seq=seq,
                lat=_dec(p.lat, 6),
                lon=_dec(p.lon, 6),
                ele=_dec(p.ele, 2) if p.ele is not None else None,
            )
        )
    await db.flush()

    route.distance_m = _dec(metrics.distance_m, 2)
    route.elevation_gain_m = (
        _dec(metrics.elevation_gain_m, 2) if metrics.elevation_gain_m is not None else None
    )
    route.elevation_loss_m = (
        _dec(metrics.elevation_loss_m, 2) if metrics.elevation_loss_m is not None else None
    )
    route.highest_point_m = (
        _dec(metrics.highest_point_m, 2) if metrics.highest_point_m is not None else None
    )
    route.lowest_point_m = (
        _dec(metrics.lowest_point_m, 2) if metrics.lowest_point_m is not None else None
    )
    route.estimated_duration_s = metrics.estimated_duration_s
    route.difficulty = RouteDifficulty(metrics.difficulty) if metrics.difficulty else None
    route.point_count = len(geo)
    if geo:
        route.start_lat = _dec(geo[0].lat, 6)
        route.start_lon = _dec(geo[0].lon, 6)
        route.end_lat = _dec(geo[-1].lat, 6)
        route.end_lon = _dec(geo[-1].lon, 6)
    return version


async def get_owned(
    db: AsyncSession,
    owner_id: uuid.UUID,
    route_id: uuid.UUID,
    include_deleted: bool = False,
) -> Route:
    stmt = select(Route).where(Route.owner_id == owner_id, Route.id == route_id)
    if not include_deleted:
        stmt = stmt.where(Route.deleted_at.is_(None))
    res = await db.execute(stmt)
    route = res.scalar_one_or_none()
    if route is None:
        raise RouteError("ROUTE_NOT_FOUND", "Route not found.", 404)
    return route


async def get_readable(db: AsyncSession, user_id: uuid.UUID, route_id: uuid.UUID) -> Route:
    """Owner sees everything; unlisted/public are readable by ID only (no discovery)."""
    res = await db.execute(select(Route).where(Route.id == route_id, Route.deleted_at.is_(None)))
    route = res.scalar_one_or_none()
    if route is None:
        raise RouteError("ROUTE_NOT_FOUND", "Route not found.", 404)
    if route.owner_id != user_id and route.privacy == RoutePrivacy.PRIVATE:
        # 404, not 403: never confirm a private route exists (ADR-05 rule).
        raise RouteError("ROUTE_NOT_FOUND", "Route not found.", 404)
    return route


async def create(db: AsyncSession, owner_id: uuid.UUID, data: RouteCreate) -> Route:
    _ensure_public_allowed(data.privacy)
    geo = _geo(data.points)
    now = _now()
    route = Route(
        owner_id=owner_id,
        name=data.name.strip(),
        description=data.description,
        activity_type=data.activity_type,
        privacy=data.privacy,
        status=RouteStatus.ACTIVE,
        source=RouteSource.MANUAL,
        current_version=1,
        created_at=now,
        updated_at=now,
    )
    db.add(route)
    await db.flush()
    await _write_version(db, route, geo, data.activity_type, changelog=None)
    route.created_at = now
    route.updated_at = now
    await db.commit()
    await db.refresh(route)
    return route


async def list_page(
    db: AsyncSession,
    owner_id: uuid.UUID,
    page: int,
    page_size: int,
    activity_type: RouteActivityType | None,
    status: RouteStatus | None,
    sort: SortField,
    order: SortOrder,
) -> tuple[list[Route], int]:
    """Owner's own routes only. No public/global discovery in Phase 5 (§34)."""
    filters = [Route.owner_id == owner_id, Route.deleted_at.is_(None)]
    if activity_type is not None:
        filters.append(Route.activity_type == activity_type)
    if status is not None:
        filters.append(Route.status == status)
    total = (await db.execute(select(func.count()).select_from(Route).where(*filters))).scalar_one()
    column = {
        "created_at": Route.created_at,
        "updated_at": Route.updated_at,
        "name": Route.name,
        "distance": Route.distance_m,
    }[sort]
    stmt = (
        select(Route)
        .where(*filters)
        .order_by(column.desc() if order == "desc" else column.asc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    items: list[Route] = list((await db.execute(stmt)).scalars())
    return items, total


async def update(db: AsyncSession, route: Route, data: RouteUpdate) -> Route:
    if data.expected_version != route.current_version:
        raise RouteError(
            "VERSION_CONFLICT",
            "Route was modified elsewhere. Reload and try again.",
            409,
        )
    changes = data.model_dump(
        exclude_unset=True, exclude={"expected_version", "points", "changelog"}
    )
    for required in ("name", "activity_type", "privacy"):
        if required in changes and changes[required] is None:
            raise RouteError("ROUTE_INVALID_FIELD", f"{required} cannot be cleared.", 400)
    if "privacy" in changes:
        _ensure_public_allowed(RoutePrivacy(changes["privacy"]))

    if data.points is not None:
        source_geo = _geo(data.points)
    else:
        current = await _version_of(db, route, route.current_version)
        if current is None:
            raise RouteError("ROUTE_VERSION_MISSING", "Current version not found.", 409)
        rows = await _points_of(db, current.id)
        source_geo = [
            GeoPoint(lat=float(r.lat), lon=float(r.lon), ele=float(r.ele) if r.ele else None)
            for r in rows
        ]

    for field, value in changes.items():
        setattr(route, field, value)

    route.current_version += 1
    route.updated_at = _now()
    await _write_version(db, route, source_geo, route.activity_type, data.changelog)
    await db.commit()
    await db.refresh(route)
    return route


async def archive(db: AsyncSession, route: Route) -> Route:
    route.status = RouteStatus.ARCHIVED
    route.updated_at = _now()
    await db.commit()
    await db.refresh(route)
    return route


async def restore(db: AsyncSession, route: Route) -> Route:
    route.status = RouteStatus.ACTIVE
    route.updated_at = _now()
    await db.commit()
    await db.refresh(route)
    return route


async def soft_delete(db: AsyncSession, route: Route) -> Route:
    route.deleted_at = route.deleted_at or _now()
    route.updated_at = _now()
    await db.commit()
    await db.refresh(route)
    return route


async def list_versions(db: AsyncSession, route: Route) -> list[RouteVersion]:
    res = await db.execute(
        select(RouteVersion)
        .where(RouteVersion.route_id == route.id)
        .order_by(RouteVersion.version_no.desc())
    )
    return list(res.scalars())


async def get_version(db: AsyncSession, route: Route, version_no: int) -> RouteVersion:
    version = await _version_of(db, route, version_no)
    if version is None:
        raise RouteError("ROUTE_VERSION_NOT_FOUND", "Route version not found.", 404)
    return version


async def get_geometry(
    db: AsyncSession, route: Route, version_no: int | None = None
) -> tuple[RouteVersion, list[RoutePoint]]:
    wanted = version_no if version_no is not None else route.current_version
    version = await get_version(db, route, wanted)
    return version, await _points_of(db, version.id)


async def import_gpx(
    db: AsyncSession,
    owner_id: uuid.UUID,
    data: bytes,
    *,
    name: str | None = None,
    activity_type: RouteActivityType = RouteActivityType.ROAD,
    privacy: RoutePrivacy = RoutePrivacy.PRIVATE,
) -> tuple[Route, ParsedGpx]:
    try:
        parsed = gpx_service.parse_gpx(data)
    except GpxError as e:
        raise RouteError(e.code, e.message, e.status) from e
    _ensure_public_allowed(privacy)
    route_name = (name or parsed.name or "Imported route").strip()[:120] or "Imported route"
    route = Route(
        owner_id=owner_id,
        name=route_name,
        description=None,
        activity_type=activity_type,
        privacy=privacy,
        status=RouteStatus.ACTIVE,
        source=RouteSource.GPX,
        current_version=1,
        created_at=_now(),
        updated_at=_now(),
    )
    db.add(route)
    await db.flush()
    await _write_version(db, route, parsed.points, activity_type, changelog="gpx-import")
    await db.commit()
    await db.refresh(route)
    return route, parsed


async def export_gpx(db: AsyncSession, route: Route) -> tuple[str, str]:
    _, rows = await get_geometry(db, route)
    points = [
        (float(r.lat), float(r.lon), float(r.ele) if r.ele is not None else None) for r in rows
    ]
    return gpx_service.sanitize_filename(route.name), gpx_service.export_gpx(route.name, points)

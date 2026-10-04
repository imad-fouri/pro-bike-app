"""Route API (Phase 5). Owner-only private routes; no social discovery (§34).

ROUTE != RIDE: these endpoints manage planned geometry only. A ride may
optionally reference (route_id, route_version) — see /rides.
"""

import uuid

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
)
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core.config import settings
from app.core.rate_limit import allow
from app.db.session import get_db
from app.models.route import RouteActivityType, RoutePrivacy, RouteStatus
from app.models.user import User
from app.schemas.route import (
    GpxImportResult,
    RouteCreate,
    RouteDetail,
    RouteGeometry,
    RouteOut,
    RoutePage,
    RoutePointOut,
    RouteUpdate,
    RouteVersionList,
    RouteVersionOut,
    SortField,
    SortOrder,
)
from app.services import route_service
from app.services.route_service import RouteError

router = APIRouter(prefix="/routes", tags=["routes"])


def _limit(request: Request, user: User, key: str = "routes", limit: int = 120) -> None:
    ident = request.client.host if request.client else "unknown"
    if not allow(f"{key}:{user.id}:{ident}", limit, 60):
        raise HTTPException(status_code=429, detail="Too many requests.")


def _err(e: RouteError) -> HTTPException:
    return HTTPException(status_code=e.status, detail={"code": e.code, "message": e.message})


def _out(route: object) -> RouteOut:
    return RouteOut.model_validate(route, from_attributes=True)


async def _detail(db: AsyncSession, route: object, include_geometry: bool) -> RouteDetail:
    from app.models.route import Route

    assert isinstance(route, Route)
    base = _out(route).model_dump()
    geometry = None
    if include_geometry:
        version, rows = await route_service.get_geometry(db, route)
        geometry = RouteGeometry(
            route_id=route.id,
            version_no=version.version_no,
            point_count=len(rows),
            points=[RoutePointOut(seq=r.seq, lat=r.lat, lon=r.lon, ele=r.ele) for r in rows],
            elevation_profile=version.elevation_profile,
        )
    return RouteDetail(**base, geometry=geometry)


@router.post("/import/gpx", response_model=GpxImportResult, status_code=201)
async def import_gpx(
    request: Request,
    file: UploadFile = File(...),
    name: str | None = Form(default=None, max_length=120),
    activity_type: RouteActivityType = Form(default=RouteActivityType.ROAD),
    privacy: RoutePrivacy = Form(default=RoutePrivacy.PRIVATE),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> GpxImportResult:
    """GPX import: untrusted upload, bounded read, XXE-safe parser (§13, §32)."""
    _limit(request, user, key="gpx-import", limit=20)
    data = await file.read(settings.GPX_MAX_BYTES + 1)
    try:
        route, parsed = await route_service.import_gpx(
            db,
            user.id,
            data,
            name=name,
            activity_type=activity_type,
            privacy=privacy,
        )
    except RouteError as e:
        raise _err(e) from e
    return GpxImportResult(
        route=await _detail(db, route, include_geometry=True),
        imported_points=len(parsed.points),
        duplicates_removed=parsed.raw_count - len(parsed.points),
        had_timestamps=parsed.has_timestamps,
    )


@router.post("", response_model=RouteOut, status_code=201)
async def create_route(
    data: RouteCreate,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> RouteOut:
    _limit(request, user)
    try:
        route = await route_service.create(db, user.id, data)
    except RouteError as e:
        raise _err(e) from e
    return _out(route)


@router.get("", response_model=RoutePage)
async def list_routes(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    activity_type: RouteActivityType | None = None,
    status: RouteStatus | None = Query(default=RouteStatus.ACTIVE),
    status_all: bool = Query(default=False),
    sort: SortField = "created_at",
    order: SortOrder = "desc",
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> RoutePage:
    items, total = await route_service.list_page(
        db,
        user.id,
        page,
        page_size,
        activity_type,
        None if status_all else status,
        sort,
        order,
    )
    return RoutePage(items=[_out(r) for r in items], total=total, page=page, page_size=page_size)


@router.get("/{route_id}", response_model=RouteDetail)
async def get_route(
    route_id: uuid.UUID,
    include_geometry: bool = Query(default=False),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> RouteDetail:
    try:
        route = await route_service.get_readable(db, user.id, route_id)
    except RouteError as e:
        raise _err(e) from e
    return await _detail(db, route, include_geometry)


@router.patch("/{route_id}", response_model=RouteOut)
async def update_route(
    route_id: uuid.UUID,
    data: RouteUpdate,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> RouteOut:
    """expected_version is required; mismatch → 409 (monotonic route_versions)."""
    _limit(request, user)
    try:
        route = await route_service.get_owned(db, user.id, route_id)
        route = await route_service.update(db, route, data)
    except RouteError as e:
        raise _err(e) from e
    return _out(route)


@router.delete("/{route_id}", response_model=RouteOut)
async def delete_route(
    route_id: uuid.UUID,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> RouteOut:
    """Soft delete (deleted_at); idempotent. Rides keep their reference."""
    _limit(request, user)
    try:
        route = await route_service.get_owned(db, user.id, route_id, include_deleted=True)
        route = await route_service.soft_delete(db, route)
    except RouteError as e:
        raise _err(e) from e
    return _out(route)


@router.post("/{route_id}/archive", response_model=RouteOut)
async def archive_route(
    route_id: uuid.UUID,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> RouteOut:
    _limit(request, user)
    try:
        route = await route_service.get_owned(db, user.id, route_id)
        route = await route_service.archive(db, route)
    except RouteError as e:
        raise _err(e) from e
    return _out(route)


@router.post("/{route_id}/restore", response_model=RouteOut)
async def restore_route(
    route_id: uuid.UUID,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> RouteOut:
    _limit(request, user)
    try:
        route = await route_service.get_owned(db, user.id, route_id, include_deleted=True)
        route = await route_service.restore(db, route)
    except RouteError as e:
        raise _err(e) from e
    return _out(route)


@router.get("/{route_id}/geometry", response_model=RouteGeometry)
async def get_route_geometry(
    route_id: uuid.UUID,
    version: int | None = Query(default=None, ge=1),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> RouteGeometry:
    """Ordered geometry for one version (default: current). Historical versions stay readable."""
    try:
        route = await route_service.get_readable(db, user.id, route_id)
        version_row, rows = await route_service.get_geometry(db, route, version)
    except RouteError as e:
        raise _err(e) from e
    return RouteGeometry(
        route_id=route.id,
        version_no=version_row.version_no,
        point_count=len(rows),
        points=[RoutePointOut(seq=r.seq, lat=r.lat, lon=r.lon, ele=r.ele) for r in rows],
        elevation_profile=version_row.elevation_profile,
    )


@router.get("/{route_id}/versions", response_model=RouteVersionList)
async def list_route_versions(
    route_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> RouteVersionList:
    try:
        route = await route_service.get_readable(db, user.id, route_id)
        versions = await route_service.list_versions(db, route)
    except RouteError as e:
        raise _err(e) from e
    return RouteVersionList(
        items=[RouteVersionOut.model_validate(v, from_attributes=True) for v in versions]
    )


@router.get("/{route_id}/export/gpx")
async def export_route_gpx(
    route_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """GPX 1.1 export: route name + points + elevation only (no user data, §14)."""
    try:
        route = await route_service.get_readable(db, user.id, route_id)
        filename, content = await route_service.export_gpx(db, route)
    except RouteError as e:
        raise _err(e) from e
    return Response(
        content=content,
        media_type="application/gpx+xml",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )

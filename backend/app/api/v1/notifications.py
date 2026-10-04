"""Phase 8.4 notification API (ADR-15).

Two routers: the rider's own notification center, and their own push devices.
Nothing here creates a notification — a public "send me a notification" endpoint
would be an unauthenticated-content broadcast vector, so notification creation is
internal to the service layer only.

Every route authenticates first and delegates to `notification_service`. The
router holds no policy of its own: a second place where "whose notification is
this?" is decided is a second place for it to be decided differently.

**404, never 403**, for anything the caller does not own — matching the Phase 8.3
convention, so a guessed id cannot confirm that something exists.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core.rate_limit import allow
from app.db.session import get_db
from app.models.user import User
from app.schemas.notifications import (
    MarkAllReadOut,
    NotificationOut,
    NotificationPage,
    PushDeviceOut,
    PushDevicePage,
    PushDeviceRegister,
    PushDeviceUpdate,
    UnreadCountOut,
)
from app.services import notification_service
from app.services.notification_service import NotificationError

router = APIRouter(tags=["notifications"])

MAX_LIST = 100

# Rate limits. Chosen for this surface rather than copied from chat: registering a
# device happens on login and on token rotation (rare), while reading a
# notification list happens on every foreground (frequent).
#
# KNOWN LIMITATION: `allow()` is an in-process dict, so with N uvicorn workers the
# effective limit is `limit * N` and it resets on restart. Same limitation Phase
# 8.3 accepted; a shared Redis limiter belongs with the Redis work.
_LIMIT = {
    "device-register": (20, 3600),  # 20/hour — login + token rotation
    "device-update": (60, 3600),  # 60/hour
    "list": (120, 60),  # 120/minute
    "read": (240, 60),  # 240/minute
    "read-all": (20, 60),  # 20/minute — rare and bulk
}


def _fail(exc: NotificationError) -> HTTPException:
    return HTTPException(status_code=exc.status, detail={"code": exc.code, "message": exc.message})


def _limited(key: str, limit_name: str) -> None:
    limit, window = _LIMIT[limit_name]
    if not allow(key, limit, window):
        raise HTTPException(status_code=429, detail="Too many requests.")


# ---------------------------------------------------------------------------
# Notification center
# ---------------------------------------------------------------------------


@router.get("/notifications", response_model=NotificationPage)
async def list_notifications(
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=MAX_LIST),
    unread_only: bool = False,
) -> NotificationPage:
    """The rider's own notifications, newest first.

    Offset-paged to match the project standard: a notification center is bounded
    and read deliberately, so unlike message history it does not shift under the
    reader while they page it.
    """
    _limited(f"notif-list:{user.id}", "list")
    items, total = await notification_service.list_notifications(
        db, user, page=page, page_size=page_size, unread_only=unread_only
    )
    return NotificationPage(
        items=[NotificationOut.model_validate(v) for v in items],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/notifications/unread-count", response_model=UnreadCountOut)
async def unread_count(
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> UnreadCountOut:
    """Unread total, served from a partial index over unread rows only.

    Separate from the list endpoint because the app bar polls this on every
    foreground; returning a full page for a number would be wasteful.
    """
    _limited(f"notif-list:{user.id}", "list")
    return UnreadCountOut(unread_count=await notification_service.unread_count(db, user))


@router.post("/notifications/read-all", response_model=MarkAllReadOut)
async def mark_all_read(
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> MarkAllReadOut:
    """Mark every unread notification read.

    Idempotent, and returns 200 with `marked: 0` when there was nothing to do —
    a client retrying this must not see a failure.
    """
    _limited(f"notif-read-all:{user.id}", "read-all")
    return MarkAllReadOut(marked=await notification_service.mark_all_read(db, user))


@router.post("/notifications/{notification_id}/read", response_model=NotificationOut)
async def mark_read(
    notification_id: uuid.UUID,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> NotificationOut:
    """Mark one notification read. Idempotent.

    Declared AFTER `/read-all` so the literal path is not captured as a UUID.
    """
    _limited(f"notif-read:{user.id}", "read")
    try:
        view = await notification_service.mark_read(db, user, notification_id)
    except NotificationError as exc:
        raise _fail(exc) from exc
    return NotificationOut.model_validate(view)


# ---------------------------------------------------------------------------
# Push devices
# ---------------------------------------------------------------------------


@router.get("/push-devices", response_model=PushDevicePage)
async def list_devices(
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> PushDevicePage:
    """The rider's own devices. **Never includes a token.**

    A page envelope rather than a bare list: every other listing in this project
    uses one, and the shared `ApiClient` decodes only JSON objects — a bare
    array would be a client-side cast error rather than a clean response.
    """
    _limited(f"notif-list:{user.id}", "list")
    rows = await notification_service.list_devices(db, user)
    return PushDevicePage(
        items=[PushDeviceOut.model_validate(r.public_view()) for r in rows],
        total=len(rows),
    )


@router.post("/push-devices", response_model=PushDeviceOut)
async def register_device(
    body: PushDeviceRegister,
    response: Response,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> PushDeviceOut:
    """Register or refresh one of the rider's own devices.

    Idempotent on `(user_id, provider, device_id)`: a re-registration updates the
    existing row and answers **200**, while a genuinely new device answers
    **201**. The distinction is what lets a client tell a first install from a
    token rotation without a second call. A caller can never register a device on
    another account — ownership comes from the JWT.

    The response body is built from `public_view()`, which has no `token` key, so
    a future column added to `push_devices` cannot leak through this route.
    """
    _limited(f"push-device:{user.id}", "device-register")
    try:
        row, created = await notification_service.register_device(
            db,
            user,
            platform=body.platform,
            provider=body.provider,
            device_id=body.device_id,
            token=body.token,
            app_version=body.app_version,
            locale=body.locale,
        )
    except NotificationError as exc:
        raise _fail(exc) from exc
    response.status_code = 201 if created else 200
    return PushDeviceOut.model_validate(row.public_view())


@router.patch("/push-devices/{device_id}", response_model=PushDeviceOut)
async def update_device(
    device_id: uuid.UUID,
    body: PushDeviceUpdate,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> PushDeviceOut:
    """Enable or disable one of the rider's own devices.

    Only `enabled` is settable. Moving a device to another user, changing its
    platform, or rewriting its token through this route is not possible by
    construction — the schema has no such fields.
    """
    _limited(f"push-device:{user.id}", "device-update")
    if body.enabled is None:
        raise HTTPException(status_code=422, detail="Nothing to update.")
    try:
        row = await notification_service.set_device_enabled(db, user, device_id, body.enabled)
    except NotificationError as exc:
        raise _fail(exc) from exc
    return PushDeviceOut.model_validate(row.public_view())


@router.delete("/push-devices/{device_id}")
async def delete_device(
    device_id: uuid.UUID,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    """Revoke one of the rider's own devices.

    A hard delete, unlike a disable: a revoked token has no reason to remain
    stored on disk. Another rider's device id answers **404**.
    """
    _limited(f"push-device:{user.id}", "device-update")
    try:
        await notification_service.delete_device(db, user, device_id)
    except NotificationError as exc:
        raise _fail(exc) from exc
    return {"status": "revoked"}

"""Social API (Phase 8.1). Owner-only, privacy-filtered, never an oracle.

Every id that matters comes from the JWT. Client-supplied ids name only the
*target* of an action and are re-resolved server-side; foreign relationships
answer 404 (never 403) so one rider cannot probe another's social graph.
"""

import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core.rate_limit import allow
from app.db.session import get_db
from app.models.user import User
from app.schemas.social import (
    BlockCreate,
    BlockOut,
    BlockPage,
    FriendOut,
    FriendPage,
    FriendRequestCreate,
    FriendRequestOut,
    FriendRequestPage,
    PublicProfileOut,
    SearchPage,
    SocialPrivacyUpdate,
    SocialProfileOut,
    SocialProfileUpdate,
)
from app.services import social_service
from app.services.social_service import SocialError

router = APIRouter(prefix="/social", tags=["social"])

MAX_LIST = 100


def _fail(exc: SocialError) -> HTTPException:
    return HTTPException(status_code=exc.status, detail={"code": exc.code, "message": exc.message})


def _limited(key: str, limit: int, window_s: int) -> None:
    if not allow(key, limit, window_s):
        raise HTTPException(status_code=429, detail="Too many requests.")


@router.get("/profile/me", response_model=SocialProfileOut)
async def my_profile(
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> SocialProfileOut:
    profile = await social_service.ensure_profile(db, user)
    await db.commit()
    return SocialProfileOut.model_validate(profile)


@router.patch("/profile", response_model=SocialProfileOut)
async def update_profile(
    body: SocialProfileUpdate,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> SocialProfileOut:
    _limited(f"social-profile:{user.id}", 30, 3600)
    try:
        profile = await social_service.update_profile(db, user, body.model_dump(exclude_unset=True))
    except SocialError as exc:
        raise _fail(exc) from exc
    return SocialProfileOut.model_validate(profile)


@router.patch("/profile/privacy", response_model=SocialProfileOut)
async def update_privacy(
    body: SocialPrivacyUpdate,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> SocialProfileOut:
    _limited(f"social-profile:{user.id}", 30, 3600)
    try:
        profile = await social_service.update_privacy(db, user, body.model_dump(exclude_unset=True))
    except SocialError as exc:
        raise _fail(exc) from exc
    return SocialProfileOut.model_validate(profile)


@router.get("/profile/{user_id}", response_model=PublicProfileOut)
async def get_profile(
    user_id: uuid.UUID,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> PublicProfileOut:
    try:
        view = await social_service.view_profile(db, user, user_id)
    except SocialError as exc:
        raise _fail(exc) from exc
    return PublicProfileOut.model_validate(view)


@router.get("/users/search", response_model=SearchPage)
async def search_users(
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
    q: str = Query(min_length=2, max_length=64),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=MAX_LIST),
) -> SearchPage:
    _limited(f"social-search:{user.id}", 60, 60)
    items, total = await social_service.search(db, user, q, page, page_size)
    return SearchPage(
        items=[PublicProfileOut.model_validate(v) for v in items],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.post("/friend-requests", status_code=201)
async def send_request(
    body: FriendRequestCreate,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    _limited(f"social-request:{user.id}", 20, 3600)
    try:
        row = await social_service.send_request(db, user, body.user_id)
    except SocialError as exc:
        raise _fail(exc) from exc
    return {"id": str(row.id), "status": row.status.value}


@router.get("/friend-requests", response_model=FriendRequestPage)
async def list_requests(
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
    direction: Literal["incoming", "outgoing"] = "incoming",
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=MAX_LIST),
) -> FriendRequestPage:
    items, total = await social_service.list_requests(db, user, direction, page, page_size)
    return FriendRequestPage(
        items=[FriendRequestOut.model_validate(v) for v in items],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.post("/friend-requests/{request_id}/accept")
async def accept_request(
    request_id: uuid.UUID,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    try:
        row = await social_service.accept_request(db, user, request_id)
    except SocialError as exc:
        raise _fail(exc) from exc
    return {"id": str(row.id), "status": row.status.value}


@router.post("/friend-requests/{request_id}/reject")
async def reject_request(
    request_id: uuid.UUID,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    try:
        await social_service.reject_request(db, user, request_id)
    except SocialError as exc:
        raise _fail(exc) from exc
    return {"status": "rejected"}


@router.delete("/friend-requests/{request_id}")
async def cancel_request(
    request_id: uuid.UUID,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    try:
        await social_service.cancel_request(db, user, request_id)
    except SocialError as exc:
        raise _fail(exc) from exc
    return {"status": "cancelled"}


@router.get("/friends", response_model=FriendPage)
async def list_friends(
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=MAX_LIST),
) -> FriendPage:
    items, total = await social_service.list_friends(db, user, page, page_size)
    return FriendPage(
        items=[FriendOut.model_validate(v) for v in items],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.delete("/friends/{user_id}")
async def remove_friend(
    user_id: uuid.UUID,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    try:
        await social_service.remove_friend(db, user, user_id)
    except SocialError as exc:
        raise _fail(exc) from exc
    return {"status": "removed"}


@router.post("/blocks", status_code=201)
async def block_user(
    body: BlockCreate,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    _limited(f"social-block:{user.id}", 30, 3600)
    try:
        row = await social_service.block_user(db, user, body.user_id)
    except SocialError as exc:
        raise _fail(exc) from exc
    return {"user_id": str(row.blocked_user_id), "status": "blocked"}


@router.get("/blocks", response_model=BlockPage)
async def list_blocks(
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=MAX_LIST),
) -> BlockPage:
    items, total = await social_service.list_blocks(db, user, page, page_size)
    return BlockPage(
        items=[BlockOut.model_validate(v) for v in items],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.delete("/blocks/{user_id}")
async def unblock_user(
    user_id: uuid.UUID,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    _limited(f"social-block:{user.id}", 30, 3600)
    try:
        await social_service.unblock_user(db, user, user_id)
    except SocialError as exc:
        raise _fail(exc) from exc
    return {"status": "unblocked"}

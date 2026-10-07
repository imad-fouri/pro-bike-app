"""Challenges API (WS-RC).

Every id that matters comes from the JWT. A client may name a challenge it
wants to join, leave, publish or cancel; it may never tell the server the
progress, the rank, or the number of points. Progress is recomputed from the
``rides`` table on read, so this module has no endpoint where a score could be
posted.
"""

import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core.rate_limit import allow
from app.db.session import get_db
from app.models.user import User
from app.schemas.challenge import (
    ChallengeCreate,
    ChallengeOut,
    ChallengePage,
    LeaderboardEntry,
    LeaderboardPage,
)
from app.services import challenge_service
from app.services.challenge_service import ChallengeError

router = APIRouter(prefix="/challenges", tags=["challenges"])

MAX_PAGE_SIZE = 100


def _fail(exc: ChallengeError) -> HTTPException:
    return HTTPException(status_code=exc.status, detail={"code": exc.code, "message": exc.message})


def _limited(key: str, limit: int, window_s: int) -> None:
    if not allow(key, limit, window_s):
        raise HTTPException(status_code=429, detail="Too many requests.")


@router.get("", response_model=ChallengePage)
async def list_challenges(
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
    scope: Literal["individual", "friends", "team", "global"] | None = None,
    mine: bool = False,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=MAX_PAGE_SIZE),
) -> ChallengePage:
    _limited(f"challenge-list:{user.id}", 120, 60)
    items, total = await challenge_service.list_challenges(
        db, user, scope=scope, mine=mine, page=page, page_size=page_size
    )
    return ChallengePage(
        items=[ChallengeOut.model_validate(v) for v in items],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.post("", status_code=201, response_model=ChallengeOut)
async def create_challenge(
    body: ChallengeCreate,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ChallengeOut:
    _limited(f"challenge-create:{user.id}", 20, 3600)
    try:
        challenge = await challenge_service.create_challenge(db, user, body.model_dump())
        await db.commit()
        view = await challenge_service.get_challenge(db, user, challenge.id)
    except ChallengeError as exc:
        raise _fail(exc) from exc
    return ChallengeOut.model_validate(view)


@router.get("/{challenge_id}", response_model=ChallengeOut)
async def get_challenge(
    challenge_id: uuid.UUID,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ChallengeOut:
    _limited(f"challenge-read:{user.id}", 120, 60)
    try:
        view = await challenge_service.get_challenge(db, user, challenge_id)
    except ChallengeError as exc:
        raise _fail(exc) from exc
    return ChallengeOut.model_validate(view)


@router.post("/{challenge_id}/publish", response_model=ChallengeOut)
async def publish_challenge(
    challenge_id: uuid.UUID,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ChallengeOut:
    _limited(f"challenge-write:{user.id}", 60, 3600)
    try:
        challenge = await challenge_service.publish_challenge(db, user, challenge_id)
        await db.commit()
        view = await challenge_service.get_challenge(db, user, challenge.id)
    except ChallengeError as exc:
        raise _fail(exc) from exc
    return ChallengeOut.model_validate(view)


@router.post("/{challenge_id}/cancel", response_model=ChallengeOut)
async def cancel_challenge(
    challenge_id: uuid.UUID,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ChallengeOut:
    _limited(f"challenge-write:{user.id}", 60, 3600)
    try:
        challenge = await challenge_service.cancel_challenge(db, user, challenge_id)
        await db.commit()
        view = await challenge_service.get_challenge(db, user, challenge.id)
    except ChallengeError as exc:
        raise _fail(exc) from exc
    return ChallengeOut.model_validate(view)


@router.post("/{challenge_id}/join")
async def join_challenge(
    challenge_id: uuid.UUID,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    _limited(f"challenge-write:{user.id}", 60, 3600)
    try:
        await challenge_service.join_challenge(db, user, challenge_id)
        await db.commit()
    except ChallengeError as exc:
        raise _fail(exc) from exc
    return {"status": "joined"}


@router.post("/{challenge_id}/leave")
async def leave_challenge(
    challenge_id: uuid.UUID,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    _limited(f"challenge-write:{user.id}", 60, 3600)
    try:
        await challenge_service.leave_challenge(db, user, challenge_id)
        await db.commit()
    except ChallengeError as exc:
        raise _fail(exc) from exc
    return {"status": "left"}


@router.get("/{challenge_id}/leaderboard", response_model=LeaderboardPage)
async def challenge_leaderboard(
    challenge_id: uuid.UUID,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=MAX_PAGE_SIZE),
) -> LeaderboardPage:
    _limited(f"challenge-read:{user.id}", 120, 60)
    try:
        items, total = await challenge_service.leaderboard(
            db, user, challenge_id, page=page, page_size=page_size
        )
    except ChallengeError as exc:
        raise _fail(exc) from exc
    return LeaderboardPage(
        items=[LeaderboardEntry.model_validate(v) for v in items],
        total=total,
        page=page,
        page_size=page_size,
    )

"""Rankings API (WS-RC).

A read-only leaderboard. The scope/period/metric vocabulary is closed; a
request names facts (a team, a category, a window) and the server decides who
is on the board and how high they are. There is no request body, no score
input, and no "shoulder-mounted" number a client could inflate.
"""

import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core.rate_limit import allow
from app.db.session import get_db
from app.models.user import User
from app.schemas.ranking import PeriodBounds, RankingPage, RankingRow
from app.services import ranking_service
from app.services.ranking_service import RankingError

router = APIRouter(prefix="/rankings", tags=["rankings"])

MAX_PAGE_SIZE = 100


def _fail(exc: RankingError) -> HTTPException:
    return HTTPException(status_code=exc.status, detail={"code": exc.code, "message": exc.message})


def _limited(key: str, limit: int, window_s: int) -> None:
    if not allow(key, limit, window_s):
        raise HTTPException(status_code=429, detail="Too many requests.")


@router.get("", response_model=RankingPage)
async def rankings(
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
    scope: Literal["global", "country", "city", "friends", "team", "category"] = Query(...),
    period: Literal["weekly", "monthly", "all_time"] = Query(default="weekly"),
    metric: Literal["distance", "elevation", "rides", "training", "points"] = Query(
        default="distance"
    ),
    country: str | None = Query(default=None, min_length=2, max_length=2),
    city: str | None = Query(default=None, max_length=120),
    team_id: uuid.UUID | None = Query(default=None),
    category: str | None = Query(default=None, max_length=32),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=MAX_PAGE_SIZE),
) -> RankingPage:
    _limited(f"rankings:{user.id}", 120, 60)
    try:
        items, total, (start, end), viewer_rank, viewer_value = await ranking_service.rankings(
            db,
            user,
            scope=scope,
            period=period,
            metric=metric,
            country=country,
            city=city,
            team_id=team_id,
            category=category,
            page=page,
            page_size=page_size,
        )
    except RankingError as exc:
        raise _fail(exc) from exc
    return RankingPage(
        items=[RankingRow.model_validate(v) for v in items],
        total=total,
        page=page,
        page_size=page_size,
        period=PeriodBounds(start=start, end=end),
        viewer_rank=viewer_rank,
        viewer_value=viewer_value,
    )

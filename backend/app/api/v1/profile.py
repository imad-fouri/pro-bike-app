"""Self profile endpoints. User isolation enforced: only own profile."""

from datetime import UTC, datetime

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.api.v1.auth import _me, _reload
from app.db.session import get_db
from app.models.user import User
from app.schemas.auth import MeOut, ProfilePatch

router = APIRouter(tags=["profile"])


@router.get("/profile", response_model=MeOut)
async def get_profile(user: User = Depends(get_current_user)) -> MeOut:
    return _me(user)


@router.patch("/profile", response_model=MeOut)
async def patch_profile(
    data: ProfilePatch,
    current: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> MeOut:
    user = await _reload(db, current)
    profile = user.profile
    assert profile is not None
    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(profile, field, value)
    profile.updated_at = datetime.now(UTC)
    user.updated_at = profile.updated_at
    await db.commit()
    await db.refresh(user, ["profile"])
    return _me(user)

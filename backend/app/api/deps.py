"""Reusable auth dependencies — foundation for future authorization."""

import uuid

from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core import security
from app.db.session import get_db
from app.models.subscription import Feature
from app.models.user import User, UserStatus
from app.services import subscription_service

_bearer = HTTPBearer(auto_error=False)


async def get_current_user(
    db: AsyncSession = Depends(get_db),
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> User:
    if creds is None or not creds.credentials:
        raise HTTPException(status_code=401, detail="Not authenticated.")
    try:
        payload = security.decode_access_token(creds.credentials)
    except ValueError as e:
        msg = "Token expired." if str(e) == "expired" else "Invalid token."
        raise HTTPException(status_code=401, detail=msg) from e
    try:
        user_id = uuid.UUID(payload["sub"])
    except ValueError as e:
        raise HTTPException(status_code=401, detail="Invalid token.") from e
    res = await db.execute(
        select(User).where(User.id == user_id).options(selectinload(User.profile))
    )
    user = res.scalar_one_or_none()
    if user is None or user.deleted_at is not None:
        raise HTTPException(status_code=401, detail="Invalid token.")
    if user.status != UserStatus.ACTIVE:
        raise HTTPException(status_code=403, detail="This account is not active.")
    return user


async def get_user_by_id_strict(user_id: uuid.UUID, db: AsyncSession, requester: User) -> User:
    """IDOR guard: private data only for self (public profiles handled in Phase 10)."""
    if user_id != requester.id:
        raise HTTPException(status_code=403, detail="Forbidden.")
    res = await db.execute(select(User).where(User.id == user_id))
    user = res.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=404, detail="Not found.")
    return user


def require_entitlement(feature: Feature):
    """FastAPI dependency factory for one premium capability.

    Every protected route calls this rather than repeating its own
    subscription check. The dependency returns the authenticated user so route
    signatures do not need both it and ``get_current_user``. Authorization is
    always re-resolved from the caller's own database rows.
    """

    async def _guard(
        db: AsyncSession = Depends(get_db),
        user: User = Depends(get_current_user),
    ) -> User:
        allowed, _ = await subscription_service.evaluate_feature(db, user.id, feature)
        if not allowed:
            raise HTTPException(status_code=403, detail=subscription_service.denial_detail(feature))
        return user

    return _guard

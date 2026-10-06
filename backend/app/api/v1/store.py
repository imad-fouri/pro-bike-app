"""Store purchase verification endpoints (Phase 10 WS-PV).

Two thin routes over one service pipeline (`purchase_verification.
process_purchase`): initial verification and restore-as-re-verification.
Both require authentication, both are user-scoped, and both return the
caller's freshly resolved entitlement state — never a provider secret, never
another rider, never an echoed credential.

Rate limiting is per user and IP at 30/hour: verification calls a provider,
so this endpoint must not become a free oracle for token-guessing, and
reinstall storms that hammer restore still fit comfortably.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core.rate_limit import allow
from app.db.session import get_db
from app.models.user import User
from app.schemas.store import VerifyPurchaseIn
from app.schemas.subscription import EntitlementOut, EntitlementStateOut
from app.services.purchase_verification import VerifyKind, process_purchase
from app.services.subscription_service import (
    FREE_CAPABILITIES,
    EntitlementError,
    EntitlementState,
)

router = APIRouter(prefix="/store", tags=["store"])


def _limit(request: Request, user: User, key: str, limit: int = 30) -> None:
    ident = request.client.host if request.client else "unknown"
    if not allow(f"store:{key}:{user.id}:{ident}", limit, 3600):
        raise HTTPException(status_code=429, detail="Too many requests.")


def _err(e: EntitlementError) -> HTTPException:
    return HTTPException(status_code=e.status, detail={"code": e.code, "message": e.message})


def _state_out(state: EntitlementState) -> EntitlementStateOut:
    return EntitlementStateOut(
        plan=state.plan,
        free_capabilities=list(FREE_CAPABILITIES),
        entitlements=[
            EntitlementOut(
                feature=item.feature,
                status=item.status,
                source=item.source,
                starts_at=item.starts_at,
                expires_at=item.expires_at,
                effective=item.effective,
            )
            for item in state.entitlements
        ],
        evaluated_at=state.evaluated_at,
    )


async def _process(
    body: VerifyPurchaseIn,
    request: Request,
    user: User,
    db: AsyncSession,
    kind: VerifyKind,
) -> EntitlementStateOut:
    try:
        state = await process_purchase(
            db,
            kind=kind,
            provider_name=body.provider,
            product_id=body.product_id,
            purchase_token=body.purchase_token,
            user_id=user.id,
        )
    except EntitlementError as e:
        raise _err(e) from e
    return _state_out(state)


@router.post("/purchases/verify", response_model=EntitlementStateOut)
async def verify_purchase(
    body: VerifyPurchaseIn,
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> EntitlementStateOut:
    """Verify one store purchase and reconcile it into subscription state."""
    _limit(request, user, "verify")
    return await _process(body, request, user, db, VerifyKind.PURCHASE)


@router.post("/purchases/restore", response_model=EntitlementStateOut)
async def restore_purchases(
    body: VerifyPurchaseIn,
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> EntitlementStateOut:
    """Reconcile a provider purchase: restore IS re-verification.

    There is no blind-grant restore path. A restore that cannot be verified
    changes nothing and says so, with the same codes as verification.
    """
    _limit(request, user, "restore")
    return await _process(body, request, user, db, VerifyKind.RESTORE)

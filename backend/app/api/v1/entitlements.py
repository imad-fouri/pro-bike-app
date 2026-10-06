"""The rider's own entitlement state. Read-only by design.

There is intentionally no `/users/{id}/entitlements` route. A parameterized
route would create IDOR surface for data that has no administrative consumer in
this workstream. Ordinary clients read only `/me/entitlements`; there is no
ordinary-client mutation route at all.
"""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.db.session import get_db
from app.models.user import User
from app.schemas.subscription import EntitlementOut, EntitlementStateOut
from app.services.subscription_service import FREE_CAPABILITIES, resolve_state

router = APIRouter(tags=["entitlements"])


@router.get("/me/entitlements", response_model=EntitlementStateOut)
async def get_my_entitlements(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> EntitlementStateOut:
    state = await resolve_state(db, user.id)
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

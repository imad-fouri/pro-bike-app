"""Provider-neutral store product catalog (read-only, provisional).

The single normalization table a future billing adapter uses to turn a store
product into an internal plan:

    Store Product → plan_for_product → Plan → subscription event → Entitlement

No adapter may map product ids inline; inline mapping is how two providers
end up disagreeing about what "pro_monthly" means.

The ids are PROVISIONAL internal names, mirrored in
`mobile/lib/features/subscriptions/domain/store_products.dart`. They are not
registered App Store or Play product ids, and claiming them requires the
stores' own flows, which have not happened.

Deliberately absent: prices, currencies, trials metadata, introductory
offers. Prices are store-owned and localized; persisting or trusting them
here would make the backend authoritative about money it does not control.
"""

from app.models.subscription import Plan

CYCLECOACH_PRO_MONTHLY = "cyclecoach_pro_monthly"
CYCLECOACH_PRO_YEARLY = "cyclecoach_pro_yearly"

#: product id → internal plan. Both products confer Pro; they differ only in
#: billing cadence, which is a commercial fact the entitlement layer does not
#: need (an entitlement is a capability window, not a receipt).
STORE_PRODUCTS: dict[str, Plan] = {
    CYCLECOACH_PRO_MONTHLY: Plan.PRO,
    CYCLECOACH_PRO_YEARLY: Plan.PRO,
}


def plan_for_product(product_id: str) -> Plan | None:
    """Map a store product id to its internal plan.

    Returns None for unknown ids so callers must handle "not ours"
    explicitly. Defaulting an unknown product to any plan would grant
    access on the basis of a string the server has never seen.
    """
    return STORE_PRODUCTS.get(product_id.strip())

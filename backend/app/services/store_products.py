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

WS-RP Phase 2 adds the provider-facing half of the catalog: the canonical
`ProviderProduct` registry (`PROVIDER_PRODUCTS`) that maps a registered
provider-side product id to an internal product. It is EMPTY until the account
holder registers real App Store / Play products (docs/store-provider-audit.md
O3), which is a fail-closed default: an unknown provider product id resolves
to nothing, not to a grant.

Deliberately absent: prices, currencies, trials metadata, introductory
offers. Prices are store-owned and localized; persisting or trusting them
here would make the backend authoritative about money it does not control.
"""

import enum
from dataclasses import dataclass

from app.models.subscription import Plan, SubscriptionProvider


class ProductPlatform(str, enum.Enum):
    """The client platform a provider product is sold on."""

    IOS = "ios"
    ANDROID = "android"


class ProductType(str, enum.Enum):
    """What kind of commercial product a provider id names.

    Only subscriptions exist in the catalog today; consumables and one-time
    purchases are deliberately unrepresentable so a verifier cannot mistake
    them for an entitlement.
    """

    SUBSCRIPTION = "subscription"


@dataclass(frozen=True)
class ProviderProduct:
    """One registered store-side product id bound to an internal product.

    This is the lookup direction a provider adapter MUST use (never inline
    id mapping): provider + provider_product_id → internal product. The
    internal product carries its entitlement plan; the adapter returns the
    internal `product_id` so the WS-PV ownership/product binding compares
    internal ids on both sides.
    """

    provider: SubscriptionProvider
    provider_product_id: str
    product_id: str
    platform: ProductPlatform
    product_type: ProductType
    plan: Plan
    active: bool = True


CYCLECOACH_PRO_MONTHLY = "cyclecoach_pro_monthly"
CYCLECOACH_PRO_YEARLY = "cyclecoach_pro_yearly"

#: product id → internal plan. Both products confer Pro; they differ only in
#: billing cadence, which is a commercial fact the entitlement layer does not
#: need (an entitlement is a capability window, not a receipt).
STORE_PRODUCTS: dict[str, Plan] = {
    CYCLECOACH_PRO_MONTHLY: Plan.PRO,
    CYCLECOACH_PRO_YEARLY: Plan.PRO,
}

#: provider → provider_product_id → ProviderProduct. Deliberately empty in
#: every shipped configuration until real store products are registered.
#: A product the server has never seen resolves to nothing (fail closed).
_PROVIDER_PRODUCTS: dict[SubscriptionProvider, dict[str, ProviderProduct]] = {}


def plan_for_product(product_id: str) -> Plan | None:
    """Map a store product id to its internal plan.

    Returns None for unknown ids so callers must handle "not ours"
    explicitly. Defaulting an unknown product to any plan would grant
    access on the basis of a string the server has never seen.
    """
    return STORE_PRODUCTS.get(product_id.strip())


def register_provider_product(product: ProviderProduct) -> None:
    """Register one provider-side product in the catalog.

    Registration is additive and idempotent for the same provider+id pair.
    MANUAL is not a store, so a "manual product" cannot be registered.
    """
    if product.provider is SubscriptionProvider.MANUAL:
        raise ValueError("manual is not a purchase provider")
    _PROVIDER_PRODUCTS.setdefault(product.provider, {})[product.provider_product_id] = product


def reset_provider_products() -> None:
    """Empty the provider-product registry. Test hook."""
    _PROVIDER_PRODUCTS.clear()


def provider_product_for(
    provider: SubscriptionProvider, provider_product_id: str
) -> ProviderProduct | None:
    """Resolve one registered provider-side product.

    Returns None for an unknown provider, an unknown id, or an inactive
    product — never a partial grant. Adapters MUST route every verified
    provider product id through here.
    """
    product = _PROVIDER_PRODUCTS.get(provider, {}).get(provider_product_id.strip())
    if product is None or not product.active:
        return None
    return product

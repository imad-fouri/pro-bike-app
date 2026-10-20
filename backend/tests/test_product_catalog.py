"""WS-RP Phase 2 tests: the provider-product catalog registry.

The provider side of the catalog is fail-closed by construction: until real
store products are registered (docs/store-provider-audit.md O3), every
provider product id resolves to None, and an inactive product resolves to
None too. These tests pin that default plus the registration contract the
Apple/Google adapters will depend on.
"""

import pytest

from app.models.subscription import Plan, SubscriptionProvider
from app.services.store_products import (
    CYCLECOACH_PRO_MONTHLY,
    ProductPlatform,
    ProductType,
    ProviderProduct,
    plan_for_product,
    provider_product_for,
    register_provider_product,
    reset_provider_products,
)


@pytest.fixture(autouse=True)
def _clean_products():
    reset_provider_products()
    yield
    reset_provider_products()


def _product(
    provider: SubscriptionProvider = SubscriptionProvider.APP_STORE,
    provider_product_id: str = "com.example.cyclecoach.pro.monthly",
    *,
    product_id: str = CYCLECOACH_PRO_MONTHLY,
    active: bool = True,
) -> ProviderProduct:
    return ProviderProduct(
        provider=provider,
        provider_product_id=provider_product_id,
        product_id=product_id,
        platform=ProductPlatform.IOS
        if provider is SubscriptionProvider.APP_STORE
        else ProductPlatform.ANDROID,
        product_type=ProductType.SUBSCRIPTION,
        plan=Plan.PRO,
        active=active,
    )


def test_registry_is_empty_by_default():
    # Fail closed: no registered store product, ever, until the account
    # holder registers real ids. An unknown product granted nothing.
    assert provider_product_for(SubscriptionProvider.APP_STORE, "com.example.pro.monthly") is None
    assert provider_product_for(SubscriptionProvider.PLAY_STORE, "cyclecoach.pro.monthly") is None
    assert provider_product_for(SubscriptionProvider.PLAY_STORE, "") is None


def test_registered_product_resolves_with_full_metadata():
    register_provider_product(_product())
    product = provider_product_for(
        SubscriptionProvider.APP_STORE, "com.example.cyclecoach.pro.monthly"
    )
    assert product is not None
    assert product.product_id == CYCLECOACH_PRO_MONTHLY
    assert product.plan is Plan.PRO
    assert product.platform is ProductPlatform.IOS
    assert product.product_type is ProductType.SUBSCRIPTION
    assert product.active is True


def test_ids_are_stripped_and_ids_are_distinct_per_provider():
    register_provider_product(_product())
    assert (
        provider_product_for(
            SubscriptionProvider.APP_STORE, "  com.example.cyclecoach.pro.monthly  "
        )
        is not None
    )
    # A Google product with the same provider_product_id string is a
    # different product; the App Store registration must not leak across.
    assert (
        provider_product_for(SubscriptionProvider.PLAY_STORE, "com.example.cyclecoach.pro.monthly")
        is None
    )
    google = _product(
        SubscriptionProvider.PLAY_STORE,
        "cyclecoach.pro.monthly",
        product_id=CYCLECOACH_PRO_MONTHLY,
    )
    register_provider_product(google)
    assert provider_product_for(SubscriptionProvider.PLAY_STORE, "cyclecoach.pro.monthly") is google


def test_inactive_product_resolves_to_nothing():
    register_provider_product(_product(active=False))
    assert (
        provider_product_for(SubscriptionProvider.APP_STORE, "com.example.cyclecoach.pro.monthly")
        is None
    )


def test_manual_products_cannot_be_registered():
    with pytest.raises(ValueError):
        register_provider_product(_product(SubscriptionProvider.MANUAL))


def test_provider_products_do_not_leak_into_the_plan_table():
    # The internal plan table (STORE_PRODUCTS / plan_for_product) is the
    # wire contract the client and the WS-PV pipeline use; registering a
    # store product must not add a purchasable internal id.
    assert plan_for_product(CYCLECOACH_PRO_MONTHLY) is Plan.PRO
    assert plan_for_product("com.example.cyclecoach.pro.monthly") is None

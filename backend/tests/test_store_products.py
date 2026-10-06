"""Phase 10 WS-SM backend tests: the provisional store product catalog.

The catalog is a read-only normalization table, not a purchase system:
product id in, internal plan out, unknown ids rejected. There is no price,
no currency, no receipt handling, and no purchase endpoint — and the tests
below pin all of those absences so a future addition must update this file
deliberately rather than slipping in.
"""

import uuid

from app.models.subscription import Plan
from app.services import store_products
from app.services.store_products import (
    CYCLECOACH_PRO_MONTHLY,
    CYCLECOACH_PRO_YEARLY,
    STORE_PRODUCTS,
    plan_for_product,
)

AUTH = "/api/v1/auth"


async def _account(client, tag: str) -> dict[str, object]:
    payload = {
        "email": f"store-{tag}-{uuid.uuid4().hex[:8]}@example.com",
        "password": "StrongPass123",
        "password_confirm": "StrongPass123",
        "display_name": f"Rider {tag}",
    }
    registered = await client.post(f"{AUTH}/register", json=payload)
    assert registered.status_code == 201, registered.text
    logged_in = await client.post(
        f"{AUTH}/login", json={"email": payload["email"], "password": payload["password"]}
    )
    assert logged_in.status_code == 200, logged_in.text
    headers = {"Authorization": f"Bearer {logged_in.json()['access_token']}"}
    return {"headers": headers}


def test_catalog_holds_exactly_the_two_provisional_products():
    assert set(STORE_PRODUCTS) == {CYCLECOACH_PRO_MONTHLY, CYCLECOACH_PRO_YEARLY}
    assert CYCLECOACH_PRO_MONTHLY == "cyclecoach_pro_monthly"
    assert CYCLECOACH_PRO_YEARLY == "cyclecoach_pro_yearly"
    assert set(STORE_PRODUCTS.values()) == {Plan.PRO}


def test_known_products_normalize_to_pro():
    assert plan_for_product(CYCLECOACH_PRO_MONTHLY) is Plan.PRO
    assert plan_for_product(CYCLECOACH_PRO_YEARLY) is Plan.PRO
    assert plan_for_product(f"  {CYCLECOACH_PRO_MONTHLY}  ") is Plan.PRO


def test_unknown_products_normalize_to_nothing():
    assert plan_for_product("not_ours") is None
    assert plan_for_product("") is None
    assert plan_for_product("cyclecoach_pro_lifetime") is None
    assert plan_for_product("CYCLECOACH_PRO_MONTHLY") is None


def test_catalog_carries_no_money():
    # Prices are store-owned and localized; a price here would make the
    # backend authoritative about money it does not control. If pricing ever
    # belongs in this codebase, this test names the file that must justify
    # it.
    module_names = set(dir(store_products))
    assert not {name for name in module_names if "price" in name.lower()}
    assert not {name for name in module_names if "currency" in name.lower()}
    assert not {
        name for name in module_names if "trial" in name.lower() and "status" not in name.lower()
    }


async def test_no_purchase_verification_route_exists(client):
    account = await _account(client, "no-verify")
    for path in (
        "/api/v1/purchases/verify",
        "/api/v1/purchases",
        "/api/v1/store/verify",
        "/api/v1/billing/verify",
        "/api/v1/subscriptions/purchase",
    ):
        res = await client.post(path, headers=account["headers"], json={})
        assert res.status_code == 404, f"{path} -> {res.status_code}"


async def test_client_is_pro_claims_are_not_purchase_paths(client):
    account = await _account(client, "no-fake-buy")
    for body in ({"is_pro": True}, {"plan": "pro"}, {"product_id": CYCLECOACH_PRO_MONTHLY}):
        res = await client.post("/api/v1/me/entitlements", headers=account["headers"], json=body)
        assert res.status_code == 405, f"{body} -> {res.status_code}"


async def test_openapi_has_no_purchase_or_billing_surface(client):
    res = await client.get("/openapi.json")
    assert res.status_code == 200, res.text
    paths = list(res.json().get("paths", {}).keys())
    assert paths, "the route table must not be empty"
    # Whole path segments, not substrings: `verify-email` and `restore` are
    # legitimate routes that a substring match would falsely accuse.
    forbidden = {"purchases", "purchase", "verify", "billing", "checkout", "store", "is_pro"}
    hits = [
        path
        for path in paths
        for segment in path.strip("/").split("/")
        if segment.lower() in forbidden
    ]
    # WS-PV adds exactly two authenticated verification routes. They are the
    # only purchase-shaped paths allowed to exist; anything else is a new
    # billing surface that must update this test deliberately.
    assert set(hits) == {
        "/api/v1/store/purchases/verify",
        "/api/v1/store/purchases/restore",
    }, f"unexpected purchase-shaped routes: {hits}"

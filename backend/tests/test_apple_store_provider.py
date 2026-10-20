"""WS-RP Phase 3: Apple adapter contract tests.

Every test exercises real ES256 + x5c JWS verification against a fresh
throwaway trust root and an injected httpx transport. No live App Store is
contacted (Phase 15 discipline); the adapter's HTTP edge is a MockTransport.
"""

import uuid
from datetime import UTC, datetime, timedelta

import httpx
import jwt
import pytest

from app.models.subscription import Plan, SubscriptionProvider, SubscriptionStatus
from app.services.apple_store_provider import AppleStoreProvider
from app.services.store_products import (
    CYCLECOACH_PRO_MONTHLY,
    ProductPlatform,
    ProductType,
    ProviderProduct,
    register_provider_product,
    reset_provider_products,
)
from app.services.subscription_providers import VerificationEnvironment
from app.services.subscription_service import EntitlementError
from tests.storekit_fixtures import (
    BUNDLE_ID,
    PROVIDER_PRODUCT_ID,
    AppleSigner,
    ms,
    renewal_payload,
    status_response,
    transaction_payload,
)

TEST_BUNDLE_ID = BUNDLE_ID
TEST_ISSUER = "69a6de83-0000-47e3-e053-5b8c7c11a4d1"
TEST_KEY_ID = "TESTKEY"
TEST_RIDER = uuid.UUID("12345678-1234-5678-1234-567812345678")
_SETTINGS = "app.services.apple_store_provider.default_settings"


@pytest.fixture(autouse=True)
def _clean_catalog():
    reset_provider_products()
    yield
    reset_provider_products()


@pytest.fixture()
def configure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(f"{_SETTINGS}.APPLE_STORE_ENABLED", True)
    monkeypatch.setattr(f"{_SETTINGS}.APPLE_API_ENVIRONMENT", "production")
    monkeypatch.setattr(f"{_SETTINGS}.APPLE_BUNDLE_ID", TEST_BUNDLE_ID)
    monkeypatch.setattr(f"{_SETTINGS}.APPLE_ISSUER_ID", TEST_ISSUER)
    monkeypatch.setattr(f"{_SETTINGS}.APPLE_KEY_ID", TEST_KEY_ID)
    monkeypatch.setattr(f"{_SETTINGS}.APPLE_PRIVATE_KEY_PATH", "/tmp/cyclecoach.p8")
    monkeypatch.setattr(f"{_SETTINGS}.APPLE_REQUEST_TIMEOUT_SECONDS", 5)


@pytest.fixture()
def signer() -> AppleSigner:
    return AppleSigner()


@pytest.fixture()
def catalog():
    register_provider_product(
        ProviderProduct(
            provider=SubscriptionProvider.APP_STORE,
            provider_product_id=PROVIDER_PRODUCT_ID,
            product_id=CYCLECOACH_PRO_MONTHLY,
            platform=ProductPlatform.IOS,
            product_type=ProductType.SUBSCRIPTION,
            plan=Plan.PRO,
            active=True,
        )
    )


def build_provider(
    signer: AppleSigner,
    response: dict,
    *,
    status_code: int = 200,
) -> tuple[AppleStoreProvider, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(status_code, json=response)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = AppleStoreProvider(
        http=client,
        root_certs=[signer.root_pem()],
        private_key_pem=signer.leaf_private_key_pem(),
    )
    return provider, seen


def now() -> datetime:
    return datetime.now(UTC)


@pytest.mark.asyncio
async def test_verify_active_purchase(configure, signer, catalog):
    tx = transaction_payload(transaction_id="tx-1", app_account_token=str(TEST_RIDER))
    provider, _ = build_provider(signer, status_response(signer, transaction=tx))

    verified = await provider.verify_purchase(purchase_token="tx-1", expected_user_id=TEST_RIDER)

    assert verified.provider is SubscriptionProvider.APP_STORE
    assert verified.provider_subscription_id == "orig-tx-0001"
    assert verified.provider_transaction_id == "tx-1"
    assert verified.user_id == TEST_RIDER
    assert verified.plan is Plan.PRO
    assert verified.status is SubscriptionStatus.ACTIVE
    assert verified.environment is VerificationEnvironment.PRODUCTION
    assert verified.product_id == CYCLECOACH_PRO_MONTHLY
    assert verified.cancel_at_period_end is False
    assert verified.current_period_start.date() < now().date()
    assert verified.current_period_end.date() > now().date()


@pytest.mark.asyncio
async def test_verify_with_original_token_resolves_restore(configure, signer, catalog):
    tx = transaction_payload(
        transaction_id="tx-2",
        original_transaction_id="orig-tx-2",
        app_account_token=str(TEST_RIDER),
    )
    provider, _ = build_provider(signer, status_response(signer, transaction=tx))
    verified = await provider.verify_purchase(
        purchase_token="orig-tx-2", expected_user_id=TEST_RIDER
    )
    assert verified.provider_subscription_id == "orig-tx-2"
    assert verified.status is SubscriptionStatus.ACTIVE


@pytest.mark.asyncio
async def test_cancel_at_period_end_flips_flag(configure, signer, catalog):
    tx = transaction_payload(transaction_id="tx-3", app_account_token=str(TEST_RIDER))
    renewal = renewal_payload(original_transaction_id="orig-tx-0001", auto_renew_status=0)
    provider, _ = build_provider(signer, status_response(signer, transaction=tx, renewal=renewal))
    verified = await provider.verify_purchase(purchase_token="tx-3", expected_user_id=TEST_RIDER)
    assert verified.status is SubscriptionStatus.ACTIVE
    assert verified.cancel_at_period_end is True


@pytest.mark.asyncio
async def test_expired_subscription(configure, signer, catalog):
    tx = transaction_payload(
        transaction_id="tx-expired",
        app_account_token=str(TEST_RIDER),
        expires_date=now() - timedelta(days=1),
    )
    provider, _ = build_provider(signer, status_response(signer, transaction=tx))
    verified = await provider.verify_purchase(
        purchase_token="tx-expired", expected_user_id=TEST_RIDER
    )
    assert verified.status is SubscriptionStatus.EXPIRED


@pytest.mark.asyncio
async def test_revoked_subscription(configure, signer, catalog):
    tx = transaction_payload(
        transaction_id="tx-revoked",
        app_account_token=str(TEST_RIDER),
        revocation_date=now() - timedelta(hours=1),
    )
    provider, _ = build_provider(signer, status_response(signer, transaction=tx))
    verified = await provider.verify_purchase(
        purchase_token="tx-revoked", expected_user_id=TEST_RIDER
    )
    assert verified.status is SubscriptionStatus.REVOKED


@pytest.mark.asyncio
async def test_sandbox_purchase_maps_environment(configure, signer, catalog):
    tx = transaction_payload(
        transaction_id="tx-sand", environment="Sandbox", app_account_token=str(TEST_RIDER)
    )
    provider, _ = build_provider(signer, status_response(signer, transaction=tx))
    verified = await provider.verify_purchase(purchase_token="tx-sand", expected_user_id=TEST_RIDER)
    assert verified.environment is VerificationEnvironment.SANDBOX


@pytest.mark.asyncio
async def test_wrong_bundle_rejected(configure, signer, catalog):
    tx = transaction_payload(
        transaction_id="tx-other-app",
        bundle_id="com.evil.app",
        app_account_token=str(TEST_RIDER),
    )
    provider, _ = build_provider(signer, status_response(signer, transaction=tx))
    with pytest.raises(EntitlementError) as exc:
        await provider.verify_purchase(purchase_token="tx-other-app", expected_user_id=TEST_RIDER)
    assert exc.value.code == "PURCHASE_INVALID"


@pytest.mark.asyncio
async def test_unknown_product_rejected(configure, signer):
    tx = transaction_payload(
        transaction_id="tx-unknown-product",
        product_id="com.example.theirs.monthly",
        app_account_token=str(TEST_RIDER),
    )
    provider, _ = build_provider(signer, status_response(signer, transaction=tx))
    with pytest.raises(EntitlementError) as exc:
        await provider.verify_purchase(
            purchase_token="tx-unknown-product", expected_user_id=TEST_RIDER
        )
    assert exc.value.code == "UNKNOWN_PRODUCT"


@pytest.mark.asyncio
async def test_cross_user_rejected(configure, signer, catalog):
    other = str(uuid.uuid4())
    tx = transaction_payload(transaction_id="tx-other-rider", app_account_token=other)
    provider, _ = build_provider(signer, status_response(signer, transaction=tx))
    with pytest.raises(EntitlementError) as exc:
        await provider.verify_purchase(purchase_token="tx-other-rider", expected_user_id=TEST_RIDER)
    assert exc.value.code == "CROSS_USER_PURCHASE"


@pytest.mark.asyncio
async def test_missing_rider_binding_rejected(configure, signer, catalog):
    tx = transaction_payload(transaction_id="tx-nobinding", app_account_token=None)
    provider, _ = build_provider(signer, status_response(signer, transaction=tx))
    with pytest.raises(EntitlementError) as exc:
        await provider.verify_purchase(purchase_token="tx-nobinding", expected_user_id=TEST_RIDER)
    assert exc.value.code == "CROSS_USER_PURCHASE"


@pytest.mark.asyncio
async def test_tampered_payload_rejected(configure, signer, catalog):
    tx = transaction_payload(transaction_id="tx-1", app_account_token=str(TEST_RIDER))
    body = status_response(signer, transaction=tx)
    tampered = body["data"][0]["lastTransactions"][0]
    tampered["signedTransactionInfo"] = (
        tampered["signedTransactionInfo"].rsplit(".", 1)[0] + ".AAAA"
    )
    provider, _ = build_provider(signer, body)
    with pytest.raises(EntitlementError) as exc:
        await provider.verify_purchase(purchase_token="tx-1", expected_user_id=TEST_RIDER)
    assert exc.value.code == "PURCHASE_INVALID"


@pytest.mark.asyncio
async def test_untrusted_root_rejected(configure, signer, catalog):
    other_root = AppleSigner()  # signature chain terminates at a DIFFERENT root
    tx = transaction_payload(transaction_id="tx-1", app_account_token=str(TEST_RIDER))
    body = status_response(other_root, transaction=tx)
    provider, _ = build_provider(signer, body)  # provider trusts signer.root only
    with pytest.raises(EntitlementError) as exc:
        await provider.verify_purchase(purchase_token="tx-1", expected_user_id=TEST_RIDER)
    assert exc.value.code == "PURCHASE_INVALID"


@pytest.mark.asyncio
async def test_not_found_transaction_rejected(configure, signer, catalog):
    provider, _ = build_provider(signer, {"errorCode": 4005003}, status_code=404)
    with pytest.raises(EntitlementError) as exc:
        await provider.verify_purchase(purchase_token="nx", expected_user_id=TEST_RIDER)
    assert exc.value.code == "PURCHASE_INVALID"


@pytest.mark.asyncio
async def test_provider_error_is_opaque(configure, signer, catalog):
    provider, _ = build_provider(signer, {"errorCode": 4005000}, status_code=500)
    with pytest.raises(EntitlementError) as exc:
        await provider.verify_purchase(purchase_token="tx-1", expected_user_id=TEST_RIDER)
    assert exc.value.code == "PROVIDER_ERROR"


@pytest.mark.asyncio
async def test_caller_jwt_is_signed_with_configured_kid(configure, signer, catalog):
    tx = transaction_payload(transaction_id="tx-1", app_account_token=str(TEST_RIDER))
    provider, seen = build_provider(signer, status_response(signer, transaction=tx))
    await provider.verify_purchase(purchase_token="tx-1", expected_user_id=TEST_RIDER)
    assert len(seen) == 1
    header = jwt.get_unverified_header(seen[0].headers["Authorization"].removeprefix("Bearer "))
    assert header["kid"] == TEST_KEY_ID
    assert header["alg"] == "ES256"


@pytest.mark.asyncio
async def test_non_subscription_rejected(configure, signer, catalog):
    tx = transaction_payload(
        transaction_id="tx-consumable",
        type_="Non-RenewingSubscription",
        app_account_token=str(TEST_RIDER),
    )
    provider, _ = build_provider(signer, status_response(signer, transaction=tx))
    with pytest.raises(EntitlementError) as exc:
        await provider.verify_purchase(purchase_token="tx-consumable", expected_user_id=TEST_RIDER)
    assert exc.value.code == "PURCHASE_INVALID"


@pytest.mark.asyncio
async def test_process_event_did_renew(configure, signer, catalog):
    tx = transaction_payload(transaction_id="tx-renew", app_account_token=str(TEST_RIDER))
    payload = {
        "signedPayload": signer.sign(
            {
                "notificationType": "DID_RENEW",
                "signedDate": ms(now()),
                "data": {
                    "signedTransactionInfo": signer.sign(tx),
                    "signedRenewalInfo": signer.sign(
                        renewal_payload(original_transaction_id="orig-tx-0001")
                    ),
                },
            }
        )
    }
    provider = AppleStoreProvider(root_certs=[signer.root_pem()])
    event = await provider.process_event(payload=payload, signature=None)
    assert event.provider is SubscriptionProvider.APP_STORE
    assert event.status is SubscriptionStatus.ACTIVE
    assert event.user_id == TEST_RIDER
    assert event.plan is Plan.PRO
    assert event.provider_subscription_id == "orig-tx-0001"
    assert event.provider_event_id.startswith("apple:DID_RENEW:tx-renew:")
    assert event.cancel_at_period_end is False


@pytest.mark.asyncio
async def test_process_event_refund_revokes(configure, signer, catalog):
    tx = transaction_payload(transaction_id="tx-refunded", app_account_token=str(TEST_RIDER))
    payload = {
        "signedPayload": signer.sign(
            {
                "notificationType": "REFUND",
                "signedDate": ms(now()),
                "data": {"signedTransactionInfo": signer.sign(tx)},
            }
        )
    }
    provider = AppleStoreProvider(root_certs=[signer.root_pem()])
    event = await provider.process_event(payload=payload, signature=None)
    assert event.status is SubscriptionStatus.REVOKED


@pytest.mark.asyncio
async def test_process_event_change_renewal_status_cancels(configure, signer, catalog):
    tx = transaction_payload(transaction_id="tx-cancels", app_account_token=str(TEST_RIDER))
    payload = {
        "signedPayload": signer.sign(
            {
                "notificationType": "DID_CHANGE_RENEWAL_STATUS",
                "subtype": "AUTO_RENEW_DISABLED",
                "signedDate": ms(now()),
                "data": {"signedTransactionInfo": signer.sign(tx)},
            }
        )
    }
    provider = AppleStoreProvider(root_certs=[signer.root_pem()])
    event = await provider.process_event(payload=payload, signature=None)
    assert event.status is SubscriptionStatus.CANCELED
    assert event.cancel_at_period_end is True


def test_configured_requires_everything(monkeypatch: pytest.MonkeyPatch):
    provider = AppleStoreProvider()
    monkeypatch.setattr(f"{_SETTINGS}.APPLE_STORE_ENABLED", False)
    monkeypatch.setattr(f"{_SETTINGS}.APPLE_API_ENVIRONMENT", "production")
    monkeypatch.setattr(f"{_SETTINGS}.APPLE_BUNDLE_ID", TEST_BUNDLE_ID)
    monkeypatch.setattr(f"{_SETTINGS}.APPLE_ISSUER_ID", TEST_ISSUER)
    monkeypatch.setattr(f"{_SETTINGS}.APPLE_KEY_ID", TEST_KEY_ID)
    monkeypatch.setattr(f"{_SETTINGS}.APPLE_PRIVATE_KEY_PATH", "/tmp/cyclecoach.p8")
    assert provider.configured() is False  # disabled -> not configured

    monkeypatch.setattr(f"{_SETTINGS}.APPLE_STORE_ENABLED", True)
    assert provider.configured() is True

    monkeypatch.setattr(f"{_SETTINGS}.APPLE_PRIVATE_KEY_PATH", "   ")
    assert provider.configured() is False  # enabled but key material missing

    monkeypatch.setattr(f"{_SETTINGS}.APPLE_PRIVATE_KEY_PATH", "/tmp/cyclecoach.p8")
    monkeypatch.setattr(f"{_SETTINGS}.APPLE_API_ENVIRONMENT", "production")
    assert provider.configured() is True

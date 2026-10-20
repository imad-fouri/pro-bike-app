"""WS-RP Phase 4: Google Play adapter tests.

Deterministic everywhere: a throwaway RSA service-account credential, real
RS256 OAuth2 assertion -> token exchange, and an injected httpx MockTransport
that routes the OAuth endpoint and `purchases.subscriptionsv2.get` exactly
like production URLs. No live Google API, no credentials in the repo, and no
client-declared environment anywhere.
"""

import urllib.parse
import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import func, select

from app.models.subscription import (
    Plan,
    Subscription,
    SubscriptionProvider,
    SubscriptionStatus,
)
from app.services.apple_store_provider import AppleStoreProvider
from app.services.google_play_provider import GooglePlayClient, GoogleStoreProvider
from app.services.purchase_verification import (
    VerifyKind,
    process_purchase,
    register_provider,
    registered_providers,
    reset_providers,
)
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
from tests.google_fixtures import (
    PACKAGE_NAME,
    PLAY_PRODUCT_ID,
    GoogleSigner,
    full_refund_context,
    revocation_context,
    rtdn_push,
    subscription_v2,
    user_cancel_context,
)
from tests.storekit_fixtures import AppleSigner, status_response, transaction_payload

TEST_RIDER = uuid.UUID("12345678-1234-5678-1234-567812345678")
_SETTINGS = "app.services.google_play_provider.default_settings"


@pytest.fixture(autouse=True)
def _clean_state():
    reset_providers()
    reset_provider_products()
    yield
    reset_providers()
    reset_provider_products()


@pytest.fixture()
def configure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(f"{_SETTINGS}.GOOGLE_PLAY_ENABLED", True)
    monkeypatch.setattr(f"{_SETTINGS}.GOOGLE_PACKAGE_NAME", PACKAGE_NAME)
    monkeypatch.setattr(f"{_SETTINGS}.GOOGLE_APPLICATION_CREDENTIALS_PATH", "/tmp/gac.json")
    monkeypatch.setattr(f"{_SETTINGS}.GOOGLE_REQUEST_TIMEOUT_SECONDS", 5)


@pytest.fixture()
def signer() -> GoogleSigner:
    return GoogleSigner()


@pytest.fixture()
def catalog():
    register_provider_product(
        ProviderProduct(
            provider=SubscriptionProvider.PLAY_STORE,
            provider_product_id=PLAY_PRODUCT_ID,
            product_id=CYCLECOACH_PRO_MONTHLY,
            platform=ProductPlatform.ANDROID,
            product_type=ProductType.SUBSCRIPTION,
            plan=Plan.PRO,
            active=True,
        )
    )


def build_provider(
    signer: GoogleSigner, publisher
) -> tuple[GoogleStoreProvider, list[httpx.Request]]:
    """Wire a provider to a MockTransport that serves OAuth + publisher."""

    def _routed(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        url = str(request.url)
        if "oauth2.googleapis.com/token" in url:
            assert "assertion=" in request.content.decode("latin-1")
            return httpx.Response(
                200, json={"access_token": "at-test", "token_type": "Bearer", "expires_in": 3600}
            )
        if url.endswith(":acknowledge"):
            return httpx.Response(204)
        return publisher(request)

    seen: list[httpx.Request] = []
    client = httpx.AsyncClient(transport=httpx.MockTransport(_routed))
    provider = GoogleStoreProvider(http=client, credentials=signer.credentials())
    return provider, seen


def ok_publisher(body: dict):
    def _pub(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=body)

    return _pub


def now() -> datetime:
    return datetime.now(UTC)


# ---------------------------------------------------------------------------
# Configuration / registry
# ---------------------------------------------------------------------------


def test_configured_fail_closed_without_configuration(signer):
    assert GoogleStoreProvider(credentials=signer.credentials()).configured() is False


def test_configured_requires_package_and_credentials(configure, signer, monkeypatch):
    provider = GoogleStoreProvider(credentials=signer.credentials())
    assert provider.configured() is True
    monkeypatch.setattr(f"{_SETTINGS}.GOOGLE_PLAY_ENABLED", False)
    assert provider.configured() is False
    monkeypatch.setattr(f"{_SETTINGS}.GOOGLE_PLAY_ENABLED", True)
    monkeypatch.setattr(f"{_SETTINGS}.GOOGLE_PACKAGE_NAME", "   ")
    assert provider.configured() is False
    assert GoogleStoreProvider(credentials=None).configured() is False


def test_default_registry_stays_empty():
    assert SubscriptionProvider.PLAY_STORE not in registered_providers()


# ---------------------------------------------------------------------------
# Verification: happy path
# ---------------------------------------------------------------------------


async def test_verify_active_purchase(configure, signer, catalog):
    body = subscription_v2(purchase_token="tok-active", obfuscated_rider=str(TEST_RIDER))
    provider, seen = build_provider(signer, ok_publisher(body))

    verified = await provider.verify_purchase(
        purchase_token="tok-active", expected_user_id=TEST_RIDER
    )

    assert verified.provider is SubscriptionProvider.PLAY_STORE
    assert verified.provider_subscription_id == "tok-active"
    assert verified.user_id == TEST_RIDER
    assert verified.plan is Plan.PRO
    assert verified.status is SubscriptionStatus.ACTIVE
    assert verified.environment is VerificationEnvironment.PRODUCTION
    assert verified.product_id == CYCLECOACH_PRO_MONTHLY
    assert verified.cancel_at_period_end is False
    assert verified.current_period_start.date() < now().date()
    assert verified.current_period_end.date() > now().date()
    requested = seen[-1]
    assert PACKAGE_NAME in requested.url.path
    assert "subscriptionsv2/tokens/tok-active" in requested.url.path
    assert "subscriptions/get/" not in requested.url.path  # deprecated endpoint never used


async def test_package_is_server_configured_not_client(configure, signer, catalog):
    # The request path is built from server config; a foreign package can
    # never be claimed because the provider only ever queries the configured
    # application identity.
    provider, seen = build_provider(
        signer, ok_publisher(subscription_v2(obfuscated_rider=str(TEST_RIDER)))
    )
    await provider.verify_purchase(purchase_token="tok", expected_user_id=TEST_RIDER)
    assert PACKAGE_NAME in seen[-1].url.path


async def test_purchase_token_is_only_ever_used_for_the_lookup(configure, signer, catalog):
    body = subscription_v2(obfuscated_rider=str(TEST_RIDER))
    provider, _ = build_provider(signer, ok_publisher(body))
    verified = await provider.verify_purchase(
        purchase_token="secret-tok", expected_user_id=TEST_RIDER
    )
    # Token appears in the legacy identity by design (audit §8.1) — never in
    # logs, metrics, or error text (covered by dedicated tests below).
    assert verified.provider_subscription_id == "secret-tok"


# ---------------------------------------------------------------------------
# Verification: rejection paths
# ---------------------------------------------------------------------------


async def test_invalid_purchase_token_400(configure, signer, catalog):
    provider, _ = build_provider(signer, lambda req: httpx.Response(400, json={}))
    with pytest.raises(EntitlementError) as exc:
        await provider.verify_purchase(purchase_token="nope", expected_user_id=TEST_RIDER)
    assert exc.value.code == "PURCHASE_INVALID"
    assert "nope" not in exc.value.message


async def test_token_not_found_404(configure, signer, catalog):
    provider, _ = build_provider(signer, lambda req: httpx.Response(404, json={}))
    with pytest.raises(EntitlementError) as exc:
        await provider.verify_purchase(purchase_token="missing", expected_user_id=TEST_RIDER)
    assert exc.value.code == "PURCHASE_INVALID"


async def test_wrong_product_rejected(configure, signer):
    body = subscription_v2(product_id="someone-elses.product", obfuscated_rider=str(TEST_RIDER))
    provider, _ = build_provider(signer, ok_publisher(body))
    with pytest.raises(EntitlementError) as exc:
        await provider.verify_purchase(purchase_token="tok", expected_user_id=TEST_RIDER)
    assert exc.value.code == "UNKNOWN_PRODUCT"


async def test_inactive_catalog_product_rejected(configure, signer):
    register_provider_product(
        ProviderProduct(
            provider=SubscriptionProvider.PLAY_STORE,
            provider_product_id=PLAY_PRODUCT_ID,
            product_id=CYCLECOACH_PRO_MONTHLY,
            platform=ProductPlatform.ANDROID,
            product_type=ProductType.SUBSCRIPTION,
            plan=Plan.PRO,
            active=False,
        )
    )
    body = subscription_v2(obfuscated_rider=str(TEST_RIDER))
    provider, _ = build_provider(signer, ok_publisher(body))
    with pytest.raises(EntitlementError) as exc:
        await provider.verify_purchase(purchase_token="tok", expected_user_id=TEST_RIDER)
    assert exc.value.code == "UNKNOWN_PRODUCT"


async def test_cross_user_rejected(configure, signer, catalog):
    body = subscription_v2(obfuscated_rider=str(uuid.uuid4()))
    provider, _ = build_provider(signer, ok_publisher(body))
    with pytest.raises(EntitlementError) as exc:
        await provider.verify_purchase(purchase_token="tok", expected_user_id=TEST_RIDER)
    assert exc.value.code == "CROSS_USER_PURCHASE"


async def test_missing_rider_binding_rejected(configure, signer, catalog):
    body = subscription_v2(obfuscated_rider="")
    provider, _ = build_provider(signer, ok_publisher(body))
    with pytest.raises(EntitlementError) as exc:
        await provider.verify_purchase(purchase_token="tok", expected_user_id=TEST_RIDER)
    assert exc.value.code == "CROSS_USER_PURCHASE"


async def test_expired_subscription(configure, signer, catalog):
    body = subscription_v2(
        state="EXPIRED", expiry=now() - timedelta(days=1), obfuscated_rider=str(TEST_RIDER)
    )
    provider, _ = build_provider(signer, ok_publisher(body))
    verified = await provider.verify_purchase(purchase_token="tok", expected_user_id=TEST_RIDER)
    assert verified.status is SubscriptionStatus.EXPIRED


async def test_cancelled_subscription_keeps_period_end(configure, signer, catalog):
    body = subscription_v2(
        state="CANCELED",
        expiry=now() + timedelta(days=5),
        auto_renew=False,
        cancel_context=user_cancel_context(),
        obfuscated_rider=str(TEST_RIDER),
    )
    provider, _ = build_provider(signer, ok_publisher(body))
    verified = await provider.verify_purchase(purchase_token="tok", expected_user_id=TEST_RIDER)
    assert verified.status is SubscriptionStatus.CANCELED
    assert verified.cancel_at_period_end is True


async def test_revoked_subscription(configure, signer, catalog):
    body = subscription_v2(
        state="CANCELED", cancel_context=revocation_context(), obfuscated_rider=str(TEST_RIDER)
    )
    provider, _ = build_provider(signer, ok_publisher(body))
    verified = await provider.verify_purchase(purchase_token="tok", expected_user_id=TEST_RIDER)
    assert verified.status is SubscriptionStatus.REVOKED


async def test_full_refund_revokes(configure, signer, catalog):
    body = subscription_v2(
        state="CANCELED", cancel_context=full_refund_context(), obfuscated_rider=str(TEST_RIDER)
    )
    provider, _ = build_provider(signer, ok_publisher(body))
    verified = await provider.verify_purchase(purchase_token="tok", expected_user_id=TEST_RIDER)
    assert verified.status is SubscriptionStatus.REVOKED


async def test_dunning_states_map_to_past_due(configure, signer, catalog):
    for state in ("IN_GRACE_PERIOD", "ON_HOLD", "IN_ACCOUNT_HOLD", "PAUSED"):
        body = subscription_v2(state=state, obfuscated_rider=str(TEST_RIDER))
        provider, _ = build_provider(signer, ok_publisher(body))
        verified = await provider.verify_purchase(purchase_token="tok", expected_user_id=TEST_RIDER)
        assert verified.status is SubscriptionStatus.PAST_DUE, state


async def test_unknown_state_fails_closed(configure, signer, catalog):
    # An unmapable provider state is a rejection, never a silent ACTIVE.
    body = subscription_v2(state="UNSPECIFIED", obfuscated_rider=str(TEST_RIDER))
    provider, _ = build_provider(signer, ok_publisher(body))
    with pytest.raises(EntitlementError):
        await provider.verify_purchase(purchase_token="tok", expected_user_id=TEST_RIDER)


async def test_missing_expiry_rejected(configure, signer, catalog):
    body = subscription_v2(expiry=None, obfuscated_rider=str(TEST_RIDER))
    del body["expiryTime"]
    del body["lineItems"][0]["expiryTime"]
    provider, _ = build_provider(signer, ok_publisher(body))
    with pytest.raises(EntitlementError) as exc:
        await provider.verify_purchase(purchase_token="tok", expected_user_id=TEST_RIDER)
    assert exc.value.code == "PURCHASE_INVALID"


async def test_malformed_expiry_rejected(configure, signer, catalog):
    body = subscription_v2(obfuscated_rider=str(TEST_RIDER))
    body["expiryTime"] = "not-a-time"
    provider, _ = build_provider(signer, ok_publisher(body))
    with pytest.raises(EntitlementError):
        await provider.verify_purchase(purchase_token="tok", expected_user_id=TEST_RIDER)


async def test_test_purchase_maps_to_sandbox_on_same_endpoint(configure, signer, catalog):
    body = subscription_v2(test_purchase=True, obfuscated_rider=str(TEST_RIDER))
    provider, seen = build_provider(signer, ok_publisher(body))
    verified = await provider.verify_purchase(purchase_token="tok", expected_user_id=TEST_RIDER)
    assert verified.environment is VerificationEnvironment.SANDBOX
    # No separate sandbox API exists: the same endpoint is the ONLY API, and
    # the environment comes from the authoritative testPurchase flag.
    assert "subscriptionsv2/tokens/tok" in seen[-1].url.path


# ---------------------------------------------------------------------------
# Provider HTTP classification
# ---------------------------------------------------------------------------


async def test_invalid_purchase_is_distinct_from_provider_outage(configure, signer, catalog):
    invalid, _ = build_provider(signer, lambda req: httpx.Response(404, json={}))
    with pytest.raises(EntitlementError) as exc:
        await invalid.verify_purchase(purchase_token="tok", expected_user_id=TEST_RIDER)
    assert exc.value.code == "PURCHASE_INVALID"

    outage, _ = build_provider(signer, lambda req: httpx.Response(503, json={}))
    with pytest.raises(EntitlementError) as exc:
        await outage.verify_purchase(purchase_token="tok", expected_user_id=TEST_RIDER)
    assert exc.value.code == "PROVIDER_ERROR"


async def test_auth_and_quota_errors_are_provider_errors(configure, signer, catalog):
    for status in (401, 403, 429):
        provider, _ = build_provider(
            signer, lambda req, status=status: httpx.Response(status, json={})
        )
        with pytest.raises(EntitlementError) as exc:
            await provider.verify_purchase(purchase_token="tok", expected_user_id=TEST_RIDER)
        assert exc.value.code == "PROVIDER_ERROR", status


async def test_5xx_is_provider_error(configure, signer, catalog):
    provider, _ = build_provider(signer, lambda req: httpx.Response(500, json={}))
    with pytest.raises(EntitlementError) as exc:
        await provider.verify_purchase(purchase_token="tok", expected_user_id=TEST_RIDER)
    assert exc.value.code == "PROVIDER_ERROR"


async def test_malformed_json_is_provider_error(configure, signer, catalog):
    provider, _ = build_provider(signer, lambda req: httpx.Response(200, text="<html>oops</html>"))
    with pytest.raises(EntitlementError) as exc:
        await provider.verify_purchase(purchase_token="tok", expected_user_id=TEST_RIDER)
    assert exc.value.code == "PROVIDER_ERROR"


async def test_timeout_is_provider_error(configure, signer, catalog):
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("slow google")

    provider, _ = build_provider(signer, boom)
    with pytest.raises(EntitlementError) as exc:
        await provider.verify_purchase(purchase_token="tok", expected_user_id=TEST_RIDER)
    assert exc.value.code == "PROVIDER_ERROR"


async def test_errors_never_echo_secrets(configure, signer, catalog):
    provider, _ = build_provider(signer, lambda req: httpx.Response(500, json={}))
    with pytest.raises(EntitlementError) as exc:
        await provider.verify_purchase(
            purchase_token="super-secret-token", expected_user_id=TEST_RIDER
        )
    message = exc.value.message
    assert "super-secret-token" not in message
    assert "at-test" not in message
    assert "PRIVATE KEY" not in message
    assert message.strip()  # bounded, generic, present


async def test_acknowledge_posts_the_action(configure, signer, catalog):
    provider, seen = build_provider(
        signer, ok_publisher(subscription_v2(obfuscated_rider=str(TEST_RIDER)))
    )
    await provider.acknowledge(purchase_token="tok-ack")
    ack = [r for r in seen if str(r.url).endswith(":acknowledge")]
    assert len(ack) == 1
    assert "tok-ack" in ack[0].url.path


# ---------------------------------------------------------------------------
# Idempotency (pipeline-level, real Google adapter + DB)
# ---------------------------------------------------------------------------


async def _account(client, tag: str) -> dict[str, object]:
    payload = {
        "email": f"gplay-{tag}-{uuid.uuid4().hex[:8]}@example.com",
        "password": "StrongPass123",
        "password_confirm": "StrongPass123",
        "display_name": f"Rider {tag}",
    }
    registered = await client.post("/api/v1/auth/register", json=payload)
    assert registered.status_code == 201, registered.text
    logged_in = await client.post(
        "/api/v1/auth/login", json={"email": payload["email"], "password": payload["password"]}
    )
    assert logged_in.status_code == 200, logged_in.text
    me = await client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {logged_in.json()['access_token']}"}
    )
    assert me.status_code == 200, me.text
    return {"user_id": uuid.UUID(me.json()["user"]["id"])}


async def test_repeat_verification_same_token_is_idempotent(
    configure, signer, catalog, client, db_session_factory
):
    account = await _account(client, "repeat")
    rider = account["user_id"]
    body = subscription_v2(obfuscated_rider=str(rider))
    provider, _ = build_provider(signer, ok_publisher(body))
    register_provider(provider)
    try:
        for _ in range(2):
            async with db_session_factory() as db:
                state = await process_purchase(
                    db,
                    kind=VerifyKind.PURCHASE,
                    provider_name="play_store",
                    product_id=CYCLECOACH_PRO_MONTHLY,
                    purchase_token="stable-tok",
                    user_id=rider,
                )
            assert state.plan is Plan.PRO
        async with db_session_factory() as db:
            count = await db.scalar(select(func.count()).select_from(Subscription))
        assert count == 1
    finally:
        reset_providers()


# ---------------------------------------------------------------------------
# RTDN event processing
# ---------------------------------------------------------------------------


async def test_process_event_refetches_authoritative_state(configure, signer, catalog):
    body = subscription_v2(obfuscated_rider=str(TEST_RIDER))
    provider, seen = build_provider(signer, ok_publisher(body))
    event = await provider.process_event(
        payload=rtdn_push(notification_type=2, purchase_token="tok"), signature="oidc-token"
    )
    assert event.provider is SubscriptionProvider.PLAY_STORE
    assert event.status is SubscriptionStatus.ACTIVE
    assert event.user_id == TEST_RIDER
    assert event.provider_subscription_id == "tok"
    # Notification fields are never trusted for state: the API is re-queried.
    assert "subscriptionsv2/tokens/tok" in seen[-1].url.path


async def test_process_event_cancelled(configure, signer, catalog):
    body = subscription_v2(
        state="CANCELED", expiry=now() + timedelta(days=3), obfuscated_rider=str(TEST_RIDER)
    )
    provider, _ = build_provider(signer, ok_publisher(body))
    event = await provider.process_event(
        payload=rtdn_push(notification_type=3, purchase_token="tok"), signature=None
    )
    assert event.status is SubscriptionStatus.CANCELED
    assert event.cancel_at_period_end is True


async def test_process_event_revoked(configure, signer, catalog):
    body = subscription_v2(
        state="CANCELED", cancel_context=revocation_context(), obfuscated_rider=str(TEST_RIDER)
    )
    provider, _ = build_provider(signer, ok_publisher(body))
    event = await provider.process_event(
        payload=rtdn_push(notification_type=12, purchase_token="tok"), signature=None
    )
    assert event.status is SubscriptionStatus.REVOKED


async def test_process_event_unknown_type_fails_closed(configure, signer, catalog):
    provider, _ = build_provider(
        signer, ok_publisher(subscription_v2(obfuscated_rider=str(TEST_RIDER)))
    )
    with pytest.raises(EntitlementError):
        await provider.process_event(
            payload=rtdn_push(notification_type=999, purchase_token="tok"), signature=None
        )


async def test_process_event_wrong_package_rejected(configure, signer, catalog):
    provider, _ = build_provider(
        signer, ok_publisher(subscription_v2(obfuscated_rider=str(TEST_RIDER)))
    )
    from tests.google_fixtures import rtdn_push as push

    with pytest.raises(EntitlementError):
        await provider.process_event(
            payload=push(notification_type=2, purchase_token="tok", package_name="com.evil.app"),
            signature=None,
        )


# ---------------------------------------------------------------------------
# Cross-provider confusion
# ---------------------------------------------------------------------------


async def test_apple_product_cannot_be_claimed_via_google(configure, signer, catalog):
    from tests.storekit_fixtures import PROVIDER_PRODUCT_ID as APPLE_PRODUCT_ID

    body = subscription_v2(product_id=APPLE_PRODUCT_ID, obfuscated_rider=str(TEST_RIDER))
    provider, _ = build_provider(signer, ok_publisher(body))
    with pytest.raises(EntitlementError) as exc:
        await provider.verify_purchase(purchase_token="tok", expected_user_id=TEST_RIDER)
    assert exc.value.code == "UNKNOWN_PRODUCT"


async def test_google_product_cannot_be_claimed_via_apple(configure, signer, catalog):
    apple = AppleSigner()
    tx = transaction_payload(product_id=PLAY_PRODUCT_ID, app_account_token=str(TEST_RIDER))
    body = status_response(apple, transaction=tx)
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda req: httpx.Response(200, json=body))
    )
    provider = AppleStoreProvider(
        http=client, root_certs=[apple.root_pem()], private_key_pem=apple.leaf_private_key_pem()
    )
    with pytest.raises(EntitlementError) as exc:
        await provider.verify_purchase(purchase_token="tx-cross", expected_user_id=TEST_RIDER)
    assert exc.value.code in ("UNKNOWN_PRODUCT", "PURCHASE_INVALID")


# ---------------------------------------------------------------------------
# Client-level OAuth flow
# ---------------------------------------------------------------------------


async def test_client_token_exchange_uses_service_account_assertion(signer):
    def handler(request: httpx.Request) -> httpx.Response:
        if "oauth2.googleapis.com/token" in str(request.url):
            body = urllib.parse.parse_qs(request.content.decode("latin-1"))
            assert body["grant_type"] == ["urn:ietf:params:oauth:grant-type:jwt-bearer"]
            assert body["assertion"][0].startswith("eyJ")  # JWT
            return httpx.Response(200, json={"access_token": "at-x", "expires_in": 3600})
        return httpx.Response(404, json={})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    play = GooglePlayClient(http=client, credentials=signer.credentials())
    token = await play._access_token()
    assert token == "at-x"
    # Cache hit: a second call performs no new exchange.
    await play._access_token()

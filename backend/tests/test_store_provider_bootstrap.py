"""WS-RP Phase 6A: production store-provider bootstrap tests.

The bootstrap is the only production path from validated settings to a live
provider registry. These tests prove that an enabled provider with valid
trust material registers exactly once, every invalid configuration fails
before registration, duplicate initialization is rejected deterministically,
and both webhook and purchase-verification flows resolve through the same
existing registry.

All trust material is generated in memory and written only to pytest's
temporary directory. No live Apple/Google credential is used, and no
external network call is made: the Google adapter's HTTP edge is replaced
with a mock transport for the integration tests.
"""

import json
import logging
import uuid

import httpx
import pytest
from sqlalchemy import select

from app.api.v1 import webhooks
from app.core.config import settings
from app.main import create_app
from app.models.subscription import (
    Entitlement,
    EntitlementStatus,
    Plan,
    Subscription,
    SubscriptionProvider,
    SubscriptionStatus,
)
from app.services.google_play_provider import GoogleStoreProvider
from app.services.pubsub_auth import GoogleOidcPushVerifier
from app.services.purchase_verification import (
    VerifyKind,
    process_purchase,
    provider_for,
    registered_providers,
    reset_providers,
)
from app.services.store_products import (
    CYCLECOACH_PRO_MONTHLY,
    CYCLECOACH_PRO_YEARLY,
    ProductPlatform,
    provider_product_for,
    reset_provider_products,
)
from app.services.store_provider_bootstrap import (
    StoreBootstrapError,
    bootstrap_store_providers,
    reset_store_bootstrap_state,
)
from tests.google_fixtures import (
    PACKAGE_NAME,
    GoogleOidcSigner,
    GoogleSigner,
    rtdn_push,
    subscription_v2,
)
from tests.storekit_fixtures import (
    BUNDLE_ID,
    AppleSigner,
    notification_payload,
    renewal_payload,
    transaction_payload,
)

APPLE_ISSUER = "69a6de83-0000-47e3-e053-5b8c7c11a4d1"
APPLE_KEY_ID = "TESTKEY"
APPLE_MONTHLY = "com.example.cyclecoach.pro.monthly"
APPLE_YEARLY = "com.example.cyclecoach.pro.yearly"
GOOGLE_MONTHLY = "cyclecoach.pro.monthly"
GOOGLE_YEARLY = "cyclecoach.pro.yearly"
RTDN_AUDIENCE = "projects/cyclecoach-test/subscriptions/cyclecoach"
AUTH = "/api/v1/auth"
WEBHOOKS = "/api/v1/webhooks"


@pytest.fixture(autouse=True)
def _clean_bootstrap_state():
    reset_store_bootstrap_state()
    reset_providers()
    reset_provider_products()
    webhooks._google_verifier_instance = None
    yield
    webhooks._google_verifier_instance = None
    reset_store_bootstrap_state()
    reset_providers()
    reset_provider_products()


def _write_apple_material(tmp_path, signer: AppleSigner) -> tuple[str, str]:
    root_path = tmp_path / "apple-root.pem"
    key_path = tmp_path / "apple-key.p8"
    root_path.write_bytes(signer.root_pem())
    key_path.write_bytes(signer.leaf_private_key_pem())
    return str(root_path), str(key_path)


def _configure_apple(monkeypatch, tmp_path, signer: AppleSigner) -> None:
    root_path, key_path = _write_apple_material(tmp_path, signer)
    monkeypatch.setattr(settings, "APPLE_STORE_ENABLED", True)
    monkeypatch.setattr(settings, "APPLE_API_ENVIRONMENT", "production")
    monkeypatch.setattr(settings, "APPLE_BUNDLE_ID", BUNDLE_ID)
    monkeypatch.setattr(settings, "APPLE_ISSUER_ID", APPLE_ISSUER)
    monkeypatch.setattr(settings, "APPLE_KEY_ID", APPLE_KEY_ID)
    monkeypatch.setattr(settings, "APPLE_PRIVATE_KEY_PATH", key_path)
    monkeypatch.setattr(settings, "APPLE_ROOT_CERTS_PATH", root_path)
    monkeypatch.setattr(settings, "APPLE_PRODUCT_PRO_MONTHLY", APPLE_MONTHLY)
    monkeypatch.setattr(settings, "APPLE_PRODUCT_PRO_YEARLY", APPLE_YEARLY)


def _write_google_credentials(tmp_path, signer: GoogleSigner) -> str:
    credentials_path = tmp_path / "google-service-account.json"
    credentials_path.write_text(json.dumps(signer.credentials()), encoding="utf-8")
    return str(credentials_path)


def _configure_google(monkeypatch, tmp_path, signer: GoogleSigner) -> None:
    monkeypatch.setattr(settings, "GOOGLE_PLAY_ENABLED", True)
    monkeypatch.setattr(settings, "GOOGLE_PACKAGE_NAME", PACKAGE_NAME)
    monkeypatch.setattr(
        settings, "GOOGLE_APPLICATION_CREDENTIALS_PATH", _write_google_credentials(tmp_path, signer)
    )
    monkeypatch.setattr(settings, "GOOGLE_PRODUCT_PRO_MONTHLY", GOOGLE_MONTHLY)
    monkeypatch.setattr(settings, "GOOGLE_PRODUCT_PRO_YEARLY", GOOGLE_YEARLY)


async def _account(client, tag: str) -> dict[str, object]:
    payload = {
        "email": f"bootstrap-{tag}-{uuid.uuid4().hex[:8]}@example.com",
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
    me = await client.get(f"{AUTH}/me", headers=headers)
    assert me.status_code == 200, me.text
    return {"user_id": uuid.UUID(me.json()["user"]["id"])}


def _google_mock(signer: GoogleSigner, publisher, seen: list[httpx.Request]) -> httpx.AsyncClient:
    def _routed(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if "oauth2.googleapis.com/token" in str(request.url):
            return httpx.Response(
                200,
                json={"access_token": "at-test", "token_type": "Bearer", "expires_in": 3600},
            )
        return publisher(request)

    return httpx.AsyncClient(transport=httpx.MockTransport(_routed))


def _ok_publisher(body: dict):
    def _publish(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=body)

    return _publish


def test_apple_disabled_registers_nothing(monkeypatch, tmp_path):
    signer = AppleSigner()
    root_path, key_path = _write_apple_material(tmp_path, signer)
    monkeypatch.setattr(settings, "APPLE_STORE_ENABLED", False)
    monkeypatch.setattr(settings, "APPLE_PRIVATE_KEY_PATH", key_path)
    monkeypatch.setattr(settings, "APPLE_ROOT_CERTS_PATH", root_path)
    monkeypatch.setattr(settings, "APPLE_PRODUCT_PRO_MONTHLY", APPLE_MONTHLY)
    monkeypatch.setattr(settings, "APPLE_PRODUCT_PRO_YEARLY", APPLE_YEARLY)

    result = bootstrap_store_providers()

    assert result.apple_provider_registered is False
    assert result.apple_products == ()
    assert provider_for(SubscriptionProvider.APP_STORE.value) is None
    assert provider_product_for(SubscriptionProvider.APP_STORE, APPLE_MONTHLY) is None


def test_apple_enabled_valid_registers_provider_and_products(monkeypatch, tmp_path):
    _configure_apple(monkeypatch, tmp_path, AppleSigner())

    result = bootstrap_store_providers()

    assert result.apple_provider_registered is True
    assert result.apple_products == (APPLE_MONTHLY, APPLE_YEARLY)
    adapter = provider_for(SubscriptionProvider.APP_STORE.value)
    assert adapter is not None
    assert adapter.configured() is True
    monthly = provider_product_for(SubscriptionProvider.APP_STORE, APPLE_MONTHLY)
    yearly = provider_product_for(SubscriptionProvider.APP_STORE, APPLE_YEARLY)
    assert monthly is not None and monthly.product_id == CYCLECOACH_PRO_MONTHLY
    assert monthly.plan is Plan.PRO
    assert monthly.platform is ProductPlatform.IOS
    assert yearly is not None and yearly.product_id == CYCLECOACH_PRO_YEARLY
    assert provider_product_for(SubscriptionProvider.APP_STORE, "client-invented") is None


def test_apple_missing_private_key_fails_closed(monkeypatch, tmp_path):
    signer = AppleSigner()
    _configure_apple(monkeypatch, tmp_path, signer)
    monkeypatch.setattr(settings, "APPLE_PRIVATE_KEY_PATH", "   ")

    with pytest.raises(StoreBootstrapError, match="APPLE_PRIVATE_KEY_PATH"):
        bootstrap_store_providers()

    assert provider_for(SubscriptionProvider.APP_STORE.value) is None
    assert provider_product_for(SubscriptionProvider.APP_STORE, APPLE_MONTHLY) is None


def test_apple_malformed_private_key_fails_closed(monkeypatch, tmp_path):
    signer = AppleSigner()
    _configure_apple(monkeypatch, tmp_path, signer)
    bad_key = tmp_path / "bad-key.p8"
    bad_key.write_text("not-a-private-key", encoding="utf-8")
    monkeypatch.setattr(settings, "APPLE_PRIVATE_KEY_PATH", str(bad_key))

    with pytest.raises(StoreBootstrapError, match="APPLE_PRIVATE_KEY_PATH"):
        bootstrap_store_providers()

    assert provider_for(SubscriptionProvider.APP_STORE.value) is None


def test_apple_missing_root_certificate_fails_closed(monkeypatch, tmp_path):
    signer = AppleSigner()
    _configure_apple(monkeypatch, tmp_path, signer)
    monkeypatch.setattr(settings, "APPLE_ROOT_CERTS_PATH", str(tmp_path / "missing.pem"))

    with pytest.raises(StoreBootstrapError, match="APPLE_ROOT_CERTS_PATH"):
        bootstrap_store_providers()

    assert provider_for(SubscriptionProvider.APP_STORE.value) is None


def test_apple_malformed_root_certificate_fails_closed(monkeypatch, tmp_path):
    signer = AppleSigner()
    _configure_apple(monkeypatch, tmp_path, signer)
    bad_root = tmp_path / "bad-root.pem"
    bad_root.write_text(
        "-----BEGIN CERTIFICATE-----\nCYCLECOACH-TEST-NOT-A-CERTIFICATE\n"
        "-----END CERTIFICATE-----\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(settings, "APPLE_ROOT_CERTS_PATH", str(bad_root))

    with pytest.raises(StoreBootstrapError, match="APPLE_ROOT_CERTS_PATH"):
        bootstrap_store_providers()

    assert provider_for(SubscriptionProvider.APP_STORE.value) is None


def test_apple_missing_product_fails_closed(monkeypatch, tmp_path):
    _configure_apple(monkeypatch, tmp_path, AppleSigner())
    monkeypatch.setattr(settings, "APPLE_PRODUCT_PRO_MONTHLY", "  ")

    with pytest.raises(StoreBootstrapError, match="APPLE_PRODUCT_PRO_MONTHLY"):
        bootstrap_store_providers()

    assert provider_for(SubscriptionProvider.APP_STORE.value) is None


def test_apple_invalid_environment_fails_closed(monkeypatch, tmp_path):
    _configure_apple(monkeypatch, tmp_path, AppleSigner())
    monkeypatch.setattr(settings, "APPLE_API_ENVIRONMENT", "staging")

    with pytest.raises(StoreBootstrapError, match="APPLE_API_ENVIRONMENT"):
        bootstrap_store_providers()

    assert provider_for(SubscriptionProvider.APP_STORE.value) is None


def test_apple_duplicate_bootstrap_is_rejected(monkeypatch, tmp_path):
    _configure_apple(monkeypatch, tmp_path, AppleSigner())
    first = bootstrap_store_providers()
    assert first.apple_provider_registered is True

    with pytest.raises(StoreBootstrapError, match="already run"):
        bootstrap_store_providers()

    assert provider_for(SubscriptionProvider.APP_STORE.value) is not None
    assert registered_providers() == (SubscriptionProvider.APP_STORE,)


def test_bootstrap_registers_only_active_products(monkeypatch, tmp_path):
    _configure_apple(monkeypatch, tmp_path, AppleSigner())
    _configure_google(monkeypatch, tmp_path, GoogleSigner())

    bootstrap_store_providers()

    for provider, product_id in (
        (SubscriptionProvider.APP_STORE, APPLE_MONTHLY),
        (SubscriptionProvider.PLAY_STORE, GOOGLE_MONTHLY),
    ):
        product = provider_product_for(provider, product_id)
        assert product is not None
        assert product.active is True


def test_google_disabled_registers_nothing(monkeypatch, tmp_path):
    signer = GoogleSigner()
    monkeypatch.setattr(settings, "GOOGLE_PLAY_ENABLED", False)
    monkeypatch.setattr(
        settings,
        "GOOGLE_APPLICATION_CREDENTIALS_PATH",
        _write_google_credentials(tmp_path, signer),
    )
    monkeypatch.setattr(settings, "GOOGLE_PACKAGE_NAME", PACKAGE_NAME)
    monkeypatch.setattr(settings, "GOOGLE_PRODUCT_PRO_MONTHLY", GOOGLE_MONTHLY)
    monkeypatch.setattr(settings, "GOOGLE_PRODUCT_PRO_YEARLY", GOOGLE_YEARLY)

    result = bootstrap_store_providers()

    assert result.google_provider_registered is False
    assert result.google_products == ()
    assert provider_for(SubscriptionProvider.PLAY_STORE.value) is None
    assert provider_product_for(SubscriptionProvider.PLAY_STORE, GOOGLE_MONTHLY) is None


def test_google_enabled_valid_registers_provider_and_products(monkeypatch, tmp_path):
    _configure_google(monkeypatch, tmp_path, GoogleSigner())

    result = bootstrap_store_providers()

    assert result.google_provider_registered is True
    assert result.google_products == (GOOGLE_MONTHLY, GOOGLE_YEARLY)
    adapter = provider_for(SubscriptionProvider.PLAY_STORE.value)
    assert isinstance(adapter, GoogleStoreProvider)
    assert adapter.configured() is True
    monthly = provider_product_for(SubscriptionProvider.PLAY_STORE, GOOGLE_MONTHLY)
    assert monthly is not None and monthly.product_id == CYCLECOACH_PRO_MONTHLY
    assert monthly.plan is Plan.PRO
    assert monthly.platform is ProductPlatform.ANDROID


def test_google_missing_credentials_path_fails_closed(monkeypatch, tmp_path):
    _configure_google(monkeypatch, tmp_path, GoogleSigner())
    monkeypatch.setattr(settings, "GOOGLE_APPLICATION_CREDENTIALS_PATH", "  ")

    with pytest.raises(StoreBootstrapError, match="GOOGLE_APPLICATION_CREDENTIALS_PATH"):
        bootstrap_store_providers()

    assert provider_for(SubscriptionProvider.PLAY_STORE.value) is None


def test_google_malformed_credentials_fail_closed(monkeypatch, tmp_path):
    signer = GoogleSigner()
    _configure_google(monkeypatch, tmp_path, signer)
    bad_credentials = tmp_path / "bad-service-account.json"
    bad_credentials.write_text(
        '{"type": "service_account", "marker": "CYCLECOACH-TEST"}', encoding="utf-8"
    )
    monkeypatch.setattr(settings, "GOOGLE_APPLICATION_CREDENTIALS_PATH", str(bad_credentials))

    with pytest.raises(StoreBootstrapError, match="GOOGLE_APPLICATION_CREDENTIALS_PATH"):
        bootstrap_store_providers()

    assert provider_for(SubscriptionProvider.PLAY_STORE.value) is None
    assert provider_product_for(SubscriptionProvider.PLAY_STORE, GOOGLE_MONTHLY) is None


def test_google_missing_package_fails_closed(monkeypatch, tmp_path):
    _configure_google(monkeypatch, tmp_path, GoogleSigner())
    monkeypatch.setattr(settings, "GOOGLE_PACKAGE_NAME", "")

    with pytest.raises(StoreBootstrapError, match="GOOGLE_PACKAGE_NAME"):
        bootstrap_store_providers()

    assert provider_for(SubscriptionProvider.PLAY_STORE.value) is None


def test_google_missing_product_fails_closed(monkeypatch, tmp_path):
    _configure_google(monkeypatch, tmp_path, GoogleSigner())
    monkeypatch.setattr(settings, "GOOGLE_PRODUCT_PRO_YEARLY", "")

    with pytest.raises(StoreBootstrapError, match="GOOGLE_PRODUCT_PRO_YEARLY"):
        bootstrap_store_providers()

    assert provider_for(SubscriptionProvider.PLAY_STORE.value) is None


def test_google_duplicate_bootstrap_is_rejected(monkeypatch, tmp_path):
    _configure_google(monkeypatch, tmp_path, GoogleSigner())
    first = bootstrap_store_providers()
    assert first.google_provider_registered is True

    with pytest.raises(StoreBootstrapError, match="already run"):
        bootstrap_store_providers()

    assert registered_providers() == (SubscriptionProvider.PLAY_STORE,)


def test_cross_provider_product_confusion_is_rejected(monkeypatch, tmp_path):
    apple_signer = AppleSigner()
    google_signer = GoogleSigner()
    _configure_apple(monkeypatch, tmp_path, apple_signer)
    _configure_google(monkeypatch, tmp_path, google_signer)
    monkeypatch.setattr(settings, "GOOGLE_PRODUCT_PRO_MONTHLY", APPLE_MONTHLY)

    with pytest.raises(StoreBootstrapError, match="APPLE_PRODUCT_PRO_MONTHLY"):
        bootstrap_store_providers()

    assert provider_for(SubscriptionProvider.APP_STORE.value) is None
    assert provider_for(SubscriptionProvider.PLAY_STORE.value) is None
    assert provider_product_for(SubscriptionProvider.APP_STORE, APPLE_MONTHLY) is None
    assert provider_product_for(SubscriptionProvider.PLAY_STORE, APPLE_MONTHLY) is None


def test_successful_bootstrap_logs_identifiers_but_not_secrets(monkeypatch, tmp_path, caplog):
    apple_signer = AppleSigner()
    google_signer = GoogleSigner()
    _configure_apple(monkeypatch, tmp_path, apple_signer)
    _configure_google(monkeypatch, tmp_path, google_signer)

    with caplog.at_level(logging.INFO, logger="cyclecoach"):
        result = bootstrap_store_providers()

    assert result.apple_provider_registered and result.google_provider_registered
    providers = {getattr(record, "provider", None): record for record in caplog.records}
    apple_record = providers.get(SubscriptionProvider.APP_STORE.value)
    google_record = providers.get(SubscriptionProvider.PLAY_STORE.value)
    assert apple_record is not None and google_record is not None
    assert getattr(apple_record, "products", "") == f"{APPLE_MONTHLY},{APPLE_YEARLY}"
    assert getattr(google_record, "products", "") == f"{GOOGLE_MONTHLY},{GOOGLE_YEARLY}"
    logged_values = []
    for record in caplog.records:
        logged_values.append(record.getMessage())
        logged_values.extend(
            str(value) for value in record.__dict__.values() if isinstance(value, str)
        )
    logged_text = "\n".join(logged_values)
    assert apple_signer.leaf_private_key_pem().decode("ascii") not in logged_text
    assert apple_signer.root_pem().decode("ascii") not in logged_text
    assert google_signer.private_key_pem not in logged_text


def test_create_app_registers_an_enabled_apple_stack(monkeypatch, tmp_path):
    _configure_apple(monkeypatch, tmp_path, AppleSigner())

    create_app()

    assert provider_for(SubscriptionProvider.APP_STORE.value) is not None
    assert provider_product_for(SubscriptionProvider.APP_STORE, APPLE_YEARLY) is not None


def test_create_app_fails_closed_for_invalid_google_credentials(monkeypatch, tmp_path):
    _configure_google(monkeypatch, tmp_path, GoogleSigner())
    bad_credentials = tmp_path / "bad-service-account.json"
    bad_credentials.write_text("not-json", encoding="utf-8")
    monkeypatch.setattr(settings, "GOOGLE_APPLICATION_CREDENTIALS_PATH", str(bad_credentials))

    with pytest.raises(StoreBootstrapError, match="GOOGLE_APPLICATION_CREDENTIALS_PATH"):
        create_app()

    assert provider_for(SubscriptionProvider.PLAY_STORE.value) is None
    assert provider_product_for(SubscriptionProvider.PLAY_STORE, GOOGLE_MONTHLY) is None


async def test_apple_webhook_uses_a_bootstrapped_provider(
    client, monkeypatch, tmp_path, db_session_factory
):
    signer = AppleSigner()
    _configure_apple(monkeypatch, tmp_path, signer)
    monkeypatch.setattr(settings, "APPLE_WEBHOOKS_ENABLED", True)
    bootstrap_store_providers()

    account = await _account(client, "bootstrap-apple")
    transaction = transaction_payload(
        transaction_id="tx-bootstrap-apple",
        product_id=APPLE_MONTHLY,
        app_account_token=str(account["user_id"]),
    )
    body = notification_payload(
        signer,
        notification_type="DID_RENEW",
        transaction=transaction,
        renewal=renewal_payload(product_id=APPLE_MONTHLY),
    )
    response = await client.post(f"{WEBHOOKS}/apple", json=body)

    assert response.status_code == 200, response.text
    async with db_session_factory() as db:
        row = (
            (
                await db.execute(
                    select(Subscription).where(Subscription.user_id == account["user_id"])
                )
            )
            .scalars()
            .first()
        )
    assert row is not None
    assert row.provider is SubscriptionProvider.APP_STORE
    assert row.status is SubscriptionStatus.ACTIVE


async def test_google_webhook_reaches_a_bootstrapped_provider(
    client, monkeypatch, tmp_path, db_session_factory
):
    signer = GoogleSigner()
    oidc_signer = GoogleOidcSigner()
    _configure_google(monkeypatch, tmp_path, signer)
    monkeypatch.setattr(settings, "GOOGLE_WEBHOOKS_ENABLED", True)
    monkeypatch.setattr(settings, "GOOGLE_RTDN_AUTH_MODE", "oidc")
    monkeypatch.setattr(settings, "GOOGLE_RTDN_OIDC_AUDIENCE", RTDN_AUDIENCE)
    bootstrap_store_providers()
    adapter = provider_for(SubscriptionProvider.PLAY_STORE.value)
    assert isinstance(adapter, GoogleStoreProvider)

    account = await _account(client, "bootstrap-google")
    purchase_token = f"bootstrap-google-{uuid.uuid4().hex}"
    body = subscription_v2(
        product_id=GOOGLE_MONTHLY,
        purchase_token=purchase_token,
        obfuscated_rider=str(account["user_id"]),
    )
    seen: list[httpx.Request] = []
    adapter._client._http = _google_mock(signer, _ok_publisher(body), seen)
    monkeypatch.setattr(
        webhooks,
        "_google_verifier_instance",
        GoogleOidcPushVerifier(audience=RTDN_AUDIENCE, jwks=oidc_signer.jwks()),
    )
    push = rtdn_push(
        purchase_token=purchase_token,
        subscription_id=GOOGLE_MONTHLY,
        package_name=PACKAGE_NAME,
    )
    response = await client.post(
        f"{WEBHOOKS}/google",
        headers={"Authorization": f"Bearer {oidc_signer.issue_token(audience=RTDN_AUDIENCE)}"},
        json=push,
    )

    assert response.status_code == 200, response.text
    assert any("oauth2.googleapis.com/token" in str(request.url) for request in seen)
    async with db_session_factory() as db:
        row = (
            (
                await db.execute(
                    select(Subscription).where(Subscription.user_id == account["user_id"])
                )
            )
            .scalars()
            .first()
        )
    assert row is not None
    assert row.provider is SubscriptionProvider.PLAY_STORE
    assert row.provider_subscription_id == purchase_token


async def test_purchase_verification_uses_the_bootstrapped_google_provider(
    monkeypatch, tmp_path, db_session_factory, client
):
    signer = GoogleSigner()
    _configure_google(monkeypatch, tmp_path, signer)
    bootstrap_store_providers()
    adapter = provider_for(SubscriptionProvider.PLAY_STORE.value)
    assert isinstance(adapter, GoogleStoreProvider)

    account = await _account(client, "bootstrap-verify")
    purchase_token = f"bootstrap-verify-{uuid.uuid4().hex}"
    body = subscription_v2(
        product_id=GOOGLE_MONTHLY,
        purchase_token=purchase_token,
        obfuscated_rider=str(account["user_id"]),
    )
    seen: list[httpx.Request] = []
    adapter._client._http = _google_mock(signer, _ok_publisher(body), seen)

    async with db_session_factory() as db:
        state = await process_purchase(
            db,
            kind=VerifyKind.PURCHASE,
            provider_name=SubscriptionProvider.PLAY_STORE.value,
            product_id=CYCLECOACH_PRO_MONTHLY,
            purchase_token=purchase_token,
            user_id=account["user_id"],
        )

    assert state.plan is Plan.PRO
    assert registered_providers() == (SubscriptionProvider.PLAY_STORE,)
    async with db_session_factory() as db:
        rows = (
            (
                await db.execute(
                    select(Subscription).where(Subscription.user_id == account["user_id"])
                )
            )
            .scalars()
            .all()
        )
    assert len(rows) == 1
    assert rows[0].status is SubscriptionStatus.ACTIVE
    assert await _entitlement_active(db_session_factory, account["user_id"]) is True


async def _entitlement_active(db_session_factory, user_id: uuid.UUID) -> bool:
    async with db_session_factory() as db:
        rows = (
            (await db.execute(select(Entitlement).where(Entitlement.user_id == user_id)))
            .scalars()
            .all()
        )
    return bool(rows) and all(row.status is EntitlementStatus.ACTIVE for row in rows)

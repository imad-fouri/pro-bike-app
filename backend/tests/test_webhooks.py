"""Phase 10: store server-notification (webhook) endpoints.

The two webhook routes are the HTTP face of the provider adapters' existing
``process_event`` -> ``apply_provider_event`` pipeline. These tests prove the
routes authenticate the callback (Apple JWS chain; Google Pub/Sub OIDC push
token), hand the body to the adapter untouched, and apply a normalized event
through the SAME idempotent / ordered pipeline as verification — with the layer's
own obligations: body caps, rate limiting, opaque 5xx on provider/storage
failure, and logs that never carry a payload, a token, or a signed payload.

No live store is contacted and no Google JWKS network is used: the Google
adapter gets a MockTransport publisher and the push-token verifier is injected
with a fixed JWKS minted by `GoogleOidcSigner`.
"""

import asyncio
import base64
import json
import logging
import uuid
from datetime import UTC, datetime, timedelta

import httpx
import jwt
import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError

from app.api import v1 as v1_module
from app.core.config import settings
from app.models.subscription import (
    Entitlement,
    EntitlementStatus,
    Plan,
    Subscription,
    SubscriptionProvider,
    SubscriptionStatus,
)
from app.services.apple_store_provider import AppleStoreProvider
from app.services.google_play_provider import GoogleStoreProvider
from app.services.pubsub_auth import GoogleOidcPushVerifier, PushTokenError
from app.services.purchase_verification import register_provider, reset_providers
from app.services.store_products import (
    CYCLECOACH_PRO_MONTHLY,
    ProductPlatform,
    ProductType,
    ProviderProduct,
    register_provider_product,
    reset_provider_products,
)
from tests.google_fixtures import (
    PACKAGE_NAME,
    PLAY_PRODUCT_ID,
    GoogleOidcSigner,
    GoogleSigner,
    rtdn_push,
    subscription_v2,
)
from tests.storekit_fixtures import (
    BUNDLE_ID,
    PROVIDER_PRODUCT_ID,
    AppleSigner,
    notification_payload,
    renewal_payload,
    transaction_payload,
)

WEBHOOKS = "/api/v1/webhooks"
AUTH = "/api/v1/auth"
# Google subscription URL used as the expected push-token audience.
RTDN_AUDIENCE = "projects/cyclecoach-test/subscriptions/cyclecoach"


async def _account(client, tag: str) -> dict[str, object]:
    payload = {
        "email": f"webhook-{tag}-{uuid.uuid4().hex[:8]}@example.com",
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


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean_state():
    reset_providers()
    reset_provider_products()
    from app.api.v1 import webhooks

    webhooks._google_verifier_instance = None
    yield
    webhooks._google_verifier_instance = None
    reset_providers()
    reset_provider_products()


@pytest.fixture()
def apple_setup(monkeypatch, signer):
    """Enable the Apple webhook end-to-end (config + product + adapter)."""
    monkeypatch.setattr(settings, "APPLE_STORE_ENABLED", True)
    monkeypatch.setattr(settings, "APPLE_WEBHOOKS_ENABLED", True)
    monkeypatch.setattr(settings, "APPLE_API_ENVIRONMENT", "production")
    monkeypatch.setattr(settings, "APPLE_BUNDLE_ID", BUNDLE_ID)
    monkeypatch.setattr(settings, "APPLE_ISSUER_ID", "69a6de83-0000-47e3-e053-5b8c7c11a4d1")
    monkeypatch.setattr(settings, "APPLE_KEY_ID", "TESTKEY")
    monkeypatch.setattr(settings, "APPLE_PRIVATE_KEY_PATH", "/tmp/cyclecoach.p8")
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
    register_provider(AppleStoreProvider(root_certs=[signer.root_pem()]))


@pytest.fixture()
def google_setup(monkeypatch, oidc_signer):
    """Enable the Google RTDN webhook end-to-end and register the adapter.

    The push-token verifier is injected with the fixture key set so no network
    is needed, matching the fixture-only CI discipline of the rest of WS-RP.
    """
    monkeypatch.setattr(settings, "GOOGLE_PLAY_ENABLED", True)
    monkeypatch.setattr(settings, "GOOGLE_PACKAGE_NAME", PACKAGE_NAME)
    monkeypatch.setattr(settings, "GOOGLE_APPLICATION_CREDENTIALS_PATH", "/tmp/gac.json")
    monkeypatch.setattr(settings, "GOOGLE_REQUEST_TIMEOUT_SECONDS", 5)
    monkeypatch.setattr(settings, "GOOGLE_WEBHOOKS_ENABLED", True)
    monkeypatch.setattr(settings, "GOOGLE_RTDN_AUTH_MODE", "oidc")
    monkeypatch.setattr(settings, "GOOGLE_RTDN_OIDC_AUDIENCE", RTDN_AUDIENCE)
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
    from app.api.v1 import webhooks

    monkeypatch.setattr(
        webhooks,
        "_google_verifier_instance",
        GoogleOidcPushVerifier(audience=RTDN_AUDIENCE, jwks=oidc_signer.jwks()),
    )


@pytest.fixture()
def signer() -> AppleSigner:
    return AppleSigner()


@pytest.fixture()
def oidc_signer() -> GoogleOidcSigner:
    return GoogleOidcSigner()


@pytest.fixture()
def gsigner() -> GoogleSigner:
    return GoogleSigner()


def build_google_provider(
    gsigner: GoogleSigner, publisher, *, seen: list[httpx.Request]
) -> GoogleStoreProvider:
    """Wire an adapter whose publisher + OAuth dance is a MockTransport."""

    def _routed(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        url = str(request.url)
        if "oauth2.googleapis.com/token" in url:
            return httpx.Response(
                200, json={"access_token": "at-test", "token_type": "Bearer", "expires_in": 3600}
            )
        return publisher(request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(_routed))
    return GoogleStoreProvider(http=client, credentials=gsigner.credentials())


def ok_publisher(body: dict):
    def _pub(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=body)

    return _pub


def status_publisher(status: int):
    def _pub(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json={"error": {"code": 4044, "message": "x"}})

    return _pub


# ---------------------------------------------------------------------------
# DB reads (same database the API under test writes to)
# ---------------------------------------------------------------------------


async def _subscription(db_session_factory, user_id: uuid.UUID) -> Subscription | None:
    async with db_session_factory() as db:
        return (
            (await db.execute(select(Subscription).where(Subscription.user_id == user_id)))
            .scalars()
            .first()
        )


async def _subscription_count(db_session_factory) -> int:
    async with db_session_factory() as db:
        return await db.scalar(select(func.count()).select_from(Subscription))


async def _entitlement_statuses(db_session_factory, user_id: uuid.UUID) -> set[EntitlementStatus]:
    async with db_session_factory() as db:
        rows = (
            (await db.execute(select(Entitlement).where(Entitlement.user_id == user_id)))
            .scalars()
            .all()
        )
        return {row.status for row in rows}


async def _fresh_account(client, tag: str) -> dict[str, object]:
    account = await _account(client, f"{tag}-{uuid.uuid4().hex[:6]}")
    return account


def _forge_rs256(claims: dict, *, kid: str) -> str:
    """A hand-built JWS whose header claims RS256 and ``kid``.

    PyJWT's ``encode`` selects its signing algorithm from the header's ``alg``
    and refuses a mismatched one, so a token with a lying header — needed to
    reach a key-parse guard before signature verification — must be assembled
    by hand. The signature segment is junk on purpose.
    """

    def _part(value: dict) -> str:
        raw = json.dumps(value, separators=(",", ":")).encode("utf-8")
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")

    header = _part({"alg": "RS256", "typ": "JWT", "kid": kid})
    body = _part(claims)
    junk = base64.urlsafe_b64encode(b"\x00" * 64).rstrip(b"=").decode("ascii")
    return f"{header}.{body}.{junk}"


# ---------------------------------------------------------------------------
# Route presence and config gates
# ---------------------------------------------------------------------------


async def test_webhook_routes_are_listed_in_the_openapi_schema(client):
    res = await client.get("/openapi.json")
    assert res.status_code == 200, res.text
    paths = set(res.json()["paths"])
    assert f"{WEBHOOKS}/apple" in paths
    assert f"{WEBHOOKS}/google" in paths


async def test_apple_webhook_rejects_when_disabled(client, signer):
    account = await _fresh_account(client, "apple-disabled")
    tx = transaction_payload(transaction_id="tx", app_account_token=str(account["user_id"]))
    body = notification_payload(signer, notification_type="DID_RENEW", transaction=tx)
    res = await client.post(f"{WEBHOOKS}/apple", json=body)
    assert res.status_code == 503, res.text


async def test_apple_webhook_rejects_when_provider_unregistered(client, monkeypatch):
    # Enabled config without a registered adapter must not silently no-op:
    # an operator who believes webhooks work would be wrong.
    monkeypatch.setattr(settings, "APPLE_STORE_ENABLED", True)
    monkeypatch.setattr(settings, "APPLE_WEBHOOKS_ENABLED", True)
    res = await client.post(f"{WEBHOOKS}/apple", json={})
    assert res.status_code == 503, res.text


async def test_google_webhook_rejects_when_disabled(client, oidc_signer):
    res = await client.post(
        f"{WEBHOOKS}/google",
        headers={"Authorization": f"Bearer {oidc_signer.issue_token(audience=RTDN_AUDIENCE)}"},
        json={},
    )
    assert res.status_code == 503, res.text


# ---------------------------------------------------------------------------
# Apple — happy path and rejection matrix
# ---------------------------------------------------------------------------


async def test_apple_webhook_applies_a_signed_notification(
    client, apple_setup, signer, db_session_factory
):
    account = await _fresh_account(client, "apple-ok")
    tx = transaction_payload(transaction_id="tx-ok", app_account_token=str(account["user_id"]))
    body = notification_payload(
        signer,
        notification_type="DID_RENEW",
        transaction=tx,
        renewal=renewal_payload(),
    )
    res = await client.post(f"{WEBHOOKS}/apple", json=body)
    assert res.status_code == 200, res.text
    assert res.json() == {"status": "processed"}

    row = await _subscription(db_session_factory, account["user_id"])
    assert row is not None
    assert row.provider is SubscriptionProvider.APP_STORE
    assert row.provider_subscription_id == "orig-tx-0001"
    assert row.status is SubscriptionStatus.ACTIVE
    assert row.cancel_at_period_end is False
    assert await _entitlement_statuses(db_session_factory, account["user_id"]) == {
        EntitlementStatus.ACTIVE
    }


async def test_apple_webhook_rejects_a_missing_payload(
    client, apple_setup, signer, db_session_factory
):
    await _fresh_account(client, "apple-missing")
    res = await client.post(f"{WEBHOOKS}/apple", json={})
    assert res.status_code == 422, res.text
    assert await _subscription_count(db_session_factory) == 0


async def test_apple_webhook_rejects_a_tampered_signature(
    client, apple_setup, signer, db_session_factory
):
    account = await _fresh_account(client, "apple-tamper")
    tx = transaction_payload(transaction_id="tx-tamper", app_account_token=str(account["user_id"]))
    body = notification_payload(signer, notification_type="DID_RENEW", transaction=tx)
    signed = body["signedPayload"]
    forged = signed.rsplit(".", 1)[0] + ".AAAA"
    res = await client.post(f"{WEBHOOKS}/apple", json={"signedPayload": forged})
    assert res.status_code == 422, res.text
    assert await _subscription_count(db_session_factory) == 0


async def test_apple_webhook_rejects_an_untrusted_root(client, apple_setup, db_session_factory):
    attacker = AppleSigner()  # a DIFFERENT root the server does not trust
    account = await _fresh_account(client, "apple-root")
    tx = transaction_payload(transaction_id="tx-root", app_account_token=str(account["user_id"]))
    body = notification_payload(attacker, notification_type="DID_RENEW", transaction=tx)
    res = await client.post(f"{WEBHOOKS}/apple", json=body)
    assert res.status_code == 422, res.text
    assert await _subscription_count(db_session_factory) == 0


async def test_apple_webhook_rejects_a_foreign_bundle(
    client, apple_setup, signer, db_session_factory
):
    account = await _fresh_account(client, "apple-bundle")
    tx = transaction_payload(
        transaction_id="tx-bundle",
        bundle_id="com.evil.other.app",
        app_account_token=str(account["user_id"]),
    )
    body = notification_payload(signer, notification_type="DID_RENEW", transaction=tx)
    res = await client.post(f"{WEBHOOKS}/apple", json=body)
    assert res.status_code == 422, res.text
    assert await _subscription_count(db_session_factory) == 0


async def test_apple_webhook_rejects_a_sandbox_notification_on_a_production_server(
    client, apple_setup, signer, monkeypatch, db_session_factory
):
    monkeypatch.setattr(settings, "ENVIRONMENT", "production")
    account = await _fresh_account(client, "apple-sandbox")
    tx = transaction_payload(
        transaction_id="tx-sandbox",
        environment="Sandbox",
        app_account_token=str(account["user_id"]),
    )
    body = notification_payload(signer, notification_type="DID_RENEW", transaction=tx)
    res = await client.post(f"{WEBHOOKS}/apple", json=body)
    assert res.status_code == 422, res.text
    assert "ENVIRONMENT_MISMATCH" in res.text
    assert await _subscription_count(db_session_factory) == 0


async def test_apple_webhook_rejects_an_unknown_notification_type(
    client, apple_setup, signer, db_session_factory
):
    account = await _fresh_account(client, "apple-unknown")
    tx = transaction_payload(transaction_id="tx-unknown", app_account_token=str(account["user_id"]))
    body = notification_payload(signer, notification_type="SOMETHING_ELSE", transaction=tx)
    res = await client.post(f"{WEBHOOKS}/apple", json=body)
    assert res.status_code == 422, res.text
    assert await _subscription_count(db_session_factory) == 0


@pytest.mark.parametrize(
    ("notification_type", "expected", "subtype"),
    [
        ("SUBSCRIBED", SubscriptionStatus.ACTIVE, None),
        ("DID_RENEW", SubscriptionStatus.ACTIVE, None),
        ("OFFER_REDEEMED", SubscriptionStatus.ACTIVE, None),
        ("RENEWAL_EXTENDED", SubscriptionStatus.ACTIVE, None),
        ("PRICE_INCREASE", SubscriptionStatus.ACTIVE, None),
        ("DID_CHANGE_RENEWAL_PREF", SubscriptionStatus.ACTIVE, None),
        ("DID_FAIL_TO_RENEW", SubscriptionStatus.PAST_DUE, None),
        ("GRACE_PERIOD_STARTED", SubscriptionStatus.PAST_DUE, None),
        ("GRACE_PERIOD_EXPIRED", SubscriptionStatus.PAST_DUE, None),
        ("EXPIRED", SubscriptionStatus.EXPIRED, None),
        ("REFUND", SubscriptionStatus.REVOKED, None),
        ("REVOKE", SubscriptionStatus.REVOKED, None),
        ("DID_CHANGE_RENEWAL_STATUS", SubscriptionStatus.CANCELED, "AUTO_RENEW_DISABLED"),
        ("DID_CHANGE_RENEWAL_STATUS", SubscriptionStatus.ACTIVE, "AUTO_RENEW_ENABLED"),
    ],
)
async def test_apple_webhook_supported_notification_types_map_to_the_expected_state(
    client, apple_setup, signer, db_session_factory, notification_type, expected, subtype
):
    account = await _fresh_account(client, f"apple-{notification_type}-{subtype or 'none'}")
    tx = transaction_payload(
        transaction_id=f"tx-{notification_type}", app_account_token=str(account["user_id"])
    )
    body = notification_payload(
        signer, notification_type=notification_type, transaction=tx, subtype=subtype
    )
    res = await client.post(f"{WEBHOOKS}/apple", json=body)
    assert res.status_code == 200, res.text
    row = await _subscription(db_session_factory, account["user_id"])
    assert row is not None
    assert row.status is expected
    assert row.cancel_at_period_end is (expected is SubscriptionStatus.CANCELED)


# ---------------------------------------------------------------------------
# Apple — idempotency, ordering, equal timestamps
# ---------------------------------------------------------------------------


async def test_apple_webhook_duplicate_delivery_is_idempotent(
    client, apple_setup, signer, db_session_factory
):
    account = await _fresh_account(client, "apple-dup")
    tx = transaction_payload(transaction_id="tx-dup", app_account_token=str(account["user_id"]))
    body = notification_payload(signer, notification_type="DID_RENEW", transaction=tx)

    first = await client.post(f"{WEBHOOKS}/apple", json=body)
    second = await client.post(f"{WEBHOOKS}/apple", json=body)
    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    assert await _subscription_count(db_session_factory) == 1


async def test_apple_webhook_concurrent_duplicates_converge_to_one_subscription(
    client, apple_setup, signer, db_session_factory
):
    account = await _fresh_account(client, "apple-cc")
    tx = transaction_payload(transaction_id="tx-cc", app_account_token=str(account["user_id"]))
    body = notification_payload(signer, notification_type="DID_RENEW", transaction=tx)

    first, second = await asyncio.gather(
        client.post(f"{WEBHOOKS}/apple", json=body),
        client.post(f"{WEBHOOKS}/apple", json=body),
    )
    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    assert await _subscription_count(db_session_factory) == 1


async def test_apple_webhook_out_of_order_event_does_not_undo_newer_state(
    client, apple_setup, signer, db_session_factory
):
    account = await _fresh_account(client, "apple-ooo")
    tx = transaction_payload(transaction_id="tx-ooo", app_account_token=str(account["user_id"]))
    base = datetime.now(UTC)
    older_renew = notification_payload(
        signer,
        notification_type="DID_RENEW",
        transaction=tx,
        signed_date=base,
    )
    newer_cancel = notification_payload(
        signer,
        notification_type="DID_CHANGE_RENEWAL_STATUS",
        transaction=tx,
        subtype="AUTO_RENEW_DISABLED",
        signed_date=base + timedelta(hours=2),
    )

    assert (await client.post(f"{WEBHOOKS}/apple", json=older_renew)).status_code == 200
    assert (await client.post(f"{WEBHOOKS}/apple", json=newer_cancel)).status_code == 200
    # The older renewal delivered AFTER the newer cancellation must not resurrect it.
    assert (await client.post(f"{WEBHOOKS}/apple", json=older_renew)).status_code == 200

    row = await _subscription(db_session_factory, account["user_id"])
    assert row is not None
    assert row.status is SubscriptionStatus.CANCELED
    assert row.cancel_at_period_end is True


@pytest.mark.parametrize(
    ("first_type", "second_type", "final"),
    [
        # Same signedDate, two distinct events: neither is stale (equal
        # timestamps carry no ordering information) — the last delivered wins.
        ("DID_RENEW", "REFUND", SubscriptionStatus.REVOKED),
        ("REFUND", "DID_RENEW", SubscriptionStatus.ACTIVE),
    ],
)
async def test_apple_webhook_equal_timestamps_apply_both_events(
    client, apple_setup, signer, db_session_factory, first_type, second_type, final
):
    account = await _fresh_account(client, f"apple-eq-{first_type}-{second_type}")
    tx = transaction_payload(transaction_id="tx-eq", app_account_token=str(account["user_id"]))
    stamp = datetime.now(UTC)
    first_body = notification_payload(
        signer, notification_type=first_type, transaction=tx, signed_date=stamp
    )
    second_body = notification_payload(
        signer, notification_type=second_type, transaction=tx, signed_date=stamp
    )
    assert (await client.post(f"{WEBHOOKS}/apple", json=first_body)).status_code == 200
    assert (await client.post(f"{WEBHOOKS}/apple", json=second_body)).status_code == 200

    row = await _subscription(db_session_factory, account["user_id"])
    assert row is not None
    assert row.status is final
    assert row.last_provider_event_id.startswith(f"apple:{second_type}:tx-eq:")


# ---------------------------------------------------------------------------
# Apple — failure semantics, limits, logging
# ---------------------------------------------------------------------------


async def test_apple_webhook_database_failure_is_a_retryable_500(
    client, apple_setup, signer, monkeypatch, db_session_factory
):
    from app.api.v1 import webhooks

    account = await _fresh_account(client, "apple-dbfail")

    async def _boom(db, event):
        raise OperationalError("statement", {}, Exception("down"))

    monkeypatch.setattr(webhooks, "apply_provider_event", _boom)
    tx = transaction_payload(transaction_id="tx-dbfail", app_account_token=str(account["user_id"]))
    body = notification_payload(signer, notification_type="DID_RENEW", transaction=tx)
    res = await client.post(f"{WEBHOOKS}/apple", json=body)
    assert res.status_code == 500, res.text
    assert await _subscription_count(db_session_factory) == 0


async def test_apple_webhook_is_rate_limited(client, apple_setup, signer, monkeypatch):
    from app.api.v1 import webhooks

    monkeypatch.setattr(webhooks, "_APPLE_RATE_LIMIT", 2)
    account = await _fresh_account(client, "apple-rl")
    tx = transaction_payload(transaction_id="tx-rl", app_account_token=str(account["user_id"]))
    body = notification_payload(signer, notification_type="DID_RENEW", transaction=tx)
    assert (await client.post(f"{WEBHOOKS}/apple", json=body)).status_code == 200
    assert (await client.post(f"{WEBHOOKS}/apple", json=body)).status_code == 200
    assert (await client.post(f"{WEBHOOKS}/apple", json=body)).status_code == 429


async def test_apple_webhook_rejects_an_oversized_body(
    client, apple_setup, signer, db_session_factory
):
    register_provider(AppleStoreProvider())
    res = await client.post(
        f"{WEBHOOKS}/apple",
        content=b"x" * (1024 * 1024 + 2),
        headers={"Content-Type": "application/json"},
    )
    assert res.status_code == 413, res.text
    assert await _subscription_count(db_session_factory) == 0


async def test_apple_webhook_logs_never_contain_the_payload(client, apple_setup, signer, caplog):
    caplog.set_level(logging.INFO, logger="cyclecoach")
    canary = "tx-canary-7f2b1d99"
    account = await _fresh_account(client, "apple-log")
    tx = transaction_payload(transaction_id=canary, app_account_token=str(account["user_id"]))
    body = notification_payload(signer, notification_type="DID_RENEW", transaction=tx)
    res = await client.post(f"{WEBHOOKS}/apple", json=body)
    assert res.status_code == 200, res.text
    assert canary not in caplog.text
    assert "signedPayload" not in caplog.text
    assert "signedRenewalInfo" not in caplog.text


# ---------------------------------------------------------------------------
# Pub/Sub OIDC push-token verifier (no network: injected JWKS)
# ---------------------------------------------------------------------------


async def test_push_verifier_accepts_a_valid_token(oidc_signer):
    verifier = GoogleOidcPushVerifier(audience=RTDN_AUDIENCE, jwks=oidc_signer.jwks())
    await verifier.verify(oidc_signer.issue_token(audience=RTDN_AUDIENCE))


@pytest.mark.parametrize(
    "mangle",
    [
        lambda t: t.rsplit(".", 1)[0] + ".AAAA",  # bad signature
        lambda t: t[:-4] + "zzzz",  # truncated/flipped signature
    ],
)
async def test_push_verifier_rejects_a_tampered_token(oidc_signer, mangle):
    verifier = GoogleOidcPushVerifier(audience=RTDN_AUDIENCE, jwks=oidc_signer.jwks())
    token = mangle(oidc_signer.issue_token(audience=RTDN_AUDIENCE))
    with pytest.raises(PushTokenError):
        await verifier.verify(token)


async def test_push_verifier_rejects_a_token_with_an_unknown_kid(oidc_signer):
    verifier = GoogleOidcPushVerifier(audience=RTDN_AUDIENCE, jwks=oidc_signer.jwks())
    # A genuinely RS256-signed token whose key id is absent from our key set —
    # the cheapest probe an attacker has — must be refused before any claim is
    # read. PyJWT picks its signing algorithm from the header's own ``alg``,
    # so the token has to be signed for real under a kid we do not publish.
    token = oidc_signer.issue_token(audience=RTDN_AUDIENCE, kid="missing-kid")
    with pytest.raises(PushTokenError):
        await verifier.verify(token)


async def test_push_verifier_rejects_the_wrong_audience(oidc_signer):
    verifier = GoogleOidcPushVerifier(audience=RTDN_AUDIENCE, jwks=oidc_signer.jwks())
    token = oidc_signer.issue_token(audience="projects/other-subscription/delivery")
    with pytest.raises(PushTokenError):
        await verifier.verify(token)


async def test_push_verifier_rejects_the_wrong_issuer(oidc_signer):
    verifier = GoogleOidcPushVerifier(audience=RTDN_AUDIENCE, jwks=oidc_signer.jwks())
    token = oidc_signer.issue_token(audience=RTDN_AUDIENCE, issuer="https://evil.example")
    with pytest.raises(PushTokenError):
        await verifier.verify(token)


async def test_push_verifier_rejects_an_expired_token(oidc_signer):
    verifier = GoogleOidcPushVerifier(audience=RTDN_AUDIENCE, jwks=oidc_signer.jwks())
    token = oidc_signer.issue_token(
        audience=RTDN_AUDIENCE,
        iat=int(datetime.now(UTC).timestamp()) - 7200,
        exp=int(datetime.now(UTC).timestamp()) - 3600,
    )
    with pytest.raises(PushTokenError):
        await verifier.verify(token)


async def test_push_verifier_rejects_an_insecure_algorithm(oidc_signer):
    verifier = GoogleOidcPushVerifier(audience=RTDN_AUDIENCE, jwks=oidc_signer.jwks())
    token = jwt.encode(
        {"some": "claim"},
        "shared-secret-not-a-google-key",
        algorithm="HS256",
    )
    with pytest.raises(PushTokenError):
        await verifier.verify(token)


async def test_push_verifier_rejects_garbage(oidc_signer):
    verifier = GoogleOidcPushVerifier(audience=RTDN_AUDIENCE, jwks=oidc_signer.jwks())
    with pytest.raises(PushTokenError):
        await verifier.verify("not.a.jwt")


async def test_push_verifier_rejects_a_malformed_jwk():
    # A JWKS entry that is RSA-shaped but garbage must fail closed rather than
    # let its token through or blow up the request. The token's header claims
    # RS256 and the broken kid so the verifier reaches the key itself; its
    # signature is never consulted, because the modulus is refused outright.
    verifier = GoogleOidcPushVerifier(
        audience=RTDN_AUDIENCE,
        jwks=[{"kty": "RSA", "kid": "broken", "n": "!!not-base64!!", "e": "AQAB"}],
    )
    now = int(datetime.now(UTC).timestamp())
    forged = _forge_rs256(
        {
            "iss": "https://accounts.google.com",
            "aud": RTDN_AUDIENCE,
            "iat": now,
            "exp": now + 3600,
        },
        kid="broken",
    )
    with pytest.raises(PushTokenError):
        await verifier.verify(forged)


# ---------------------------------------------------------------------------
# Google — happy path and rejection matrix
# ---------------------------------------------------------------------------


async def test_google_webhook_applies_a_verified_push(
    client, google_setup, gsigner, oidc_signer, db_session_factory
):
    account = await _fresh_account(client, "google-ok")
    token = oidc_signer.issue_token(audience=RTDN_AUDIENCE)
    seen: list[httpx.Request] = []
    register_provider(
        build_google_provider(
            gsigner,
            ok_publisher(
                subscription_v2(
                    purchase_token="tok-ok",
                    obfuscated_rider=str(account["user_id"]),
                )
            ),
            seen=seen,
        )
    )
    envelope = rtdn_push(notification_type=2, purchase_token="tok-ok")
    res = await client.post(
        f"{WEBHOOKS}/google",
        headers={"Authorization": f"Bearer {token}"},
        json=envelope,
    )
    assert res.status_code == 200, res.text
    assert res.json() == {"status": "processed"}

    row = await _subscription(db_session_factory, account["user_id"])
    assert row is not None
    assert row.provider is SubscriptionProvider.PLAY_STORE
    assert row.provider_subscription_id == "tok-ok"
    assert row.status is SubscriptionStatus.ACTIVE
    # The adapter MUST have re-fetched authoritative state (G13), never trusted
    # the envelope: assert the publisher request actually happened.
    assert any("subscriptionsv2/tokens/tok-ok" in str(r.url.path) for r in seen)


async def test_google_webhook_rejects_an_unauthenticated_push(
    client, google_setup, db_session_factory
):
    res = await client.post(f"{WEBHOOKS}/google", json={})
    assert res.status_code == 403, res.text
    assert await _subscription_count(db_session_factory) == 0


async def test_google_webhook_rejects_a_garbage_token(client, google_setup, db_session_factory):
    res = await client.post(
        f"{WEBHOOKS}/google",
        headers={"Authorization": "Bearer garbage.token.value"},
        json={},
    )
    assert res.status_code == 403, res.text
    assert await _subscription_count(db_session_factory) == 0


@pytest.mark.parametrize(
    "mangle",
    [
        lambda t: t.rsplit(".", 1)[0] + ".AAAA",
        lambda t: t[:-4] + "zzzz",
    ],
)
async def test_google_webhook_rejects_a_tampered_push_token(
    client, google_setup, oidc_signer, db_session_factory, mangle
):
    token = mangle(oidc_signer.issue_token(audience=RTDN_AUDIENCE))
    res = await client.post(
        f"{WEBHOOKS}/google",
        headers={"Authorization": f"Bearer {token}"},
        json={"message": {}},
    )
    assert res.status_code == 403, res.text
    assert await _subscription_count(db_session_factory) == 0


async def test_google_webhook_rejects_a_token_for_the_wrong_audience(
    client, google_setup, oidc_signer, db_session_factory
):
    token = oidc_signer.issue_token(audience="projects/other-subscription/delivery")
    res = await client.post(
        f"{WEBHOOKS}/google",
        headers={"Authorization": f"Bearer {token}"},
        json={"message": {}},
    )
    assert res.status_code == 403, res.text
    assert await _subscription_count(db_session_factory) == 0


async def test_google_webhook_rejects_an_expired_push_token(
    client, google_setup, oidc_signer, db_session_factory
):
    token = oidc_signer.issue_token(
        audience=RTDN_AUDIENCE,
        iat=int(datetime.now(UTC).timestamp()) - 7200,
        exp=int(datetime.now(UTC).timestamp()) - 3600,
    )
    res = await client.post(
        f"{WEBHOOKS}/google",
        headers={"Authorization": f"Bearer {token}"},
        json={"message": {}},
    )
    assert res.status_code == 403, res.text
    assert await _subscription_count(db_session_factory) == 0


async def test_google_webhook_rejects_a_malformed_envelope(
    client, google_setup, gsigner, oidc_signer, db_session_factory
):
    account = await _fresh_account(client, "google-bad")
    register_provider(
        build_google_provider(
            gsigner,
            ok_publisher(
                subscription_v2(purchase_token="tok-bad", obfuscated_rider=str(account["user_id"]))
            ),
            seen=[],
        )
    )
    token = oidc_signer.issue_token(audience=RTDN_AUDIENCE)
    for envelope in [
        {},
        {"message": {}},
        {"message": {"data": base64.b64encode(b"not json").decode("ascii")}},
        {"message": {"data": "!!!not-base64!!!"}},
    ]:
        res = await client.post(
            f"{WEBHOOKS}/google",
            headers={"Authorization": f"Bearer {token}"},
            json=envelope,
        )
        assert res.status_code == 422, f"{envelope} -> {res.text}"
    assert await _subscription_count(db_session_factory) == 0


async def test_google_webhook_rejects_a_foreign_package(
    client, google_setup, gsigner, oidc_signer, db_session_factory
):
    account = await _fresh_account(client, "google-pkg")
    register_provider(
        build_google_provider(
            gsigner,
            ok_publisher(
                subscription_v2(purchase_token="tok-pkg", obfuscated_rider=str(account["user_id"]))
            ),
            seen=[],
        )
    )
    token = oidc_signer.issue_token(audience=RTDN_AUDIENCE)
    envelope = rtdn_push(
        notification_type=2,
        purchase_token="tok-pkg",
        package_name="com.evil.other.app",
    )
    res = await client.post(
        f"{WEBHOOKS}/google",
        headers={"Authorization": f"Bearer {token}"},
        json=envelope,
    )
    assert res.status_code == 422, res.text
    assert await _subscription_count(db_session_factory) == 0


async def test_google_webhook_unknown_type_fails_fast_without_refetching(
    client, google_setup, gsigner, oidc_signer, db_session_factory
):
    account = await _fresh_account(client, "google-unknown")
    seen: list[httpx.Request] = []
    register_provider(
        build_google_provider(
            gsigner,
            ok_publisher(
                subscription_v2(
                    purchase_token="tok-unknown", obfuscated_rider=str(account["user_id"])
                )
            ),
            seen=seen,
        )
    )
    token = oidc_signer.issue_token(audience=RTDN_AUDIENCE)
    envelope = rtdn_push(notification_type=999, purchase_token="tok-unknown")
    res = await client.post(
        f"{WEBHOOKS}/google",
        headers={"Authorization": f"Bearer {token}"},
        json=envelope,
    )
    assert res.status_code == 422, res.text
    # An unrecognized type must not consume an API call (fail fast, G13).
    assert seen == []
    assert await _subscription_count(db_session_factory) == 0


@pytest.mark.parametrize("status", [401, 403, 429, 500, 503])
async def test_google_webhook_provider_outage_is_a_503(
    client, google_setup, gsigner, oidc_signer, db_session_factory, status
):
    await _fresh_account(client, f"google-out-{status}")
    token = oidc_signer.issue_token(audience=RTDN_AUDIENCE)
    register_provider(
        build_google_provider(
            gsigner,
            status_publisher(status),
            seen=[],
        )
    )
    envelope = rtdn_push(
        notification_type=2,
        purchase_token=f"tok-out-{status}",
    )
    res = await client.post(
        f"{WEBHOOKS}/google",
        headers={"Authorization": f"Bearer {token}"},
        json=envelope,
    )
    # Fail closed: an outage never grants and never looks like a rejection of
    # the REQUEST — it is a retryable 5xx so delivery is retried later.
    assert res.status_code == 503, res.text
    assert await _subscription_count(db_session_factory) == 0


@pytest.mark.parametrize("status", [400, 404])
async def test_google_webhook_invalid_token_is_a_422(
    client, google_setup, gsigner, oidc_signer, db_session_factory, status
):
    await _fresh_account(client, f"google-invalid-{status}")
    token = oidc_signer.issue_token(audience=RTDN_AUDIENCE)
    register_provider(build_google_provider(gsigner, status_publisher(status), seen=[]))
    envelope = rtdn_push(
        notification_type=2,
        purchase_token=f"tok-invalid-{status}",
    )
    res = await client.post(
        f"{WEBHOOKS}/google",
        headers={"Authorization": f"Bearer {token}"},
        json=envelope,
    )
    assert res.status_code == 422, res.text
    assert await _subscription_count(db_session_factory) == 0


@pytest.mark.parametrize(
    ("notification_type", "expected"),
    [
        (1, SubscriptionStatus.ACTIVE),  # RECOVERED
        (2, SubscriptionStatus.ACTIVE),  # RENEWED
        (4, SubscriptionStatus.ACTIVE),  # PURCHASED
        (7, SubscriptionStatus.ACTIVE),  # RESTARTED
        (9, SubscriptionStatus.ACTIVE),  # DEFERRED
        (11, SubscriptionStatus.ACTIVE),  # PAUSE_SCHEDULED
        (3, SubscriptionStatus.CANCELED),
        (5, SubscriptionStatus.PAST_DUE),  # ON_HOLD
        (6, SubscriptionStatus.PAST_DUE),  # IN_GRACE_PERIOD
        (10, SubscriptionStatus.PAST_DUE),  # PAUSED
        (12, SubscriptionStatus.REVOKED),
        (13, SubscriptionStatus.EXPIRED),
        (8, SubscriptionStatus.ACTIVE),  # PRICE_CHANGE_CONFIRMED (deprecated)
    ],
)
async def test_google_webhook_supported_rtdn_types_map_to_expected_state(
    client, google_setup, gsigner, oidc_signer, db_session_factory, notification_type, expected
):
    account = await _fresh_account(client, f"google-ntype-{notification_type}")
    token = oidc_signer.issue_token(audience=RTDN_AUDIENCE)
    register_provider(
        build_google_provider(
            gsigner,
            ok_publisher(
                subscription_v2(
                    purchase_token=f"tok-{notification_type}",
                    obfuscated_rider=str(account["user_id"]),
                )
            ),
            seen=[],
        )
    )
    envelope = rtdn_push(
        notification_type=notification_type, purchase_token=f"tok-{notification_type}"
    )
    res = await client.post(
        f"{WEBHOOKS}/google",
        headers={"Authorization": f"Bearer {token}"},
        json=envelope,
    )
    assert res.status_code == 200, res.text
    row = await _subscription(db_session_factory, account["user_id"])
    assert row is not None
    assert row.status is expected
    assert row.cancel_at_period_end is (expected is SubscriptionStatus.CANCELED)


# ---------------------------------------------------------------------------
# Google — idempotency, ordering, equal timestamps
# ---------------------------------------------------------------------------


async def test_google_webhook_duplicate_delivery_is_idempotent(
    client, google_setup, gsigner, oidc_signer, db_session_factory
):
    account = await _fresh_account(client, "google-dup")
    token = oidc_signer.issue_token(audience=RTDN_AUDIENCE)
    register_provider(
        build_google_provider(
            gsigner,
            ok_publisher(
                subscription_v2(purchase_token="tok-dup", obfuscated_rider=str(account["user_id"]))
            ),
            seen=[],
        )
    )
    envelope = rtdn_push(notification_type=2, purchase_token="tok-dup")
    headers = {"Authorization": f"Bearer {token}"}
    first = await client.post(f"{WEBHOOKS}/google", headers=headers, json=envelope)
    second = await client.post(f"{WEBHOOKS}/google", headers=headers, json=envelope)
    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    assert await _subscription_count(db_session_factory) == 1


async def test_google_webhook_out_of_order_event_does_not_undo_newer_state(
    client, google_setup, gsigner, oidc_signer, db_session_factory
):
    account = await _fresh_account(client, "google-ooo")
    token = oidc_signer.issue_token(audience=RTDN_AUDIENCE)
    register_provider(
        build_google_provider(
            gsigner,
            ok_publisher(
                subscription_v2(purchase_token="tok-ooo", obfuscated_rider=str(account["user_id"]))
            ),
            seen=[],
        )
    )
    base = datetime.now(UTC)
    headers = {"Authorization": f"Bearer {token}"}
    newer_cancel = rtdn_push(
        notification_type=3, purchase_token="tok-ooo", event_time=base + timedelta(hours=2)
    )
    older_renew = rtdn_push(notification_type=2, purchase_token="tok-ooo", event_time=base)
    assert (
        await client.post(f"{WEBHOOKS}/google", headers=headers, json=newer_cancel)
    ).status_code == 200
    assert (
        await client.post(f"{WEBHOOKS}/google", headers=headers, json=older_renew)
    ).status_code == 200

    row = await _subscription(db_session_factory, account["user_id"])
    assert row is not None
    assert row.status is SubscriptionStatus.CANCELED
    assert row.cancel_at_period_end is True


@pytest.mark.parametrize(
    ("first_type", "second_type", "final"),
    [
        (2, 3, SubscriptionStatus.CANCELED),
        (3, 2, SubscriptionStatus.ACTIVE),
    ],
)
async def test_google_webhook_equal_timestamps_apply_both_events(
    client,
    google_setup,
    gsigner,
    oidc_signer,
    db_session_factory,
    first_type,
    second_type,
    final,
):
    account = await _fresh_account(client, f"google-eq-{first_type}-{second_type}")
    token = oidc_signer.issue_token(audience=RTDN_AUDIENCE)
    register_provider(
        build_google_provider(
            gsigner,
            ok_publisher(
                subscription_v2(purchase_token="tok-eq", obfuscated_rider=str(account["user_id"]))
            ),
            seen=[],
        )
    )
    stamp = datetime.now(UTC)
    headers = {"Authorization": f"Bearer {token}"}
    first_body = rtdn_push(notification_type=first_type, purchase_token="tok-eq", event_time=stamp)
    second_body = rtdn_push(
        notification_type=second_type, purchase_token="tok-eq", event_time=stamp
    )
    assert (
        await client.post(f"{WEBHOOKS}/google", headers=headers, json=first_body)
    ).status_code == 200
    assert (
        await client.post(f"{WEBHOOKS}/google", headers=headers, json=second_body)
    ).status_code == 200

    row = await _subscription(db_session_factory, account["user_id"])
    assert row is not None
    assert row.status is final


# ---------------------------------------------------------------------------
# Google — failure semantics, limits, logging
# ---------------------------------------------------------------------------


async def test_google_webhook_database_failure_is_a_retryable_500(
    client, google_setup, gsigner, oidc_signer, monkeypatch, db_session_factory
):
    from app.api.v1 import webhooks

    account = await _fresh_account(client, "google-dbfail")
    token = oidc_signer.issue_token(audience=RTDN_AUDIENCE)
    register_provider(
        build_google_provider(
            gsigner,
            ok_publisher(
                subscription_v2(
                    purchase_token="tok-dbfail", obfuscated_rider=str(account["user_id"])
                )
            ),
            seen=[],
        )
    )

    async def _boom(db, event):
        raise OperationalError("statement", {}, Exception("down"))

    monkeypatch.setattr(webhooks, "apply_provider_event", _boom)
    envelope = rtdn_push(notification_type=2, purchase_token="tok-dbfail")
    res = await client.post(
        f"{WEBHOOKS}/google",
        headers={"Authorization": f"Bearer {token}"},
        json=envelope,
    )
    assert res.status_code == 500, res.text
    assert await _subscription_count(db_session_factory) == 0


async def test_google_webhook_is_rate_limited(
    client, google_setup, gsigner, oidc_signer, monkeypatch
):
    from app.api.v1 import webhooks

    monkeypatch.setattr(webhooks, "_GOOGLE_RATE_LIMIT", 2)
    account = await _fresh_account(client, "google-rl")
    token = oidc_signer.issue_token(audience=RTDN_AUDIENCE)
    register_provider(
        build_google_provider(
            gsigner,
            ok_publisher(
                subscription_v2(purchase_token="tok-rl", obfuscated_rider=str(account["user_id"]))
            ),
            seen=[],
        )
    )
    envelope = rtdn_push(notification_type=2, purchase_token="tok-rl")
    headers = {"Authorization": f"Bearer {token}"}
    assert (
        await client.post(f"{WEBHOOKS}/google", headers=headers, json=envelope)
    ).status_code == 200
    assert (
        await client.post(f"{WEBHOOKS}/google", headers=headers, json=envelope)
    ).status_code == 200
    assert (
        await client.post(f"{WEBHOOKS}/google", headers=headers, json=envelope)
    ).status_code == 429


async def test_google_webhook_rejects_an_oversized_body(
    client, google_setup, gsigner, oidc_signer, db_session_factory
):
    register_provider(
        build_google_provider(
            gsigner,
            ok_publisher({"subscriptionState": "ACTIVE"}),
            seen=[],
        )
    )
    token = oidc_signer.issue_token(audience=RTDN_AUDIENCE)
    res = await client.post(
        f"{WEBHOOKS}/google",
        headers={"Authorization": f"Bearer {token}"},
        content=b"x" * (1024 * 1024 + 2),
    )
    assert res.status_code == 413, res.text
    assert await _subscription_count(db_session_factory) == 0


async def test_google_webhook_logs_never_contain_the_token_or_envelope(
    client, google_setup, gsigner, oidc_signer, caplog
):
    caplog.set_level(logging.INFO, logger="cyclecoach")
    account = await _fresh_account(client, "google-log")
    token_canary = f"oidc-canary-{uuid.uuid4().hex}"
    token = oidc_signer.issue_token(audience=RTDN_AUDIENCE, sub=token_canary)
    purchase_canary = f"tok-canary-{uuid.uuid4().hex}"
    register_provider(
        build_google_provider(
            gsigner,
            ok_publisher(
                subscription_v2(
                    purchase_token=purchase_canary,
                    obfuscated_rider=str(account["user_id"]),
                )
            ),
            seen=[],
        )
    )
    envelope = rtdn_push(notification_type=2, purchase_token=purchase_canary)
    res = await client.post(
        f"{WEBHOOKS}/google",
        headers={"Authorization": f"Bearer {token}"},
        json=envelope,
    )
    assert res.status_code == 200, res.text
    assert token_canary not in caplog.text
    assert purchase_canary not in caplog.text
    assert "at-test" not in caplog.text
    assert "PRIVATE KEY" not in caplog.text
    assert "messageId" not in caplog.text
    assert "signedPayload" not in caplog.text


def test_webhook_router_is_registered():
    # FastAPI >= 0.141 wraps included routers as `_IncludedRouter` objects
    # whose `.path` is an accessor-less wrapper, so resolve the endpoints by
    # route NAME instead: `url_path_for` raises RouteNotFound if not mounted.
    # The `/api/v1` prefix is applied when the app mounts the v1 router, so
    # paths here are router-relative.
    relative = WEBHOOKS.removeprefix(settings.API_PREFIX)
    assert str(v1_module.v1.url_path_for("apple_webhook")) == f"{relative}/apple"
    assert str(v1_module.v1.url_path_for("google_webhook")) == f"{relative}/google"

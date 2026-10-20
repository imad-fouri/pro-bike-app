"""Deterministic Google Play fixtures (WS-RP Phase 4/15).

Builds a throwaway RSA service-account credential and realistic
``SubscriptionPurchaseV2`` bodies for `purchases.subscriptionsv2.get`, plus
RTDN Pub/Sub push payloads. Everything is ephemeral and self-contained, so
provider contract tests exercise real RS256 assertion-signing and OAuth2
token exchange without any Google credential or live API.
"""

import base64
import json
import uuid
from datetime import UTC, datetime, timedelta

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

PACKAGE_NAME = "com.cyclecoach.android"
PLAY_PRODUCT_ID = "cyclecoach_pro_monthly"
TEST_OBFUSCATED_RIDER = "12345678-1234-5678-1234-567812345678"


def rfc3339(when: datetime) -> str:
    return when.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def ms(when: datetime) -> int:
    return int(when.timestamp() * 1000)


class GoogleSigner:
    """RSA service-account credential pair for fixture signing and OAuth."""

    def __init__(self) -> None:
        self._key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.private_key_pem = self._key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ).decode("ascii")
        self.client_email = "cyclecoach-test@cyclecoach-test.iam.gserviceaccount.com"

    def credentials(self) -> dict:
        return {
            "type": "service_account",
            "project_id": "cyclecoach-test",
            "private_key_id": "a1b2c3",
            "private_key": self.private_key_pem,
            "client_email": self.client_email,
            "client_id": "114400000000000000000",
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
            "client_x509_cert_url": "https://www.googleapis.com/robot/v1/metadata/x509/test",
        }


def subscription_v2(
    *,
    state: str = "ACTIVE",
    product_id: str = PLAY_PRODUCT_ID,
    purchase_token: str = "purchase-token-1",
    start: datetime | None = None,
    expiry: datetime | None = None,
    test_purchase: bool = False,
    obfuscated_rider: str = TEST_OBFUSCATED_RIDER,
    auto_renew: bool = True,
    acknowledgement_state: str = "ACKNOWLEDGED",
    cancel_context: dict | None = None,
    linked_purchase_token: str | None = None,
) -> dict:
    """A SubscriptionPurchaseV2-shaped resource."""
    now = datetime.now(UTC)
    start = start or (now - timedelta(days=10))
    expiry = expiry or (now + timedelta(days=20))
    item: dict = {
        "productId": product_id,
        "expiryTime": rfc3339(expiry),
        "autoRenewingPlan": {"autoRenewEnabled": auto_renew},
    }
    body: dict = {
        "subscriptionState": state,
        "startTime": rfc3339(start),
        "expiryTime": rfc3339(expiry),
        "lineItems": [item],
        "latestOrderId": "GPA.1234-5678-9101",
        "kind": "androidpublisher#subscriptionPurchaseV2",
        "testPurchase": test_purchase,
        "acknowledgementState": acknowledgement_state,
        "externalAccountIdentifiers": {
            "obfuscatedExternalAccountId": obfuscated_rider,
        },
    }
    if linked_purchase_token is not None:
        body["linkedPurchaseToken"] = linked_purchase_token
    if cancel_context is not None:
        body["canceledStateContext"] = cancel_context
    return body


def user_cancel_context(when: datetime | None = None) -> dict:
    return {"userInitiatedCancellation": {"cancelTime": rfc3339(when or datetime.now(UTC))}}


def revocation_context(when: datetime | None = None) -> dict:
    return {"revocation": {"revokeTime": rfc3339(when or datetime.now(UTC)), "revokeReason": 1}}


def full_refund_context(when: datetime | None = None) -> dict:
    return {"fullRefund": {"refundTime": rfc3339(when or datetime.now(UTC))}}


def rtdn_push(
    *,
    notification_type: int = 2,
    purchase_token: str = "purchase-token-1",
    subscription_id: str = PLAY_PRODUCT_ID,
    package_name: str = PACKAGE_NAME,
    event_time: datetime | None = None,
) -> dict:
    """A Pub/Sub push message whose data is an RTDN body."""
    event_time = event_time or datetime.now(UTC)
    data = {
        "version": "1.0",
        "packageName": package_name,
        "eventTimeMillis": ms(event_time),
        "subscriptionNotification": {
            "version": "1.0",
            "notificationType": notification_type,
            "purchaseToken": purchase_token,
            "subscriptionId": subscription_id,
        },
    }
    encoded = base64.b64encode(json.dumps(data).encode("utf-8")).decode("ascii")
    return {
        "message": {
            "data": encoded,
            "messageId": "raft-msg-0001",
            "publishTime": rfc3339(event_time),
            "attributes": {"event_type": "SUBSCRIPTION_NOTIFICATION"},
        },
        "subscription": "projects/cyclecoach-test/subscriptions/cyclecoach",
    }


def fresh_token(prefix: str = "t") -> str:
    return f"{prefix}-{uuid.uuid4().hex}"


def _b64u_int(value: int) -> str:
    length = max(1, (value.bit_length() + 7) // 8)
    return base64.urlsafe_b64encode(value.to_bytes(length, "big")).rstrip(b"=").decode("ascii")


class GoogleOidcSigner:
    """RSA key that ISSUES Pub/Sub OIDC push tokens for fixture verification.

    The webhook layer verifies the ``Authorization: Bearer`` token on a Google
    RTDN push against Google's published JWKS (app/services/pubsub_auth.py).
    This signer plays Google: it exposes a JWKS whose sole key it possesses and
    mints RS256 identity tokens under it, so the real PyJWT + cryptography
    verification path runs with no network and no Google credential.
    """

    _KID = "cyclecoach-test-oidc-key-1"

    def __init__(self) -> None:
        self._key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    def jwks(self) -> list[dict]:
        numbers = self._key.public_key().public_numbers()
        return [
            {
                "kty": "RSA",
                "alg": "RS256",
                "use": "sig",
                "kid": self._KID,
                "n": _b64u_int(numbers.n),
                "e": _b64u_int(numbers.e),
            }
        ]

    def issue_token(
        self,
        *,
        audience: str,
        issuer: str = "https://accounts.google.com",
        kid: str | None = None,
        **overrides: object,
    ) -> str:
        now = int(datetime.now(UTC).timestamp())
        claims: dict = {
            "iss": issuer,
            "aud": audience,
            "iat": now,
            "exp": now + 3600,
            "sub": "cyclecoach-test@cyclecoach-test.iam.gserviceaccount.com",
            "email": "cyclecoach-test@cyclecoach-test.iam.gserviceaccount.com",
            "email_verified": True,
        }
        claims.update(overrides)
        return jwt.encode(
            claims,
            self._key,
            algorithm="RS256",
            headers={"kid": kid or self._KID, "alg": "RS256"},
        )

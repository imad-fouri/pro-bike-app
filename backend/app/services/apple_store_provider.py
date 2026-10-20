"""App Store Server API verification adapter (WS-RP Phase 3).

Implements the provider-neutral contract defined in
:mod:`app.services.subscription_providers` against the App Store Server API:

* ``verify_purchase`` — fetches the subscription status for a purchase token
  (``GET /inApps/v1/subscriptions/{transactionId}``, Get All Subscription
  Statuses), verifies every signed transaction/renewal JWS it reads, enforces
  bundle id, product, rider binding (``appAccountToken``), type, environment,
  and expiry, then returns a :class:`VerifiedPurchase`.
* ``get_subscription`` — the provider's current view of one subscription,
  resolved from the same call.
* ``process_event`` — converts an authenticated App Store Server Notifications
  v2 payload (the ``signedPayload`` JWS is the signature) into a normalized
  :class:`ProviderSubscriptionEvent`. No HTTP endpoint calls this yet.

Trust boundaries honored here, per docs/store-provider-audit.md:

* The caller JWT that signs our requests never leaves the server.
* Environment comes from the *signed* transaction payload (``environment``:
  ``Production``/``Sandbox``), never from the client.
* Every ``signedTransactionInfo`` / ``signedRenewalInfo``/notification payload
  is verified as a JWS whose x5c chain terminates in a configured Apple root
  before a single field of it is trusted.
* Product ids are resolved exclusively through the server catalog registry
  (:mod:`app.services.store_products`); unknown or inactive products fail
  closed.

This module contains no endpoint, no raw purchase-token storage, and no code
path that would apply a provider result without the pipeline.
"""

import base64
import time
import uuid
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast
from urllib.parse import quote

import httpx
import jwt
from cryptography import x509
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509 import Certificate

from app.core.config import settings as default_settings
from app.models.subscription import SubscriptionProvider, SubscriptionStatus
from app.services.store_products import ProviderProduct, provider_product_for
from app.services.subscription_providers import (
    ProviderSubscriptionView,
    SubscriptionProviderProtocol,
    VerificationEnvironment,
    VerifiedPurchase,
)
from app.services.subscription_service import (
    EntitlementError,
    ProviderSubscriptionEvent,
)

#: Base URLs, chosen by SERVER config per environment. The transaction's own
#: signed `environment` field is what gates sandbox, not this choice.
_PRODUCTION_BASE = "https://api.storekit.apple.com"
_SANDBOX_BASE = "https://api.storekit-sandbox.apple.com"
#: Apple requires a caller token issued no more than 60 minutes before use.
_CALLER_TOKEN_TTL_S = 60 * 60
_SUBSCRIPTION_TYPE = "Auto-RenewableSubscription"


def _epoch_ms(value: Any) -> datetime:
    """App Store Server API timestamps are epoch milliseconds (often strings)."""
    return datetime.fromtimestamp(int(value) / 1000.0, tz=UTC)


def _now() -> datetime:
    return datetime.now(UTC)


class AppleStoreProvider(SubscriptionProviderProtocol):
    """App Store Server API adapter.

    Construction is fully testable: HTTP is injectable (httpx transport or
    client), trust roots are injectable bytes, and the API-signing private key
    is injectable in PEM form. In production all three come from settings.
    """

    name = SubscriptionProvider.APP_STORE

    def __init__(
        self,
        *,
        http: httpx.AsyncClient | None = None,
        root_certs: Sequence[bytes] = (),
        private_key_pem: bytes | None = None,
    ) -> None:
        self._http = http
        self._settings = default_settings
        self._private_key_pem = private_key_pem
        self._private_key: ec.EllipticCurvePrivateKey | None = None
        self._roots: tuple[Certificate, ...] = self._parse_roots(root_certs)

    # -- configuration -----------------------------------------------------

    def configured(self) -> bool:
        """True only when a deployment actually turned App Store on.

        Enabling without the key material is a config error, not a graceful
        grant: an unconfigured adapter must never be reachable, and the
        registry (Phase 5) honors exactly this predicate.
        """
        s = self._settings
        if not s.APPLE_STORE_ENABLED:
            return False
        if s.APPLE_API_ENVIRONMENT not in ("production", "sandbox"):
            return False
        return bool(
            s.APPLE_BUNDLE_ID.strip()
            and s.APPLE_ISSUER_ID.strip()
            and s.APPLE_KEY_ID.strip()
            and s.APPLE_PRIVATE_KEY_PATH.strip()
        )

    def _base_url(self) -> str:
        return (
            _PRODUCTION_BASE
            if self._settings.APPLE_API_ENVIRONMENT == "production"
            else _SANDBOX_BASE
        )

    # -- private key / caller token ----------------------------------------

    def _load_key(self) -> ec.EllipticCurvePrivateKey:
        if self._private_key is not None:
            return self._private_key
        if self._private_key_pem is not None:
            key = serialization.load_pem_private_key(self._private_key_pem, password=None)
        else:
            try:
                path = Path(self._settings.APPLE_PRIVATE_KEY_PATH)
                key = serialization.load_pem_private_key(path.read_bytes(), password=None)
            except Exception as exc:
                # The server cannot authenticate its own store call. Fail
                # closed with an opaque 503; never echo key-material details.
                raise EntitlementError(
                    "PROVIDER_ERROR", "The purchase provider failed.", 503
                ) from exc
        if not isinstance(key, ec.EllipticCurvePrivateKey):
            raise EntitlementError("PROVIDER_ERROR", "The purchase provider failed.", 503)
        self._private_key = key
        return key

    def _caller_jwt(self) -> str:
        now = int(time.time())
        payload = {
            "iss": self._settings.APPLE_ISSUER_ID,
            "iat": now,
            "exp": now + _CALLER_TOKEN_TTL_S,
            "aud": "appstoreconnect-v1",
            "bid": self._settings.APPLE_BUNDLE_ID,
        }
        headers = {
            "alg": "ES256",
            "kid": self._settings.APPLE_KEY_ID,
            "typ": "JWT",
        }
        return jwt.encode(payload, self._load_key(), algorithm="ES256", headers=headers)

    # -- JWS verification ----------------------------------------------------

    @staticmethod
    def _parse_roots(root_certs: Sequence[bytes]) -> tuple[Certificate, ...]:
        roots: list[Certificate] = []
        for blob in root_certs:
            try:
                roots.append(x509.load_pem_x509_certificate(blob))
            except ValueError:
                roots.append(x509.load_der_x509_certificate(blob))
        return tuple(roots)

    @staticmethod
    def _b64url(value: bytes) -> str:
        return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")

    @staticmethod
    def _b64url_decode(value: str) -> bytes:
        return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))

    def _verify_jws(self, signed: str) -> dict[str, Any]:
        """Verify one App Store JWS and return its decoded claims.

        Steps: well-formed JWS → ES256 → leaf certificate from x5c → chain
        terminates at a configured Apple root → signature valid. Any failure
        is an unverified payload and, per the trust boundary, is treated as
        an invalid purchase rather than an unverified-but-usable one.
        """
        try:
            parts = signed.strip().split(".")
            if len(parts) != 3:
                raise ValueError("malformed JWS")
            header = jwt.get_unverified_header(signed)
            if header.get("alg") != "ES256":
                raise ValueError(f"unexpected alg {header.get('alg')!r}")
            chain = header.get("x5c")
            if not chain or not isinstance(chain, list):
                raise ValueError("x5c missing")
            leaf = x509.load_der_x509_certificate(base64.b64decode(chain[0].encode("ascii")))
            self._require_chain_to_root(chain)
            claims = jwt.decode(
                signed,
                key=cast(Any, leaf.public_key()),
                algorithms=["ES256"],
                options={"verify_exp": False},
            )
            if not isinstance(claims, dict):
                raise TypeError("claims not object")
            return claims
        except (
            ValueError,
            TypeError,
            InvalidSignature,
            jwt.InvalidTokenError,
            jwt.DecodeError,
        ) as exc:
            raise EntitlementError(
                "PURCHASE_INVALID", "This purchase could not be verified.", 422
            ) from exc

    def _require_chain_to_root(self, chain: list[object]) -> None:
        if not self._roots:
            # No trust anchors configured -> nothing can be trusted. Fail
            # closed; an operator must supply Apple's roots to enable store
            # verification (see APPLE_ROOT_CERTS_PATH).
            raise ValueError("no trust anchors")
        certs = [x509.load_der_x509_certificate(base64.b64decode(str(c))) for c in chain]
        for issuer, subject_cert in zip(certs[1:], certs[:-1]):
            subject_cert.verify_directly_issued_by(issuer)
        last = certs[-1]
        trusted = False
        for root in self._roots:
            if root == last:
                trusted = True
                break
            try:
                last.verify_directly_issued_by(root)
                trusted = True
                break
            except InvalidSignature:
                continue
        if not trusted:
            raise ValueError("chain does not reach a trusted root")

    # -- API call ----------------------------------------------------------

    async def _request(self, path: str) -> httpx.Response:
        headers = {
            "Authorization": f"Bearer {self._caller_jwt()}",
            "Accept": "application/json",
        }
        url = self._base_url() + path
        timeout = self._settings.APPLE_REQUEST_TIMEOUT_SECONDS
        if self._http is not None:
            return await self._http.get(url, headers=headers)
        async with httpx.AsyncClient(timeout=timeout) as client:
            return await client.get(url, headers=headers)

    async def _fetch(self, token: str) -> dict[str, Any]:
        """GET Get All Subscription Statuses; returns the parsed response."""
        try:
            response = await self._request(f"/inApps/v1/subscriptions/{quote(token, safe='')}")
        except httpx.HTTPError as exc:
            raise EntitlementError("PROVIDER_ERROR", "The purchase provider failed.", 503) from exc
        if response.status_code == 200:
            try:
                body = response.json()
            except ValueError as exc:
                raise EntitlementError(
                    "PROVIDER_ERROR", "The purchase provider failed.", 503
                ) from exc
            if not isinstance(body, dict) or not isinstance(body.get("data"), list):
                raise EntitlementError("PROVIDER_ERROR", "The purchase provider failed.", 503)
            return body
        if 400 <= response.status_code < 500 and response.status_code not in (401, 403, 429):
            # 404/410: this app id never minted this transaction/say not found.
            raise EntitlementError("PURCHASE_INVALID", "This purchase could not be verified.", 422)
        raise EntitlementError("PROVIDER_ERROR", "The purchase provider failed.", 503)

    # -- shared resolution -------------------------------------------------

    def _require_catalog_product(self, product_id: str) -> ProviderProduct:
        product = provider_product_for(SubscriptionProvider.APP_STORE, product_id)
        if product is None:
            raise EntitlementError("UNKNOWN_PRODUCT", "Unknown product.", 422)
        return product

    def _rider_uuid(self, claims: dict[str, Any]) -> uuid.UUID | None:
        raw = claims.get("appAccountToken")
        if not raw:
            return None
        try:
            return uuid.UUID(str(raw))
        except ValueError:
            return None

    def _require_transaction(
        self, item: dict[str, Any]
    ) -> tuple[dict[str, Any], dict[str, Any] | None]:
        tx_claims = self._verify_jws(str(item.get("signedTransactionInfo", "")))
        renewal_claims = None
        if item.get("signedRenewalInfo"):
            renewal_claims = self._verify_jws(str(item["signedRenewalInfo"]))
        return tx_claims, renewal_claims

    def _select_item(
        self, body: dict[str, Any], *, token: str
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any] | None]:
        """Pick and verify the last-transaction entry matching ``token``.

        The token may be either a transactionId (renewal submitted by the
        client) or an originalTransactionId (restore with the original token);
        either is a valid verification key.
        """
        items: list[dict[str, Any]] = []
        for group in body.get("data", []):
            for item in group.get("lastTransactions", []):
                if isinstance(item, dict) and item.get("signedTransactionInfo"):
                    items.append(item)
        if not items:
            raise EntitlementError("PURCHASE_INVALID", "This purchase could not be verified.", 422)
        for item in items:
            tx_claims, renewal_claims = self._require_transaction(item)
            if (
                tx_claims.get("transactionId") == token
                or tx_claims.get("originalTransactionId") == token
            ):
                return item, tx_claims, renewal_claims
        if len(items) == 1:
            tx_claims, renewal_claims = self._require_transaction(items[0])
            return items[0], tx_claims, renewal_claims
        raise EntitlementError("PURCHASE_INVALID", "This purchase could not be verified.", 422)

    def _normalize(
        self,
        claims: dict[str, Any],
        renewal_claims: dict[str, Any] | None,
        *,
        expected_user_id: uuid.UUID,
        check_rider: bool = True,
    ) -> dict[str, Any]:
        """Turn verified transaction claims into server-side facts.

        All checks are order-independent but all are mandatory: a purchase
        that fails any of them is rejected without applying state.
        """
        if claims.get("type") != _SUBSCRIPTION_TYPE:
            raise EntitlementError("PURCHASE_INVALID", "This is not a subscription purchase.", 422)
        if str(claims.get("bundleId", "")) != self._settings.APPLE_BUNDLE_ID:
            raise EntitlementError(
                "PURCHASE_INVALID", "This purchase does not belong to this app.", 422
            )
        env = str(claims.get("environment", ""))
        if env == "Production":
            environment = VerificationEnvironment.PRODUCTION
        elif env == "Sandbox":
            environment = VerificationEnvironment.SANDBOX
        else:
            environment = VerificationEnvironment.UNKNOWN
        rider = self._rider_uuid(claims)
        if check_rider and (rider is None or rider != expected_user_id):
            raise EntitlementError(
                "CROSS_USER_PURCHASE", "This purchase belongs to another rider.", 409
            )
        product = self._require_catalog_product(str(claims.get("productId", "")))
        product_id = str(claims.get("productId", ""))
        expires = _epoch_ms(claims.get("expiresDate"))
        start = _epoch_ms(claims.get("purchaseDate"))
        occurred = _epoch_ms(claims.get("signedDate")) if claims.get("signedDate") else start
        now = _now()
        if claims.get("revocationDate"):
            status = SubscriptionStatus.REVOKED
        elif now >= expires:
            status = SubscriptionStatus.EXPIRED
        else:
            status = SubscriptionStatus.ACTIVE
        cancel_at_period_end = False
        if renewal_claims is not None:
            cancel_at_period_end = renewal_claims.get("autoRenewStatus") == 0
        return {
            "user_id": rider,
            "product_id": product_id,
            "internal_product_id": product.product_id,
            "plan": product.plan,
            "status": status,
            "environment": environment,
            "start": start,
            "end": expires,
            "occurred_at": occurred,
            "transaction_id": str(claims.get("transactionId", "")),
            "subscription_id": str(claims.get("originalTransactionId", "")),
            "cancel_at_period_end": cancel_at_period_end,
        }

    # -- protocol ------------------------------------------------------------

    async def verify_purchase(
        self, *, purchase_token: str, expected_user_id: uuid.UUID
    ) -> VerifiedPurchase:
        token = purchase_token.strip()
        if not token:
            raise EntitlementError("PURCHASE_INVALID", "Missing purchase token.", 422)
        body = await self._fetch(token)
        _item, tx_claims, renewal_claims = self._select_item(body, token=token)
        facts = self._normalize(
            tx_claims, renewal_claims, expected_user_id=expected_user_id, check_rider=True
        )
        assert facts["user_id"] is not None
        return VerifiedPurchase(
            provider=self.name,
            provider_subscription_id=facts["subscription_id"],
            user_id=facts["user_id"],
            plan=facts["plan"],
            status=facts["status"],
            current_period_start=facts["start"],
            current_period_end=facts["end"],
            occurred_at=facts["occurred_at"],
            provider_event_id=f"apple:{facts['transaction_id']}",
            product_id=facts["internal_product_id"],
            environment=facts["environment"],
            cancel_at_period_end=facts["cancel_at_period_end"],
            provider_transaction_id=facts["transaction_id"],
        )

    async def get_subscription(self, *, provider_subscription_id: str) -> ProviderSubscriptionView:
        sub_id = provider_subscription_id.strip()
        if not sub_id:
            raise EntitlementError("PURCHASE_INVALID", "Missing transaction id.", 422)
        body = await self._fetch(sub_id)
        _item, tx_claims, renewal_claims = self._select_item(body, token=sub_id)
        facts = self._normalize(
            tx_claims, renewal_claims, expected_user_id=uuid.UUID(int=0), check_rider=False
        )
        if facts["user_id"] is None:
            raise EntitlementError("PURCHASE_INVALID", "This purchase has no rider binding.", 422)
        return ProviderSubscriptionView(
            provider=self.name,
            provider_subscription_id=facts["subscription_id"],
            user_id=facts["user_id"],
            plan=facts["plan"],
            status=facts["status"],
            current_period_start=facts["start"],
            current_period_end=facts["end"],
            cancel_at_period_end=facts["cancel_at_period_end"],
        )

    async def process_event(
        self, *, payload: Mapping[str, object], signature: str | None
    ) -> ProviderSubscriptionEvent:
        toast = signature
        if toast is None and isinstance(payload, dict):
            toast = payload.get("signedPayload")
        if not toast:
            raise EntitlementError("PURCHASE_INVALID", "Missing signed payload.", 422)
        decoded = self._verify_jws(str(toast))
        notification_type = str(decoded.get("notificationType") or "")
        if not notification_type:
            raise EntitlementError("PURCHASE_INVALID", "Missing notification type.", 422)
        data = decoded.get("data")
        if not isinstance(data, dict) or not data.get("signedTransactionInfo"):
            raise EntitlementError("PURCHASE_INVALID", "Missing transaction data.", 422)
        tx_claims = self._verify_jws(str(data.get("signedTransactionInfo", "")))
        renewal_claims = None
        if data.get("signedRenewalInfo"):
            renewal_claims = self._verify_jws(str(data["signedRenewalInfo"]))
        facts = self._normalize(
            tx_claims, renewal_claims, expected_user_id=uuid.UUID(int=0), check_rider=False
        )
        rider = facts["user_id"]
        if rider is None:
            raise EntitlementError("PURCHASE_INVALID", "This purchase has no rider binding.", 422)
        if facts["environment"] is VerificationEnvironment.SANDBOX and self._settings.is_production:
            # A server notification from Apple's sandbox changes real state no
            # more than a sandbox purchase does: the notification is signed, so
            # it is genuinely from Apple — but it is from Apple's test store,
            # and a test store must never move production entitlements.
            # Rejected loudly (422); Apple retries, and re-verifying can never
            # promote a sandbox event because the gate is on the payload.
            raise EntitlementError(
                "ENVIRONMENT_MISMATCH",
                "A test notification cannot change production access.",
                422,
            )
        status, cancel = self._event_state(
            notification_type,
            renewal_claims,
            decoded.get("subtype"),
        )
        occurred = (
            _epoch_ms(decoded.get("signedDate"))
            if decoded.get("signedDate")
            else facts["occurred_at"]
        )
        if cancel and status is not SubscriptionStatus.CANCELED:
            # A change-to-canceled notification is the only way our state
            # model knows end-of-period cancellation; renewals upstream keep
            # the period but a canceled status must carry the flag instead.
            status = SubscriptionStatus.CANCELED
        return ProviderSubscriptionEvent(
            provider=self.name,
            provider_subscription_id=facts["subscription_id"],
            provider_event_id=f"apple:{notification_type}:{facts['transaction_id']}:{occurred.timestamp():.0f}",
            user_id=rider,
            plan=facts["plan"],
            status=status,
            effective_start=facts["start"],
            effective_end=facts["end"],
            occurred_at=occurred,
            cancel_at_period_end=cancel,
        )

    @staticmethod
    def _event_state(
        notification_type: str,
        renewal_claims: dict[str, Any] | None,
        subtype: object = None,
    ) -> tuple[SubscriptionStatus, bool]:
        if notification_type == "DID_CHANGE_RENEWAL_STATUS":
            if subtype == "AUTO_RENEW_DISABLED":
                return SubscriptionStatus.CANCELED, True
            if subtype == "AUTO_RENEW_ENABLED":
                return SubscriptionStatus.ACTIVE, False
            return (
                SubscriptionStatus.CANCELED
                if (renewal_claims or {}).get("autoRenewStatus") == 0
                else SubscriptionStatus.ACTIVE
            ), False
        if notification_type in ("EXPIRED",):
            return SubscriptionStatus.EXPIRED, False
        if notification_type in ("REFUND", "REVOKE"):
            return SubscriptionStatus.REVOKED, False
        if notification_type in (
            "GRACE_PERIOD_STARTED",
            "DID_FAIL_TO_RENEW",
            "GRACE_PERIOD_EXPIRED",
        ):
            return SubscriptionStatus.PAST_DUE, False
        if notification_type in (
            "SUBSCRIBED",
            "DID_RENEW",
            "RENEWAL_EXTENDED",
            "OFFER_REDEEMED",
            "PRICE_INCREASE",
            "DID_CHANGE_RENEWAL_PREF",
        ):
            return SubscriptionStatus.ACTIVE, False
        raise EntitlementError("PURCHASE_INVALID", "Unsupported notification type.", 422)

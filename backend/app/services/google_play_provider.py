"""Google Play verification adapter (WS-RP Phase 4).

Implements the provider-neutral contract from
:mod:`app.services.subscription_providers` against the Play Developer API
endpoint selected by the Phase 0 audit (docs/store-provider-audit.md G5/G6):

    purchases.subscriptionsv2.get
      GET {base}/androidpublisher/v3/applications/{packageName}/purchases/
          subscriptionsv2/tokens/{token}

The deprecated ``purchases.subscriptions.get`` is never called.

Trust boundaries honored here (audit G17, G18, X3, §8.2, §8.3):

* Authentication is the server-side Google *service-account* OAuth2 flow
  (RS256 JWT assertion -> access token). Credentials, the assertion, the
  access token, and purchase tokens never leave the server and are never
  logged, labeled, echoed, or returned.
* Google has no separate sandbox API: the SAME endpoint serves test and
  production purchases. Environment is therefore derived server-side from the
  authoritative response's ``testPurchase`` flag (plus the enforced package
  name); the client can neither declare it nor override it. A missing/
  invalid/unknown state is never treated as ACTIVE.
* Package identity is enforced by construction: the request is issued against
  the CONFIGURED package name, so a token minted for another application
  cannot come back valid under ours.
* Product identity is resolved only through the server catalog registry
  (:mod:`app.services.store_products`); unknown/inactive products fail closed
  and no client-supplied id can map to an entitlement.
* expiries come from the authoritative response (``expiryTime``), never the
  client.
* ``process_event`` maps a Real-time Developer Notification (RTDN) to a
  normalized event by RE-FETCHING authoritative state via subscriptionsv2.get
  (audit G13) instead of trusting notification fields. The OIDC push-token
  verification that authenticates the HTTP callback is the webhook layer's job
  (Phase 10) and is not duplicated here.

The Google adapter diverges from Apple in one documented way: RTDN has no JWS
inside ``message.data`` — authentication happens at the Pub/Sub push boundary,
and state correctness happens by re-fetching the API. ``process_event``
therefore treats ``signature`` as a passed-already-verified webhook signal
(never re-parseable here) and always re-fetches supplier state.
"""

import base64
import hashlib
import json
import time
import uuid
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast
from urllib.parse import quote

import httpx
import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

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

_BASE = "https://androidpublisher.googleapis.com"
_TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
_SCOPE = "https://www.googleapis.com/auth/androidpublisher"
_GRANT_TYPE = "urn:ietf:params:oauth:grant-type:jwt-bearer"

#: Provider outage vs. invalid purchase: these MUST stay distinct domain
#: results: a store outage never grants.
_OPAQUE_PROVIDER_ERROR = "The purchase provider failed."


def _now() -> datetime:
    return datetime.now(UTC)


def _parse_rfc3339(value: Any, *, required: bool = False) -> datetime | None:
    if value is None:
        if required:
            raise EntitlementError("PURCHASE_INVALID", "This purchase could not be verified.", 422)
        return None
    text = str(value).strip()
    if not text:
        if required:
            raise EntitlementError("PURCHASE_INVALID", "This purchase could not be verified.", 422)
        return None
    try:
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt
    except ValueError:
        raise EntitlementError(
            "PURCHASE_INVALID", "This purchase could not be verified.", 422
        ) from None


def _rtdn_int(mapping: Mapping[str, Any], key: str, *, default: int = 0) -> int:
    raw: Any = mapping.get(key)
    if raw is None:
        return default
    return int(raw)


#: The notification types the internal model can represent (audit G12/G13).
#: Everything else is rejected BEFORE the state refetch: an unrecognized type
#: would consume an API call and produce a refetched snapshot that still has
#: no legal mapping, so failing fast is cheaper and just as authoritative.
_SUPPORTED_NOTIFICATION_TYPES = frozenset({1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13})


def _require_supported_notification_type(notification_type: int) -> None:
    if notification_type not in _SUPPORTED_NOTIFICATION_TYPES:
        raise EntitlementError("PURCHASE_INVALID", "Unsupported notification type.", 422)


class GooglePlayClient:
    """Thin, injectable HTTP boundary around the Play Developer API.

    Owns the OAuth2 service-account token dance and the two conversations the
    adapter needs. Domain logic lives in :class:`GoogleStoreProvider`; this
    class only shapes requests and classifies HTTP results.
    """

    def __init__(
        self,
        *,
        http: httpx.AsyncClient | None = None,
        credentials: Mapping[str, object] | None = None,
        token_uri: str | None = None,
        timeout_seconds: int = 10,
    ) -> None:
        self._http = http
        self._credentials = credentials
        self._token_uri = token_uri or _TOKEN_ENDPOINT
        self._timeout = timeout_seconds
        self._access_token_value: str | None = None
        self._access_token_expires_at: float = 0.0

    @property
    def credentials_configured(self) -> bool:
        if not self._credentials:
            return False
        return bool(
            (self._credentials.get("type") == "service_account")
            and self._credentials.get("client_email")
            and self._credentials.get("private_key")
        )

    # -- OAuth2 -------------------------------------------------------------

    def _load_signing_key(self) -> rsa.RSAPrivateKey:
        credentials = self._credentials
        if credentials is None or not credentials.get("private_key"):
            raise EntitlementError("PROVIDER_ERROR", _OPAQUE_PROVIDER_ERROR, 503)
        key = serialization.load_pem_private_key(
            str(credentials["private_key"]).encode("utf-8"), password=None
        )
        if not isinstance(key, rsa.RSAPrivateKey):
            raise EntitlementError("PROVIDER_ERROR", _OPAQUE_PROVIDER_ERROR, 503)
        return key

    def _assertion_jwt(self) -> str:
        credentials = self._credentials
        if credentials is None:
            raise EntitlementError("PROVIDER_ERROR", _OPAQUE_PROVIDER_ERROR, 503)
        now = int(time.time())
        payload = {
            "iss": str(credentials.get("client_email") or ""),
            "scope": _SCOPE,
            "aud": self._token_uri,
            "iat": now,
            "exp": now + 3599,
        }
        headers = {"alg": "RS256", "typ": "JWT"}
        return jwt.encode(
            payload,
            cast(Any, self._load_signing_key()),
            algorithm="RS256",
            headers=headers,
        )

    async def _access_token(self) -> str:
        # Cached until ~1 minute before expiry so repeated verifications
        # don't re-sign and re-exchange on every call.
        if self._access_token_value and time.time() < self._access_token_expires_at - 60:
            return self._access_token_value
        data = {"grant_type": _GRANT_TYPE, "assertion": self._assertion_jwt()}
        try:
            if self._http is not None:
                response = await self._http.post(self._token_uri, data=data)
            else:
                async with httpx.AsyncClient(timeout=self._timeout) as client:
                    response = await client.post(self._token_uri, data=data)
        except httpx.HTTPError as exc:
            raise EntitlementError("PROVIDER_ERROR", _OPAQUE_PROVIDER_ERROR, 503) from exc
        if response.status_code != 200:
            raise EntitlementError("PROVIDER_ERROR", _OPAQUE_PROVIDER_ERROR, 503)
        try:
            body = response.json()
        except ValueError as exc:
            raise EntitlementError("PROVIDER_ERROR", _OPAQUE_PROVIDER_ERROR, 503) from exc
        token = body.get("access_token")
        if not isinstance(token, str) or not token:
            raise EntitlementError("PROVIDER_ERROR", _OPAQUE_PROVIDER_ERROR, 503)
        self._access_token_value = token
        self._access_token_expires_at = time.time() + int(body.get("expires_in", 3600))
        return token

    # -- API ----------------------------------------------------------------

    @staticmethod
    def _classify(status: int) -> EntitlementError:
        # 400/404: the token is not a Play subscription purchase for this
        # app — an invalid purchase, distinct from a provider being down.
        if status in (400, 404):
            return EntitlementError("PURCHASE_INVALID", "This purchase could not be verified.", 422)
        if status in (401, 403):
            # Our own credentials are bad/forbidden; the request was valid but
            # the server could not authenticate it. Never echo why, never grant.
            return EntitlementError("PROVIDER_ERROR", _OPAQUE_PROVIDER_ERROR, 503)
        if status == 429:
            return EntitlementError("PROVIDER_ERROR", _OPAQUE_PROVIDER_ERROR, 503)
        return EntitlementError("PROVIDER_ERROR", _OPAQUE_PROVIDER_ERROR, 503)

    def _publisher_url(self, *, package_name: str, purchase_token: str) -> str:
        return (
            f"{_BASE}/androidpublisher/v3/applications/"
            f"{quote(package_name, safe='')}/purchases/subscriptionsv2/tokens/"
            f"{quote(purchase_token, safe='')}"
        )

    async def _publisher_request(self, url: str, *, method: str = "GET") -> httpx.Response:
        headers = {"Authorization": f"Bearer {await self._access_token()}"}
        try:
            if self._http is not None:
                return await self._http.request(method, url, headers=headers)
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                return await client.request(method, url, headers=headers)
        except httpx.HTTPError as exc:
            raise EntitlementError("PROVIDER_ERROR", _OPAQUE_PROVIDER_ERROR, 503) from exc

    async def get_subscription(self, *, package_name: str, purchase_token: str) -> dict[str, Any]:
        """GET subscriptionsv2.get; raises classified EntitlementError."""
        response = await self._publisher_request(
            self._publisher_url(package_name=package_name, purchase_token=purchase_token)
        )
        if response.status_code != 200:
            raise self._classify(response.status_code)
        try:
            body = response.json()
        except ValueError as exc:
            raise EntitlementError("PROVIDER_ERROR", _OPAQUE_PROVIDER_ERROR, 503) from exc
        if not isinstance(body, dict):
            raise EntitlementError("PROVIDER_ERROR", _OPAQUE_PROVIDER_ERROR, 503)
        return body

    async def acknowledge(self, *, package_name: str, purchase_token: str) -> None:
        """POST the :acknowledge action; returns once acknowledged."""
        response = await self._publisher_request(
            self._publisher_url(package_name=package_name, purchase_token=purchase_token)
            + ":acknowledge",
            method="POST",
        )
        if response.status_code not in (200, 204):
            raise self._classify(response.status_code)


class GoogleStoreProvider(SubscriptionProviderProtocol):
    """Play Developer API adapter over the provider-neutral contract.

    Construction is fully testable: HTTP, service-account credentials, and the
    OAuth token endpoint are all injectable, matching the Apple adapter. In
    production everything comes from settings; the default is unconfigured,
    so the registry stays empty until an operator actually enables Play.
    """

    name = SubscriptionProvider.PLAY_STORE

    def __init__(
        self,
        *,
        http: httpx.AsyncClient | None = None,
        credentials: Mapping[str, object] | None = None,
        token_uri: str | None = None,
    ) -> None:
        self._settings = default_settings
        self._http = http
        self._client = GooglePlayClient(
            http=http,
            credentials=credentials,
            token_uri=token_uri,
            timeout_seconds=self._settings.GOOGLE_REQUEST_TIMEOUT_SECONDS,
        )

    # -- configuration ------------------------------------------------------

    def configured(self) -> bool:
        s = self._settings
        if not s.GOOGLE_PLAY_ENABLED:
            return False
        return bool(s.GOOGLE_PACKAGE_NAME.strip()) and self._client.credentials_configured

    def _load_credentials(self) -> Mapping[str, object]:
        if self._client.credentials_configured:
            return self._client._credentials or {}
        path = Path(self._settings.GOOGLE_APPLICATION_CREDENTIALS_PATH)
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise EntitlementError("PROVIDER_ERROR", _OPAQUE_PROVIDER_ERROR, 503) from exc
        if not isinstance(raw, dict):
            raise EntitlementError("PROVIDER_ERROR", _OPAQUE_PROVIDER_ERROR, 503)
        credentials = {str(k): v for k, v in raw.items()}
        self._client._credentials = credentials
        return credentials

    @property
    def package_name(self) -> str:
        package = self._settings.GOOGLE_PACKAGE_NAME.strip()
        if not package:
            raise EntitlementError("PROVIDER_ERROR", _OPAQUE_PROVIDER_ERROR, 503)
        return package

    # -- normalization ------------------------------------------------------

    @staticmethod
    def _catalog_product(product_id: str) -> ProviderProduct:
        product = provider_product_for(SubscriptionProvider.PLAY_STORE, product_id.strip())
        if product is None:
            raise EntitlementError("UNKNOWN_PRODUCT", "Unknown product.", 422)
        return product

    def _normalize(
        self,
        body: Mapping[str, Any],
        *,
        token: str,
        expected_user_id: uuid.UUID,
        check_rider: bool = True,
    ) -> dict[str, Any]:
        """Turn an authoritative subscriptionsv2 response into server facts.

        Every check is mandatory and order-independent; any failure rejects
        the purchase without applying state. The lookup token IS the stable
        external subscription identity (audit §8.1) — it is not echoed by the
        API, so it is carried in from the request, never taken from the body.
        """
        state = str(body.get("subscriptionState") or "")
        if not state or state in ("UNSPECIFIED", ""):
            raise EntitlementError("PURCHASE_INVALID", "This purchase could not be verified.", 422)

        riders = body.get("externalAccountIdentifiers") or {}
        rider_raw = riders.get("obfuscatedExternalAccountId") if isinstance(riders, dict) else None
        rider: uuid.UUID | None = None
        if rider_raw:
            try:
                rider = uuid.UUID(str(rider_raw))
            except ValueError:
                rider = None
        if check_rider and (rider is None or rider != expected_user_id):
            raise EntitlementError(
                "CROSS_USER_PURCHASE", "This purchase belongs to another rider.", 409
            )

        product = self._catalog_product(_product_id(body))
        environment = (
            VerificationEnvironment.SANDBOX
            if bool(body.get("testPurchase"))
            else VerificationEnvironment.PRODUCTION
        )

        expiry, start = self._period(body)
        status, cancel, occurred = self._commercial_state(body, expiry)

        stamp = occurred.isoformat()
        return {
            "user_id": rider,
            "product_id": _product_id(body),
            "internal_product_id": product.product_id,
            "plan": product.plan,
            "status": status,
            "environment": environment,
            "start": start,
            "end": expiry,
            "occurred_at": occurred,
            "transaction_stamp": f"{status.value}:{stamp}",
            "subscription_id": token,
            "cancel_at_period_end": cancel,
        }

    async def _resolve(
        self, token: str, *, expected_user_id: uuid.UUID, check_rider: bool
    ) -> dict[str, Any]:
        token = token.strip()
        if not token:
            raise EntitlementError("PURCHASE_INVALID", "Missing purchase token.", 422)
        body = await self._client.get_subscription(
            package_name=self.package_name, purchase_token=token
        )
        return self._normalize(
            body, token=token, expected_user_id=expected_user_id, check_rider=check_rider
        )

    # -- protocol ------------------------------------------------------------

    async def verify_purchase(
        self, *, purchase_token: str, expected_user_id: uuid.UUID
    ) -> VerifiedPurchase:
        self._load_credentials()
        facts = await self._resolve(
            purchase_token, expected_user_id=expected_user_id, check_rider=True
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
            provider_event_id=f"play:{facts['transaction_stamp']}",
            product_id=facts["internal_product_id"],
            environment=facts["environment"],
            cancel_at_period_end=facts["cancel_at_period_end"],
            provider_transaction_id=facts["transaction_stamp"],
        )

    async def get_subscription(self, *, provider_subscription_id: str) -> ProviderSubscriptionView:
        self._load_credentials()
        facts = await self._resolve(
            provider_subscription_id, expected_user_id=uuid.UUID(int=0), check_rider=False
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

    async def acknowledge(self, *, purchase_token: str) -> None:
        self._load_credentials()
        await self._client.acknowledge(
            package_name=self.package_name, purchase_token=purchase_token
        )

    async def process_event(
        self, *, payload: Mapping[str, object], signature: str | None
    ) -> ProviderSubscriptionEvent:
        """Map an RTDN push to a normalized event by re-fetching state.

        ``payload`` is the Pub/Sub push body (``message.data`` is base64 JSON)
        or an already-decoded RTDN mapping. ``signature`` is the OIDC push
        token, verified by the webhook layer (audit G13; Phase 10) — RTDN has
        no JWS to parse here, so state is ALWAYS re-fetched and never taken
        from notification fields.
        """
        if signature is not None:
            # Documented asymmetry: the OIDC token belongs to the HTTP boundary
            # (audience/issuer context lives there). We do not fabricate a
            # JWS-style parse for a channel that has none.
            pass
        message = payload.get("message")
        if isinstance(message, dict):
            data_field = message.get("data")
            if isinstance(data_field, str):
                try:
                    decoded = json.loads(base64.b64decode(data_field))
                except (ValueError, TypeError) as exc:
                    raise EntitlementError(
                        "PURCHASE_INVALID", "This purchase could not be verified.", 422
                    ) from exc
                if not isinstance(decoded, dict):
                    raise EntitlementError(
                        "PURCHASE_INVALID", "This purchase could not be verified.", 422
                    )
                return await self._event_from_rtdn(decoded)
        if isinstance(payload, dict) and payload.get("subscriptionNotification"):
            return await self._event_from_rtdn(dict(payload))
        raise EntitlementError("PURCHASE_INVALID", "This purchase could not be verified.", 422)

    async def _event_from_rtdn(self, decoded: Mapping[str, Any]) -> ProviderSubscriptionEvent:
        self._load_credentials()
        notify = decoded.get("subscriptionNotification")
        if not isinstance(notify, dict):
            raise EntitlementError("PURCHASE_INVALID", "This purchase could not be verified.", 422)
        package = str(decoded.get("packageName") or "")
        if package and package != self.package_name:
            # The subscription belongs to a different application. Nothing
            # here may be trusted (audit G13 "names ours").
            raise EntitlementError("PURCHASE_INVALID", "This purchase could not be verified.", 422)
        try:
            ntype = _rtdn_int(notify, "notificationType")
            event_ms = _rtdn_int(decoded, "eventTimeMillis")
        except (TypeError, ValueError):
            raise EntitlementError(
                "PURCHASE_INVALID", "This purchase could not be verified.", 422
            ) from None
        _require_supported_notification_type(ntype)
        token = str(notify.get("purchaseToken") or "").strip()
        if not token:
            raise EntitlementError("PURCHASE_INVALID", "Missing purchase token.", 422)
        body = await self._client.get_subscription(
            package_name=package or self.package_name, purchase_token=token
        )
        facts = self._normalize(
            body, token=token, expected_user_id=uuid.UUID(int=0), check_rider=False
        )
        rider = facts["user_id"]
        if rider is None:
            raise EntitlementError("PURCHASE_INVALID", "This purchase has no rider binding.", 422)
        if facts["environment"] is VerificationEnvironment.SANDBOX and self._settings.is_production:
            # A real-time notification that resolves to a TEST purchase on a
            # production server must not move production entitlements, any more
            # than a sandbox verification would (the same ENVIRONMENT_MISMATCH
            # discipline as verification and Apple events). The refetch is
            # authoritative, so the gate reads the server's own answer.
            raise EntitlementError(
                "ENVIRONMENT_MISMATCH",
                "A test notification cannot change production access.",
                422,
            )
        status, cancel = _notification_state(ntype, facts["end"])
        occurred = datetime.fromtimestamp(event_ms / 1000.0, tz=UTC)
        digest = hashlib.sha256(f"{token}|{event_ms}".encode()).hexdigest()[:20]
        return ProviderSubscriptionEvent(
            provider=self.name,
            provider_subscription_id=token,
            provider_event_id=f"play:{ntype}:{digest}",
            user_id=rider,
            plan=facts["plan"],
            status=status,
            effective_start=facts["start"],
            effective_end=facts["end"],
            occurred_at=occurred,
            cancel_at_period_end=cancel,
        )

    # -- helpers ------------------------------------------------------------

    def _period(self, body: Mapping[str, Any]) -> tuple[datetime, datetime]:
        line_items = body.get("lineItems")
        if not isinstance(line_items, list) or not line_items:
            raise EntitlementError("PURCHASE_INVALID", "This purchase could not be verified.", 422)
        item = line_items[0]
        if not isinstance(item, dict):
            raise EntitlementError("PURCHASE_INVALID", "This purchase could not be verified.", 422)
        expiry = _parse_rfc3339(body.get("expiryTime") or item.get("expiryTime"), required=True)
        start = _parse_rfc3339(body.get("startTime") or item.get("startTime"), required=True)
        if expiry is None or start is None:
            raise EntitlementError("PURCHASE_INVALID", "This purchase could not be verified.", 422)
        return expiry, start

    def _commercial_state(
        self, body: Mapping[str, Any], expiry: datetime
    ) -> tuple[SubscriptionStatus, bool, datetime]:
        """Map authoritative v2 state into the internal model (audit §8.2)."""
        state = str(body.get("subscriptionState") or "")
        cancel_ctx = body.get("canceledStateContext")
        if isinstance(cancel_ctx, dict) and (
            cancel_ctx.get("revocation") or cancel_ctx.get("fullRefund")
        ):
            revoked_at = _parse_rfc3339(cancel_ctx.get("revocation", {}).get("revokeTime"))
            return SubscriptionStatus.REVOKED, False, revoked_at or expiry
        if state == "ACTIVE":
            return SubscriptionStatus.ACTIVE, False, expiry
        if state in ("PENDING", "IN_GRACE_PERIOD", "ON_HOLD", "IN_ACCOUNT_HOLD", "PAUSED"):
            # Dunning / account-hold / pause boundary (audit §8.2, pending O2):
            # the customer still holds the account — preserve, fail open only
            # through the existing period, never past it.
            return SubscriptionStatus.PAST_DUE, False, expiry
        if state == "CANCELED":
            canceled_at = None
            if isinstance(cancel_ctx, dict):
                user_cancel = cancel_ctx.get("userInitiatedCancellation")
                if isinstance(user_cancel, dict):
                    canceled_at = _parse_rfc3339(user_cancel.get("cancelTime"))
            return (
                SubscriptionStatus.CANCELED,
                expiry > _now(),
                canceled_at or expiry,
            )
        if state == "EXPIRED":
            return SubscriptionStatus.EXPIRED, False, expiry
        raise EntitlementError("PURCHASE_INVALID", "This purchase could not be verified.", 422)


def _product_id(body: Mapping[str, Any]) -> str:
    line_items = body.get("lineItems")
    if isinstance(line_items, list) and line_items:
        item = line_items[0]
        if isinstance(item, dict) and item.get("productId"):
            return str(item["productId"])
    raise EntitlementError("PURCHASE_INVALID", "This purchase could not be verified.", 422)


def _notification_state(
    notification_type: int, expiry: datetime
) -> tuple[SubscriptionStatus, bool]:
    if notification_type in (
        1,
        2,
        4,
        7,
        9,
        11,
    ):  # RECOVERED/RENEWED/PURCHASED/RESTARTED/DEFERRED/PAUSE_SCHEDULED
        return SubscriptionStatus.ACTIVE, False
    if notification_type == 3:  # CANCELED
        return SubscriptionStatus.CANCELED, expiry > _now()
    if notification_type in (5, 6, 10):  # ON_HOLD / IN_GRACE_PERIOD / PAUSED
        return SubscriptionStatus.PAST_DUE, False
    if notification_type == 12:  # REVOKED
        return SubscriptionStatus.REVOKED, False
    if notification_type == 13:  # EXPIRED
        return SubscriptionStatus.EXPIRED, False
    if notification_type == 8:  # PRICE_CHANGE_CONFIRMED (deprecated by Google)
        return SubscriptionStatus.ACTIVE, False
    raise EntitlementError("PURCHASE_INVALID", "Unsupported notification type.", 422)

"""Store server-notification (webhook) endpoints (Phase 10).

Two thin routes over the SAME provider adapters and subscription pipeline the
verification flows use:

    POST /api/v1/webhooks/apple   — App Store Server Notifications v2
    POST /api/v1/webhooks/google  — Google Play RTDN Pub/Sub push

Neither route interprets purchase data. They authenticate the callback, hand
the body to the provider adapter's ``process_event``, and apply the resulting
normalized event through ``apply_provider_event`` — the same idempotency,
ordering, and advisory-lock machinery as ``/store/purchases/verify``. There is
no second entitlement path and no provider business logic in this module.

Trust boundaries
----------------
* Apple: the JWS on ``signedPayload`` IS the signature. The adapter verifies
  the ES256 signature and the x5c chain to a configured Apple root before any
  field is trusted; the route simply passes the payload through.
* Google: the OIDC push token (``Authorization: Bearer``) is verified against
  Google's published JWKS before the envelope is decoded (app/services/
  pubsub_auth.py). RTDN fields are never authoritative: the adapter re-fetches
  ``subscriptionsv2.get`` before emitting an event.

Failure semantics
-----------------
* Any unauthenticated or unverifiable input is a 4xx. Apple and Pub/Sub retry
  on any non-2xx, so a 4xx is a permanent "not our event, drop it" signal and
  the retry pressure is safe.
* A provider outage/timeout is a 5xx, so the sender's delivery system retries
  later — retry is the safe direction; an error must never grant.
* A database failure is a 500 so the event is retried, never silently lost.
* Unknown notification types fail closed with 4xx (never a state change).
* A configured-but-not-registered provider is a 503: the adapter registry is
  what the rest of the pipeline uses, and this endpoint is no different.

Body size is capped before parsing, and rate limiting is a coarse per-IP
backstop via the shared in-memory limiter (per-process best effort, exactly as
documented for the store routes).

Logging: only outcome/status/provider — never the payload, never a signed
payload, never a purchase token, never the Authorization header, never a
request header value.
"""

import json
import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging import redact
from app.core.rate_limit import allow
from app.db.session import get_db
from app.models.subscription import SubscriptionProvider
from app.services.pubsub_auth import GoogleOidcPushVerifier, PushTokenError
from app.services.purchase_verification import provider_for
from app.services.subscription_service import (
    EntitlementError,
    ProviderSubscriptionEvent,
    apply_provider_event,
)

router = APIRouter(prefix="/webhooks", tags=["webhooks"])

log = logging.getLogger("cyclecoach")

#: Body cap: a server notification is at most a few KB. 1 MiB accepts Apple's
#: and Pub/Sub's real envelopes with huge margin while rejecting buffer-shaped
#: floods before they are parsed.
_MAX_BODY_BYTES = 1024 * 1024

#: Coarse per-IP backstop, per endpoint. Apple retries failed deliveries for up
#: to 24h and Pub/Sub until ack, so the ceiling is about abuse, not throughput.
#: The shared limiter is in-memory and per-process; documented as best effort.
_APPLE_RATE_LIMIT = 720  # per hour per IP
_GOOGLE_RATE_LIMIT = 7200  # per hour per IP


def _log(event: str, **fields: object) -> None:
    """Operational events only: bounded enums and outcome codes, never data."""
    log.info(f"webhook.{event}", extra=redact(dict(fields)))


def _err(e: EntitlementError) -> HTTPException:
    return HTTPException(status_code=e.status, detail={"code": e.code, "message": e.message})


def _limit(request: Request, key: str, limit: int, window_s: int = 3600) -> None:
    ident = request.client.host if request.client else "unknown"
    if not allow(f"webhooks:{key}:{ident}", limit, window_s):
        raise HTTPException(status_code=429, detail="Too many requests.")


async def _read_capped_body(request: Request) -> bytes:
    """Read the raw request body, refusing anything over the cap (413).

    Streamed rather than ``await request.body()`` so an oversized payload
    never enters memory as a whole.
    """
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > _MAX_BODY_BYTES:
            raise HTTPException(status_code=413, detail="Payload too large.")
        chunks.append(chunk)
    return b"".join(chunks)


def _json_object(body: bytes) -> dict[str, object]:
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        # Raised `from None`: a JSONDecodeError message quotes the offending
        # document fragment, and that fragment is attacker-supplied content the
        # response must not echo or log.
        raise HTTPException(status_code=400, detail="Invalid JSON body.") from None
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Invalid JSON body.")
    return payload


def _bearer_token(request: Request) -> str:
    header = request.headers.get("Authorization") or ""
    scheme, _, credentials = header.partition(" ")
    if scheme.lower() != "bearer" or not credentials.strip():
        raise HTTPException(status_code=403, detail="Unauthorized.")
    return credentials.strip()


#: Module-level singleton so the JWKS cache survives across deliveries. Tests
#: replace this attribute with a verifier injected with a fixed JWKS.
_google_verifier_instance: GoogleOidcPushVerifier | None = None


def _google_verifier() -> GoogleOidcPushVerifier:
    global _google_verifier_instance
    if _google_verifier_instance is None:
        _google_verifier_instance = GoogleOidcPushVerifier(
            audience=settings.GOOGLE_RTDN_OIDC_AUDIENCE.strip(),
            issuer=settings.GOOGLE_RTDN_OIDC_ISSUER,
            jwks_uri=settings.GOOGLE_RTDN_JWKS_URI,
        )
    return _google_verifier_instance


async def _apply(db: AsyncSession, event: ProviderSubscriptionEvent) -> None:
    try:
        await apply_provider_event(db, event)
    except EntitlementError as exc:
        raise _err(exc) from exc
    except SQLAlchemyError as exc:
        # A storage failure after the event was authenticated must not be
        # swallowed: the sender retries a 5xx, and retry is the only way the
        # event is not lost.
        _log(
            "error",
            provider=event.provider.value,
            kind="database",
            error_type=type(exc).__name__,
        )
        raise HTTPException(status_code=500, detail="Internal error.") from exc


@router.post("/apple")
async def apple_webhook(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, str]:
    """Receive an App Store Server Notification v2.

    The notification is authenticated by its own JWS: the route reads nothing
    from the payload and lets the adapter verify signature + chain + bundle +
    product + environment before any field is trusted.
    """
    _limit(request, "apple", _APPLE_RATE_LIMIT)
    if not (settings.APPLE_WEBHOOKS_ENABLED and settings.APPLE_STORE_ENABLED):
        raise HTTPException(status_code=503, detail="Webhook not configured.")
    adapter = provider_for(SubscriptionProvider.APP_STORE.value)
    if adapter is None:
        raise HTTPException(status_code=503, detail="Webhook not configured.")
    payload = _json_object(await _read_capped_body(request))
    try:
        event = await adapter.process_event(payload=payload, signature=None)
    except EntitlementError as exc:
        _log(
            "rejected",
            provider=SubscriptionProvider.APP_STORE.value,
            code=exc.code,
        )
        raise _err(exc) from exc
    await _apply(db, event)
    _log(
        "processed",
        provider=SubscriptionProvider.APP_STORE.value,
        status=event.status.value,
        plan=event.plan.value,
    )
    return {"status": "processed"}


@router.post("/google")
async def google_webhook(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, str]:
    """Receive a Google RTDN Pub/Sub push.

    The OIDC push token is verified against Google's published JWKS before the
    envelope is decoded. RTDN fields are never authoritative: the adapter
    re-fetches ``subscriptionsv2.get`` for the purchase token in the envelope
    and the route applies only the adapter's normalized event.
    """
    _limit(request, "google", _GOOGLE_RATE_LIMIT)
    if not (settings.GOOGLE_WEBHOOKS_ENABLED and settings.GOOGLE_PLAY_ENABLED):
        raise HTTPException(status_code=503, detail="Webhook not configured.")
    if settings.GOOGLE_RTDN_AUTH_MODE != "oidc" or not settings.GOOGLE_RTDN_OIDC_AUDIENCE.strip():
        raise HTTPException(status_code=503, detail="Webhook not configured.")
    token = _bearer_token(request)
    try:
        await _google_verifier().verify(token)
    except PushTokenError as exc:
        _log("rejected", provider=SubscriptionProvider.PLAY_STORE.value, code="UNAUTHORIZED")
        raise HTTPException(status_code=403, detail="Unauthorized.") from exc
    adapter = provider_for(SubscriptionProvider.PLAY_STORE.value)
    if adapter is None:
        raise HTTPException(status_code=503, detail="Webhook not configured.")
    payload = _json_object(await _read_capped_body(request))
    try:
        event = await adapter.process_event(payload=payload, signature=token)
    except EntitlementError as exc:
        _log(
            "rejected",
            provider=SubscriptionProvider.PLAY_STORE.value,
            code=exc.code,
        )
        raise _err(exc) from exc
    await _apply(db, event)
    _log(
        "processed",
        provider=SubscriptionProvider.PLAY_STORE.value,
        status=event.status.value,
        plan=event.plan.value,
    )
    return {"status": "processed"}

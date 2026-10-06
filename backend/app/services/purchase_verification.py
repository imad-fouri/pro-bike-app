"""Server-side purchase verification: the store trust boundary.

Authoritative flow:

    Store purchase → provider.verify_purchase → this module →
    subscription event → entitlement rows → EntitlementState

The mobile client is never in this chain as a decision-maker. It submits an
opaque purchase credential; everything after that — provider lookup, product
validation, verification, ownership binding, environment gating, idempotent
application — happens here, against the provider's answers and the server's
own catalog. A client that forges any claim gets a rejection shaped exactly
like any other validation failure, and no state changes.

What this module is NOT: a store integration. The provider registry is empty
in production until real adapters land, so every verification attempt today
ends in PROVIDER_UNAVAILABLE without contacting anything. That is the honest
state, and it is tested as such rather than faked around.

Responsibility split, enforced by construction:

* Provider adapters answer "is this purchase valid according to this
  provider?" and nothing else.
* This module answers "what subscription state should CycleCoach maintain?"
* ``subscription_service`` answers "what features does this user have?"

None of the three reaches into the others' decisions.
"""

import asyncio
import enum
import logging
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging import redact
from app.models.subscription import Plan, SubscriptionProvider
from app.services.store_products import plan_for_product
from app.services.subscription_providers import (
    SubscriptionProviderProtocol,
    VerificationEnvironment,
    VerifiedPurchase,
)
from app.services.subscription_service import (
    EntitlementError,
    EntitlementState,
    ProviderSubscriptionEvent,
    apply_provider_event,
    resolve_state,
)

log = logging.getLogger("cyclecoach")

#: Upper bound on one provider verification call. A store API that hangs must
#: fail the request, never the worker: without this bound a slow provider
#: becomes a thread-pool exhaustion vector.
PURCHASE_VERIFY_TIMEOUT_S = 10.0


class VerifyKind(str, enum.Enum):
    """Why a verification is running. One pipeline, two audit names.

    A restore IS a re-verification — the same idempotent flow, the same
    guards. Splitting them into two implementations would let the two paths
    disagree about what "verified" means, which is exactly how blind-grant
    restore bugs are born.
    """

    PURCHASE = "purchase"
    RESTORE = "restore"


_PROVIDERS: dict[SubscriptionProvider, SubscriptionProviderProtocol] = {}


def register_provider(provider: SubscriptionProviderProtocol) -> None:
    """Register a billing adapter. Called by provider integrations and tests.

    Production registers nothing until real Apple/Google adapters land, which
    is why every verification attempt today ends at PROVIDER_UNAVAILABLE.
    """
    _PROVIDERS[provider.name] = provider


def registered_providers() -> tuple[SubscriptionProvider, ...]:
    return tuple(_PROVIDERS)


def reset_providers() -> None:
    """Empty the registry. Test hook: each test starts from no providers."""
    _PROVIDERS.clear()


def _log(event: str, **fields: object) -> None:
    """Operational events only: bounded enums and codes, never credentials."""
    log.info(f"purchase.{event}", extra=redact(dict(fields)))


def _count(outcome: str, provider: str) -> None:
    try:
        from app.core.metrics import record_purchase_verification

        record_purchase_verification(outcome=outcome, provider=provider)
    except Exception as exc:  # noqa: BLE001 - metrics must not fail verification
        log.warning("metrics_record_failed", extra={"error_type": type(exc).__name__})


async def process_purchase(
    db: AsyncSession,
    *,
    kind: VerifyKind,
    provider_name: str,
    product_id: str,
    purchase_token: str,
    user_id: uuid.UUID,
) -> EntitlementState:
    """Run the full verification pipeline for one purchase credential.

    Steps, in order, each refusing before the next trusts anything:

    1. Provider name must be a known purchase origin (MANUAL is not one —
       it marks operational records, and a purchase "from manual" is a
       contradiction, rejected as unknown).
    2. A registered adapter must exist (none do in production yet).
    3. The product must be in the server catalog.
    4. The provider verifies the credential — with a timeout, and with any
       provider-raised domain error preserved verbatim.
    5. Ownership: the verified user must be the caller; the verified product
       must be the requested product; the verified plan must match the
       catalog plan for that product.
    6. Environment: unknown is always rejected; sandbox is rejected on a
       production server. The environment comes from verification, never
       from the request.
    7. The result is applied as a subscription event (idempotent,
       ordered, cross-user-guarded by the existing machinery).
    8. The caller's freshly resolved state is returned.

    ``purchase_token`` is used exactly once — as the verification argument —
    and is never stored, logged, or returned. It does not appear in any
    label, log field, dataclass, or response in this module.
    """
    provider = _resolve_provider(provider_name)
    catalog_plan = _require_product(product_id)
    adapter = _PROVIDERS.get(provider)
    if adapter is None:
        _log("unavailable", provider=provider.value, kind=kind.value)
        _count("unavailable", provider.value)
        raise EntitlementError(
            "PROVIDER_UNAVAILABLE",
            "Purchase verification is not available for this provider yet.",
            503,
        )
    verified = await _verify(adapter, purchase_token, user_id)
    _bind_ownership(verified_user_id=verified.user_id, user_id=user_id)
    _bind_product(
        requested=product_id,
        verified_product=verified.product_id,
        verified_plan=verified.plan,
        catalog_plan=catalog_plan,
    )
    _gate_environment(verified.environment)
    event = ProviderSubscriptionEvent(
        provider=verified.provider,
        provider_subscription_id=verified.provider_subscription_id,
        provider_event_id=(
            f"verify:{verified.provider.value}:"
            f"{verified.provider_subscription_id}:{verified.occurred_at.isoformat()}"
        ),
        user_id=user_id,
        # The catalog plan, not the provider's: after the equality check the
        # two agree, and where they could disagree the server's data wins.
        plan=catalog_plan,
        status=verified.status,
        effective_start=verified.current_period_start,
        effective_end=verified.current_period_end,
        occurred_at=verified.occurred_at,
        cancel_at_period_end=verified.cancel_at_period_end,
    )
    await apply_provider_event(db, event)
    state = await resolve_state(db, user_id)
    event_name = "restored" if kind is VerifyKind.RESTORE else "verified"
    _log(
        event_name,
        provider=provider.value,
        status=verified.status.value,
        plan=state.plan.value,
        kind=kind.value,
    )
    _count("verified", provider.value)
    return state


def _resolve_provider(provider_name: str) -> SubscriptionProvider:
    try:
        provider = SubscriptionProvider(provider_name.strip())
    except ValueError:
        _log("rejected", provider="unknown", kind="unknown-provider")
        _count("rejected", "unknown")
        raise EntitlementError("UNKNOWN_PROVIDER", "Unknown purchase provider.", 422) from None
    if provider is SubscriptionProvider.MANUAL:
        # MANUAL marks operational/test records with no billing vendor behind
        # them. A purchase credential "from manual" claims a vendor that does
        # not exist, so it is rejected, not routed.
        _log("rejected", provider=provider.value, kind="unknown-provider")
        _count("rejected", provider.value)
        raise EntitlementError("UNKNOWN_PROVIDER", "Unknown purchase provider.", 422)
    return provider


def _require_product(product_id: str) -> Plan:
    plan = plan_for_product(product_id.strip())
    if plan is None:
        _log("rejected", provider="unknown", kind="unknown-product")
        _count("rejected", "unknown")
        raise EntitlementError("UNKNOWN_PRODUCT", "Unknown product.", 422)
    return plan


async def _verify(
    adapter: SubscriptionProviderProtocol, purchase_token: str, user_id: uuid.UUID
) -> VerifiedPurchase:
    """Run provider verification with a timeout and failure discipline.

    The caller is passed as `expected_user_id` so the adapter can enforce
    its documented binding duty; the service re-checks the returned binding
    in _bind_ownership, because a confused adapter must not be able to
    attach a purchase to whoever asked.

    Domain errors raised by the adapter (invalid, expired-as-rejection, and
    friends) pass through untouched — they are the provider's verdict, and
    rewriting them would editorialize a refusal. Anything else (crash,
    malformed response, hang) becomes a 503/504 without leaking internals:
    a provider failure must never grant, and never explain itself with a
    traceback.
    """
    try:
        async with asyncio.timeout(PURCHASE_VERIFY_TIMEOUT_S):
            return await adapter.verify_purchase(
                purchase_token=purchase_token, expected_user_id=user_id
            )
    except EntitlementError:
        raise
    except TimeoutError as exc:
        _log("error", provider=adapter.name.value, kind="timeout")
        _count("error", adapter.name.value)
        raise EntitlementError("PROVIDER_TIMEOUT", "The purchase provider timed out.", 504) from exc
    except Exception as exc:
        # A deliberately broad catch: provider adapters are third-party-shaped
        # code, and any failure mode they invent must become an opaque 503 —
        # never a grant, never a traceback in the response.
        _log(
            "error",
            provider=adapter.name.value,
            kind="failure",
            error_type=type(exc).__name__,
        )
        _count("error", adapter.name.value)
        raise EntitlementError("PROVIDER_ERROR", "The purchase provider failed.", 503) from exc


def _bind_ownership(*, verified_user_id: uuid.UUID, user_id: uuid.UUID) -> None:
    # A store transaction id alone is not ownership: whoever holds the token
    # string could submit it. The provider binds the credential to a rider;
    # the service requires that rider to be the caller. A mismatch is a
    # conflict, not a validation error — the purchase is real, it is just
    # not yours.
    if verified_user_id != user_id:
        _log("rejected", provider="unknown", kind="cross-user")
        _count("rejected", "unknown")
        raise EntitlementError(
            "CROSS_USER_PURCHASE",
            "This purchase belongs to another rider.",
            409,
        )


def _bind_product(
    *, requested: str, verified_product: str | None, verified_plan: Plan, catalog_plan: Plan
) -> None:
    if verified_product is None or verified_product.strip() != requested.strip():
        _log("rejected", provider="unknown", kind="product-mismatch")
        _count("rejected", "unknown")
        raise EntitlementError(
            "PRODUCT_MISMATCH",
            "This purchase is for a different product.",
            422,
        )
    if verified_plan is not catalog_plan:
        _log("rejected", provider="unknown", kind="plan-mismatch")
        _count("rejected", "unknown")
        raise EntitlementError(
            "PRODUCT_PLAN_MISMATCH",
            "The provider and catalog disagree about this product.",
            422,
        )


def _gate_environment(environment: VerificationEnvironment) -> None:
    if environment is VerificationEnvironment.UNKNOWN:
        # A provider that cannot say where a purchase lives cannot have it
        # trusted anywhere. Fail closed rather than guessing sandbox.
        _log("rejected", provider="unknown", kind="unknown-environment")
        _count("rejected", "unknown")
        raise EntitlementError(
            "ENVIRONMENT_UNKNOWN",
            "The purchase environment could not be determined.",
            422,
        )
    if environment is VerificationEnvironment.SANDBOX and settings.is_production:
        # The one direction that matters: a test purchase must never
        # silently become a production entitlement. Loud rejection, no state
        # change.
        _log("rejected", provider="unknown", kind="sandbox-on-production")
        _count("rejected", "unknown")
        raise EntitlementError(
            "ENVIRONMENT_MISMATCH",
            "A test purchase cannot grant production access.",
            422,
        )

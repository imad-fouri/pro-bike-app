"""Provider-neutral subscription interface for Phase 10 WS-S.

This module defines the contract a future billing provider must satisfy. It
contains no App Store implementation, no Play implementation, no Stripe code, no
purchase-token storage, and no webhook endpoint. A fake provider may implement
this protocol inside tests, but that fake is explicitly a contract test: it
proves the application handles verified, failed, expired, revoked, duplicate,
and out-of-order provider results without proving anything about Apple or
Google.

The three responsibilities mirror what every store or billing integration must
eventually supply:

* ``verify_purchase`` turns an opaque client purchase credential into a bounded
  server-side verification result. The credential itself is never persisted.
* ``get_subscription`` returns the provider's current view of one external
  subscription.
* ``process_event`` turns an authenticated provider callback into a normalized
  internal event. No production callback path calls it yet; when one does, it
  must verify the provider signature before parsing.
"""

import enum
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from app.models.subscription import Plan, SubscriptionProvider, SubscriptionStatus
from app.services.subscription_service import ProviderSubscriptionEvent


class VerificationEnvironment(str, enum.Enum):
    """Where the provider says the purchase lives.

    Both Apple and Google expose a sandbox/production distinction; this enum
    is the provider-neutral form of it. The value always comes FROM a
    verification response — never from a client request — which is what makes
    the sandbox-on-production rejection meaningful rather than theatrical.
    """

    SANDBOX = "sandbox"
    PRODUCTION = "production"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class VerifiedPurchase:
    """The bounded result of checking one purchase credential.

    The credential used for verification is deliberately absent. Keeping it in
    the result would create a second copy of a secret that already exists in
    the request, and the request is the only place it is allowed to exist.
    """

    provider: SubscriptionProvider
    provider_subscription_id: str
    user_id: uuid.UUID
    plan: Plan
    status: SubscriptionStatus
    current_period_start: datetime
    current_period_end: datetime
    occurred_at: datetime
    provider_event_id: str
    # Phase 10 WS-PV additions. All defaulted, so existing constructors are
    # unaffected. `product_id` binds the verification to the catalog entry
    # the caller claimed; `environment` gates sandbox purchases off
    # production; `cancel_at_period_end` carries end-of-period cancellation
    # through reconciliation.
    product_id: str | None = None
    environment: VerificationEnvironment = VerificationEnvironment.UNKNOWN
    cancel_at_period_end: bool = False


@dataclass(frozen=True)
class ProviderSubscriptionView:
    """The provider's current view of one external subscription."""

    provider: SubscriptionProvider
    provider_subscription_id: str
    user_id: uuid.UUID
    plan: Plan
    status: SubscriptionStatus
    current_period_start: datetime
    current_period_end: datetime
    cancel_at_period_end: bool


class SubscriptionProviderProtocol(Protocol):
    """Contract for a future billing-provider adapter.

    A provider adapter is the only code allowed to interpret provider-specific
    product identifiers, receipts, and callbacks. Everything downstream works
    with :class:`ProviderSubscriptionEvent`, so Apple and Google never leak
    their vocabulary into authorization decisions.
    """

    @property
    def name(self) -> SubscriptionProvider:
        """The billing origin this adapter speaks for."""
        ...

    async def verify_purchase(
        self, *, purchase_token: str, expected_user_id: uuid.UUID
    ) -> VerifiedPurchase:
        """Verify one opaque purchase credential for one rider.

        Implementations must reject a credential belonging to another rider
        rather than attaching it to the caller. The credential must not be
        persisted, logged, or returned.
        """
        ...

    async def get_subscription(self, *, provider_subscription_id: str) -> ProviderSubscriptionView:
        """Return the provider's authoritative view of one subscription."""
        ...

    async def process_event(
        self, *, payload: Mapping[str, object], signature: str | None
    ) -> ProviderSubscriptionEvent:
        """Convert an authenticated provider callback into a normalized event.

        A missing, malformed, or unverifiable signature must fail before any
        provider-specific field is trusted. This method is the parsing contract
        only; the production HTTP callback that will call it does not exist yet.
        """
        ...

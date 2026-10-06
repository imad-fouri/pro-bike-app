"""Server-authoritative subscription and entitlement decisions.

The backend owns the answer to "may this rider use this feature". The client
may cache that answer for display, but it cannot grant access: every premium
route re-resolves the caller's rows from PostgreSQL at request time.

Two distinctions shape this module:

* A subscription is commercial state. It says who paid whom, through which
  origin, for which period. It never directly opens a feature.
* An entitlement is authorization state. It says this rider may use this
  capability from this instant until that instant. It is the only thing a route
  consults.

That indirection is what lets a future provider integration change billing rules
without touching authorization. It is also why revocation, expiration, and
future-dated grants are all properties of entitlement rows: the commercial
record can say "canceled", while the authorization rows say exactly when the
rider stops being able to use the feature.

No public API in this workstream creates, updates, or deletes these rows. Tests
and controlled operations apply provider-shaped events through
:func:`apply_provider_event`; ordinary clients only read their own resolved
state.
"""

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import redact
from app.models.subscription import (
    Entitlement,
    EntitlementSource,
    EntitlementStatus,
    Feature,
    Plan,
    Subscription,
    SubscriptionProvider,
    SubscriptionStatus,
)

log = logging.getLogger("cyclecoach")


def _now() -> datetime:
    return datetime.now(UTC)


#: Premium capabilities and the plan each one requires. Every value is
#: currently PRO. The mapping exists so a future capability can require a
#: different plan without rewriting route guards.
FEATURE_PLANS: dict[Feature, Plan] = {
    Feature.AI_COACH: Plan.PRO,
    Feature.ADVANCED_TRAINING: Plan.PRO,
    Feature.ADVANCED_ANALYTICS: Plan.PRO,
    Feature.ADVANCED_ROUTES: Plan.PRO,
    Feature.NO_ADS: Plan.PRO,
}

#: Capabilities an authenticated rider has without any stored grant. This is
#: the explicit Free model: Free is a defined product state, not merely the
#: absence of PRO rows.
FREE_CAPABILITIES: tuple[str, ...] = (
    "core_ride_recording",
    "core_routes",
    "core_training",
    "limited_ai_status",
)

_GRANTING_STATUSES = {SubscriptionStatus.ACTIVE, SubscriptionStatus.TRIALING}


class EntitlementError(Exception):
    def __init__(self, code: str, message: str, status: int = 403) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


@dataclass(frozen=True)
class ProviderSubscriptionEvent:
    """One normalized provider event, already authenticated upstream.

    ``provider_subscription_id`` and ``provider_event_id`` are stable external
    identifiers used for idempotency and ordering. They are not secrets, but
    they are also not needed for operations, so they are stored without being
    logged or exposed through the client API.
    """

    provider: SubscriptionProvider
    provider_subscription_id: str
    provider_event_id: str
    user_id: uuid.UUID
    plan: Plan
    status: SubscriptionStatus
    effective_start: datetime
    effective_end: datetime
    occurred_at: datetime
    cancel_at_period_end: bool = False

    def __post_init__(self) -> None:
        _require_text(self.provider_subscription_id, "provider_subscription_id")
        _require_text(self.provider_event_id, "provider_event_id")
        _require_aware(self.effective_start, "effective_start")
        _require_aware(self.effective_end, "effective_end")
        _require_aware(self.occurred_at, "occurred_at")
        if self.plan is not Plan.PRO:
            # The only paid product in this foundation is PRO. Accepting a
            # FREE commercial subscription would invent a product state with
            # no billing meaning and no entitlement mapping.
            raise EntitlementError(
                "SUBSCRIPTION_PLAN_UNSUPPORTED",
                "Only the Pro plan can create a subscription.",
                422,
            )
        if self.effective_start >= self.effective_end:
            raise EntitlementError(
                "SUBSCRIPTION_PERIOD_INVALID",
                "The effective period must end after it starts.",
                422,
            )
        if self.cancel_at_period_end and self.status is not SubscriptionStatus.CANCELED:
            raise EntitlementError(
                "SUBSCRIPTION_EVENT_INVALID",
                "Only a canceled subscription can end at its period end.",
                422,
            )


@dataclass(frozen=True)
class ResolvedEntitlement:
    feature: Feature
    status: EntitlementStatus
    source: EntitlementSource
    starts_at: datetime
    expires_at: datetime
    effective: bool


@dataclass(frozen=True)
class EntitlementState:
    plan: Plan
    entitlements: tuple[ResolvedEntitlement, ...]
    evaluated_at: datetime


def _require_text(value: str, name: str, limit: int = 128) -> str:
    text = value.strip()
    if not text or len(text) > limit:
        raise EntitlementError(
            "SUBSCRIPTION_EVENT_INVALID",
            f"{name} must be 1-{limit} characters.",
            422,
        )
    return text


def _require_aware(value: datetime, name: str) -> datetime:
    if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
        # Naive timestamps make expiration depend on the writer's local zone.
        # Authorization boundaries must not depend on that accident.
        raise EntitlementError(
            "SUBSCRIPTION_EVENT_INVALID",
            f"{name} must be timezone-aware.",
            422,
        )
    return value


def _is_effective(row: Entitlement, now: datetime) -> bool:
    return row.status is EntitlementStatus.ACTIVE and row.starts_at <= now < row.expires_at


def _required_plan(feature: Feature) -> Plan:
    return FEATURE_PLANS[feature]


def _log(event: str, **fields: object) -> None:
    """Operational events only: bounded enums and counts, never identifiers."""
    log.info(f"subscription.{event}", extra=redact(dict(fields)))


def _count_subscription_event(event: str, provider: str, status: str) -> None:
    try:
        from app.core.metrics import record_subscription_event

        record_subscription_event(event=event, provider=provider, status=status)
    except Exception as exc:  # noqa: BLE001 - metrics must not fail billing state
        log.warning("metrics_record_failed", extra={"error_type": type(exc).__name__})


def _count_check(feature: str, outcome: str, plan: str) -> None:
    try:
        from app.core.metrics import record_entitlement_check

        record_entitlement_check(feature=feature, outcome=outcome, plan=plan)
    except Exception as exc:  # noqa: BLE001 - metrics must not fail authorization
        log.warning("metrics_record_failed", extra={"error_type": type(exc).__name__})


def _count_denial(feature: str, plan: str) -> None:
    try:
        from app.core.metrics import record_entitlement_denial

        record_entitlement_denial(feature=feature, plan=plan)
    except Exception as exc:  # noqa: BLE001 - metrics must not fail authorization
        log.warning("metrics_record_failed", extra={"error_type": type(exc).__name__})


async def _lock_subscription(db: AsyncSession, event: ProviderSubscriptionEvent) -> None:
    """Serialize all mutations for one external subscription.

    A retried provider callback and a renewal for the same external id must
    not interleave their read-modify-write sequences. One advisory lock per
    transaction is the same pattern used for social pairs, teams, and chats.
    """
    key = f"subscription:{event.provider.value}:{event.provider_subscription_id}"
    await db.execute(select(func.pg_advisory_xact_lock(func.hashtext(key))))


async def _existing_subscription(
    db: AsyncSession, event: ProviderSubscriptionEvent
) -> Subscription | None:
    res = await db.execute(
        select(Subscription).where(
            Subscription.provider == event.provider,
            Subscription.provider_subscription_id == event.provider_subscription_id,
        )
    )
    return res.scalar_one_or_none()


def _stale(existing: Subscription, event: ProviderSubscriptionEvent) -> bool:
    """Whether an event is older than the state already stored.

    Provider callbacks can arrive out of order. An older cancellation must not
    undo a newer renewal, and an older renewal must not resurrect a newer
    cancellation. Equal timestamps are tie-broken by event id so the decision
    is deterministic rather than arrival-order dependent.
    """
    last_at = existing.last_provider_event_at
    if last_at is None:
        return False
    if event.occurred_at != last_at:
        return event.occurred_at < last_at
    return event.provider_event_id < (existing.last_provider_event_id or "")


async def apply_provider_event(db: AsyncSession, event: ProviderSubscriptionEvent) -> Subscription:
    """Apply one authenticated provider event idempotently.

    The same event applied twice returns the same subscription without writing
    duplicate rows. An older event applied after a newer one is ignored. A
    newer event replaces the subscription-backed entitlement rows for the same
    external subscription in the same transaction.
    """
    await _lock_subscription(db, event)
    subscription = await _existing_subscription(db, event)
    if subscription is not None:
        if subscription.user_id != event.user_id:
            # The external id is stable across providers. Moving it between
            # riders would transfer paid access, so a mismatch is a conflict
            # rather than an update.
            raise EntitlementError(
                "SUBSCRIPTION_USER_MISMATCH",
                "This subscription belongs to another rider.",
                409,
            )
        if subscription.last_provider_event_id == event.provider_event_id:
            _log(
                "event_duplicate_ignored",
                provider=event.provider.value,
                status=event.status.value,
            )
            _count_subscription_event("duplicate_ignored", event.provider.value, event.status.value)
            # Detach before ending the transaction. A rollback expires attached
            # instances, and the caller legitimately keeps using the returned
            # subscription after this session closes.
            db.expunge(subscription)
            await db.rollback()
            return subscription
        if _stale(subscription, event):
            _log(
                "event_out_of_order_ignored",
                provider=event.provider.value,
                status=event.status.value,
            )
            _count_subscription_event(
                "out_of_order_ignored", event.provider.value, event.status.value
            )
            db.expunge(subscription)
            await db.rollback()
            return subscription
        subscription.status = event.status
        subscription.current_period_start = event.effective_start
        subscription.current_period_end = event.effective_end
        subscription.cancel_at_period_end = event.cancel_at_period_end
        subscription.last_provider_event_id = event.provider_event_id
        subscription.last_provider_event_at = event.occurred_at
        subscription.updated_at = event.occurred_at
    else:
        now = _now()
        subscription = Subscription(
            user_id=event.user_id,
            provider=event.provider,
            provider_subscription_id=event.provider_subscription_id,
            plan=event.plan,
            status=event.status,
            started_at=event.effective_start,
            current_period_start=event.effective_start,
            current_period_end=event.effective_end,
            cancel_at_period_end=event.cancel_at_period_end,
            last_provider_event_id=event.provider_event_id,
            last_provider_event_at=event.occurred_at,
            created_at=now,
            updated_at=event.occurred_at,
        )
        db.add(subscription)
        await db.flush()

    await _apply_subscription_entitlements(db, subscription, event)
    await db.commit()
    await db.refresh(subscription)
    _log(
        "event_applied",
        provider=event.provider.value,
        status=event.status.value,
        plan=event.plan.value,
    )
    _count_subscription_event("applied", event.provider.value, event.status.value)
    return subscription


async def _apply_subscription_entitlements(
    db: AsyncSession, subscription: Subscription, event: ProviderSubscriptionEvent
) -> None:
    """Reconcile authorization rows with one commercial event.

    Only rows linked to this subscription are touched. Manual grants are a
    separate authorization story and must survive a subscription cancellation.
    """
    res = await db.execute(
        select(Entitlement).where(
            Entitlement.user_id == subscription.user_id,
            Entitlement.source_subscription_id == subscription.id,
        )
    )
    rows = {row.feature: row for row in res.scalars()}

    if event.status in _GRANTING_STATUSES:
        for feature in FEATURE_PLANS:
            row = rows.get(feature)
            if row is None:
                db.add(
                    Entitlement(
                        user_id=subscription.user_id,
                        feature=feature,
                        status=EntitlementStatus.ACTIVE,
                        source=EntitlementSource.SUBSCRIPTION,
                        source_subscription_id=subscription.id,
                        starts_at=event.effective_start,
                        expires_at=event.effective_end,
                        created_at=event.occurred_at,
                        updated_at=event.occurred_at,
                    )
                )
            else:
                # A newer payment, trial, or restoration replaces the row. The
                # subscription's event fields retain the commercial history, so
                # the authorization row holds only the current effective grant.
                row.status = EntitlementStatus.ACTIVE
                row.starts_at = event.effective_start
                row.expires_at = event.effective_end
                row.updated_at = event.occurred_at
        await db.flush()
        return

    if event.status is SubscriptionStatus.PAST_DUE or (
        event.status is SubscriptionStatus.CANCELED and event.cancel_at_period_end
    ):
        # Dunning and end-of-period cancellation share one rule: the rider keeps
        # what the current period already granted, and nothing is extended.
        # Revoked rows stay revoked; cancellation does not un-revoke.
        for row in rows.values():
            if row.status is not EntitlementStatus.REVOKED:
                row.expires_at = min(row.expires_at, event.effective_end)
                row.updated_at = event.occurred_at
        await db.flush()
        return

    for row in rows.values():
        if row.status is EntitlementStatus.REVOKED and event.status is not (
            SubscriptionStatus.REVOKED
        ):
            # Terminal means terminal within one event lineage: only another
            # revocation touches the marker. A newer grant recreates access
            # through the granting path above instead.
            row.updated_at = event.occurred_at
        elif event.status is SubscriptionStatus.REVOKED:
            row.status = EntitlementStatus.REVOKED
            row.updated_at = event.occurred_at
        else:
            row.status = EntitlementStatus.INACTIVE
            row.updated_at = event.occurred_at
    await db.flush()


async def resolve_state(
    db: AsyncSession, user_id: uuid.UUID, *, now: datetime | None = None
) -> EntitlementState:
    """Resolve one rider's current product state from their own rows only."""
    moment = now or _now()
    _require_aware(moment, "now")
    res = await db.execute(select(Entitlement).where(Entitlement.user_id == user_id))
    resolved = tuple(
        ResolvedEntitlement(
            feature=row.feature,
            status=row.status,
            source=row.source,
            starts_at=row.starts_at,
            expires_at=row.expires_at,
            effective=_is_effective(row, moment),
        )
        for row in sorted(res.scalars(), key=lambda r: r.feature.value)
    )
    plan = (
        Plan.PRO
        if any(item.feature in FEATURE_PLANS and item.effective for item in resolved)
        else Plan.FREE
    )
    return EntitlementState(plan=plan, entitlements=resolved, evaluated_at=moment)


async def has_feature(
    db: AsyncSession,
    user_id: uuid.UUID,
    feature: Feature,
    *,
    now: datetime | None = None,
) -> bool:
    """Whether one rider may use one feature right now."""
    moment = now or _now()
    _require_aware(moment, "now")
    res = await db.execute(
        select(Entitlement.id).where(
            Entitlement.user_id == user_id,
            Entitlement.feature == feature,
            Entitlement.status == EntitlementStatus.ACTIVE,
            Entitlement.starts_at <= moment,
            Entitlement.expires_at > moment,
        )
    )
    return res.scalar_one_or_none() is not None


async def evaluate_feature(
    db: AsyncSession,
    user_id: uuid.UUID,
    feature: Feature,
    *,
    now: datetime | None = None,
) -> tuple[bool, EntitlementState]:
    """Evaluate one capability and record the authorization outcome.

    The returned state is the rider's own resolved state. Checks and denials
    are counted with bounded labels only: feature, outcome, and plan. No user,
    subscription, provider, or event identifier is ever a metric label.
    """
    state = await resolve_state(db, user_id, now=now)
    allowed = any(item.feature is feature and item.effective for item in state.entitlements)
    _count_check(feature.value, "allowed" if allowed else "denied", state.plan.value)
    if not allowed:
        _log("check_denied", feature=feature.value, plan=state.plan.value)
        _count_denial(feature.value, state.plan.value)
    return allowed, state


def denial_detail(feature: Feature) -> dict[str, str]:
    """Stable denial body for premium routes.

    The caller learns which capability is missing and which plan supplies it.
    Subscription status, provider, external identifiers, and other riders are
    never included.
    """
    return {
        "code": "ENTITLEMENT_REQUIRED",
        "message": "This feature requires CycleCoach Pro.",
        "feature": feature.value,
        "required_plan": _required_plan(feature).value,
    }

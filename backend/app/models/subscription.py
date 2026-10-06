"""Phase 10 WS-S domain: commercial subscriptions and product entitlements.

A subscription is the commercial relationship with a billing origin. An
entitlement is the server-authoritative permission to use a product capability.
Features never consult billing-provider terminology directly; a future provider
adapter normalizes an external product or purchase into the internal
``Plan``/``Feature`` vocabulary used here.

What this module deliberately does not contain:

* No payment credentials, card data, purchase tokens, provider secrets, or
  access tokens. Provider-issued identifiers are allowed only where they are
  needed for idempotency and reconciliation.
* No Stripe, App Store, or Play billing integration. ``MANUAL`` means an
  operational/test record with no billing vendor behind it; it is not a claim
  that a purchase happened.
* No public mutation path. Rows are created or changed by provider-event
  ingestion and controlled operations, never by ordinary client requests.
"""

import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    String,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.user import _uuid, _values


class Plan(str, enum.Enum):
    """The product plan presented to the client."""

    FREE = "free"
    PRO = "pro"


class Feature(str, enum.Enum):
    """Stable machine-readable product capabilities.

    These identifiers are authorization vocabulary, not display copy. The
    client translates them for display; the server never accepts a translated
    string as an authorization identifier.
    """

    AI_COACH = "ai_coach"
    ADVANCED_TRAINING = "advanced_training"
    ADVANCED_ANALYTICS = "advanced_analytics"
    ADVANCED_ROUTES = "advanced_routes"
    NO_ADS = "no_ads"


class SubscriptionProvider(str, enum.Enum):
    """Origin of a commercial subscription record.

    ``MANUAL`` is intentionally not a billing vendor. It marks an operational or
    test record created without any provider purchase behind it, so a test or a
    future controlled operation cannot be mistaken for App Store or Play
    revenue. Stripe is absent because web billing is deferred.
    """

    MANUAL = "manual"
    APP_STORE = "app_store"
    PLAY_STORE = "play_store"


class SubscriptionStatus(str, enum.Enum):
    """Commercial lifecycle states with product meaning.

    Every value changes entitlement reconciliation in a documented way:

    * ``ACTIVE`` and ``TRIALING`` confer the subscription's premium features
      while their effective period is current.
    * ``PAST_DUE`` preserves existing entitlements through the current period.
      It is a dunning grace, not continued service without a boundary.
    * ``CANCELED`` preserves entitlements through the current period only when
      ``cancel_at_period_end`` is true; otherwise it ends them immediately.
    * ``EXPIRED`` and ``REVOKED`` never confer access. ``REVOKED`` is terminal
      for the affected rows until a newer authoritative event replaces them.
    """

    ACTIVE = "active"
    TRIALING = "trialing"
    PAST_DUE = "past_due"
    CANCELED = "canceled"
    EXPIRED = "expired"
    REVOKED = "revoked"


class EntitlementStatus(str, enum.Enum):
    """Row lifecycle for one product capability.

    Expiration is derived from ``starts_at``/``expires_at`` rather than stored
    as a separate state. Storing both a status and an overlapping expiration
    state would create two sources of truth about the same fact.
    """

    ACTIVE = "active"
    INACTIVE = "inactive"
    REVOKED = "revoked"


class EntitlementSource(str, enum.Enum):
    """How an entitlement row came into existence."""

    SUBSCRIPTION = "subscription"
    MANUAL = "manual"


class Subscription(Base):
    """One commercial subscription for one rider."""

    __tablename__ = "subscriptions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    provider: Mapped[SubscriptionProvider] = mapped_column(
        Enum(SubscriptionProvider, name="subscription_provider", values_callable=_values),
        nullable=False,
    )
    provider_subscription_id: Mapped[str | None] = mapped_column(String(128))
    plan: Mapped[Plan] = mapped_column(
        Enum(Plan, name="subscription_plan", values_callable=_values), nullable=False
    )
    status: Mapped[SubscriptionStatus] = mapped_column(
        Enum(SubscriptionStatus, name="subscription_status", values_callable=_values),
        nullable=False,
    )
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    current_period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    current_period_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    cancel_at_period_end: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    last_provider_event_id: Mapped[str | None] = mapped_column(String(128))
    last_provider_event_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        # A future paid plan beyond PRO would need a migration. Today the only
        # paid product is PRO, and a subscription row asserting another plan
        # would be a meaningless state.
        CheckConstraint("plan = 'pro'", name="ck_subscriptions_pro_plan"),
        CheckConstraint(
            "current_period_start < current_period_end",
            name="ck_subscriptions_period_order",
        ),
        CheckConstraint(
            "length(provider_subscription_id) > 0 OR provider_subscription_id IS NULL",
            name="ck_subscriptions_provider_subscription_id",
        ),
        CheckConstraint(
            "length(last_provider_event_id) > 0 OR last_provider_event_id IS NULL",
            name="ck_subscriptions_provider_event_id",
        ),
        # Provider-backed rows need a stable external identity for idempotent
        # event application. Manual rows intentionally have no such identity.
        Index(
            "uq_subscriptions_provider_external",
            "provider",
            "provider_subscription_id",
            unique=True,
            postgresql_where=text("provider_subscription_id IS NOT NULL"),
        ),
        Index("ix_subscriptions_user_status", "user_id", "status"),
    )


class Entitlement(Base):
    """One product capability grant for one rider."""

    __tablename__ = "entitlements"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    feature: Mapped[Feature] = mapped_column(
        Enum(Feature, name="entitlement_feature", values_callable=_values), nullable=False
    )
    status: Mapped[EntitlementStatus] = mapped_column(
        Enum(EntitlementStatus, name="entitlement_status", values_callable=_values),
        nullable=False,
    )
    source: Mapped[EntitlementSource] = mapped_column(
        Enum(EntitlementSource, name="entitlement_source", values_callable=_values),
        nullable=False,
    )
    source_subscription_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("subscriptions.id", ondelete="CASCADE"),
    )
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        # A subscription grant and a manual grant are different authorization
        # stories. The linkage must match the story: subscription rows point at
        # the commercial record, manual rows point at nothing.
        CheckConstraint(
            "(source = 'subscription' AND source_subscription_id IS NOT NULL) OR "
            "(source = 'manual' AND source_subscription_id IS NULL)",
            name="ck_entitlements_source_link",
        ),
        CheckConstraint("starts_at < expires_at", name="ck_entitlements_window_order"),
        # One effective row per subscription-backed capability. Renewals update
        # the row rather than appending history; the subscription event fields
        # preserve the commercial audit trail.
        Index(
            "uq_entitlements_subscription_feature",
            "source_subscription_id",
            "feature",
            unique=True,
            postgresql_where=text("source_subscription_id IS NOT NULL"),
        ),
        # Manual grants have no commercial record to key on, so the user and
        # feature are the identity.
        Index(
            "uq_entitlements_manual_feature",
            "user_id",
            "feature",
            unique=True,
            postgresql_where=text("source_subscription_id IS NULL"),
        ),
        Index("ix_entitlements_user_feature_status", "user_id", "feature", "status"),
    )

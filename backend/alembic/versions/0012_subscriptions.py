"""Phase 10 WS-S migration: commercial subscriptions and product entitlements.

Two additive tables on top of ``0011_group_rides``. No existing table is
modified, so WS-S cannot regress Phases 1-9 or WS-O by schema change.

A subscription is commercial state: who pays through which origin, for which
period, under which lifecycle status. An entitlement is authorization state:
which rider may use which capability, from which instant until which instant.
Routes consult entitlements only; they never branch on provider terminology.

Deliberately absent:

* No payment credentials, purchase tokens, provider secrets, or customer
  billing profile. Provider-issued identifiers exist only where idempotent
  event handling needs them.
* No Stripe value. Web billing is deferred, and naming it here would imply a
  supported integration.
* No mutation stored procedure or trigger. Current application code applies an
  authenticated event in one transaction; there is no public mutation route.

Idempotency and concurrency are storage facts, not comments:

* ``uq_subscriptions_provider_external`` makes provider plus external id the
  identity for provider-backed rows. Manual rows have no external id, so the
  predicate excludes them instead of forcing a fake key.
* One authorization row exists per subscription-backed capability, and one per
  manual capability. Re-application updates that row instead of appending a
  second live grant.
* Source linkage is CHECKed: subscription grants point at their commercial
  record, while manual grants point at nothing.
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0012_subscriptions"
down_revision = "0011_group_rides"

_provider = postgresql.ENUM(
    "manual", "app_store", "play_store", name="subscription_provider", create_type=False
)
_plan = postgresql.ENUM("free", "pro", name="subscription_plan", create_type=False)
_status = postgresql.ENUM(
    "active",
    "trialing",
    "past_due",
    "canceled",
    "expired",
    "revoked",
    name="subscription_status",
    create_type=False,
)
_feature = postgresql.ENUM(
    "ai_coach",
    "advanced_training",
    "advanced_analytics",
    "advanced_routes",
    "no_ads",
    name="entitlement_feature",
    create_type=False,
)
_entitlement_status = postgresql.ENUM(
    "active", "inactive", "revoked", name="entitlement_status", create_type=False
)
_source = postgresql.ENUM(
    "subscription", "manual", name="entitlement_source", create_type=False
)


def upgrade() -> None:
    _provider.create(op.get_bind(), checkfirst=True)
    _plan.create(op.get_bind(), checkfirst=True)
    _status.create(op.get_bind(), checkfirst=True)
    _feature.create(op.get_bind(), checkfirst=True)
    _entitlement_status.create(op.get_bind(), checkfirst=True)
    _source.create(op.get_bind(), checkfirst=True)

    # --- subscriptions ------------------------------------------------------
    op.create_table(
        "subscriptions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("provider", _provider, nullable=False),
        sa.Column("provider_subscription_id", sa.String(128)),
        sa.Column("plan", _plan, nullable=False),
        sa.Column("status", _status, nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("current_period_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("current_period_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "cancel_at_period_end", sa.Boolean, nullable=False, server_default=sa.false()
        ),
        sa.Column("last_provider_event_id", sa.String(128)),
        sa.Column("last_provider_event_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("plan = 'pro'", name="ck_subscriptions_pro_plan"),
        sa.CheckConstraint(
            "current_period_start < current_period_end",
            name="ck_subscriptions_period_order",
        ),
        sa.CheckConstraint(
            "length(provider_subscription_id) > 0 OR provider_subscription_id IS NULL",
            name="ck_subscriptions_provider_subscription_id",
        ),
        sa.CheckConstraint(
            "length(last_provider_event_id) > 0 OR last_provider_event_id IS NULL",
            name="ck_subscriptions_provider_event_id",
        ),
    )
    op.create_index("ix_subscriptions_user_id", "subscriptions", ["user_id"])
    op.create_index(
        "uq_subscriptions_provider_external",
        "subscriptions",
        ["provider", "provider_subscription_id"],
        unique=True,
        postgresql_where=sa.text("provider_subscription_id IS NOT NULL"),
    )
    op.create_index("ix_subscriptions_user_status", "subscriptions", ["user_id", "status"])

    # --- entitlements -------------------------------------------------------
    op.create_table(
        "entitlements",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("feature", _feature, nullable=False),
        sa.Column("status", _entitlement_status, nullable=False),
        sa.Column("source", _source, nullable=False),
        sa.Column(
            "source_subscription_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("subscriptions.id", ondelete="CASCADE"),
        ),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "(source = 'subscription' AND source_subscription_id IS NOT NULL) OR "
            "(source = 'manual' AND source_subscription_id IS NULL)",
            name="ck_entitlements_source_link",
        ),
        sa.CheckConstraint(
            "starts_at < expires_at", name="ck_entitlements_window_order"
        ),
    )
    op.create_index("ix_entitlements_user_id", "entitlements", ["user_id"])
    op.create_index(
        "ix_entitlements_source_subscription_id",
        "entitlements",
        ["source_subscription_id"],
    )
    op.create_index(
        "uq_entitlements_subscription_feature",
        "entitlements",
        ["source_subscription_id", "feature"],
        unique=True,
        postgresql_where=sa.text("source_subscription_id IS NOT NULL"),
    )
    op.create_index(
        "uq_entitlements_manual_feature",
        "entitlements",
        ["user_id", "feature"],
        unique=True,
        postgresql_where=sa.text("source_subscription_id IS NULL"),
    )
    op.create_index(
        "ix_entitlements_user_feature_status",
        "entitlements",
        ["user_id", "feature", "status"],
    )


def downgrade() -> None:
    op.drop_table("entitlements")
    op.drop_table("subscriptions")
    _source.drop(op.get_bind(), checkfirst=True)
    _entitlement_status.drop(op.get_bind(), checkfirst=True)
    _feature.drop(op.get_bind(), checkfirst=True)
    _status.drop(op.get_bind(), checkfirst=True)
    _plan.drop(op.get_bind(), checkfirst=True)
    _provider.drop(op.get_bind(), checkfirst=True)

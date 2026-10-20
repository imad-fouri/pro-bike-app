"""Phase 10 WS-RP widening: provider subscription ids can exceed 128 characters.

Google Play purchase tokens are opaque, unbounded strings, and the token IS the
stable external subscription identity the RTDN webhook and verification pipeline
reuse (docs/store-provider-audit.md §8.1). Truncating it would break idempotency
(the provider-external unique identity) and out-of-order reconciliation for a
Google subscription, so the column is widened rather than shortened.

App Store ``originalTransactionId`` values are short numeric strings and fit in
either width, so this is a pure widening: no row is touched, no constraint
changes shape (``ck_subscriptions_provider_subscription_id`` only requires
non-empty-or-NULL, and the partial unique index ``uq_subscriptions_provider_external``
is keyed on the same column and survives an ``ALTER TYPE``). A downgrade is the
same statement in reverse, safe because every existing value already fits in 128.
"""

import sqlalchemy as sa

from alembic import op

revision = "0015_store_webhooks"
down_revision = "0014_activity_integrity"


def upgrade() -> None:
    op.alter_column(
        "subscriptions",
        "provider_subscription_id",
        existing_type=sa.String(128),
        type_=sa.String(1024),
        existing_nullable=True,
    )


def downgrade() -> None:
    op.alter_column(
        "subscriptions",
        "provider_subscription_id",
        existing_type=sa.String(1024),
        type_=sa.String(128),
        existing_nullable=True,
    )

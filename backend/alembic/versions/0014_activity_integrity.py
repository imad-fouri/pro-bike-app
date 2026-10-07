"""Phase 11 WS-AC migration: the persisted integrity verdict on ``rides``.

Four columns added to the existing ``rides`` table. No new table, no change
to any other table, so the WS-AC surface cannot regress any earlier phase by
schema change.

Why persistence is genuinely required (docs/activity-integrity.md)
------------------------------------------------------------------
The verdict is Python logic over stored points - it cannot be a SQL read-time
predicate - and the "no silent re-evaluation" guarantee requires a stored,
versioned result. Writing the verdict once at finalisation is what makes the
eligibility predicate a stable, explainable column instead of an engine that
could answer differently on every read.

Columns
-------
``integrity_status``              the verdict (accepted/suspicious/rejected).
``integrity_calculation_version`` which rule-set produced it (``v1``).
``integrity_rules_triggered``     the rule ids that fired, as JSONB.
``integrity_evaluated_at``        when the verdict was written.

All four are NULLABLE and ``None`` means "not yet evaluated". The eligibility
predicate in ``activity_integrity`` treats ``None`` as NOT eligible (fail
closed), so an in-progress ride - or a completed ride that somehow never ran
the engine - can never silently appear in competition.

Backfill (provenance, not recalculation)
----------------------------------------
Rides already completed before this migration are the rides WS-RC treated as
eligible (the server-finalised them from server-accepted points). The UPDATE
labels them ``accepted`` at version ``v1`` with their own ``updated_at`` as the
evaluation timestamp. It is a provenance stamp - no points are re-judged, no
summary is recomputed, nothing historical is re-evaluated - so it is not a
broad historical backfill in the sense WS-AC rules out. ``integrity_status``
for in-progress rides stays NULL.
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0014_activity_integrity"
down_revision = "0013_rankings_challenges"

# ``postgresql.ENUM`` attached to an ``op.create_table`` column is created by
# the table statement, but ``op.add_column`` on an existing table is not: the
# emitted DDL only references the type, so it must exist first. Created here
# explicitly, and dropped in ``downgrade`` after the columns are gone.
_integrity_status = postgresql.ENUM("accepted", "suspicious", "rejected", name="integrity_status")

_LEGACY_VERSION = "v1"


def upgrade() -> None:
    _integrity_status.create(op.get_bind(), checkfirst=True)
    op.add_column(
        "rides",
        sa.Column("integrity_status", _integrity_status, nullable=True),
    )
    op.add_column(
        "rides",
        sa.Column("integrity_calculation_version", sa.String(32), nullable=True),
    )
    op.add_column(
        "rides",
        sa.Column("integrity_rules_triggered", postgresql.JSONB(), nullable=True),
    )
    op.add_column(
        "rides",
        sa.Column("integrity_evaluated_at", sa.DateTime(timezone=True), nullable=True),
    )

    # Provenance stamp for rides the server itself finalised before WS-AC
    # (WS-RC treated every such ride as eligible). Not a re-evaluation: no
    # points are re-judged and no summary is recomputed.
    op.execute(
        "UPDATE rides SET integrity_status = 'accepted', "
        f"integrity_calculation_version = '{_LEGACY_VERSION}', "
        "integrity_rules_triggered = '[]'::jsonb, "
        "integrity_evaluated_at = updated_at "
        "WHERE status = 'completed' AND integrity_status IS NULL"
    )


def downgrade() -> None:
    op.drop_column("rides", "integrity_evaluated_at")
    op.drop_column("rides", "integrity_rules_triggered")
    op.drop_column("rides", "integrity_calculation_version")
    op.drop_column("rides", "integrity_status")
    _integrity_status.drop(op.get_bind(), checkfirst=True)

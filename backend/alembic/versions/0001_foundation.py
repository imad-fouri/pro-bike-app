"""Phase 1: minimal foundation migration — proves tooling, no domain tables."""

revision = "0001_foundation"
down_revision = None


def upgrade() -> None:
    pass  # No tables in Phase 1 by design.


def downgrade() -> None:
    pass

"""Phase 3: bikes table + owner/status/category indexes."""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0003_bikes"
down_revision = "0002_auth"

_bike_category = postgresql.ENUM(
    "road",
    "gravel",
    "mountain_bike",
    "cyclocross",
    "endurance",
    "time_trial",
    "track",
    "bmx",
    "touring",
    "bikepacking",
    "e_bike",
    "commuting",
    "other",
    name="bike_category",
)
_bike_status = postgresql.ENUM("active", "archived", name="bike_status")


def upgrade() -> None:
    op.create_table(
        "bikes",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "owner_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(80), nullable=False),
        sa.Column("category", _bike_category, nullable=False),
        sa.Column("brand", sa.String(120)),
        sa.Column("model", sa.String(120)),
        sa.Column("model_year", sa.Integer),
        sa.Column("frame_size", sa.String(32)),
        sa.Column("weight_kg", sa.Numeric(5, 2)),
        sa.Column("notes", sa.Text),
        sa.Column("image_ref", sa.String(512)),
        sa.Column("status", _bike_status, nullable=False, server_default="active"),
        sa.Column("initial_distance_km", sa.Numeric(10, 2), nullable=False, server_default="0"),
        sa.Column("version", sa.Integer, nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "model_year IS NULL OR (model_year >= 1900 AND model_year <= 2100)",
            name="ck_bikes_year",
        ),
        sa.CheckConstraint(
            "weight_kg IS NULL OR (weight_kg > 0 AND weight_kg <= 200)",
            name="ck_bikes_weight",
        ),
        sa.CheckConstraint(
            "initial_distance_km >= 0 AND initial_distance_km <= 1000000",
            name="ck_bikes_distance",
        ),
    )
    op.create_index("ix_bikes_owner", "bikes", ["owner_id"])
    op.create_index("ix_bikes_owner_status", "bikes", ["owner_id", "status"])
    op.create_index("ix_bikes_owner_category", "bikes", ["owner_id", "category"])


def downgrade() -> None:
    op.drop_table("bikes")
    _bike_status.drop(op.get_bind(), checkfirst=True)
    _bike_category.drop(op.get_bind(), checkfirst=True)

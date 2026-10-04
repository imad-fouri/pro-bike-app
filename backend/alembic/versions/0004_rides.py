"""Phase 4: rides + ride_points (idempotency keys, RESTRICT bike FK)."""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0004_rides"
down_revision = "0003_bikes"

_ride_status = postgresql.ENUM("recording", "paused", "completed", "discarded", name="ride_status")


def upgrade() -> None:
    op.create_table(
        "rides",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "bike_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("bikes.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("client_ride_uuid", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", _ride_status, nullable=False, server_default="recording"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True)),
        sa.Column("elapsed_seconds", sa.Integer, nullable=False, server_default="0"),
        sa.Column("moving_seconds", sa.Integer, nullable=False, server_default="0"),
        sa.Column("distance_m", sa.Numeric(12, 2), nullable=False, server_default="0"),
        sa.Column("elevation_gain_m", sa.Numeric(10, 2), nullable=False, server_default="0"),
        sa.Column("elevation_loss_m", sa.Numeric(10, 2), nullable=False, server_default="0"),
        sa.Column("average_speed_m_s", sa.Numeric(8, 3), nullable=False, server_default="0"),
        sa.Column("max_speed_m_s", sa.Numeric(8, 3), nullable=False, server_default="0"),
        sa.Column("start_lat", sa.Numeric(9, 6)),
        sa.Column("start_lon", sa.Numeric(10, 6)),
        sa.Column("end_lat", sa.Numeric(9, 6)),
        sa.Column("end_lon", sa.Numeric(10, 6)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("user_id", "client_ride_uuid", name="uq_rides_user_client_uuid"),
    )
    op.create_index("ix_rides_user_id", "rides", ["user_id"])
    op.create_index("ix_rides_bike_id", "rides", ["bike_id"])
    op.create_index("ix_rides_user_status", "rides", ["user_id", "status"])
    op.create_index("ix_rides_started", "rides", ["started_at"])

    op.create_table(
        "ride_points",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column(
            "ride_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("rides.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("seq", sa.Integer, nullable=False),
        sa.Column("client_point_uuid", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("lat", sa.Numeric(9, 6), nullable=False),
        sa.Column("lon", sa.Numeric(10, 6), nullable=False),
        sa.Column("alt", sa.Numeric(8, 2)),
        sa.Column("accuracy", sa.Numeric(6, 2)),
        sa.Column("speed", sa.Numeric(6, 2)),
        sa.Column("heading", sa.Numeric(5, 1)),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("accepted", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("reject_reason", sa.String(32)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("ride_id", "client_point_uuid", name="uq_points_ride_client_uuid"),
        sa.UniqueConstraint("ride_id", "seq", name="uq_points_ride_seq"),
    )
    op.create_index("ix_ride_points_ride_id", "ride_points", ["ride_id"])
    op.create_index("ix_points_ride_seq", "ride_points", ["ride_id", "seq"])


def downgrade() -> None:
    op.drop_table("ride_points")
    op.drop_table("rides")
    _ride_status.drop(op.get_bind(), checkfirst=True)

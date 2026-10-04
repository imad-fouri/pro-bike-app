"""Phase 5: routes + route_versions + route_points, ride→route association.

PostGIS still deferred (ADR-09 §3): geometry is normalized, versioned rows.
Downgrade drops ride association columns first (FK), then route tables.
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0005_routes"
down_revision = "0004_rides"

# create_type=False: types are created/dropped explicitly below so that the two
# tables sharing `route_difficulty` never race on CREATE TYPE.
_route_activity = postgresql.ENUM(
    "road",
    "gravel",
    "mountain_bike",
    "touring",
    "bikepacking",
    "commuting",
    "e_bike",
    "other",
    name="route_activity_type",
    create_type=False,
)
_route_privacy = postgresql.ENUM(
    "private", "unlisted", "public", name="route_privacy", create_type=False
)
_route_status = postgresql.ENUM("active", "archived", name="route_status", create_type=False)
_route_source = postgresql.ENUM("manual", "gpx", name="route_source", create_type=False)
_route_difficulty = postgresql.ENUM(
    "easy", "moderate", "hard", "extreme", name="route_difficulty", create_type=False
)


def upgrade() -> None:
    _route_activity.create(op.get_bind(), checkfirst=True)
    _route_privacy.create(op.get_bind(), checkfirst=True)
    _route_status.create(op.get_bind(), checkfirst=True)
    _route_source.create(op.get_bind(), checkfirst=True)
    _route_difficulty.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "routes",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "owner_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("description", sa.String(2000)),
        sa.Column("activity_type", _route_activity, nullable=False),
        sa.Column("privacy", _route_privacy, nullable=False, server_default="private"),
        sa.Column("status", _route_status, nullable=False, server_default="active"),
        sa.Column("source", _route_source, nullable=False, server_default="manual"),
        sa.Column("current_version", sa.Integer, nullable=False, server_default="1"),
        sa.Column("distance_m", sa.Numeric(12, 2), nullable=False, server_default="0"),
        # NULL = unknown elevation (never faked as 0)
        sa.Column("elevation_gain_m", sa.Numeric(10, 2)),
        sa.Column("elevation_loss_m", sa.Numeric(10, 2)),
        sa.Column("highest_point_m", sa.Numeric(8, 2)),
        sa.Column("lowest_point_m", sa.Numeric(8, 2)),
        sa.Column("estimated_duration_s", sa.Integer),
        sa.Column("difficulty", _route_difficulty),
        sa.Column("point_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("start_lat", sa.Numeric(9, 6)),
        sa.Column("start_lon", sa.Numeric(10, 6)),
        sa.Column("end_lat", sa.Numeric(9, 6)),
        sa.Column("end_lon", sa.Numeric(10, 6)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_routes_owner_id", "routes", ["owner_id"])
    op.create_index("ix_routes_owner_created", "routes", ["owner_id", "created_at"])
    op.create_index("ix_routes_owner_privacy", "routes", ["owner_id", "privacy"])
    op.create_index("ix_routes_owner_status", "routes", ["owner_id", "status"])

    op.create_table(
        "route_versions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "route_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("routes.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("version_no", sa.Integer, nullable=False),
        sa.Column("point_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("distance_m", sa.Numeric(12, 2), nullable=False, server_default="0"),
        sa.Column("elevation_gain_m", sa.Numeric(10, 2)),
        sa.Column("elevation_loss_m", sa.Numeric(10, 2)),
        sa.Column("estimated_duration_s", sa.Integer),
        sa.Column("difficulty", _route_difficulty),
        sa.Column("elevation_profile", postgresql.JSONB),
        sa.Column("changelog", sa.String(200)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("route_id", "version_no", name="uq_route_versions_route_version"),
    )
    op.create_index("ix_route_versions_route_id", "route_versions", ["route_id"])

    op.create_table(
        "route_points",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column(
            "route_version_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("route_versions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("seq", sa.Integer, nullable=False),
        sa.Column("lat", sa.Numeric(9, 6), nullable=False),
        sa.Column("lon", sa.Numeric(10, 6), nullable=False),
        sa.Column("ele", sa.Numeric(8, 2)),
        sa.UniqueConstraint("route_version_id", "seq", name="uq_route_points_version_seq"),
    )

    # Ride → route association: nullable, existing rides stay valid (§21).
    op.add_column("rides", sa.Column("route_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("rides", sa.Column("route_version", sa.Integer, nullable=True))
    op.create_foreign_key(
        "fk_rides_route_id", "rides", "routes", ["route_id"], ["id"], ondelete="SET NULL"
    )
    op.create_index("ix_rides_route_id", "rides", ["route_id"])


def downgrade() -> None:
    op.drop_index("ix_rides_route_id", table_name="rides")
    op.drop_constraint("fk_rides_route_id", "rides", type_="foreignkey")
    op.drop_column("rides", "route_version")
    op.drop_column("rides", "route_id")

    op.drop_table("route_points")
    op.drop_table("route_versions")
    op.drop_table("routes")

    _route_difficulty.drop(op.get_bind(), checkfirst=True)
    _route_source.drop(op.get_bind(), checkfirst=True)
    _route_status.drop(op.get_bind(), checkfirst=True)
    _route_privacy.drop(op.get_bind(), checkfirst=True)
    _route_activity.drop(op.get_bind(), checkfirst=True)

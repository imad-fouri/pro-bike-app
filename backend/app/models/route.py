"""Phase 5 domain: routes (planned/reference geometry).

ROUTE != RIDE: a Route is planned geometry; a Ride is recorded activity
(docs/05, ADR-09). Geometry lives on route_versions → route_points so that
every version is immutable and a ride can pin (route_id, route_version).
"""

import enum
import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, NUMERIC, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.user import _uuid, _values


class RouteActivityType(str, enum.Enum):
    ROAD = "road"
    GRAVEL = "gravel"
    MOUNTAIN_BIKE = "mountain_bike"
    TOURING = "touring"
    BIKEPACKING = "bikepacking"
    COMMUTING = "commuting"
    E_BIKE = "e_bike"
    OTHER = "other"


class RoutePrivacy(str, enum.Enum):
    PRIVATE = "private"
    UNLISTED = "unlisted"
    PUBLIC = "public"  # enum-ready, API-disabled until start/end masking (ADR-09 §5)


class RouteStatus(str, enum.Enum):
    ACTIVE = "active"
    ARCHIVED = "archived"


class RouteSource(str, enum.Enum):
    MANUAL = "manual"  # drawn on the map
    GPX = "gpx"  # imported


class RouteDifficulty(str, enum.Enum):
    """Rule-based estimate only — never presented as an AI/universal score."""

    EASY = "easy"
    MODERATE = "moderate"
    HARD = "hard"
    EXTREME = "extreme"


class Route(Base):
    __tablename__ = "routes"
    __table_args__ = (
        Index("ix_routes_owner_created", "owner_id", "created_at"),
        Index("ix_routes_owner_privacy", "owner_id", "privacy"),
        Index("ix_routes_owner_status", "owner_id", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    owner_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(String(2000))
    activity_type: Mapped[RouteActivityType] = mapped_column(
        Enum(RouteActivityType, name="route_activity_type", values_callable=_values),
        nullable=False,
    )
    privacy: Mapped[RoutePrivacy] = mapped_column(
        Enum(RoutePrivacy, name="route_privacy", values_callable=_values),
        default=RoutePrivacy.PRIVATE,
        nullable=False,
    )
    status: Mapped[RouteStatus] = mapped_column(
        Enum(RouteStatus, name="route_status", values_callable=_values),
        default=RouteStatus.ACTIVE,
        nullable=False,
    )
    source: Mapped[RouteSource] = mapped_column(
        Enum(RouteSource, name="route_source", values_callable=_values),
        default=RouteSource.MANUAL,
        nullable=False,
    )
    # Monotonic, optimistic-locking key (docs/03, docs/06).
    current_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    # DERIVED from points at write time (never on a UI frame).
    # NULL elevation ⇒ "unknown", never silently 0 (§15).
    distance_m: Mapped[Decimal] = mapped_column(NUMERIC(12, 2), default=Decimal(0), nullable=False)
    elevation_gain_m: Mapped[Decimal | None] = mapped_column(NUMERIC(10, 2))
    elevation_loss_m: Mapped[Decimal | None] = mapped_column(NUMERIC(10, 2))
    highest_point_m: Mapped[Decimal | None] = mapped_column(NUMERIC(8, 2))
    lowest_point_m: Mapped[Decimal | None] = mapped_column(NUMERIC(8, 2))
    # ESTIMATED (rule-based), NULL when it cannot be justified.
    estimated_duration_s: Mapped[int | None] = mapped_column(Integer)
    difficulty: Mapped[RouteDifficulty | None] = mapped_column(
        Enum(RouteDifficulty, name="route_difficulty", values_callable=_values)
    )
    point_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # Bounding-box / preview columns (ADR-09 §3) — avoid loading geometry.
    start_lat: Mapped[Decimal | None] = mapped_column(NUMERIC(9, 6))
    start_lon: Mapped[Decimal | None] = mapped_column(NUMERIC(10, 6))
    end_lat: Mapped[Decimal | None] = mapped_column(NUMERIC(9, 6))
    end_lon: Mapped[Decimal | None] = mapped_column(NUMERIC(10, 6))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class RouteVersion(Base):
    """Immutable snapshot created on every route edit (never overwritten)."""

    __tablename__ = "route_versions"
    __table_args__ = (
        UniqueConstraint("route_id", "version_no", name="uq_route_versions_route_version"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    route_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("routes.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    point_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    distance_m: Mapped[Decimal] = mapped_column(NUMERIC(12, 2), default=Decimal(0), nullable=False)
    elevation_gain_m: Mapped[Decimal | None] = mapped_column(NUMERIC(10, 2))
    elevation_loss_m: Mapped[Decimal | None] = mapped_column(NUMERIC(10, 2))
    estimated_duration_s: Mapped[int | None] = mapped_column(Integer)
    difficulty: Mapped[RouteDifficulty | None] = mapped_column(
        Enum(RouteDifficulty, name="route_difficulty", values_callable=_values)
    )
    # DERIVED elevation profile cache: [[distance_m, ele_m], …] (downsampled).
    elevation_profile: Mapped[list | None] = mapped_column(JSONB)
    changelog: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class RoutePoint(Base):
    """Ordered geometry for one version. seq starts at 0."""

    __tablename__ = "route_points"
    __table_args__ = (
        UniqueConstraint("route_version_id", "seq", name="uq_route_points_version_seq"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    route_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("route_versions.id", ondelete="CASCADE"),
        nullable=False,
    )
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    lat: Mapped[Decimal] = mapped_column(NUMERIC(9, 6), nullable=False)
    lon: Mapped[Decimal] = mapped_column(NUMERIC(10, 6), nullable=False)
    ele: Mapped[Decimal | None] = mapped_column(NUMERIC(8, 2))

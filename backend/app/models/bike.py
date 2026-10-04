"""Phase 3 domain: bikes. Rides reference bikes in Phase 4+ (FK RESTRICT)."""

import enum
import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import CheckConstraint, DateTime, Enum, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.user import _uuid, _values


class BikeCategory(str, enum.Enum):
    ROAD = "road"
    GRAVEL = "gravel"
    MOUNTAIN_BIKE = "mountain_bike"
    CYCLOCROSS = "cyclocross"
    ENDURANCE = "endurance"
    TIME_TRIAL = "time_trial"
    TRACK = "track"
    BMX = "bmx"
    TOURING = "touring"
    BIKEPACKING = "bikepacking"
    E_BIKE = "e_bike"
    COMMUTING = "commuting"
    OTHER = "other"


class BikeStatus(str, enum.Enum):
    ACTIVE = "active"
    ARCHIVED = "archived"


class Bike(Base):
    __tablename__ = "bikes"
    __table_args__ = (
        CheckConstraint(
            "model_year IS NULL OR (model_year >= 1900 AND model_year <= 2100)",
            name="ck_bikes_year",
        ),
        CheckConstraint(
            "weight_kg IS NULL OR (weight_kg > 0 AND weight_kg <= 200)", name="ck_bikes_weight"
        ),
        CheckConstraint(
            "initial_distance_km >= 0 AND initial_distance_km <= 1000000", name="ck_bikes_distance"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    owner_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    category: Mapped[BikeCategory] = mapped_column(
        Enum(BikeCategory, name="bike_category", values_callable=_values),
        nullable=False,
    )
    brand: Mapped[str | None] = mapped_column(String(120))
    model: Mapped[str | None] = mapped_column(String(120))
    model_year: Mapped[int | None] = mapped_column(Integer)
    frame_size: Mapped[str | None] = mapped_column(String(32))
    weight_kg: Mapped[Decimal | None] = mapped_column()  # NUMERIC via Decimal type
    notes: Mapped[str | None] = mapped_column(Text)
    image_ref: Mapped[str | None] = mapped_column(String(512))
    status: Mapped[BikeStatus] = mapped_column(
        Enum(BikeStatus, name="bike_status", values_callable=_values),
        default=BikeStatus.ACTIVE,
        nullable=False,
    )
    initial_distance_km: Mapped[Decimal] = mapped_column(default=Decimal(0), nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

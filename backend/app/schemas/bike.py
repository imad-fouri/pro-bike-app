"""Bike schemas. Canonical units (kg/km Decimals); presentation converts."""

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, Field, field_validator

from app.models.bike import BikeCategory, BikeStatus

Name = Annotated[str, Field(min_length=1, max_length=80)]
OptStr120 = Annotated[str, Field(min_length=1, max_length=120)] | None


def _current_year() -> int:
    return datetime.now(UTC).year


class BikeCreate(BaseModel):
    name: Name
    category: BikeCategory
    brand: OptStr120 = None
    model: OptStr120 = None
    model_year: int | None = Field(default=None, ge=1900)
    frame_size: str | None = Field(default=None, min_length=1, max_length=32)
    weight_kg: Decimal | None = Field(default=None, gt=0, le=200, max_digits=5, decimal_places=2)
    notes: str | None = Field(default=None, min_length=1, max_length=2000)
    initial_distance_km: Decimal = Field(
        default=Decimal(0), ge=0, le=1000000, max_digits=10, decimal_places=2
    )

    @field_validator("name", mode="before")
    @classmethod
    def _strip_name(cls, v: object) -> object:
        return v.strip() if isinstance(v, str) else v

    @field_validator("model_year")
    @classmethod
    def _year(cls, v: int | None) -> int | None:
        if v is not None and v > _current_year() + 1:
            raise ValueError("Model year is in the future.")
        return v


class BikeUpdate(BaseModel):
    name: Name | None = None
    category: BikeCategory | None = None
    brand: OptStr120 = None
    model: OptStr120 = None
    model_year: int | None = Field(default=None, ge=1900)
    frame_size: str | None = Field(default=None, min_length=1, max_length=32)
    weight_kg: Decimal | None = Field(default=None, gt=0, le=200, max_digits=5, decimal_places=2)
    notes: str | None = Field(default=None, min_length=1, max_length=2000)
    initial_distance_km: Decimal | None = Field(
        default=None, ge=0, le=1000000, max_digits=10, decimal_places=2
    )
    expected_version: int | None = Field(default=None, ge=1)

    @field_validator("model_year")
    @classmethod
    def _year(cls, v: int | None) -> int | None:
        if v is not None and v > _current_year() + 1:
            raise ValueError("Model year is in the future.")
        return v


class BikeOut(BaseModel):
    id: uuid.UUID
    name: str
    category: BikeCategory
    brand: str | None
    model: str | None
    model_year: int | None
    frame_size: str | None
    weight_kg: Decimal | None
    notes: str | None
    image_ref: str | None
    status: BikeStatus
    initial_distance_km: Decimal
    version: int
    created_at: datetime
    updated_at: datetime


class BikePage(BaseModel):
    items: list[BikeOut]
    total: int
    page: int
    page_size: int


SortField = Literal["created_at", "updated_at", "name"]
SortOrder = Literal["asc", "desc"]

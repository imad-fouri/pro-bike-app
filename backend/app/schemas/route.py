"""Route schemas. Canonical metric units; UI converts to km/mi (§43).

Provenance is explicit (§15): DERIVED metrics vs ESTIMATED duration/difficulty.
"""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, Field

from app.models.route import (
    RouteActivityType,
    RouteDifficulty,
    RoutePrivacy,
    RouteSource,
    RouteStatus,
)

Name = Annotated[str, Field(min_length=1, max_length=120)]
# Hard safety net; the enforced, env-configurable limit lives in the service.
MAX_POINTS_HARD = 100_000


class RoutePointIn(BaseModel):
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    ele: float | None = Field(default=None, ge=-500, le=9000)


class RouteCreate(BaseModel):
    name: Name
    description: str | None = Field(default=None, min_length=1, max_length=2000)
    activity_type: RouteActivityType
    privacy: RoutePrivacy = RoutePrivacy.PRIVATE  # private by default (§7)
    points: list[RoutePointIn] = Field(min_length=2, max_length=MAX_POINTS_HARD)


class RouteUpdate(BaseModel):
    """Optimistic concurrency is mandatory: every edit bumps the version (§11)."""

    expected_version: int = Field(ge=1)
    name: Name | None = None
    description: str | None = Field(default=None, min_length=1, max_length=2000)
    activity_type: RouteActivityType | None = None
    privacy: RoutePrivacy | None = None
    points: list[RoutePointIn] | None = Field(
        default=None, min_length=2, max_length=MAX_POINTS_HARD
    )
    changelog: str | None = Field(default=None, min_length=1, max_length=200)


class MetricsBasis(BaseModel):
    """RAW/DERIVED/ESTIMATED markers (§15). Never claim estimates are exact."""

    distance_m: Literal["derived"] = "derived"
    elevation: Literal["derived"] = "derived"
    estimated_duration_s: Literal["estimated"] = "estimated"
    difficulty: Literal["rule_based_estimate"] = "rule_based_estimate"


class RouteOut(BaseModel):
    id: uuid.UUID
    name: str
    description: str | None
    activity_type: RouteActivityType
    privacy: RoutePrivacy
    status: RouteStatus
    source: RouteSource
    current_version: int
    distance_m: Decimal
    elevation_gain_m: Decimal | None
    elevation_loss_m: Decimal | None
    highest_point_m: Decimal | None
    lowest_point_m: Decimal | None
    estimated_duration_s: int | None
    difficulty: RouteDifficulty | None
    point_count: int
    start_lat: Decimal | None
    start_lon: Decimal | None
    end_lat: Decimal | None
    end_lon: Decimal | None
    basis: MetricsBasis = Field(default_factory=MetricsBasis)
    created_at: datetime
    updated_at: datetime


class RoutePointOut(BaseModel):
    seq: int
    lat: Decimal
    lon: Decimal
    ele: Decimal | None


class RouteGeometry(BaseModel):
    route_id: uuid.UUID
    version_no: int
    point_count: int
    points: list[RoutePointOut]
    elevation_profile: list[list[float]] | None


class RouteDetail(RouteOut):
    geometry: RouteGeometry | None = None


class RoutePage(BaseModel):
    items: list[RouteOut]
    total: int
    page: int
    page_size: int


class RouteVersionOut(BaseModel):
    id: uuid.UUID
    version_no: int
    point_count: int
    distance_m: Decimal
    elevation_gain_m: Decimal | None = None
    elevation_loss_m: Decimal | None = None
    estimated_duration_s: int | None
    difficulty: RouteDifficulty | None
    changelog: str | None
    created_at: datetime


class RouteVersionList(BaseModel):
    items: list[RouteVersionOut]


class GpxImportResult(BaseModel):
    route: RouteDetail
    imported_points: int
    duplicates_removed: int
    had_timestamps: bool


SortField = Literal["created_at", "updated_at", "name", "distance"]
SortOrder = Literal["asc", "desc"]

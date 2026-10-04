"""Ride schemas. Meters/seconds canonical; presentation converts to km/mi."""

import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, Field

from app.models.ride import RideStatus

MAX_CHUNK = 500


class RideCreate(BaseModel):
    bike_id: uuid.UUID
    client_ride_uuid: uuid.UUID
    # Optional planned route; pinned (route_id, route_version) — never the reverse.
    route_id: uuid.UUID | None = None
    route_version: int | None = Field(default=None, ge=1)


class PointIn(BaseModel):
    client_point_uuid: uuid.UUID
    seq: int = Field(ge=0)
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    recorded_at: datetime
    alt: float | None = Field(default=None, ge=-500, le=9000)
    accuracy: float | None = Field(default=None, ge=0, le=1000)
    speed: float | None = Field(default=None, ge=0, le=300)
    heading: float | None = Field(default=None, ge=0, lt=360)
    # Optional sensor sample riding along with the fix (Phase 6, ADR-10 §6).
    # There is no sensor hardware yet: these stay absent unless a client has a
    # real reading. Nothing is ever estimated from GPS or speed.
    power_w: float | None = Field(default=None, ge=0, le=1500)
    hr_bpm: float | None = Field(default=None, ge=25, le=250)
    cadence_rpm: float | None = Field(default=None, ge=0, le=250)


class PointsChunk(BaseModel):
    points: list[PointIn] = Field(min_length=1, max_length=MAX_CHUNK)


class RejectedPoint(BaseModel):
    seq: int
    reason: str


class RideSummary(BaseModel):
    distance_m: Decimal
    elevation_gain_m: Decimal
    elevation_loss_m: Decimal
    moving_seconds: int
    elapsed_seconds: int
    average_speed_m_s: Decimal
    max_speed_m_s: Decimal
    accepted_points: int


class RideOut(BaseModel):
    id: uuid.UUID
    bike_id: uuid.UUID
    status: RideStatus
    started_at: datetime
    ended_at: datetime | None
    summary: RideSummary
    start_lat: Decimal | None
    start_lon: Decimal | None
    end_lat: Decimal | None
    end_lon: Decimal | None
    route_id: uuid.UUID | None = None
    route_version: int | None = None


class RideRouteLink(BaseModel):
    """Associate/clear a planned route for an owned ride (nullable = detach)."""

    route_id: uuid.UUID | None = None
    route_version: int | None = Field(default=None, ge=1)


class ChunkResult(BaseModel):
    accepted: int
    rejected: list[RejectedPoint]
    duplicates: int
    summary: RideSummary


class RidePage(BaseModel):
    items: list[RideOut]
    total: int
    page: int
    page_size: int

"""Phase 9 group-ride API schemas (ADR-16).

Request schemas are `extra="forbid"` so a client cannot smuggle a field the
service does not read — `organizer_user_id`, `status` or `route_version` arriving
in a create body is a bug worth failing loudly rather than ignoring.

The RESPONSE shape is one for every rider, not one per privilege level, and the
service decides what each viewer sees inside it. Two things are therefore NOT in
these schemas:

* No `is_admin`-style flag on the roster. Whether YOU organize this ride is your
  own fact, and it is in `viewer`, computed per request.
* No invitation-management surface. `message` is the inviter's note to the rider,
  which the rider reads; there is no field for a manager to edit somebody else's
  response.

`coordinates` appears in exactly one place: the live-location request and
response models. It is deliberately not part of any ride model, because a ride
does not have a location — people do, and only ephemerally (ADR-16 §6).
"""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class RideCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=1000)
    starts_at: datetime | None = None
    #: A free-text meeting point. Deliberately not a coordinate pair: navigation
    #: and geocoding are non-goals for this phase.
    meeting_point: str | None = Field(default=None, max_length=160)
    #: An IMMUTABLE geometry pin. Both-or-neither with `route_version`, and the
    #: version must already exist — a ride never resolves "latest" for you, because
    #: later editing a route must not change what riders agreed to ride.
    route_id: uuid.UUID | None = None
    route_version: int | None = Field(default=None, ge=1)


class InviteCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_id: uuid.UUID
    message: str | None = Field(default=None, max_length=280)


class InviteBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    #: Bounded so one request cannot fan out to an unbounded number of push
    #: notifications, and so the organizer gets partial feedback rather than a
    #: single opaque failure.
    user_ids: list[uuid.UUID] = Field(min_length=1, max_length=50)
    message: str | None = Field(default=None, max_length=280)


class RespondRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    accept: bool


class LocationPing(BaseModel):
    """One rider's consented position for a ride they are currently on."""

    model_config = ConfigDict(extra="forbid")

    latitude: float = Field(ge=-90.0, le=90.0)
    longitude: float = Field(ge=-180.0, le=180.0)
    #: Optional accuracy in metres, used to grey out a dot that does not know where
    #: it is precisely. Not a privacy control.
    accuracy_m: float | None = Field(default=None, ge=0.0, le=10_000.0)


class LocationRider(BaseModel):
    """A visible rider's position.

    Includes `age_seconds` so the client can decide staleness itself rather than
    trusting a server-rendered "last seen 2 minutes ago" string that goes stale
    in the widget. `is_self` so a rider can find their own dot without a second
    lookup.
    """

    user_id: uuid.UUID
    username: str
    display_name: str | None = None
    avatar_url: str | None = None
    latitude: float
    longitude: float
    accuracy_m: float | None = None
    age_seconds: int
    is_self: bool

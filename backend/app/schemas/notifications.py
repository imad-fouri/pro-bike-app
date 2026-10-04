"""Phase 8.4 notification API schemas (ADR-15).

Two output shapes: a device registration (never including its token) and a
notification (referencing a localization key, never carrying rendered copy).

The single most important rule in this file: **no schema has a `token` field on
the way out.** `PushDeviceOut` is built explicitly rather than by validating a
model, so adding a column to `push_devices` can never accidentally expose it.
"""

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.notifications import NotificationType, PushPlatform, PushProvider


class PushDeviceOut(BaseModel):
    """A device registration, without its token.

    `device_id` is client-generated and identifies the installation; it is not a
    credential, so exposing it to its owner is safe and is what lets the client
    recognise "this is the device I registered".
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    platform: PushPlatform
    provider: PushProvider
    device_id: str
    app_version: str | None = None
    locale: str | None = None
    enabled: bool
    last_seen_at: datetime | None = None
    created_at: datetime


class PushDeviceRegister(BaseModel):
    """Register or refresh one device.

    There is no `user_id`: ownership is server-derived from the JWT, and a
    client that could name a user would be able to register a push target on
    someone else's account.
    """

    model_config = ConfigDict(extra="forbid")

    platform: PushPlatform
    provider: PushProvider
    #: Stable per-installation id the client generates and keeps. Used as the
    #: identity so a provider token rotation is an UPDATE, not a new row.
    device_id: str = Field(min_length=1, max_length=128)
    token: str = Field(min_length=1, max_length=4096)
    app_version: str | None = Field(default=None, max_length=32)
    locale: str | None = Field(default=None, max_length=8)

    @field_validator("device_id", "token")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        """Refuse whitespace-only input here rather than letting it become a row.

        The database has CHECK constraints for the same thing, but those surface
        as a constraint violation (a 500) instead of a validation error.
        """
        if not v.strip():
            raise ValueError("Must not be empty.")
        return v


class PushDeviceUpdate(BaseModel):
    """Only the fields a rider may change on their own device.

    Deliberately narrow: a client cannot move a device to another user, change
    its platform, or rewrite its token through this route (a new token arrives
    through registration, which is idempotent).
    """

    model_config = ConfigDict(extra="forbid")

    enabled: bool | None = None


class PushDevicePage(BaseModel):
    """The rider's own devices.

    An envelope rather than a bare array for two reasons: every other listing in
    this project uses one, and the shared `ApiClient` decodes only JSON objects —
    a top-level array would become a client-side cast error.
    """

    items: list[PushDeviceOut]
    total: int


class NotificationOut(BaseModel):
    """One notification.

    Carries `l10n_key` + `params` rather than a rendered title/body, so the
    client renders the notification in the rider's own locale. An unknown
    `l10n_key` must be handled by the client as a generic fallback, not a crash.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    type: NotificationType
    entity_type: str | None = None
    entity_id: uuid.UUID | None = None
    l10n_key: str
    params: dict[str, Any]
    deep_link: str | None = None
    is_read: bool
    is_unread: bool
    created_at: datetime
    read_at: datetime | None = None
    actor_user_id: uuid.UUID | None = None
    actor_username: str | None = None
    actor_display_name: str | None = None
    entity_name: str | None = None


class NotificationPage(BaseModel):
    """Offset-paged, matching the project standard.

    Cursor pagination was considered and rejected: unlike message history, a
    notification center is bounded and read deliberately, so an offset page is
    stable in practice and the standard envelope gives the UI a total for free
    (ADR-15 §7).
    """

    items: list[NotificationOut]
    total: int
    page: int
    page_size: int


class UnreadCountOut(BaseModel):
    unread_count: int


class MarkAllReadOut(BaseModel):
    marked: int

"""Phase 8.1 social API schemas (ADR-12).

Two profile shapes only: the full profile the owner sees, and the
privacy-filtered public profile everyone else sees. The service decides
which one a viewer gets; the schemas just describe them. Nothing here
carries email, tokens, coordinates, or internal database state.
"""

import uuid
from datetime import datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class Visibility(str, Enum):
    PUBLIC = "public"
    FRIENDS = "friends"
    PRIVATE = "private"


class FriendRequestsPolicy(str, Enum):
    EVERYONE = "everyone"
    NOBODY = "nobody"


class SearchVisibility(str, Enum):
    DISCOVERABLE = "discoverable"
    HIDDEN = "hidden"


class RelationshipState(str, Enum):
    SELF = "SELF"
    NONE = "NONE"
    OUTGOING_PENDING = "OUTGOING_PENDING"
    INCOMING_PENDING = "INCOMING_PENDING"
    FRIENDS = "FRIENDS"
    BLOCKED = "BLOCKED"
    BLOCKED_BY_USER = "BLOCKED_BY_USER"


class SocialProfileOut(BaseModel):
    """The owner's own full profile. No relationship field: it is always SELF."""

    model_config = ConfigDict(from_attributes=True)

    user_id: uuid.UUID
    username: str | None
    display_name: str
    bio: str | None
    avatar_url: str | None
    cycling_category: str | None
    country_code: str | None
    city: str | None
    profile_visibility: Visibility
    allow_friend_requests: FriendRequestsPolicy
    search_visibility: SearchVisibility
    created_at: datetime
    updated_at: datetime


class PublicProfileOut(BaseModel):
    """What a viewer may see. `limited=True` means visibility rules redacted
    the profile down to bare identity; the client must not render locked
    sections as if they were empty."""

    user_id: uuid.UUID
    username: str | None
    display_name: str | None
    bio: str | None = None
    avatar_url: str | None = None
    cycling_category: str | None = None
    country_code: str | None = None
    city: str | None = None
    relationship: RelationshipState
    limited: bool


class SocialProfileUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: str | None = Field(default=None, max_length=30)
    display_name: str | None = Field(default=None, max_length=80)
    bio: str | None = Field(default=None, max_length=500)
    avatar_url: str | None = Field(default=None, max_length=512)
    cycling_category: str | None = Field(default=None, max_length=32)
    country_code: str | None = Field(default=None, max_length=2)
    city: str | None = Field(default=None, max_length=120)


class SocialPrivacyUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    profile_visibility: Visibility | None = None
    allow_friend_requests: FriendRequestsPolicy | None = None
    search_visibility: SearchVisibility | None = None


class FriendRequestCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_id: uuid.UUID


class FriendRequestOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    user_id: uuid.UUID
    username: str | None
    display_name: str | None
    avatar_url: str | None = None
    direction: str
    status: str
    created_at: datetime


class FriendOut(BaseModel):
    user_id: uuid.UUID
    username: str | None
    display_name: str | None
    avatar_url: str | None = None
    friends_since: datetime


class BlockOut(BaseModel):
    user_id: uuid.UUID
    username: str | None
    display_name: str | None
    blocked_at: datetime


class BlockCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_id: uuid.UUID


class FriendRequestPage(BaseModel):
    """Established pagination shape: items/total/page/page_size."""

    items: list[FriendRequestOut]
    total: int
    page: int
    page_size: int


class FriendPage(BaseModel):
    items: list[FriendOut]
    total: int
    page: int
    page_size: int


class BlockPage(BaseModel):
    items: list[BlockOut]
    total: int
    page: int
    page_size: int


class SearchPage(BaseModel):
    items: list[PublicProfileOut]
    total: int
    page: int
    page_size: int

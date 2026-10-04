"""Phase 8.2 team API schemas (ADR-13).

Two team shapes, mirroring the Phase 8.1 split: the owner's full view (which
carries their own role and the management counters they may act on) and the
public view every other member or discoverer sees. The service decides which one
a viewer gets; these schemas only describe them.

Nothing here carries email, phone, coordinates, tokens, or membership state the
viewer is not entitled to see. A member list entry is a public social profile
projection — the same shape social already exposes — plus a role.
"""

import uuid
from datetime import datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class Visibility(str, Enum):
    PUBLIC = "public"
    PRIVATE = "private"


class Status(str, Enum):
    ACTIVE = "active"
    ARCHIVED = "archived"


class Role(str, Enum):
    OWNER = "owner"
    ADMIN = "admin"
    MEMBER = "member"


class InvitationStatus(str, Enum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    DECLINED = "declined"
    REVOKED = "revoked"


class TeamState(str, Enum):
    """Server-authoritative viewer's relationship to a team.

    Never derived client-side: the server resolves it from the membership and
    invitation tables on every read.
    """

    MEMBER = "MEMBER"
    OWNER = "OWNER"
    ADMIN = "ADMIN"
    JOIN_REQUEST_PENDING = "JOIN_REQUEST_PENDING"
    INVITED = "INVITED"
    NOT_AFFILIATED = "NOT_AFFILIATED"


class TeamCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=80)
    handle: str | None = Field(default=None, max_length=30)
    description: str | None = Field(default=None, max_length=500)
    avatar_url: str | None = Field(default=None, max_length=512)
    category: str | None = Field(default=None, max_length=32)
    visibility: Visibility = Visibility.PUBLIC


class TeamUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=80)
    handle: str | None = Field(default=None, max_length=30)
    description: str | None = Field(default=None, max_length=500)
    avatar_url: str | None = Field(default=None, max_length=512)
    category: str | None = Field(default=None, max_length=32)
    visibility: Visibility | None = None


class TeamOut(BaseModel):
    """The owner's own team on create.

    Carries `owner_user_id` because the creator has just been told they own it.
    Every other read uses `PublicTeamOut`, which omits the owner id so a
    non-member browsing a public team learns nothing about who runs it beyond
    the member list they can already see.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    owner_user_id: uuid.UUID
    name: str
    handle: str | None
    description: str | None
    avatar_url: str | None
    category: str | None
    visibility: Visibility
    status: Status
    member_count: int
    created_at: datetime
    updated_at: datetime
    my_role: Role
    state: TeamState
    pending_requests_count: int = 0
    pending_invitations_count: int = 0


class PublicTeamOut(BaseModel):
    """What any viewer may see, plus their own server-resolved state.

    Deliberately omits `owner_user_id`: a stranger browsing a public team
    should learn that it exists and what it calls itself, not who to target.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    handle: str | None
    description: str | None
    avatar_url: str | None
    category: str | None
    visibility: Visibility
    status: Status
    member_count: int
    created_at: datetime
    my_role: Role | None
    state: TeamState
    # Present only for owners/admins, who are the only ones who may act on them.
    pending_requests_count: int = 0
    pending_invitations_count: int = 0


class TeamMemberOut(BaseModel):
    """One member. Public identity projection + role. Never email, never GPS."""

    user_id: uuid.UUID
    username: str | None
    display_name: str | None
    avatar_url: str | None
    role: Role
    joined_at: datetime


class JoinRequestCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message: str | None = Field(default=None, max_length=280)


class JoinRequestOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    team_id: uuid.UUID
    user_id: uuid.UUID
    username: str | None
    display_name: str | None
    message: str | None
    created_at: datetime


class InvitationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_id: uuid.UUID
    message: str | None = Field(default=None, max_length=280)


class InvitationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    team_id: uuid.UUID
    team_name: str | None = None
    team_handle: str | None = None
    invited_user_id: uuid.UUID
    invited_by_user_id: uuid.UUID
    invited_by_username: str | None = None
    status: InvitationStatus
    message: str | None
    created_at: datetime
    responded_at: datetime | None = None


class TeamPage(BaseModel):
    """Established pagination shape: items/total/page/page_size."""

    items: list[PublicTeamOut]
    total: int
    page: int
    page_size: int


class TeamMemberPage(BaseModel):
    items: list[TeamMemberOut]
    total: int
    page: int
    page_size: int


class JoinRequestPage(BaseModel):
    items: list[JoinRequestOut]
    total: int
    page: int
    page_size: int


class InvitationPage(BaseModel):
    items: list[InvitationOut]
    total: int
    page: int
    page_size: int

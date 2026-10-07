"""WS-RC challenge API schemas.

Every number a client may write is a *target*: the metric, the target value,
and the points on offer. Progress, rank, completion and participant counts are
server outputs only and never appear in a request schema. All request bodies
forbid unknown keys, so a future client that tries to smuggle ``progress``,
``score`` or ``rank`` into a payload is rejected with 422.
"""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Metric = Literal["distance", "elevation", "rides", "training", "streak"]
Scope = Literal["individual", "friends", "team", "global"]
Visibility = Literal["public", "private"]
State = Literal["draft", "scheduled", "active", "completed", "cancelled", "expired"]
Status = Literal["draft", "scheduled", "active", "completed", "cancelled"]
ViewerState = Literal["joined", "active", "completed", "left", "disqualified"]


class ChallengeCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=1000)
    metric: Metric
    target: Decimal = Field(gt=0, max_digits=14, decimal_places=2)
    points: int | None = Field(default=None, ge=10, le=1000)
    scope: Scope = "global"
    visibility: Visibility = "public"
    team_id: uuid.UUID | None = None
    start_at: datetime
    end_at: datetime
    publish: bool = True


class ViewerProgress(BaseModel):
    value: Decimal
    target: Decimal
    percent: Decimal
    rides: int
    completed: bool
    joined_at: datetime | None
    completed_at: datetime | None


class ChallengeOut(BaseModel):
    """One challenge as the server resolves the viewer's own relationship.

    ``state`` is the derived clock truth; ``status`` is the stored lifecycle.
    ``participant_count`` is ``None`` for a private challenge the viewer has
    not joined - the absence is itself the privacy answer.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str
    description: str | None
    metric: Metric
    target: Decimal
    points: int
    scope: Scope
    visibility: Visibility
    state: State
    status: Status
    team_id: uuid.UUID | None
    start_at: datetime
    end_at: datetime
    created_at: datetime
    is_creator: bool
    participant_count: int | None
    viewer_state: ViewerState | None
    viewer_progress: ViewerProgress
    can_join: bool
    can_leave: bool


class ChallengePage(BaseModel):
    items: list[ChallengeOut]
    total: int
    page: int
    page_size: int


class LeaderboardEntry(BaseModel):
    rank: int
    user_id: uuid.UUID
    username: str | None
    display_name: str | None
    avatar_url: str | None
    value: Decimal
    rides: int


class LeaderboardPage(BaseModel):
    items: list[LeaderboardEntry]
    total: int
    page: int
    page_size: int

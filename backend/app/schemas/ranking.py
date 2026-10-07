"""WS-RC ranking API schemas.

A ranking read carries no client-supplied numbers. ``value`` is a real
aggregate computed server-side from the rides and challenge-completion tables,
and ``viewer_rank`` / ``viewer_value`` are the same numbers for the caller.
``period`` reports the closed query window so a client never guesses a
"last week" from a local clock.
"""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel

Scope = Literal["global", "country", "city", "friends", "team", "category"]
Period = Literal["weekly", "monthly", "all_time"]
Metric = Literal["distance", "elevation", "rides", "training", "points"]


class RankingRow(BaseModel):
    rank: int
    user_id: uuid.UUID
    username: str | None
    display_name: str | None
    avatar_url: str | None
    value: Decimal
    rides: int


class PeriodBounds(BaseModel):
    start: datetime | None
    end: datetime | None


class RankingPage(BaseModel):
    items: list[RankingRow]
    total: int
    page: int
    page_size: int
    period: PeriodBounds
    viewer_rank: int | None
    viewer_value: Decimal | None

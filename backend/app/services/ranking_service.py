"""WS-RC: read-time leaderboard aggregation.

Server authority
----------------
Nothing is stored for a leaderboard and nothing is read back from a request.
Each read aggregates the ``rides`` table (and, for the ``points`` metric, the
``challenge_completions`` table) over the requested window and scope. Because
there are no leaderboard rows, there is nothing to go stale when friendships,
teams, privacy, blocks, or activity visibility change: the next read is the
next truth.

Qualifying activity is the single ``activity_integrity`` definition, and scope
is always decided by the server from stored facts - a request names a team or
a category, it never names the riders in it.

Visibility (docs/ranking-challenges.md 8)
-----------------------------------------
global/country/city/category  require ``activity_visibility == public``.
friends/team                  require ``activity_visibility != private``.
Deactivated/suspended riders are excluded. A rider blocked either way by the
viewer is dropped from the response without renumbering, so ranks may contain
gaps. Private riders disappear rather than being shown as anonymised rows.

Periods are computed in UTC from the server clock; there is no device-local
time anywhere in this module. A period edge is never visible to a client as
input - only the resolved ``start``/``end`` are reported back.
"""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import and_, exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.metrics import record_competition_event
from app.models.bike import Bike, BikeCategory
from app.models.challenge import Challenge, ChallengeCompletion, ChallengeStatus
from app.models.ride import Ride
from app.models.social import FriendRelationship, RelationshipStatus, SocialProfile, UserBlock
from app.models.team import TeamMembership, TeamMembershipStatus
from app.models.training import TrainingActivity
from app.models.user import User, UserProfile, UserStatus, Visibility
from app.services import activity_integrity

MAX_PAGE_SIZE = 100

SCOPES = {"global", "country", "city", "friends", "team", "category"}
PERIODS = {"weekly", "monthly", "all_time"}
METRICS = {"distance", "elevation", "rides", "training", "points"}


class RankingError(Exception):
    def __init__(self, code: str, message: str, status: int = 422):
        self.code = code
        self.message = message
        self.status = status
        super().__init__(message)


def period_bounds(period: str) -> tuple[datetime | None, datetime | None]:
    """Half-open weekday/month window in UTC, or ``(None, None)`` for all-time.

    weekly  Monday 00:00:00 UTC of the current ISO week -> the week after.
    monthly first instant of the calendar month UTC    -> the month after.
    """
    now = datetime.now(UTC)
    if period == "all_time":
        return None, None
    if period == "weekly":
        start = (now - timedelta(days=now.weekday())).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        return start, start + timedelta(days=7)
    start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return start, (start + timedelta(days=32)).replace(day=1)


def _profile_condition(
    user_id_expr, *, public_only: bool, country: str | None = None, city: str | None = None
):
    conds = [
        UserProfile.user_id == user_id_expr,
        (
            UserProfile.activity_visibility == Visibility.PUBLIC
            if public_only
            else UserProfile.activity_visibility != Visibility.PRIVATE
        ),
    ]
    if country is not None:
        conds.append(UserProfile.country == country)
    if city is not None:
        conds.append(UserProfile.city == city)
    return exists().where(*conds)


def _active_user_condition(user_id_expr):
    return exists().where(
        and_(
            User.id == user_id_expr,
            User.deleted_at.is_(None),
            User.status == UserStatus.ACTIVE,
        )
    )


def _friends_condition(user_id_expr, viewer: uuid.UUID):
    return or_(
        user_id_expr == viewer,
        exists().where(
            and_(
                FriendRelationship.status == RelationshipStatus.ACCEPTED,
                or_(
                    and_(
                        FriendRelationship.user_a_id == user_id_expr,
                        FriendRelationship.user_b_id == viewer,
                    ),
                    and_(
                        FriendRelationship.user_a_id == viewer,
                        FriendRelationship.user_b_id == user_id_expr,
                    ),
                ),
            )
        ),
    )


def _team_condition(user_id_expr, team_id: uuid.UUID):
    return exists().where(
        and_(
            TeamMembership.team_id == team_id,
            TeamMembership.user_id == user_id_expr,
            TeamMembership.status == TeamMembershipStatus.ACTIVE,
        )
    )


def _category_condition(category: str):
    return exists().where(and_(Bike.id == Ride.bike_id, Bike.category == BikeCategory(category)))


def _blocked_condition(user_id_expr, viewer: uuid.UUID):
    return exists().where(
        or_(
            and_(UserBlock.blocker_user_id == viewer, UserBlock.blocked_user_id == user_id_expr),
            and_(UserBlock.blocker_user_id == user_id_expr, UserBlock.blocked_user_id == viewer),
        )
    )


async def rankings(
    db: AsyncSession,
    user: User,
    *,
    scope: str,
    period: str,
    metric: str,
    country: str | None = None,
    city: str | None = None,
    team_id: uuid.UUID | None = None,
    category: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[dict], int, tuple[datetime | None, datetime | None], int | None, Any]:
    if scope not in SCOPES:
        raise RankingError("INVALID_SCOPE", "Unknown ranking scope.")
    if period not in PERIODS:
        raise RankingError("INVALID_PERIOD", "Unknown ranking period.")
    if metric not in METRICS:
        raise RankingError("INVALID_METRIC", "Unknown ranking metric.")
    if scope == "country" and not country:
        raise RankingError("COUNTRY_REQUIRED", "A country scope needs a country code.")
    if scope == "city" and not city:
        raise RankingError("CITY_REQUIRED", "A city scope needs a city.")
    if scope == "team" and not team_id:
        raise RankingError("TEAM_REQUIRED", "A team scope needs a team.")
    if scope == "category" and not category:
        raise RankingError("CATEGORY_REQUIRED", "A category scope needs a category.")
    if scope == "category" and metric == "points":
        raise RankingError(
            "INVALID_COMBO", "Points are not ride-based; a category scope needs a ride metric."
        )

    viewer = user.id
    start, end = period_bounds(period)
    window_parts: list[Any] = [Ride.ended_at.is_not(None)]
    if start is not None:
        window_parts.append(Ride.ended_at >= start)
    if end is not None:
        window_parts.append(Ride.ended_at < end)
    window = and_(*window_parts)

    if scope == "team":
        assert team_id is not None
        membership = await db.execute(
            select(TeamMembership.id)
            .where(
                TeamMembership.team_id == team_id,
                TeamMembership.user_id == viewer,
                TeamMembership.status == TeamMembershipStatus.ACTIVE,
            )
            .limit(1)
        )
        if membership.first() is None:
            raise RankingError("NOT_TEAM_MEMBER", "The team leaderboard is for members only.", 403)

    if scope in ("global", "country", "city", "category"):
        public_only = True
    else:
        public_only = False

    base_conditions = [window]
    if scope == "category":
        assert category is not None
        base_conditions.append(_category_condition(category))

    rider_conditions = [
        _profile_condition(
            Ride.user_id,
            public_only=public_only,
            country=country if scope == "country" else None,
            city=city if scope == "city" else None,
        ),
        _active_user_condition(Ride.user_id),
    ]
    if scope == "friends":
        rider_conditions.append(_friends_condition(Ride.user_id, viewer))
    if scope == "team":
        assert team_id is not None
        rider_conditions.append(_team_condition(Ride.user_id, team_id))

    stmt: Any
    if metric in ("distance", "elevation", "rides", "training"):
        conditions = [*base_conditions, *rider_conditions, activity_integrity.QUALIFYING_RIDE]
        value: Any
        if metric == "distance":
            value = func.sum(Ride.distance_m)
        elif metric == "elevation":
            value = func.sum(Ride.elevation_gain_m)
        elif metric == "rides":
            value = func.count(Ride.id)
        else:
            value = func.count(func.distinct(TrainingActivity.id))
        rides_expr = func.count(func.distinct(Ride.id))
        if metric == "training":
            from_clause = Ride.__table__.join(
                TrainingActivity.__table__, TrainingActivity.ride_id == Ride.id
            )
            stmt = (
                select(
                    Ride.user_id.label("user_id"), value.label("value"), rides_expr.label("rides")
                )
                .select_from(from_clause)
                .where(*conditions)
                .group_by(Ride.user_id)
            )
        else:
            stmt = (
                select(
                    Ride.user_id.label("user_id"), value.label("value"), rides_expr.label("rides")
                )
                .select_from(Ride)
                .where(*conditions)
                .group_by(Ride.user_id)
            )
    else:
        # points: sum completion awards, excluding cancelled challenges.
        conditions = [
            Challenge.status != ChallengeStatus.CANCELLED,
        ]
        if start is not None:
            conditions.append(ChallengeCompletion.completed_at >= start)
            conditions.append(ChallengeCompletion.completed_at < end)
        conditions.append(_profile_condition(ChallengeCompletion.user_id, public_only=public_only))
        conditions.append(_active_user_condition(ChallengeCompletion.user_id))
        if scope == "friends":
            conditions.append(_friends_condition(ChallengeCompletion.user_id, viewer))
        if scope == "team":
            assert team_id is not None
            conditions.append(_team_condition(ChallengeCompletion.user_id, team_id))
        stmt = (
            select(
                ChallengeCompletion.user_id.label("user_id"),
                func.sum(ChallengeCompletion.points_awarded).label("value"),
                func.count(ChallengeCompletion.id).label("rides"),
            )
            .join(Challenge, Challenge.id == ChallengeCompletion.challenge_id)
            .where(*conditions)
            .group_by(ChallengeCompletion.user_id)
        )

    base = stmt.subquery("ranked_base")
    ranked = select(
        base.c.user_id.label("user_id"),
        base.c.value.label("value"),
        base.c.rides.label("rides"),
        func.rank().over(order_by=[base.c.value.desc()]).label("rank"),
    ).subquery("ranked")

    hidden = _blocked_condition(ranked.c.user_id, viewer)
    total = (await db.execute(select(func.count()).select_from(ranked).where(~hidden))).scalar_one()

    page_size = min(page_size, MAX_PAGE_SIZE)
    rows = (
        (
            await db.execute(
                select(
                    ranked.c.rank.label("rank"),
                    ranked.c.user_id.label("user_id"),
                    ranked.c.value.label("value"),
                    ranked.c.rides.label("rides"),
                    SocialProfile.username.label("username"),
                    func.coalesce(SocialProfile.display_name, UserProfile.display_name).label(
                        "display_name"
                    ),
                    SocialProfile.avatar_url.label("avatar_url"),
                )
                .select_from(ranked)
                .outerjoin(SocialProfile, SocialProfile.user_id == ranked.c.user_id)
                .outerjoin(UserProfile, UserProfile.user_id == ranked.c.user_id)
                .where(~hidden)
                .order_by(ranked.c.value.desc(), ranked.c.user_id.asc())
                .limit(page_size)
                .offset((page - 1) * page_size)
            )
        )
        .mappings()
        .all()
    )
    items = [dict(r) for r in rows]

    viewer_row = (
        await db.execute(select(ranked.c.rank, ranked.c.value).where(ranked.c.user_id == viewer))
    ).first()
    viewer_rank: int | None = viewer_row[0] if viewer_row else None
    viewer_value: Any = viewer_row[1] if viewer_row else None

    record_competition_event(event="rankings_query", outcome="ok")
    return items, total, (start, end), viewer_rank, viewer_value

"""Shared arrangements for the WS-RC tests.

Not collected: the module name carries no ``test_`` prefix, so pytest ignores
it. Everything here either goes through the public API or seeds rows the API
deliberately cannot produce (rides with fabricated totals, tombstones, blocks).
Nothing here writes a score, rank, or progress value - those come from the same
server-side recompute paths the product code uses.
"""

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import update

from app.models.ride import IntegrityStatus, Ride, RideStatus
from app.models.social import UserBlock
from app.models.training import TrainingActivity
from app.models.user import User, UserStatus

AUTH = "/api/v1/auth"
PROFILE = "/api/v1/profile"
BIKES = "/api/v1/bikes"
SOCIAL = "/api/v1/social"
RIDES = "/api/v1/rides"
TEAMS = "/api/v1/teams"
CHALLENGES = "/api/v1/challenges"
RANKINGS = "/api/v1/rankings"

_PASSWORD = "StrongPass123"


def _reg(tag: str, email: str | None = None) -> dict:
    return {
        "email": email or f"{tag}@example.com",
        "password": _PASSWORD,
        "password_confirm": _PASSWORD,
        "display_name": f"Rider {tag.title()}",
    }


async def user(client, tag: str, email: str | None = None):
    """Register + login + touch /social/profile/me (creates the projection)."""
    r = await client.post(f"{AUTH}/register", json=_reg(tag, email))
    assert r.status_code == 201, r.text
    r = await client.post(
        f"{AUTH}/login",
        json={"email": email or f"{tag}@example.com", "password": _PASSWORD},
    )
    assert r.status_code == 200, r.text
    headers = {"Authorization": f"Bearer {r.json()['access_token']}"}
    me = await client.get(f"{SOCIAL}/profile/me", headers=headers)
    assert me.status_code == 200, me.text
    return headers, me.json()["user_id"]


async def set_profile(client, headers, **fields) -> None:
    r = await client.patch(PROFILE, json=fields, headers=headers)
    assert r.status_code == 200, r.text


async def bike(client, headers, category: str = "road") -> str:
    r = await client.post(BIKES, json={"name": "Test bike", "category": category}, headers=headers)
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def make_friends(client, headers_a, headers_b) -> None:
    """Send + accept one friend request: A requests B, B accepts."""
    me = await client.get(f"{SOCIAL}/profile/me", headers=headers_b)
    b_id = me.json()["user_id"]
    r = await client.post(f"{SOCIAL}/friend-requests", json={"user_id": b_id}, headers=headers_a)
    assert r.status_code == 201, r.text
    r = await client.post(f"{SOCIAL}/friend-requests/{r.json()['id']}/accept", headers=headers_b)
    assert r.status_code == 200, r.text


async def make_team(client, headers_owner, headers_member) -> str:
    """Public team with the owner and one more active member."""
    r = await client.post(TEAMS, json={"name": "Test team"}, headers=headers_owner)
    assert r.status_code == 201, r.text
    team_id = r.json()["id"]
    me = await client.get(f"{SOCIAL}/profile/me", headers=headers_member)
    member_id = me.json()["user_id"]
    inv = await client.post(
        f"{TEAMS}/{team_id}/invitations", json={"user_id": member_id}, headers=headers_owner
    )
    assert inv.status_code == 201, inv.text
    acc = await client.post(
        f"{TEAMS}/invitations/{inv.json()['id']}/accept", headers=headers_member
    )
    assert acc.status_code == 200, acc.text
    return team_id


async def seed_ride(
    factory,
    user_id,
    bike_id,
    *,
    distance_m=0,
    elevation_gain_m=0,
    ended_at=None,
) -> str:
    """A completed ride with fabricated totals, straight into the source table.

    The ride is labelled a WS-AC ``ACCEPTED``/``v1`` fixture: these rows stand
    in for rides the server itself finalised from accepted points, which is
    what the competition engines are allowed to assume. Seeding them does not
    invent a verdict any more than fabricating totals does - it names the
    provenance the fixture already claimed.
    """
    ended_at = ended_at or datetime.now(UTC)
    started_at = ended_at - timedelta(minutes=45)
    ride_id = uuid.uuid4()
    async with factory() as s:
        s.add(
            Ride(
                id=ride_id,
                user_id=user_id,
                bike_id=bike_id,
                client_ride_uuid=uuid.uuid4(),
                status=RideStatus.COMPLETED,
                started_at=started_at,
                ended_at=ended_at,
                elapsed_seconds=2700,
                moving_seconds=2520,
                distance_m=Decimal(str(distance_m)),
                elevation_gain_m=Decimal(str(elevation_gain_m)),
                elevation_loss_m=Decimal(0),
                average_speed_m_s=Decimal(0),
                max_speed_m_s=Decimal(0),
                integrity_status=IntegrityStatus.ACCEPTED,
                integrity_calculation_version="v1",
                integrity_rules_triggered=[],
                integrity_evaluated_at=ended_at,
                created_at=started_at,
                updated_at=ended_at,
            )
        )
        await s.commit()
    return ride_id


async def seed_integrity_ride(
    factory,
    user_id,
    bike_id,
    *,
    status,
    rules=(),
    distance_m=0,
    elevation_gain_m=0,
    ended_at=None,
) -> str:
    """A completed ride carrying a non-accepted WS-AC verdict.

    The API deliberately cannot produce a rejected/suspicious verdict (the
    engine rejects impossible input before it can be stored), so these rows
    are seeded directly - the same posture as the fabricated totals above.
    They exist to prove the *consumers* exclude the activity correctly.
    """
    ended_at = ended_at or datetime.now(UTC)
    started_at = ended_at - timedelta(minutes=45)
    ride_id = uuid.uuid4()
    async with factory() as s:
        s.add(
            Ride(
                id=ride_id,
                user_id=user_id,
                bike_id=bike_id,
                client_ride_uuid=uuid.uuid4(),
                status=RideStatus.COMPLETED,
                started_at=started_at,
                ended_at=ended_at,
                elapsed_seconds=2700,
                moving_seconds=2520,
                distance_m=Decimal(str(distance_m)),
                elevation_gain_m=Decimal(str(elevation_gain_m)),
                elevation_loss_m=Decimal(0),
                average_speed_m_s=Decimal(0),
                max_speed_m_s=Decimal(0),
                integrity_status=status,
                integrity_calculation_version="v1",
                integrity_rules_triggered=list(rules),
                integrity_evaluated_at=ended_at,
                created_at=started_at,
                updated_at=ended_at,
            )
        )
        await s.commit()
    return ride_id


async def seed_training(factory, ride_id, user_id, *, ended_at=None) -> None:
    """A TrainingActivity row for a ride, so the training metric has a count."""
    ended_at = ended_at or datetime.now(UTC)
    started_at = ended_at - timedelta(minutes=45)
    async with factory() as s:
        s.add(
            TrainingActivity(
                user_id=user_id,
                ride_id=ride_id,
                local_date=started_at.date(),
                started_at=started_at,
                ended_at=ended_at,
                analysis_version="test",
                created_at=started_at,
                updated_at=ended_at,
            )
        )
        await s.commit()


async def seed_block(factory, blocker_id, blocked_id) -> None:
    async with factory() as s:
        s.add(
            UserBlock(
                blocker_user_id=blocker_id,
                blocked_user_id=blocked_id,
                created_at=datetime.now(UTC),
            )
        )
        await s.commit()


async def tombstone(factory, user_id) -> None:
    """Deactivate a user the way account deletion does: tombstone + status."""
    now = datetime.now(UTC)
    async with factory() as s:
        await s.execute(
            update(User)
            .where(User.id == user_id)
            .values(status=UserStatus.DEACTIVATED, deleted_at=now)
        )
        await s.commit()


async def count_rows(factory, model_cls, **filters) -> int:
    from sqlalchemy import func, select

    conditions = [getattr(model_cls, k) == v for k, v in filters.items()]
    async with factory() as s:
        row = await s.execute(select(func.count()).select_from(model_cls).where(*conditions))
    return row.scalar_one()

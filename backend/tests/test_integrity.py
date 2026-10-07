"""WS-AC tests: the deterministic activity-integrity engine.

Three layers, matching the design in docs/activity-integrity.md:

1. RULES   - ``evaluate`` is pure, total and deterministic over stored facts.
   Every v1 rule is exercised by a fabricated Ride + Observation list, with no
   database: the engine is a function, and the function is tested as one.
2. INTEGRATION - the verdict lands atomically on the finish path, the read-only
   surface shows the owner their status, and ranking + challenge aggregation
   exclude anything that is not ``ACCEPTED`` (the single QUALIFYING_RIDE gate).
3. SECURITY - a client can never name its own verdict; the response surface
   never leaks rules, evidence or GPS.

Rides that reach REJECTED/SUSPICIOUS are deliberately *unreachable* through the
public API (the engine rejects impossible input before it can be stored), so
those fixtures are seeded straight into the table, exactly like the fabricated
totals in ``_competition_helpers`` - the consumers are what is under test.
"""

import json
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.models.challenge import ChallengeCompletion
from app.models.ride import IntegrityStatus, Ride, RideStatus
from app.services import activity_integrity
from app.services.gps_engine import SPEED_MAX_M_S, Observation
from tests._competition_helpers import (
    CHALLENGES,
    RANKINGS,
    RIDES,
    bike,
    count_rows,
    seed_integrity_ride,
    seed_ride,
    set_profile,
    user,
)

_NOW = datetime.now(UTC)


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


def _ride(**kw) -> Ride:
    """An in-memory completed Ride; nothing here touches a database."""
    base = {
        "id": uuid.uuid4(),
        "user_id": uuid.uuid4(),
        "bike_id": uuid.uuid4(),
        "client_ride_uuid": uuid.uuid4(),
        "status": RideStatus.COMPLETED,
        "started_at": _NOW - timedelta(minutes=45),
        "ended_at": _NOW,
        "created_at": _NOW - timedelta(minutes=45),
        "updated_at": _NOW,
        "max_speed_m_s": Decimal(0),
    }
    base.update(kw)
    return Ride(**base)


def _obs(seq, lat, lon, t) -> Observation:
    return Observation(seq=seq, lat=lat, lon=lon, recorded_at=t, alt=100.0, accuracy=5.0)


def _valid_points(n=3) -> list[Observation]:
    base = _NOW.timestamp()
    return [_obs(i, 33.0, -6.0 + i * 0.00009, base + i * 5.0) for i in range(n)]


async def _create_user(client, tag, visibility=None):
    h, uid = await user(client, tag)
    if visibility:
        await set_profile(client, h, activity_visibility=visibility)
    b = await bike(client, h)
    return h, uid, b


def _api_points(n=3) -> list[dict]:
    now = datetime.now(UTC)
    return [
        {
            "client_point_uuid": str(uuid.uuid4()),
            "seq": i,
            "lat": 33.0,
            "lon": -6.0 + i * 0.00009,
            "recorded_at": (now + timedelta(seconds=i * 5)).isoformat(),
        }
        for i in range(n)
    ]


async def _ride_helper(client, headers, bike_id, points):
    ride = (
        await client.post(
            RIDES,
            json={"bike_id": bike_id, "client_ride_uuid": str(uuid.uuid4())},
            headers=headers,
        )
    ).json()
    assert ride.get("integrity_status") is None, "an in-progress ride has no verdict yet"
    if points is not None:
        assert (
            await client.post(
                f"{RIDES}/{ride['id']}/points",
                json={"points": points},
                headers=headers,
            )
        ).status_code == 200
    return await client.post(f"{RIDES}/{ride['id']}/finish", headers=headers)


# ---------------------------------------------------------------------------
# 1. Rules (pure, no database)
# ---------------------------------------------------------------------------


def test_evaluate_accepts_a_healthy_ride():
    result = activity_integrity.evaluate(_ride(), _valid_points())
    assert result.status == IntegrityStatus.ACCEPTED
    assert result.eligible is True
    assert result.rules_triggered == []
    assert result.calculation_version == "v1"


def test_evaluate_is_deterministic():
    ride, points = _ride(), _valid_points()
    a = activity_integrity.evaluate(ride, points)
    b = activity_integrity.evaluate(ride, points)
    assert a.status == b.status
    assert a.rules_triggered == b.rules_triggered


def test_reject_rule_coordinate_out_of_bounds():
    bad = [_obs(0, 91.0, 0.0, _NOW.timestamp())]
    result = activity_integrity.evaluate(_ride(), bad)
    assert result.status == IntegrityStatus.REJECTED
    assert result.eligible is False
    assert result.rules_triggered == [activity_integrity.GPS_COORDINATE_INVALID]


def test_reject_rule_seq_out_of_order():
    base = _NOW.timestamp()
    points = [
        _obs(0, 33.0, -6.0, base),
        _obs(2, 33.0, -6.0, base + 5),
        _obs(1, 33.0, -6.0, base + 10),
    ]
    result = activity_integrity.evaluate(_ride(), points)
    assert result.status == IntegrityStatus.REJECTED
    assert result.rules_triggered == [activity_integrity.GPS_POINT_ORDER_INVALID]


def test_reject_rule_time_in_the_past():
    base = _NOW.timestamp()
    points = [
        _obs(0, 33.0, -6.0, base),
        _obs(1, 33.0, -6.0, base + 5),
        _obs(2, 33.0, -6.0, base - 1),
    ]
    result = activity_integrity.evaluate(_ride(), points)
    assert result.status == IntegrityStatus.REJECTED
    assert result.rules_triggered == [activity_integrity.GPS_TIME_SEQUENCE_INVALID]


def test_reject_rule_implausible_speed():
    rid = _ride(max_speed_m_s=Decimal(str(SPEED_MAX_M_S + 1)))
    result = activity_integrity.evaluate(rid, _valid_points())
    assert result.status == IntegrityStatus.REJECTED
    assert result.rules_triggered == [activity_integrity.GPS_SPEED_IMPLAUSIBLE]


def test_suspicious_rule_no_accepted_points():
    result = activity_integrity.evaluate(_ride(), [])
    assert result.status == IntegrityStatus.SUSPICIOUS
    assert result.eligible is False
    assert result.rules_triggered == [activity_integrity.RIDE_NO_ACCEPTED_POINTS]


def test_suspicious_rule_distance_mismatch():
    points = _valid_points()
    recomputed = Decimal(str(activity_integrity.gps_engine.recompute(points)["distance_m"]))
    result = activity_integrity.evaluate(
        _ride(), points, pre_finalize_distance_m=recomputed + Decimal("50.00")
    )
    assert result.status == IntegrityStatus.SUSPICIOUS
    assert result.rules_triggered == [activity_integrity.GPS_DISTANCE_MISMATCH]


def test_distance_mismatch_tolerance_accepts_rounding():
    points = _valid_points()
    recomputed = Decimal(str(activity_integrity.gps_engine.recompute(points)["distance_m"]))
    result = activity_integrity.evaluate(
        _ride(), points, pre_finalize_distance_m=recomputed - Decimal("0.01")
    )
    assert result.status == IntegrityStatus.ACCEPTED
    assert result.rules_triggered == []


def test_severity_reject_wins_over_suspicious():
    bad = [_obs(0, 91.0, 0.0, _NOW.timestamp())]
    result = activity_integrity.evaluate(_ride(), bad, pre_finalize_distance_m=Decimal("9999.00"))
    assert result.status == IntegrityStatus.REJECTED
    assert activity_integrity.GPS_COORDINATE_INVALID in result.rules_triggered
    assert activity_integrity.GPS_DISTANCE_MISMATCH in result.rules_triggered


def test_all_rules_reported_in_documented_order():
    base = _NOW.timestamp()
    # Coordinate-invalid, seq-reordered AND time-regressing: all three reject
    # rules must fire, in the documented emission order.
    points = [
        _obs(0, 91.0, 0.0, base),
        _obs(2, 91.0, 0.0, base + 5),
        _obs(1, 91.0, 0.0, base + 2),
    ]
    result = activity_integrity.evaluate(_ride(), points)
    assert result.rules_triggered == [
        activity_integrity.GPS_COORDINATE_INVALID,
        activity_integrity.GPS_POINT_ORDER_INVALID,
        activity_integrity.GPS_TIME_SEQUENCE_INVALID,
    ]


# ---------------------------------------------------------------------------
# 2. Integration: finish-path verdict + competition exclusion
# ---------------------------------------------------------------------------


async def test_completed_ride_is_accepted_and_ranked(client, db_session_factory):
    h_a, _, b = await _create_user(client, "a", visibility="public")
    await _create_user(client, "x", visibility="public")

    done = await _ride_helper(client, h_a, b, _api_points())
    assert done.status_code == 200, done.text
    assert done.json()["integrity_status"] == "accepted"

    board = (await client.get(f"{RANKINGS}?scope=global&metric=distance", headers=h_a)).json()
    assert float(board["viewer_value"]) > 0
    assert board["viewer_rank"] == 1


async def test_no_points_ride_is_suspicious_and_excluded(client, db_session_factory):
    h_a, _, b = await _create_user(client, "a", visibility="public")
    await _create_user(client, "x", visibility="public")

    done = await _ride_helper(client, h_a, b, None)
    assert done.status_code == 200, done.text
    assert done.json()["integrity_status"] == "suspicious"

    detail = (await client.get(f"{RIDES}/{done.json()['id']}", headers=h_a)).json()
    assert detail["integrity_status"] == "suspicious"

    board = (await client.get(f"{RANKINGS}?scope=global&metric=distance", headers=h_a)).json()
    assert board["total"] == 0
    assert board["viewer_rank"] is None


async def test_rejected_and_suspicious_rides_are_excluded_from_rankings(client, db_session_factory):
    h_a, uid, b = await _create_user(client, "a", visibility="public")
    await _create_user(client, "x", visibility="public")

    await seed_ride(db_session_factory, uid, b, distance_m=1000)
    await seed_integrity_ride(
        db_session_factory,
        uid,
        b,
        status=IntegrityStatus.REJECTED,
        rules=[activity_integrity.GPS_COORDINATE_INVALID],
        distance_m=5000,
    )
    await seed_integrity_ride(
        db_session_factory,
        uid,
        b,
        status=IntegrityStatus.SUSPICIOUS,
        rules=[activity_integrity.RIDE_NO_ACCEPTED_POINTS],
        distance_m=7000,
    )

    board = (await client.get(f"{RANKINGS}?scope=global&metric=distance", headers=h_a)).json()
    assert float(board["viewer_value"]) == 1000.0
    assert board["viewer_rank"] == 1


async def test_challenge_progress_ignores_rejected_and_suspicious(client, db_session_factory):
    h_a, _, _ = await _create_user(client, "a")
    h_b, b_id, bb = await _create_user(client, "b")

    now = datetime.now(UTC)
    c = (
        await client.post(
            CHALLENGES,
            json={
                "title": "Integrity challenge",
                "metric": "distance",
                "target": "10000",
                "points": 100,
                "start_at": (now - timedelta(hours=1)).isoformat(),
                "end_at": (now + timedelta(days=2)).isoformat(),
            },
            headers=h_a,
        )
    ).json()
    assert (await client.post(f"{CHALLENGES}/{c['id']}/join", headers=h_b)).status_code == 200

    await seed_ride(db_session_factory, b_id, bb, distance_m=1000)
    await seed_integrity_ride(
        db_session_factory,
        b_id,
        bb,
        status=IntegrityStatus.REJECTED,
        rules=[activity_integrity.GPS_COORDINATE_INVALID],
        distance_m=5000,
    )
    await seed_integrity_ride(
        db_session_factory,
        b_id,
        bb,
        status=IntegrityStatus.SUSPICIOUS,
        rules=[activity_integrity.RIDE_NO_ACCEPTED_POINTS],
        distance_m=4000,
    )

    vp = (await client.get(f"{CHALLENGES}/{c['id']}", headers=h_b)).json()["viewer_progress"]
    assert float(vp["value"]) == 1000.0
    assert vp["rides"] == 1
    assert vp["completed"] is False
    assert (
        await count_rows(
            db_session_factory, ChallengeCompletion, challenge_id=uuid.UUID(c["id"]), user_id=b_id
        )
        == 0
    )


# ---------------------------------------------------------------------------
# 3. Security: no client authority, no leakage
# ---------------------------------------------------------------------------


async def test_client_cannot_declare_a_verdict(client):
    h_a, _, b = await _create_user(client, "a", visibility="public")

    created = await client.post(
        RIDES,
        json={
            "bike_id": b,
            "client_ride_uuid": str(uuid.uuid4()),
            "integrity_status": "accepted",
            "integrity_score": 100,
            "verified": True,
            "eligible_for_ranking": True,
            "eligible_for_challenges": True,
        },
        headers=h_a,
    )
    assert created.status_code == 201, created.text
    # A RECORDING ride has no server verdict; the smuggled one must be ignored.
    assert created.json()["integrity_status"] is None

    ride_id = created.json()["id"]
    pts = _api_points(1)
    pts[0]["integrity_status"] = "accepted"
    pts[0]["integrity_score"] = 99
    up = await client.post(
        f"{RIDES}/{ride_id}/points",
        json={"points": pts, "integrity_status": "rejected"},
        headers=h_a,
    )
    assert up.status_code == 200, up.text

    fin = await client.post(f"{RIDES}/{ride_id}/finish", headers=h_a)
    assert fin.status_code == 200, fin.text
    # The verdict is the server's, never the client's.
    assert fin.json()["integrity_status"] == "accepted"


async def test_integrity_surface_leaks_no_rules_or_evidence(client, db_session_factory):
    h_a, uid, b = await _create_user(client, "a", visibility="public")
    await seed_integrity_ride(
        db_session_factory,
        uid,
        b,
        status=IntegrityStatus.REJECTED,
        rules=[activity_integrity.GPS_COORDINATE_INVALID],
        distance_m=5000,
    )
    done = await _ride_helper(client, h_a, b, _api_points(2))
    detail = (await client.get(f"{RIDES}/{done.json()['id']}", headers=h_a)).json()

    # The owner sees only the verdict; rules, version and evidence stay server-side.
    assert detail["integrity_status"] == "accepted"
    for key in (
        "integrity_rules_triggered",
        "integrity_calculation_version",
        "integrity_evaluated_at",
        "integrity_score",
        "evidence",
    ):
        assert key not in detail

    # Competitive surfaces never mention integrity at all - no verdict, no rules.
    for payload in [
        (await client.get(f"{RANKINGS}?scope=global&metric=distance", headers=h_a)).json(),
    ]:
        text = json.dumps(payload).lower()
        assert "integrity" not in text
        for key in ["lat", "lon", "gps", "location", "email", "token"]:
            assert key not in text


async def test_verdict_is_stable_across_finish_attempts(client):
    h_a, _, b = await _create_user(client, "a", visibility="public")
    done = await _ride_helper(client, h_a, b, _api_points())
    assert done.status_code == 200
    assert done.json()["integrity_status"] == "accepted"

    again = await client.post(f"{RIDES}/{done.json()['id']}/finish", headers=h_a)
    assert again.status_code == 409

    detail = (await client.get(f"{RIDES}/{done.json()['id']}", headers=h_a)).json()
    assert detail["integrity_status"] == "accepted"

"""Challenges tests (WS-RC): lifecycle, membership, recomputed progress.

Progress is never posted: it is recomputed from the ``rides`` table on read
(``_sync_member``), so most tests seed rides directly and then read the
challenge back - exactly the self-healing path a real client exercises after a
failed hook. One test drives a ride through the real ``/complete`` transition
to prove ``on_ride_completed`` fires in production order.
"""

import uuid
from datetime import UTC, datetime, timedelta

from app.models.challenge import ChallengeCompletion, ChallengeProgressEvent
from tests._competition_helpers import (
    CHALLENGES,
    RIDES,
    bike,
    count_rows,
    make_friends,
    make_team,
    seed_ride,
    set_profile,
    user,
)


def _now() -> datetime:
    return datetime.now(UTC)


def _challenge(
    metric="distance", target="1000", points=100, *, past_hours=1, future_days=2, **kw
) -> dict:
    now = _now()
    window = {
        "start_at": (now - timedelta(hours=past_hours)).isoformat(),
        "end_at": (now + timedelta(days=future_days)).isoformat(),
    }
    window.update({k: kw.pop(k) for k in ("start_at", "end_at") if k in kw})
    return {
        "title": "Test challenge",
        "metric": metric,
        "target": target,
        "points": points,
        **window,
        **kw,
    }


async def _create(client, headers, **kw) -> dict:
    r = await client.post(CHALLENGES, json=_challenge(**kw), headers=headers)
    assert r.status_code == 201, r.text
    return r.json()


async def _join(client, headers, challenge_id) -> int:
    r = await client.post(f"{CHALLENGES}/{challenge_id}/join", headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "joined"
    return r.status_code


async def _progress(client, headers, challenge_id) -> dict:
    r = await client.get(f"{CHALLENGES}/{challenge_id}", headers=headers)
    assert r.status_code == 200, r.text
    return r.json()["viewer_progress"]


async def test_lifecycle_draft_to_publish(client):
    h_a, _ = await user(client, "a")
    now = _now()
    draft = await _create(
        client,
        h_a,
        publish=False,
        start_at=(now + timedelta(hours=1)).isoformat(),
        end_at=(now + timedelta(days=2)).isoformat(),
    )
    assert draft["status"] == "draft"
    assert draft["state"] == "draft"
    assert draft["is_creator"] is True
    assert draft["can_join"] is False

    published = await client.post(f"{CHALLENGES}/{draft['id']}/publish", headers=h_a)
    assert published.status_code == 200, published.text
    body = published.json()
    assert body["state"] == "scheduled"
    assert body["status"] == "scheduled"
    assert body["can_join"] is True


async def test_progress_is_recomputed_and_awarded_once(client, db_session_factory):
    h_a, _ = await user(client, "a")
    h_b, b_id = await user(client, "b")
    c = await _create(client, h_a, target="3000")
    await _join(client, h_b, c["id"])
    bike_b = await bike(client, h_b)

    await seed_ride(db_session_factory, b_id, bike_b, distance_m=1200)
    vp = await _progress(client, h_b, c["id"])
    assert float(vp["value"]) == 1200.0
    assert vp["rides"] == 1
    assert float(vp["percent"]) == 40.0
    assert vp["completed"] is False

    await seed_ride(db_session_factory, b_id, bike_b, distance_m=1800)
    vp = await _progress(client, h_b, c["id"])
    assert float(vp["value"]) == 3000.0
    assert vp["completed"] is True

    # Awarded exactly once across repeated reads and additional rides, and a
    # completed participant is frozen: no further ledger rows and no movement.
    await seed_ride(db_session_factory, b_id, bike_b, distance_m=500)
    vp = await _progress(client, h_b, c["id"])
    assert float(vp["value"]) == 3000.0
    assert (
        await count_rows(
            db_session_factory, ChallengeCompletion, challenge_id=uuid.UUID(c["id"]), user_id=b_id
        )
        == 1
    )
    assert (
        await count_rows(
            db_session_factory,
            ChallengeProgressEvent,
            challenge_id=uuid.UUID(c["id"]),
            user_id=b_id,
        )
        == 2
    )


async def test_hook_updates_progress_on_a_real_completed_ride(client, db_session_factory):
    h_a, _ = await user(client, "a")
    h_b, _ = await user(client, "b")
    c = await _create(client, h_a, target="10")
    await _join(client, h_b, c["id"])
    bike_b = await bike(client, h_b)

    ride = (
        await client.post(
            RIDES,
            json={"bike_id": bike_b, "client_ride_uuid": str(uuid.uuid4())},
            headers=h_b,
        )
    ).json()
    now = _now()
    pts = [
        {
            "client_point_uuid": str(uuid.uuid4()),
            "seq": i,
            "lat": 33.0,
            "lon": -6.0 + i * 0.00009,
            "recorded_at": (now + timedelta(seconds=i * 5)).isoformat(),
        }
        for i in range(3)
    ]
    assert (
        await client.post(f"{RIDES}/{ride['id']}/points", json={"points": pts}, headers=h_b)
    ).status_code == 200
    done = await client.post(f"{RIDES}/{ride['id']}/finish", headers=h_b)
    assert done.status_code == 200, done.text

    vp = await _progress(client, h_b, c["id"])
    assert float(vp["value"]) == float(done.json()["summary"]["distance_m"])
    assert vp["rides"] == 1
    assert vp["completed"] is True


async def test_no_retroactive_credit_before_join(client, db_session_factory):
    h_a, _ = await user(client, "a")
    h_b, b_id = await user(client, "b")
    c = await _create(client, h_a, past_hours=3, target="1000")
    bike_b = await bike(client, h_b)

    # A ride finished before joining must not count.
    await seed_ride(
        db_session_factory,
        b_id,
        bike_b,
        distance_m=500,
        ended_at=_now() - timedelta(hours=2),
    )
    await _join(client, h_b, c["id"])
    assert float((await _progress(client, h_b, c["id"]))["value"]) == 0.0

    await seed_ride(db_session_factory, b_id, bike_b, distance_m=1000)
    assert float((await _progress(client, h_b, c["id"]))["value"]) == 1000.0


async def test_duplicate_join_is_idempotent_and_leave_zeroes_then_rejoin(
    client, db_session_factory
):
    h_a, _ = await user(client, "a")
    h_b, b_id = await user(client, "b")
    c = await _create(client, h_a, target="1000")
    await _join(client, h_b, c["id"])
    await _join(client, h_b, c["id"])  # the second join converges, not errors
    bike_b = await bike(client, h_b)

    await seed_ride(db_session_factory, b_id, bike_b, distance_m=999)
    assert float((await _progress(client, h_b, c["id"]))["value"]) == 999.0
    assert (
        await count_rows(
            db_session_factory, ChallengeCompletion, challenge_id=c["id"], user_id=b_id
        )
        == 0
    )

    left = await client.post(f"{CHALLENGES}/{c['id']}/leave", headers=h_b)
    assert left.status_code == 200, left.text
    body = (await client.get(f"{CHALLENGES}/{c['id']}", headers=h_b)).json()
    assert body["viewer_state"] == "left"
    assert float(body["viewer_progress"]["value"]) == 0.0

    await seed_ride(db_session_factory, b_id, bike_b, distance_m=1000)
    assert float((await _progress(client, h_b, c["id"]))["value"]) == 0.0

    await _join(client, h_b, c["id"])
    assert float((await _progress(client, h_b, c["id"]))["value"]) == 0.0
    assert (
        await count_rows(
            db_session_factory,
            ChallengeProgressEvent,
            challenge_id=uuid.UUID(c["id"]),
            user_id=b_id,
        )
        == 0
    )


async def test_private_challenge_hides_roster_until_joined(client):
    h_a, _ = await user(client, "a")
    h_b, _ = await user(client, "b")
    c = await _create(client, h_a, scope="global", visibility="private", target="5000")

    viewed = (await client.get(f"{CHALLENGES}/{c['id']}", headers=h_b)).json()
    assert viewed["participant_count"] is None
    assert viewed["can_join"] is True

    gated = await client.get(f"{CHALLENGES}/{c['id']}/leaderboard", headers=h_b)
    assert gated.status_code == 403, gated.text
    assert gated.json()["error"]["code"] == "CHALLENGE_PRIVATE"

    await _join(client, h_b, c["id"])
    private = (await client.get(f"{CHALLENGES}/{c['id']}", headers=h_b)).json()
    assert private["participant_count"] == 1
    assert (await client.get(f"{CHALLENGES}/{c['id']}/leaderboard", headers=h_b)).status_code == 200


async def test_cancel_closes_the_challenge(client):
    h_a, _ = await user(client, "a")
    h_b, _ = await user(client, "b")
    h_c, _ = await user(client, "c")
    c = await _create(client, h_a)
    await _join(client, h_b, c["id"])

    cancelled = await client.post(f"{CHALLENGES}/{c['id']}/cancel", headers=h_a)
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["state"] == "cancelled"

    again = await client.post(f"{CHALLENGES}/{c['id']}/cancel", headers=h_a)
    assert again.status_code == 409
    assert again.json()["error"]["code"] == "ALREADY_FINAL"

    refused = await client.post(f"{CHALLENGES}/{c['id']}/join", headers=h_c)
    assert refused.status_code == 409
    assert refused.json()["error"]["code"] == "CHALLENGE_CLOSED"


async def test_streak_metric_counts_consecutive_days(client, db_session_factory):
    h_a, _ = await user(client, "a")
    h_b, b_id = await user(client, "b")
    c = await _create(client, h_a, metric="streak", target="3", future_days=3)
    await _join(client, h_b, c["id"])
    bike_b = await bike(client, h_b)

    now = _now()
    for hours in (1, 26, 50):  # three consecutive future calendar days
        await seed_ride(
            db_session_factory,
            b_id,
            bike_b,
            distance_m=1000,
            ended_at=now + timedelta(hours=hours),
        )
    await seed_ride(
        db_session_factory,
        b_id,
        bike_b,
        distance_m=1000,
        ended_at=now + timedelta(hours=5 * 24),
    )
    vp = await _progress(client, h_b, c["id"])
    assert float(vp["value"]) == 3.0
    assert vp["completed"] is True


async def test_friends_scope_is_limited_to_friends(client):
    h_a, _ = await user(client, "a")
    h_b, _ = await user(client, "b")
    h_c, _ = await user(client, "c")
    await make_friends(client, h_a, h_b)
    c = await _create(client, h_a, scope="friends", target="1000")

    assert (await client.post(f"{CHALLENGES}/{c['id']}/join", headers=h_b)).status_code == 200

    outsider = await client.get(f"{CHALLENGES}/{c['id']}", headers=h_c)
    assert outsider.status_code == 404
    assert outsider.json()["error"]["code"] == "CHALLENGE_NOT_FOUND"


async def test_team_challenge_requires_a_member(client):
    h_a, _ = await user(client, "a")
    h_b, _ = await user(client, "b")
    h_d, _ = await user(client, "d")
    team_id = await make_team(client, h_a, h_b)
    c = await _create(client, h_a, scope="team", team_id=team_id, target="1000")

    assert (await client.post(f"{CHALLENGES}/{c['id']}/join", headers=h_b)).status_code == 200

    outsider = await client.get(f"{CHALLENGES}/{c['id']}", headers=h_d)
    assert outsider.status_code == 404

    non_member_create = await client.post(
        CHALLENGES,
        json=_challenge(scope="team", team_id=team_id),
        headers=h_d,
    )
    assert non_member_create.status_code == 403
    assert non_member_create.json()["error"]["code"] == "NOT_TEAM_MEMBER"


async def test_folder_challenge_leaderboard_orders_and_gaps(client, db_session_factory):
    h_a, a_id = await user(client, "a")
    h_b, b_id = await user(client, "b")
    h_c, c_id = await user(client, "c")
    for h in (h_a, h_b, h_c):
        await set_profile(client, h, activity_visibility="public")
    c = await _create(client, h_a, target="5000")
    ba = await bike(client, h_a)
    bb = await bike(client, h_b)
    bc = await bike(client, h_c)
    await _join(client, h_a, c["id"])
    await _join(client, h_b, c["id"])
    await _join(client, h_c, c["id"])
    await seed_ride(db_session_factory, a_id, ba, distance_m=3000)
    await seed_ride(db_session_factory, b_id, bb, distance_m=3000)
    await seed_ride(db_session_factory, c_id, bc, distance_m=1000)
    for h in (h_a, h_b, h_c):  # each read refreshes that member's cached progress
        await client.get(f"{CHALLENGES}/{c['id']}", headers=h)

    board = (await client.get(f"{CHALLENGES}/{c['id']}/leaderboard", headers=h_a)).json()
    assert board["total"] == 3
    a_row = _row_by(board, a_id)
    b_row = _row_by(board, b_id)
    c_row = _row_by(board, c_id)
    assert a_row["rank"] == b_row["rank"] == 1
    assert c_row["rank"] == 3
    assert float(a_row["value"]) == 3000.0
    assert float(c_row["value"]) == 1000.0


def _row_by(body, user_id):
    return next(r for r in body["items"] if r["user_id"] == user_id)

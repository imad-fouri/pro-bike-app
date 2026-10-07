"""Rankings API tests (WS-RC): scopes, periods, metrics, ties, visibility.

Rides are seeded straight into the source table with fabricated totals - the
ranking engine reads ``rides`` (and ``challenge_completions``) and nothing on
this page invents a score: every ``value`` below is an aggregate the server
computed from rows this test already knew about.
"""

from datetime import UTC, datetime, timedelta

from tests._competition_helpers import (
    RANKINGS,
    bike,
    make_friends,
    make_team,
    seed_block,
    seed_ride,
    seed_training,
    set_profile,
    tombstone,
    user,
)


def _now() -> datetime:
    return datetime.now(UTC)


def _row_by(body, user_id):
    return next(r for r in body["items"] if r["user_id"] == user_id)


async def test_global_weekly_distance_ranks_ties_and_gaps(client, db_session_factory):
    h_a, a_id = await user(client, "a")
    h_b, b_id = await user(client, "b")
    h_c, c_id = await user(client, "c")
    for h in (h_a, h_b, h_c):
        await set_profile(client, h, activity_visibility="public")
    ba = await bike(client, h_a)
    bb = await bike(client, h_b)
    bc = await bike(client, h_c)

    await seed_ride(db_session_factory, a_id, ba, distance_m=1000)
    await seed_ride(db_session_factory, a_id, ba, distance_m=500)
    await seed_ride(db_session_factory, b_id, bb, distance_m=1500)
    await seed_ride(db_session_factory, c_id, bc, distance_m=200)

    r = await client.get(f"{RANKINGS}?scope=global&period=weekly&metric=distance", headers=h_a)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] == 3
    a_row = _row_by(body, a_id)
    b_row = _row_by(body, b_id)
    c_row = _row_by(body, c_id)
    # Equal totals -> tied ranks; the next distinct rider must see a gap.
    assert a_row["rank"] == b_row["rank"] == 1
    assert c_row["rank"] == 3
    assert float(a_row["value"]) == 1500.0
    assert float(b_row["value"]) == 1500.0
    assert float(c_row["value"]) == 200.0
    assert body["viewer_rank"] == 1
    assert float(body["viewer_value"]) == 1500.0
    assert body["period"]["start"] is not None and body["period"]["end"] is not None
    wanted = {"rank", "user_id", "username", "display_name", "avatar_url", "value", "rides"}
    assert wanted == set(body["items"][0].keys())


async def test_weekly_vs_all_time_window(client, db_session_factory):
    h_a, a_id = await user(client, "a")
    await set_profile(client, h_a, activity_visibility="public")
    ba = await bike(client, h_a)
    now = _now()
    old = now - timedelta(days=40)
    await seed_ride(db_session_factory, a_id, ba, distance_m=100, ended_at=now)
    await seed_ride(db_session_factory, a_id, ba, distance_m=50, ended_at=old)

    weekly = (
        await client.get(f"{RANKINGS}?scope=global&period=weekly&metric=distance", headers=h_a)
    ).json()
    all_time = (
        await client.get(f"{RANKINGS}?scope=global&period=all_time&metric=distance", headers=h_a)
    ).json()
    assert float(_row_by(weekly, a_id)["value"]) == 100.0
    assert float(_row_by(all_time, a_id)["value"]) == 150.0
    assert _row_by(weekly, a_id)["rides"] == 1


async def test_country_and_city_scopes(client, db_session_factory):
    h_a, a_id = await user(client, "a")
    h_b, b_id = await user(client, "b")
    h_c, c_id = await user(client, "c")
    for h, kw in (
        (h_a, {"activity_visibility": "public", "country": "FR", "city": "Paris"}),
        (h_b, {"activity_visibility": "public", "country": "FR", "city": "Lyon"}),
        (h_c, {"activity_visibility": "public", "country": "ES"}),
    ):
        await set_profile(client, h, **kw)
    ba = await bike(client, h_a)
    bb = await bike(client, h_b)
    bc = await bike(client, h_c)
    await seed_ride(db_session_factory, a_id, ba, distance_m=100)
    await seed_ride(db_session_factory, b_id, bb, distance_m=200)
    await seed_ride(db_session_factory, c_id, bc, distance_m=400)

    board = (
        await client.get(f"{RANKINGS}?scope=country&country=FR&metric=distance", headers=h_a)
    ).json()
    assert board["total"] == 2
    assert {r["user_id"] for r in board["items"]} == {a_id, b_id}
    assert float(_row_by(board, a_id)["value"]) == 100.0

    paris = (
        await client.get(f"{RANKINGS}?scope=city&city=Paris&metric=distance", headers=h_a)
    ).json()
    assert paris["total"] == 1
    assert paris["items"][0]["user_id"] == a_id

    caf = (
        await client.get(f"{RANKINGS}?scope=country&country=ES&metric=distance", headers=h_c)
    ).json()
    assert caf["total"] == 1
    assert caf["items"][0]["user_id"] == c_id


async def test_category_scope_uses_the_bikes_category(client, db_session_factory):
    h_a, a_id = await user(client, "a")
    h_b, b_id = await user(client, "b")
    for h in (h_a, h_b):
        await set_profile(client, h, activity_visibility="public")
    gravel = await bike(client, h_a, category="gravel")
    road = await bike(client, h_b, category="road")
    await seed_ride(db_session_factory, a_id, gravel, distance_m=100)
    await seed_ride(db_session_factory, b_id, road, distance_m=200)

    board = (
        await client.get(f"{RANKINGS}?scope=category&category=gravel&metric=distance", headers=h_a)
    ).json()
    assert board["total"] == 1
    assert board["items"][0]["user_id"] == a_id
    assert float(board["items"][0]["value"]) == 100.0


async def test_friends_scope_includes_friends_only(client, db_session_factory):
    h_a, a_id = await user(client, "a")
    h_b, b_id = await user(client, "b")
    h_c, c_id = await user(client, "c")
    await make_friends(client, h_a, h_b)
    ba = await bike(client, h_a)
    bb = await bike(client, h_b)
    bc = await bike(client, h_c)
    await seed_ride(db_session_factory, a_id, ba, distance_m=100)
    await seed_ride(db_session_factory, b_id, bb, distance_m=50)
    await seed_ride(db_session_factory, c_id, bc, distance_m=300)

    board = (await client.get(f"{RANKINGS}?scope=friends&metric=distance", headers=h_a)).json()
    assert board["total"] == 2
    assert {r["user_id"] for r in board["items"]} == {a_id, b_id}


async def test_private_activity_hidden_from_global_and_friends(client, db_session_factory):
    h_a, a_id = await user(client, "a")
    h_b, b_id = await user(client, "b")
    h_c, c_id = await user(client, "c")
    await set_profile(client, h_a, activity_visibility="public")
    await set_profile(client, h_b, activity_visibility="private")
    await set_profile(client, h_c, activity_visibility="public")
    await make_friends(client, h_a, h_b)
    ba = await bike(client, h_a)
    bb = await bike(client, h_b)
    bc = await bike(client, h_c)
    await seed_ride(db_session_factory, a_id, ba, distance_m=100)
    await seed_ride(db_session_factory, b_id, bb, distance_m=200)
    await seed_ride(db_session_factory, c_id, bc, distance_m=300)

    board = (await client.get(f"{RANKINGS}?scope=global&metric=distance", headers=h_a)).json()
    assert board["total"] == 2
    assert {r["user_id"] for r in board["items"]} == {a_id, c_id}

    friends = (await client.get(f"{RANKINGS}?scope=friends&metric=distance", headers=h_a)).json()
    assert friends["total"] == 1
    assert friends["items"][0]["user_id"] == a_id


async def test_team_scope_for_members_only(client, db_session_factory):
    h_a, a_id = await user(client, "a")
    h_b, b_id = await user(client, "b")
    h_d, _ = await user(client, "d")
    team_id = await make_team(client, h_a, h_b)
    ba = await bike(client, h_a)
    bb = await bike(client, h_b)
    await seed_ride(db_session_factory, a_id, ba, distance_m=100)
    await seed_ride(db_session_factory, b_id, bb, distance_m=50)

    board = (
        await client.get(f"{RANKINGS}?scope=team&team_id={team_id}&metric=distance", headers=h_a)
    ).json()
    assert board["total"] == 2
    assert {r["user_id"] for r in board["items"]} == {a_id, b_id}

    refused = await client.get(
        f"{RANKINGS}?scope=team&team_id={team_id}&metric=distance", headers=h_d
    )
    assert refused.status_code == 403, refused.text
    assert refused.json()["error"]["code"] == "NOT_TEAM_MEMBER"


async def test_metrics_rides_and_training(client, db_session_factory):
    h_a, a_id = await user(client, "a")
    h_b, b_id = await user(client, "b")
    for h in (h_a, h_b):
        await set_profile(client, h, activity_visibility="public")
    ba = await bike(client, h_a)
    bb = await bike(client, h_b)
    r1 = await seed_ride(db_session_factory, a_id, ba, distance_m=100)
    r2 = await seed_ride(db_session_factory, a_id, ba, distance_m=50)
    r3 = await seed_ride(db_session_factory, b_id, bb, distance_m=30)
    for ride_id, uid in ((r1, a_id), (r2, a_id), (r3, b_id)):
        await seed_training(db_session_factory, ride_id, uid)

    rides = (await client.get(f"{RANKINGS}?scope=global&metric=rides", headers=h_a)).json()
    assert int(_row_by(rides, a_id)["value"]) == 2
    assert int(_row_by(rides, b_id)["value"]) == 1

    training = (await client.get(f"{RANKINGS}?scope=global&metric=training", headers=h_a)).json()
    assert int(_row_by(training, a_id)["value"]) == 2
    assert int(_row_by(training, b_id)["value"]) == 1


async def test_blocked_rider_is_dropped_both_ways(client, db_session_factory):
    h_a, a_id = await user(client, "a")
    h_b, b_id = await user(client, "b")
    for h in (h_a, h_b):
        await set_profile(client, h, activity_visibility="public")
    ba = await bike(client, h_a)
    bb = await bike(client, h_b)
    await seed_ride(db_session_factory, a_id, ba, distance_m=100)
    await seed_ride(db_session_factory, b_id, bb, distance_m=50)
    await seed_block(db_session_factory, a_id, b_id)

    from_a = (await client.get(f"{RANKINGS}?scope=global&metric=distance", headers=h_a)).json()
    assert from_a["total"] == 1
    assert from_a["items"][0]["user_id"] == a_id

    from_b = (await client.get(f"{RANKINGS}?scope=global&metric=distance", headers=h_b)).json()
    assert from_b["total"] == 1
    assert from_b["items"][0]["user_id"] == b_id


async def test_deactivated_rider_is_excluded(client, db_session_factory):
    h_a, a_id = await user(client, "a")
    h_b, b_id = await user(client, "b")
    for h in (h_a, h_b):
        await set_profile(client, h, activity_visibility="public")
    ba = await bike(client, h_a)
    bb = await bike(client, h_b)
    await seed_ride(db_session_factory, a_id, ba, distance_m=100)
    await seed_ride(db_session_factory, b_id, bb, distance_m=50)
    await tombstone(db_session_factory, a_id)

    board = (await client.get(f"{RANKINGS}?scope=global&metric=distance", headers=h_b)).json()
    assert board["total"] == 1
    assert board["items"][0]["user_id"] == b_id


async def test_pagination_and_viewer_rank(client, db_session_factory):
    seen = {}
    ids = {}
    for tag, value in (("a", 100), ("b", 200), ("c", 300)):
        h, uid = await user(client, tag)
        seen[tag] = h
        ids[uid] = value
        await set_profile(client, h, activity_visibility="public")
        b = await bike(client, h)
        await seed_ride(db_session_factory, uid, b, distance_m=value)

    page1 = (
        await client.get(f"{RANKINGS}?scope=global&metric=distance&page_size=2", headers=seen["b"])
    ).json()
    assert page1["total"] == 3
    assert len(page1["items"]) == 2
    assert page1["viewer_rank"] == 2
    assert float(page1["viewer_value"]) == 200.0

    page2 = (
        await client.get(
            f"{RANKINGS}?scope=global&metric=distance&page_size=2&page=2",
            headers=seen["b"],
        )
    ).json()
    assert len(page2["items"]) == 1
    assert page2["items"][0]["user_id"] in ids

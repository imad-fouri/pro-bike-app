"""WS-RC security tests: no client-submitted scores, idempotent joins,
privacy gates, and the structural guarantee that the API surface cannot smuggle
a number onto a leaderboard.

The pairs here are deliberately boring: the interesting property is that there
is no endpoint that would have something interesting to betray.
"""

import uuid
from datetime import UTC, datetime, timedelta

from tests._competition_helpers import (
    CHALLENGES,
    RANKINGS,
    bike,
    make_friends,
    seed_block,
    seed_ride,
    set_profile,
    user,
)


def _now() -> datetime:
    return datetime.now(UTC)


def _challenge(**kw) -> dict:
    now = _now()
    payload = {
        "title": "Security challenge",
        "metric": "distance",
        "target": "1000",
        "points": 100,
        "start_at": (now - timedelta(hours=1)).isoformat(),
        "end_at": (now + timedelta(days=2)).isoformat(),
    }
    payload.update(kw)
    return payload


async def test_ranking_requires_authentication(client):
    r = await client.get(f"{RANKINGS}?scope=global")
    assert r.status_code == 401


async def test_ranking_rejects_missing_scope_arguments(client):
    h_a, _ = await user(client, "a")
    assert (await client.get(f"{RANKINGS}?scope=global", headers=h_a)).status_code == 200

    no_scope = await client.get(f"{RANKINGS}?period=weekly", headers=h_a)
    assert no_scope.status_code == 422

    no_country = await client.get(f"{RANKINGS}?scope=country", headers=h_a)
    assert no_country.status_code == 422
    assert no_country.json()["error"]["code"] == "COUNTRY_REQUIRED"

    combo = await client.get(f"{RANKINGS}?scope=category&category=road&metric=points", headers=h_a)
    assert combo.status_code == 422
    assert combo.json()["error"]["code"] == "INVALID_COMBO"

    bad_scope = await client.get(f"{RANKINGS}?scope=astronaut", headers=h_a)
    assert bad_scope.status_code == 422


async def test_rankings_get_has_no_request_body(client):
    openapi = (await client.get("/openapi.json")).json()
    route = openapi["paths"]["/api/v1/rankings"]["get"]
    assert "requestBody" not in route


async def test_challenge_create_rejects_score_smuggling(client):
    h_a, _ = await user(client, "a")
    for extra in (
        {"progress": 9000},
        {"rank": 1},
        {"score": 99},
        {"user_id": str(uuid.uuid4())},
        {"points_awarded": 500},
    ):
        r = await client.post(CHALLENGES, json={**_challenge(), **extra}, headers=h_a)
        assert r.status_code == 422, extra


async def test_challenge_create_body_admits_only_target_intent(client):
    openapi = (await client.get("/openapi.json")).json()
    op = openapi["paths"]["/api/v1/challenges"]["post"]
    schema_ref = op["requestBody"]["content"]["application/json"]["schema"]
    schema = openapi["components"]["schemas"][schema_ref["$ref"].rsplit("/", 1)[-1]]
    props = set(schema["properties"])
    assert not (props & {"progress", "rank", "score", "value", "user_id", "points_awarded"})
    assert {"metric", "target", "points", "scope", "visibility", "start_at", "end_at"} <= props


async def test_join_has_no_number_to_post(client):
    h_a, _ = await user(client, "a")
    h_b, _ = await user(client, "b")
    c = (await client.post(CHALLENGES, json=_challenge(), headers=h_a)).json()

    # A body full of "scores" is accepted in the sense that it is ignored: the
    # endpoint's only input is the path id. Progress must still be flat.
    r = await client.post(
        f"{CHALLENGES}/{c['id']}/join",
        json={"progress": 99999, "rank": 1, "score": 777},
        headers=h_b,
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "joined"
    view = (await client.get(f"{CHALLENGES}/{c['id']}", headers=h_b)).json()
    assert float(view["viewer_progress"]["value"]) == 0.0
    assert view["viewer_progress"]["rides"] == 0


async def test_challenge_leaderboard_drops_blocked_rows_without_renumbering(
    client, db_session_factory
):
    h_a, a_id = await user(client, "a")
    h_b, b_id = await user(client, "b")
    h_c, c_id = await user(client, "c")
    for h in (h_a, h_b, h_c):
        await set_profile(client, h, activity_visibility="public")
    c = (await client.post(CHALLENGES, json=_challenge(target="5000"), headers=h_a)).json()
    ba = await bike(client, h_a)
    bb = await bike(client, h_b)
    bc = await bike(client, h_c)
    for h in (h_a, h_b, h_c):
        assert (await client.post(f"{CHALLENGES}/{c['id']}/join", headers=h)).status_code == 200
    await seed_ride(db_session_factory, a_id, ba, distance_m=5000)
    await seed_ride(db_session_factory, b_id, bb, distance_m=3000)
    await seed_ride(db_session_factory, c_id, bc, distance_m=2000)
    for h in (h_a, h_b, h_c):  # each read refreshes that member's cached progress
        await client.get(f"{CHALLENGES}/{c['id']}", headers=h)
    await seed_block(db_session_factory, a_id, b_id)

    from_a = (await client.get(f"{CHALLENGES}/{c['id']}/leaderboard", headers=h_a)).json()
    assert from_a["total"] == 2
    ranks_a = {r["user_id"]: r["rank"] for r in from_a["items"]}
    assert ranks_a[a_id] == 1
    # b's row disappears but c keeps its full-set rank: no renumbering.
    assert ranks_a[c_id] == 3

    from_c = (await client.get(f"{CHALLENGES}/{c['id']}/leaderboard", headers=h_c)).json()
    assert from_c["total"] == 3
    ranks = {r["user_id"]: r["rank"] for r in from_c["items"]}
    assert ranks[a_id] == 1
    assert ranks[b_id] == 2
    assert ranks[c_id] == 3


async def test_friends_challenge_is_not_probeable(client):
    h_a, _ = await user(client, "a")
    h_b, _ = await user(client, "b")
    h_c, _ = await user(client, "c")
    await make_friends(client, h_a, h_b)
    c = (await client.post(CHALLENGES, json=_challenge(scope="friends"), headers=h_a)).json()

    outsider = await client.get(f"{CHALLENGES}/{c['id']}", headers=h_c)
    assert outsider.status_code == 404
    assert outsider.json()["error"]["code"] == "CHALLENGE_NOT_FOUND"

    bogus = await client.get(f"{CHALLENGES}/{uuid.uuid4()}", headers=h_c)
    assert bogus.status_code == 404
    assert bogus.json()["error"]["code"] == "CHALLENGE_NOT_FOUND"

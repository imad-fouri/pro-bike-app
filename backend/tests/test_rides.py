"""Phase 4 ride API tests: lifecycle, ingest, sync, ownership, IDOR."""

import uuid
from datetime import UTC, datetime, timedelta

RIDES = "/api/v1/rides"
AUTH = "/api/v1/auth"
BIKES = "/api/v1/bikes"

A = {
    "email": "ra@example.com",
    "password": "StrongPass123",
    "password_confirm": "StrongPass123",
    "display_name": "Rider A",
}
B = {
    "email": "rb@example.com",
    "password": "StrongPass123",
    "password_confirm": "StrongPass123",
    "display_name": "Rider B",
}

LAT, LON = 33.0, -6.0
STEP = 0.00009  # ~10 m latitude
T0 = datetime(2026, 5, 1, 7, 0, tzinfo=UTC)


def pt(seq, dlat=0.0, sec=0, **kw):
    t = T0 + timedelta(seconds=sec if sec else seq * 5)
    return {
        "client_point_uuid": str(uuid.uuid4()),
        "seq": seq,
        "lat": LAT + dlat,
        "lon": LON,
        "recorded_at": t.isoformat(),
        **kw,
    }


async def _user(client, data):
    assert (await client.post(f"{AUTH}/register", json=data)).status_code == 201
    r = await client.post(
        f"{AUTH}/login", json={"email": data["email"], "password": data["password"]}
    )
    assert r.status_code == 200
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def _bike(client, headers, name="Road", category="road"):
    r = await client.post(BIKES, json={"name": name, "category": category}, headers=headers)
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def _ride(client, headers, bike_id, cuuid=None):
    cuuid = cuuid or str(uuid.uuid4())
    r = await client.post(
        RIDES, json={"bike_id": bike_id, "client_ride_uuid": cuuid}, headers=headers
    )
    return r


# --- creation ---
async def test_create_ride(client):
    h = await _user(client, A)
    bike = await _bike(client, h)
    r = await _ride(client, h, bike)
    assert r.status_code == 201, r.text
    assert r.json()["status"] == "recording"


async def test_create_idempotent_same_uuid(client):
    h = await _user(client, A)
    bike = await _bike(client, h)
    cuuid = str(uuid.uuid4())
    r1 = (await _ride(client, h, bike, cuuid)).json()
    r2 = await _ride(client, h, bike, cuuid)
    assert r2.status_code == 201 and r2.json()["id"] == r1["id"]


async def test_create_foreign_bike_rejected(client):
    ha = await _user(client, A)
    hb = await _user(client, B)
    bike_a = await _bike(client, ha)
    r = await _ride(client, hb, bike_a)
    assert r.status_code == 404


async def test_create_archived_bike_rejected(client):
    h = await _user(client, A)
    bike = await _bike(client, h)
    assert (await client.post(f"{BIKES}/{bike}/archive", headers=h)).status_code == 200
    assert (await _ride(client, h, bike)).status_code == 409


async def test_create_unauthenticated(client):
    r = await client.post(
        RIDES, json={"bike_id": str(uuid.uuid4()), "client_ride_uuid": str(uuid.uuid4())}
    )
    assert r.status_code == 401


# --- ingest + summary ---
async def test_points_chunk_and_summary(client):
    h = await _user(client, A)
    ride = (await _ride(client, h, await _bike(client, h))).json()
    chunk = [pt(0), pt(1, dlat=STEP), pt(2, dlat=2 * STEP)]
    r = await client.post(f"{RIDES}/{ride['id']}/points", json={"points": chunk}, headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["accepted"] == 3 and body["rejected"] == [] and body["duplicates"] == 0
    assert (
        float(body["summary"]["distance_m"]) == abs(20.0)
        or 19.0 < float(body["summary"]["distance_m"]) < 21.0
    )


async def test_chunk_retry_idempotent(client):
    h = await _user(client, A)
    ride = (await _ride(client, h, await _bike(client, h))).json()
    chunk = [pt(0), pt(1, dlat=STEP)]
    first = (
        await client.post(f"{RIDES}/{ride['id']}/points", json={"points": chunk}, headers=h)
    ).json()
    # Same logical points, NEW uuids but same seq → duplicates, no extra distance.
    retry = [dict(p, client_point_uuid=str(uuid.uuid4())) for p in chunk]
    second = (
        await client.post(f"{RIDES}/{ride['id']}/points", json={"points": retry}, headers=h)
    ).json()
    assert second["duplicates"] == 2 and second["accepted"] == 0
    assert second["summary"]["distance_m"] == first["summary"]["distance_m"]


async def test_partial_resume(client):
    h = await _user(client, A)
    ride = (await _ride(client, h, await _bike(client, h))).json()
    c1 = await client.post(
        f"{RIDES}/{ride['id']}/points", json={"points": [pt(0), pt(1, dlat=STEP)]}, headers=h
    )
    assert c1.json()["accepted"] == 2
    c2 = await client.post(
        f"{RIDES}/{ride['id']}/points", json={"points": [pt(2, dlat=2 * STEP)]}, headers=h
    )
    assert c2.json()["accepted"] == 1
    assert 19.0 < float(c2.json()["summary"]["distance_m"]) < 21.0


async def test_reordered_late_point_no_corruption(client):
    h = await _user(client, A)
    ride = (await _ride(client, h, await _bike(client, h))).json()
    await client.post(
        f"{RIDES}/{ride['id']}/points",
        json={"points": [pt(0), pt(1, dlat=STEP), pt(2, dlat=2 * STEP)]},
        headers=h,
    )
    late = await client.post(
        f"{RIDES}/{ride['id']}/points", json={"points": [pt(1, dlat=STEP)]}, headers=h
    )
    body = late.json()
    assert body["accepted"] == 0 and (body["duplicates"] == 1 or len(body["rejected"]) == 1)
    assert 19.0 < float(body["summary"]["distance_m"]) < 21.0


async def test_bad_accuracy_point_rejected(client):
    h = await _user(client, A)
    ride = (await _ride(client, h, await _bike(client, h))).json()
    chunk = [pt(0), pt(1, dlat=STEP, accuracy=99.0)]
    body = (
        await client.post(f"{RIDES}/{ride['id']}/points", json={"points": chunk}, headers=h)
    ).json()
    assert body["accepted"] == 1 and body["rejected"][0]["reason"] == "bad_accuracy"
    assert float(body["summary"]["distance_m"]) == 0.0


async def test_chunk_too_large_rejected(client):
    h = await _user(client, A)
    ride = (await _ride(client, h, await _bike(client, h))).json()
    big = [pt(i, dlat=i * STEP) for i in range(501)]
    r = await client.post(f"{RIDES}/{ride['id']}/points", json={"points": big}, headers=h)
    assert r.status_code in (413, 422)


# --- pause / resume / finish ---
async def test_pause_resume_no_gap_distance(client):
    h = await _user(client, A)
    ride = (await _ride(client, h, await _bike(client, h))).json()
    rid = ride["id"]
    await client.post(
        f"{RIDES}/{rid}/points", json={"points": [pt(0), pt(1, dlat=STEP)]}, headers=h
    )
    assert (await client.post(f"{RIDES}/{rid}/pause", headers=h)).status_code == 200
    # Upload while paused → explicit 409 (client queues locally).
    r = await client.post(
        f"{RIDES}/{rid}/points", json={"points": [pt(2, dlat=2 * STEP)]}, headers=h
    )
    assert r.status_code == 409
    assert (await client.post(f"{RIDES}/{rid}/resume", headers=h)).status_code == 200
    # Post-gap point: gap itself must not become distance.
    r2 = await client.post(
        f"{RIDES}/{rid}/points", json={"points": [pt(2, dlat=2 * STEP, sec=3700)]}, headers=h
    )
    assert r2.status_code == 200
    finished = (await client.post(f"{RIDES}/{rid}/finish", headers=h)).json()
    assert finished["status"] == "completed"
    assert 19.0 < float(finished["summary"]["distance_m"]) < 21.0


async def test_finish_empty_ride(client):
    h = await _user(client, A)
    ride = (await _ride(client, h, await _bike(client, h))).json()
    body = (await client.post(f"{RIDES}/{ride['id']}/finish", headers=h)).json()
    assert body["status"] == "completed"
    assert float(body["summary"]["distance_m"]) == 0.0
    assert body["summary"]["moving_seconds"] == 0


async def test_invalid_transitions_rejected(client):
    h = await _user(client, A)
    ride = (await _ride(client, h, await _bike(client, h))).json()
    rid = ride["id"]
    await client.post(f"{RIDES}/{rid}/finish", headers=h)
    assert (await client.post(f"{RIDES}/{rid}/pause", headers=h)).status_code == 409
    assert (await client.post(f"{RIDES}/{rid}/resume", headers=h)).status_code == 409
    assert (await client.post(f"{RIDES}/{rid}/finish", headers=h)).status_code == 409
    r = await client.post(f"{RIDES}/{rid}/points", json={"points": [pt(0)]}, headers=h)
    assert r.status_code == 409


async def test_discard(client):
    h = await _user(client, A)
    ride = (await _ride(client, h, await _bike(client, h))).json()
    assert (await client.post(f"{RIDES}/{ride['id']}/discard", headers=h)).json()[
        "status"
    ] == "discarded"


# --- ownership / history ---
async def test_ride_idor(client):
    ha = await _user(client, A)
    hb = await _user(client, B)
    ride = (await _ride(client, ha, await _bike(client, ha))).json()
    rid = ride["id"]
    assert (await client.get(f"{RIDES}/{rid}", headers=hb)).status_code == 404
    assert (
        await client.post(f"{RIDES}/{rid}/points", json={"points": [pt(0)]}, headers=hb)
    ).status_code == 404
    assert (await client.post(f"{RIDES}/{rid}/finish", headers=hb)).status_code == 404


async def test_archived_bike_keeps_history(client):
    h = await _user(client, A)
    bike = await _bike(client, h)
    ride = (await _ride(client, h, bike)).json()
    await client.post(f"{RIDES}/{ride['id']}/finish", headers=h)
    assert (await client.post(f"{BIKES}/{bike}/archive", headers=h)).status_code == 200
    r = await client.get(f"{RIDES}/{ride['id']}", headers=h)
    assert r.status_code == 200 and r.json()["bike_id"] == bike


async def test_bike_delete_blocked_with_rides(client):
    h = await _user(client, A)
    bike = await _bike(client, h)
    await _ride(client, h, bike)
    r = await client.delete(f"{BIKES}/{bike}", headers=h)
    assert r.status_code == 409


async def test_list_pagination(client):
    h = await _user(client, A)
    bike = await _bike(client, h)
    for _ in range(3):
        await _ride(client, h, bike)
    r = await client.get(f"{RIDES}?page=1&page_size=2", headers=h)
    assert (r.json()["total"], len(r.json()["items"])) == (3, 2)

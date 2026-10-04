"""Phase 3 bike tests: CRUD, validation, ownership/IDOR, pagination, archive."""

import uuid

BIKES = "/api/v1/bikes"
AUTH = "/api/v1/auth"

A = {
    "email": "a@example.com",
    "password": "StrongPass123",
    "password_confirm": "StrongPass123",
    "display_name": "Rider A",
}
B = {
    "email": "b@example.com",
    "password": "StrongPass123",
    "password_confirm": "StrongPass123",
    "display_name": "Rider B",
}

ROAD = {
    "name": "Road Bike",
    "category": "road",
    "brand": "Spec",
    "model": "Tarmac",
    "model_year": 2023,
    "weight_kg": "8.20",
}


async def _user(client, data):
    assert (await client.post(f"{AUTH}/register", json=data)).status_code == 201
    r = await client.post(
        f"{AUTH}/login", json={"email": data["email"], "password": data["password"]}
    )
    assert r.status_code == 200
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def _make(client, headers, **over):
    data = {**ROAD, **over}
    return await client.post(BIKES, json=data, headers=headers)


# --- create ---
async def test_create_valid(client):
    h = await _user(client, A)
    r = await _make(client, h)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["name"] == "Road Bike"
    assert body["category"] == "road"
    assert body["weight_kg"] == "8.20"  # exact decimals, no float drift
    assert body["status"] == "active"
    assert body["version"] == 1


async def test_create_invalid_category(client):
    h = await _user(client, A)
    r = await _make(client, h, category="spaceship")
    assert r.status_code == 422


async def test_create_invalid_weight(client):
    h = await _user(client, A)
    assert (await _make(client, h, weight_kg="-2")).status_code == 422
    assert (await _make(client, h, weight_kg="0")).status_code == 422
    assert (await _make(client, h, weight_kg="500")).status_code == 422


async def test_create_invalid_year(client):
    h = await _user(client, A)
    assert (await _make(client, h, model_year=1800)).status_code == 422
    assert (await _make(client, h, model_year=2200)).status_code == 422


async def test_create_missing_name(client):
    h = await _user(client, A)
    r = await client.post(BIKES, json={"category": "road"}, headers=h)
    assert r.status_code == 422


async def test_create_unauthenticated(client):
    assert (await client.post(BIKES, json=ROAD)).status_code == 401


# --- read ---
async def test_list_empty(client):
    h = await _user(client, A)
    r = await client.get(BIKES, headers=h)
    assert r.status_code == 200
    assert r.json() == {"items": [], "total": 0, "page": 1, "page_size": 20}


async def test_detail(client):
    h = await _user(client, A)
    bike_id = (await _make(client, h)).json()["id"]
    r = await client.get(f"{BIKES}/{bike_id}", headers=h)
    assert r.status_code == 200
    assert r.json()["id"] == bike_id


async def test_detail_unknown_id(client):
    h = await _user(client, A)
    r = await client.get(f"{BIKES}/{uuid.uuid4()}", headers=h)
    assert r.status_code == 404


# --- update + OCC ---
async def test_update_valid(client):
    h = await _user(client, A)
    bike = (await _make(client, h)).json()
    r = await client.patch(
        f"{BIKES}/{bike['id']}", json={"name": "Gravel Rig", "expected_version": 1}, headers=h
    )
    assert r.status_code == 200, r.text
    assert r.json()["name"] == "Gravel Rig"
    assert r.json()["version"] == 2


async def test_update_invalid(client):
    h = await _user(client, A)
    bike = (await _make(client, h)).json()
    r = await client.patch(f"{BIKES}/{bike['id']}", json={"weight_kg": "-1"}, headers=h)
    assert r.status_code == 422


async def test_update_version_conflict(client):
    h = await _user(client, A)
    bike = (await _make(client, h)).json()
    r = await client.patch(
        f"{BIKES}/{bike['id']}", json={"name": "Stale", "expected_version": 999}, headers=h
    )
    assert r.status_code == 409


# --- archive / restore / delete ---
async def test_archive_restore(client):
    h = await _user(client, A)
    bike_id = (await _make(client, h)).json()["id"]
    assert (await client.post(f"{BIKES}/{bike_id}/archive", headers=h)).status_code == 200
    # repeated archive: idempotent 200
    r = await client.post(f"{BIKES}/{bike_id}/archive", headers=h)
    assert r.status_code == 200 and r.json()["status"] == "archived"
    # default list hides archived
    assert (await client.get(BIKES, headers=h)).json()["total"] == 0
    # restore brings it back
    r = await client.post(f"{BIKES}/{bike_id}/restore", headers=h)
    assert r.json()["status"] == "active"
    assert (await client.get(BIKES, headers=h)).json()["total"] == 1


async def test_delete_soft_idempotent(client):
    h = await _user(client, A)
    bike_id = (await _make(client, h)).json()["id"]
    assert (await client.delete(f"{BIKES}/{bike_id}", headers=h)).status_code == 200
    assert (await client.delete(f"{BIKES}/{bike_id}", headers=h)).status_code == 200
    assert (await client.get(f"{BIKES}/{bike_id}", headers=h)).status_code == 404
    assert (await client.get(BIKES, headers=h)).json()["total"] == 0


# --- ownership / IDOR ---
async def test_cross_owner_forbidden(client):
    ha = await _user(client, A)
    hb = await _user(client, B)
    bike_id = (await _make(client, ha)).json()["id"]
    assert (await client.get(f"{BIKES}/{bike_id}", headers=hb)).status_code == 404
    assert (
        await client.patch(f"{BIKES}/{bike_id}", json={"name": "Hijack"}, headers=hb)
    ).status_code == 404
    assert (await client.post(f"{BIKES}/{bike_id}/archive", headers=hb)).status_code == 404
    assert (await client.delete(f"{BIKES}/{bike_id}", headers=hb)).status_code == 404
    # A's bike untouched
    assert (await client.get(f"{BIKES}/{bike_id}", headers=ha)).status_code == 200
    # B's list is empty — no leak
    assert (await client.get(BIKES, headers=hb)).json()["total"] == 0


# --- pagination / filtering / sorting ---
async def test_pagination_and_filters(client):
    h = await _user(client, A)
    for i in range(3):
        await _make(client, h, name=f"Bike {i}", category="road" if i < 2 else "gravel")
    r = await client.get(f"{BIKES}?page=1&page_size=2", headers=h)
    assert (r.json()["total"], len(r.json()["items"])) == (3, 2)
    r = await client.get(f"{BIKES}?page=2&page_size=2", headers=h)
    assert len(r.json()["items"]) == 1
    r = await client.get(f"{BIKES}?category=gravel", headers=h)
    assert r.json()["total"] == 1
    r = await client.get(f"{BIKES}?sort=name&order=asc", headers=h)
    names = [b["name"] for b in r.json()["items"]]
    assert names == sorted(names)


async def test_status_filter(client):
    h = await _user(client, A)
    bike_id = (await _make(client, h)).json()["id"]
    await client.post(f"{BIKES}/{bike_id}/archive", headers=h)
    assert (await client.get(BIKES, headers=h)).json()["total"] == 0
    r = await client.get(f"{BIKES}?status_all=true", headers=h)
    assert r.json()["total"] == 1

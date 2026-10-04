"""Phase 5 route tests: CRUD, privacy/IDOR, versioning, GPX, ride association."""

import uuid

ROUTES = "/api/v1/routes"
RIDES = "/api/v1/rides"
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

PTS = [
    {"lat": 46.2000, "lon": 6.1400, "ele": 400.0},
    {"lat": 46.2010, "lon": 6.1410, "ele": 420.0},
    {"lat": 46.2020, "lon": 6.1420, "ele": 410.0},
]

GOOD_GPX = b"""<?xml version="1.0" encoding="UTF-8"?>
<gpx version="1.1" creator="test" xmlns="http://www.topografix.com/GPX/1/1">
  <metadata><name>Imported Loop</name></metadata>
  <trk>
    <name>Imported Loop</name>
    <trkseg>
      <trkpt lat="46.2000" lon="6.1400"><ele>400</ele></trkpt>
      <trkpt lat="46.2010" lon="6.1410"><ele>420</ele></trkpt>
      <trkpt lat="46.2010" lon="6.1410"><ele>420</ele></trkpt>
      <trkpt lat="46.2020" lon="6.1420"><ele>410</ele></trkpt>
    </trkseg>
  </trk>
</gpx>
"""

XXE_GPX = b"""<?xml version="1.0"?>
<!DOCTYPE gpx [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>
<gpx version="1.1" xmlns="http://www.topografix.com/GPX/1/1">
  <trk><trkseg><trkpt lat="46.2" lon="6.1"><ele>&xxe;</ele></trkpt></trkseg></trk>
</gpx>
"""


async def _user(client, data):
    assert (await client.post(f"{AUTH}/register", json=data)).status_code == 201
    r = await client.post(
        f"{AUTH}/login", json={"email": data["email"], "password": data["password"]}
    )
    assert r.status_code == 200
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def _route(client, headers, **over):
    payload = {
        "name": "Lake Loop",
        "activity_type": "road",
        "privacy": "private",
        "points": PTS,
    }
    payload.update(over)
    return await client.post(ROUTES, json=payload, headers=headers)


async def _make(client, headers, **over):
    return (await _route(client, headers, **over)).json()


# --- create ---------------------------------------------------------------
async def test_create_valid(client):
    h = await _user(client, A)
    r = await _route(client, h)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["current_version"] == 1
    assert body["point_count"] == 3
    assert body["status"] == "active"
    assert body["source"] == "manual"
    assert float(body["distance_m"]) > 0
    assert float(body["elevation_gain_m"]) == 20.0  # 400 -> 420 with 3 m hysteresis
    assert body["difficulty"] in ("easy", "moderate", "hard", "extreme")
    assert body["basis"]["estimated_duration_s"] == "estimated"
    assert body["start_lat"] is not None and body["end_lon"] is not None


async def test_create_too_few_points(client):
    h = await _user(client, A)
    r = await _route(client, h, points=PTS[:1])
    assert r.status_code == 422


async def test_create_out_of_range_coordinates(client):
    h = await _user(client, A)
    r = await _route(client, h, points=[{"lat": 1000.0, "lon": 6.1}])
    assert r.status_code == 422


async def test_create_public_rejected(client):
    """public stays in the enum but is disabled until start/end masking (ADR-09)."""
    h = await _user(client, A)
    r = await _route(client, h, privacy="public")
    assert r.status_code == 400
    detail = r.json()["error"]
    assert detail["code"] == "PRIVACY_PUBLIC_DISABLED"


async def test_create_no_elevation_is_null_not_zero(client):
    h = await _user(client, A)
    points = [{"lat": p["lat"], "lon": p["lon"]} for p in PTS]
    body = await _make(client, h, points=points)
    assert body["elevation_gain_m"] is None
    assert body["difficulty"] is not None


# --- privacy / IDOR -------------------------------------------------------
async def test_private_route_hides_from_other_user(client):
    ha, hb = await _user(client, A), await _user(client, B)
    route = await _make(client, ha)
    assert (await client.get(f"{ROUTES}/{route['id']}", headers=hb)).status_code == 404
    # No existence oracle: same 404 for foreign id and nonexistent id.
    missing = await client.get(f"{ROUTES}/{uuid.uuid4()}", headers=hb)
    assert missing.status_code == 404
    assert (await client.get(f"{ROUTES}", headers=hb)).json()["total"] == 0


async def test_unlisted_route_readable_by_id_only(client):
    ha, hb = await _user(client, A), await _user(client, B)
    route = await _make(client, ha, privacy="unlisted")
    r = await client.get(f"{ROUTES}/{route['id']}", headers=hb)
    assert r.status_code == 200
    assert r.json()["privacy"] == "unlisted"
    # No discovery: B's list never contains A's route.
    assert (await client.get(ROUTES, headers=hb)).json()["items"] == []


async def test_foreign_mutations_are_404(client):
    ha, hb = await _user(client, A), await _user(client, B)
    route = await _make(client, ha)
    patch = {"expected_version": 1, "name": "Hijacked"}
    assert (
        await client.patch(f"{ROUTES}/{route['id']}", json=patch, headers=hb)
    ).status_code == 404
    assert (await client.delete(f"{ROUTES}/{route['id']}", headers=hb)).status_code == 404
    assert (await client.post(f"{ROUTES}/{route['id']}/archive", headers=hb)).status_code == 404
    # A still sees it unchanged.
    own = await client.get(f"{ROUTES}/{route['id']}", headers=ha)
    assert own.json()["name"] == "Lake Loop"


# --- versioning -----------------------------------------------------------
async def test_edit_bumps_version_and_keeps_history(client):
    h = await _user(client, A)
    route = await _make(client, h)
    r = await client.patch(
        f"{ROUTES}/{route['id']}",
        json={"expected_version": 1, "name": "Renamed", "changelog": "rename"},
        headers=h,
    )
    assert r.status_code == 200, r.text
    assert r.json()["current_version"] == 2

    versions = (await client.get(f"{ROUTES}/{route['id']}/versions", headers=h)).json()
    assert [v["version_no"] for v in versions["items"]] == [2, 1]
    assert versions["items"][0]["changelog"] == "rename"

    old = await client.get(f"{ROUTES}/{route['id']}?include_geometry=true", headers=h)
    assert old.json()["geometry"]["version_no"] == 2
    v1 = await client.get(f"{ROUTES}/{route['id']}/geometry?version=1", headers=h)
    assert v1.status_code == 200
    assert v1.json()["version_no"] == 1
    assert v1.json()["point_count"] == 3


async def test_stale_version_conflicts(client):
    h = await _user(client, A)
    route = await _make(client, h)
    first = await client.patch(
        f"{ROUTES}/{route['id']}", json={"expected_version": 1, "name": "One"}, headers=h
    )
    assert first.status_code == 200
    stale = await client.patch(
        f"{ROUTES}/{route['id']}", json={"expected_version": 1, "name": "Two"}, headers=h
    )
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "VERSION_CONFLICT"


async def test_geometry_edit_recomputes_metrics(client):
    h = await _user(client, A)
    route = await _make(client, h)
    longer = PTS + [
        {"lat": 46.2030, "lon": 6.1430, "ele": 430.0},
        {"lat": 46.2040, "lon": 6.1440, "ele": 440.0},
    ]
    r = await client.patch(
        f"{ROUTES}/{route['id']}",
        json={"expected_version": 1, "points": longer, "changelog": "extend"},
        headers=h,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["current_version"] == 2
    assert body["point_count"] == 5
    assert float(body["distance_m"]) > float(route["distance_m"])
    # 400→420 (+20), 410→430 (+20), 430→440 (+10); the dip 420→410 is a loss.
    assert float(body["elevation_gain_m"]) == 50.0
    # history preserved: version 1 still has the original 3 points
    v1 = await client.get(f"{ROUTES}/{route['id']}/geometry?version=1", headers=h)
    assert v1.json()["point_count"] == 3


async def test_clearing_name_rejected(client):
    h = await _user(client, A)
    route = await _make(client, h)
    r = await client.patch(
        f"{ROUTES}/{route['id']}", json={"expected_version": 1, "name": None}, headers=h
    )
    assert r.status_code in (400, 422)


async def test_missing_version_404(client):
    h = await _user(client, A)
    route = await _make(client, h)
    r = await client.get(f"{ROUTES}/{route['id']}/geometry?version=99", headers=h)
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "ROUTE_VERSION_NOT_FOUND"


# --- list / lifecycle -----------------------------------------------------
async def test_list_filters_sort_and_paginates(client):
    h = await _user(client, A)
    for i in range(3):
        await _make(client, h, name=f"Route {i}", activity_type="road" if i < 2 else "gravel")
    page = await client.get(f"{ROUTES}?page=1&page_size=2&sort=name&order=asc", headers=h)
    assert page.status_code == 200
    body = page.json()
    assert body["total"] == 3
    assert [r["name"] for r in body["items"]] == ["Route 0", "Route 1"]
    gravel = await client.get(f"{ROUTES}?activity_type=gravel&status_all=true", headers=h)
    assert gravel.json()["total"] == 1


async def test_archive_restore_and_soft_delete(client):
    h = await _user(client, A)
    route = await _make(client, h)

    assert (await client.post(f"{ROUTES}/{route['id']}/archive", headers=h)).json()[
        "status"
    ] == "archived"
    assert (await client.get(ROUTES, headers=h)).json()["total"] == 0  # default: active
    assert (await client.get(f"{ROUTES}?status_all=true", headers=h)).json()["total"] == 1

    assert (await client.post(f"{ROUTES}/{route['id']}/restore", headers=h)).json()[
        "status"
    ] == "active"
    assert (await client.get(ROUTES, headers=h)).json()["total"] == 1

    assert (await client.delete(f"{ROUTES}/{route['id']}", headers=h)).status_code == 200
    assert (await client.get(f"{ROUTES}/{route['id']}", headers=h)).status_code == 404
    assert (await client.get(ROUTES, headers=h)).json()["total"] == 0
    # Idempotent delete after soft delete.
    assert (await client.delete(f"{ROUTES}/{route['id']}", headers=h)).status_code == 200


async def test_unknown_route_404(client):
    h = await _user(client, A)
    r = await client.get(f"{ROUTES}/{uuid.uuid4()}", headers=h)
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "ROUTE_NOT_FOUND"


# --- GPX ------------------------------------------------------------------
async def test_gpx_import(client):
    h = await _user(client, A)
    r = await client.post(
        f"{ROUTES}/import/gpx",
        files={"file": ("loop.gpx", GOOD_GPX, "application/gpx+xml")},
        data={"name": "My Import", "activity_type": "gravel"},
        headers=h,
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["route"]["source"] == "gpx"
    assert body["route"]["name"] == "My Import"
    assert body["route"]["point_count"] == 3  # 4 raw, 1 consecutive duplicate dropped
    assert body["imported_points"] == 3
    assert body["duplicates_removed"] == 1
    assert body["had_timestamps"] is False


async def test_gpx_import_xxe_rejected(client):
    h = await _user(client, A)
    r = await client.post(
        f"{ROUTES}/import/gpx",
        files={"file": ("evil.gpx", XXE_GPX, "application/gpx+xml")},
        headers=h,
    )
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "GPX_FORBIDDEN_MARKUP"


async def test_gpx_import_garbage_rejected(client):
    h = await _user(client, A)
    r = await client.post(
        f"{ROUTES}/import/gpx",
        files={"file": ("bad.gpx", b"<gpx><oops>", "application/gpx+xml")},
        headers=h,
    )
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "GPX_INVALID_XML"


async def test_gpx_export(client):
    h = await _user(client, A)
    route = await _make(client, h, name="Export Me")
    r = await client.get(f"{ROUTES}/{route['id']}/export/gpx", headers=h)
    assert r.status_code == 200
    assert "gpx+xml" in r.headers["content-type"]
    assert "Export_Me.gpx" in r.headers["content-disposition"]
    text = r.text
    assert text.count("<rtept") == 3
    assert "Export Me" in text


# --- ride association -----------------------------------------------------
async def _ride(client, h, route_id=None, route_version=None, uuid_=None):
    bike = (await client.post(BIKES, json=ROAD, headers=h)).json()
    payload = {
        "bike_id": bike["id"],
        "client_ride_uuid": str(uuid_ or uuid.uuid4()),
    }
    if route_id is not None:
        payload["route_id"] = str(route_id)
        if route_version is not None:
            payload["route_version"] = route_version
    return await client.post(RIDES, json=payload, headers=h)


async def test_ride_pins_route_version(client):
    h = await _user(client, A)
    route = await _make(client, h)
    await client.patch(
        f"{ROUTES}/{route['id']}", json={"expected_version": 1, "name": "v2"}, headers=h
    )
    r = await _ride(client, h, route_id=route["id"], route_version=1)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["route_id"] == route["id"]
    assert body["route_version"] == 1  # pinned explicitly, not "current"


async def test_ride_defaults_to_current_version(client):
    h = await _user(client, A)
    route = await _make(client, h)
    r = await _ride(client, h, route_id=route["id"])
    assert r.status_code == 201
    assert r.json()["route_version"] == 1


async def test_ride_cannot_pin_foreign_or_missing_route(client):
    ha, hb = await _user(client, A), await _user(client, B)
    private_route = await _make(client, ha)
    r = await _ride(client, hb, route_id=private_route["id"])
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "ROUTE_NOT_FOUND"
    assert (await _ride(client, hb, route_id=uuid.uuid4())).status_code == 404

    public_to_b = await _make(client, hb, privacy="unlisted")
    bad_version = await _ride(client, hb, route_id=public_to_b["id"], route_version=42)
    assert bad_version.status_code == 404
    assert bad_version.json()["error"]["code"] == "ROUTE_VERSION_NOT_FOUND"


async def test_ride_route_link_and_detach(client):
    h = await _user(client, A)
    route = await _make(client, h)
    ride = (await _ride(client, h)).json()
    assert ride["route_id"] is None

    linked = await client.post(
        f"{RIDES}/{ride['id']}/route", json={"route_id": route["id"]}, headers=h
    )
    assert linked.status_code == 200, linked.text
    assert linked.json()["route_id"] == route["id"]

    detached = await client.post(f"{RIDES}/{ride['id']}/route", json={"route_id": None}, headers=h)
    assert detached.status_code == 200
    assert detached.json()["route_id"] is None
    assert detached.json()["route_version"] is None


async def test_ride_without_route_stays_null(client):
    h = await _user(client, A)
    r = await _ride(client, h)
    assert r.status_code == 201
    body = r.json()
    assert body["route_id"] is None and body["route_version"] is None

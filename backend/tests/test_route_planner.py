"""Phase D planner tests: Nominatim geocoding + OSRM cycling routing.

Upstream HTTP is a MockTransport fixture — no live provider is ever
contacted. Endpoint tests monkeypatch the service boundary (stdlib only)
so validation, auth, ownership, and rate limits run for real.
"""

import httpx
import pytest

from app.core.config import settings
from app.services import route_planner
from app.services.route_planner import PlannerError, clear_geocode_cache, geocode, plan_route

AUTH = "/api/v1/auth"
ROUTES = "/api/v1/routes"

A = {
    "email": "plan@example.com",
    "password": "StrongPass123",
    "password_confirm": "StrongPass123",
    "display_name": "Planner",
}
B = {
    "email": "planb@example.com",
    "password": "StrongPass123",
    "password_confirm": "StrongPass123",
    "display_name": "Planner B",
}

NOMINATIM_OK = [
    {"display_name": "Alexanderplatz, Berlin", "lat": "52.5219814", "lon": "13.4112804"},
    {"display_name": "Bad entry", "lat": "999", "lon": "0"},
    {"display_name": "", "lat": "52.5", "lon": "13.4"},
]

OSRM_OK = {
    "code": "Ok",
    "routes": [
        {
            "distance": 1888.0,
            "duration": 260.4,
            "geometry": {
                "type": "LineString",
                "coordinates": [[13.38886, 52.517037], [13.39, 52.52], [13.397634, 52.529407]],
            },
        }
    ],
}


def transport(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def _user(client, data):
    assert (await client.post(f"{AUTH}/register", json=data)).status_code == 201
    r = await client.post(
        f"{AUTH}/login", json={"email": data["email"], "password": data["password"]}
    )
    assert r.status_code == 200
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


@pytest.fixture(autouse=True)
def _clean_cache():
    clear_geocode_cache()
    yield
    clear_geocode_cache()


# --- geocode service ------------------------------------------------------
async def test_geocode_success_skips_bad_entries():
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["q"] == "Alexanderplatz"
        assert "User-Agent" in request.headers
        return httpx.Response(200, json=NOMINATIM_OK)

    results = await geocode("  Alexanderplatz  ", http=transport(handler))
    assert len(results) == 1
    assert results[0]["name"] == "Alexanderplatz, Berlin"
    assert results[0]["lat"] == pytest.approx(52.5219814)
    assert results[0]["lon"] == pytest.approx(13.4112804)


async def test_geocode_empty_results_is_not_an_error():
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[])

    assert await geocode("Xyzzy Nowhere", http=transport(handler)) == []


async def test_geocode_blank_query_short_circuits():
    async def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("must not call upstream")

    assert await geocode("   ", http=transport(handler)) == []


async def test_geocode_caches_repeat_queries():
    calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json=NOMINATIM_OK[:1])

    first = await geocode("Berlin", http=transport(handler))
    second = await geocode("Berlin", http=transport(handler))
    assert calls == 1 and first == second


async def test_geocode_timeout():
    async def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("slow")

    with pytest.raises(PlannerError) as e:
        await geocode("Berlin", http=transport(handler))
    assert (e.value.code, e.value.status) == ("GEOCODER_TIMEOUT", 504)


async def test_geocode_unreachable():
    async def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down")

    with pytest.raises(PlannerError) as e:
        await geocode("Berlin", http=transport(handler))
    assert (e.value.code, e.value.status) == ("GEOCODER_UNAVAILABLE", 503)


async def test_geocode_rate_limited():
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={})

    with pytest.raises(PlannerError) as e:
        await geocode("Berlin", http=transport(handler))
    assert (e.value.code, e.value.status) == ("GEOCODER_RATE_LIMITED", 429)


async def test_geocode_malformed():
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"not json")

    with pytest.raises(PlannerError) as e:
        await geocode("Berlin", http=transport(handler))
    assert e.value.code == "GEOCODER_INVALID"

    async def handler2(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"unexpected": "shape"})

    with pytest.raises(PlannerError) as e2:
        await geocode("Berlin", http=transport(handler2))
    assert e2.value.code == "GEOCODER_INVALID"


async def test_geocode_disabled(monkeypatch):
    monkeypatch.setattr(settings, "GEOCODING_PROVIDER", "none")
    with pytest.raises(PlannerError) as e:
        await geocode("Berlin")
    assert (e.value.code, e.value.status) == ("PROVIDER_DISABLED", 503)


# --- plan service ---------------------------------------------------------
async def test_plan_success_maps_lon_lat():
    async def handler(request: httpx.Request) -> httpx.Response:
        assert "/route/v1/bike/" in str(request.url)
        return httpx.Response(200, json=OSRM_OK)

    result = await plan_route([(52.5, 13.38), (52.53, 13.40)], http=transport(handler))
    assert result["profile"] == "cycling"
    assert result["distance_m"] == pytest.approx(1888.0)
    assert result["duration_s"] == pytest.approx(260.4)
    # GeoJSON [lon, lat] becomes [{lat, lon}].
    assert result["points"][0] == {"lat": pytest.approx(52.517037), "lon": pytest.approx(13.38886)}
    assert len(result["points"]) == 3


async def test_plan_unreachable():
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"code": "NoRoute", "message": "sea"})

    with pytest.raises(PlannerError) as e:
        await plan_route([(0.0, 0.0), (1.0, 1.0)], http=transport(handler))
    assert (e.value.code, e.value.status) == ("ROUTE_UNREACHABLE", 422)


async def test_plan_rejects_bad_input():
    with pytest.raises(PlannerError) as e:
        await plan_route([(52.5, 13.38)])
    assert e.value.code == "TOO_FEW_WAYPOINTS"
    with pytest.raises(PlannerError) as e:
        await plan_route([(0.0, 0.0)] * 26)
    assert e.value.code == "TOO_MANY_WAYPOINTS"
    with pytest.raises(PlannerError) as e:
        await plan_route([(91.0, 0.0), (0.0, 0.0)])
    assert e.value.code == "INVALID_COORDINATES"
    with pytest.raises(PlannerError) as e:
        await plan_route([(52.5, 13.38), (52.53, 13.40)], profile="mtb")
    assert (e.value.code, e.value.status) == ("UNSUPPORTED_PROFILE", 422)


async def test_plan_timeout_unreachable_ratelimit_malformed():
    async def timeout(request):
        raise httpx.ConnectTimeout("slow")

    with pytest.raises(PlannerError) as e:
        await plan_route([(0.0, 0.0), (1.0, 1.0)], http=transport(timeout))
    assert e.value.status == 504

    async def down(request):
        raise httpx.ConnectError("down")

    with pytest.raises(PlannerError) as e:
        await plan_route([(0.0, 0.0), (1.0, 1.0)], http=transport(down))
    assert e.value.status == 503

    async def limited(request):
        return httpx.Response(429, json={})

    with pytest.raises(PlannerError) as e:
        await plan_route([(0.0, 0.0), (1.0, 1.0)], http=transport(limited))
    assert e.value.status == 429

    async def garbage(request):
        return httpx.Response(200, json={"code": "Ok", "routes": [{"distance": 1}]})

    with pytest.raises(PlannerError) as e:
        await plan_route([(0.0, 0.0), (1.0, 1.0)], http=transport(garbage))
    assert e.value.code == "ROUTER_INVALID"


async def test_plan_disabled(monkeypatch):
    monkeypatch.setattr(settings, "ROUTING_PROVIDER", "none")
    with pytest.raises(PlannerError) as e:
        await plan_route([(0.0, 0.0), (1.0, 1.0)])
    assert (e.value.code, e.value.status) == ("PROVIDER_DISABLED", 503)


# --- endpoints ------------------------------------------------------------
async def test_geocode_endpoint_success_and_empty(client, monkeypatch):
    async def fake_geocode(query, limit=5):
        assert query == "Berlin"
        return [{"name": "Berlin, Germany", "lat": 52.52, "lon": 13.405}]

    monkeypatch.setattr(route_planner, "geocode", fake_geocode)
    h = await _user(client, A)
    r = await client.get(f"{ROUTES}/geocode?q=Berlin", headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["query"] == "Berlin" and len(body["results"]) == 1

    async def fake_empty(query, limit=5):
        return []

    monkeypatch.setattr(route_planner, "geocode", fake_empty)
    r = await client.get(f"{ROUTES}/geocode?q=Xyzzy", headers=h)
    assert r.status_code == 200 and r.json()["results"] == []


async def test_geocode_endpoint_requires_auth_and_validates(client):
    r = await client.get(f"{ROUTES}/geocode?q=Berlin")
    assert r.status_code == 401
    h = await _user(client, A)
    assert (await client.get(f"{ROUTES}/geocode?q=" + "x" * 201, headers=h)).status_code == 422
    assert (await client.get(f"{ROUTES}/geocode?limit=0", headers=h)).status_code == 422


async def test_geocode_endpoint_disabled(client, monkeypatch):
    monkeypatch.setattr(settings, "GEOCODING_PROVIDER", "none")
    h = await _user(client, A)
    r = await client.get(f"{ROUTES}/geocode?q=Berlin", headers=h)
    assert r.status_code == 503
    assert r.json()["error"]["code"] == "PROVIDER_DISABLED"


async def test_geocode_endpoint_upstream_failure_mapping(client, monkeypatch):
    async def fake_fail(query, limit=5):
        raise PlannerError("GEOCODER_TIMEOUT", "slow", 504)

    monkeypatch.setattr(route_planner, "geocode", fake_fail)
    h = await _user(client, A)
    r = await client.get(f"{ROUTES}/geocode?q=Berlin", headers=h)
    assert r.status_code == 504
    assert r.json()["error"]["code"] == "GEOCODER_TIMEOUT"


async def test_plan_endpoint_success(client, monkeypatch):
    async def fake_plan(points, profile="cycling"):
        assert len(points) == 2 and profile == "cycling"
        return {
            "points": [{"lat": 52.5, "lon": 13.38}, {"lat": 52.53, "lon": 13.40}],
            "distance_m": 1888.0,
            "duration_s": 260.4,
            "profile": "cycling",
        }

    monkeypatch.setattr(route_planner, "plan_route", fake_plan)
    h = await _user(client, A)
    r = await client.post(
        f"{ROUTES}/plan",
        json={"points": [{"lat": 52.5, "lon": 13.38}, {"lat": 52.53, "lon": 13.40}]},
        headers=h,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["distance_m"] == 1888.0 and body["profile"] == "cycling"
    assert len(body["points"]) == 2
    assert "elevation" not in body  # no DEM source: absent, never faked


async def test_plan_endpoint_validation_and_auth(client):
    r = await client.post(f"{ROUTES}/plan", json={"points": [{"lat": 0.0, "lon": 0.0}]})
    assert r.status_code == 401
    h = await _user(client, A)
    one = await client.post(
        f"{ROUTES}/plan", json={"points": [{"lat": 0.0, "lon": 0.0}]}, headers=h
    )
    assert one.status_code == 422
    many = await client.post(
        f"{ROUTES}/plan",
        json={"points": [{"lat": 0.0, "lon": 0.0}] * 26},
        headers=h,
    )
    assert many.status_code == 422
    bad_profile = await client.post(
        f"{ROUTES}/plan",
        json={"points": [{"lat": 0.0, "lon": 0.0}, {"lat": 1.0, "lon": 1.0}], "profile": "mtb"},
        headers=h,
    )
    assert bad_profile.status_code == 422


async def test_plan_endpoint_unreachable_mapping(client, monkeypatch):
    async def fake_noroute(points, profile="cycling"):
        raise PlannerError("ROUTE_UNREACHABLE", "sea", 422)

    monkeypatch.setattr(route_planner, "plan_route", fake_noroute)
    h = await _user(client, A)
    r = await client.post(
        f"{ROUTES}/plan",
        json={"points": [{"lat": 0.0, "lon": 0.0}, {"lat": 1.0, "lon": 1.0}]},
        headers=h,
    )
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "ROUTE_UNREACHABLE"


async def test_planned_geometry_saves_through_existing_create(client):
    h = await _user(client, A)
    planned = [
        {"lat": 52.517037, "lon": 13.38886},
        {"lat": 52.52, "lon": 13.39},
        {"lat": 52.529407, "lon": 13.397634},
    ]
    r = await client.post(
        ROUTES,
        json={"name": "Planned Loop", "activity_type": "road", "points": planned},
        headers=h,
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["name"] == "Planned Loop"
    g = await client.get(f"{ROUTES}/{body['id']}/geometry", headers=h)
    assert g.status_code == 200
    assert len(g.json()["points"]) == 3


async def test_planner_cross_user_isolated(client):
    await _user(client, A)
    hb = await _user(client, B)
    assert (await client.get(f"{ROUTES}/geocode?q=x", headers=hb)).status_code == 200
    # B sees none of A's saved routes.
    assert (await client.get(ROUTES, headers=hb)).json()["total"] == 0


# --- production gate ------------------------------------------------------
def test_production_gate_rejects_demo_urls():
    from app.core.config import Settings

    bad = Settings(ENVIRONMENT="production")
    problems = bad.production_config_problems()
    assert any("ROUTING_OSRM_BASE_URL" in p for p in problems)
    assert any("GEOCODING_NOMINATIM_BASE_URL" in p for p in problems)


def test_production_gate_accepts_self_hosted():
    from app.core.config import Settings

    good = Settings(
        ENVIRONMENT="production",
        SECRET_KEY="a" * 40,
        EMAIL_PROVIDER="smtp",
        CORS_ORIGINS="https://app.example.com",
        DATABASE_URL="postgresql+asyncpg://u:p@db:5432/cyclecoach",
        ROUTING_OSRM_BASE_URL="https://osrm.internal.example.com",
        GEOCODING_NOMINATIM_BASE_URL="https://nominatim.internal.example.com",
    )
    problems = good.production_config_problems()
    assert not any("OSRM_BASE_URL" in p or "NOMINATIM_BASE_URL" in p for p in problems)

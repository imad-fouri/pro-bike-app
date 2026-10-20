"""Route planning providers (Phase D): Nominatim geocoding + OSRM cycling
routing, both proxied server-side.

Both providers are keyless and free; both defaults are development-grade
public deployments — production must point at self-hosted endpoints (the
production gate enforces this). No provider credentials exist anywhere.

Policy posture:
- Nominatim fair use: debounced client, per-user rate limit at the
  endpoint, identifying User-Agent, short in-process result cache
  (ADR-09 asks Redis; Redis infra has not landed, so a bounded TTL cache
  with the same 24 h TTL stands in — documented limitation).
- OSRM demo terms: no bulk use, no persistent caching. Route responses
  are NEVER cached; every plan is a live request. Waypoints are capped.
- Exactly one cycling profile is offered because OSRM exposes exactly
  one bike profile. Road/Gravel/MTB stay user metadata on saved routes,
  never provider claims.
"""

import time

import httpx

from app.core.config import settings

_NOMINATIM_UA = "CycleCoach/1.0.0 (route planning)"
_GEOCODE_CACHE_MAX = 500


class PlannerError(Exception):
    def __init__(self, code: str, message: str, status: int = 502) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


# In-process geocode cache: {normalized_query: (expires_at, results)}.
# Bounded and TTL-capped; a Redis cache replaces this when that infra lands.
_geocode_cache: dict[str, tuple[float, list[dict]]] = {}


def clear_geocode_cache() -> None:
    """Test hook: drop all cached geocode responses."""
    _geocode_cache.clear()


def _cache_get(query: str) -> list[dict] | None:
    entry = _geocode_cache.get(query)
    if entry is None:
        return None
    expires_at, results = entry
    if expires_at < time.monotonic():
        del _geocode_cache[query]
        return None
    return results


def _cache_put(query: str, results: list[dict]) -> None:
    if len(_geocode_cache) >= _GEOCODE_CACHE_MAX:
        oldest = min(_geocode_cache, key=lambda k: _geocode_cache[k][0])
        del _geocode_cache[oldest]
    _geocode_cache[query] = (time.monotonic() + settings.GEOCODING_CACHE_SECONDS, results)


async def geocode(query: str, limit: int = 5, http: httpx.AsyncClient | None = None) -> list[dict]:
    """Place search via Nominatim. Returns [{name, lat, lon}], possibly empty.

    Empty results are an ordinary outcome (unknown place), not an error.
    Raises PlannerError for disabled provider, timeouts, rate limits,
    unreachable hosts, and malformed responses.
    """
    if settings.GEOCODING_PROVIDER == "none":
        raise PlannerError("PROVIDER_DISABLED", "Geocoding is not configured.", 503)
    q = " ".join(query.split())
    if not q:
        return []
    cached = _cache_get(f"{q}|{limit}")
    if cached is not None:
        return cached
    url = f"{settings.GEOCODING_NOMINATIM_BASE_URL}/search"
    params = {"format": "jsonv2", "q": q, "limit": str(limit), "addressdetails": "0"}
    headers = {"User-Agent": _NOMINATIM_UA, "Accept": "application/json"}
    try:
        if http is not None:
            response = await http.get(
                url, params=params, headers=headers, timeout=settings.GEOCODING_TIMEOUT_SECONDS
            )
        else:
            async with httpx.AsyncClient(timeout=settings.GEOCODING_TIMEOUT_SECONDS) as client:
                response = await client.get(url, params=params, headers=headers)
    except httpx.TimeoutException as e:
        raise PlannerError("GEOCODER_TIMEOUT", "Geocoder timed out.", 504) from e
    except httpx.HTTPError as e:
        raise PlannerError("GEOCODER_UNAVAILABLE", "Geocoder unreachable.", 503) from e
    if response.status_code == 429:
        raise PlannerError("GEOCODER_RATE_LIMITED", "Geocoder rate limit hit.", 429)
    if response.status_code != 200:
        raise PlannerError(
            "GEOCODER_UNAVAILABLE", f"Geocoder answered {response.status_code}.", 503
        )
    try:
        payload = response.json()
    except ValueError as e:
        raise PlannerError("GEOCODER_INVALID", "Geocoder answered nonsense.", 502) from e
    if not isinstance(payload, list):
        raise PlannerError("GEOCODER_INVALID", "Geocoder answered nonsense.", 502)
    results: list[dict] = []
    for entry in payload:
        if not isinstance(entry, dict):
            continue
        try:
            lat = float(entry["lat"])
            lon = float(entry["lon"])
        except (KeyError, TypeError, ValueError):
            continue
        if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
            continue
        name = entry.get("display_name")
        if not isinstance(name, str) or not name.strip():
            continue
        results.append({"name": name.strip(), "lat": lat, "lon": lon})
    _cache_put(f"{q}|{limit}", results)
    return results


async def plan_route(
    points: list[tuple[float, float]],
    profile: str = "cycling",
    http: httpx.AsyncClient | None = None,
) -> dict:
    """Cycling route via OSRM. Returns {points, distance_m, duration_s}.

    `points` are (lat, lon) waypoints, 2..ROUTING_MAX_WAYPOINTS. Exactly one
    route is ever returned: OSRM answers one, and no alternative is
    invented. Raises PlannerError for disabled provider, bad input,
    unreachable destinations, timeouts, rate limits, and malformed answers.
    """
    if settings.ROUTING_PROVIDER == "none":
        raise PlannerError("PROVIDER_DISABLED", "Routing is not configured.", 503)
    if profile != "cycling":
        raise PlannerError("UNSUPPORTED_PROFILE", "Only the 'cycling' profile is offered.", 422)
    if len(points) < 2:
        raise PlannerError("TOO_FEW_WAYPOINTS", "At least two points required.", 422)
    if len(points) > settings.ROUTING_MAX_WAYPOINTS:
        raise PlannerError(
            "TOO_MANY_WAYPOINTS",
            f"At most {settings.ROUTING_MAX_WAYPOINTS} waypoints.",
            422,
        )
    for lat, lon in points:
        if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
            raise PlannerError("INVALID_COORDINATES", "Coordinates out of range.", 422)
    coords = ";".join(f"{lon},{lat}" for lat, lon in points)
    url = f"{settings.ROUTING_OSRM_BASE_URL}/route/v1/{settings.ROUTING_OSRM_PROFILE}/{coords}"
    params = {"overview": "full", "geometries": "geojson", "steps": "false"}
    try:
        if http is not None:
            response = await http.get(url, params=params, timeout=settings.ROUTING_TIMEOUT_SECONDS)
        else:
            async with httpx.AsyncClient(timeout=settings.ROUTING_TIMEOUT_SECONDS) as client:
                response = await client.get(url, params=params)
    except httpx.TimeoutException as e:
        raise PlannerError("ROUTER_TIMEOUT", "Router timed out.", 504) from e
    except httpx.HTTPError as e:
        raise PlannerError("ROUTER_UNAVAILABLE", "Router unreachable.", 503) from e
    if response.status_code == 429:
        raise PlannerError("ROUTER_RATE_LIMITED", "Router rate limit hit.", 429)
    if response.status_code != 200:
        raise PlannerError("ROUTER_UNAVAILABLE", f"Router answered {response.status_code}.", 503)
    try:
        payload = response.json()
    except ValueError as e:
        raise PlannerError("ROUTER_INVALID", "Router answered nonsense.", 502) from e
    if not isinstance(payload, dict):
        raise PlannerError("ROUTER_INVALID", "Router answered nonsense.", 502)
    if payload.get("code") == "NoRoute":
        raise PlannerError("ROUTE_UNREACHABLE", "No cycling route connects these points.", 422)
    if payload.get("code") != "Ok":
        raise PlannerError("ROUTER_INVALID", "Router refused the request.", 502)
    routes = payload.get("routes")
    if not isinstance(routes, list) or not routes:
        raise PlannerError("ROUTER_INVALID", "Router returned no route.", 502)
    route = routes[0]
    if not isinstance(route, dict):
        raise PlannerError("ROUTER_INVALID", "Router returned no route.", 502)
    try:
        distance = float(route["distance"])
        duration = float(route["duration"])
        raw_coords = route["geometry"]["coordinates"]
    except (KeyError, TypeError, ValueError) as e:
        raise PlannerError("ROUTER_INVALID", "Router geometry malformed.", 502) from e
    if distance < 0 or duration < 0:
        raise PlannerError("ROUTER_INVALID", "Router metrics malformed.", 502)
    geometry: list[dict] = []
    if not isinstance(raw_coords, list) or not raw_coords:
        raise PlannerError("ROUTER_INVALID", "Router geometry malformed.", 502)
    for pair in raw_coords:
        if (
            not isinstance(pair, (list, tuple))
            or len(pair) != 2
            or not isinstance(pair[0], (int, float))
            or not isinstance(pair[1], (int, float))
        ):
            raise PlannerError("ROUTER_INVALID", "Router geometry malformed.", 502)
        lon, lat = float(pair[0]), float(pair[1])
        if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
            raise PlannerError("ROUTER_INVALID", "Router geometry malformed.", 502)
        geometry.append({"lat": lat, "lon": lon})
    return {
        "points": geometry,
        "distance_m": distance,
        "duration_s": duration,
        "profile": "cycling",
    }

# PHASE D — Route Planning (WS-MOBILE-LIVE)

Status: PASS (automated gates) — see "What remains device-verification" below.

Phase D adds server-proxied, provider-agnostic route planning and geocoding to
the product, wires a planner screen into the mobile app, and lets a saved route
be ridden directly. It does NOT invent elevation, surface, or "dirt" data: the
planner reports exactly what the provider returns and is explicit when climbing
data is unavailable.

## What was built

### Backend (provider-proxying, fail-closed)
- `app/core/config.py`:
  - `ROUTING_PROVIDER` (`none | osrm | graphhopper | mapbox | valhalla`, default
    `osrm`) and `GEOCODING_PROVIDER` (`none | nominatim | photon | mapbox`,
    default `nominatim`).
  - `ROUTING_OSRM_BASE_URL`, `GEOCODING_NOMINATIM_BASE_URL` (defaults are the
    public demo servers — acceptable for development only), `ROUTING_OSRM_PROFILE`
    (`bike`), request timeouts, `GEOCODING_MAX_RESULTS=5`, `GEOCODING_CACHE_SECONDS`,
    and `ROUTING_MAX_WAYPOINTS=25`.
  - **Production gate #11** (in `validate_production`): a production deployment
    may not silently proxy a third-party demo server. The gate names the settings
    and problem, never values, and is fail-closed.
- `app/schemas/route.py`: `GeocodeResult(s)`, `PlanWaypoint`, `PlanRequest`
  (enforces `2..25` waypoints), `PlanPoint`, `PlanResult`.
- `app/services/route_planner.py`: `PlannerError` with stable codes
  (`ROUTE_UNREACHABLE`, `NETWORK_ERROR`, `NETWORK_TIMEOUT`, `PROVIDER_ERROR`,
  `GEOCODING_ERROR`, `TOO_MANY_WAYPOINTS`), Nominatim `User-Agent`
  (`CycleCoach/1.0.0 (route planning)`), a bounded in-process TTL geocode cache
  (routes are never cached — fresh OSRM geometry every plan), rate limiting per
  user, and `transport` DI so tests never touch the network.
- `app/api/v1/routes.py` endpoints (registered before the parametric
  `/routes/{id}`):
  - `GET /routes/geocode?q=...&limit=...` — 30 req/min/key `route-geocode`.
  - `POST /routes/plan` — 60 req/min/key `route-plan`.

### Mobile (planner, geometry discipline, ride-from-route)
- `domain/route_planner.dart` (`PlaceResult`, `PlannedRoute`, `PlannedPoint`) and
  `data/route_planner_repository.dart`.
- `presentation/route_planner_providers.dart`: `routePlannerProvider` state
  machine (search/plan/multi-stop/clean errors), caps input at the backend
  waypoint limit, debounce at the UI layer, saves through `routesRepository`.
- `presentation/route_planner_page.dart`: search field + result list, start/dest
  + up to 25 stops, interactive map (tap to place a point, recenter following),
  result bar with distance/time and an honest "elevation unavailable" note, save
  flow, Arabic RTL support.
- l10n: `planner.*` keys in en/fr/ar.
- Route history integration: `/routes/plan` reachable from the route list;
  route detail gains "Ride this route" → `/ride/start?route=<id>`;
  ride-start pre-selects that route only when it is loaded and unarchived.
- `RideLiveMap` renders the planned geometry as a dashed mute layer and the live
  track over it — planned and recorded geometry stay visually distinct
  (`LiveRide.routeId`, `track`, `backgroundLimited` wiring through
  `ride_recorder.dart` / `ride_recording_page.dart`).

## API contracts (mobile calls these in production)
- `GET /api/v1/routes/geocode?q=...` →
  `{"query": "...", "results": [{"name", "lat", "lon"}]}`.
- `POST /api/v1/routes/plan` body `{"waypoints": [{"lat", "lon"}], "profile":
  "cycling"}` (2..25 waypoints) →
  `{"points": [{"lat","lon"}], "distance_m", "duration_s", "profile"}`.
- Errors use the standard envelope `{"error": {"code","message","details"}}`.
  **No elevation/surface fields exist anywhere in the payload** — the product
  deliberately does not fabricate them.

## Provider selection (verified live during preflight)
- Routing: OSRM `bike` profile (live `/route/v1/bike` round-trip on the two-line
  fixture returned one route, 1888 m / 260 s, matching upstream fixture shapes).
- Geocoding: Nominatim (live structured query returned stable place results;
  `accept-language=en`, bounded `limit`).
- Both are keyless self-hostable; prod gate forces operators to pin their own
  instances. No routes are cached (fresh geometry per plan); geocoding is cached
  in-process (TTL 24 h, bounded by `GEOCODING_MAX_RESULTS`).

## Gate results (this phase's clean end-to-end run)
- Backend full suite: **1286 passed** (`pytest -q`, 32:10; +25 planner tests vs
  the Phase C 1261 baseline; the 19 production-gate/security-regression failures
  the new demo-URL rule caused were resolved by making those fixtures configure a
  safe baseline: provider `none`, and the OpenAPI-security test pins
  `ROUTING_PROVIDER`/`GEOCODING_PROVIDER` to `none`).
- `ruff check` + `ruff format --check` on the 7 touched files: clean.
- `mypy app`: success (114 source files, no issues).
- `alembic heads`: `0015_store_webhooks (head)` — no migration required.
- Mobile full suite: **805 passed** (was 788 at Phase C; +17 planner/widget
  tests). Full-suite + route_planner run, `flutter analyze`: no issues,
  `dart format --set-exit-if-changed`: clean.
- `flutter build apk --debug`: APK built (with the map/media/Nominatim UA paths).

## Files changed (this phase)
- Backend: `app/core/config.py`, `app/schemas/route.py`,
  `app/services/route_planner.py`, `app/api/v1/routes.py`,
  `tests/test_route_planner.py`, `tests/test_production_config.py`,
  `tests/test_security_regression.py`.
- Mobile: `lib/features/routes/domain/route_planner.dart`,
  `data/route_planner_repository.dart`,
  `presentation/route_planner_providers.dart`,
  `presentation/route_planner_page.dart`,
  `lib/core/l10n/app_localizations.dart`, `lib/core/routing/app_router.dart`,
  `lib/features/routes/presentation/route_list_page.dart`,
  `lib/features/routes/presentation/route_detail_page.dart`,
  `lib/features/ride/presentation/ride_start_page.dart`,
  `lib/features/ride/presentation/ride_live_map.dart`,
  `lib/features/ride/presentation/ride_recording_page.dart`,
  `lib/features/ride/data/ride_recorder.dart`,
  `test/route_planner_test.dart`.

## Known limitations (explicit, never hidden)
- Real elevation/surface/gravel classification is absent; the UI says so.
- Default demo providers are dev-grade and rate-limited by the upstream hosts;
  production must self-host (enforced by gate #11).
- Offline planning is out of scope for this phase (offline *download* of a saved
  route remains, from Phase C).
- No live cell triangulation — device GPS quality still unproven (see the
  product audit; Phase A+B engine and Phase E real-device steps follow).

## What remains device-verification
- Planner screen on-device: search autocomplete UX, map tapping/pan, save, and
  "Ride this route" → live recording need a physical Android phone (none was
  attached this session; covered by widget tests + the desk-check checklist).
- Real OSRM/Nominatim round-trips on-device (network + provider latency, UA).
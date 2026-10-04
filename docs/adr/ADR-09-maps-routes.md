# ADR-09 — Maps & Routes: Provider Strategy, Geometry Storage, PostGIS (Re)Decision

Date: 2026-09-27 | Status: Accepted | Phase: 5

## Context

Phase 5 introduces Maps & Routes. `ROUTE != RIDE`: a Route is planned/reference
geometry; a Ride is recorded activity (docs/05, master prompt §2). Required
decisions: map/tile/routing/geocoding/elevation providers, geometry storage,
PostGIS reassessment (ADR-06 deferred it "until route geometry / proximity
queries land"), offline strategy, privacy.

## 1. Provider decomposition (five distinct concerns)

| Concern | What it is | Phase 5 decision | Status |
|---|---|---|---|
| MAP DATA | The underlying geography (OSM, commercial) | OSM-derived raster tiles in dev; provider-agnostic config | IMPLEMENTED (config) |
| TILE PROVIDER | HTTP endpoint serving {z}/{x}/{y} images | `MAP_TILE_URL_TEMPLATE` env var, no key in repo | IMPLEMENTED (config) |
| ROUTING PROVIDER | Point A→road-following geometry | Not wired. Abstraction only (`RoutingProvider` concept, docs/05) | DESIGNED / DEFERRED |
| GEOCODING PROVIDER | Place search / reverse | Not wired. Abstraction + constraints documented below | DESIGNED / DEFERRED |
| ELEVATION PROVIDER | DEM lookup for `ele` | Not wired. GPX-supplied `<ele>` is the only elevation source | IMPLEMENTED (GPX) / DEFERRED (DEM) |

**Rule: no external map/routing/geocoding vendor may be a hard dependency of the
domain layer** (docs/05: "No hard-coded provider"). All provider access goes
through config-driven abstractions; the domain computes distance/elevation/
difficulty from stored coordinates, never from a provider call.

### Map client (Flutter)

Compared: `flutter_map` (8.x, BSD-3, pure Dart, Android/iOS/Web/desktop),
Google Maps (`google_maps_flutter`, native SDKs, billing account + key
mandatory), Mapbox (`mapbox_maps_flutter`, native SDKs, API key, free tier then
pay-as-you-go, offline style packs), MapLibre (`flutter_map_maplibre`,
vector/MVT, native SDK build complexity).

**Decision: `flutter_map` + raster `TileLayer`.**
- Pure Dart → same code path on Android/iOS/**web** (Phase 5 demo runs in Chrome);
  no native SDK keys, no billing account, no `.plist`/manifest key wiring.
- Vendor-free client: tile URL is a template, so the tile provider is swappable
  without code change (MapTiler / Stadia / Mapbox Raster / self-hosted tile server).
- BSD-3 license; polyline/marker layers ship in the core package.
- Rejected now: Google/Mapbox native SDKs (keys + billing + no web parity in our
  setup), MVT/MapLibre (better performance/offline, but native build burden —
  revisit when offline basemaps are scoped).

### Tile provider

- Dev/demo default: `https://tile.openstreetmap.org/{z}/{x}/{y}.png`
  (OSM tile usage policy: must show attribution, low volume, valid HTTP
  Referer/UA — acceptable for development only).
- Production: set `MAP_TILE_URL_TEMPLATE` (+ `MAP_TILER_API_KEY` if needed) to a
  commercial or self-hosted tile server. Attribution string is configurable via
  `MAP_ATTRIBUTION` and is always rendered.
- **No API key is ever committed** (`app/core/config.py` env-driven, `extra="ignore"`).
- Status: IMPLEMENTED (template + attribution), provider account = DEFERRED (ops).

### Routing provider

Not implemented in Phase 5 (§12: "Do not require routing immediately"; §26: no
automatic road snapping). Routes are created from tapped map points or GPX.
Abstraction to introduce when wired (docs/05 `RouteProvider`):

```
RouteRequest(start, end, waypoints[], activity_type, options)
        │
        ▼
RoutingProvider.route(request) -> RouteResult(geometry[], distance_m, ascend_m)
        │   (failure -> RoutingError → client keeps manual points, §31)
        ▼
domain: metrics + persistence (never vendor objects)
```

Candidates when scoped: **self-hosted OSRM or Valhalla** (OSS, OSM data, MIT/
Apache, full control, no per-call cost, but you run planet/tiles + no SLA),
**GraphHopper / openrouteservice** (hosted, cycling profiles incl. gravel-ish
surface data, free dev quota then paid), **Mapbox/Google** (best UX + global SLA,
per-call cost, ToS lock-in). Decision deferred until a Phase ≥7 navigation
feature needs it. Status: DESIGNED.

### Geocoding provider

Not implemented (§29 constraints recorded for later): provider abstraction
(`Geocoder.search(q, bbox?) / reverse(lat, lon)`), input debounce ≥300 ms,
result cache (Redis, TTL ≥1 day), per-user rate limit, key server-side only.
Candidates: Nominatim (OSM, free, strict fair-use policy — no bulk/autocomplete),
Photon (Komoot, self-hostable), hosted Mapbox/Google/GraphHopper.
Status: DESIGNED / DEFERRED.

### Elevation provider

Phase 5 reads `ele` from GPX only; metrics are computed from stored coordinates.
When GPX has no elevation, gain/loss/highest/lowest are `NULL` (explicitly
"unknown", never faked). DEM lookup candidates (OpenTopoData, AWS Terrain Tiles,
Mapbox Terrain-RGB, self-hosted SRTM/Copernicus) deferred to climb-analysis
phase. Status: IMPLEMENTED (GPX path) / DEFERRED (DEM path).

## 2. Geometry storage (no PostGIS yet)

**Decision: normalized `route_points` rows scoped to a route version.**

```
routes 1─n route_versions 1─n route_points (route_version_id, seq, lat, lon, ele)
```

Rationale:
- **Versioning requires per-version geometry.** A ride pins `route_id +
  route_version`; version N must stay immutable, so points belong to the
  version, not the route (docs/03: "Route edits create new route_versions row …
  never overwrite"; docs/06: "route_versions monotonic … no silent corruption").
- Ordered rows give: deterministic metrics recomputation, elevation-profile
  generation, bounded API payloads (list endpoints never ship geometry),
  per-version point counts, and future bbox/proximity scans
  (`WHERE lat BETWEEN … AND lon BETWEEN …` via the unique (version, seq) index
  + start/end lat/lon columns on `routes`).
- JSONB blob rejected as primary store: not indexable point-wise, no partial
  reads, awkward versioned diffing (docs/03's `geojson/gpx_ref` noted; we chose
  rows + a derived `elevation_profile` JSONB *cache* on `route_versions` so the
  UI never recomputes profiles).
- Precisions match ADR-06: `lat NUMERIC(9,6)`, `lon NUMERIC(10,6)`,
  `ele NUMERIC(8,2)` (~11 cm / 1 cm).

## 3. PostGIS — reassessed for routes, still deferred

Reassessment trigger (ADR-06): "Revisit when map search / route geometry /
proximity queries land."

| Question | Finding |
|---|---|
| LineString storage | No query needs `geometry` yet: Phase 5 is owner-only CRUD, no discovery (§34), no intersection/proximity/map-matching (§9) |
| Spatial index / GiST | Nothing to index against: no `ST_*` predicates in any query |
| Bounding boxes | Served by `routes.start_lat/lon` + `end_lat/lon` (already stored) or `route_points` range predicates |
| Route intersection / proximity / map matching | Not in Phase 5 scope; future discovery/navigation phases |
| Worldwide coordinates | Plain `NUMERIC` handles WGS84 globally; no projection involved |
| Storage size | Rows are ~30 B/point vs PostGIS ~24 B/point + index overhead — irrelevant at ≤5 000 points/route |
| Migration complexity | `postgres:16` (docker-compose **and** CI service) ships **without** PostGIS; adopting it changes image, migration bootstrap, local dev onboarding, and every environment |
| Testing | Would add extension-dependent tests to a suite that today runs on stock PG16 |

**Decision: keep deferring PostGIS in Phase 5.** Not "because it is
uninteresting" — because no Phase 5 query is spatial, and the deploy cost hits
every environment for zero query benefit. Geometry-as-rows is required
regardless (version immutability), so PostGIS would be a *derived* copy today.

**Documented migration path (no data loss):**
```sql
ALTER TABLE routes ADD COLUMN IF NOT EXISTS geom geography(LineString, 4326);
-- backfill from route_points (current version), ordered by seq
CREATE INDEX ix_routes_geom ON routes USING GIST (geom);
```
Revisit trigger: first phase that needs spatial joins (route proximity search,
"routes near me", map matching, shared-route discovery) — then `geom` becomes
the query surface while `route_points` remains the versioned source of truth.

## 4. Offline strategy

- **Route geometry offline: IMPLEMENTED.** Explicit user-driven download into
  the local Drift cache (`cached_routes` + `cached_route_points`, keyed by
  `route_id` + `route_version`); invalidation only when the server version is
  newer; bounded (user-initiated, no bulk prefetch) per §23.
- **Offline basemap tiles: DEFERRED.** No tile provider was selected yet, so no
  license permits bulk tile download (OSM tile policy explicitly forbids
  offline bulk downloads); an MBTiles/`offline_tiles`-style pack needs the
  commercial/self-hosted tile decision from §1 first. Route polyline + markers
  render without a basemap (tiles simply don't paint), so offline route
  *geometry* stays usable.

## 5. Privacy model

- Default privacy = `private`; `unlisted` = direct-ID access only (no listing,
  no discovery); `public` exists in the enum but **API rejects `privacy=public`
  with `PRIVACY_PUBLIC_DISABLED`** until start/end masking ships (§33: "public
  routes should remain disabled or clearly restricted"). Status: IMPLEMENTED.
- Private coordinates never leave the owner's session (owner-only reads, 404 —
  no existence oracle, same rule as ADR-05). No location logging (§45).
- No social discovery, no global search (§34). Home/work exposure is therefore
  limited to GPX the user themselves uploaded.

## 6. Concurrency / sync

Monotonic `routes.current_version`; every edit inserts an immutable
`route_versions` snapshot (metadata + metrics + points copy). `PATCH` **requires
`expected_version`**; mismatch → `409 VERSION_CONFLICT`; client refreshes and
reconciles (docs/06: "concurrent edit → 409 + merge prompt. No silent
corruption"). Rides store `route_id` + `route_version` at association time and
are never rewritten (§20).

## 7. Config keys (no secrets in repo)

`MAP_TILE_URL_TEMPLATE`, `MAP_ATTRIBUTION`, `MAP_MAX_ZOOM`, `ROUTING_PROVIDER`,
`GEOCODING_PROVIDER`, `ROUTE_MAX_POINTS`, `GPX_MAX_BYTES`.
All env-driven in `Settings`; CI/local use defaults.

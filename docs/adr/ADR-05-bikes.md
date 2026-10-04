# ADR-05 — Bike Management Decisions

Date: 2026-09-27 | Status: Accepted | Phase 3

## 1. Archive vs DELETE
- `POST /bikes/{id}/archive` → `status=archived` (reversible via `/restore`).
  Archived bikes are hidden from the default list but kept for history.
- `DELETE` = **soft delete** (`deleted_at` set, excluded from all queries).
  Idempotent: repeated DELETE returns 200. Rationale: future `rides.bike_id`
  must never dangle; hard delete is forbidden while any ride references the
  bike (enforced in Phase 4+ via FK `ON DELETE RESTRICT` from rides).
- Cross-owner access returns **404** (not 403) — no existence oracle.

## 2. Canonical units + precision
- Backend stores `weight_kg NUMERIC(5,2)` and `initial_distance_km
  NUMERIC(10,2)`. NUMERIC, not float: exact decimal arithmetic for analytics.
- UI converts to lb/mi per the user's Phase-2 `measurement_system`.
- Validation limits: name 1–80 chars; brand/model ≤120; year 1900–(current+1);
  weight 0.5–200 kg; frame_size free text ≤32 (sizes are not comparable across
  categories: "M" vs "54cm" vs "17.5in"); notes ≤2000; initial_distance
  0–1,000,000 km.

## 3. Optimistic concurrency
- `version INTEGER` on every bike, incremented per PATCH. Clients may send
  `expected_version`; mismatch → 409 `VERSION_CONFLICT`. Omitted → last-write-
  wins (documented; Flutter form sends the version it loaded, so conflicts
  surface as "changed elsewhere, reload").

## 4. Mileage
- `initial_distance_km` = odometer baseline at creation (second-hand bikes).
- Future: `mileage = initial_distance_km + SUM(rides.distance_km)` over
  completed rides. Never hand-edited as source of truth. No `mileage` column.

## 5. Categories / images / components
- `bike_category` Postgres enum (13 values); new category = new enum value via
  migration + `values_callable` parity (same pattern as Phase 2 enums).
- Images: storage reference only (`BikeImageStorage`, local dev impl),
  mirroring avatar approach. No blobs, no processing pipeline yet.
- Components deferred: future `bike_components(bike_id FK RESTRICT, type,
  …)` — current model imposes no blocker.

## 6. Rate limits (documented, Redis-backed with in-memory fallback)
- Bike writes: 120/min per user. Reads: 300/min per user.

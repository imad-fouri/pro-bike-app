# ADR-06 — Ride Point Storage: Numeric Columns, PostGIS Deferred

Date: 2026-09-27 | Status: Accepted | Phase 4

## Decision: plain NUMERIC lat/lon (+ Haversine in Python), no PostGIS in Phase 4.

| Criterion | Numeric + app logic | PostGIS |
|---|---|---|
| Per-ride metrics (distance/gain/speed) | Haversine over ordered points — sufficient | Same, heavier |
| Bounding box / start-end queries | B-tree on (ride_id, seq) + stored start/end — sufficient | GiST, overkill now |
| Storage for 10k-point rides | ~1 MB/ride, fine | Similar |
| Deployment (managed Postgres, local docker) | Zero extension friction | Extension must exist everywhere incl. CI |
| Team/phase cost | Pure Python, unit-testable | Migration + driver + ops |

## Consequence
- `ride_points`: `lat NUMERIC(9,6)`, `lon NUMERIC(10,6)` (~11 cm),
  `alt NUMERIC(8,2)`, accuracy/speed NUMERIC(6,2), heading NUMERIC(5,1).
- Revisit when map search / route geometry / proximity queries land
  (route discovery phase). Migration path: `ALTER TABLE … ADD COLUMN geom
  geography(Point)` backfilled from lat/lon — no data loss.

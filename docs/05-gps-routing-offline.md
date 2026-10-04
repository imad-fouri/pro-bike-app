# 05 — GPS, Maps/Routing, Offline Sync

## GPS pipeline (client pre-filter → server validation)
Client: accuracy gate (>25m degraded), duplicate filter, Kalman/light smoothing, auto-pause (<2km/h, 10s). Server: reject impossible jumps (>80m/s or HDOP-bad), de-dupe by (time,seq), gap-marking, Haversine+ele for distance/gain (threshold ≥3m per point to kill noise), moving vs elapsed time split.
Metrics: distance, speed/avg/max, ele gain/loss, gradient (smoothed window), heading, moving/elapsed time, accuracy stats, route progress.

## Maps/routing
`RouteProvider` interface (OSM/GraphHopper/Mapbox interchangeable). Surface tags (road/gravel/mtb/touring), elevation-aware cost, difficulty score, waypoints, GPX 1.1 import/export (validated, size-capped, XXE-safe parse). No hard-coded provider.

## Offline
Hive outbox: ride points, route downloads, chat queue. Auto-sync on reconnect with idempotency keys + `client_uuid` de-dupe, server `version_no` conflict detection, exponential backoff, background sync where OS permits. Network loss never kills a ride.

# Ride Sync Protocol (Phase 4)

Local ride is source of truth while recording; backend is system of record
after finalize. Statuses `syncing`/`sync_failed` are **local-only**; the
server enum is recording/paused/completed/discarded.

## Flow

```text
1. POST /rides {bike_id, client_ride_uuid}
     → 201 {ride} | 200 existing (same uuid → idempotent create)
2. Local recording: points → Drift, live metrics via Dart GpsProcessor
3. Upload loop (chunk = 100 points, configurable):
     POST /rides/{id}/points {points:[{client_point_uuid, seq, lat, lon,
        recorded_at, alt?, accuracy?, speed?, heading?}]}
     → {accepted, rejected:[{seq, reason}], summary}
   Server dedupes by (ride_id, client_point_uuid) AND (ride_id, seq):
   retries never duplicate distance.
4. POST /rides/{id}/finish → server recomputes summary from accepted points
   ordered by seq (deterministic) and returns it.
```

## Recovery rules
- Chunk N fails → resume from chunk N (client tracks `uploaded_seq` in
  `sync_state`; server state is derived, never trusted blindly).
- Timeout → same as failure: retry is safe (idempotency keys).
- Reordered/late points: stored with accepted=false reason=out_of_order;
  metrics use the in-order accepted stream; finish recomputes deterministically.
- App restart: local ride in recording/paused → recovery screen
  (resume / finish / discard). Nothing auto-discarded.
- Auth failure mid-sync → pause sync, re-login, resume (no data loss).

## Idempotency keys
- Ride: `client_ride_uuid` UNIQUE per user.
- Point: `client_point_uuid` UNIQUE per ride (+ unique (ride_id, seq)).

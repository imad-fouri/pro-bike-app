# Phase C Report — Real Ride History and Production Dashboard

Status convention: PASS / FAIL / BLOCKED / NOT TESTED.
Nothing in this work was committed. Stopping before Phase D as instructed.

## 1. Files changed and architectural decisions

Backend (`backend/`, no migration — schema untouched):

```text
app/schemas/ride.py
  RidePointOut / RidePointsOut (bounded accepted-fix trail + truncation
  flag); RidesOverviewOut (totals + UTC window starts).
app/services/ride_service.py
  ride_points() (ownership enforced by callers via get_owned; accepted
  fixes only, seq-ordered, limit+1 truncation probe); rides_overview()
  (single aggregate query over COMPLETED rides; UTC calendar windows via a
  testable _week_month_starts() helper).
app/api/v1/rides.py
  GET /rides/stats/overview (registered before parametric routes) and
  GET /rides/{ride_id}/points (limit 1..5000, default 2000).
  Reused: GET /rides (paginated newest-first + status filter),
  GET /rides/{ride_id} (owned), RidePage/RideOut/RideSummary, rate limits.
app/services/training_service.py
  _ride_samples() now also samples `stationary` holds (see §5: without
  this, the Phase A+B drift gate would starve power/HR/cadence analysis
  of slow-ride sensor data — a real regression caught by 10 training
  tests, investigated, and fixed at the consumption point rather than by
  weakening the GPS gate).
```

Mobile (`mobile/`):

```text
lib/features/ride/data/rides_api.dart — listRides/detail/ridePoints/overview.
lib/features/ride/domain/ride_history.dart (new) — tolerant models (JSON
  numbers or decimal strings; missing optionals stay null, never zero).
lib/features/ride/data/ride_history_repository.dart (new) — history page,
  detail, trail, overview over the shared ApiClient.
lib/features/ride/presentation/history_providers.dart (new) — paginated
  feed notifier, detail family, overview notifier, all with retry disabled
  (explicit UI retry owns recovery; framework backoff behind a left screen
  caused an infinite retry loop found during testing).
lib/features/ride/presentation/ride_history_page.dart (new) — /rides feed:
  pull-refresh, 80% infinite scroll, loading/empty/offline/error+retry,
  bike-name join from the fetched bike list, numeric RTL-safe dates.
lib/features/ride/presentation/ride_detail_page.dart (new) — /rides/:id:
  recorded trail on the shared flutter_map config, persisted metrics,
  timestamps, bike, truncation notice, 404 → not-found + back.
lib/features/home/home_page.dart — static workout/stat/event cards
  removed (incl. hardcoded 180 km / 2,400 m); overview stats, recent
  rides (shared feed provider — dashboard and history cannot disagree),
  empty guidance, retry paths. Recovery banner, record action, ads kept.
lib/core/routing/app_router.dart — real /rides + /rides/:id (static
  first); only /settings remains placeholder.
lib/core/l10n/app_localizations.dart — 26 keys × en/fr/ar.
test/ride_history_test.dart (new, 20 tests), test/router_l10n_test.dart
  (shell made hermetic — see §5).
```

Decisions: aggregates endpoint justified (single query vs paging entire
history client-side; no N+1; feed carries summaries so no per-item point
download); bike names join client-side from one bike fetch; week =
Monday 00:00 UTC, month = 1st 00:00 UTC, completed-only, single-query
counting (double-count impossible); no caching (no history-cache
convention exists); integrity verdict not duplicated on detail (lives in
competition surfaces).

## 2. API endpoints and response contracts

- `GET /api/v1/rides?page=&page_size=&status=` → `{items:[RideOut],
  total, page, page_size}` (pre-existing, reused; page_size ≤ 100).
- `GET /api/v1/rides/{id}` → RideOut, 404 unless owned (pre-existing).
- `GET /api/v1/rides/{id}/points?limit=` → `{points:[{seq,lat,lon,
  alt?}], total, truncated}`; 404 unless owned.
- `GET /api/v1/rides/stats/overview` → `{total_rides,
  total_distance_m, total_moving_seconds, total_elevation_gain_m,
  week_distance_m, month_distance_m, week_start, month_start}`.
- Decimals serialize as strings (backend convention); mobile parses both.

## 3. Aggregation definitions and timezone rules

Completed status only (recording/paused/discarded excluded; rejected-
verdict rides contribute their persisted zeros, i.e. nothing). Week =
current UTC calendar week from Monday 00:00; month = from the 1st 00:00
UTC; both starts returned in the response. Unit-tested boundaries
(Mon/Sun/month-turn). No per-user timezone (documented limitation).

## 4. Security and ownership enforcement

All four read paths enforce `user_id` ownership server-side
(`get_owned` → 404; list/overview filter by owner). No cross-user
exposure (tested both directions). No tokens/credentials logged; trail
responses contain coordinates only for the owning rider.

## 5. Test commands and exact results

Backend: `python -m pytest -q` (full) → **1261 passed, 0 failed**
(1243 baseline + 18 new: engine mirror 5, history/points/overview 13).
`ruff check` + `ruff format --check` on touched files PASS; `mypy app`
PASS (113 files). No migration → head stays `0015_store_webhooks`,
no upgrade/downgrade run required.

Mobile: `flutter test` (full) → **788 passed** (768 + 20 new);
`flutter analyze` PASS; `dart format --set-exit-if-changed` PASS;
`flutter build apk --debug` PASS.

Notable investigations (not regressions — resolved without weakening
gates): (1) two resume tests failed mid-phase from a missing anchor
restore in ingest (fixed in service, tests kept); (2) ten training
tests failed from sensor starvation on held fixes (fixed in training
sampling, §1); (3) new feed tests hung on Riverpod's default error
retry (disabled per-provider, matching the unread-count precedent);
(4) router_l10n's home test hung on non-hermetic real storage/network
— shell now seeds session + failing transport (test-only change).

## 6. Known limitations and outstanding real-device checks

- Automated evidence only: feed/detail/dashboard proven against fake
  transports; no on-device pass of these screens yet.
- Week/month are UTC, not rider-local; elevation gain is server-persisted
  (see Phase A+B limits on phone altimetry).
- Trail cap 2000 (max 5000) with truncation flag; ultra rides draw a
  leading window on detail (metrics unaffected).
- History has no offline cache (no convention exists); offline shows an
  honest error + retry.
- `/settings` remains the last placeholder; global green theme, splash
  PNGs, and tile user-agent items carry over from prior phases.

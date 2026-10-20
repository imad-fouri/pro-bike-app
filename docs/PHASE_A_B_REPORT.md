# Phase A+B Report — GPS Reliability and Live Recording Map

Status convention: PASS / FAIL / BLOCKED / NOT TESTED.
Nothing in this work was committed.

## 1. Files changed and rationale

Mobile (`mobile/`):

```text
lib/features/ride/domain/gps_processor.dart
  Confidence-anchor metering: distance/moving/elevation accrue only across
  fixes displaced beyond the confidence radius; sub-confidence fixes hold
  (reason 'stationary', stored but unmetered). Elevation accrues only on
  confident segments. Documented, testable GpsPolicy additions.
lib/features/ride/domain/location_source.dart
  Android: plugin foreground service activated via
  ForegroundNotificationConfig (localized title/text threaded from UI);
  new check-only hasBackgroundPermission() (Android 11+ needs the settings
  route — never auto-prompted).
lib/features/ride/data/ride_recorder.dart
  Metered-fix track buffer (capped 1000) for the live map; platform Doppler
  speed preferred for the live readout; backgroundLimited flag surfaced.
lib/features/ride/presentation/ride_providers.dart
lib/features/ride/presentation/ride_start_page.dart
lib/features/ride/presentation/ride_summary_page.dart
  Threading for foreground strings (localized at the UI call sites).
lib/features/ride/presentation/ride_live_map.dart (new)
  Live flutter_map: track polyline, rider/start markers, follow mode with
  gesture-aware pause, recenter + fit-track actions, waiting/tiles-offline
  states, always-on attribution.
lib/features/ride/presentation/ride_recording_page.dart
  Doodle + dead buttons replaced by RideLiveMap; background-limited notice.
lib/core/l10n/app_localizations.dart
  7 ride.* keys × en/fr/ar (fgTitle/fgText/bgLimited/recenter/fitTrack/
  waitingFix/tilesOffline).
```

Backend (`backend/`, no migration — schema untouched):

```text
app/services/gps_engine.py
  Identical anchor mirror (GpsConfig gains confidence_factor,
  min_confident_displacement_m, default_accuracy_m; 'stationary' verdict).
  Required: finish-time summaries and ranking inputs must use the same
  rules as the live display, or the client fix would be cosmetic only.
app/services/ride_service.py
  Ingest rebuilds the anchor from the last accepted fix (the last fix that
  moved the meters — exactly the anchor definition). No migration: the
  anchor is derived from already-persisted points.
```

Tests:

```text
mobile/test/gps_filter_test.dart (new, 13 tests)
mobile/test/ride_test.dart (recorder track/speed/bg tests, map widget
  tests, key parity; fakes updated for the interface additions)
mobile/test/group_rides_test.dart (fake updated)
mobile/test/android_location_service_test.dart (new, 6 tests: permissions,
  no hand-written <service>, release cleartext-free)
backend/tests/test_gps_engine.py (5 new mirror tests; 14 pre-existing pass)
```

## 2. GPS algorithm and threshold decisions

- Gate: required displacement = max(3.0 m, accuracy × 1.0), default
  accuracy 8 m when the platform reports none. Rationale: fixes scatter
  within ~1σ, so sub-accuracy moves are noise.
- Held fixes advance seq/time (later fixes judge against fresh state) but
  move no meters and skip elevation — standing still cannot inflate rides.
- Slow movement is quantized, not lost: a 0.5 m/s walk accrues in ~5 m
  chunks (proven: 25 m in / 25 m out, moving time correctly 0 under the
  unchanged 1 m/s threshold).
- Elevation accrues only on confident segments against a ratcheted
  baseline (±3 m) — kills stationary altitude-drift gain. No DEM or
  barometer exists: sustained grade errors from bad phone altimetry
  remain possible (see §6).
- Jump (80 m/s), accuracy reject (25 m), ordering, and pause-segment
  semantics are byte-for-byte preserved; two backend resume tests that
  failed mid-phase were investigated and kept (they pin legitimate
  resume metering — the failure was a missing anchor restore in ingest,
  now fixed, not a behavior to change).
- Live speed prefers platform Doppler when sane, else segment speed.

## 3. Android permission/service configuration

- No hand-written native service (per task direction): the app activates
  the geolocator plugin's own `GeolocatorLocationService`
  (`foregroundServiceType="location"`), verified merged into the debug
  AND release APKs. App manifest keeps permissions only (fine/coarse/
  background + both foreground-service permissions + INTERNET).
- Runtime: foreground grant → check-only background probe →
  foreground-only recording with a visible notice when background is
  denied (settings-directed, no prompt loop — Play-policy compliant).
- Wake lock stays OFF (no new permission, no SecurityException surface);
  trade-off documented in §6.
- iOS unchanged (system location mode declared; platform default
  settings used).

## 4. Map implementation and tile provider

- Existing `flutter_map` + `MAP_TILE_URL` (OSM raster) + mandatory
  attribution chip — same configuration and policy posture as the route
  map. No new provider, no keys.
- Rider dot follows at zoom 16 until the user pans (gesture pauses
  follow); recenter resumes, fit frames the track. Track = metered
  fixes only, so the drawing always agrees with the distance.
- Offline: tiles fail → notice chip; polyline still draws (positions are
  local). No-fix state shows a waiting chip, never a crash.

## 5. Tests executed with exact results

| Check | Result |
|-------|--------|
| New `gps_filter_test.dart` (13) | PASS |
| Full mobile `flutter test` | PASS — 768 (742 baseline + 26 new) |
| `flutter analyze` | PASS — no issues |
| `dart format --set-exit-if-changed lib test` | PASS — 0 changed |
| `flutter build apk --debug` | PASS; merged manifests verified (plugin service + location type in both variants; release cleartext-free) |
| Backend `test_gps_engine.py` (19) | PASS |
| Backend `test_rides.py` + integrity + challenges + rankings + competition-security | PASS — 84 passed, 2 initially failed then fixed via anchor restore (see §2) |
| Backend `ruff check`, `ruff format --check`, `mypy` on touched files | PASS |

## 6. Known limitations (automated evidence only — see §8)

- No real-ride accuracy claimed: thresholds are reasoned, not measured.
- Sustained phone-altimetry bias can still inflate/deflate climbing; no
  DEM/barometer fusion exists.
- Max speed still uses per-fix spikes (preserved semantics); jitter can
  spike it.
- OEM Doze may batch fixes without a wake lock (positions keep correct
  timestamps; UI updates may lag on some devices).
- Swiped-away kill still ends the stream (plugin limitation); data is
  safe via pending-sync recovery, not continuous.
- System notification strings are localized via UI threading, but the
  notification-channel name stays the plugin default English.
- Tile user-agent still reads `com.cyclecoach.app` (pre-existing,
  carried over; needs correction with the tile scaling plan).
- Track buffer caps at 1000 points (~8+ h at 5 m fixes; ultra rides
  draw a trailing window — metrics unaffected).

## 7. Physical-device validation checklist (all NOT TESTED)

1. 10 km pocket-carry road ride, screen on: completes, distance within
   ±3% of bike computer/second phone, zero gaps.
2. Same ride, screen off 1 h: zero signal gaps; foreground notification
   persistent; battery delta noted.
3. 5 min stationary mid-ride: added distance <50 m.
4. Known 300 m climb: gain reads 270–330 m.
5. Airplane mode 10 min → online: full sync, zero duplicates.
6. Kill app at 50% → resume: completes, zero duplicates.
7. Background permission denied: notice shows, foreground recording
   works with screen on.
8. Steady 20 km/h: live speed reads 18–22, max sane.

## 8. Evidence separation

AUTOMATED (this phase): 768 mobile + backend focused suites green,
analyzer/lints clean, APK builds, merged-manifest inspection, widget
rendering of all map states. REAL-DEVICE: none — §7 is entirely open.
Do not describe GPS accuracy, background endurance, or map field
behavior as proven until §7 is measured.

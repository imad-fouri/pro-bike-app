# ADR-08 — Location: geolocator + Permission/Battery/Background Strategy

Date: 2026-09-27 | Status: Accepted | Phase 4

## Package: geolocator (Baseflow, mature, MIT)
Why not geolocator alternatives / custom channels: permission states
(denied/deniedForever/whileInUse/always), accuracy control, and
`getPositionStream` in one maintained API. No extra background plugin yet:
Android records via foreground-service declaration + `ACCESS_FINE_LOCATION`
(+ `ACCESS_BACKGROUND_LOCATION` only when the user enables background mode);
iOS via `NSLocationWhenInUseUsageDescription` + `Always` + background mode
`location`. A dedicated foreground-service plugin is deferred until
device-measured need (battery data first).

## Tracking policy (configurable, `TrackingPolicy`)
- Desired accuracy: `best` outdoors; distance filter **5 m**; no fixed time
  interval (position-driven, avoids timer wakeups).
- Battery modes: `balanced` (10 m filter, lower accuracy) vs `precise`
  (5 m, best). Default: precise while recording, balanced otherwise.
- Rationale: distance-driven updates scale cost with movement; stationary
  riders cost ~nothing. No premature optimization before measurement.

## Permissions (requested only when starting a ride, never at app start)
States: granted-while-in-use (record, warn background limits) →
request always (background) → denied (explain + settings deep-link) →
deniedForever/restricted (blocked UI, no silent failure). Approximate vs
precise (Android 12+): recording requires precise; otherwise blocked with
explanation.

## Verification status (honest)
- IMPLEMENTED + unit-tested: engine, permissions state machine (mocked),
  local-first recording, sync, recovery.
- DESIGNED + configured: Android manifest entries, iOS plist/mode entries.
- NOT device-verified: multi-hour locked-screen recording on real hardware
  (no device farm in this environment). Explicitly DEFERRED to device QA.

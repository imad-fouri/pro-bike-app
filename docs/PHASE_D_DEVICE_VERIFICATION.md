# PHASE D — Physical Device Verification

Status: **BLOCKED — no physical Android device attached.**

Phase D's automated gates are green (backend `1286 passed`; mobile `805 passed`;
APK built). The verification below was requested before Phase E, but every item
on the list exercises the hardware (real GPS fix quality, screen-off recording,
location notification) and **cannot be honestly run without a physical phone**.

## Exact blocker

A physical Android device is **not connected to this machine**:

- `adb devices` → empty (`List of devices attached`, no lines).
- `flutter devices` → only `Windows (desktop)`, `Chrome (web)`, `Edge (web)`.
  No `android-arm64`/`android` entry, no physical serial number.

Per instructions: device evidence was **not fabricated**, the report is marked
BLOCKED/NOT TESTED accordingly, and **Phase E has not been started.**

## What was verified before the blocker

| Check                                      | Result | Evidence |
|--------------------------------------------|--------|----------|
| Latest debug APK exists                    | PASS (build artifact) | `mobile/build/app/outputs/flutter-apk/app-debug.apk`, 246,249,385 bytes, built 2026-10-11 23:26 |
| Backend containers (Postgres/Redis) up     | PASS | `bikepromobile-postgres-1 Up`, `bikepromobile-redis-1 Up` (Docker) |
| Automated gates (this phase)               | PASS | backend 1286 passed; mobile 805 passed; ruff/mypy/alembic heads clean; `docs/PHASE_D_REPORT.md` |

## Per-item status

| # | Item | Status | Evidence |
|---|------|--------|----------|
| 1 | Install and launch the latest debug APK on a physical device | **BLOCKED** | No device attached (`adb devices` empty). |
| 2 | Device can reach the configured backend | **NOT TESTED** | Requires installed app + LAN/`--dart-define` backend URL on a device. |
| 3 | Search a real location + record actual geocoding response | **NOT TESTED** | Requires device. Backend endpoint covered by automated tests + live preflight (Nominatim hit) only. |
| 4 | Generate cycling route + confirm geometry follows roads | **NOT TESTED** | Requires device. OSRM live preflight returned one real route (1888 m/260 s) but no screen/geometry on hardware. |
| 5 | Save route, leave planner, reopen saved route | **NOT TESTED** | Requires device. Covered by widget tests only. |
| 6 | Start a ride via "Ride this route" | **NOT TESTED** | Requires device. Widget flow tested (scroll + tap → `/ride/start?route=<id>`). |
| 7 | Planned route and live recorded GPS track remain separate | **NOT TESTED** | Requires real GPS. Layer separation covered by widget test; hardware GPS needed. |
| 8 | Short outdoor ride; GPS distance vs a reasonable reference | **NOT TESTED** | Requires outdoor GPS fix. No device, no ride. |
| 9 | Lock-screen recording: gaps, notification behavior, distance continuity | **NOT TESTED** | Requires physical screen-off + foreground notification. |
| 10 | Upstream/network failure → honest UI error | **NOT TESTED** | Requires device. Offline/`noroute` paths covered by automated tests only. |
| 11 | Record device model, Android version, permissions, backend URL, steps, observations, logs | **BLOCKED** | No device to record these from. |

## Owner steps to unblock (then re-run this phase)

1. Connect an Android phone with **USB debugging enabled** and the existing
   Windows driver; then this machine's `adb devices` must show the serial.
2. Phone and test machine on the same Wi-Fi; set the network profile to
   **Private** and add the firewall rule for port 8000 (Phase 1 report owner
   steps), or use `flutter run --dart-define=API_BASE=http://<LAN-IP>:8000`
   with the backend bound to `0.0.0.0:8000`.
3. Confirm backend readiness: `curl http://<LAN-IP>:8000/api/v1/health` and
   `.../ready` before installing the APK.
4. Re-execute items 1–11 with the phone, filling in the evidence columns.

## Standing rule

No item in this document is marked PASS without physical-device evidence. Items
1, 11 remain BLOCKED; items 2–10 remain NOT TESTED until the phone is attached.
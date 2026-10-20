# WS-MOBILE-LIVE — Phase 1 Report: Development Device Configuration

Workstream: WS-MOBILE-LIVE ("Real Device Validation & Production-Like QA
Without App Store Billing"), Phase 1.
Status convention: PASS / FAIL / BLOCKED / NOT TESTED.
Nothing in this phase was committed.

## 1. Objective

Make a physical Android phone able to reach a development CycleCoach API over
the local network (`http://<COMPUTER-LAN-IP>:8000`) while keeping release
builds HTTPS-only and production configuration fail-closed. No store accounts
were purchased, no billing was implemented, and no production security was
weakened.

## 2. Files changed

Modified:

```text
mobile/android/app/src/main/AndroidManifest.xml
  - Added android.permission.INTERNET with an explanatory comment.
  - No cleartext allowance added. No network security config referenced.

mobile/android/app/src/debug/AndroidManifest.xml
  - Added a debug-only <application android:networkSecurityConfig="@xml/..."/>
    attribute merge. Release builds never see this file.
```

Created:

```text
mobile/android/app/src/debug/res/xml/network_security_config.xml
  - Debug-source-set-only network security config permitting cleartext HTTP.
  - Keeps system trust anchors. No host is hardcoded.

mobile/test/android_network_security_test.dart
  - 9 automated tests locking the posture below.

docs/PHASE_MOBILE_LIVE_PHASE_1_REPORT.md
  - This report.
```

No backend, ranking, challenge, integrity, or subscription files were changed.
No LAN IP, credential, or secret was committed.

## 3. Security decisions

1. INTERNET in the release manifest (fixes Phase 0 finding F1). The
   permission only grants socket access; on API 28+ cleartext HTTP stays
   blocked by the platform default. A release APK can now reach an HTTPS API
   and still cannot send cleartext.
2. Cleartext is permitted ONLY under `src/debug` (fixes Phase 0 finding F2).
   The config merges into debug builds and is absent from release builds, so
   release stays HTTPS-only by construction, not by convention.
3. No LAN IP is hardcoded anywhere. The phone build always receives its API
   URL through the existing `--dart-define=API_BASE=...` mechanism, which the
   app validates fail-closed at startup (`AppConfig.validate()`).
4. The device-side production gate still rejects a cleartext LAN URL when
   `APP_ENV=production`, proven by test (a misbuilt release fails at startup
   instead of leaking tokens).
5. Firewall guidance below opens ONLY TCP 8000, scoped to the Private
   profile, and never disables the firewall. The machine's Wi-Fi profile is
   currently Public, so the owner must first set the home network to Private.
6. Database credentials were NOT changed. The compose-file/dev-default
   password discrepancy (Phase 0 finding F3) is inert: the Postgres volume
   was initialized with the dev-default password, which the running
   container, the test suite, and the dev server all use successfully.

## 4. API URL configuration

- Debug phone builds: `--dart-define=APP_ENV=dev
  --dart-define=API_BASE=http://<COMPUTER-LAN-IP>:8000`
  (`WS_BASE` defaults to `ws://localhost:8000` and is consumed by nothing at
  runtime; chat uses HTTP polling.)
- Release builds: must pass an `https://` API_BASE with `APP_ENV=production`
  or the app refuses to start.
- Computer LAN IP at verification time: `192.168.1.40` on the Wi-Fi adapter
  (other listed addresses are WSL virtual adapters and are NOT usable by the
  phone). The IP can change; section 8 shows how to re-check it. It is
  intentionally not stored in the repo.

## 5. Test results

| Check | Result |
|-------|--------|
| New `android_network_security_test.dart` (9 tests) | PASS |
| Full mobile suite `flutter test` | PASS — 718 passed (709 baseline + 9 new) |
| `flutter analyze` | PASS — no issues |
| `dart format --set-exit-if-changed lib test` | PASS — 0 changed |
| Backend tests | NOT RUN — no backend files changed |
| Pre-existing backend lint/format debt (`ruff check .` 2× F541, `ruff format --check .` 10 baseline files) | Unchanged, still pre-existing; unrelated files were NOT touched to chase green |

One build failure occurred mid-phase and was fixed: the first draft of the
debug XML comment contained `--dart-define`, and `--` is illegal inside XML
comments (AAPT `parseDebugLocalResources` failure). The comment was reworded;
no behavior changed.

## 6. Debug APK result

- `flutter build apk --debug`: PASS — fresh `app-debug.apk` (167.7 MB).
- APK contents verified: contains `res/xml/network_security_config.xml`.
- Merged-manifest verification via real Gradle merge
  (`:app:processDebugMainManifest`, `:app:processReleaseMainManifest`):
  - Debug merge: INTERNET present, `networkSecurityConfig` present on
    `<application>`, no `usesCleartextTraffic` flag. PASS.
  - Release merge: INTERNET present, NO `networkSecurityConfig`, NO
    cleartext flags. PASS.

## 7. Release networking verification

- Release merged manifest grants INTERNET and permits no cleartext: PASS.
- No release signing/publishing performed (release variant uses the debug
  signing key in this repo; no production keystore exists and none was
  created).
- Production fail-closed behavior unchanged and covered by
  `security_config_test.dart` plus the new cleartext-LAN rejection test.

## 8. Backend / network verification (this machine)

- Docker Postgres 16 + Redis 7: Up, ports 5432/6379 bound. PASS.
- Dev API started with `uvicorn app.main:app --host 0.0.0.0 --port 8000`:
  - `GET /api/v1/health` via `127.0.0.1:8000`: ok, database up, redis up.
  - `GET /api/v1/ready` via `127.0.0.1:8000`: ready, database up, redis up.
  - `GET /api/v1/health` via `192.168.1.40:8000`: ok — the server IS
    reachable on the LAN interface. PASS.
  - Server stopped afterwards; port 8000 confirmed free (no stale process).
- Windows Firewall: NO inbound rule for port 8000 exists, so a phone cannot
  connect yet — expected. Rule creation is an owner step (section 9).
- Physical-device connectivity: NOT TESTED — no phone has connected yet. No
  success is claimed here.

## 9. Remaining blockers / next steps

1. Owner must connect the Android phone (Phase 2): same Wi-Fi, set network
   to Private, add the Private-scoped port-8000 firewall rule, start the dev
   API, run with `--dart-define=API_BASE=http://<LAN-IP>:8000`.
2. Apple/Google real billing remains BLOCKED (no store accounts) — unchanged.
3. External push delivery remains BLOCKED (no FCM/APNs credentials).
4. iOS real-device testing remains NOT TESTABLE from this Windows host.

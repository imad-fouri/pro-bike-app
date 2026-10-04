# Phase 6 Report — Training Engine & Cycling Coaching Foundation

Status: **complete**. No Phase 7 work was started.

## What shipped

**Deterministic calculation engine** (`backend/app/services/training_calc.py`)
Pure functions only — no database, no HTTP, no clock, no randomness. Twelve
registered calculation versions, each carrying its own formula, parameters and
kind. AI is never consulted; a version string travels with every stored number.

**Persistence** (migration `0006_training_foundation`)
`training_profiles`, `ftp_records`, `training_activities`, `training_activity_zones`,
`training_loads`, `training_calculation_versions`, `workouts`, `workout_steps`,
plus optional `power_w` / `hr_bpm` / `cadence_rpm` on ride points. Adding the
optional sensor columns is deliberately not a signal change: GPS-only rides keep
working and produce activities with null power metrics.

**API** — 11 endpoints, all owner-scoped, foreign resources return 404:

| Path | Purpose |
| --- | --- |
| `GET/PUT /training/profile` | Read and update training inputs. |
| `GET/POST /training/ftp-records` | Append-only FTP history. |
| `GET /training/activities`, `GET /{id}` | Metric history and detail. |
| `POST /training/activities/{ride_id}/reanalyze` | Explicit re-derivation. |
| `GET /training/loads` | Daily CTL/ATL/TSB. |
| `GET /training/recovery` | Load-change signal with evidence. |
| `GET /training/summary` | Everything the overview screen needs in one call. |
| `GET /training/calculation-versions` | Formula registry, code-first. |
| `GET/POST /workouts`, `GET/PATCH/DELETE /workouts/{id}` | Structured workouts with optimistic locking. |

A completed ride automatically produces a training activity.

**Mobile** (`cyclecoach`)
Riverpod + repository, en/fr/ar with Arabic RTL. Overview, activity history,
settings, calculation-version browser, workout list/create/edit. Presentation-only
unit conversion; null stays visibly "not measured" rather than rendering `0`.

## Invariants enforced

- **Missing input is `NULL`, never `0`, never a guess.** No power data means no
  intensity factor. Nothing is ever estimated from GPS speed or from age.
- **FTP is append-only.** Resolution order: confirmed beats `estimated`, then
  newest `effective_at`, then source rank, then `created_at`, then `id`. `id` is a
  UUID, so it only guarantees determinism — recency comes from the date columns.
- **Test protocols are server-derived.** A client-asserted test value is never
  transmitted and never trusted; the ride decides.
- **History is immutable.** Adding an FTP record does not rewrite stored metrics.
  Re-deriving is an explicit action.
- **Null-aware partial updates.** An unchanged or empty field is omitted from the
  request, so a save never appends a duplicate FTP record or resets a value.
- **Optimistic locking.** A stale workout write is a `409 VERSION_CONFLICT` in the
  structured envelope, not a silent last-write-wins.

## Defects found and fixed during verification

1. **`ApiClient._send` had no `PUT` case.** The new `put()` fell through to
   `default` and silently issued a **GET**, so profile saves were broken. Fixed in
   `mobile/lib/core/network/api_client.dart`.
2. **Decimal fields crashed the client.** Pydantic serializes `NUMERIC` as JSON
   **strings**, but every training model cast with `as num?`, which throws on real
   API data while passing hand-written numeric fixtures. Added
   `mobile/lib/core/units/api_number.dart` and converted all ~50 call sites.
3. **Client-asserted test FTP was transmitted.** The repository sent `value_w` for
   test sources, contradicting ADR-10. Now omitted via `FtpSource.isTestDerived`.
4. **Every settings save appended an FTP record.** The field is prefilled with the
   current value, so an untouched save duplicated history on each visit. Now only a
   genuine change is sent.
5. **Failed saves reported success.** The snackbar always said "Saved" because the
   notifier swallows the error into its state. Now reads the state back.
6. **Every workout error was labelled a version conflict.** A network failure was
   presented as "someone else changed this". Now only `VERSION_CONFLICT` maps to
   the conflict message.
7. **Dev database was at `0005`.** The training tables did not exist, so every live
   training call returned 500 while the isolated test suite passed. Applied
   `alembic upgrade head` — worth remembering that a green suite does not prove a
   migrated database.

## Verification

| Gate | Result |
| --- | --- |
| `ruff check .` | clean |
| `ruff format --check .` | 73 files formatted |
| `mypy app` | clean, 53 source files |
| `python -m pytest -q` | **229 passed**, 1 pre-existing asyncpg warning |
| `dart format --set-exit-if-changed .` | clean |
| `flutter analyze` | no issues |
| `flutter test` | **72 passed** (26 training + 46 existing) |
| `flutter build web` | `mobile/build/web` |
| `flutter build apk --debug` | `mobile/build/app/outputs/flutter-apk/app-debug.apk` |
| Live smoke, Phase 5 | 27/27 |
| Live smoke, Phase 6 | 54/54 |

The Phase 6 live smoke runs against real Postgres and two independent users, so it
proves the ownership boundary end to end: a second user gets 404 on activity read,
reanalyze, workout read, and workout update. It also confirms a GPS-only ride yields
null power rather than a fabricated zero, and that a test-protocol FTP without a
usable ride is refused.

## Deliberately deferred

No AI Coach, no social features, no BLE or sensor hardware. The engine is built so
an explanation layer can be added on top without ever becoming a source of truth,
and the sensor columns are ready for real hardware without pretending to have any.

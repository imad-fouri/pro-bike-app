# PHASE 8.5 CI REMEDIATION REPORT — Lint/Format Gate → Green CI

Date: 2026-10-04
Scope: make the Phase 8.5 remediation CI workflow pass. **Phase 8.6 was not
started.** No product behavior, API, schema, test, or security semantic was
changed (whitespace, import order, and provably-dead code only).

---

## 1. Original CI run

| Field | Value |
|---|---|
| Workflow | `ci` (`.github/workflows/ci.yml`) |
| Run | `37194631512` |
| Head SHA | `fc74203` |
| Result | **FAILURE** — both jobs failed fast at dependency-install steps |

Follow-up runs `37199852786` (`450ce8b`) and `37200263745` (`ba33a5e`,
action pins) failed identically, ruling out transient blips and the
action-version theory. Step logs are admin-gated; install errors were never
directly observed.

## 2. Failed jobs (original)

| Job | Failed step | Duration |
|---|---|---|
| `backend` | `pip install -e ".[test]" ruff mypy` | ~15 s |
| `mobile` | `flutter pub get` | ~11 s |

No test, lint, migration, or build step executed in those runs.

## 3. Exact dependency errors

The install-step errors remain admin-gated (API 403 twice-confirmed, web UI
"Sign in to view logs"). What the investigation **did** establish instead:

- Backend install proven valid in three clean configurations: Windows venv
  (pip 25.0.1 and 26.2.1) and Linux `python:3.12-slim` container — all exit 0.
- `flutter pub get` resolves cleanly with the committed `pubspec.lock`; all
  139 hosted locked versions checked against CI's Dart 3.13.5 via pub.dev —
  none excluded (5 pre-3.0 packages proven accepted by pub with a minimal
  repro).
- Conclusion: no repo-side install defect existed. (A later run, §8, then
  showed installs succeeding — consistent with runner-side trouble that
  cleared.)

## 4. Root cause (of the RED gate, as finally observed)

With install steps passing, the workflow proceeded to lint/format — and the
**real, deterministic** failures surfaced:

1. **Backend: local ruff was stale (0.8.4); CI installs current (0.16.10).**
   `ruff check app tests` passed locally for months while CI's newer ruff
   reported **48 errors** (2 safe-fixable, 45 unsafe, 1 unfixable):
   43× RUF059 (unpacked-but-never-read variables, mostly test `_user()`
   unpackings), 2× TRY201 (`raise exc`), 1× SIM102 (nested `if`),
   1× I001 (import sort), 1× RUF022 (`__all__` sort).
2. **Backend format drift:** 8 files needed reformatting under ruff-format
   0.16 (pre-existing style drift, flagged lines untouched by this work).
3. **Flutter: formatter-version drift.** Local Dart 3.12.2 reported
   `126 files (0 changed)`; CI's Dart 3.13.5 (Flutter 3.47.6) reformats
   `test/chat_test.dart` (one `expect` block, new line-splitting style).
   Reproduced **exactly** (`126 files (1 changed)`) with a downloaded Dart
   3.13.5 SDK before fixing.

## 5. Files changed

| File | Change |
|---|---|
| `backend/app/api/v1/chat.py` | I001 import sort (`--fix`) |
| `backend/app/notifications/provider.py` | RUF022 `__all__` sort (`--fix`); TRY201 ×2 → bare `raise` (identical semantics) |
| `backend/app/services/team_service.py` | SIM102: merged pure nested condition (identical semantics) |
| `backend/app/services/chat_service.py` | Removed one provably-dead pure assignment (`low, high` never read) |
| 8 backend files (teams/config/models/schemas + 3 test files) | `ruff format` whitespace-only line joins |
| `backend/tests/test_chat_api.py`, `test_notifications_api.py`, `test_teams_api.py` | RUF059 ×41: underscore-prefix provably-unread unpack targets (line-targeted script, LHS-only, collision-checked) |
| `mobile/test/chat_test.dart` | `dart format` 3.13.5 line-break restyle (whitespace only) |
| `.github/workflows/ci.yml` | Pinned checkout v7.0.1, setup-python v7.0.0, flutter-action v2.23.0 (run commands byte-identical) |

**No unsafe-fixes used.** `--fix` applied only the 2 safe fixes; every other
edit was manual, reviewed in `git diff`, and behavior-preserving.
`analysis_options.yaml` untouched. No API, schema, auth, ride, team,
notification, or AI change. No test added, removed, or weakened.

## 6. Fix implemented

Lint/format remediation only, as inventoried in §5.

## 7–8. Backend / Flutter local installation results

Backend install exit 0 on Windows (pip 25 + 26) and Linux slim; Flutter
`pub get` clean with the committed lockfile (byte-identical after regen).

## 9. Backend tests

Full suite after lint fixes: **564 passed, 0 failed** (affected files first:
68/68 chat+team-unit; then full run).

## 10. Flutter tests

Full suite after formatting: **441 passed** (unchanged count —
whitespace-only change).

## 11. Ruff

`ruff check .` (CI's ruff 0.16.10): **All checks passed.**
`ruff format --check app tests`: **106 files already formatted.**

## 12. mypy

**Success: no issues found in 85 source files.**

## 13. Flutter analyze

**No issues found.**

## 14. Dart format

New-formatter check (`dart format --set-exit-if-changed lib test`, Dart
3.13.5): **126 files, 0 changed, exit 0.**

## 15. Migration validation

Unchanged (no schema change): `downgrade -1` → `0009_chat`,
`upgrade head` → `0010_notifications`, single head.

## 16. Build result

`flutter build web --release`: succeeded (prior remediation validation; no
build-relevant change since — one test file's whitespace).

## 17. New GitHub Actions run

| Field | Value |
|---|---|
| Run | `37204105736` |
| Head SHA | `33f3edd` ("CI lint/format: satisfy ruff 0.16 and dart 3.13 gates") |
| Event | `push` |
| Result | **SUCCESS** — `backend: success`, `mobile: success`, every step success |

CI steps are single `run:` blocks executed with bash `-e`, so job success
means every command exited 0: installs, `ruff check`, `ruff format --check`,
`mypy`, `alembic upgrade head`, full `pytest -q`, downgrade/upgrade cycle,
`flutter pub get`, `dart format` check, `flutter analyze`, full
`flutter test`.

## 18. Final CI result

**SUCCESS.** The Phase 8.5 CI gate is green on a descendant commit containing
the remediation (`b089887`, `fc74203` ⊂ `33f3edd`).

## 19. Remaining issues

1. The original install-step failures (runs `...512`, `...786`, `...745`)
   were never directly observed (admin-gated logs) and never reproduced
   locally; they cleared without any repo-side install change. If they
   recur, the repo owner should read the step logs — likely runner egress.
2. CI pins no lockfile for pip (by design — installs from `pyproject.toml`);
   only `pubspec.lock` pins versions.
3. The workflow still runs no `flutter build`, secret scan, or dependency
   audit despite `docs/09-testing-cicd.md` describing them — pre-existing
   scope gap, unchanged here.
4. Local dev machines should upgrade ruff past 0.8.x and Flutter past 3.44
   to match CI, or these version-drift failures will recur silently.

---

## Final status

**PASS.**

Phase 8.5 CI gate is green on run `37204105736` (head `33f3edd`). All five
original blockers remain fixed and untouched by this change (whitespace,
import order, and dead code only — proven by 564 + 441 green suites).
Per the phase instructions: **Phase 8.5 = PASS. STOP. Phase 8.6 not started.**

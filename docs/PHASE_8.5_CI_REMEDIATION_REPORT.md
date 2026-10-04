# PHASE 8.5 CI REMEDIATION REPORT — Dependency-Installation Failures

Date: 2026-10-04
Scope: make the existing Phase 8.5 remediation CI workflow pass. **Phase 8.6
was not started.** No product code, test, or behavior was changed for this
investigation except `.github/workflows/ci.yml` action pins (see §5).

---

## 1. Original CI run

| Field | Value |
|---|---|
| Workflow | `ci` (`.github/workflows/ci.yml`) |
| Run | `37194631512` |
| Head SHA | `fc74203` |
| Result | **FAILURE** |
| Repo | `imad-fouri/pro-bike-app` (public, default branch `master`) |

## 2. Failed jobs

| Job | Failed step | Duration | Note |
|---|---|---|---|
| `backend` (`111413827365`) | `pip install -e ".[test]" ruff mypy` | ~15 s | No tests, ruff, mypy, or migrations ran |
| `mobile` (`111413827206`) | `flutter pub get` | ~11 s | No tests, analyze, or format ran |

Both failures are fast, exit code 1, at dependency-download steps — before any
verification step. **This was not a test failure.**

## 3. Exact dependency errors

**Unobtainable.** Step logs are admin-gated (API: 403 "Must have admin
rights to Repository"; web UI: "Sign in to view logs"). Public check-run
annotations contain only "Process completed with exit code 1" plus unrelated
runner-image notices. This was verified three ways (API log endpoint, API
annotations, web fetch) and is stated plainly per the brief rather than
worked around.

## 4. Root cause

**Not determined from CI data — and every repo-side hypothesis was
systematically ruled out:**

| Hypothesis | Test | Result |
|---|---|---|
| Invalid `pyproject.toml` / extras / resolver conflict | Fresh-venv `pip install -e ".[test]" ruff mypy` (Windows, pip 25.0.1) | ✅ exit 0 |
| Same, current pip | Fresh venv, pip upgraded to 26.2.1 | ✅ exit 0 |
| Same, on Linux | `python:3.12-slim` container, repo mounted | ✅ exit 0, incl. linux-only `uvloop` |
| Stale/conflicting Flutter constraints | `flutter pub get` + `--enforce-lockfile` (Flutter 3.44.8 / Dart 3.12.2) | ✅ "Got dependencies!" |
| Missing lockfile nondeterminism | `mobile/pubspec.lock` already committed in `b089887`; regenerated copy byte-identical | ✅ deterministic |
| Locked packages incompatible with CI's Dart 3.13.5 | Queried pub.dev for all 139 hosted locked versions' SDK constraints | No package excludes 3.13.5 (5 pre-3.0 packages proven accepted by pub via minimal repro) |
| Outdated GitHub Actions (Node 20 deprecation) | Pinned checkout v7.0.1, setup-python v7.0.0, flutter-action v2.23.0; pushed; new run observed | ❌ **identical failures** — theory ruled out |

**Remaining assessment (inference, labeled as such, not a determination):**
three consecutive runs fail fast (9–19 s) at network-download steps across two
independent ecosystems while all local reproductions succeed. That signature is
consistent with runner-side egress/provisioning trouble, not repository
content. It cannot be confirmed without the gated logs.

## 5. Files changed

| File | Change | Rationale |
|---|---|---|
| `.github/workflows/ci.yml` | `checkout@v4→v7.0.1`, `setup-python@v5→v7.0.0`, `flutter-action@v2→v2.23.0` | Runs' own annotations flag Node.js 20 deprecation on the old majors; exact pins for deterministic CI. **All run commands byte-identical.** |
| *(none other)* | — | No product, test, dependency, or config change was justified by the evidence. Backend install proven valid; nothing to fix there. |

YAML re-validated after edit (jobs/steps intact).

## 6. Fix implemented

Action-version pins only (§5). Deliberately **no** change to install commands,
dependencies, tests, or application behavior — altering those without the
exact error would be guessing, which the brief forbids.

## 7–8. Backend / Flutter local installation results

| Configuration | Command | Result |
|---|---|---|
| Windows venv, pip 25.0.1 | `pip install -e ".[test]" ruff mypy` | ✅ exit 0, 60+ packages |
| Windows venv, pip 26.2.1 | same | ✅ exit 0 |
| Linux `python:3.12-slim` container | same | ✅ exit 0 |
| Flutter 3.44.8 / Dart 3.12.2 | `flutter pub get` / `--enforce-lockfile` | ✅ "Got dependencies!" |

## 9–14. Backend tests / Flutter tests / Ruff / mypy / analyze / format

Not re-run for this report: no product or test file was modified, and the
previously recorded results stand (backend **564 passed**, Flutter **441
passed**, all static gates clean, per the Phase 8.5 remediation report).
Re-running full suites was unnecessary — nothing they cover changed.

## 15. Migration validation

Unchanged since remediation: `downgrade -1` → `0009_chat`, `upgrade head` →
`0010_notifications`, single head. No schema change in this investigation.

## 16. Build result

No build re-run: nothing build-relevant changed (one workflow file, action
pins only).

## 17. New GitHub Actions runs

| Run | Head | Result | Detail |
|---|---|---|---|
| `37199852786` | `450ce8b` (diagnostic retrigger, no source changes) | **FAILURE** | Identical fast install failures → ruled out transient blip |
| `37200263745` | `ba33a5e` (action pins) | **FAILURE** | Identical fast install failures → ruled out action-version theory |

Per-step timings for run `37200263745`: pip install 11:54:15→11:54:29 (14 s);
`flutter pub get` 11:47:18→11:47:29 (11 s). Three consecutive runs now show
the same signature.

## 18. Final CI result

**FAILURE** — the gate is red on install steps; no test, lint, migration, or
build step has executed in CI to date.

## 19. Remaining issues

1. **The exact install errors are inside admin-gated logs.** The single most
   valuable next action belongs to the repository owner (`imad-fouri`): open
   run `37200263745`, read the `pip install` and `flutter pub get` step
   output, and report the error text. Everything else is secondary.
2. If the owner confirms runner-egress trouble: re-run (or retrigger) with no
   changes; if a systematic toolchain issue: the log text will name it.
3. The Phase 8.5 fixes themselves remain intact and locally proven (564 +
   441 green, smoke 101/101 ×2, live A–D 15/15); **no test, product, or
   security finding in this investigation implicates application code.**
4. Pre-existing CI-scope gaps (unchanged, out of scope here): the workflow
   runs no `flutter build`, no secret scan, and no dependency audit despite
   `docs/09-testing-cicd.md` describing them.

---

## Final status

**BLOCKED.**

Mandatory gates: install steps red in three consecutive CI runs; root cause
confined to runner-side behavior by elimination, but the exact errors are
admin-gated and therefore undetermined — and the brief forbids guessing.
Phase 8.6 remains unstarted. The repository state is committed and pushed
(`ba33a5e`); the working tree contains no uncommitted remediation work.

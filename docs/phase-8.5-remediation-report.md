# PHASE 8.5 REMEDIATION REPORT — Blocker Closure

Date: 2026-10-04
Audit basis: `docs/phase-8.5-architecture-audit.md` (verdict: BLOCKED)
Scope: the five audit blockers plus the two directly related production defects
(DevEmailService, CI/git integrity). **No Phase 8.6 feature work was performed.**

---

## 1. Blocker #1 — Access token refresh

**Original blocker.** Flutter declared `refreshSession()` but never called it;
no 401 handling existed anywhere. Access tokens expire after 15 minutes, so
every session died and no ride longer than 15 minutes could sync.

**Root cause.** The transport (`ApiClient._send`) threw on the first 401 and
the refresh helper had zero call sites across all 125 Dart files. Recovery was
not merely buggy — it was absent.

**Code fix** (`mobile/lib/core/network/api_client.dart`,
`mobile/lib/features/auth/data/auth_repository.dart`,
`mobile/lib/features/auth/presentation/auth_state.dart`):

- `ApiClient` accepts `refreshAccessToken` and `onAuthFailure` callbacks plus a
  single `_refreshInFlight` future. `_withAuthRefresh` wraps **every** request
  shape (`_send`, multipart, binary GET): on 401 of an authenticated,
  non-session request it awaits one serialized refresh, then retries the
  original request **exactly once**. A second 401 is terminal for that request.
- Concurrent 401s share the in-flight refresh future instead of rotating
  independently. This is mandatory, not incidental: the backend burns the whole
  refresh-token family on reuse (ADR-04), so two concurrent rotations would log
  the user out of every device.
- The refresh call itself uses `auth: false` and session endpoints are excluded
  by `_isSessionEndpoint`, so a failed refresh can never recurse.
- On refresh failure, `onAuthFailure` runs once (by the refresh originator, not
  per waiter): `AuthNotifier.handleSessionExpired` revokes server-side
  best-effort, clears credentials mandatorily, and sets `unauthenticated`,
  which routes to re-login.
- `AuthRepository._savePair` validates both tokens are non-empty strings before
  persisting, closing the `'${pair[...]}'` null-stringification hole. The
  refresh token is transmitted only to `POST /api/v1/auth/refresh`; nothing in
  the client logs tokens.

**Regression tests** (`mobile/test/auth_refresh_test.dart`, 6 tests):

| Test | Proves |
|---|---|
| expired access refreshes once and retries | 401 → 1 refresh → 200, tokens rotated |
| three simultaneous 401s share one refresh | `Future.wait` × 3 → 1 refresh POST, 6 protected calls (3 + 3 retries) |
| rotated refresh token is used next | single-use rotation chain enforced by the double |
| failed refresh clears credentials, no retry loop | 1 protected + 1 refresh call, tokens wiped |
| retry rejected after refresh is terminal | 1 refresh + 2 protected calls, then failure — no loop |
| session expiry requires re-login | `handleSessionExpired` → unauthenticated + wiped storage |

**Live verification.**

- Backend rotation chain, real PostgreSQL: expired JWT → 401; refresh → 200
  with a rotated pair; retry with the new access → 200; three concurrent
  expired requests → identical 401s with no family burn; consumed token → 401
  reuse detection. 7/7 checks (`live_85_abcd.py`, temp script).
- Real Flutter `ApiClient` against the live backend (temporary
  `flutter test`, removed afterwards): corrupted access token, three
  concurrent `GET /auth/me` → all 200, **exactly 1 refresh POST**, stored
  access rotated. This is the end-to-end proof the unit test predicts.

**Security implications.** No new mechanism was invented; the existing opaque
rotating family from Phase 2 is reused. The single-flight design exists
specifically to avoid tripping family-burn. Failure handling clears rather
than preserves credentials.

---

## 2. Blocker #2 — `finish()` strands ride data

**Original blocker.** `finish()` cancelled the only retry timer, uploaded at
most one 100-point chunk, marked the ride `completed` — and `unfinishedRide()`
filters to `recording`/`paused`, so the remainder became unreachable by every
query in the app. Permanent, silent data loss.

**Root cause.** Completion was a single in-memory step with no durable
intermediate state: nothing recorded "finish requested but drain incomplete",
and nothing could resume without the dead timer.

**Code fix** (`mobile/lib/features/ride/data/ride_recorder.dart`,
`ride_database.dart`, `ride_providers.dart`, `home_page.dart`,
`app_localizations.dart` en/fr/ar):

- Durable completion states: `finish()` persists `finalizing` **before** any
  network call, stops GPS, cancels timers, then `drainAndFinalize()` loops
  `_uploadNextChunk()` (existing idempotent chunks, watermark advances only on
  acknowledgement) until empty, then finalizes the server ride. Only then is
  the local row marked `completed`.
- Incomplete drain persists `pending_sync` — never `completed`. A crash between
  server-finalize and the local write is handled: the next drain's transition
  is rejected with `INVALID_TRANSITION`, and recovery confirms via `detail()`
  instead of failing.
- `_flushBuffer` now writes before clearing (previously cleared first —
  a kill in that window lost up to 20 points from memory *and* disk).
- `unfinishedRide()` includes `finalizing`/`pending_sync`; new
  `pendingSyncRides()` plus `RideSession.recoverPendingSync()` /
  `finishPendingRide()` provide headless recovery (new recorder instances,
  same durable database, no location tracking, no timer).
- Home shows an `_UnfinishedRideBanner` (existing l10n keys only) linking to
  the previously orphaned `/ride/recovery` page, so stuck work is discoverable
  after a restart.

**Regression tests** (`mobile/test/ride_test.dart`, 7 tests): finish drains
100 points in one chunk; 101 across the chunk boundary; 500 and 1000 points
with exact unique-uuid counts and chunk counts; network failure → `pending_sync`
(never `completed`) with both recovery probes finding the row; failed-chunk
retry then container-level `recoverPendingSync()` completing exactly once;
kill-mid-drain (1 chunk landed) recovering the remaining 150 points after
restart with zero duplicates.

**Live verification.** Real backend: 150-point ride uploaded across chunks,
`accepted=150`, finish returns `completed` with `accepted_points=150` (checks
B1–B3, `live_85_abcd.py`). Server restart durability is structural: uploads are
keyed by stable `client_point_uuid`/`seq` and the backend dedupes, so a restart
between chunks is a retry, not a loss.

**Security implications.** None — owner-scoped ride endpoints unchanged; the
recovery path reuses the same authenticated `RidesApi`.

---

## 3. Blocker #3 — Team-archive fan-out collapse

**Original blocker.** `_create_one` pre-checked `dedupe_key == key` with
`key is None` for unkeyed calls; SQLAlchemy renders that as `IS NULL`, matching
any prior unkeyed row. `notify_team_archived` was the only caller passing no
key. Live reproduction: 3 live members → 1 row, 1 recipient.

**Root cause.** A missing null guard combined with the one caller that relied
on unkeyed delivery.

**Code fix** (`backend/app/services/notification_service.py`):

- `_create_one` skips the pre-check entirely when `key is None` (the
  `IntegrityError` branch already understood the NULL case; the pre-check did
  not).
- `notify_team_archived` now passes `dedupe_key=f"team_archived:{team_id}"`,
  which `_create_one` suffixes per recipient. Archiving is terminal (no
  un-archive path exists in `team_service.py`), so a team-scoped key is safe: a
  retry returns the existing rows instead of duplicating them.

**Regression tests** (`backend/tests/test_notifications_api.py`): the invalid
`test_team_archive_notifies_members` was replaced by parametrized
`test_team_archive_notifies_every_member_exactly_once[1,2,3]`, asserting per
recipient: exactly one row, `type == team_archived`, `entity_type/id`,
`deep_link`, `l10n_key`; distinct row ids across members (N members → N rows);
and a repeated archive changing nothing.

**Proof the test catches the bug.** With both halves of the fix temporarily
reverted to the original code, the new test fails for 2 and 3 members and
passes for 1 — exactly the collapse signature. (Reverting only the guard while
keeping the key passes, confirming the key alone suffices and the guard is
defense in depth.) Fix restored afterwards; 3/3 pass.

**Live verification.** Real backend, 3-member team: archive → 200; each member
holds exactly one `TEAM_ARCHIVED` with exact entity/team/deep-link; 3 distinct
rows; repeated archive → unchanged counts (checks C1–C5, `live_85_abcd.py`).

**Security implications.** None — recipient scoping unchanged; the fix only
stops rows from being wrongly suppressed.

---

## 4. Blocker #4 — Invalid notification regression test

Covered in §3 above: the count-only test certified a broken path because the
second member's `total == 1` was an earlier `team_invitation`, not the archive
notice. Replaced per the test-quality rule (recipient set, type, team
association, no unexpected recipient). The revert-proves-failure check in §3 is
the evidence this class of false confidence is closed.

---

## 5. Blocker #5 — Production `SECRET_KEY`

**Original blocker.** `SECRET_KEY` defaulted to an in-repo literal, the sole
HS256 signing key, with no production validation — a deploy without the env var
booted successfully, signed with a public key.

**Root cause.** Fail-open defaults with no startup gate.

**Code fix** (`backend/app/core/config.py`, `backend/app/main.py`,
`backend/.env.example`):

- `Settings.require_production_secrets()` raises `RuntimeError` (never a
  fallback, never a generated key) when production and the key is missing,
  blank, the shipped default, or shorter than 32 characters. Messages name the
  failed check, never the value.
- `create_app()` calls it before serving anything.
- New `EMAIL_PROVIDER` setting (default `"dev"`) documents the selection point
  for a future real provider.

**Regression tests** (`backend/tests/test_foundation.py`, 10 tests): missing,
blank, dev-default, and short secrets rejected; 64-char secret accepted; error
messages never echo the value; non-production unaffected; `create_app` itself
raises with insecure production settings.

**Live verification (real processes).** `ENVIRONMENT=production` boot →
`RuntimeError`, exit 1. Placeholder secret → rejected, exit 1. Short secret →
rejected, exit 1. Valid 64-char secret → secret check passes. Development boot
with defaults → unaffected (live server ran throughout).

**Security implications.** Closes token forgery with a repo-public key. Note
the compounding factor is now explicit in the report: `sub` is the `users.id`
UUID returned by `GET /auth/me`, so any user-id leak previously converted to
full takeover.

---

## 6. DevEmailService

**Original defect.** Wired unconditionally; reset tokens never delivered
anywhere, and every raw token accumulated forever in process memory.

**Code fix** (`backend/app/services/email.py`): explicit
`build_email_service(environment, provider)` — production raises at import
(verified live: `RuntimeError`, exit 1) rather than silently swallowing
delivery; unknown names fail loudly; dev/test get a `DevEmailService` whose
outbox is bounded at 100 messages with oldest-first eviction. `.env.example`
documents `EMAIL_PROVIDER` and the production requirement.

**Regression tests** (`test_foundation.py`): bound proven (150 sends → 100
retained, oldest evicted, newest kept); production with `dev` or anything else
rejected; dev/test selection returns the double; unknown names rejected.

**Security implications.** Production can no longer silently run without
delivery; the credential-retention window is bounded by construction. A real
provider remains unimplemented by design — the fix converts silent misuse into
a loud startup failure.

---

## 7. Migration changes

**None.** No schema change was required by any fix (ride completion states are
client-local; notification dedupe uses the existing partial index; config and
email are code-only). Verified: `downgrade -1` → `0009_chat` (exit 0),
`upgrade head` → `0010_notifications` (exit 0), single head,
`test_migrations.py` 2 passed.

---

## 8. CI evidence

**Commit:** `b089887` — *Phase 8.5 remediation: close all five audit blockers*
(new project-scoped repository in `bike pro mobile/`; the enclosing home
directory was never a usable repo — zero commits, no remotes, entire home
directory untracked).

**Pre-commit verification:** `git status` reviewed; only intended paths staged
(370 files); `backend/cyclecoach_api.egg-info/` (build artifact) and the
pre-existing stray root script `_fix_team_doubletap.py` deliberately left
untracked; secret scan of staged content found only test fixtures
(`StrongPass123` etc.) and no private keys; no `.env` files exist or were
staged; the one `.env`-suffixed file is Flutter's benign build config,
ignored by `mobile/ios/.gitignore`.

**CI workflow:** `.github/workflows/ci.yml` (backend lint/type/test/migrate +
mobile format/analyze/test) **was not executed — no remote exists and no `gh`
CLI is available, so GitHub Actions cannot run.** This is stated plainly per
the brief: CI PASS is **not** claimed. Instead, every gate the workflow runs
was executed locally against the committed tree:

| Gate | Result |
|---|---|
| `pytest tests` | **564 passed**, 0 failed |
| `ruff check app tests` / `ruff format` | clean |
| `mypy app` | clean, 85 files |
| `alembic upgrade head` + `downgrade -1` + `upgrade head` | clean, single head |
| `flutter analyze` / `dart format` | clean / 126 files, 0 changed |
| `flutter test` | **441 passed** |
| `flutter build web --release` | succeeded |
| `live_smoke_notifications.py` | **101/101 × 2 runs**, exit 0 |
| Live A–D (`live_85_abcd.py`) | **15/15** |
| Live Flutter refresh (temp test, removed) | 3 concurrent → 1 refresh, all 200 |

**Flakiness note (honest).** Two earlier full-suite runs during this session
showed 3 and then 1 failures in timing-sensitive concurrency tests
(`test_concurrent_joins_resolve_to_one_membership`,
`test_concurrent_sends_get_distinct_sequences`,
`test_patch_on_a_private_team_is_404_not_403`) — all in teams/chat paths my
changes do not touch, all passing in isolation and in file-level runs
(87/87 teams). The final full run is **564/564**. These tests are load-
sensitive; CI should run them with `--numprocesses` isolation or retries
before anyone treats a red run as a regression.

---

## 9. Remaining findings

**Closed:** B1–B5, H1 (email), H2 (repo now exists with one commit; CI itself
still cannot run without a remote).

**Not in scope, unchanged, still valid from the audit:** the HIGH items around
observability (correlation-id mismatch, dropped log fields, unauthenticated
`/health` always-200), rate-limiter memory growth and proxy keying, the
`limit × workers` semantics, GPS buffer durability beyond finish (fixed for
finish; the 5 s periodic flush still clears-then-writes — now write-then-
clears, fixed as part of §2), validation-error secret reflection (H6),
`GET /teams/{id}/members` 500-where-404 (M27), refresh `FOR UPDATE` race (M1),
stale reset tokens (M2), enumeration oracles (M6), `user_id`-less local tables
beyond rides (routes cache), lifecycle observer absence beyond recovery
discovery, and the full documentation-discrepancy list (L10–L18), which was
explicitly **reported, not corrected**, per the audit brief.

**Known limitation of this remediation:** the Flutter refresh path treats any
401 on an authenticated request as refreshable; a backend that returns 401 for
non-expiry reasons (suspended account) triggers one wasted rotation before the
terminal failure clears the session. This is safe (single rotation, then
logout) but logs one extra refresh family per such event.

---

## 10. Final status

| Gate | Required | Result |
|---|---|---|
| access token refresh works | §26 | ✅ unit + live Flutter + live API |
| concurrent refresh is serialized | §26 | ✅ 3 concurrent → 1 refresh (unit + live) |
| refresh failure is safe | §26 | ✅ tokens wiped, unauthenticated, no loop |
| finish() cannot strand ride data | §26 | ✅ full drain, 100/101/500/1000 |
| finish() survives restart/network failure | §26 | ✅ pending_sync + headless recovery |
| team archive fans out to every eligible member | §26 | ✅ 1/2/3 members, live C |
| notification test verifies recipient/type/team | §26 | ✅ + proven to fail on old code |
| SECRET_KEY production validation exists | §26 | ✅ + live process checks |
| DevEmailService cannot silently run in production | §26 | ✅ import-time failure |
| git commit exists | §26 | ✅ `b089887` |
| actual CI run exists | §26 | ❌ **no remote — honestly reported, not claimed** |
| backend tests pass | §26 | ✅ 564 |
| Flutter tests pass | §26 | ✅ 441 |
| migration gate passes | §26 | ✅ |
| ruff passes | §26 | ✅ |
| mypy passes | §26 | ✅ |
| flutter analyze passes | §26 | ✅ |
| dart format passes | §26 | ✅ |
| web release build passes | §26 | ✅ |
| live smoke passes | §26 | ✅ 101/101 × 2 |
| documentation complete | §26 | ✅ this report + audit addendum below |

**One gate is honestly unmet:** no CI run exists, because there is no remote
for GitHub Actions to run against. Everything CI would execute was executed
locally against the committed tree with the results above. Per §27, which
requires ALL FIVE ORIGINAL BLOCKERS closed (they are) but also lists the CI
run as required: the five blockers are closed and proven; the CI-run checkbox
cannot be satisfied in this environment.

### Final decision

**PHASE 8.5 REMEDIATION: BLOCKED — solely on CI execution.**

All five original blockers are fixed, regression-tested, and live-verified.
The single remaining item is environmental, not technical: the remediation
commit `b089887` exists, but no CI run exists because the repository has no
remote. The moment this commit is pushed to a remote with Actions enabled, the
`.github/workflows/ci.yml` gates (all reproduced green locally above) will
execute. Until a CI run is observed, PASS cannot be claimed without violating
the brief's own honesty rule — so it is not claimed.

**STOP.** Phase 8.6 is not started.

# PHASE 8.6 RESULT - Production Hardening and Session Integrity

Date: 2026-10-04
Status: **PASS (local + live + remote CI run `37229175806`)**
Scope: close the Phase 8.5 audit's residual production risks - concurrent-write
races, unbounded in-process state, a logout/refresh race on the client, an
always-green health endpoint, and invisible `extra=` log fields. **No product
feature was added, no API contract was removed, and no test was deleted,
weakened, skipped, or xfailed.**

---

## 1. Baseline before any change

Baseline was established first, exactly as required, so every later number has
a reference point.

| Gate | Baseline | Final |
|---|---|---|
| Backend pytest | 564 passed, 1 warning | **583 passed, 1 warning** |
| Ruff (0.16.10, CI-pinned) | clean | clean |
| mypy (`app`, 85 files) | clean | clean |
| Flutter test | 441 passed | **462 passed** |
| `flutter analyze` | clean | clean |
| `dart format --set-exit-if-changed` | clean | clean |
| `flutter build web --release` | pass | pass |
| Alembic downgrade/upgrade | `0010` head, single head | unchanged |

The backend suite ran against real PostgreSQL and real Redis. Nothing in
`app/` is stubbed except the push provider, which has no network dependency by
design.

---

## 2. Defects closed

### 2.1 Concurrent refresh rotation minted two live sessions

`refresh()` read the session row without a lock, so two simultaneous
presentations of one refresh token both saw `revoked_at IS NULL` and both minted
a live descendant. Compromise containment was therefore not enforceable: the
"reuse" branch only triggers when the read observes a revoked row.

- Fix: `SELECT ... FOR UPDATE` on the presented session row
  (`app/services/auth_service.py:114`), so the second presenter blocks, then sees
  the committed `revoked_at` and trips reuse detection.
- Evidence: `tests/test_hardening.py::test_concurrent_refresh_yields_one_winner`
  asserts exactly `[200, 401]` and then asserts the *winner's* token is also dead
  (the loser burned the family).

### 2.2 A password reset left sibling tokens alive

Only the presented token was marked used. A second outstanding reset token -
the exact artifact an attacker who phished one email would hold - remained
valid after the password had already been changed.

- Fix: `app/services/auth_service.py:223` marks **all** outstanding reset
  tokens used at the moment the password changes.
- Evidence: `test_password_reset_invalidates_other_tokens` requests two reset
  tokens, spends the first, and requires the second to be rejected 400.

### 2.3 Concurrent ride chunk uploads could 500

Deduplication was read-then-write. Two identical chunks racing both passed the
check and one died on the unique constraint at commit, surfacing as an internal
server error on a client-recoverable condition.

- Fix: lock the ride row (`app/services/ride_service.py:223`) with
  `populate_existing`, re-check status on the locked row, and map a lost race
  to `409 CHUNK_CONFLICT` instead of a 500
  (`app/services/ride_service.py:312`). All-or-nothing: nothing from the losing
  chunk is stored, so the client can re-read and retry.
- Evidence: `test_concurrent_duplicate_chunks_never_500` - two identical chunks
  produce exactly `(accepted=2, duplicates=0)` and `(accepted=0, duplicates=2)`,
  the stored summary shows 2 points, and no 5xx.

### 2.4 `GET /teams/{id}/members` returned 500 where the contract says 404

The service raises `TeamError` for a missing team and for a private team the
caller cannot see; only the former was mapped, so a non-member probing a private
team got an internal error and an existence signal.

- Fix: `app/api/v1/teams.py:236` routes `TeamError` through the standard
  envelope.
- Evidence: `test_members_private_and_missing_team_identical_404` requires both
  cases to return a byte-identical 404 body with code `TEAM_NOT_FOUND`.

### 2.5 The rate limiter grew without bound

`_buckets` never expired keys and had no cap, so rotating keys (for example
source IPs against an IP-keyed route) grew process memory monotonically until
restart.

- Fix: `app/core/rate_limit.py` - each key stores `(hits, window_s)` so a sweep
  under one key's window can never widen another's limit; amortized full sweep
  at most once per second (`:22`); hard cap of 10 000 keys with
  oldest-inserted-first eviction, enforced in the sweep and again immediately
  after insert so the bound holds mid-burst (`:52`).
- Evidence: `test_limiter_memory_stays_bounded` inserts 18 000 distinct keys and
  asserts `len(_buckets) <= 10 000` while limiting still works;
  `test_limiter_keeps_per_key_windows` proves a sweep triggered by a short
  window does not widen a long one.

### 2.6 The health endpoint was always green

`/api/v1/health` answered 200 even with PostgreSQL unreachable, and it was the
only probe available - so an orchestrator could route traffic into a process
that cannot reach its database, and there was no way to tell the two states
apart.

- Fix: `/health` stays a pure **liveness** signal (always 200, dependency state
  reported in the body, `app/api/v1/health.py:22`); a new `/ready` endpoint
  (`:36`) returns 503 when PostgreSQL is down. Redis is reported but does not
  gate readiness, because no request path depends on it today - the comment at
  the endpoint records that this must be revisited when a worker or a shared
  limiter does.
- Evidence: four tests covering healthy readiness, Redis-blip readiness,
  database-down 503, and liveness staying 200 while dependencies are down.

### 2.7 `extra=` log fields were silently dropped

Every service logged an event name with structured fields; the formatter never
referenced the extra keys, so operators saw *that* something happened and never
*to what*.

- Fix: `ExtraFormatter` (`app/core/logging.py:87`) renders call-site extras as
  `key=value` and masks any key whose name contains `password`, `token`,
  `secret`, `api_key`, `apikey`, or `authorization` as `***`. The correlation
  filter moved from the handler to the logger (`:121`) so every record carries
  the context regardless of which handler consumes it.
- Evidence: `test_extra_formatter_renders_fields_and_masks_secrets`,
  `test_failed_login_logs_event_without_secrets` (the event name appears; the
  password and the email do not).

### 2.8 A reported correlation id was not the id in the logs

The middleware accepted any client `X-Request-ID` verbatim, then generated a
*different* id for the logs. A user-reported id was therefore unsearchable, and
an attacker-chosen value (including header-breaking characters) was reflected
back into a response header.

- Fix: `app/main.py:44` accepts a client id only if it is ASCII, alphanumeric
  (hyphen/underscore tolerated), and at most 64 characters; otherwise it mints
  one. The **same** id goes into the logs and the response header.
- Evidence: `test_request_id_echo_matches_log_record` (the id echoed to the
  client appears on the log records), `test_malicious_request_id_is_not_reflected`
  (CRLF, spaces, `..`, `a;b`, `<script>` are all replaced),
  `test_oversized_but_valid_request_id_is_truncated_not_dropped`.

### 2.9 Logout could be undone by an in-flight refresh

The client had no defence against this ordering: a request 401s, the refresh is
issued, the user taps logout, the local tokens are cleared, and then the refresh
response lands and **writes a fresh, valid session back to storage**. The user
believes they signed out while the app keeps a working credential pair.

- Fix: `AuthRepository` now carries a monotonic session generation
  (`mobile/lib/features/auth/data/auth_repository.dart`). A refresh records the
  generation it was issued under and may persist its pair only if that
  generation is unchanged; login bumps it (a new session invalidates any
  leftover refresh). Logout bumps it and clears storage **inside one serialized
  credential-write critical section**, so no interleaving of check-then-write vs
  bump-and-clear can resurrect a session. A rejected refresh raises
  `SESSION_ENDED`, which `ApiClient` already treats as a terminal auth failure
  and routes to `onAuthFailure`.
- Evidence: `mobile/test/auth_logout_race_test.dart` drives the real repository
  against a gated fake transport (completers, not sleeps):
  logout-during-refresh leaves storage empty; refresh-then-logout ends cleared;
  signing in a different account discards a stale in-flight refresh and keeps
  the new account's tokens; a refresh after logout never reaches the network;
  logout survives an unreachable server; three concurrent logouts leave nothing
  behind.

### 2.10 A malformed 2xx body leaked a raw decoder exception

`jsonDecode(body) as Map<String, dynamic>` meant HTML from a proxy, a bare JSON
array, or a truncated body escaped as `FormatException`/`TypeError`, outside the
`ApiException` contract every caller handles.

- Fix: `ApiClient._decodeObject` maps any non-object 2xx body to
  `ApiException(status, 'INVALID_RESPONSE')`.
- Contract change, stated plainly: one existing assertion in
  `mobile/test/coach_test.dart` expected `FormatException` for a malformed 200.
  It now expects `ApiException`/`INVALID_RESPONSE`. The test's stated intent -
  "surfaces as an error, never an answer" - is unchanged and still asserted; only
  the error type is now the typed one the app handles.

---

## 3. Failure-mode matrix

| Failure | Before | After | Test |
|---|---|---|---|
| Two concurrent refreshes with one token | two live sessions | `[200, 401]`, family burned | `test_concurrent_refresh_yields_one_winner` |
| Second outstanding reset token after a reset | still valid | 400 | `test_password_reset_invalidates_other_tokens` |
| Two identical ride chunks racing | 500 | `409 CHUNK_CONFLICT`, both attempts clean | `test_concurrent_duplicate_chunks_never_500` |
| Five identical friend requests | possible 500 | `[201, 409 x4]` | `test_concurrent_friend_requests_create_one_row` |
| Private team probed via `/members` | 500 + existence leak | identical 404 | `test_members_private_and_missing_team_identical_404` |
| 18 000 distinct rate-limit keys | unbounded memory | capped at 10 000 | `test_limiter_memory_stays_bounded` |
| Sweep under a short window | could widen a long window | windows travel with the key | `test_limiter_keeps_per_key_windows` |
| Database unreachable | `/health` 200 | `/ready` 503, `/health` 200 | `test_ready_503_when_db_down`, `test_health_stays_200_liveness` |
| Redis unreachable | indistinguishable | reported, not gating | `test_ready_ignores_redis_but_reports_it` |
| Extra log fields | dropped | rendered, sensitive keys masked | `test_extra_formatter_renders_fields_and_masks_secrets` |
| Hostile `X-Request-ID` | reflected, uncorrelated | replaced, correlated | `test_malicious_request_id_is_not_reflected` |
| Logout during in-flight refresh | session resurrected | pair discarded, storage empty | `auth_logout_race_test.dart` |
| Concurrent duplicate friend request notification | two rows | one row | `test_concurrent_duplicate_friend_request_notifies_exactly_once` |
| Two concurrent team archives | two notification waves | `[200, 404]`, one row each | `test_concurrent_team_archive_notifies_each_member_once` |
| Four concurrent mark-read calls | possible 500 | all 2xx, one read state | `test_concurrent_mark_read_is_idempotent` |
| HTML / array / truncated 2xx body | raw `FormatException` | `ApiException INVALID_RESPONSE` | `api_client_failure_test.dart` |
| Multipart or binary 401 | no retry on those paths | one refresh, one retry, payload intact | `api_client_failure_test.dart` |
| Request hangs | raw `TimeoutException` | `NETWORK_TIMEOUT`, status 0 | `api_client_failure_test.dart` |
| Socket failure | raw client exception | `NETWORK_ERROR`, status 0 | `api_client_failure_test.dart` |
| 400/403/404/409/422/429/500/503 on auth call | must not refresh | refresh count 0 for every one | `api_client_failure_test.dart` |

Every race is resolved with explicit ordering (row locks, unique constraints,
completers) or asserted as a permitted set of outcomes. **No test sleeps to
create a race**, and no test asserts an order that the implementation does not
guarantee.

---

## 4. Verification performed

### Local gates

| Gate | Command | Result |
|---|---|---|
| Backend tests | `python -m pytest -q` | 583 passed, 1 warning |
| Ruff | `ruff check app tests` | All checks passed |
| Ruff format | `ruff format --check` | clean |
| Types | `mypy app` | no issues, 85 files |
| Mobile tests | `flutter test` | 462 passed |
| Static analysis | `flutter analyze` | No issues found |
| Formatting | `dart format --set-exit-if-changed lib test` | 0 changed |
| Web build | `flutter build web --release` | built |

`analysis_options.yaml` now excludes generated output (`build/**`,
`android/**`, `ios/**`, `web/**`) so `flutter analyze` measures the source tree
instead of build artifacts. `pubspec.lock` moved with the Flutter 3.47.6 /
Dart 3.13.5 upgrade to the versions CI resolves.

### Live smoke (real uvicorn, real PostgreSQL, real Redis)

| Script | Checks |
|---|---|
| `live_smoke_notifications.py` | **101/101** across 2 runs |
| `live_smoke_teams.py` | **47/47** |
| `live_smoke_chat.py` | **57/57** |
| `live_smoke_social.py` | **41/41** |
| **Total** | **246/246** |

Each script ran against a freshly started server on `127.0.0.1:8099`. The
server was restarted between scripts: sharing one process across scripts
exhausts the (correctly working) registration rate limit and yields `429`, which
is the limiter doing its job, not a regression. No script was modified.

### Migrations

`alembic current` -> `0010_notifications (head)`; downgrade to `0009_chat` and
upgrade to `head` both succeed; single head. **Phase 8.6 adds no migration** -
every fix is application-level, and no schema change was needed.

---

## 5. Deliberate decisions and limits

- **Liveness vs readiness is now a contract.** `/health` must never flap with
  dependency state or an orchestrator will restart a healthy process;
  `/ready` carries that signal. Redis is reported but not gating, and the
  endpoint says so in a comment - it becomes gating the day a worker or shared
  limiter depends on it.
- **The in-memory limiter is still per process.** This phase made it bounded
  and window-correct, not distributed. A multi-replica deployment still needs
  the Redis limiter; nothing here pretends otherwise.
- **Readiness requires PostgreSQL only** for the reason above.
- **A session-generation guard is client-local.** It protects one device. It
  does not replace server-side revocation, which is already enforced.
- **`ExtraFormatter` masks by key name.** Any field whose name contains
  `password`, `token`, `secret`, `api_key`, `apikey`, or `authorization` renders
  as `***` even if a call site forgets. Values are not pattern-scanned, so a
  secret smuggled into a benignly named field would still print - call sites
  must not do that, and the two new auth log events carry only ids and counts.
- **Concurrent-dedup tests assert exact status sets** (`[201, 409 x4]`,
  `[200, 404]`) because unique constraints make those outcomes deterministic.

## 6. Not done in this phase

No FCM/APNs, no real email provider, no production workers, no AI capability
change, no new dependency, no migration, no new endpoint beyond `/ready`, and no
work in Phase 8.7 or Phase 9. `backend/cyclecoach_api.egg-info/` and
`_fix_team_doubletap.py` remain untracked generated/one-shot artifacts and were
deliberately not committed.

---

## 7. Remote CI

| Field | Value |
|---|---|
| Workflow | `ci` (`.github/workflows/ci.yml`) |
| Run | `37229175806` |
| Head SHA | `769df32` |
| `backend` job | success |
| `mobile` job | success |
| Conclusion | **success** |

Both jobs passed on the first run of this commit: dependency install, Ruff,
mypy, the full backend suite, `flutter pub get`, `dart format
--set-exit-if-changed`, `flutter analyze`, the full Flutter suite, and the web
release build. The preceding docs commit `53304a6` also passed
(`37204625121`), so the green lineage is unbroken from `33f3edd` through
`769df32`.

## 8. Phase result

**PASS.** The Phase 8.5 audit's residual production risks are closed with
deterministic coverage, every local gate and all four live smoke suites are
green, and the change is green on remote CI. Phase 8.7 and Phase 9 were not
started.
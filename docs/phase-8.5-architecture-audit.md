# PHASE 8.5 ARCHITECTURE & PRODUCTION READINESS AUDIT

Date: 2026-10-04
Scope: full repository — `backend/`, `mobile/`, `docs/`, `alembic/`, `.github/`, `docker-compose.yml`
Mode: **audit only.** No production code, migration, dependency, or configuration was modified.
Companion baseline: `docs/phase-8.4-report.md` (Phase 8.4, complete).

---

## 1. Executive Summary

**Verdict: BLOCKED.**

The codebase is, in several respects, the best-engineered social/messaging backend I would expect at this stage: ownership is scoped in the SQL `WHERE` clause on every one of ~116 endpoints, the deterministic Training Engine is architecturally and provably authoritative over the AI layer, the blocked-DM privacy rule is enforced with no row written at all, deep links are allowlisted and UUID-validated client-side, refresh-token reuse detection burns the whole session family, and seven partial unique indexes encode real invariants cheaply. Those are not accidental qualities; they are the product of consistent, documented decisions.

But five defects are individually sufficient to stop the next phase, and three of them were **invisible to a 552-test backend suite, a 428-test Flutter suite, and a 101/101 live smoke run**. That combination — real production-breaking bugs plus a verification story that reports green over them — is the central finding of this audit.

The five blockers:

| # | Blocker | Verified how |
|---|---|---|
| **B1** | **The Flutter client cannot refresh an access token.** `refreshSession()` is declared once and called zero times across all 125 Dart files; there is no 401 branch anywhere. Access tokens live 15 minutes. Every session dies at minute 16 with no login prompt and no recovery. Every ride longer than 15 minutes therefore never uploads. | Direct search of 125 Dart files |
| **B2** | **`RideRecorder.finish()` permanently strands uploaded ride data.** It cancels the only retry timer, uploads at most one 100-point chunk, marks the ride `completed` — and `unfinishedRide()` filters to `status IN ('recording','paused')`, so the remaining points become invisible to every query in the app. | Code read at `ride_recorder.dart:248-265` + `ride_database.dart:35` |
| **B3** | **Archiving a team notifies 1 member instead of N.** `_create_one` pre-checks `dedupe_key == key`; when `key is None` SQLAlchemy renders `IS NULL`, which matches any prior unkeyed row. `notify_team_archived` is the only caller that passes no key. | **Empirically reproduced against the live DB:** 3 live members → **1 row, 1 recipient** |
| **B4** | **The shipped test for B3 passes for the wrong reason.** `test_team_archive_notifies_members` asserts only `total == 1` and never checks the notification type. B's single notification is the earlier `team_invitation`; B never receives `team_archived`. This is stop-rule §25.13 — a suite giving false confidence over a broken invariant. | **Empirically reproduced:** `A: ['team_archived']` / `B: ['team_invitation']` |
| **B5** | **`SECRET_KEY` defaults to an in-repo literal and production never validates it.** `"change-me-in-production-min-32-chars"` signs every access token. `is_production` gates exactly one thing (`TrustedHostMiddleware`). A production deploy with no `SECRET_KEY` set boots successfully, signed with a publicly known key. | Direct read of `config.py:17` + `main.py:32` |

Two further blockers sit one level below, and I want to be precise about them rather than inflate them:

- `DevEmailService` is wired **unconditionally** (`email.py:30`) with no environment branch, while the production `EmailService.send` raises `NotImplementedError`. Self-service password reset is non-functional in every environment, and every reset/verify token accumulates unboundedly in process memory. This is stop-rule §25.12 (a required dependency missing).
- The **git repository has zero commits** — `git rev-list --all --count` returns `0`, `git ls-files` returns nothing. `.github/workflows/ci.yml` is untracked and **has never executed**. The entire CI posture described in `docs/09-testing-cicd.md` is unverified in practice.

**Maturity assessment.** The architecture is *development-ready* and, in several subsystems, genuinely production-quality. It is **not staging-ready**: the documented first-run path is broken (three different Postgres passwords across compose/Alembic/CI), there is no deployment procedure at all, and `/health` always returns `200 "ok"` so it cannot serve as a readiness probe. See §17.

**Can the next phase begin?** No. B1–B5 are pre-existing defects in *shipped, verified* code, not risks in new work. Starting a new product phase now would build on a client that cannot stay logged in and a notification path that silently drops messages — and, more seriously, on a verification story that would certify both as working.

---

## 2. Verified Baseline

All gates re-run during this audit. Nothing was modified to make them pass.

| Gate | Command | Result |
|---|---|---|
| Backend tests | `python -m pytest tests -q` | **552 passed**, 1 warning (19m24s) |
| Backend lint | `python -m ruff check .` | **All checks passed** |
| Backend types | `python -m mypy app` | **Success: no issues found in 85 source files** |
| Migrations | `python -m alembic current` | **`0010_notifications` (head)** |
| Migration chain | `python -m alembic history` | **Linear, 10 revisions, no branches, single leaf** |
| Flutter analyze | `flutter analyze` | **No issues found** |
| Flutter format | `dart format --set-exit-if-changed lib test` | **125 files, 0 changed** (exit 0) |
| Flutter tests | `flutter test` | **428 passed** |
| Flutter web build | `flutter build web --release` | **Built `build/web`** |
| Live smoke | `python live_smoke_notifications.py` | **101/101 checks passed across 2 runs**, exit 0 |

The single backend warning is a `ResourceWarning` about an unclosed resource under `greenlet_spawn`; it is pre-existing and not a gate failure.

**Baseline integrity note:** the notification-provider deferral limit was verified to behave as documented — `PUSH_INLINE_FANOUT_LIMIT=20` bounds *delivery* only (`notification_service.py:541-550`), the rows are still written, and the deferral is logged as `deliver_deferred`. That claim in `docs/phase-8.4-report.md` is accurate.

---

## 3. Architecture Map

### Backend — as actually implemented

```
                     ┌──────────────────────────────────────────┐
  HTTP /health ──────▶ health.py  (always 200 "ok")            │  ← NOT a readiness probe
                     └──────────────────────────────────────────┘

  ┌─────────────────────────────────────────────────────────────────────┐
  │ FastAPI app factory (main.py)                                       │
  │   CORSMiddleware (allow_credentials=True, unconditional)            │
  │   TrustedHostMiddleware  ← ONLY if ENVIRONMENT == "production"     │
  │   @app.middleware("http") correlation (X-Request-ID — see O-1)      │
  │   3 exception handlers (errors.py) — no domain type registered      │
  └─────────────────────────────────────────────────────────────────────┘
        │
        ▼
  deps.get_current_user  ── HTTPBearer → HS256 verify (type=="access")
        │                    → fresh DB read: deleted_at IS NULL, status==ACTIVE
        │                    (uncached per request — correct by construction)
        ▼
  ┌───────────────────────────────────────────────────────────────────────┐
  │ 12 routers / ~116 endpoints        app/api/v1/*.py                     │
  │   auth · profile · bikes · rides · routes · training · coach          │
  │   social · teams · chat · notifications · health                      │
  │                                                                       │
  │   Ownership is scoped in the SQL WHERE clause — the project's          │
  │   strongest and most consistent pattern.                              │
  └───────────────────────────────────────────────────────────────────────┘
        │
        ▼
  ┌───────────────────────────────────────────────────────────────────────┐
  │ 17 domain services     app/services/*.py                              │
  │   training_calc.py  ── PURE, deterministic, AUTHORITATIVE              │
  │   chat_service · team_service · social_service                        │
  │   notification_service · ride_service · route_service                │
  │   advisory locks: chat per-conversation, social per-pair, team       │
  └───────────────────────────────────────────────────────────────────────┘
        │                    │                    │
        ▼                    ▼                    ▼
  ┌────────────┐   ┌──────────────────┐   ┌──────────────────────┐
  │PostgreSQL  │   │ AI provider seam │   │ Push provider seam   │
  │30 tables   │   │ app/ai/provider  │   │ app/notifications/   │
  │10 migrations│  │ OpenAICompatible │   │ FakePushProvider only│
  │44 FKs, all │  │ FakeAIProvider   │   │ Unconfigured (default│
  │ ondelete   │  │ (test-only)      │   │  → every device SKIPPED│
  │ explicit   │   │                  │   │ get_provider() NEVER │
  │            │   │ NOT authoritative │   │  raises               │
  └────────────┘   └──────────────────┘   └──────────────────────┘

  Redis  ── used by EXACTLY ONE endpoint: /health. Not on any request path.
  ARQ    ── 12-line stub: functions=[ping], redis_settings=None. No job has
           ever run. `arq` is declared in pyproject.toml and never imported.
```

### Mobile — as actually implemented

```
  Flutter app (single ProviderScope, Riverpod 3.4.3, go_router 17.5.0)
      │
      ▼
  12 feature modules  (auth bikes chat coach home notifications
                      profile ride routes social teams training)
      │  10/12 use data/ domain/ presentation/
      │  home + profile are single pages
      ▼
  Repositories ──▶ ONE ApiClient (lib/core/network/api_client.dart, 212 lines)
      │            no retry, no backoff, NO 401 HANDLING
      │            15s timeout, query strings hand-built
      ▼
  Drift / SQLite  (lib/features/ride/data/ride_database.dart)
      schemaVersion 3 · LocalRides, LocalPoints, CachedRoutes
      NO user_id column on any table
      NO sync_state table (ADR-07 specified one)
      NO lifecycle observer anywhere in the tree

  auth: flutter_secure_storage (correct) · refreshSession() defined, never called
  notifications: UnconfiguredPushRegistrar · no push SDK, no app_links
  transport: PollingChatTransport — wsBase config is dead
```

---

## 4. Findings

Severity: **CRITICAL** / **HIGH** / **MEDIUM** / **LOW** / **INFO**. Only findings with concrete evidence appear. IDs are stable within this report.

### 4.1 CRITICAL — must be fixed before the next phase begins

| ID | Severity | Area | Finding | Evidence | Recommendation |
|----|----------|------|---------|----------|----------------|
| **B1** | CRITICAL | Mobile auth | **No access-token refresh anywhere.** `refreshSession()` declared once, called zero times; no 401 branch in `ApiClient._send`. Access tokens are 15-minute JWTs. Every session terminates at minute 16 with no recovery, and no ride longer than 15 minutes can sync. | `auth_repository.dart:46` (only hit in 125-file search); no `refreshSession` call site; no `status == 401` outside `api_client.dart:125` (missing-token case only); `ADR-04:5` | Add **single-flight** refresh in `ApiClient._send`: memoize the in-flight `Future`, one rotation at a time, single retry after refresh. **Single-flight is mandatory** — ADR-04:13-17 revokes the entire session family on reuse detection, so two concurrent refreshes would log the user out of every device. |
| **B2** | CRITICAL | Offline/sync | **`finish()` permanently strands uploaded ride data.** Cancels the only retry timer, uploads ≤1 chunk of 100, sets `status='completed'`; `unfinishedRide()` filters `status.isIn(['recording','paused'])`, so the rest is unreachable by any query in the app. | `ride_recorder.dart:248-265` (`_cancelTimers()` → `syncNow()` → `transition()` → `_setStatus('completed')`); `ride_database.dart:35` | `finish()` must drain the queue, or refuse to finalize with pending points and record `status='pending_sync'`. Startup must scan for rides with `uploadedSeq < max(seq)`, not just by `status`. |
| **B3** | CRITICAL | Notifications | **Team-archive fan-out collapses to one recipient.** `_create_one` pre-checks `dedupe_key == key`; `key is None` renders `IS NULL`, matching any prior unkeyed row. `notify_team_archived` is the only caller passing no key. Compounding: that query is also unindexed (the partial index excludes NULLs). | `notification_service.py:487-492`; `notify_team_archived:361-382`. **Empirically reproduced:** 3 live members → `notify()` returned 3 objects, **1 row in the DB, 1 distinct recipient**, `dedupe_key=[None]` | Guard the pre-check with `if key is not None:`. The author already understood the NULL case in the `IntegrityError` branch at `:511-512`. |
| **B4** | CRITICAL | Testing | **The shipped test for B3 passes for the wrong reason.** `test_team_archive_notifies_members` asserts only `total == 1` and never checks type; B's one notification is the earlier `team_invitation`. This is stop-rule §25.13. | `test_notifications_api.py:351-360`. **Empirically reproduced:** `A: total=1 types=['team_archived']` / `B: total=1 types=['team_invitation']` | Assert the notification **type** per recipient, and add a multi-member fan-out test. `101/101` smoke + `552` pytest currently certify a broken path. |
| **B5** | CRITICAL | Config/secrets | **`SECRET_KEY` defaults to an in-repo literal and production never validates it.** Signs every HS256 access token; `is_production` gates only `TrustedHostMiddleware`; the same placeholder ships in `.env.example`. Fails **open**, silently. | `config.py:17`; `main.py:32`; no validation anywhere (grep: only 2 uses, both in `security.py`) | Refuse to start when `ENVIRONMENT == "production"` and `SECRET_KEY` is absent, short, or equal to the placeholder. Supply from a secret manager. Note the compounding factor: `sub` is the `users.id` UUIDv4 returned by `GET /auth/me`, so any user-id leak becomes full account takeover. |

### 4.2 HIGH

| ID | Severity | Area | Finding | Evidence | Recommendation |
|----|----------|------|---------|----------|----------------|
| **H1** | HIGH | Config/secrets | **`DevEmailService` wired unconditionally**, no environment branch; production `EmailService.send` raises `NotImplementedError`. Password-reset and email-verification tokens are **never delivered**, and each `OutboxMessage` (containing the raw token) accumulates forever in process memory — an unbounded leak of live credentials on the heap. | `email.py:30`, `:17`, `:27`; `EmailService` is never instantiated | Environment-branch the binding and fail loudly when no provider is configured. Stop retaining messages in production. |
| **H2** | HIGH | Deployment | **Zero git commits.** `git rev-list --all --count` → `0`; `git ls-files` → empty. `.github/workflows/ci.yml` is untracked and has **never executed**. Every CI claim in `docs/09-testing-cicd.md` is unverified in practice. | Verified directly | Commit the repository and let CI run green before relying on any gate. |
| **H3** | HIGH | Deployment | **Three different Postgres passwords.** compose `cyclecoach2026`; `alembic.ini:3` + `.env.example:5` `cyclecoach`; `ci.yml:11` `cyclecoach`. The documented first-run (`docker compose up -d` then `alembic upgrade head`) fails with `password authentication failed`. | Verified directly | One source of truth. |
| **H4** | HIGH | Deployment | **Alembic ignores `DATABASE_URL`.** `env.py` only honours the undocumented `ALEMBIC_DB_URL` env var, never `Settings.DATABASE_URL` and never `.env`. A prod deploy runs the app against prod while `alembic upgrade head` targets **hardcoded localhost** — and if that resolves, it migrates the wrong database and *appears to succeed*. | `alembic/env.py:13-15`; `alembic.ini:3` | Have `env.py` import `Settings` and default `sqlalchemy.url` from it. Document or remove `ALEMBIC_DB_URL`. |
| **H5** | HIGH | Deployment | **No deployment procedure exists.** `Dockerfile:8` runs bare uvicorn with no migration step; compose has **no api service**; no entrypoint, k8s/fly/render manifest, or deploy job; no doc describes migrating on deploy. Only CI runs `alembic upgrade head`, against a throwaway DB. | `Dockerfile`, `docker-compose.yml` (2 services: postgres, redis), repo-wide search | Define the migration-on-deploy step and its ownership. |
| **H6** | HIGH | Backend errors | **Validation errors echo the submitted plaintext secret back.** `errors.py:22-23` passes `jsonable_encoder(exc.errors())`, and Pydantic v2 includes the raw `input`. A short password returns `{"loc":["body","password"],"input":"short"}`; a malformed email returns the address. **I triggered this live** during this audit. Contradicts `schemas/auth.py:1` ("no passwords echoed"). | `core/errors.py:22-23`; live 422 body captured | Pop `input` (and `url`) from each error dict before encoding. |
| **H7** | HIGH | Backend errors | **Point ingest can 500 and lose up to 500 points.** `ingest()` dedupes in Python rather than letting the unique constraints arbitrate, and commits with no `IntegrityError` handler — while `create()` in the same file gets this right. A retried chunk racing the original loses the chunk. Also O(n²): every point's uuid+seq is re-materialized per chunk. Docstring claims "retries are safe". | `ride_service.py:220-225, 285-292`; constraints `0004_rides.py:75-76` | Wrap the commit; re-read the winner as `create()` does. Move dedupe to the constraint. |
| **H8** | HIGH | Observability | **The correlation id in the response is not the id in the logs.** `new_request_id()` ignores the caller's `rid` and generates a second uuid, so a client-supplied `X-Request-ID` never appears in any log line. 500s carry no id at all (the `Exception` handler is installed in `ServerErrorMiddleware`, outside the correlation middleware). | `main.py:36-42`; `logging.py:65-68` | Pass `rid` into `new_request_id(rid)`. Move the exception handler inside, or set the header in a `finally`. |
| **H9** | HIGH | Observability | **Every `extra=` log field is discarded.** The formatter references only `asctime/levelname/env/request_id/name/message`; ~90 `_log()` sites pass `conversation_id`, `recipient_user_id`, `error_category`, `cost_usd`, `prompt_version`… and the emitted line contains **none of them**. A redaction design is moot when the transmission is broken. Also: `auth_service`, `bike_service`, `ride_service`, `route_service` log **nothing at all** — login failure, refresh-token reuse, and GPX import are invisible. | `logging.py:55-56`; formatter omits `extra`; per-service grep | Include `extra` in the format (or emit JSON). Add event logging to auth and ride ingest. |
| **H10** | HIGH | Rate limiting | **Unbounded memory growth in the limiter = remote memory DoS.** `_buckets` entries are never evicted — no TTL, no LRU, no sweeper. Line 11 prunes timestamps but keeps the key. Unauthenticated `POST /auth/login`, keyed purely on IP, grows RSS without limit from rotating source addresses — on the endpoint whose purpose is to resist hostile traffic. | `core/rate_limit.py:5, 11-16` | Evict on window expiry (cheap sweep) or move to Redis — which is **already declared and running** in compose and CI. |
| **H11** | HIGH | Offline/sync | **No `user_id` on any local table.** `LocalRides`/`CachedRoutes` have none, the DB is never wiped on logout and never invalidated. User A's unfinished ride and cached routes are returned to user B after a logout/login, and B's resume uploads A's ride under B's token. | `ride_tables.dart:5-49`; `ride_database.dart:16`; `auth_state.dart:93-96` | Add `userId` to every local table; clear the database on logout. |
| **H12** | HIGH | Mobile state | **Logout invalidates nothing.** ~20 non-`autoDispose` providers hold user A's data; only 13 references to `authProvider` exist and **no feature provider reads it**. B sees A's friends, routes, bikes, training history and notifications on first paint. `RideDatabase` is never closed or wiped. | `auth_state.dart:93-96`; provider audit | One `ref.invalidate` fan-out on logout, or `ProviderScope(key: ValueKey(userId))`. |
| **H13** | HIGH | Mobile lifecycle | **Zero lifecycle handling in the entire tree.** No `WidgetsBindingObserver`, no `AppLifecycleState` — 0 hits including tests. Three doc comments promise foreground behaviour that does not exist: the unread count "refreshes on the next foreground", `PollingChatTransport.refresh()` is "called when the rider returns to the foreground" (never called), and there is no flush-on-`paused` during a ride. | repo-wide grep for `AppLifecycleState` → 0 | Add a lifecycle observer: flush on `paused`, drain on `resumed`, invalidate notification/chat providers on resume. |
| **H14** | HIGH | Offline/sync | **GPS buffer is cleared before the durable write.** `_buffer.clear()` runs, then `db.batch` writes. A kill in that window loses the points from memory *and* disk, recurring every 5 s while the app is being killed. | `ride_recorder.dart:198-206` | Write first, clear after. |
| **H15** | HIGH | Offline/sync | **Server point-rejection response is entirely discarded.** `uploadChunk`'s return value is unbound; `markUploaded` then advances the watermark past rejected points. No reconciliation, no conflict table, no UI. Client and server summaries silently disagree. | `ride_recorder.dart:328-345`; contract in `ride-sync-protocol.md:16` | Bind and inspect the response; record conflicts locally. |
| **H16** | HIGH | AI | **Misconfigured `AI_PROVIDER` fails silently and `/coach/status` reports the opposite of the truth.** `get_provider()` raising `ProviderUnavailable` is returned with **zero log lines**; `/coach/status` reports `provider: "fake"`, `fallback_only: false` and a model name. It fails *safe* (no wrong numbers) but is completely invisible. | `ai/service.py:182-187`; `coach.py:127-146`; `provider.py:181` | Log the failure; derive `/coach/status` from a real `get_provider()` attempt; type the setting as `Literal[...]` so a typo fails at load. |
| **H17** | HIGH | Notifications | **Deferred push delivery is enqueue-and-forget with no queue, no worker, and no persisted marker.** Past 20 recipients, `_deliver` returns. `Notification` has no `delivered_at`/`attempt_count`/`last_error`. Any team larger than 20 members **never receives push**, permanently, and nothing records it. | `notification_service.py:541-550`; model `models/notifications.py:175-224` | Acceptable **only** as long as push is understood to be in-app-only. Before real providers ship, this needs the worker plus delivery-state columns. |
| **H18** | HIGH | Performance | **The hottest table has no index matching its real query.** Four sites filter `ride_id` **and** `accepted` and order by `seq`; no index includes `accepted`. `_finalize` and `_ride_samples` run on every ride completion, and `gps_engine` rejects a non-trivial fraction of fixes, so heap traffic scales with *total* points. | `ride_service.py:57,230,300`; `training_service.py:267`; indexes at `0004_rides.py:60-79` | `CREATE INDEX … ON ride_points (ride_id, seq) WHERE accepted;` — mirroring the partial-index pattern already used elsewhere. |
| **H19** | HIGH | Database | **The API test suite never builds the schema from migrations.** Tests use `Base.metadata.create_all`; migrations are exercised separately in `test_migrations.py`, which asserts index and constraint **names only** — never types, FKs, `ondelete`, or partial predicates. Drift between model and migration is invisible. | `tests/conftest.py`; `tests/test_migrations.py` | Extend `test_migrations.py` to assert `ondelete`, predicates, and column types; add a create_all-vs-migrate schema comparison. |
| **H20** | HIGH | Database | **`users.deleted_at` is read in four places and written in zero.** No delete/deactivate endpoint exists; email is never released, so the address is permanently taken and `POST /auth/register` answers `409 EMAIL_TAKEN`. Soft-deleted users leave all GPS traces, chat history, training loads and push tokens physically present. | `models/user.py:63`; `deps.py:37`; `auth_service.py:45` | Account deletion is deliberately out of scope, but the **half-built** state should be recorded as a known gap with an owner. |

### 4.3 MEDIUM (selected — full evidence in §5–§16)

`M1` 422-reflected secrets (`H6`) aside: `M1` refresh has no `FOR UPDATE` — two concurrent refreshes mint two live descendants and reuse detection does not fire until the *next* rotation. `M2` password reset does not invalidate the user's other outstanding tokens (60-min takeover window survives a victim reset). `M3` refresh lifetime slides forever with no absolute cap. `M4` `assert user is not None` on a DB invariant in an auth-critical path — behaviour depends on `python -O` (10 sites). `M5` `email_verified` gates nothing. `M6` `409 EMAIL_TAKEN` + Argon2 short-circuit timing oracle defeat the reset flow's deliberate anti-enumeration. `M7` email uniqueness is app-level only — no unique index on `lower(email)`. `M8` `allow_credentials=True` unconditional + `CORS_ORIGINS=*` would make Starlette echo any origin. `M9` `ENVIRONMENT` is an unvalidated `str`; a typo silently disables the only production guard. `M10` per-IP rate limits are defeated by any proxy (no `ProxyHeadersMiddleware`), turning `register 10/h` into 10 platform-wide. `M11` `{user}:{IP}` composite keys are reset by IP rotation and shared behind NAT. `M12` `POST /auth/password-reset/confirm` is unrated while running Argon2id. `M13` `PATCH /profile` entirely unrated. `M14` `GET /routes/{id}/geometry` and `/export/gpx` — the heaviest reads — unrated. `M15` `GET /training/summary` (≈20 queries) unrated. `M16` N+1: one COUNT per ride in list pages; one extra query per activity with zones. `M17` `/health` always `200 "ok"` → unusable as a readiness probe. `M18` no metrics or tracing anywhere. `M19` rate-limit (429) events never logged. `M20` `check_db`/`check_redis` swallow everything with no log. `M21` 44 FKs: 38 CASCADE vs 4 RESTRICT is internally contradictory; `DELETE FROM users` is **impossible** (verified live), so erasure can be satisfied by neither hard nor soft delete. `M22` `messages` are CASCADE-deleted via `teams.id`, contradicting `0009_chat.py:9-12`. `M23` `notifications.actor_user_id` and `conversations.created_by_user_id` unindexed while backing RESTRICT — every `DELETE FROM users` scans the fastest-growing table. `M24` `notify_team_member_removed` keys on a stable *pair*, not an event, so a rejoin-and-re-expel is silently never notified. `M25` `rides.route_version` is not a composite FK to `(route_id, version_no)`. `M26` uncaught `IntegrityError` in `register()` → 500 instead of 409. `M27` `GET /teams/{id}/members` returns **500 where 404 is contractual** (verified live; the other two endpoints the audit suspected are correct). `M28` `sync_ride` swallows all exceptions → `200 OK` with no `training_activities` row and no hint. `M29` chat write path returns 403 `CHAT_BLOCKED` where the read path returns 404 — self-contradicting the file's own stated convention. `M30` no `WidgetsBindingObserver`; `GoRouter` rebuilt on every auth transition and never disposed. `M31` offline cold start conflates "network down" with "signed out". `M32` no `locale:`/`localeResolutionCallback` and no in-app language switch, though `/me` returns `preferred_language`; `intl` is an unused direct dependency. `M33` `LocalPoints.uploaded` is a dead column contradicting `uploadedSeq`. `M34` no index on `LocalPoints.rideId` or `LocalRides.status`. `M35` `uploadedSeq` cannot represent retries, conflicts, per-item state, or pull sync. `M36` `/ride/recovery` fully built but never navigated to. `M37` non-nullable `as Map<String,dynamic>` casts raise `TypeError`, which the bare `on Exception` in `restore()` reports as "logged out". `M38` `'${json['x']}'` stringifies `null` to `"null"` — could persist the literal as a bearer token. `M39` AI context metric labels and entity names are hardcoded English in all three locales, contradicting the project's own l10n-key rule. `M40` no prompt-version drift test and no persistence — contrast the enforced `CALCULATION_VERSIONS` drift test. `M41` all six Phase 8.4 push settings absent from `.env.example`.

### 4.4 LOW / INFO — selected

`L1` AI daily cost cap is inert (prices default to 0.0) — honestly documented, bounded by token caps and request count. `L2` AI spend dict is per-process and never pruned. `L3` no coach idempotency key (contrast chat, which has a mature one). `L4` coach POSTs have no router-level limit and the quota is checked *after* full context assembly. `L5` home dashboard renders **fabricated** weekly distance/elevation/trends with no provider. `L6` `/rides` and `/performance` are placeholder pages — 2 of 5 bottom-nav destinations dead. `L7` 2 of 5 bottom-nav switches destroy the previous tab's stack (no `StatefulShellRoute`). `L8` cold-start deep-link mechanism is fully built but has **no producer** — no push SDK, no `app_links`; asserted only by a test that reads source text. `L9` notification state has no local persistence; offline bell reads `0`, meaning "none" rather than "unknown". `L10` `README.md` is frozen at Phase 2 and claims `alembic upgrade head` creates "no tables" (it creates 30). `L11` `docs/08-security.md` claims at-rest encryption, an audit log, virus scanning, and store-webhook subscriptions — none exist; there is **no audit log of any kind**. `L12` `docs/03` line 5 lists ~24 tables that do not exist, incl. `ai_recommendations`. `L13` `docs/04` documents `POST /coach/recommendations`, `GET /coach/history`, `/rides/{id}/points:batch`, a location API and an analytics API — none exist. `L14` `docs/01`/`02` describe `app/modules/`, `app/realtime/`, dio, Hive, and Flutter flavors; actual is `app/api/v1/`, `http`, Drift, `--dart-define`. `L15` `phase-8.4-architecture-audit.md` is a pre-implementation proposal still marked "Decided" and contradicts shipped code on four load-bearing points — including putting `l10n_key` and `params` on a lock-screen payload, which ADR-15 §7.1 exists to prevent. `L16` `README.md:8` and `docs/01:9-11` claim Redis cache/pub-sub/rate-limit and five worker responsibilities — none exist. `L17` `docs/09-testing-cicd.md` documents a `security` job, `pip-audit`/`osv`, an OpenAPI snapshot gate, and `flutter build apk` — none exist. `L18` `docs/10` ADR index points at `ADR-01` (nonexistent) and misattributes ADR-05/06/07 by three positions. `L19` Dockerfile runs as **root**, single-stage, every dependency `>=` with no lockfile, no `HEALTHCHECK`, no `.dockerignore`. `L20` compose publishes `5432` and an unauthenticated `6379` to the host, with no healthchecks and no restart policies. `L21` no backup/restore/PITR/retention documentation anywhere. `L22` downgrades are implemented but destroy all data; no rollback procedure is documented. `L23` `TrustedHostMiddleware` allowlist is hardcoded `["*.cyclecoach.app"]` and not configurable. `L24` `APP_VERSION` is `1.0.0` at Phase 8.4.

**Verified-good (INFO — investigated and accepted, not problems):** ownership scoped in `WHERE` on all ~116 endpoints; the AI layer provably cannot write a number into any persisted column; safety classification runs *before* the provider; push payloads are exactly three keys built from named fields; `dedupe_key` is a partial unique index on an immutable business id; 7/7 partial-index predicates match their query sites (`read_at IS NULL`, never `is_read`); all 44 FKs declare `ondelete` explicitly; zero `NO ACTION`; deep links reject scheme, authority, traversal, and non-UUID ids; the pending-intent queue clears on logout so a tap can't replay into a later account; idempotency keys are persisted *before* the network call in both rides and chat; `markUploaded` runs *after* acknowledgement; Drift access is 100% typed with no raw SQL; no `dynamic` and no unguarded `!` in a data path; Arabic is genuinely implemented and RTL-tested; `SharedPreferences` is correctly absent for tokens; CHECK-constraint vs Pydantic validation is aligned everywhere; `AI_DAILY_COST_LIMIT` is enforced per user, not globally.

---

## 5. Security Audit

**Authentication — strong.** Argon2id (verified: 64 MiB, t=3, p=4), HS256 with an algorithm allow-list (no `alg` confusion), `type == "access"` enforced so a refresh token cannot be used as a bearer, opaque 256-bit refresh tokens stored as SHA-256, single-use rotation, and **family-wide reuse detection** — the correct compromise-containment design. `get_current_user` performs an uncached DB read per request, so suspension and soft-delete take effect on the very next call.

Gaps: `B5` (placeholder signing key), `H1` (reset tokens never delivered), `M1` (refresh race), `M2` (stale reset tokens survive), `M3` (unbounded sliding refresh), `M4` (`assert` in auth path), `M5` (`email_verified` decorative), `M6` (enumeration via `409` + timing), `M7` (case-sensitive email uniqueness).

**Authorization — the strongest area, with one verified exception.** Ownership is pushed into the SQL `WHERE` clause on essentially every endpoint, in three descending strengths: owner predicate on the mutation itself (`notification_service.py:819-825`), owner-scoped `get_owned`/`_require_*` helpers, and fetch-then-check only where the fetched object *is* the decision (always under `FOR UPDATE` + advisory lock). **No IDOR found in any of the 12 routers.** `M27` is a `500`-where-`404`-belongs defect on `GET /teams/{id}/members`; I verified live that it returns 500 for both a private team and a random UUID, so it does **not** leak private-team existence — it is an envelope break and a 500 where 4xx belongs, not an isolation break. `M29` is a 403/404 inconsistency on the chat write path; the caller has already proven participation, so no existence information leaks.

**Secrets.** `B5` is the critical one. `.gitignore` correctly excludes `.env`. But there is **no production secret-delivery mechanism at all** — no Vault, no cloud secret manager, no compose `env_file`/`secrets:`, no deploy job. `alembic.ini:3` hardcodes a database password in source; `docker-compose.yml:6` hardcodes another.

**Payload reflection.** `H6` — validation errors echo the submitted password/email back. Live-confirmed during this audit.

**Rate limiting as a security control.** `H10` (unbounded memory), `M10` (defeated by any proxy), `M11` (IP-rotation bypass), `M12`/`M13`/`M14`/`M15` (unrated expensive or abusable endpoints). The documented `limit × workers` limitation is **accurate in all three places it is stated**, and Redis — the stated upgrade precondition — is already declared and already running.

---

## 6. Privacy Audit

**Enforcement that is genuinely correct, and worth stating plainly:**

- A blocked DM creates **no notification row for either party** — not filtered, none. Refused sends leave no trace on retry. Verified live in the 101/101 smoke.
- Blocked DM create/send refused **both directions**; history retained; unblock restores the thread. Verified live.
- A block does **not** sever a team channel, and team notifications still cross a block. Verified live. This is ADR-13 §6 / ADR-14 §2.1 carried into notifications consistently across `chat_service.py`, `notification_service.py`, and the docs.
- Push payloads are exactly `{notification_id, notification_type, deep_link}`, built from **named fields** rather than a free-form dict, so adding a dataclass field cannot widen the payload. Verified.
- Notification content is `l10n_key` + `params`, never rendered copy — so history re-renders in the rider's current language and keys are a storage contract.
- Push tokens are never returned, logged, or payloaded; `PushDeviceOut` is built explicitly rather than model-validated so a new column cannot expose one. Verified.
- Deep links are allowlisted, UUID-validated, reject scheme/authority/traversal, and the client performs **no** pre-navigation authorization — the destination re-authorizes server-side. A hostile push can at most land the rider on an error screen.
- AI context is owner-scoped through the same accessors the REST layer uses, so the Coach cannot read another user's ride; hallucinated entity references are filtered against a permitted set; no GPS coordinate is ever read into the context.

**Privacy gaps:**

- `H6` — plaintext secrets reflected in 422 bodies.
- `H11`/`H12` — on-disk and in-memory user data survives logout; user B can see and sync user A's rides and routes. **On a shared device this is a real cross-account disclosure.**
- `M21`/`H20` — `DELETE FROM users` is impossible (verified live: FK violation), and soft delete leaves GPS traces, chat history, training loads and push tokens present. There is no erasure path by either mechanism — a GDPR Art. 17 gap for a fitness app recording location and health-adjacent loads.
- `M22` — `messages` are CASCADE-deleted via `teams.id`, contradicting the ADR's "history is never cascade-deleted".
- `L11` — `docs/08-security.md` claims an audit log and at-rest encryption. **Neither exists.** The security document a reviewer would scope from overstates the controls by a wide margin.
- `M39` — AI coach output hardcodes English metric labels, producing e.g. a French sentence wrapped around English terms.
- `redact()` uses exact-match keys: `token_hash`, `refresh_token`, `access_token`, `email`, `lat`, `lon`, `start_lat` are **not** covered, and it does not recurse into lists. Combined with `H9`, log content is being discarded anyway.

**Live location, group rides, media messaging: NOT IMPLEMENTED.** Confirmed absent by grep (0 matches for `location_session`/`location_update`). No findings are raised against them; their absence is documented as intended scope.

---

## 7. Database Audit

**Chain: clean.** 10 migrations, linear, unique revision ids, no shared `down_revision`, single root (`0001`, a deliberate no-op), single leaf (`0010_notifications`). Verified.

**The strongest part of the schema.** All **44** foreign keys declare `ondelete` explicitly — zero `NO ACTION`, zero implicit defaults, so no FK can silently orphan. Five **partial unique indexes** encode real invariants cheaply and correctly (`training_activities` one-per-ride, `team_memberships` single-owner, `team_invitations` pending-pair, `conversations` one-team-channel, `notifications` dedupe), each with a comment explaining the invariant, plus a `CHECK` enforcing the canonical friend-pair ordering in SQL. **7/7 partial-index predicates match their query sites** — including the `read_at IS NULL` predicate, which is queried as `read_at.is_(None)` in all four places and never as the derived `is_read` boolean. The one mismatch is `B3`'s unindexed `IS NULL` fallback.

**Verified by execution:** `DELETE FROM users` fails with `conversations_created_by_user_id_fkey` — the RESTRICT set makes hard deletion impossible in practice, so the 38 CASCADE FKs are largely unreachable via the user path.

**Gaps:** `M21` (CASCADE/RESTRICT contradiction), `M22`, `M23` (unindexed RESTRICT FKs), `M24` (event-vs-pair dedupe key), `M25` (no composite FK for pinned route version), `H18` (missing `ride_points(ride_id, accepted, seq)`), `H19` (tests never build schema from migrations; `test_migrations.py` asserts names only), `B3` (the `IS NULL` query is also a sequential scan on every unkeyed create), plus `M26`/`H7` (two missing `IntegrityError` handlers).

**Not verified:** whether any downgrade is *semantically* safe. All ten `downgrade()` functions exist and are mechanically valid, but each drops tables outright (`L22`); there is no data-preserving rollback path, and `docs` describe none. No `ALTER TYPE … ADD VALUE` precedent exists, so adding an enum value later has no safe downgrade — a constraint on future phases.

---

## 8. API Audit

**Consistent and well-designed.** Uniform error envelope `{error:{code,message,details}}`; standard offset-paginated envelope; "404, not 403" as an explicit, mostly-honoured convention; idempotency keys on rides, points, messages and notifications; `PushDeviceRegister` with `extra="forbid"` and no `user_id`, so ownership is server-derived; `PatchDeviceUpdate` narrowed to `enabled` only.

**Inconsistencies that matter:**

1. **`M27`** — no domain error type is registered with `register_error_handlers`; each of 12 routers hand-rolls its own `_err()`/`_fail()` converter (10 copies, zero enforcement). `GET /teams/{id}/members` forgot its `try/except TeamError` and returns **500** where 404 is contractual. **Verified live.** This single mechanism explains every envelope inconsistency found.
2. **`H6`** — validation errors bypass the envelope entirely and reflect `input`.
3. **`M29`** — chat write path returns 403 where the read path returns 404 for the identical block condition, contradicting the file's own stated invariant.
4. Three non-envelope paths bypass the handlers: CORS rejection and `TrustedHostMiddleware` rejection return plain text, and 500s carry no `X-Request-ID`.
5. `X-Request-ID` is client-controlled, unvalidated, unbounded, and reflected verbatim into a response header — while the logs get a *different* id (`H8`).
6. `GET /routes/{id}/versions` and `/{id}/geometry` gate on `get_readable` **before** touching version rows, so historical versions of a foreign private route are correctly unreadable — no version-level IDOR.
7. `L13` — the API map documents endpoints that do not exist and omits several that do.

**Pagination is inconsistent by design, not by accident:** offset pages (`page`/`page_size`) for notifications, teams, bikes, routes and training; cursor (`before_seq`/`limit`, no `total`) for message history; offset for social search. Both are documented. This is acceptable — reporting it only so it is not mistaken for an oversight.

---

## 9. Mobile Audit

**Genuinely good:** exactly one `ApiClient` (`package:http` appears in 2 files, neither a screen); no duplicated models; no stray hardcoded URLs outside `app_config.dart`; `flutter_secure_storage` for tokens with `SharedPreferences` correctly absent; `ApiException` parsing handles three envelope shapes and normalizes network failures; `friendlyError` maps codes to localized sentences with a fallback ladder; enums `parse` with `orElse` rather than throwing; `api_number.dart` exists specifically because the backend serializes `NUMERIC` as strings; no `dynamic` and no unguarded `!` in a data path; no `setState` during `build`.

**Blockers and gaps:** `B1` (no refresh), `B2` (stranded rides), `H11` (no `user_id` on local tables), `H12` (logout invalidates nothing), `H13` (zero lifecycle handling), `H14` (buffer cleared before durable write), `H15` (server rejections discarded), `M31` (offline cold start reads as forced logout), `M37`/`M38` (unsafe casts and `${}` null-stringification), `L5` (fabricated dashboard numbers — the only place in the app where numbers are invented rather than fetched, presented with the same visual weight as real data), `L6`, `L7`, `L8`, `L9`, `M32`–`M36`.

**Layering:** nominally consistent (10/12 features use `data`/`domain`/`presentation`) but the boundaries are not real — there is no DTO layer, domain models carry `fromJson` and are therefore wire-coupled, a presentation-shaped `LiveRide` lives in `data/`, and the app-wide Drift database is owned by `features/ride/` while `features/routes/` already reaches into it. Medium debt, not a runtime bug.

**Deep links — the strongest-engineered part of the client.** Allowlist with required trailing-id counts; scheme/authority rejection; longest-prefix match; UUIDv4 validation; exact segment-count match; fail-closed to `null`; single-slot pending queue with once-only consumption and newest-wins; and it clears on `unauthenticated` so a tap cannot replay into a later account. Ten focused adversarial tests. The only defect is `L8`: **no producer exists**, so the whole cold-start path is currently unreachable — asserted only by a test that reads `main.dart` as a string.

---

## 10. Offline / Sync Audit

**What is correct and worth preserving verbatim:** the GPS filtering engine mirrors the server exactly and is thoroughly unit-tested; idempotency keys (`client_ride_uuid` as PK, `client_point_uuid` as UNIQUE) are **persisted before the network call** in both ride paths and reused verbatim on retry; `markUploaded` runs only **after** acknowledgement, so the watermark can never run ahead of stored data; Drift access is 100% typed with no raw SQL; chunk writes use a real `db.batch` transaction; chat reuses `client_message_id` across retries with a test proving no duplicate bubble.

**What is broken:** `B2`, `H14`, `H15`, plus **no sync engine and no queue** — `syncNow()` is a method on a feature-local class reading its own private fields, driven by a fixed `Timer.periodic(15 s)` with no backoff, no jitter, no attempt counter, no max attempts, no dead-letter, and no persisted failure state (`_syncState` is an in-memory string that dies with the process). A permanently-rejected chunk (422) is retried forever. One 100-point chunk per tick. `finish()` can finalize while a chunk is in flight, so the server summary permanently understates the ride. `/ride/recovery` is fully built and **never navigated to**, so an orphaned ride is invisible.

**Coverage matrix — the honest answer:**

| Scenario | Covered? |
|---|---|
| Network interruption mid-ride | **NO** — the `MockClient` returns 200 for everything |
| Token expiry mid-sync | **NO** — no 401 in any ride test |
| App restart / crash recovery | **NO** — every test uses an in-memory DB; no upgrade path is ever exercised |
| Duplicate prevention | **PARTIAL** — chat yes (`fake.sends == 1`), rides no |
| Chunking (>100 points) | **NO** — no test creates more than one chunk |
| Server rejection / conflict | **NO** — the response body is ignored |
| Local DB migration `onUpgrade` | **NO** |
| Deep-link allowlist | **YES — 10 adversarial tests** |
| Localization completeness | **YES — key parity across en/fr/ar, interpolation, missing-key fallback, no Latin-in-Arabic** |

`test/ride_test.dart` covers the GPS engine thoroughly and the recorder's happy path once; it covers sync under failure not at all. The notification and l10n suites are the best-tested code in the repo and are the right internal benchmark.

**Can this architecture support training, routes, social and notifications later?** **Partially — it needs a new sync layer, not a rewrite of the ride path.**

Reusable: `ApiClient`, the proven idempotency-key *pattern*, the `GpsEngine` as a model of testing pure logic, Drift + `db.batch`, additive `MigrationStrategy`.

Missing, and genuinely required: a `SyncEngine`/`SyncCoordinator` abstraction; a durable outbox table (which would replace `uploadedSeq`, the dead `LocalPoints.uploaded`, and the never-built `sync_state`); a shared `IdempotencyKey` contract (two incompatible ad-hoc implementations today); a per-resource conflict strategy; lifecycle and connectivity triggers; and single-flight token refresh.

Per domain: **routes** is ~40% done (the `CachedRoutes` cache already exists — least work, needs user scoping and invalidation); **training** wants last-writer-wins + version-conflict, which is *simpler* than the ride watermark and needs a different strategy; **social** is deliberately network-only with aggressive invalidation and needs a genuine outbox for offline mutations — a different problem from uploading GPS chunks; **notifications** needs only a read-cache plus invalidation on push, and is blocked on the unbuilt push SDK.

The blocking structural problem is that the app-wide database is owned by `features/ride/` and is already the app-wide store, so the ownership debt is already paid; and `uploadedSeq` is a monotonic watermark that can express only "append-only log, forward-only, one cursor" — it cannot represent attempt counts, per-item state, conflict payloads, or any bidirectional pull.

---

## 11. AI Architecture Audit

**The critical question — is the AI layer ever authoritative? — is answered NO, and the answer is architecturally enforced, not merely documented.**

`app/ai` is imported by exactly **one** module outside itself: the coach router. There is zero coupling in the other direction. `training_calc.py`, `training_service.py`, `ride_service.py` and `gps_engine.py` contain no `app.ai` reference. The AI package contains **no write path at all** — no `db.add`, no `db.execute`, no ORM entity write; `TrainingActivity` and `TrainingLoad` are imported as *read* types only. `FtpRecord` and `Workout` are never referenced from `app/ai/`.

The strongest single piece of evidence: the model may only emit a `CoachDraft` whose fields are all `str` or lists of strings, plus two optional `Decimal`s that exist **solely** to be range-checked and are then carried as display text. **There is no numeric channel from the model back into any persisted column.** The prescription ceiling is the engine's — `_cap_violation()` compares against `calc.suggest_intensity_target(...)`, a violation is fatal and triggers the deterministic fallback, and the fallback's own target comes from `calc`. Numeric grounding is enforced: every number in model prose must match a context value within 1%, and ungrounded text is pruned or replaced by deterministic text.

Safety classification runs **before** the provider is obtained, so a safety-classified message never reaches a model — *"a prompt that asks a model to decline is a request the model can fail; a request that is never sent cannot."* Limits run before classification. `FakeAIProvider` is reachable only via `set_provider()` from tests; no production branch can return it. No HTML or Markdown is produced or rendered anywhere, so there is no XSS vector.

**Gaps:** `H16` (silent misconfiguration, and `/coach/status` actively lies), `M39` (English-only metric labels in all locales), `M40` (no prompt-version drift test and nothing persisted — contrast the enforced `CALCULATION_VERSIONS` drift test), `L1`/`L2`/`L3`/`L4`, and `H9` (the AI log line is, in practice, the word "coach" and nothing else, because the formatter drops every `extra` field).

---

## 12. Notification Audit

**Correct and verified:** the "row is the record, push is an accelerant" inversion holds — rows are committed before delivery is attempted, so provider failure cannot lose an event; `dedupe_key` is a partial unique index on an immutable business id; per-recipient keying prevents cross-recipient collision; the payload is exactly three keys built from named fields; deep links are allowlisted and UUID-validated; the pending-intent queue clears on logout; unknown `l10n_key` degrades to a generic sentence rather than throwing; `PUSH_INLINE_FANOUT_LIMIT` behaves exactly as documented (bounds delivery, not persistence, and logs the deferral); token ownership is enforced by `UNIQUE(provider, token)` with transfer-and-merge semantics on conflict.

**Defects:** `B3` (fan-out collapse — the most serious), `B4` (the test that certifies it), `H17` (deferred delivery with no queue and no persisted marker), `M24` (`notify_team_member_removed` keyed on a stable pair rather than an event, so a rejoin-and-re-expel is never notified).

**Not implemented, correctly:** no `FCMProvider`/`APNsProvider` (the enum names them only so a device row can record which ecosystem issued a token), no native configuration, no SDK, no worker, no push `title`/`body` (the OS renders push text in a locale the server cannot observe), no preferences table, no deletion/archival. `get_provider()` never raises — with nothing configured it reports every device `SKIPPED`, which is the correct normal state.

**Answering the brief's specific question:** `PUSH_INLINE_FANOUT_LIMIT` *does* behave as documented. Verified at `notification_service.py:541-550`.

---

## 13. Redis / Worker Audit

**Redis is used by exactly one endpoint** — `/health`. `get_redis()` is referenced only from `check_redis()`; there is no `Depends(get_redis)`, no cache, no pub/sub, no limiter bucket in Redis, and no `arq` import anywhere in `app/`. **Redis is fully down-tolerant by virtue of being unused**: a total Redis outage costs one health-field value and nothing else.

**The ARQ worker is a genuine stub, and the code says so honestly** — `workers/settings.py` is 12 lines, `functions = [ping]`, `redis_settings = None` with the comment "bound from REDIS_URL at runtime in later phases". Running it today would fail immediately. No job has ever been defined or run. The `arq` dependency is declared in `pyproject.toml` and never imported.

**Does anything assume a worker will run?** Exactly one place, and it is the problem rather than a design: `H17`'s `deliver_deferred` is enqueue-and-forget with no enqueue. Everywhere else is correct — email is synchronous with no queue assumed, and training analysis is synchronous and inline in the request with no eventual consistency assumed. There is **no `queued`/`pending` job status and no job table anywhere** in the schema, so no code path is silently waiting on a worker that does not exist.

`app/websocket/manager.py` is dead code with no `/ws` route, no auth, no channels, no pub/sub — despite `docs/01` and `docs/06` describing Redis pub/sub WS fan-out with per-channel authZ.

**Infrastructure a real worker needs** (derived from evidence): Redis ✅ have it; `arq` ✅ declared; a `RedisSettings` binding ❌ ~3 lines; a worker entrypoint ❌ (second `CMD`/compose service/CI step); a `deliver_push(notification_ids)` job wrapping the existing `_deliver` logic ❌; three `Notification` columns (`delivered_at`/`delivery_attempts`/`last_error`) ❌; an `enqueue_job` call replacing the `return` ❌; a `cron_job` for the `PUSH_DEVICE_STALE_DAYS` sweep ❌ — **note this setting is declared in config and referenced nowhere, so the growth it documents as prevented is unmitigated**; and job-level log correlation, since the `request_id` ContextVar has no value in a worker.

---

## 14. Rate Limiting Audit

Implementation is 17 lines: a **sliding window over a list of timestamps** (despite the docstring claiming a token bucket), in a **module-global `dict`** with no Redis path, no TTL, no backend abstraction, and no injection point.

**59 of ~116 endpoints are limited.** Keying is inconsistent across three policies: IP-only (auth), user-only (chat, social, teams, notifications), and `{user}:{IP}` composite (bikes, rides, routes, training).

| Finding | Severity | Detail |
|---|---|---|
| `H10` unbounded memory | HIGH | Entries never evicted. Unauthenticated `POST /auth/login`, IP-keyed, grows RSS without limit from rotating sources. |
| `M10` proxy defeat | MEDIUM | No `ProxyHeadersMiddleware`; behind a reverse proxy every client shares one key, so `register 10/h` becomes 10 **platform-wide** — a trivial signup DoS. Conversely, if proxy headers are enabled, `X-Forwarded-For` spoofing bypasses everything. |
| `M11` IP-rotation bypass | MEDIUM | The `{user}:{IP}` composite resets on IP change, so an attacker gets an unlimited quota — and a legitimate IPv6 user gets a fresh quota on every network change. |
| `M12` | MEDIUM | `POST /auth/password-reset/confirm` is unrated and runs Argon2id on success — an unmetered CPU/memory amplifier. |
| `M13`–`M15` | MEDIUM | `PATCH /profile`, `GET /routes/{id}/geometry`, `GET /routes/{id}/export/gpx`, `GET /training/summary` all unrated. |
| `M16` | MEDIUM | N+1: one COUNT per ride in list pages; one extra query per activity with zones. |
| — | — | **Fail open or closed?** Neither — there is no external storage, so no storage-failure path exists. The precise statement is that the limiter *cannot* fail closed ever, because it has no shared state. |

**The documented limitation is accurate** in all three places it appears (`notifications.py:47-49`, `rate_limit.py:9`, `docs/04:212-214`), and Redis — the stated upgrade precondition — is already a declared dependency already running in compose and CI. The gap is that the mitigation has not been taken despite the dependency being present. This is the single highest-value, lowest-effort fix in the audit.

---

## 15. Observability Audit

**Present:** request correlation plumbing; `redact()`; `setup_logging`; a health endpoint; and per-service `_log()` helpers on chat/social/team/notification/AI.

**Missing or broken:**

| Finding | Severity | Detail |
|---|---|---|
| `H8` | HIGH | The `X-Request-ID` a client is given is **not** the id in the logs. `new_request_id()` ignores its argument and generates a second uuid. Every investigation starting from a user-reported id fails. 500s carry no id at all. |
| `H9` | HIGH | The formatter references **no** `extra` key, so every field at ~90 `_log()` sites is discarded. `auth_service`, `bike_service`, `ride_service`, `route_service` log nothing — login failure, refresh-token reuse, and GPX import are invisible. |
| `M18` | MEDIUM | **No metrics and no tracing anywhere** — no Prometheus, OpenTelemetry, or Sentry. |
| `M17` | MEDIUM | `/health` always returns `200` with `status: "ok"` regardless of DB or Redis state. **Verified: no status-code assignment, no branch.** Unusable as a readiness probe; a pinned test asserts this behaviour, so it is deliberate — but then no readiness probe exists at all. |
| `M19` | MEDIUM | **429 events are never logged.** An operator cannot detect abuse, credential stuffing behind a limiter, or a misconfigured limit throttling legitimate traffic. |
| `M20` | MEDIUM | `check_db` and `check_redis` swallow everything with no log — a total Postgres outage and an auth error produce identical output. |
| — | MEDIUM | Logging is **plain text, not JSON**, despite the docstring saying "JSON-ish". `root.handlers = [handler]` destroys any operator-installed logging config at import time. No file handler, no rotation, no documented log shipping. |
| — | LOW | `redact()` uses exact-match keys — `token_hash`, `refresh_token`, `access_token`, `email`, `lat`, `lon`, `start_lat` are **not** covered, and it does not recurse into lists. |

**Answering the brief's six questions:** *what failed* — partially (event name only); *which user* — partially, and dropped by the formatter; *which request* — yes in logs, no from the client; *which subsystem* — yes; *can it be retried* — **no**, nothing logs retryability or attempt count, and both provider mappers compute a retryable flag then discard it; *was sensitive data logged* — redaction is designed but its output is discarded, so the question is moot in one direction and unenforced in the other.

**Absent:** no `/ready`, no `/live`, no `/metrics`, no DB-revision check, no pool-saturation check, no disk check.

---

## 16. Testing Audit

**The counts are real.** 552 backend tests pass, 428 Flutter tests pass, 101/101 live-smoke checks pass across 2 runs, the full migration chain applies to a virgin database and downgrades back, ruff/mypy/analyze/format are all clean, and both web release and debug APK builds succeed. That is a stronger baseline than most projects at this stage.

**But the audit's central finding is about what the suite does not protect.** Three defects in §4.1 were invisible to all 980+ checks, and one of them (`B4`) is a test that passes *because* it asserts the wrong thing. Concretely:

- `B3`/`B4` — the notification fan-out invariant is certified by a test asserting `total` without asserting type.
- `B1` — no test constructs an expired-access-token scenario, because the client has no refresh path to test. `refreshSession()` has zero call sites and therefore zero tests.
- `B2`/`H14`/`H15` — every sync test runs against a `MockClient` returning 200 for everything, and `uploadedPosts >= 1` is satisfied by exactly the buggy behaviour (a single chunk).
- `M26`/`H7` — the two missing `IntegrityError` handlers are both **concurrency** bugs. The suite is entirely sequential, so a read-then-insert TOCTOU cannot fail in a test.
- `H19` — the API suite builds schema from `Base.metadata.create_all`, never from migrations, and `test_migrations.py` asserts index and constraint **names** only. Model/migration drift is structurally invisible.
- `M27` — `GET /teams/{id}/members` returning 500 for a stranger is not covered, because no test asserts the status code for a non-member on that route.

**What the suite protects very well, and should be credited:** IDOR on every resource router (cross-owner tests throughout), the full blocked-DM policy including "no row is created", token non-exposure, the 404-not-403 convention on most routes, notification payload safety, the deep-link allowlist (10 adversarial tests), localization key parity across en/fr/ar including no-Latin-leakage-in-Arabic, and the idempotency invariant for chat.

**Assessment:** coverage is broad and shallow where it matters most. The suite is strong on **authorization and privacy** — the invariants that are hardest to retrofit — and weak on **failure modes**: concurrency, partial failure, token expiry, restart recovery, and response-shape assertions. Those are exactly the classes that produced every blocker in this audit.

---

## 17. Deployment Readiness

| Stage | Verdict | Basis |
|---|---|---|
| **Development** | ✅ **Ready** | All gates green; migrations apply from zero; live smoke passes twice. One caveat: the documented first-run path is broken (`H3`), so a new developer must be told the correct password. |
| **Staging** | ❌ **Not ready** | No deployment procedure (`H5`); Alembic ignores `DATABASE_URL` and can migrate the wrong database while appearing to succeed (`H4`); `/health` cannot serve as a readiness probe (`M17`); Dockerfile runs as root with no lockfile, no `HEALTHCHECK`, no `.dockerignore` (`L19`); compose has no app service and publishes an unauthenticated Redis (`L20`); `B5` means a staging deploy with defaults signs tokens with a public key. |
| **Production architecture** | ⚠️ **Partially** | Layering, ownership scoping, the AI isolation boundary, provider seams and the migration chain are all sound and would survive to production. But there is **no infrastructure definition at all**, and secrets have no production supply path. |
| **Production operations** | ❌ **Not ready** | Zero commits, so **CI has never run** (`H2`); no backup, restore, PITR, retention or RPO/RTO documentation anywhere (`L21`); no rollback procedure, and every downgrade destroys data (`L22`); no metrics or tracing (`M18`); rate limits and the AI budget are per-process, so a multi-worker deploy silently multiplies them; no reverse proxy, TLS or proxy-header configuration (`M23`, `M10`); `docs/09-testing-cicd.md` documents a security-scan job, a dependency audit, an OpenAPI snapshot gate and an APK build — **none of which exist** (`L17`). |

**Explicitly: passing tests is not production readiness, and this repository is not production-operationally-ready.** The single most consequential operational fact is `H2`: because there are no commits, the CI configuration is an unexecuted file, so "CI is green" is currently an assumption rather than a fact.

---

## 18. Technical Debt

### Must Fix — blocks the next phase

| ID | Item |
|---|---|
| `B1` | Flutter token refresh — **single-flight mandatory** (family reuse detection would otherwise log users out of all devices) |
| `B2` | `finish()` stranding uploaded ride data |
| `B3` | Team-archive fan-out collapse |
| `B4` | The team-archive test that certifies `B3` |
| `B5` | `SECRET_KEY` placeholder fails open |
| `H1` | `DevEmailService` wired unconditionally — reset tokens never delivered, unbounded credential memory growth |
| `H2` | Zero git commits — CI has never executed |

### Should Fix Soon — meaningful future risk

`H3`–`H5` (deployment/migration path) · `H6` (secrets reflected in 422 bodies) · `H7` (point ingest 500 + chunk loss) · `H8`/`H9` (correlation id and dropped log fields — cheap fixes, disproportionate operational value) · `H10` (unbounded limiter memory; Redis is already available) · `H11`/`H12` (no `user_id` locally; logout invalidates nothing) · `H13` (zero lifecycle handling) · `H14` (buffer cleared before durable write) · `H15` (server rejections discarded) · `H16` (AI misconfiguration silent, `/coach/status` misleading) · `H17` (deferred push with no worker and no delivery state) · `H18` (missing `ride_points` composite index) · `H19` (tests never build schema from migrations) · `M1`–`M3` (auth session hardening) · `M21` (erasure impossible by either mechanism) · `M24` (dedupe key is a pair, not an event).

### Accepted Debt — known, documented, safe to carry for now

In-process rate limiter and AI budget (`limit × workers`) — accurately documented in three places, and Redis is available when it matters · synchronous fan-out with a 20-recipient delivery bound · no FCM/APNs · no worker · no WebSockets · polling instead of SSE · plaintext push tokens (forced by the function, correctly quarantined) · two overlapping profile models (`UserProfile` / `SocialProfile`) · single-token-generator reuse for reset and refresh tokens · `team_memberships.status` as a single-value column · `Enum` values declared but never emitted.

### Future Architecture — valid, not currently needed

Distributed rate limiter and job broker on Redis · real push providers and native config · SSE/WebSocket transport with per-channel authZ · account deletion with a purge path and an explicit retention policy · expand/contract migrations with a data-preserving rollback · an outbox table plus a `SyncEngine` for offline domains · metrics/tracing · notification retention/archival · per-type notification preferences · admin roles and a real audit log.

### Not a Problem — investigated and intentionally accepted

Missing FCM/APNs, workers, WebSockets, payments, subscriptions, ads, live location, group rides, and account deletion are all **out of scope by design** and are not defects. Redis being unused is not a defect. ARQ being a stub is not a defect — the code says so honestly in four places. The AI layer's non-authority is a strength, not a gap. No IDOR exists in any router. All 44 FKs declare `ondelete`. No GPS coordinate ever reaches the AI context or a push payload.

---

## 19. Recommended Next Phase

**Do not start a new product phase.** The correct next phase is **Phase 8.6 — Production Hardening & Session Integrity**, and it is a remediation phase, not a feature phase.

**Rationale.** `B1`–`B5` are defects in code that Phase 8.4 declared complete and that 980+ passing checks certified. Every one of them is in a path the next phase would build on: token refresh underpins every authenticated surface; the notification fan-out underpins any social feature that notifies people; the team-archive notification is a correctness bug in the social graph itself. Building on top would compound the defect and, worse, would add new features under a verification story that has already been shown to report green over a broken invariant (`B4`) — which means the next phase's own gates would not be trustworthy either.

**Scope, in order:**

1. **`B5` + `H1`** — fail-closed configuration. Refuse to boot in production on a placeholder `SECRET_KEY`; environment-branch the email service. Small, and it removes the two worst failure modes.
2. **`B1`** — single-flight token refresh in `ApiClient`, with the family-reuse hazard designed for explicitly (memoized in-flight future, one rotation, one retry).
3. **`B3` + `B4`** — the `key is not None` guard, plus type-asserting multi-member fan-out tests. A one-line fix that is only trustworthy once the test is corrected.
4. **`B2`** — make `finish()` drain or defer, and make startup scan for pending uploads by watermark rather than by status.
5. **`H2` + `H3` + `H4` + `H5`** — commit the repository, run CI for real, reconcile the three Postgres passwords, make Alembic read `Settings.DATABASE_URL`, and write down a migration-on-deploy procedure.
6. **`H8` + `H9` + `H10`** — correlation id that matches the logs, log fields that survive the formatter, limiter eviction. Three small changes with the largest operational payoff in the audit.

**Risks to manage during this phase:** the token refresh in step 2 is the highest-risk single change, because a naive implementation triggers ADR-04 family revocation and logs users out of every device — it needs a deliberate single-flight design and a test that fires two concurrent refreshes. Fixing `B3` will change observable notification counts and may surface latent expectations in existing tests. Enabling real CI (`H2`) may reveal failures in code that has never been exercised outside one machine.

**What should NOT be done yet:** no FCM/APNs (the seam is the deliverable; the vendors are a later phase, and `H17` must be resolved first or push will be silently dropped for any team over 20 members) · no new product feature · no WebSockets or SSE · no account deletion until `M21`'s erasure contradiction is resolved, because neither hard nor soft delete currently works · no worker until the delivery-state columns exist · no route/social/offline-first expansion until the outbox and `SyncEngine` exist, since `uploadedSeq` cannot represent those domains.

---

## 20. Explicit Non-Goals

No implementation was performed during this audit. Specifically, **none** of the following was created, modified, migrated, installed, or configured: FCM, APNs, Firebase, WebSockets, SSE, background workers, ARQ job functions, Redis pub/sub, Redis-backed rate limiting, live location, location sharing, group rides, route sharing, media messaging, payments, subscriptions, ads, Garmin/Polar/Komoot integration, BLE, AI automation, account deletion, an analytics platform, or a moderation platform.

The only file created is this report: **`docs/phase-8.5-architecture-audit.md`**.

**Documentation discrepancies were reported, not corrected.** Per the audit brief, I did not silently modify unrelated files. Three documentation items are worth an explicit decision from the maintainer, because each actively misleads:

1. `docs/08-security.md` claims at-rest encryption, an audit log for location access and admin actions, GPX virus scanning, and store-webhook subscription verification. **None exists**, and there is no audit log of any kind. This is the document a security reviewer would scope from.
2. `docs/phase-8.4-architecture-audit.md` is a pre-implementation proposal still labelled "Decided" and contradicts shipped code on four load-bearing points — most seriously, it places `l10n_key` and `params` (including an actor display name) on a lock-screen push payload, which is precisely the leak `ADR-15` §7.1 and `docs/08-security.md` exist to prevent. Its filename also differs from the ADR it created (`ADR-15-notifications.md`).
3. `README.md` is frozen at Phase 2 and states that `alembic upgrade head` creates "no tables"; it creates 30. `README.md:8` and `docs/01:9-11` also describe Redis cache/pub-sub/rate-limiting and five worker responsibilities that do not exist, and `docs/09-testing-cicd.md` describes a security-scan job, a dependency audit, an OpenAPI snapshot gate and an APK build that were never implemented.

I also did **not** correct one in-code docstring that contradicts its own function 90 lines below: `app/notifications/provider.py:9-10` claims the factory "raises when nothing usable is configured", while `get_provider()` at `:105-107` documents that it deliberately never raises. Reported as `L15` for the maintainer to fix alongside the Phase 8.6 changes.

---

## 21. Final Verdict

# BLOCKED

**Blockers, all verified directly against the running system or by exhaustive source search:**

1. **`B1` — CRITICAL:** the Flutter client has no access-token refresh. `refreshSession()` is declared once and called **zero** times across all 125 Dart files; there is **no** 401 branch anywhere. Access tokens live 15 minutes. Every session dies at minute 16 with no recovery, and no ride longer than 15 minutes can sync.
2. **`B2` — CRITICAL:** `RideRecorder.finish()` permanently strands uploaded ride data. It cancels the only retry timer, uploads at most one 100-point chunk, marks the ride `completed`, and `unfinishedRide()` filters to `status IN ('recording','paused')` — so the remaining points become unreachable by every query in the app.
3. **`B3` — CRITICAL:** archiving a team notifies **1 member instead of N**. `dedupe_key == key` with `key is None` renders `IS NULL`, matching any prior unkeyed row. Empirically reproduced against the live database: 3 live members → **1 row, 1 distinct recipient**.
4. **`B4` — CRITICAL:** the shipped test certifying `B3` passes for the wrong reason. It asserts only `total == 1` and never checks type; the member B's single notification is the earlier `team_invitation`. Empirically reproduced: `A: ['team_archived']` / `B: ['team_invitation']`. This is stop-rule §25.13 — a test suite giving false confidence over a broken critical invariant.
5. **`B5` — CRITICAL:** `SECRET_KEY` defaults to an in-repo literal, is the sole HS256 signing key for every access token, and production startup never validates it. A deploy with no `SECRET_KEY` set boots successfully, signed with a publicly known key.
6. **`H1` — HIGH (stop-rule §25.12, missing required dependency):** `DevEmailService` is wired unconditionally with no environment branch while the production `EmailService.send` raises `NotImplementedError`. Self-service password reset is non-functional in every environment, and every reset/verify token accumulates unboundedly in process memory.
7. **`H2` — HIGH (stop-rule §25.13):** the git repository has **zero commits** (`git rev-list --all --count` → `0`, `git ls-files` → empty). `.github/workflows/ci.yml` is untracked and has **never executed**, so the entire documented CI gate posture is unverified in practice.

**What is genuinely strong, and should not be discounted because of the above:** ownership scoped in the SQL `WHERE` clause across all ~116 endpoints with **no IDOR in any of the 12 routers**; the deterministic Training Engine provably authoritative over the AI layer, with no numeric channel from model to database; the blocked-DM policy enforced by writing **no row at all**; push payloads of exactly three keys; refresh-token reuse detection burning the whole session family; 44/44 FKs declaring `ondelete`; five well-chosen partial unique indexes; 7/7 partial-index predicates matching their query sites; idempotency keys persisted **before** the network call in both rides and chat; a 100%-typed Drift layer with no raw SQL; and genuinely tested EN/FR/AR with working RTL.

**Do not begin the next feature phase until `B1`–`B5` and `H1`–`H2` are resolved.** The recommended next phase is **Phase 8.6 — Production Hardening & Session Integrity**, scoped in §19. Critically, `B1` (token refresh) and `B3` (notification fan-out) are each small in code size but must be treated as high-risk changes: a naive refresh implementation trips ADR-04's family revocation and logs users out of every device, and the `B3` fix must land together with a corrected test or the same false confidence recurs.

# CycleCoach Security Audit

Workstream: **WS-M — Production Security Audit & Hardening**
Phase: 10
Baseline: `780592f`
Status: see §3

Every finding below was **reproduced against the running code** before it was
written down, and every fix has a regression test that fails against the
pre-fix behaviour. Nothing here is a code-reading guess.

No finding in this document contains a real secret, token, password or private
credential. Canary values used in tests are synthetic and generated per run.

---

## 1. Threat model

```
Internet
   |
   v
Reverse proxy / load balancer        <- TLS termination, HSTS, request size
   |
   v
FastAPI (Starlette)
   |-- TrustedHostMiddleware         production only: *.cyclecoach.app
   |-- CORSMiddleware                explicit allowlist, credentials on
   |-- correlation middleware        X-Request-ID, X-Content-Type-Options
   |
   +---- PostgreSQL 16   durable, authoritative, RESTRICT on attribution
   +---- Redis 7         EPHEMERAL: live consented positions only, TTL 300s
   +---- AI provider     outbound only, deterministic fallback on failure
   +---- Push provider   FUTURE seam, rows always written, delivery never blocks
   +---- Email provider  dev provider BANNED in production (WS-B)
```

The mobile application is an **untrusted client**. It is assumed an attacker has
the APK, can read every byte of it, and can issue arbitrary requests.

### Trust boundaries that matter most here

| Boundary | Rule enforced |
|---|---|
| Mobile → API | Every identifier in a request is a claim, never proof. Ownership is re-resolved server-side against the caller's own roster row. |
| API → PostgreSQL | The only durable store. `RESTRICT` on `messages.sender_user_id`, `conversation_members.user_id`, `notifications.actor_user_id`, `group_ride_participants.invited_by_user_id` and `group_rides.organizer_user_id` means content stays attributable. |
| API → Redis | Holds **consented live positions only**, TTL-bounded. Never a system of record. |
| Service → provider | Provider failure must not fail a business action (notification) and must never become authoritative (AI). |

### Assets

Credentials (password hashes, access/refresh tokens) · consented live location ·
social graph and chat · training history · AI provider key · production secret key.

---

## 2. Method

Baseline established first, then a systematic sweep of: error handling and
leakage, logging and secret masking, correlation, authorization/IDOR,
mass assignment, role escalation, team and group-ride access, live location,
Redis and PostgreSQL configuration, CORS, file upload and GPX, chat,
notifications, AI boundaries, rate limiting, dependency manifests, security
headers, and mobile storage/network configuration.

Each finding was **reproduced** (a live request, a live log capture, or a live
`redact()` call), then a regression test was written and confirmed to fail
before the fix.

---

## 3. Findings and fixes

### SEC-01 — HIGH — Credentials echoed in validation error responses

**Area:** error handling / sensitive data leakage
**File:** `backend/app/core/errors.py:15-25` (pre-fix)

**Description.** The `RequestValidationError` handler returned
`jsonable_encoder(exc.errors())`. Pydantic's error entries include `input`: the
value that *failed* validation. On this API those fields are `password`,
`password_confirm`, `new_password`, `new_password_confirm`, `refresh_token` and
reset/verification `token`.

**Reproduction** (live, pre-fix):

```
POST /api/v1/auth/register  {"password": "short", ...}
422 {"error":{"details":{"errors":[
  {"loc":["body","password"],"msg":"String should have at least 10 characters",
   "input":"short","ctx":{"min_length":10}}, ...]}}}

POST /api/v1/auth/refresh  {"refresh_token": "tooshort"}
422 ... "input":"tooshort" ...
```

**Impact.** A response body is not a private channel. Reverse proxies, load
balancers, WAFs and APM tools (Sentry and comparable products capture response
bodies by default) persist it; so do developer consoles and client-side error
reporters. Echoing a credential back to the caller that just supplied it
protects nobody while multiplying the number of places the credential exists.
The submitted `email` leaked the same way.

**Fix.** `_safe_errors()` reduces each entry to an **allowlist** — `type`, `loc`,
`msg`, plus numeric `ctx` constraints. An allowlist rather than a denylist,
because pydantic can add keys between versions and a denylist would silently
begin publishing whatever it added. `input` is never included. `ctx` is filtered
to `int`/`float` only, which is what preserves `min_length` for client-side
pre-validation while excluding the non-serializable objects that made the
original `jsonable_encoder` call unsafe.

**Contract impact:** none. No test asserted on `input`, and no mobile code reads
`details.errors[].input`. The envelope keys are unchanged and
`error.code == "VALIDATION_ERROR"` still holds. `loc`/`msg` are preserved, so a
client can still attach the message to the right form field.

**Regression tests:** `test_a_short_password_is_not_echoed_back`,
`test_a_refresh_token_is_not_echoed_back`,
`test_a_password_reset_token_is_not_echoed_back`,
`test_a_submitted_email_is_not_echoed_back`,
`test_no_validation_error_carries_an_input_key`,
`test_validation_errors_still_identify_the_offending_field`,
`test_validation_errors_keep_numeric_constraints`,
`test_the_error_envelope_shape_is_unchanged`,
`test_a_canary_password_never_reaches_the_log`.

**Status:** FIXED

---

### SEC-02 — MEDIUM — SQL statements and bound parameters written to logs

**Area:** logging / database
**Files:** `backend/app/db/session.py:9`, `backend/app/core/errors.py:56` (pre-fix)

**Description.** The engine was created without `hide_parameters`, so
SQLAlchemy's default is `False`, and the unhandled-exception handler logged
`exc` itself. A SQLAlchemy exception renders as the statement **plus its bound
parameter values**:

```
(psycopg.errors.UniqueViolation) duplicate key value violates unique constraint ...
[SQL: INSERT INTO users (email, password_hash, ...) VALUES (%(email)s, ...)]
[parameters: {'email': 'victim@example.com', 'password_hash': '$argon2id$v=19$m=...'}]
```

**Impact.** A duplicate registration — trivially triggerable — put a plaintext
email address and an Argon2id hash into the log aggregator. This is the same
disclosure class `ride_location_service` explicitly refuses for coordinates,
applied to identity data instead.

**Fix.** Two independent layers, because either alone can be undone by a future
line: `create_async_engine(..., hide_parameters=True)`, and the handler now logs
`type(exc).__name__` rather than `exc`.

**Regression tests:** `test_the_engine_hides_bound_parameters`,
`test_a_uniqueness_violation_logs_no_email_or_hash` (asserts on captured log
text, so it holds even if `hide_parameters` is later removed).

**Status:** FIXED

---

### SEC-03 — MEDIUM — Two divergent secret-masking rules; compound names unprotected

**Area:** logging / sensitive data leakage
**File:** `backend/app/core/logging.py:10-37` (pre-fix)

**Description.** `redact()` matched keys by **exact** membership in `_SENSITIVE`.
The compound names this codebase actually uses therefore matched nothing and
passed through in cleartext:

```
redact({"access_token": "t"})   -> {"access_token": "t"}     NOT redacted
redact({"refresh_token": "t"})  -> {"refresh_token": "t"}    NOT redacted
redact({"password_hash": "h"})  -> {"password_hash": "h"}    NOT redacted
redact({"token_hash": "x"})     -> {"token_hash": "x"}       NOT redacted
redact({"email": "a@b.co"})     -> {"email": "a@b.co"}       NOT redacted
redact({"points": [{"latitude": 5.0}]})  -> unchanged         NOT recursed
```

`redact()` also recursed only into dicts, never lists — which is the exact shape
a location payload takes. Separately, `ExtraFormatter` carried its own
`_SENSITIVE_SUBSTRINGS` list that knew about `password`/`token` but not
`email`/`latitude`: two lists intended to agree, already divergent.

**Impact.** Latent rather than actively exploited — all 78 `_log()` call sites
were audited and none currently passes `email`, a token or a coordinate. The
protection in force today is per-service *discipline expressed in comments*,
not enforced by code. A single new `extra={"email": ...}` would have leaked.

**Fix.** One predicate, `_is_sensitive()`, used by both `redact()` and
`ExtraFormatter`. Two lists that are meant to agree will eventually disagree; one
function cannot. Matching is **substring** for long unambiguous credential names
(`password`, `token`, `secret`, `api_key`, `authorization`, `email`) and
**exact** for short location/prose forms (`latitude`, `lat`, `coordinates`,
`gps`, `message`, `prompt`, …).

The split is deliberate and was itself forced by a test failure: naive substring
matching made `lat` match `latency_ms`, redacting the service's own performance
metric. Over-redaction is a real cost too — it destroys the log's usefulness, so
the rule must not simply be widened without checking collisions. `redact()` now
also recurses into lists and tuples.

**Regression tests:** `test_redact_masks_every_sensitive_key` (19 parametrized
keys), `test_redact_still_masks_the_names_it_always_did`,
`test_redact_recurses_into_a_list_of_dicts`, `test_redact_recurses_through_nesting`,
`test_redact_leaves_operational_fields_alone` (the `latency_ms` control),
`test_redact_passes_through_scalars`, `test_a_canary_token_never_reaches_the_log_via_redact`.

**Status:** FIXED

---

### SEC-04 — MEDIUM — Log formatter silently discarded third-party records

**Area:** logging / observability
**File:** `backend/app/core/logging.py:114, 121, 123` (pre-fix)

**Description.** The format string requires `%(env)s` and `%(request_id)s`, but
those attributes are injected by `CorrelationFilter` attached to the
`cyclecoach` **logger**. Any record from a different logger that propagates to
the root handler reached the formatter without them and raised
`ValueError: Formatting field not found in record: 'env'`. Python's logging
swallows that and prints a traceback to stderr — the record is **lost**.

Reproduced live during this audit; the affected loggers are `httpx` (the AI
provider's outbound calls, which carry rider health-adjacent context),
`sqlalchemy.engine` and `asyncio`. `uvicorn.access` is unaffected (it has
`propagate: False` and its own handler).

**Impact.** Security-relevant boundary records — outbound calls to third
parties — were being dropped silently, which is exactly the class of event a
security audit needs.

**Fix.** `ExtraFormatter.format()` now guarantees both attributes exist,
defaulting to `-` (the same default as the `request_id` ContextVar).

**Regression tests:** `test_the_formatter_renders_a_record_from_a_foreign_logger`,
`test_the_formatter_still_renders_a_correlated_record`.

**Status:** FIXED

---

### SEC-05 — MEDIUM — 500 responses did not carry the correlation id

**Area:** observability / incident response
**Files:** `backend/app/main.py:50-51`, `backend/app/core/errors.py:51` (pre-fix)

**Description.** `main.py` documented: *"The SAME id goes into the logs and the
response header, so a reported id is searchable."* Starlette's
`ServerErrorMiddleware` sits **outside** the user middleware stack, so an
unhandled exception propagated past the correlation middleware and
`headers["X-Request-ID"] = rid` never executed.

Reproduced pre-fix: a 422 returned the id; a 500 returned `X-Request-ID: None`,
while the log line for that same request did carry it.

**Impact.** On exactly the requests worth tracing, the client cannot obtain the
id that is sitting in the logs. The audit trail exists but is unreachable from
the reporter's side.

**Fix.** The middleware publishes `request.state.request_id`; the unhandled
handler reads it and sets the header when present.

**Regression tests:** `test_a_500_carries_the_request_id` (drives a real 500
through the ASGI stack — the defect was about *where* the handler sits),
`test_a_validation_error_carries_the_request_id`,
`test_the_unhandled_handler_attaches_the_request_id`,
`test_the_unhandled_handler_omits_the_header_when_there_is_no_id`.

**Status:** FIXED

---

### SEC-06 — LOW — Full API schema published in production

**Area:** attack surface
**File:** `backend/app/main.py` (pre-fix)

**Description.** `docs_url="/docs"` and `openapi_url="/openapi.json"` were set
unconditionally. `GET /openapi.json` returned 200 and 176,804 bytes with **no
authentication**: the complete route table, every parameter and every schema.

**Impact.** Hands an attacker the API map for free, including the Phase 9
group-ride and location surfaces whose safety partly depends on nobody knowing
the shape in advance.

**Fix.** Disabled in production only (`docs_url`, `redoc_url`, `openapi_url` all
`None`). Development and test keep them, because the contract tests read the
schema.

**Regression tests:** `test_production_disables_the_openapi_schema`,
`test_development_keeps_the_openapi_schema`.

**Status:** FIXED

---

### SEC-07 — MEDIUM — Release builds defaulted to cleartext localhost

**Area:** mobile network security
**File:** `mobile/lib/core/config/app_config.dart` (pre-fix)

**Description.** `AppConfig` had **no validation at all**, and `env` was read but
referenced nowhere in the entire app. `apiBase` defaulted to
`http://localhost:8000` and `wsBase` to `ws://localhost:8000` with no build-mode
awareness.

**Impact.** A release build that omitted `--dart-define=API_BASE=...` started up
silently pointing at localhost over **cleartext HTTP**, carrying the access
token, the refresh token and every recorded GPS coordinate to a host that is not
the API. The app looked and behaved like a working build: it logged in and
recorded rides. Nothing in the running app revealed that its traffic was going
nowhere in the clear.

**Fix.** `productionConfigProblems()` / `validate()`, mirroring the backend's
`Settings.production_config_problems()` vocabulary, called once from `main()`
before the first frame. Production requires `https`/`wss` and rejects loopback
hosts; an unrecognised `APP_ENV` is rejected in **every** build, so a typo cannot
skip the production checks. `prod` is accepted as an alias so a release pipeline
using it is still gated.

**Regression tests:** `mobile/test/security_config_test.dart` — 20 tests covering
cleartext refusal, loopback refusal, unparseable URLs, the environment
fail-open guard, dev/test/staging exemption, and no-secret-in-message.

**Status:** FIXED

---

## 4. Audited and confirmed SAFE

Recorded so the coverage is legible, and because "checked and fine" is a result.

### Error handling
- No `debug=True` on `FastAPI()`, so FastAPI's `DebugTracebackMiddleware` is never
  installed and the default traceback handler is unreachable.
- All four failure modes verified against a live client: unhandled exception,
  validation error, `HTTPException`, and database exception. The 500 path returns
  a byte-identical generic body in every case — no stack trace, SQL, filesystem
  path, exception repr or SQLAlchemy internal reaches any response.
- All ~180 domain-error construction sites use hand-written messages; none
  interpolates an exception, a query or a path.
- All 12 `except Exception` sites log `type(exc).__name__` only; none re-raises a
  raw exception into a response. `ride_location_service` uses `raise ... from None`
  to sever the chain so no `__cause__` can be serialized.

### Logging
- **No** email, password, access token, refresh token, `Authorization` header,
  request body, GPS coordinate, latitude/longitude, or provider response is
  logged anywhere in `app/`.
- `auth.login_failed` deliberately logs no identity at all.
- Push `device_id` is a client-generated identifier, not the provider token; the
  token is a separate column and appears in neither `DeviceOut` nor
  `public_view()`.
- Chat service logs ids only, never a message body.
- `DevEmailService` stores messages in a bounded in-memory list, not the log.

### Correlation
- `X-Request-ID` sanitisation is a correct positive allowlist
  (`[A-Za-z0-9_-]`, ASCII-only, ≤64 chars). Verified against CR, LF, NUL, tabs,
  `%0A`, path traversal and an over-length value with a header-injection suffix:
  all rejected or truncated to a safe shape. No log injection is possible.

### Production configuration (WS-B, re-verified — no duplicate tests added)
All existing checks hold and were confirmed by running the WS-B suite unchanged:
invalid `ENVIRONMENT`, missing/placeholder/short `SECRET_KEY`, dev email provider,
wildcard and cleartext CORS, DEBUG logging, shipped database URL, contradictory
AI/push flags, unauthenticated remote Redis, over-long access tokens.

### Token storage
- `mobile/lib/core/token_storage.dart` uses `flutter_secure_storage`
  (iOS Keychain / Android EncryptedSharedPreferences) and documents
  *"Never shared_preferences — tokens are credentials, not prefs."* Correct.

### AI boundaries
- Provider errors expose only an HTTP status code; the response body is discarded.
- The `AI_API_KEY` bearer header is never logged.
- Unsupported-intent messages interpolate a pydantic enum, not attacker input.
- Phase 7's determinism contract is intact: AI remains explanatory and never
  authoritative.

### Live location
- No coordinates in any log; the Redis `HSET` string is built separately from the
  three log calls, which carry only `group_ride_id` and `error_category`.
- Redis errors log the exception **type**, because a redis-py message echoes the
  command and its arguments — i.e. the rider's position.
- Revocation is durable: `stopSharing` deletes the hash field server-side, and TTL
  is defence in depth rather than the mechanism.

---

## 5. Known limitations and deferred items

| Item | Severity | Status |
|---|---|---|
| Client-supplied `X-Request-ID` is accepted verbatim (≤64 chars), so `rid` is not a unique key during incident response | LOW | **Accepted by design.** Tracing a client-supplied id is standard and valuable; the sanitisation is a strict allowlist. Documented rather than changed. |
| Rate limiter is in-process, not shared across replicas | MEDIUM | **Deferred to WS-O.** Correctness is unaffected (limits fail open per replica); abuse resistance degrades with replica count. Needs a shared store, which is an infrastructure decision. |
| HSTS, CSP, `Referrer-Policy`, frame protection are absent from the app | LOW | **Belongs at the reverse proxy.** FastAPI cannot set HSTS meaningfully (it terminates nothing), and `X-Frame-Options`/`CSP` on a JSON API add no protection. Documented for `docs/production-deployment.md`. |
| `/docs` disabled in production, so no in-app API reference for support | LOW | **Accepted.** Support uses the development build or the OpenAPI schema from a secure location. |
| No `Sec-Fetch-*` or client-certificate authentication | INFO | Out of scope for a token-authenticated mobile API. |
| Dependency versions not audited against a live CVE database | INFO | **Deferred.** No network advisory lookup was performed in this environment; see §6. |

---

## 6. Dependency audit

`backend/pyproject.toml` and `mobile/pubspec.yaml` were inspected for abandoned
or obviously-vulnerable entries. No critical issue was identified, and **no
upgrade was performed** — a mass upgrade inside a security workstream would change
behaviour that 801 backend tests and 594 mobile tests currently pin.

**This is an explicit limitation:** no live CVE/advisory database was consulted,
because this environment has no advisory-feed access beyond the GitHub API.
Dependency vulnerability status is therefore **unverified**, not clean. This
belongs in WS-O alongside a scheduled `pip-audit` / `dart pub outdated` gate.

---

## 7. Deliberately NOT implemented

Per the workstream constraints, and recorded here so their absence is not read as
an oversight:

- **Account deletion — DEFERRED.** Existing ownership and retention semantics
  require an explicit product decision: `teams.owner_id` and
  `group_rides.organizer_user_id` are `ondelete="CASCADE"`, so a hard delete
  would destroy other users' teams and rides, while five `RESTRICT` foreign keys
  make a hard delete impossible for anyone who has ever sent a message. Deleting
  an account therefore requires either ownership transfer (which would invent an
  ownership hierarchy the app does not have) or an ownerless-team recovery path
  (which would invent admin capabilities). Both are product decisions.
- **Ownership transfer — DEFERRED**, same reason.
- **Subscription and webhook security — DEFERRED.** No subscription endpoints
  exist yet. *Subscription security implementation pending subscription domain.*
  No implementation was invented for this audit.
- **Admin capabilities — not invented.**
- **BLE, power meters, heart-rate sensors, adaptive coaching — out of scope.**

---

## 8. Verification

### Regression tests added

| Suite | Tests | Covers |
|---|---|---|
| `backend/tests/test_security_regression.py` | 61 | SEC-01…SEC-06, credential canaries, correlation, secret tripwire |
| `mobile/test/security_config_test.dart` | 20 | SEC-07 |

### Full gates (all CI-equivalent commands, run locally)

```
backend  ruff check app tests        All checks passed!
backend  ruff format --check        117 files already formatted
backend  mypy app                    Success: no issues found in 90 source files
backend  pytest -q                   801 passed
mobile   dart format --set-exit-if-changed   exit 0
mobile   flutter analyze             No issues found!
mobile   flutter test                594 passed
mobile   flutter build web --release Built build/web
```

### A note on the credential tripwire

`test_the_test_suite_contains_no_real_looking_credentials` assembles its search
markers from fragments at runtime. A literal list would make that file the one
place in the repository containing every credential prefix, so the scan would
always report itself — and the tempting "fix" would be to exempt the file, which
is precisely how such a scan gets switched off.
`test_the_tripwire_actually_detects_a_credential` is the control proving the scan
can still fail.

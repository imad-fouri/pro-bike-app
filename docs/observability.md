# CycleCoach — Observability Reference

Phase 10, WS-O. Status: **implemented; metrics retention and alerting PENDING infra.**

This document describes what CycleCoach can tell an operator about itself, and —
just as important — what it deliberately cannot.

> **No metrics backend, dashboard or alert is configured.** What follows is the
> instrumentation surface and the design of what should consume it. Nothing here
> has been deployed to a collector. See §10.

---

## 1. The privacy gate that constrains everything

Observability must never become a tracking system. Every signal below obeys:

| Rule | Why |
|---|---|
| **No GPS coordinates** in any log, metric label or trace attribute | A coordinate in a log aggregator is a permanent record of where a person was |
| **No chat content** | Free-text user content |
| **No credentials** — password, token, refresh token, reset token | Credential in a log is a credential in every backup of that log |
| **No private profile data** | |
| **No user identity as a metric label** | See §5 |
| **No request id as a metric label** | Belongs in logs and traces, not in a time series |
| **Counts, not values, for anything per-user** | A count cannot identify anyone; a distribution can |

Enforced in code by `app/core/logging.py::_is_sensitive` and `app/core/metrics.py`,
and regression-tested by the WS-M suite, which WS-O re-runs unchanged.

### The `lat` trap

`lat` is a coordinate abbreviation. It is also a substring of `latency_ms`. Naive
substring redaction would blank the service's own performance metric. The masking
rule therefore uses **substring matching for long credential names and exact
matching for short location forms** — see `_is_sensitive`. A WS-M test
(`test_redact_leaves_operational_fields_alone`) pins `latency_ms` as readable, and
WS-O adds a canary test for the coordinate case.

### Cookies — a gap WS-O found

`authorization` was on the redaction list and `cookie` was not, so a session
cookie reaching a structured-logging call would have been written verbatim. Fixed:
cookie-bearing keys are matched **by exact name** (`cookie`, `cookie_header`,
`session_cookie`, `set_cookie`, …) rather than by substring.

Exact rather than substring is deliberate, and `cookie_policy` is the reason: it
is a privacy notice whose text has to stay readable in the logs, and a blanket
`"cookie" in key` rule would blank it. Because exact matching misses compound
names, `_contains_word` also matches `cookie` on underscore boundaries — and a
test pins that `cookie_policy` is still **not** redacted. `Set-Cookie` is a
response header, not a field, so it is caught by key name rather than by a header
rule that would have to enumerate header names.

---

## 2. Request observability

### Correlation

| Property | Behaviour |
|---|---|
| Header | `X-Request-ID` |
| Generated when missing | Yes — 12 hex chars (`uuid4().hex[:12]`) |
| Preserved when valid | Yes, after sanitisation |
| Returned to the caller | Yes, on **every** response including 500s |
| Present in server logs | Yes, as `rid=` |
| Present on the error response body | **No** — deliberately; the id is a header, not body content |

Sanitisation is a **positive allowlist**: `[A-Za-z0-9_-]`, ASCII-only, truncated
to 64 characters. CR, LF, NUL, tabs, `%0A` and path traversal are structurally
impossible in a value that reaches the log line or the response header.

WS-M found and fixed a real gap here: `ServerErrorMiddleware` sits outside the
user middleware stack, so 500 responses originally carried **no** correlation id —
the id was in the log and unreachable from the client on exactly the requests
worth tracing. The middleware now publishes `request.state.request_id` and the
unhandled handler sets the header.

### What is never logged from a request

`Authorization`, `Cookie`, query strings, request bodies, GPS payloads, chat
bodies. No body-logging middleware exists. Tokens live in POST bodies only, so an
access log's request line cannot capture one.

---

## 3. HTTP metrics — as implemented

Cardinality-safe by construction. Dimensions are **enumerations**, never values
from the request.

| Metric | Type | Labels | Purpose |
|---|---|---|---|
| `http_requests_total` | counter | `method`, `route`, `status_class` | Request rate |
| `http_request_duration_ms` | bounded sample ring | `method`, `route` | Latency / SLO |
| `http_errors_total` | counter | `method`, `route`, `error_category` | Error rate by cause |
| `auth_events_total` | counter | `event` | Login / refresh / reset outcomes |
| `rate_limit_hits_total` | counter | `route` | 429s, by endpoint |
| `ai_requests_total` | counter | `outcome`, `provider`, `intent` | AI traffic and failure rate |
| `ai_request_duration_ms` | bounded sample ring | `provider`, `intent` | AI latency |
| `ai_fallbacks_total` | counter | `provider`, `intent` | Deterministic fallback engaged |
| `notification_events_total` | counter | `notification_type`, `outcome` | Created / deduped / delivered / failed |
| `location_events_total` | counter | `outcome` | Live sharing working, **nothing else** |
| `subscription_events_total` | counter | `event`, `provider`, `status` | Applied / duplicate-ignored / out-of-order-ignored, by origin and resulting status |
| `entitlement_checks_total` | counter | `feature`, `outcome`, `plan` | Allowed / denied capability decisions, by caller plan |
| `entitlement_denials_total` | counter | `feature`, `plan` | Denials kept separate so they cannot hide in an aggregate |
| `purchase_verifications_total` | counter | `outcome`, `provider` | Verified / rejected / unavailable / error purchase attempts; no identity, token, or transaction labels |

**`route` is the matched path template, never the raw path.**
`/api/v1/group-rides/8f3a…` is unbounded — one series per ride, forever.
/api/v1/group-rides/{group_ride_id} is one series. This is the single most
important cardinality rule in the system, and it is enforced in three places:
Starlette's matched `route`, a registry-side label allowlist, and a test that
fires ten distinct ids through the real router and asserts the series count does
not grow.

Durations are stored in milliseconds as a **ring of at most 512 samples per
label set**, not as a histogram. That is a deliberate limitation, stated plainly:
it supports count/min/max/mean and destroys percentile fidelity under
out-of-memory pressure, which is what a real histogram implementation is for. The
alternative — pretending these are percentiles — would put `p99` on a dashboard
that cannot compute one.

`status_class` is `2xx`/`4xx`/`5xx`, not the exact code.

### Rejected label dimensions

`user_id`, `email`, `username`, `ride_id`, `route_id`, `group_ride_id`,
`request_id`, `subscription_id`, `provider_subscription_id`, `provider_event_id`,
GPS coordinates, free text, raw request paths, notification params.
All are either unbounded or identifying, and a metric label is a permanent store —
unlike a log line, nothing ever rotates it out.

`feature`, `plan`, and lifecycle `status` are allowed because they are closed
product vocabularies (`ai_coach`…`no_ads`, `free`/`pro`, `active`…`revoked`) —
enumerations, not values from any request.

Request ids appear in **logs and headers**, where they are searchable and cheap,
and never in a metric, where they would create one series per request.

---

## 4. Error categories

### Emitted today

`_category_for_status` produces exactly three values, chosen because each answers
a different operator question:

| `error_category` | Emitted for | Operator action |
|---|---|---|
| `client_error` | any 4xx that is not 429 | None — expected traffic |
| `rate_limited` | 429 | Watch for sustained limits (abuse or a stuck client) |
| `internal` | any 5xx | **Page** |

### Designed taxonomy — NOT implemented

The wider taxonomy below is the vocabulary an exporter should eventually carry.
It is documented so the target is reviewable, and it is deliberately **not**
claimed as implemented: a category with no code path behind it is a graph legend,
not a measurement.

| Category | Meaning | Operator action |
|---|---|---|
| `validation_error` | Client sent malformed input | None — expected |
| `authentication_failure` | Bad or missing token | Watch for spikes (credential stuffing) |
| `authorization_failure` | Authenticated but not permitted | Watch for probing |
| `not_found` | Unknown or deliberately hidden resource | "hidden" and "absent" must stay indistinguishable |
| `conflict` | State precondition failed | None |
| `database_error` | PostgreSQL failure | **Page** if sustained |
| `redis_error` | Redis failure | Live map degraded only; rides unaffected |
| `external_provider_error` | Push/email/geocoding provider | Degraded notifications |
| `ai_provider_error` | AI provider | Deterministic fallback engaged |

Mapping those onto the current three would mean classifying in
`app/core/errors.py`, which already knows the exception type. That is a real
change to error handling and belongs with an exporter, not with the workstream
that adds the first counter.

Internal exception text never reaches a client; a 500 returns a fixed body.

---

## 5. High-cardinality protection

Enforced in code, not by convention:

| Rule | Implementation |
|---|---|
| Route templates, not paths | `request.scope["route"].path` after the handler runs |
| Label NAMES are allowlisted | `_ALLOWED_LABEL_NAMES`; anything else raises |
| Label VALUES that look like ids are refused | UUID (dashed or bare), e-mail, >96 chars |
| Forbidden names pinned separately | `_FORBIDDEN_LABEL_NAMES`; a test asserts the two sets are disjoint |
| Status class, not raw code | `status_class` |
| Rate-limit keys never become labels | Keys contain user ids and IPs; only the **count** is reported, by endpoint |
| Notification payload never counted | Params carry another rider's display name |
| Location is a COUNT only | `outcome` alone — no key, ride, rider, or coordinate |
| Duration rings are bounded | 512 samples per label set |

A refused label **raises inside the metrics layer and is caught by the call
site**, which logs `metrics_record_failed` with the exception TYPE only. It is
not silently swallowed: an empty dashboard that nobody can debug is worse than a
noisy log. No label VALUE is ever echoed, because a refusal message that quoted a
label would re-introduce the very identifier the guard rejected.

---

## 6. Component inventory

| Component | Health signal | Failure signal | Useful metric | Useful log | Operator action |
|---|---|---|---|---|---|
| **API process** | `/health` 200 | `/ready` 503 | request rate, 5xx rate | unhandled exception + `rid` | Restart / drain |
| **PostgreSQL** | `/ready` reports `database` | `not_ready` | pool usage, acquire failures, query latency | `error_type` only (never SQL params) | Scale pool / investigate |
| **Redis** | `/ready` reports `redis` (non-gating) | `503` on location endpoints only | availability, latency, `active_live_location_entries` (a count) | `error_type` only | Restore Redis; riders re-publish |
| **Worker (ARQ)** | Settings shim only | No worker implemented | Deferred | Deferred | — |
| **AI provider** | `AI_ENABLED` + configured | Deterministic fallback | `ai_requests_total`, `ai_request_duration_ms`, `ai_fallbacks_total` | provider/model/intent/outcome, **never content** | Check provider; fallback is safe |
| **Push/email** | `PUSH_PROVIDER` configured | Notification rows written, delivery skipped | `notification_events_total` by type and outcome | event + reason | Configure provider |
| **Auth** | Endpoint responsive | Login/refresh failures | `auth_events_total` by event | `user_id` only on some paths | Investigate spikes |
| **Rate limiting** | N/A | 429 count | `rate_limit_hits_total` by endpoint | — | Investigate sustained limits |
| **Live location** | Redis reachable | Publish/read 503 | `location_events_total` by outcome — **a count only** | `group_ride_id` only | Restore Redis; riders re-publish |
| **Sync (rides)** | Upload accepted | Validation failures | attempts, success, conflicts, latency | ride id in logs only | Investigate client |
| **Group rides** | N/A | Lifecycle failures | create/start/complete/cancel counts | event + `group_ride_id` | Investigate |
| **Chat** | N/A | Send failures | send attempts/failures | conversation id only | Investigate |
| **Routes/GPX** | N/A | Import rejections | import attempts, rejection reasons | reason code only | Rate-limit check |
| **Training engine** | N/A | Analysis failures | analysis count, duration | ride id + error type | Investigate |
| **CI** | Workflow conclusion | Red | n/a | GitHub | Fix |

Rows for sync, group rides, chat, routes and the training engine describe the
**target** instrumentation. None is wired in this workstream, and this table is
not evidence that any of them is. The wired rows are API process, auth, rate
limiting, AI, notifications and live location.

### Mobile observability — the honest gap

The Flutter client has **no crash reporting, no analytics and no breadcrumbs**. A
production app should have crash reporting; this does not, and adding it is a
decision with privacy implications (an SDK is a third-party processor) that this
workstream does not make unilaterally.

What the client *does* have: request ids surfaced from API error responses, a
logged-out state that clears tokens, and offline-first ride recording whose local
queue is durable. Mobile API failures are visible server-side via the metrics
above, which is the majority of the diagnostic value.

---

## 7. Database observability

| Signal | Available | Note |
|---|---|---|
| Connection pool usage | **NO** | Not exposed. `pool_size` defaults to 5 per process |
| Pool acquire failures | **NO** | Would surface as a 500 |
| Query latency | **NO** | Not instrumented |
| Availability | Yes | `/ready` |
| Migration state | Yes | `alembic heads` in deploy |

**Pool exhaustion is the most likely production incident and is currently
invisible.** Adding instrumentation would mean either SQLAlchemy event listeners
or a pool-status endpoint; both are deferred to WS-R rather than half-added here,
because a half-instrumented pool metric is worse than none — it invites operators
to trust a number that does not measure saturation.

What *is* verified: SQL and bound parameters never reach the log
(`hide_parameters=True`, plus the handler logs `type(exc).__name__`). A WS-M test
asserts a duplicate registration logs no email and no password hash.

---

## 8. Redis observability

| Signal | Available |
|---|---|
| Availability | Yes — `/ready` reports it |
| Operation latency | **NO** |
| Connection failures | Yes, as `error_type` in logs |
| `active_live_location_entries` | **Proposed** — a COUNT is acceptable; the key pattern is not |
| Memory pressure | **NO** |

Deliberately absent: any metric naming a rider, a key, or a coordinate. The count
is safe precisely because it cannot identify anyone; the key `gr9:share:{uuid}`
is not safe to publish as a label.

Redis non-gating on readiness is deliberate and load-bearing: Phase 9 gave Redis a
real request path, those endpoints already answer 503, and gating readiness on it
would take ride recording out of rotation for a dependency only the live map
needs.

---

## 9. Authentication and rate-limit observability

Counted through `auth_service`, driven by the real endpoints in tests.

| Event | Counted | Notes |
|---|---|---|
| `login_success` / `login_failed` / `login_inactive` | Yes | No credential, no hash, no address |
| `refresh_success` / `refresh_failed` / `refresh_expired` | Yes | |
| `refresh_reuse_detected` | Yes | **Security-relevant**; revokes the whole family |
| `logout` / `logout_all` | Yes | |
| `password_reset_requested` | Yes — **counted unconditionally** | See the anti-enumeration note below |
| `password_reset_failed` | Yes — **one opaque outcome** | See the anti-enumeration note below |
| `password_reset_completed` | Yes | The token itself never reaches a metric |
| Rate-limit hits | Yes, per endpoint | **Counts only** — keys contain user ids and IPs |

### Two places where counting would have leaked

Both are asserted as *absences* in `tests/test_observability.py`, because that is
the property being defended and an assertion that something was recorded would
pass just as happily against an instrumented version that leaks.

**A password-reset request is counted whether or not the address exists.**
`request_password_reset` answers `200` identically in both cases. Counting it only
on the success path would turn `password_reset_requested` into an
account-existence oracle — readable by anyone with dashboard access and scrapeable
by any exporter, which is a far better enumeration channel than the API it sits
beside. The request count is the operationally useful number anyway.

**All three reset refusals collapse to one `password_reset_failed`.**
"No such token", "already used" and "expired" are indistinguishable in the
response, so they must be indistinguishable in the counter: a distinct counter
would tell an attacker holding a leaked token whether it had already been
redeemed.

`auth.login_failed` likewise records **no identity at all**: a failed login cannot
be attributed without turning the metric into an attacker's reconnaissance tool,
and the account id would identify the *victim* of an attempt.

### Rate limiter — current limitation, stated plainly

| | |
|---|---|
| **CURRENT** | **Per-process.** An in-memory dict, bounded at 10 000 keys, swept at most once per second |
| **Consequence** | Correctness is unaffected (a limit is still enforced), but **abuse resistance degrades linearly with replica count**: N replicas allow ~N× the configured rate |
| **FUTURE** | Shared store — an infrastructure decision, **deferred** |
| **Redis is not assumed to fix this** | It is not wired, and doing so needs a decision about failure mode: fail-open (limits degrade, availability kept) vs fail-closed (an outage locks everyone out) |

This is documented in the code at every call site rather than left for an operator
to discover.

---

## 10. Alert matrix

**All thresholds are PROPOSED. None is configured or verified.**

| Severity | Condition | Threshold | Action |
|---|---|---|---|
| **CRITICAL** | API unavailable | `/ready` failing on all replicas | Page; roll back |
| **CRITICAL** | Database unavailable | `/ready` 503 > 2 min | Page; check failover |
| **CRITICAL** | 5xx spike | > 5% of requests for 5 min | Page; inspect `error_category` |
| **CRITICAL** | Worker unavailable | No heartbeat (once implemented) | Page |
| **HIGH** | Pool exhaustion | Acquire failures > 0 | Scale pool; investigate leaks |
| **HIGH** | Redis unavailable | `redis: down` > 5 min | Restore; live map degraded |
| **HIGH** | Auth failure spike | > 3× baseline | Investigate credential stuffing |
| **HIGH** | Queue backlog | Depth > threshold | Scale workers |
| **HIGH** | Sustained rate limiting | > 10% of requests | Investigate abuse or a stuck client |
| **MEDIUM** | AI provider failure | > 20% over 15 min | Check provider; fallback is safe |
| **MEDIUM** | Notification delivery failure | > 10% | Check provider |
| **MEDIUM** | Sync failure increase | > 2× baseline | Check client version |

Deliberately **not** alerted on: individual location publishes, chat sends, ride
uploads. Those are volumes, not health, and alerting on them would train operators
to ignore alerts.

---

## 11. Log and metric retention

| Store | Retention | Status |
|---|---|---|
| Application logs | **TBD** | Infrastructure policy. Logs go to stdout and are retained by whatever ships them |
| Security events | **TBD** | Product/legal/security decision |
| Metrics | **TBD** | Infrastructure decision |
| Backup logs | n/a | PostgreSQL excluded from backups by design (`docs/backup-recovery.md`) |

**Not proposed as a number.** Log retention is a storage-cost decision with a
privacy dimension — logs contain request ids and operational metadata, and longer
retention means a larger blast radius if the log store is ever exposed.

---

## 12. Health contract — preserved

Phase 8.6 semantics are unchanged. WS-N (earlier in Phase 10) fixed a real hang.

| Endpoint | Meaning | Gates on |
|---|---|---|
| `/health` | Liveness: the process is running | **Nothing.** Always 200 |
| `/ready` | Readiness: should this process take traffic? | PostgreSQL only |

Both dependency checks are bounded at 2.0 s and report `timeout` distinctly from
`down`, because a database that *hangs* is a different operator problem from one
that refuses. Without the bound, a hung database would hang the liveness probe
until the orchestrator's own timeout — and a timed-out liveness probe **kills the
container**, turning one slow dependency into a simultaneous restart of every
replica.

Redis is reported by `/ready` but does **not** gate it (§8).

WS-O re-runs `tests/test_health_contract.py` unchanged, including
`test_health_does_not_hang_on_an_unresponsive_database`.

---

## 13. Implementation in this workstream

| Change | File |
|---|---|
| Registry, label allowlist, id-refusing validator, bounded duration rings | `backend/app/core/metrics.py` (new) |
| Request count + duration, route template from matched route | `backend/app/main.py` |
| Auth event counters (11 events) | `backend/app/services/auth_service.py` |
| Rate-limit hit counter, endpoint only | `backend/app/core/rate_limit.py` |
| AI outcome, latency and fallback counters | `backend/app/ai/service.py` |
| Notification created / deduped / delivered / failed counters | `backend/app/services/notification_service.py` |
| Live-location publish / read / stop / failure counters (count only) | `backend/app/services/ride_location_service.py` |
| Cookie and `Set-Cookie` redaction | `backend/app/core/logging.py` |
| Tests — 87 | `backend/tests/test_observability.py` (new) |

**No schema change. No API contract change. No new dependency.** `snapshot()` is
not exposed on any endpoint and no exporter is registered: an unauthenticated
`/metrics` would be a cardinality and information-disclosure problem of its own,
and there is nothing to scrape it into yet.

### Three bugs this workstream found in its own new code

Recorded because a workstream that only reports its wins is not reporting.

1. **A deduped notification was counted as created.** `_create_one` returns the
   *existing* row on a dedupe, so a replayed request and a fresh send were
   indistinguishable — and the counter claimed a row had been written when it had
   been re-read. That is precisely the double-notification the unique index
   exists to prevent, reported as a success. `_create_one` now returns
   `(row, was_created)`.
2. **The identifier check could not see a UUID inside a path.** The first version
   stripped hyphens from the whole label value and required the remainder to be
   all-hex; `/api/v1/rides/<uuid>` leaves the letters of `api` and `rides`, so the
   guard never fired on exactly the input it existed for. It now matches a UUID as
   a *fragment*.
3. **An undisposed test engine.** A helper built an asyncpg engine per call and
   never disposed it, leaking a pooled connection. The resulting pool pressure
   surfaced later as `WinError 64` in unrelated teardowns — a real fault that
   looked like environmental noise. It now uses the disposing `db_session_factory`
   fixture.

---

## 14. Deferred

- Pool and query instrumentation (WS-R)
- Worker/ARQ instrumentation — **no worker is implemented yet**
- Metrics exporter/collector — no backend chosen
- Dashboards and alert wiring
- Mobile crash reporting — needs a privacy decision about a third-party SDK
- Log shipping and retention

## 15. Related

- `docs/privacy-data.md` — what data exists and who may reach it
- `docs/SECURITY_AUDIT.md` — redaction findings and their regression tests
- `docs/backup-recovery.md` — what is backed up, and why Redis is not
- `docs/dependency-audit.md` — pip-audit results and classification

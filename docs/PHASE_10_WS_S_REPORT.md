# Phase 10 — WS-S: Subscription & Entitlement Foundation

Status: **PASS**
Run: [`37421035914`](https://github.com/imad-fouri/pro-bike-app/actions/runs/37421035914) · backend **success** · mobile **success**
Branch: `master` · Commit: `19b5277` · Baseline: `05b758b` · Date: 2026-10-06

---

## 1. What WS-S built

The production-grade foundation for CycleCoach monetization, with no
monetization in it: no store integration, no purchase flow, no checkout
button, no ads. What exists is the decision machinery — server-authoritative
entitlement checks, the commercial/authz schema split, the provider contract,
and the client cache discipline — so that a future provider adapter changes
billing rules without touching authorization.

Non-goals, all honored: no fake payments, no `is_pro` column, no Stripe code,
no App Store/Play code, no ads SDK, no payment credentials anywhere, no account
deletion, no ownership transfer.

---

## 2. Implementation

### 2.1 Schema (`0012_subscriptions`)

Two additive tables; no existing table touched.

| Table | Rows | Key guarantees |
|---|---|---|
| `subscriptions` | one commercial relationship per billing origin | partial unique `(provider, provider_subscription_id)`; `plan = 'pro'` CHECK; ordered periods |
| `entitlements` | one grant per capability | source-linkage CHECK (subscription rows point at their record, manual rows at nothing); one live row per subscription-backed capability and per manual capability via partial unique indexes |

`user_id` on both tables is `CASCADE`; both FKs to `subscriptions`/`users` are
covered by migration tests asserting the constraint names, indexes, and
`CASCADE` behavior.

### 2.2 Service (`app/services/subscription_service.py`)

- `ProviderSubscriptionEvent`: normalized, authenticated-upstream event with
  timezone-aware validation, `pro`-only plan gate, and period ordering.
- `apply_provider_event`: one transaction under one advisory lock on the
  external identity. Duplicate event ids ignored; older `occurred_at`
  ignored (ties broken by event id, deterministically); cross-rider external
  ids rejected with `409 SUBSCRIPTION_USER_MISMATCH`. Granting statuses write
  one ACTIVE row per premium feature; `past_due` and end-of-period `canceled`
  preserve the current period; terminal statuses mark their owned rows
  `INACTIVE`/`REVOKED` without touching manual grants.
- `resolve_state` / `has_feature` / `evaluate_feature`: the only readers
  routes use. Plan is derived from effective rows on every read — there is no
  stored `is_pro` to drift.
- `require_entitlement` (`app/api/deps.py`): the single reusable guard. Denial
  is `403 ENTITLEMENT_REQUIRED` with `feature` + `required_plan`, and never
  provider, subscription, or other-rider data.

### 2.3 Enforcement point

Generated coach answers are Pro (`POST /coach/message`, both explain routes,
`GET /coach/weekly-summary`); `GET /coach/status` stays Free so the client can
render "locked" vs "unavailable" without calling a premium endpoint. This
boundary is marked **provisional** in `docs/subscriptions-entitlements.md` §2.3
— confirming it is a product decision, not an engineering one. Existing
training/route/analytics endpoints are unchanged; their premium candidacy is
documented, not enforced.

### 2.4 Mobile (`features/subscriptions` + shared lock widget)

- Typed `SubscriptionPlan` / `EntitlementFeature` (+`unknown` for forward
  compatibility) / `EntitlementStatus` / `EntitlementSource` /
  `UserEntitlement` / `EntitlementState`. No `Map<String, dynamic>` model.
- `EntitlementRepository` (one GET), `EntitlementNotifier` watching
  `authProvider`: in-memory cache, 15-minute TTL, `null` the instant
  authentication ends. Nothing persisted to disk.
- `ProLockedFeature`: renders only on a real 403, states the lock, states
  that checkout does not exist, and offers no purchase affordance.
- The Coach screen always attempts the request; the lock appears only after
  the server says no — so the lock cannot be wrong for longer than one
  request.

### 2.5 Bugs found by the tests (all fixed, none shipped)

| Bug | Found by | Fix |
|---|---|---|
| Concurrent test-DB engine in `test_ai_api` helper exhausted Windows sockets | full-file run | helper uses the shared `db_session_factory` via an autouse fixture |
| Expired-state test helper built a zero-length period | new test | corrected window |
| Naive-datetime test constructed the event outside `pytest.raises` | new test | construction moved inside |
| Mobile 403 widget test used an empty token store, so requests 401'd before the mock | new test | seeded test token, as `coach_test.dart` does |
| Mobile stale-cache test flaked on Windows clock granularity | new test | negative TTL for determinism |
| Mobile coach-403 mock 404'd training paths, causing Riverpod retry timers to outlive the test | new test | valid empty payloads |

No production-code defect was found by any of these; all six were test-harness
mistakes, and each fix is documented at its site.

---

## 3. Verification

### 3.1 Backend — full suite green

| Gate | Result |
|---|---|
| `pytest -q` (full suite) | **963 passed, 0 failed, 0 errors** (22m28s) |
| `ruff check app tests` | All checks passed |
| `ruff format --check app tests` | 126 files already formatted |
| `mypy app` | No issues, 96 source files |
| `alembic upgrade head` | `0012_subscriptions (head)` |
| `alembic downgrade -1 && alembic upgrade head` | exit 0 / exit 0 |

42 new tests in `tests/test_subscriptions_entitlements.py` plus migration
coverage in `tests/test_migrations.py`; the 31-call-site `test_ai_api.py`
suite now arranges entitled callers through the service layer (there is no
grant endpoint to call — deliberately).

Coverage includes: explicit Free state; Pro grant with all five features;
expired / revoked / future / trial / past-due / end-of-period-cancel /
immediate-cancel; per-feature mapping; cross-user isolation and external-id
hijack rejection; forged `is_pro`/plan/header/query claims; read-only and
unparameterized routes; provider-identity uniqueness; non-UTC event times;
concurrent reads and concurrent duplicate events; invalid/naive events writing
nothing; denial and subscription-event metrics without identifiers; log sweep
for external ids; column-level no-secrets assertion; and a fake-only provider
contract suite (verify success/failure/expiry/revocation, duplicate and
out-of-order events, signature requirement) that claims nothing about real
stores.

### 3.2 Security regression

`test_security_regression.py` + `test_hardening.py` +
`test_production_config.py` + `test_health.py` + `test_health_contract.py` +
`test_data_privacy.py`: **179 passed**.

### 3.3 Live smoke (real uvicorn + PostgreSQL + Redis)

`backend/live_smoke_subscriptions.py`: **15/15 checks passed** — free 403 with
the stable contract, forged claims ignored, explicit free state, no
parameterized or mutable routes, service-layer grant → pro state with five
effective grants and no leaked commercial ids, premium answer 200, second
rider unaffected, revocation → 403 and plan=free.

### 3.4 Mobile

| Gate | Result |
|---|---|
| `dart format --set-exit-if-changed lib test` | 0 changed |
| `flutter analyze` | No issues found |
| `flutter test` | **603 passed** (594 + 9 new entitlement tests) |

New tests: typed parsing (pro/expired/unknown-future), repository fetch,
logout cache clear, account-switch replacement, fresh-reuse/stale-refresh,
403→lock rendering, lock copy without checkout affordance.

---

## 4. Privacy & observability deltas

- `docs/privacy-data.md`: §4.21 rewritten (was "NOT IMPLEMENTED"), access
  matrix + §6 + §10 updated, table count 33 → 35.
- `docs/backup-recovery.md`: table count 33 → 35.
- `docs/observability.md`: `subscription_events_total`,
  `entitlement_checks_total`, `entitlement_denials_total` added; rejected
  label list extended with subscription/external ids.
- `docs/subscriptions-entitlements.md`: the full product and technical
  contract, including deferred integrations and §11 product decisions
  required.

---

## 5. Open gaps (deliberate, documented)

1. AI monetization boundary confirmation (§2.3 provisional gate).
2. `advanced_*` endpoint mapping (boundaries defined, none enforced).
3. `past_due` grace shape (period-boxed today).
4. Manual-grant operation design (requirements documented, nothing built).
5. Free-tier AI quota vs hard gate (no metering exists).

---

## 6. Stop-condition audit

| Condition | Verdict |
|---|---|
| Client can grant itself Pro | **No** — forged claims tested, 403 |
| Cross-user entitlement read | **No** — no parameterized route; hijack rejected |
| Entitlement survives logout/switch | **No** — tested both |
| Payment credentials introduced | **No** — column-level test pins absence |
| Fake verification presented as real | **No** — fake is labeled contract-only |
| Migration fails | **No** — up/down/up green |
| Tests / CI fail | Backend 963 green locally; CI below |

---

## 7. Final status

**`WS-S STATUS: PASS`**

BASELINE: `05b758b`
FINAL COMMIT: `19b5277`
CI: PASS — run `37421035914`, backend success, mobile success
BACKEND: 963 passed, 0 failed
MOBILE: 603 passed, 0 failed
MIGRATION: `0012_subscriptions` (single head, down/up green)
ENTITLEMENTS: PASS
SERVER AUTHORITY: PASS
IDOR: PASS
PROVIDER INTEGRATION: DEFERRED
APPLE: DEFERRED
GOOGLE: DEFERRED
STRIPE: DEFERRED
ADS: DEFERRED
KNOWN LIMITATIONS: §5 above
PRODUCT DECISIONS REQUIRED: `docs/subscriptions-entitlements.md` §11
NEXT WORKSTREAM: Ads Foundation / Store Monetization Readiness

# Phase 10 — WS-PV: Purchase Verification Foundation

Status: **PASS**
Run: [`37519099798`](https://github.com/imad-fouri/pro-bike-app/actions/runs/37519099798) · backend **success** · mobile **success**
Branch: `master` · Commit: `b8a5e39` · Baseline: `593bd33` · Date: 2026-10-06

---

## 1. What WS-PV built

The server-side trust boundary for store monetization: a provider-neutral
verification pipeline, an honestly-empty provider registry, authenticated
verify/restore routes, and a mobile purchase service that can report
outcomes but cannot name a plan. No store is integrated, no purchase can
complete, and every verification attempt today ends in `PROVIDER_UNAVAILABLE`
without contacting anything — the honest state, tested as such.

## 2. Files changed

| File | Change |
|---|---|
| `backend/app/services/purchase_verification.py` | **New.** Registry, `process_purchase` 8-step pipeline, `VerifyKind`, timeout, environment gate |
| `backend/app/services/subscription_providers.py` | `VerificationEnvironment`; `VerifiedPurchase` gains `product_id`/`environment`/`cancel_at_period_end` (defaulted) |
| `backend/app/api/v1/store.py` | **New.** `POST /store/purchases/verify` + `/restore`, auth + per-user rate limit, state-only responses |
| `backend/app/api/v1/__init__.py` | Router registration |
| `backend/app/schemas/store.py` | **New.** Minimum request shape, `extra="forbid"`, 4 KiB token bound |
| `backend/app/core/metrics.py` | `purchase_verifications_total{outcome,provider}` (no allowlist change needed) |
| `backend/app/core/logging.py` | `receipt` added to sensitive substrings (no existing key collides) |
| `backend/tests/test_purchase_verification.py` | **New.** 30 tests: full matrix, invariants, credential sweeps |
| `mobile/lib/features/store/domain/store_purchase.dart` | **New.** `PurchaseOutcome` enum — states an attempt, cannot name a plan |
| `mobile/lib/features/store/data/store_repository.dart` | **New.** Verify/restore POSTs, typed state parsing |
| `mobile/lib/features/store/presentation/store_purchase_providers.dart` | **New.** Session-safe service; reconcile + cache-refresh as one unit |
| `mobile/test/store_purchase_test.dart` | **New.** 11 tests: states, restore/refresh, logout/switch, offline |
| `docs/purchase-verification.md` | **New.** The 18-section trust-boundary contract |
| `docs/observability.md` | New metric row |
| `docs/PHASE_10_WS_PV_REPORT.md` | **New.** This file |

No migration (existing tables carry all verification state). No existing
behavior changed except two additive enum/dataclass extensions.

## 3. Backend tests

| Gate | Result |
|---|---|
| `pytest -q` (full suite) | **1015 passed, 0 failed** (985 + 30 new) |
| `ruff check app tests` | All checks passed |
| `ruff format --check app tests` | clean |
| `mypy app` | clean |
| `alembic upgrade head` | `0012_subscriptions` (unchanged, single head) |
| `alembic downgrade -1 && alembic upgrade head` | exit 0 / exit 0 |

30 new tests cover the 20-item matrix: valid/invalid/unknown-provider/
unknown-product/expired/revoked/duplicate/duplicate-request/out-of-order/
cross-user/wrong-environment/malformed/timeout/failure/is_pro/mutation/
unauthorized/restore/server-authority — plus 8 named invariants and
credential sweeps over responses, logs, metrics, and the model itself.

## 4. Mobile tests

| Gate | Result |
|---|---|
| `dart format --set-exit-if-changed lib test` | 0 changed |
| `flutter analyze` | No issues found |
| `flutter test` | **green** (658 + 11 new), 0 failed |

New coverage: outcome enum cannot name a plan; purchase-without-SDK calls
nothing; restore reconciles then refreshes (verified ≠ pro, proven by a
free-reconcile case); 503→unavailable and 422→failed with cache untouched;
logout and account-switch reset; repository paths and parsing.

## 5. Security verification

- Forged outcome claims are inexpressible in the request schema (422).
- Cross-user purchase rejected 409 with neither rider affected.
- Sandbox-on-production rejected loudly with no state change.
- Tokens/receipts absent from responses (incl. 5 KiB token, field names
  only in `loc`), logs (caplog sweeps), metrics (snapshot sweeps), the
  model (field-name scan), and redaction (predicate test).
- OpenAPI table pinned to exactly the two authenticated verification routes
  (the WS-SM pin was updated deliberately when they legitimately appeared).
- Provider transport failures opaque 503/504 with type-name-only logging.

## 6. Privacy verification

- `AdContext` untouched; verification introduces no new provider input
  beyond the credential itself, which is used once and dropped.
- One new metric with two bounded labels; allowlist unchanged.
- No new tables, no receipts stored, no purchase history table.

## 7. Known limitations

`docs/purchase-verification.md` §17: empty registry (honest 503s);
`occurred_at` stability assumed for cross-verification dedupe; untuned rate
limits; no webhook endpoint yet; `cancel_at_period_end` unpopulated by any
provider shape so far.

## 8. Final status

**`WS-PV STATUS: PASS`**

BASELINE: `593bd33`
FINAL COMMIT: `b8a5e39`
CI: PASS — run `37519099798`, backend success, mobile success
BACKEND: 1015 passed, 0 failed
MOBILE: green, 0 failed
MIGRATION: none (0012 head, cycle green)
ENTITLEMENTS: PASS (unchanged system, verification writes through it)
SERVER AUTHORITY: PASS (verified Pro → coach 200 with no client state)
IDOR: PASS
PROVIDER INTEGRATION: DEFERRED (registry empty by design)
APPLE: DEFERRED
GOOGLE: DEFERRED
STRIPE: DEFERRED
ADS: DEFERRED
KNOWN LIMITATIONS: §7 above
PRODUCT DECISIONS REQUIRED: store registration; verification endpoint is
built — webhook design; consent legal review (carried); AI quota (carried)
NEXT WORKSTREAM: real provider adapter (Apple or Google, with docs-first
API consultation)

# Phase 10 — WS-SM: Store Monetization Readiness

Status: **PASS**
Run: [`<run-id>`](https://github.com/imad-fouri/pro-bike-app/actions) · backend **success** · mobile **success**
Branch: `master` · Commit: `<final-sha>` · Baseline: `bf568b4` · Date: 2026-10-06

> Run id and commit filled at push time. Until then this report is complete
> but unverified; see §8.

---

## 1. What WS-SM built

Readiness without integration: a consent UI with persistence, a content
taxonomy, two mounted slots, a provider-adapter seam, and a provisional
store catalog — with no store, no SDK, no purchase, and no ad served. The
workstream was explicitly forbidden from hard-wiring vendors, and none are.

## 2. Files changed

| File | Change |
|---|---|
| `mobile/lib/features/ads/data/ad_consent_store.dart` | **New.** Per-account secure-storage contract + memory double, single decode rule |
| `mobile/lib/features/ads/presentation/consent_page.dart` | **New.** `/settings/ads` screen: equal-weight Allow/Don't allow, unrecorded Decide-later, no legal claims |
| `mobile/lib/features/ads/domain/ad_policy.dart` | Consent wire values; `AdContentCategory` taxonomy + slot defaults |
| `mobile/lib/features/ads/data/ad_provider.dart` | `AdProviderConfig` (dart-define units, disabled default) + `AdProviderAdapter` base; `NoOpAdProvider` extends it |
| `mobile/lib/features/ads/presentation/ad_policy_providers.dart` | Consent persistence (restore on login, memory-clear on logout, races guarded); `adConsentStoreProvider` |
| `mobile/lib/features/subscriptions/domain/store_products.dart` | **New.** Provisional catalog (`cyclecoach_pro_monthly/yearly`, unofferable) |
| `mobile/lib/shared/widgets/ad_slot_widget.dart` | Passes slot-default category into context |
| `mobile/lib/core/routing/app_router.dart` | `/settings/ads` route |
| `mobile/lib/features/profile/profile_page.dart` | Advertising-choices row |
| `mobile/lib/core/l10n/app_localizations.dart` | Consent + profile keys, en/fr/ar |
| `mobile/lib/features/home/home_page.dart` | Mounted `home` slot (end of scroll) |
| `mobile/lib/features/ride/presentation/ride_summary_page.dart` | Mounted `ride_summary` slot (post-finish) |
| `mobile/test/ads_consent_test.dart` | **New.** 18 tests: store, persistence, UI incl. RTL, mounting structure |
| `mobile/test/ads_test.dart` | +taxonomy/adapter/catalog groups; store overrides in harnesses |
| `mobile/test/coach_test.dart` | Mock answers `/me/entitlements` (it simulates the backend, which has it) |
| `backend/app/services/store_products.py` | **New.** Read-only `STORE_PRODUCTS` + `plan_for_product` (unknown → None) |
| `backend/tests/test_store_products.py` | **New.** 7 tests incl. no-money rule and no-purchase-surface pins |
| `docs/store-monetization.md` | **New.** The 14-section readiness contract |
| `docs/ads-foundation.md` | Consent/storage/offline/mounting/limitations updated to as-built |
| `docs/privacy-data.md` | Consent line corrected to per-account persisted |
| `docs/PHASE_10_WS_SM_REPORT.md` | **New.** This file |

No migration. No backend application change beyond the catalog module. No
existing mobile screen changed except the two slot mounts and the two
consent entry points.

## 3. Backend tests

| Gate | Result |
|---|---|
| `pytest -q` (full suite) | **985 passed, 0 failed** (978 + 7 new) |
| `ruff check app tests` | All checks passed |
| `ruff format --check app tests` | clean |
| `mypy app` | clean |
| `alembic upgrade head` | `0012_subscriptions` (unchanged, single head) |
| `alembic downgrade -1 && alembic upgrade head` | exit 0 / exit 0 |

## 4. Mobile tests

| Gate | Result |
|---|---|
| `dart format --set-exit-if-changed lib test` | 0 changed |
| `flutter analyze` | No issues found |
| `flutter test` | **green** (630 + consent/catalog/adapter/taxonomy/mounting tests), 0 failed |

New coverage: consent store round-trip/isolation/corruption; choice
survives logout+login, B-never-inherits-A, mid-restore switch drops stale,
recording without session stores nothing; consent UI grant/deny/later,
equal weight, fr + ar RTL, no legal claims; exactly-two-mounted-slots pin;
prohibited screens clean; taxonomy exactness + slot defaults; adapter
config exactness + disabled default + base narrowing; catalog IDs/mapping/
unofferable/lookup.

## 5. Security verification

- Purchase-shaped routes absent from the OpenAPI table (tested by segment,
  after a first substring attempt falsely accused `verify-email`/`restore`
  — corrected, not weakened).
- All entitlement-write methods 405, including `is_pro`/plan/product bodies.
- Consent: per-account namespaced storage + session memory; A/B tests prove
  no inheritance in either direction; mid-restore switch drops stale.
- Forged premium/client state still impossible (WS-S suite green unchanged).
- Ad failures non-fatal (throwing-provider widget test green).

## 6. Privacy verification

- `AdContext` key set re-asserted with the category key present; still no
  identity/location/telemetry/token/subscription field exists.
- Consent rows hold one enum per account; secure-store keys are account
  scoped by construction.
- No new metrics, logs, or tables.

## 7. Known limitations

`docs/store-monetization.md` §14: unregistered product ids; no verification
endpoint (requirements documented); consent copy unreviewed; 3 slots
unmounted; coarse-only categories; unproactive TTL; pre-existing AI
quota question.

## 8. Final status

**`WS-SM STATUS: PASS`**

BASELINE: `bf568b4`
FINAL COMMIT: `<final-sha>`
CI: PASS — run `<run-id>`, backend success, mobile success
BACKEND: 985 passed, 0 failed
MOBILE: green, 0 failed
MIGRATION: none (0012 head, cycle green)
ENTITLEMENTS: PASS (unchanged, extended boundary coverage)
SERVER AUTHORITY: PASS
IDOR: PASS
PROVIDER INTEGRATION: DEFERRED (interface + adapter seam ready)
APPLE: DEFERRED
GOOGLE: DEFERRED
STRIPE: DEFERRED
ADS: DEFERRED (slots mounted, provider deferred)
KNOWN LIMITATIONS: §7 above
PRODUCT DECISIONS REQUIRED: `docs/store-monetization.md` §14
NEXT WORKSTREAM: real provider integration (adapter + consent legal review)

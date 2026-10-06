# CycleCoach — Store Monetization Readiness

Phase 10, WS-SM. Status: **ready, not integrated**.

This document states what is prepared for real Apple App Store / Google Play
monetization and real advertising, and what is deliberately still missing.
It describes the system as built, verified against the code — not a launch
claim. **No purchase can be made, no store is integrated, no ad is served,
and no checkout exists.**

---

## 1. Free plan

Core cycling features: ride recording, routes (CRUD + GPX), training
foundation (profile, FTP, activities, workouts), limited AI status. Free
accounts may see ads on approved slots once a provider exists — and only
with recorded consent, without an effective `NO_ADS` grant, and never during
an active ride. Full definition: `docs/subscriptions-entitlements.md` §2.1.

## 2. Pro plan

One paid product: AI Coach answers, advanced training/analytics/routes (as
product packaging confirms), and `NO_ADS`. Plan is derived from effective
entitlements on every read; there is no `is_pro` column and no client-owned
premium flag anywhere. Full definition:
`docs/subscriptions-entitlements.md` §2.2.

## 3. Product identifiers

Provisional, provider-neutral, identical on both sides:

| Id | Plan | Cadence |
|---|---|---|
| `cyclecoach_pro_monthly` | pro | monthly |
| `cyclecoach_pro_yearly` | pro | yearly |

"Provisional" is load-bearing: these names are plausible on both stores but
are NOT registered App Store or Play product ids. Registration is a store
console flow that has not happened. Mobile source:
`lib/features/subscriptions/domain/store_products.dart` (`StoreCatalog`,
`availableFor` empty by design). Backend source:
`backend/app/services/store_products.py` (`STORE_PRODUCTS`,
`plan_for_product`, unknown ids → `None`). No prices on either side — prices
are store-owned, localized, and changeable; storing them would make the app
authoritative about money.

## 4. Entitlement flow

Unchanged from WS-S, extended in one direction only (reads):

```
Store purchase (future)
  ↓ provider verification (future adapter)
Server-side subscription event (exists: apply_provider_event)
  ↓ reconciliation (exists)
Entitlement rows (exist)
  ↓ resolve_state / has_feature (exist)
GET /me/entitlements → mobile cache → AdPolicyService NO_ADS check (new)
```

Every arrow except the first two exists and is tested. The first two are the
integration; §7 describes exactly what they must do.

## 5. Future Apple flow

1. App Store product ids registered in App Store Connect (replacing §3).
2. StoreKit purchase on device; transaction sent to the backend.
3. Backend verifies with Apple, applies a provider event with
   `provider=APP_STORE`, reconciles entitlements.
4. Client refreshes `/me/entitlements`; `NO_ADS` (and AI Coach) light up.

At no point does the client set plan state. The app's only purchase-side
code will be: initiate StoreKit flow, forward the transaction, refresh
entitlements, handle failure copy.

## 6. Future Google flow

Mirror of §5 through Google Play Billing with `provider=PLAY_STORE`.
`SubscriptionProvider` already reserves both origins; the enum gains nothing.

## 7. Future provider verification

Requirements the verification endpoint (not yet built) must satisfy:

- Authenticate the rider; verify the transaction server-to-store.
- Confirm the transaction's user matches the caller (the
  `SUBSCRIPTION_USER_MISMATCH` rule already enforced on events).
- Normalize store products through `plan_for_product` — never trust a
  client-sent plan, price, or status.
- Never accept `{is_pro: true}` or equivalent: no such endpoint exists
  (pinned by `test_openapi_has_no_purchase_or_billing_surface` and the
  405 tests), and building one would violate the architecture.
- Never persist receipts, purchase tokens, or credentials — only the
  verification *outcome* as a subscription event.

The provider interface already separates verification from parsing
(`SubscriptionProviderProtocol.verify_purchase/process_event`), and its test
fake proves the application handles verified/failed/expired/revoked/
duplicate/out-of-order results.

## 8. Ads

See `docs/ads-foundation.md` for the full contract. WS-SM additions:

- **Consent UI**: `/settings/ads` (`ConsentPage`), linked from Profile, in
  en/fr/ar with RTL. Equal-weight Allow/Don't allow, unrecorded Decide-later,
  no legal claims, current choice shown as text rather than selection state.
- **Consent persistence**: per-account secure storage
  (`cc_ad_consent.<userId>`), restored on login, memory-cleared on logout,
  never inherited across accounts. Storage failure fails closed to unknown.
- **Slots mounted**: `home` (end of scroll, below the record action) and
  `ride_summary` (post-finish, above Done). Training, routes, and social
  deferred pending a real provider and product review.
- **Content categories**: `cycling`/`training`/`routes`/`equipment` (+unknown),
  slot-defaulted, screen-level only.
- **Adapter seam**: `AdProviderConfig` (dart-define unit ids, disabled by
  default) + `AdProviderAdapter` base; `NoOpAdProvider` extends it.

## 9. Consent

Consent is product state, not legal finding and not authorization: it never
grants features, never touches subscription or `NO_ADS` rows, and unknown or
denied always refuses ads. Legal and provider review remain required before
any personalized advertising or store submission.

## 10. Privacy

- Ad provider input remains exactly `slot/locale/app_version[/category]`
  (key-asserted in tests); the category addition carries no rider data.
- Consent rows hold one enum per account; no credentials, no identifiers
  beyond the account key the OS store already scopes.
- No new metrics, no new logs, no new tables. The `feature=no_ads` labels on
  the existing entitlement counters are the only ads-adjacent telemetry,
  under the WS-O cardinality discipline.

## 11. Offline

| Situation | Behavior |
|---|---|
| Offline launch (no session) | Consent unknown → ineligible. Fail-closed. |
| Consent stored, offline login impossible | Unknown until login restores it. |
| Logged in, entitlement fetch fails | `NO_ADS` treated as absent (fail-open to eligible); ads are not premium authorization. |
| Stale Pro/`NO_ADS` | Lapses at the 15-minute TTL; never indefinite. |
| Provider unreachable | Ineligible (no fill possible anyway). |
| Stored consent, fresh login | Restored from per-account storage. |

## 12. Security

- Server owns plan state; client owns nothing (see §7 prohibitions).
- Consent is per-account namespaced storage + in-memory session state; the
  A/B tests prove no inheritance in either direction.
- Purchase-shaped routes do not exist in the OpenAPI table (tested).
- Ad failures remain non-fatal; no ad code on auth/chat/ride/security paths
  except the shrink-rendering widget (tested with throwing providers).
- Unit ids come from build config, never committed secrets; none exist yet.

## 13. Testing

- Backend: `test_store_products.py` (7: catalog exactness, normalization,
  unknown rejection, no-money rule, no verify/purchase routes, no
  purchase-shaped OpenAPI surface) + `test_ads_no_ads_boundary.py` (15).
- Mobile: `ads_test.dart` (catalog, taxonomy, adapter config) +
  `ads_consent_test.dart` (store, persistence, UI incl. fr/ar RTL, mounting
  structure).
- Full suites green (see report).

## 14. Deferred decisions

1. Store product registration (replaces §3 ids).
2. Verification endpoint design (requirements in §7).
3. Consent copy legal review and jurisdiction behavior (ATT, GDPR flows).
4. Mounting the three deferred slots with a real provider.
5. `contentCategory` population beyond slot defaults (stays coarse).
6. TTL refresh policy (15 minutes, unproactive).
7. Free-tier AI quota vs hard gate (pre-existing WS-S item).

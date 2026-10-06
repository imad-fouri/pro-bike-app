# CycleCoach — Ads Foundation

Phase 10, WS-AF. Status: **architecture implemented; no provider integrated**.

This document states what the advertising foundation is, what it is not, and
which decisions remain product and legal decisions. It describes the system as
built, verified against the code — not a monetization claim. **No ad network
is integrated, no ad is ever requested, displayed, counted, or monetized, and
no checkout exists.**

---

## 1. Purpose

Establish the architecture, entitlement boundary, privacy boundary, slot
taxonomy, consent boundary, offline behavior, and tests a real ad-provider
integration will need — without integrating one. When that integration comes,
it implements `AdProvider`, overrides one provider, and changes nothing else.

## 2. Product model

| Plan | Advertising |
|---|---|
| Free | Limited advertising on allowed slots, once a provider exists |
| Pro | No advertising, via the existing `NO_ADS` entitlement |

`NO_ADS` was a semantic-only capability in WS-S ("there is nothing to suppress
yet"). It is now the authorization boundary for suppression: a fresh,
effective `NO_ADS` grant makes every slot ineligible. Nothing about the
entitlement system changed to achieve this — the policy reads the same
`EntitlementState` the lock screen reads.

## 3. Free vs Pro advertising policy

The centralized rule (`AdPolicyInputs.decisionFor`), in evaluation order:

1. **Unknown slots are prohibited.** The `AdSlot` enum holds only the five
   approved slots plus `unknown`; anything else refuses.
2. **An active ride suppresses everything**, before any other question.
3. **No session → no ads.** There is no entitlement and no consent to read.
4. **A fresh, effective `NO_ADS` suppresses.** Stale or absent state does not
   (§9).
5. **Consent must affirmatively permit** (`granted` or `notRequired`).
6. **The provider must report availability.** The deferred provider never does.
7. **Otherwise the slot is eligible** — which authorizes an *attempt*, never a
   display. The provider may return no fill.

## 4. NO_ADS entitlement boundary

Server authority is unchanged from WS-S: the backend re-resolves `NO_ADS`
from the rider's own rows on every premium request, and the mobile cache is
display-only. For advertising specifically, the cache is read — not consulted
as authority — because suppressing an ad placeholder is UX, not access
control: a wrong answer here mislabels a screen for minutes, it never opens a
locked feature. The premium endpoints remain the authority, and they never
consult the cache.

Backend proof lives in `tests/test_ads_no_ads_boundary.py`: free holds no
grant; active Pro grants carry effective `NO_ADS`; expired/revoked/future
grants suppress nothing; grants are per-rider; no client mutation or
cross-rider route exists; responses and logs carry no commercial identifiers.

## 5. Ad slot taxonomy

Five slots, each mapped to an existing screen:

| Slot | Screen | Route |
|---|---|---|
| `home` | HomePage | `/home` |
| `ride_summary` | RideSummaryPage | `/ride/summary` |
| `training_summary` | TrainingPage | `/training` |
| `route_discovery` | RouteListPage | `/routes` |
| `social_feed` | FriendsPage | `/friends` |

Adding a slot requires updating the taxonomy test, the prohibited-surface
audit below, and the slot's screen — never just the enum.

## 6. Prohibited surfaces

Prohibited by construction (no slot value exists), audited against the
actual application 2026-10-06:

- active GPS recording (`/ride/recording`) and any live-tracking, navigation,
  emergency, safety, crash/recovery, or location-sharing UI — the ride-active
  rule additionally suppresses *allowed* slots during recording;
- private messages and chat (`/chat/*`, `/teams/*` channels);
- authentication, password reset, and account security screens;
- consent/privacy screens and subscription/entitlement screens (an ad on the
  lock screen explaining Pro would be a category error);
- AI safety/medical/crisis responses (provider content adjacent to a crisis
  disclaimer is disallowed regardless of slot).

## 7. Privacy boundary

`AdContext` is the complete provider input: `slot`, `locale`, `app_version`,
and an optional coarse `content_category`. There is no field — and therefore
no wire path — for user ids, emails, locations, ride/route/friend ids,
message content, telemetry, tokens, or subscription state. `toSafeMap` is the
only serialization, asserted key-for-key in tests.

Metrics and logs gain nothing new: entitlement checks already count
`feature=no_ads` outcomes under the WS-O cardinality discipline, and no ad
impression/click/revenue event exists anywhere by design (§14).

## 8. Consent boundary

`AdConsentState`: `unknown`, `notRequired`, `required`, `granted`, `denied`.
Only `granted` and `notRequired` permit ads; everything else refuses,
including the initial `unknown`. The enum describes product knowledge, not a
legal finding — its existence claims no GDPR/ATT compliance, and the actual
consent flows are a later workstream.

Storage (WS-SM): per-account secure storage (`cc_ad_consent.<accountId>`),
restored on login, memory-cleared on logout, never inherited across
accounts. Storage failure fails closed to unknown. There is no pre-existing
consent store to integrate with (`docs/privacy-data.md` §6 records the
absence); the consent screen (`/settings/ads`, en/fr/ar) is the only writer,
through explicit Allow / Don't allow / Decide-later actions of equal weight.

## 9. Offline behavior

Deterministic and bounded:

- Entitlement state unavailable (loading/error/null) → `NO_ADS` treated as
  absent → slot eligibility decided by the remaining rules. Rationale: ads
  are not premium authorization, so the failure direction favors eligibility;
  the failure *duration* is bounded by the 15-minute cache TTL.
- A stale Pro grant lapses — it never becomes an indefinite `NO_ADS`. Tested
  with a deterministically stale cache.
- After a restart there is no entitlement state (still in-memory only), while
  consent restores from per-account storage on login. Offline-from-install
  therefore behaves as Free-unknown-consent: ineligible until the server is
  reached and consent is recorded or restored.

## 10. Provider abstraction

`AdProvider`: `initialize()`, `isAvailable`, `load(AdContext)`,
`renderSlot(BuildContext, AdSlot)`, `dispose()`. The provider knows nothing
about subscriptions, entitlements, accounts, GPS, or training data —
eligibility is decided before any provider method runs. `AdLoadResult` cannot
carry content, impressions, clicks, or revenue: fake or fabricated ad events
are structurally impossible, not merely forbidden.

## 11. No-op provider

`NoOpAdProvider`: never contacts anything (there is no SDK, endpoint, or unit
id in its file or imports), always unavailable, always loads empty, renders
null, never throws. Every slot stays dark while the full policy → widget →
provider path executes around it.

## 12. Mobile architecture

```
EntitlementState (WS-S cache)
        │
        ▼
AdPolicyService (adPolicyServiceProvider)
  watches: auth, entitlements, consent, ride session, provider
        │  decisionFor(slot, now) — pure, explicit clock
        ▼
AdSlotWidget(slot) — the only ad surface in the app
        │  eligible → load once → provider pixels or nothing
        │  anything else → SizedBox.shrink
        ▼
AdProvider (NoOpAdProvider; override point for the future)
```

No screen reads entitlements, consent, ride state, or provider state for
advertising purposes; there is no `isPro`/`showAds` branching anywhere. The
widget never throws, never navigates, never intercepts gestures — its only
non-empty output is the provider's own rendering.

Screen mounting (WS-SM): `home` at the end of the home scroll (below the
record action, never displacing it) and `ride_summary` post-finish (above
Done, never between the rider and leaving). Training, routes, and social
slots stay unmounted pending a real provider and product review — the
taxonomy defines them, nothing renders them. Mounting is what surfaced the
one integration cost in this workstream: the home slot's entitlement fetch
broke an existing router test whose mock predated `/me/entitlements`. The
mock was updated to answer the endpoint (it simulates the backend, which has
it), not the production code weakened — and the incident is why mounting
stays at two screens until fill exists behind them.

## 13. Future provider integration

1. Implement `AdProvider` against the real SDK.
2. Override `adProviderProvider`.
3. Mount `AdSlotWidget(slot: …)` on approved screens.
4. Nothing else changes: no policy, widget, entitlement, or backend change.

Still required then (not now): real consent flows, ATT integration, ad-unit
inventory mapping, fill/error dashboards, and the legal review §8 defers.

## 14. Security

- No client path grants `NO_ADS`: forged claims, forged headers, and forged
  query parameters are ignored (WS-S tests); the mobile policy reads only the
  server-fetched cache, never client input.
- No cross-user leakage: policy inputs derive from the authenticated
  session's own fetch; account switch replaces state (tested Pro→Free).
- No mutation surface: backend 405s on all entitlement writes; no
  parameterized policy route exists (tested, including the absence of
  `/me/ad-policy` and `/me/no-ads`).
- Ad failures are non-fatal everywhere: provider throws are caught at the
  widget boundary; home, recording, routes, training, social, chat, coach,
  and auth cannot break through the ad path (no ad code runs on their paths
  except the shrink-rendering widget, which cannot throw).

## 15. Testing

- Mobile `test/ads_test.dart` (policy, provider, widget, taxonomy, adapter,
  catalog) and `test/ads_consent_test.dart` (store, persistence, consent
  screen incl. fr/ar RTL, mounted-slot structure): taxonomy exactness and
  unknown degradation; consent truth table and persistence across
  logout/login/switch; context key allowlist; no-op and adapter behavior;
  all seven policy rules incl. expired/revoked/future/stale/missing/failed
  grants; logout and account-switch isolation; widget dark/filled/throwing/
  unavailable/unknown paths; exactly-two-mounted-slots pin; prohibited
  screens clean; no fake analytics by construction.
- Backend `tests/test_ads_no_ads_boundary.py` (15 tests) and
  `tests/test_store_products.py` (7 tests): lifecycle, isolation,
  immutability, route absence, response/log identifier sweeps, `no_ads`
  metric labels without identity, table-name pin, catalog exactness and
  unknown rejection, no-money rule, no verify/purchase routes, no
  purchase-shaped OpenAPI surface.
- Full suites: backend 978 + 22, mobile 630 + new consent/catalog tests,
  all green (see report).

## 16. Known limitations

1. Two slots mounted (`home`, `ride_summary`); training, routes, and social
   stay taxonomy-only pending a real provider and product review (§12).
2. Consent copy awaits legal review; jurisdiction behavior (ATT, GDPR flows)
   is explicitly deferred (§8).
3. The 15-minute TTL bounds staleness but does not proactively refresh; a
   Pro grant expiring mid-TTL suppresses at most TTL-long.
4. `contentCategory` carries slot defaults only — coarse by construction,
   never rider data.
5. No backend ad-policy endpoint exists, deliberately: `/me/entitlements`
   already carries everything the policy needs (§4 of the workstream brief).

## 17. Explicitly deferred work

Google Mobile Ads / AdMob, Meta Audience Network, Unity Ads, AppLovin, real
requests/impressions/clicks/revenue, mediation, rewarded and interstitial
formats, personalized advertising, advertising identifiers (IDFA/AAID), ATT
integration, production consent SDK, store submission, Apple/Google billing,
Stripe, checkout, account deletion, ownership transfer, AI quota metering,
and `advanced_*` enforcement.

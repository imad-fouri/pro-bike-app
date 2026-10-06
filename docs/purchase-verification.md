# CycleCoach — Purchase Verification

Phase 10, WS-PV. Status: **trust boundary implemented; no provider integrated**.

This document states how a store purchase becomes an entitlement without
ever trusting the client, and what remains for real Apple/Google
integration. It describes the system as built, verified against the code —
not a billing claim. **No purchase can be completed, no store is
integrated, and every verification attempt today ends in an honest
`PROVIDER_UNAVAILABLE`.**

---

## 1. Architecture

```
Store purchase
  ↓  opaque credential, submitted by the client, trusted by no one
Provider adapter (none registered yet)
  ↓  VerifiedPurchase: provider's verdict, nothing else
PurchaseVerificationService (app/services/purchase_verification.py)
  ↓  provider → product → verify → ownership → environment → apply
Subscription rows (existing WS-S tables, untouched)
  ↓  existing reconciliation
Entitlement rows (existing WS-S tables, untouched)
  ↓  GET /me/entitlements
Mobile cache (refreshed, never written by the purchase flow)
```

Responsibility split, enforced by construction:

- Adapters answer "is this purchase valid according to this provider?"
- The verification service answers "what subscription state follows?"
- `subscription_service` answers "what features does this user have?"

None reaches into the others' decisions. The service never interprets
receipts; adapters never touch entitlements.

## 2. Trust boundaries

| Boundary | Rule |
|---|---|
| Client → API | The purchase token is opaque input. Plan, status, expiration, and every other claim about the outcome cannot be expressed in the request schema (`extra="forbid"`), so forgery is a 422, not a grant. |
| Adapter → service | `verify_purchase` receives the credential AND the expected user, so the adapter performs the first binding check; the service re-checks `verified.user_id == caller`, so a confused adapter cannot reassign a purchase. Either check failing ends the flow. |
| Provider → catalog | The requested product must be in `STORE_PRODUCTS`, the verified product must equal the requested one, and the verified plan must equal the catalog plan. Any disagreement rejects. Where server and provider could disagree, the server's data wins. |
| Verification → environment | The environment comes from the verification response, never the request. Unknown always rejects; sandbox on a production server rejects loudly (`ENVIRONMENT_MISMATCH`, no state change). |

## 3. Purchase flow

`process_purchase` runs one pipeline for both `verify` and `restore`
(a restore IS a re-verification — two implementations would let the paths
disagree about "verified"):

1. Provider name must be a known purchase origin (`manual` is rejected:
   it marks operational records, and a purchase "from manual" claims a
   vendor that does not exist).
2. A registered adapter must exist — none do, so this step ends every
   production attempt with `PROVIDER_UNAVAILABLE` and no verification call.
3. Product must be in the server catalog.
4. Adapter verifies with a 10-second timeout. Adapter domain errors pass
   through verbatim; crashes, malformed responses, and hangs become
   503/504 with the internals withheld.
5. Ownership, product, and plan binding (§2).
6. Environment gating (§2).
7. The result is applied as a subscription event through the existing
   idempotent, ordered, cross-user-guarded machinery — no second
   subscription system, no `is_pro`, no `premium_users`.
8. The caller's freshly resolved state is returned.

The event id is derived deterministically
(`verify:<provider>:<subscription>:<occurred_at>`): re-verifying the same
purchase replays the same event (duplicate-ignored), while a renewal carries
a new timestamp and processes in order. This assumes providers supply stable,
monotonic event times — documented here so a future adapter that cannot is
caught in review rather than in production.

## 4. Provider abstraction

`SubscriptionProviderProtocol` (WS-S, unchanged in shape) plus WS-PV
additions:

- `VerificationEnvironment`: `sandbox`/`production`/`unknown`, supplied by
  verification responses only.
- `VerifiedPurchase` gains `product_id`, `environment`, and
  `cancel_at_period_end` (all defaulted; existing constructors unaffected).
  It still carries no credential, receipt, secret, price, or currency.
- The provider registry (`register_provider`, empty in production) is the
  only lookup path — no adapter is referenced by name anywhere in routes.

No Apple, Google, or Stripe adapter exists. The test fake
(`FakeVerifyProvider`, signatures literally `valid:<id>`) proves handling
without proving anything about any store.

## 5. VerifiedPurchase model

Bounded by design: provider, external subscription id, user, plan, status,
period, occurrence time, event id, product id, environment,
cancel-at-period-end. Never: the credential, the full provider response,
prices, payment data, or anything the domain does not consume. A field-level
test pins the absence of credential-shaped names.

## 6. Idempotency

Same purchase verified twice → same event id → duplicate-ignored → one
subscription, one row per capability. Tested at both service and HTTP
levels. Database partial-unique indexes remain the final arbiter; the
service logic is the fast path, not the guarantee.

## 7. Ownership binding

Two layers: the adapter receives `expected_user_id` and must reject foreign
credentials; the service requires `verified.user_id == caller` and returns
`409 CROSS_USER_PURCHASE` otherwise. User B submitting User A's token changes
nothing for either rider (tested both directions of that sentence).

## 8. Environment handling

Server environment comes from `Settings.ENVIRONMENT`. Matrix:

| Verified \ Server | dev/test | production |
|---|---|---|
| sandbox | allowed | **rejected, loudly, no state change** |
| production | allowed | allowed |
| unknown | rejected | rejected |

## 9. Subscription integration

Verification writes through `apply_provider_event` exclusively: advisory
locking, upsert, reconciliation, and ordering are inherited, not
reimplemented. Terminal verifications (expired/revoked) write terminal
state rather than refusing — the honest answer to "verify this dead
purchase" is the current state, not an error.

## 10. Entitlement integration

The pipeline returns `resolve_state`, serialized as `EntitlementStateOut`
— the same shape as `/me/entitlements`, containing plan, capabilities,
and the caller's own rows only. Premium features then answer through the
unchanged guards (proven end to end: verified Pro → coach 200, no client
state involved).

## 11. Restore purchases

`POST /store/purchases/restore` runs the identical pipeline with restore
audit naming. Restore on an expired purchase returns plan free; restore
with nothing to reconcile returns the current state. There is no
blind-grant path and never will be: adding one would require a second flow,
which §3 exists to forbid.

## 12. Security

- Authentication required on both routes; per-user+IP rate limit (30/hour —
  verification calls a provider, so the endpoint must not be a free
  token-guessing oracle, and reinstall restore storms still fit).
- `purchase_token` bounded at 4 KiB, never echoed (the shared validation
  handler strips `input`; tested with a 5 KiB token and with a canary).
- Logs carry provider/status/plan/kind plus exception *type names* only;
  `purchase_token` and `receipt*` keys are masked by the shared redaction
  predicate (extended in this workstream; no `receipt` key existed to
  collide with). Caplog sweeps assert absence per request.
- Metrics: one counter, `purchase_verifications_total{outcome,provider}`
  with outcomes verified/rejected/unavailable/error. No identity, token,
  product, or transaction labels — the allowlist needed no change.
- OpenAPI table pinned to exactly the two authenticated verification routes
  (`test_openapi_has_no_purchase_or_billing_surface`, updated deliberately
  when they legitimately appeared).

## 13. Logging

Covered in §12. The single deliberate choice worth restating: provider
transport failures log the exception type name (as the 500 handler does),
because "the provider failed" without a which is unactionable, while the
message and traceback stay server-side.

## 14. Offline behavior

- Verification requires network by nature; there is no offline verify and
  no queued purchase (a queued credential is a credential at rest — out of
  scope without a storage justification).
- Mobile restore failure leaves entitlements untouched; the outcome enum
  distinguishes `unavailable` (nothing to retry until integration lands)
  from `failed`.
- Stale server state cannot grant: plan is re-resolved per request,
  server-side, on every premium call.

## 15. Apple deferred requirements

App Store product registration (replacing the provisional ids); StoreKit
transaction forwarding; App Store Server API verification adapter;
server-notification (JWS) endpoint with signature verification *before*
parsing; sandbox/production routing per Apple's environment field. Consult
current Apple documentation at implementation time — not this document and
not memory.

## 16. Google deferred requirements

Play product registration; Play Billing purchase forwarding; Google Play
Developer API verification adapter; Real-time Developer Notifications with
signature verification before parsing. Same documentation warning as §15.

## 17. Known limitations

1. The registry is empty: every verification attempt ends at
   `PROVIDER_UNAVAILABLE`. This is the honest state, tested, not a stub.
2. `occurred_at` stability is assumed for duplicate detection across
   re-verifications (see §3); a provider without stable event times needs
   an explicit idempotency design before integration.
3. Rate limits (30/hour) are starting values, not tuned ones.
4. No webhook/server-notification endpoint exists yet (§15–16 describe
   what it must do).
5. `cancel_at_period_end` travels from verification but no provider sets
   it yet; end-of-period cancellation via verify is therefore untested
   against a real shape (the reconciliation side is fully tested in WS-S).

## 18. Future webhook/server notification architecture

When it lands, it must: verify the provider signature before parsing
(the protocol already takes signature separately for this reason); map
through `plan_for_product`; apply through `apply_provider_event` (which
already handles duplicates, ordering, and cross-user); and never add a
second subscription system. The route will be provider-scoped, HMAC/signature
gated, and rate-limited independently of the user-facing verify routes.

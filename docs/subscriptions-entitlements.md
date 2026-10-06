# CycleCoach — Subscriptions & Entitlements

Phase 10, WS-S. Status: **implemented; store integrations deferred**.

This document states what the monetization foundation is, what it is not, and
which decisions are still product decisions rather than engineering ones. It
describes the system as built, verified against the schema and the code — not a
billing claim. **No purchase can be made, no store is integrated, and no ads
exist.**

---

## 1. The two words that must not be confused

| Term | Meaning | Example |
|---|---|---|
| **Subscription** | A commercial relationship with a billing origin | A Pro monthly subscription through the App Store, current period 2026-10-06 → 2026-11-06 |
| **Entitlement** | Server-authoritative permission to use one product capability | `ai_coach`, effective 2026-10-06T04:00:00Z → 2026-11-06T04:00:00Z |

A subscription never opens a feature directly. Every premium route asks one
question — "does this rider hold an effective entitlement for this feature?" —
and the subscription tables exist to answer where that entitlement came from,
not to grant access themselves.

This indirection is load-bearing. When a real provider is integrated, its
adapter will normalize external products into the internal `Plan`/`Feature`
vocabulary, and no route handler will change.

---

## 2. Plans and capabilities

### 2.1 Free — an explicit product state

Free is **not** "the absence of Pro rows". It is a defined plan with a defined
capability list, returned by `GET /me/entitlements` for every rider:

| Capability id | Meaning |
|---|---|
| `core_ride_recording` | Record rides, the product's core |
| `core_routes` | Route CRUD, GPX import/export |
| `core_training` | Training profile, FTP records, activities, workouts |
| `limited_ai_status` | `GET /coach/status` — enough to render "locked" vs "unavailable" |

Free riders **may see ads** in the future. There is no ad system today (§9).

### 2.2 Pro — one paid product

| Capability id | Meaning | Enforced today |
|---|---|---|
| `ai_coach` | Generated coach answers (all four intents) | **Yes** — `POST /coach/message`, both explain routes, `GET /coach/weekly-summary` |
| `advanced_training` | Future structured/adaptive training | No — boundary defined, product packaging pending |
| `advanced_analytics` | Future longitudinal trends and insights | No — existing summary/loads/recovery stay core |
| `advanced_routes` | Future premium route capabilities | No — existing route CRUD/GPX stays core |
| `no_ads` | Suppress advertising | Semantic only — there is nothing to suppress yet |

The identifiers are stable machine-readable strings. The client translates them
for display; a translated string is never accepted as authorization input.

### 2.3 The AI monetization boundary (provisional)

Gating all four coach answer endpoints while leaving `/coach/status` free is a
**foundation decision, and it is marked provisional**:

> **AI monetization boundary: PRODUCT DECISION REQUIRED.**
>
> The current enforcement assumes generated answers are Pro and operational
> status is Free. Whether read-only weekly summaries, deterministic fallbacks,
> or limited free quotas should remain Free is a packaging decision this
> workstream does not make. Changing it means moving the `require_entitlement`
> guard, not redesigning anything.

---

## 3. Server authority

```
Client
  ↓  authenticated request, no entitlement claim
Backend entitlement service
  ↓  re-resolved from the caller's own rows, at request time
Feature access: allowed / 403 ENTITLEMENT_REQUIRED
```

NOT:

```
Flutter → local "isPro" → feature enabled
```

Consequences, all tested:

- A client sending `is_pro=true`, `plan=pro`, a forged feature name, or an
  `X-Is-Pro` header gets the same 403 as a client sending nothing.
- The mobile cache (`EntitlementNotifier`) is display-only. A stale or
  tampered cache can mislabel a screen for up to 15 minutes; it cannot open a
  premium endpoint, because the endpoint never consults it.
- `GET /me/entitlements` is the only entitlement route, and it returns only the
  caller's own state. There is no `/users/{id}/entitlements`, no POST/PUT/PATCH/
  DELETE on entitlement state, and no admin grant endpoint.

### 3.1 Denial contract

`403` with a stable machine-readable code:

```json
{
  "error": {
    "code": "ENTITLEMENT_REQUIRED",
    "message": "This feature requires CycleCoach Pro.",
    "details": {
      "code": "ENTITLEMENT_REQUIRED",
      "message": "This feature requires CycleCoach Pro.",
      "feature": "ai_coach",
      "required_plan": "pro"
    }
  }
}
```

The response names the missing capability and the plan that supplies it. It
never names a provider, a subscription status, an external id, or another
rider.

---

## 4. Lifecycle

### 4.1 Subscription states

`active`, `trialing`, `past_due`, `canceled`, `expired`, `revoked` — each with a
documented reconciliation rule (§4.2). `past_due` preserves current-period
access (dunning grace, not free service); `canceled` preserves access through
the period only when `cancel_at_period_end` is true, otherwise ends it at once.

### 4.2 How one event becomes authorization

`apply_provider_event` runs in one transaction under one advisory lock on the
external subscription identity:

1. **Duplicate** (same provider event id) → ignored, same row returned.
2. **Out-of-order** (older `occurred_at` than stored, ties broken by event id) →
   ignored. An older cancellation never undoes a newer renewal.
3. **User mismatch** (external id already belongs to another rider) → `409
   SUBSCRIPTION_USER_MISMATCH`. Paid access cannot be moved between riders by
   replaying an event.
4. Otherwise the subscription row is upserted and the rows it owns are
   reconciled: granting statuses write one ACTIVE row per premium feature;
   grace statuses preserve the current period; terminal statuses mark their
   rows `INACTIVE` (`EXPIRED`, immediate `CANCELED`) or `REVOKED`.

Only rows linked to that subscription are touched. Manual grants are a separate
authorization story and survive subscription events.

### 4.3 Time

All timestamps are timezone-aware; naive timestamps are rejected before any
write. Effectiveness is `status == ACTIVE and starts_at <= now < expires_at`.
Expiration is derived, never stored as a competing state.

---

## 5. Provider abstraction (interface only)

`app/services/subscription_providers.py` defines `SubscriptionProviderProtocol`
with `verify_purchase`, `get_subscription`, and `process_event`. No adapter
exists. The test fake (`FakeSubscriptionProvider`, signatures literally
`valid:<event_id>`) proves the application handles verified, failed, expired,
revoked, duplicate, and out-of-order provider results — it proves nothing about
Apple or Google, and says so in its docstring.

When a real callback path is built, it must verify the provider signature
**before** parsing. The protocol's `process_event` takes the signature as a
separate argument so that ordering is structural, not conventional.

| Integration | Status |
|---|---|
| Apple App Store | **DEFERRED** |
| Google Play | **DEFERRED** |
| Stripe / web billing | **DEFERRED** (not referenced in code) |
| Advertising SDK / ad serving | **DEFERRED** |

---

## 6. Internal operations

There is no admin subscription API. If manual entitlement management becomes
necessary, it is a **future controlled operation** with requirements, not a
route to add:

- authenticated operator identity, separate from rider auth;
- dual control or ticket reference for every grant;
- append-only audit record naming operator, rider (by id), capability,
  window, and reason — never credentials or provider secrets;
- expiry on every manual grant; permanent manual Pro is not an operation.

Nothing above exists today. `MANUAL` rows in tests are fixtures, not operations.

---

## 7. Privacy

Subscription and entitlement data is user data, classified alongside the
account: the tables are covered by the same owner-only access matrix as
`users` (`docs/privacy-data.md` §2, §4.22).

What is stored: user id, provider origin (`manual` in all rows written today),
external subscription/event ids where idempotency needs them, plan, statuses,
effective windows, timestamps. What is **never** stored: card data, purchase
tokens, receipts, provider secrets, access tokens.

What is logged: bounded enums and counts (`subscription.event_applied |
plan=pro provider=manual status=active`). External ids, purchase credentials,
and user ids are not log fields here. What is metered: `feature`, `plan`,
`status`, `outcome` — all bounded enums; `user_id`, `email`, and
`subscription_id` are structurally unrecordable as metric labels.

---

## 8. Observability

New in-process counters, same WS-O discipline:

| Metric | Labels |
|---|---|
| `subscription_events_total` | `event` (applied/duplicate_ignored/out_of_order_ignored), `provider`, `status` |
| `entitlement_checks_total` | `feature`, `outcome` (allowed/denied), `plan` |
| `entitlement_denials_total` | `feature`, `plan` |

---

## 9. Offline and cache behavior

- Entitlement state is cached **in memory only**, TTL 15 minutes, and cleared
  the moment authentication ends — logout, session expiry, or account switch.
  A restart starts with no cached state.
- A stale cache can only mislabel; premium API features require network and
  server authorization, so offline cached Pro state never becomes authority.
- The Coach screen does not pre-gate on the cache. It always attempts the
  request and renders the lock only on a real 403 — which is why the lock
  cannot be wrong for longer than one request.

---

## 10. What WS-S did not do

- No billing integration, no purchase UI, no checkout button (the lock screen
  deliberately has no purchase affordance).
- No `is_pro` column anywhere. Plan is derived from effective entitlements on
  every read.
- No change to deterministic training authority, route engine, or coach
  safety behavior. Entitlement denial happens before any of those run.
- No account deletion, ownership transfer, or admin hierarchy.

## 11. Product decisions required

1. AI monetization boundary (§2.3) — the provisional gate needs confirmation.
2. Which `advanced_*` capabilities map to which endpoints (§2.2).
3. Whether `past_due` grace should be time-boxed rather than period-boxed.
4. Manual-grant operation design (§6) — only if ops need it before providers.
5. Free-tier AI quota vs hard gate (a quota would need usage metering that does
   not exist).

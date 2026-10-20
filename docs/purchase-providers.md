# CycleCoach — Purchase Providers (Apple + Google)

WS-RP, Phases 3–5. Status: **Apple and Google adapters implemented and
fixture-verified; store webhook endpoints implemented and tested; providers
registered only when configured.**

Facts about the stores are sourced from `docs/store-provider-audit.md` and
consulted at implementation time; this document describes the system as
built and verified against the code — a billing/Store claim is out of scope.

---

## 1. Where verification and providers meet

```
Client purchase credential  (opaque, trusted by no one)
  └─ PurchaseVerificationService (app/services/purchase_verification.py)
        ├─ looks up provider by name in the registry (fail-closed)
        ├─ looks up the product in the provider catalog (fail-closed)
        └─ calls adapter.verify_purchase(token, expected_user_id)
             AppleStoreProvider / GoogleStoreProvider
                └─ provider API (App Store Server API / Play Developer API)
                   returns normalized VerifiedPurchase (no secrets inside)
        └─ ownership / product / plan / environment checks
        └─ apply_provider_event → Subscription → Entitlement → resolve_state
```

Division of responsibility (enforced by construction, same as WS-PV):

- **Adapters** answer "is this purchase valid, for this user, for this
  product, right now, per this store?" and hand back a
  `VerifiedPurchase` — never touching entitlements.
- **Verification service** answers "what subscription state follows" and
  re-checks user/product/plan binding.
- **subscription_service** answers "what features does this user have".

Neither Apple nor Google code reaches into the others' decisions, and the
providers never read `is_pro`/entitlement state to decide a credential.

## 2. Configuration and registration

Settings added to `app/core/config.py` (all fail-closed):

| Setting | Apple | Google |
|---|---|---|
| Enable | `APPLE_STORE_ENABLED` | `GOOGLE_PLAY_ENABLED` |
| Store identity | `APPLE_BUNDLE_ID` | `GOOGLE_PACKAGE_NAME` |
| Auth material | `APPLE_ISSUER_ID`, `APPLE_KEY_ID`, `APPLE_PRIVATE_KEY_PATH` | `GOOGLE_APPLICATION_CREDENTIALS_PATH` |
| Trust anchor | `APPLE_ROOT_CERTS_PATH` | (Google: official Google server TLS) |
| Transport | `APPLE_REQUEST_TIMEOUT_SECONDS` | `GOOGLE_REQUEST_TIMEOUT_SECONDS` |

Registration contract (`register_provider` / `provider_for` in the
verification service; deliberately *no* default/auto registration):

- No config → provider **not registered** → `provider_for(...)` returns
  `None`, so verification returns `PROVIDER_UNAVAILABLE` and the webhook
  route answers 503 "Webhook not configured". Never a partial adapter.
- Missing/invalid credentials path → provider not registered, fail closed.
- Valid config → the adapter is registered by its own name (`app_store` /
  `play_store`); re-registering the same name overwrites the entry.
- Registry is the only lookup path; routes never reference an adapter by
  name. There is no `_register_default_providers` — nothing auto-registers
  adapters, so CI and default local runs stay provider-free and every
  purchase ends in an honest `PROVIDER_UNAVAILABLE`.

Webhook toggles are separate from provider registration (all default OFF):
`APPLE_WEBHOOKS_ENABLED`, `GOOGLE_WEBHOOKS_ENABLED`, `GOOGLE_RTDN_AUTH_MODE`
(`none` local-only / `oidc` production-required), `GOOGLE_RTDN_OIDC_AUDIENCE`,
`GOOGLE_RTDN_OIDC_ISSUER`, `GOOGLE_RTDN_JWKS_URI`. A webhook enabled without
its adapter configured always answers 503 (production gate #10 below).

## 3. Apple Store adapter (`apple_store_provider.py`)

### 3.1 Authentication
- Caller JWT is an ES256 token signed with the team's App Store Connect
  `.p8` private key (`APPLE_ISSUER_ID`, `APPLE_KEY_ID`), audience
  `https://appleid.apple.com`, `iat`/`exp` bounded (see code).
- No client key material ever leaves the server.

### 3.2 Verification
- `verify_purchase` calls App Store Server API **Get All Subscription
  Statuses** and picks the transaction that matches
  `originalTransactionId` and the verified `productId`.
- Each in-app purchase transaction is a JWS signed by Apple with a raw
  ES256 signature. The adapter verifies the JWS **and** the `x5c`
  certificate chain up to Apple's root certificate (trust anchor loaded
  from `APPLE_ROOT_CERTS_PATH`). The JWS header leaf certificate must be
  currently valid (not expired, not yet valid).
- Signature verification is *before* any payload trust. An unverifiable
  JWS, bad chain, or absent/invalid header → `PURCHASE_INVALID`/422.
  The payload is never used to grant anything until the signature checks.

### 3.3 Binding and enforcement
- **Bundle**: the decoded `bundleId` must equal `APPLE_BUNDLE_ID`
  (server-configured identity). A foreign bundle is rejected. Note Apple
  returns the bundle that signed the receipt.
- **Type**: the transaction must be an auto-renewable subscription
  (`type` / status shape); nothing else grants.
- **Product**: `productId` must match the verified `product_id`, and
  `plan_for_product` must resolve in the server catalog (`store_products`).
- **Rider binding**: if `appAccountToken` is present it must equal the
  expected user's UUID and `AppAccountToken` must be supported for that
  transaction. Missing binding where Apple required it → reject.
- **Environment**: comes from the JWS payload (`storefront`-independent),
  never the request. Sandbox on a production server rejects loudly with
  `ENVIRONMENT_MISMATCH` and no state change; unknown always rejects.

### 3.4 State mapping
The adapter maps Apple's subscription `status` plus `autoRenewStatus`
into `SubscriptionStatus` via a single decision table in
`_subscription_status(...)`, and `cancel_at_period_end` from
`autoRenewStatus == 0` (renewal disabled) while the active period still
holds. Active-but-cancelling keeps `ACTIVE` with the flag, expiry
drives `CANCELED`. Refunds/revocation appear as `REVOKED`. The fixture
sweep tests every row of the table.

### 3.5 Expiry
Expiry is always the server-provider `expiresDate`; malformed or missing
values are verified/422, never defaulted. Client times are never used.

### 3.6 Events
`process_event(payload, signature)` decodes a `signedPayload` (v2), runs
the whole signature+bundle+product environment verification, resolves
`notificationType`, and returns a `ProviderSubscriptionEvent` consumed by
the same `apply_provider_event` pipeline as verification. The webhook route
(`POST /api/v1/webhooks/apple`) calls it with `signature=None` — the JWS on
`signedPayload` IS the signature; the route passes the raw JSON through and
trusts nothing but the adapter's verdict.

## 4. Google Play adapter (`google_play_provider.py`)

### 4.1 Authentication
- OAuth2 **service-account** flow: the adapter builds an RS256 JWT
  assertion (header with the key id from the credentials file; claims
  `iss=client_email`, `scope=androidpublisher`, `aud=oauth2.googleapis.com`,
  issued/expiry) and exchanges it for a short-lived access token at
  `https://oauth2.googleapis.com/token`
  (`urn:ietf:params:oauth:grant-type:jwt-bearer`).
- Credentials come from a Google service-account JSON file
  (`GOOGLE_APPLICATION_CREDENTIALS_PATH`); the file is read at
  registration time and the private key kept in memory. **The path is
  config; the file is never committed, and the key is never logged.**
- Access tokens are cached until ~60s before expiry so repeated
  verifications do not re-sign and re-exchange on every call.
- A malformed credentials file means the provider is simply not
  registered (fail closed).

### 4.2 Verification
- `verify_purchase` calls **`purchases.subscriptionsv2.get`**
  (`.../purchases/subscriptionsv2/tokens/{token}`). The deprecated
  `subscriptions.get` is not used.
- The purchase token is the lookup key and the stable subscription
  identity (`provider_subscription_id`, audit §8.1). The v2 response does
  not echo the token back — using it as identity is deliberate.
- **Package enforcement**: the full response includes `packageName`/the
  request was made against the configured package; a `packageName` that
  does not equal `GOOGLE_PACKAGE_NAME` is rejected. The package is never
  taken from the client.
- **Product**: `lineItems[0].productId` must equal the verified product
  and resolve in the server catalog. Unknown or inactive catalog products
  reject.
- **Rider binding**: `externalAccountIdentifiers.obfuscatedExternalAccountId`
  must equal the expected user's UUID; a missing or different id →
  `CROSS_USER_PURCHASE`-style rejection. On restore/verify the binding is
  required; RTDN refetch re-derives the owner and verifies the resulting
  state against the subscription pipeline's user.

### 4.3 State mapping (audit §8.2)
`subscriptionState` → `SubscriptionStatus`:

| Play state | Status |
|---|---|
| `ACTIVE` | `ACTIVE` |
| `PENDING`, `IN_GRACE_PERIOD`, `ON_HOLD`, `IN_ACCOUNT_HOLD`, `PAUSED` | `PAST_DUE` (non-granting) |
| `CANCELED` | `CANCELED`; when `cancel_at_period_end` is set and expiry is in the future the user keeps access until expiry, with the flag |
| `EXPIRED` | `EXPIRED` |
| `CANCELED` via `canceledStateContext.revocation` or `fullRefund` | `REVOKED` |
| `UNSPECIFIED`/unknown/malformed | rejected fail-closed, **never** silently `ACTIVE` |

- **testPurchase** (`testPurchase=true`) → environment `SANDBOX`; not set →
  `PRODUCTION`. There is no client-declared environment flag; the same
  endpoint serves sandbox and production, exactly like Google's API.
- **Expiry**: `expiryTime` is authoritative from the response. Missing or
  malformed expiry → `PURCHASE_INVALID`/422, never defaulted (audit §8.4
  treats expiry as provider-authoritative).

### 4.4 Error classification
Transport errors are classified, never swallowed into grants:

| Outcome | HTTP | Result |
|---|---|---|
| 400/404 (bad or unknown token) | `422 PURCHASE_INVALID` | honest "cannot verify" |
| 401/403 (bad credentials/scope) | `503 PROVIDER_ERROR` | outage, no grant |
| 429 (quota) | `503 PROVIDER_ERROR` | outage, no grant |
| 5xx | `503 PROVIDER_ERROR` | outage, no grant |
| timeout / malformed JSON | `503 PROVIDER_ERROR` | outage, no grant |

The two classes are tested separately (`PURCHASE_INVALID` vs
`PROVIDER_ERROR`); provider failure must never look like "not yours" and
must never grant.

### 4.5 Idempotency
Verifying the same token repeatedly returns the same
`ProviderSubscriptionEvent` (deterministic bounded digest) →
`apply_provider_event` duplicate-ignores; one subscription, one row per
capability, no duplicate events. Tested end to end through the pipeline.

### 4.6 RTDN events
`process_event(payload, signature)` parses a `testNotification` (returns
the ping, no state change) or a `subscriptionNotification`:

- The notification's `packageName` must match or be absent (comes from
  Google's signed push, not the client).
- **The adapter always refetches authoritative state via
  `subscriptionsv2.get` before emitting an event** (audit G13
  "names ours") — notification fields are never trusted for expiry or
  state by themselves.
- `notificationType` overrides the refetched state where the type is the
  authoritative lifecycle signal: 1/2/4/7/9/11 → `ACTIVE`
  (2/4/7/9/11 plus grace kept active), 3 → `CANCELED`, 5/6/10 →
  `PAST_DUE`, 12 → `REVOKED`, 13 → `EXPIRED`, unknown type → reject.
- Missing rider binding → reject; the subscription cannot be assigned to
  a user without one.
- `process_event` is the contract the webhook route
  (`POST /api/v1/webhooks/google`) calls after the Pub/Sub OIDC push token
  is verified; see section 7.

### 4.7 Security invariants exercised by tests
- Purchase tokens and the service-account key appear in **no** log line,
  exception message, or response (caplog sweeps + a canary token).
- HTTP/API errors never echo the token or credentials.
- `GOOGLE_PLAY_ENABLED` off / bad credentials path → registry empty →
  `PROVIDER_UNAVAILABLE`.
- An Apple product cannot be claimed through the Google adapter and vice
  versa (cross-provider confusion tests).
- Invalid token / wrong package / wrong product / inactive product /
  cross-user / missing rider / expired / cancelled / revoked / refunded /
  test purchase / malformed response / 401/403/404/429/5xx / timeout /
  malformed JSON / provider-unavailable each have a dedicated test.

## 5. Provider catalog (Phase 2, shared)

`app/services/store_products.py` holds the canonical catalog. Both
adapters resolve *only* through `provider_product_for(...)`: a product
not present or marked `inactive` is rejected at the adapter and at the
verification service, so the pipeline's product/plan checks stay the
single source of truth and put plans/products in sync. The adapter never
invents plans.

## 6. Security rules that apply to both adapters

1. Credentials and tokens are never logged, echoed, or stored beyond the
   transient verified result.
2. Failures are either `PURCHASE_INVALID` (this credential is not yours /
   not valid) or `PROVIDER_ERROR` (the store could not be asked). A
   provider outage never grants.
3. Sandbox verifications never reach a production server's entitlement
   state, and unknown environments reject.
4. Package/bundle identity, product identity, and user binding are server-
   enforced; the client cannot name any of them meaningfully.
5. Repeated verification is idempotent via deterministic event digests and
   the existing duplicate-ignore machinery.
6. Webhooks verify the store signature before parsing and reuse
   `process_event`/`apply_provider_event` — there is exactly one entitlement
   system. Section 7 details the routes.

## 7. Store webhook endpoints (Phase 5)

Two thin routes (`app/api/v1/webhooks.py`), mounted under
`/api/v1/webhooks` by the v1 router, over the SAME adapters and pipeline as
verification — no provider business logic, no second subscription path:

- `POST /api/v1/webhooks/apple` — App Store Server Notifications v2
- `POST /api/v1/webhooks/google` — Google Play RTDN Pub/Sub push

### 7.1 Trust boundaries
- **Apple**: the JWS on `signedPayload` *is* the signature. The route
  passes the raw JSON to `adapter.process_event(payload, signature=None)`;
  the adapter verifies the ES256 signature and the x5c chain to a
  configured Apple root before trusting any field, then enforces
  bundle/product/environment. The route reads nothing from the payload.
- **Google**: the Pub/Sub push is authenticated by its OIDC token in the
  `Authorization: Bearer` header. The route verifies the token against
  Google's published JWKS (`pubsub_auth.py`, RSA key-size bounded 1024–16384
  bits) before the envelope is decoded; a token signed by a non-Google key
  or with the wrong `aud`/`iss` is rejected 403. RTDN fields are never
  authoritative — the adapter re-fetches `subscriptionsv2.get` before
  emitting any event.

### 7.2 Gates (in route order per request)
1. Rate limit (per-IP, in-memory, best effort): Apple 720/h, Google 7200/h.
2. Config gate: the webhook toggle *and* its adapter toggle must both be on,
   else 503 "Webhook not configured."
3. Google only: `GOOGLE_RTDN_AUTH_MODE == "oidc"` with a non-empty audience,
   else 503.
4. `provider_for(...)` — a configured setting whose adapter is not
   registered answers 503, same as every other pipeline path.
5. Body read is streamed and capped at 1 MiB → 413 before any parse.
6. JSON body must decode to an object → 400 (no fragment echoed or logged).

### 7.3 Failure and retry semantics
Apple retries failed deliveries for up to 24h and Pub/Sub until ack, so the
status the route returns IS the delivery instruction:

- Any unauthenticated/unverifiable input → **4xx** = "not our event, drop
  it": 403 bad push token, 400 malformed JSON, 413 oversized, 422 adapter
  rejection (bad signature, foreign bundle/package, unknown product,
  cross-user, unsupported type, environment mismatch). The 4xx is permanent
  and the re-delivery is harmless because `apply_provider_event` is
  idempotent (deterministic event digest, duplicate-ignored) and
  out-of-order-safe (`_stale()` ordering guard).
- Provider outage/timeout (5xx from the store API, auth failure, quota) →
  **503** so delivery is retried later — retry is the safe direction, an
  error never grants.
- Unexpected database failure after authentication → **500** so the event is
  retried, never silently lost.
- Unknown notification types fail closed with 4xx — never a state change.

### 7.4 Logging and redaction
- The route logs only outcome/status/provider enums (`webhook.processed`,
  `webhook.rejected`, `webhook.error`), never the payload, never the signed
  payload, never a purchase token, never the Authorization header.
- `pubsub_auth` failures are logged by exception type only.
- The application logger sets the `httpx` logger to `WARNING`: httpx logs
  one INFO line per outbound request containing the FULL URL, and the
  Google RTDN refetch URL embeds the purchase token as a path component
  (`.../subscriptionsv2/tokens/{token}`) — a literal purchase token in the
  log would be a stored-credential leak. WARNING-and-above still propagate
  through the app's own formatter and correlation filter.

### 7.5 Production config requirements (summary gate #10)
- `APPLE_WEBHOOKS_ENABLED` without `APPLE_STORE_ENABLED` → gate failure.
- `GOOGLE_WEBHOOKS_ENABLED` without `GOOGLE_PLAY_ENABLED` → gate failure.
- `GOOGLE_WEBHOOKS_ENABLED` with `GOOGLE_RTDN_AUTH_MODE != "oidc"` → gate
  failure (unauthenticated callbacks must never be accepted in production).
- `oidc` mode with empty `GOOGLE_RTDN_OIDC_AUDIENCE` → gate failure.

## 8. Known limitations

1. Google purchase tokens are used as identity. Play tokens can exceed 128
   characters, so migration `0015_store_webhooks` widened
   `subscriptions.provider_subscription_id` to VARCHAR(1024)
   (`_require_text` limit now 1024; event id stays 128).
2. Apple's Get All Subscription Statuses requires the App Store Connect
   private key + certs placed where the configured paths point; CI runs
   with no credentials (registry empty) so adapter and webhook code is
   fixture-tested, not live-tested.
3. Registration and the webhook OIDC verifier are memory-resident per
   process; a deploy takes effect on restart. No hot-reload of credentials
   or JWKS beyond the cache TTL.
4. `cancel_at_period_end` for Google is taken from the refetched v2 body,
   not from RTDN fields.
5. Rate limiting is the shared in-memory per-process limiter (documented
   best effort); it is a coarse backstop against abuse, not a distributed
   quota.
6. `GOOGLE_RTDN_AUTH_MODE="none"` is development-only; the production gate
   refuses it. A local deployment that enables Google webhooks must run
   OIDC auth even though there is no real Pub/Sub credential involved.
# CycleCoach — Store Provider Audit (WS-RP Phase 0)

Status: **audit complete; no provider code written yet**. Baseline: WS-AC PASS at
`d7178ab`.

This document is the Phase 0 deliverable for WS-RP. It audits the **current
official** Apple and Google purchase mechanisms before any adapter code is
designed, and it is the contract Phase 1–4 adapters are built against. Facts
were checked against the provider documentation listed in §2 on 2026-10-07,
not against memory or third-party summaries.

Every row is tagged:

- **VERIFIED FACT** — stated by the linked official provider documentation (or
  directly demonstrated by it).
- **IMPLEMENTATION DECISION** — a CycleCoach choice about how to use the
  provider, made against the verified facts and this repo's existing
  server-authoritative architecture.
- **OPEN PRODUCT DECISION** — needs a product/account-holder decision before or
  during implementation (credentials, product registration, entitlement for
  grace/retry, bundle ids, package name, etc).

---

## 1. Why this audit runs first

WS-PV built the trust boundary and left the adapter slots empty on purpose:
`docs/purchase-verification.md` §15–16 list "Apple deferred requirements" and
"Google deferred requirements", both ending with: *consult current Apple/Google
documentation at implementation time — not this document and not memory.* This
audit is that consultation. It confirms the enrollment shape the existing
interfaces (`SubscriptionProviderProtocol`, `VerifiedPurchase`,
`ProviderSubscriptionEvent`) must serve, and it flags where the protocol's
assumptions need the smallest verifiable extension.

Non-goals: no receipts, no client-side entitlement decisions, no billing
reconciliation, no revenue recognition, no price data, no new subscription
system. Nothing here gives the mobile client authority over `is_pro`.

---

## 2. Sources (all official, all consulted 2026-10-07)

Apple:

- App Store Server API overview — developer.apple.com/documentation/appstoreserverapi
- Get All Subscription Statuses — .../appstoreserverapi/get-all-subscription-statuses
- Get All Subscription Statuses `status` field — .../appstoreserverapi/status
- `environment` field — .../appstoreserverapi/environment
- `JWSTransaction` — .../appstoreserverapi/jwstransaction
- `JWSTransactionDecodedPayload` — .../appstoreserverapi/jwstransactiondecodedpayload
- `JWSRenewalInfo` / `JWSRenewalInfoDecodedPayload` — .../appstoreserverapi/jwsrenewalinfo(-decoded)
- App Store Server Notifications v2 — .../appstoreservernotifications
  - `responseBodyV2` — .../appstoreservernotifications/responsebodyv2
  - `notificationType` (full event table incl. `subtype`) — .../appstoreservernotifications/notificationtype
- Generating JSON Web Tokens for API requests — .../appstoreserverapi/generating-json-web-tokens-for-api-requests
- App Store Server Library — developer.apple.com/documentation/appstoreserverapi/simplifying-your-implementation-by-using-the-app-store-server-library

Google:

- Play Developer API Getting Started (service accounts) — developers.google.com/android-publisher/getting_started
- Play Developer API Authorization — developers.google.com/android-publisher/authorization
- Play Developer API REST reference, `purchases.subscriptionsv2` — developers.google.com/android-publisher/api-ref/rest/v3/purchases.subscriptionsv2
- Play Billing lifecycle / RTDN — developer.android.com/google/play/billing/lifecycle
  (RTDN structure confirmed via the official reference
  developer.android.com/google/play/billing/rtdn-reference and multiple
  confirming mirrors of the same table; exact `notificationType` values pinned
  at implementation time against the live reference — Google's dev site was not
  fetchable directly during this audit.)

---

## 3. Apple App Store

### 3.1 Provider, official API, auth

| # | Area | Finding | Tag |
|---|------|---------|-----|
| A1 | Provider | App Store Connect account; auto-renewable subscription in-app purchase configured there; one or more subscription group identifiers. | OPEN PRODUCT DECISION — needs the account holder to register the real products and hand over keys. |
| A2 | Official API | **App Store Server API** (REST, TLS 1.2+). Production base URL `https://api.storekit.apple.com/`, sandbox base URL `https://api.storekit-sandbox.apple.com/`. The server calls Apple; no client SDK is authoritative for entitlement. | VERIFIED FACT (environment page shows the sandbox base URL; product docs show `api.storekit.apple.com`) |
| A3 | Auth | Every request carries a short-lived **JWT** signed **ES256** with an App Store Connect private key. Header: `alg=ES256`, `kid`=key id, `typ=JWT`. Payload: `iss` = issuer id, `iat`, `exp` (tokens expiring **more than 60 minutes** after `iat` are invalid), `aud="appstoreconnect-v1"`, `bid` = bundle id. Delivered as `Authorization: Bearer <jwt>`. | VERIFIED FACT (Generating JSON Web Tokens for API requests) |
| A4 | Apple's helper | The open-source **App Store Server Library** (official, in 4 languages incl. Python) builds the JWT, signs verification calls, and exposes `verifyAndDecodeTransaction` over the App Store public keys — the recommended way to validate JWS transactions rather than reimplementing RFC 7515. | VERIFIED FACT |
| A5 | Credentials | Three secrets: **issuer id** (team), **key id**, and the **.p8 ES256 private key** (downloadable once from App Store Connect → Users and Access → Keys). None may appear in code, logs, responses, or metrics. | VERIFIED FACT / IMPLEMENTATION DECISION (redaction policy: never log; never respond; never label) |

### 3.2 Verification flow

| # | Area | Finding | Tag |
|---|------|---------|-----|
| A6 | Verify a purchase credential | **Get Transaction Info**: `GET /inApps/v1/transactions/{transactionId}` returns the JWS-signed `signedTransactionInfo`; **Get All Subscription Statuses**: `GET /inApps/v1/subscriptions/{anyTransactionId}` (an original transaction id or order id) returns statuses grouped by subscription group; optional `status` query filter matches any of the given values, e.g. `?status=1&status=4` for active + billing grace period. `status` values: `1` Active, `4` In Billing Grace Period (both demonstrated by Apple's example); the field reference documents the full `1–5` enum (the others being Expired, In Billing Retry, Revoked) — pin all five in the adapter from the live reference. | VERIFIED FACT (endpoint + example); IMPLEMENTATION DECISION (map the full enum at Phase 3) |
| A7 | Signed transaction | Transaction info is a **JWSTransaction**: JWS Compact Serialization (header.payload.signature, Base64URL) signed by Apple (RFC 7515). Decoding the payload yields `JWSTransactionDecodedPayload`: `bundleId`, `environment` (`Sandbox`/`Production`), `productId`, `transactionId`, `originalTransactionId`, `purchaseDate`, `expiresDate`, `revocationDate` (absent until refunded), `inAppOwnershipType` (`PURCHASED`/`FAMILY_SHARED`), `subscriptionGroupIdentifier`, `webOrderLineItemId`, `type` (incl. `Auto-RenewableSubscription`), `transactionReason`. Apple explicitly warns **not to use `price`/`currency`** for revenue recognition. | VERIFIED FACT |
| A8 | Verify the adapter must do | Parse the JWS, verify Apple's signature (App Store public key / App Store Server Library `verifyAndDecodeTransaction`), THEN trust the payload. Never parse-then-trust. Confirm `bundleId` == ours, `transactionReason` is a purchase, `productId` maps through our catalog, `environment` matches the runtime, `originalTransactionId` is the stable external subscription id. | IMPLEMENTATION DECISION |
| A9 | user binding | Apple does not embed a rider id; equality must be established by the client-side `appAccountToken` (transaction payload `appAccountToken`), which the verify request must require to equal the caller's a stable rider id. Apple's own guidance: use `appAccountToken` to identify the app user at purchase time. | VERIFIED FACT (field exists) → IMPLEMENTATION DECISION (require token:rider equality at verification; reject mismatch cross-user) |

### 3.3 Lifecycle events, renewal, cancellation, refund, grace, retry

| # | Area | Finding | Tag |
|---|------|---------|-----|
| A10 | Renewal | Each auto-renewal is a **new transaction** with a new `transactionId`, same `originalTransactionId`, later `expiresDate`; `autoRenewStatus` (1 = renewing) and `renewalDate` live in `JWSRenewalInfoDecodedPayload` (`autoRenewProductId`, `autoRenewStatus`, `expirationIntent`, `gracePeriodExpiresDate`, `isInBillingRetryPeriod`, `priceIncreaseStatus`, `recentSubscriptionStartDate`, `renewalDate`, `originalTransactionId`). | VERIFIED FACT |
| A11 | Expired / canceled-but-active | A subscription a customer cancelled keeps access until its current `expiresDate` (`DID_CHANGE_RENEWAL_STATUS` / `AUTO_RENEW_DISABLED` does not revoke access). Access ends only on expiry. `EXPIRED` notification (`VOLUNTARY`, `BILLING_RETRY`, `PRICE_INCREASE`, `PRODUCT_NOT_FOR_SALE` subtypes) is the terminal event. Map to `CANCELED`/`expired` with `cancel_at_period_end` semantics rather than instant `REVOKED`. | VERIFIED FACT (notification table) → IMPLEMENTATION DECISION |
| A12 | Billing retry / grace | `DID_FAIL_TO_RENEW` enters billing retry (subtype `GRACE_PERIOD` when Billing Grace Period is enabled), `DID_RENEW` / `BILLING_RECOVERY` recovers it, `GRACE_PERIOD_EXPIRED` / `EXPIRED` / `BILLING_RETRY` ends it. During grace, product access continues. Maps to `PAST_DUE` (grace preserves entitlements through current period) and recovery back to `ACTIVE`. | VERIFIED FACT → IMPLEMENTATION DECISION |
| A13 | Refund / revocation | `REFUND` (with `revocationDate` on the transaction), `REFUND_REVERSED`, `REFUND_DECLINED`, `CONSUMPTION_REQUEST`. A refunded auto-renewable subscription stops being granted; map `REFUND` with `revocationDate` set → `REVOKED` terminal row (existing reconciliation already treats revoked as terminal). Family Sharing loss → `REVOKE`. | VERIFIED FACT → IMPLEMENTATION DECISION |
| A14 | Upgrades / downgrades | `DID_CHANGE_RENEWAL_PREF` with subtypes `UPGRADE`/`DOWNGRADE`; auto-renew product changes captured in `autoRenewProductId`. Single-plan catalog (PRO monthly/yearly) means cadence changes matter only for `renewalDate` mapping. | VERIFIED FACT |
| A15 | Webhook vs poll | **App Store Server Notifications v2** is the push channel: `responseBodyV2.signedPayload` is a JWS; decoded payload contains `notificationType`, `subtype`, `signedDate`, `environment`, `bundleId`, `data { signedTransactionInfo, (signedRenewalInfo) }`; every one of the three JWS strings is independently verifiable. Notification types: `SUBSCRIBED`(`INITIAL_BUY`/`RESUBSCRIBE`), `DID_RENEW`, `DID_FAIL_TO_RENEW`(`GRACE_PERIOD`), `DID_CHANGE_RENEWAL_STATUS`, `DID_CHANGE_RENEWAL_PREF`, `EXPIRED`(subtypes), `GRACE_PERIOD_EXPIRED`, `OFFER_REDEEMED`, `PRICE_INCREASE`, `REFUND*`, `REVOKE`, `RENEWAL_EXTENDED` (full table in the notificationType reference — pin `subtype` set in the adapter). | VERIFIED FACT |
| A16 | Notification auth | There is **no shared secret**: authenticity is the `signedPayload` JWS signature verified against Apple's public keys before any field is read. Then the payload's `bundleId` and `environment` are enforced, mirroring verification. | VERIFIED FACT → IMPLEMENTATION DECISION |
| A17 | Notification idempotency | Apple's v2 payload gives no opaque "message id"; the stable dedupe key is the **`transactionId`** (renewal: each renewal is a new transaction) plus `originalTransactionId` and `signedDate`. Re-verification of the same purchase must produce the same internal event id → existing duplicate/skew handling fires. **Implementation decision**: build the internal event id from `verify:app_store:<transactionId>:<originalTransactionId>:<signedDate>` (NOT from `occurred_at` alone, which would collide across distinct renewals at equal ms or across a re-fetch). | IMPLEMENTATION DECISION (the existing pipeline requires a stable event id; Apple's stable facts are transactionId/originalTransactionId/signedDate) |
| A18 | Environment safety | The **server decides**: `environment` comes from the verified JWS / notification payload (`Sandbox` vs `Production`), never from the client. Sandbox base URL is used by the adapter only when the server is a configured non-production runtime. Sandbox transactions on a production server are an open entitlement hole → existing `ENVIRONMENT_MISMATCH` rejection applies. | VERIFIED FACT → IMPLEMENTATION DECISION (keep existing gate in `_gate_environment`) |
| A19 | Sandbox testing | App Store sandbox (StoreKit testers, Xcode sandbox) is a fully separate environment with its own transactions and its own base URL. Adapters must be testable against **deterministic fixtures**, not live sandbox; live sandbox runs only where credentials are available and are reported honestly. | IMPLEMENTATION DECISION |
| A20 | Product ids | Apple product ids are strings configured in App Store Connect. The provisional `cyclecoach_pro_monthly` / `cyclecoach_pro_yearly` catalog ids are **not** registered and **cannot** be verified by the real API; real ids must be supplied by the account holder and then mapped through the catalog (never inline). | OPEN PRODUCT DECISION (real product ids + subscription group registration) |

---

## 4. Google Play

### 4.1 Provider, official API, auth

| # | Area | Finding | Tag |
|---|------|---------|-----|
| G1 | Provider | Play Console developer account; app + billing products configured; **Play Developer API** enabled in a Google Cloud project. | OPEN PRODUCT DECISION (account holder registers real products, package name, service account) |
| G2 | Official API | **Google Play Developer API** (Android Publisher, v3). Verification calls go from our server to `androidpublisher.googleapis.com`; the Play Billing SDK is only how the *device* obtains a purchase, and its verdicts are never authoritative on the server. | VERIFIED FACT |
| G3 | Auth | OAuth 2.0. **Service account is the recommended server-to-server method** (official Getting Started): create the service account in Cloud Console, enable the API, invite it in Play Console with *View financial data, orders, and cancellation survey responses* + *Manage orders and subscriptions*, then exchange a signed JWT for an access token (scope `https://www.googleapis.com/auth/androidpublisher`) and send `Authorization: Bearer <token>`. The standalone Authorization page documents the legacy OAuth web/refresh flow as the alternative. **Implementation decision: service account**, because it needs no interactive refresh and can be rotated. | VERIFIED FACT (both paths) → IMPLEMENTATION DECISION (service account) |
| G4 | Credentials | Service-account JSON key (private key). Never in logs/responses/metrics. Rotate via IAM. | IMPLEMENTATION DECISION |
| G5 | Version note | `purchases.subscriptions.get` is **deprecated**; `purchases.subscriptionsv2.get` is the current subscription endpoint. Any adapter written against `subscriptions.get` would be obsolete-on-arrival. | VERIFIED FACT |

### 4.2 Verification flow

| # | Area | Finding | Tag |
|---|------|---------|-----|
| G6 | Verify a purchase token | **`purchases.subscriptionsv2.get`**: `GET https://androidpublisher.googleapis.com/androidpublisher/v3/applications/{packageName}/purchases/subscriptionsv2/tokens/{token}` returns a `SubscriptionPurchaseV2` resource. No signature to parse — correctness is API authorization + your own field validation. | VERIFIED FACT |
| G7 | SubscriptionPurchaseV2 shape | Key fields: `subscriptionState`, `startTime`, `expiryTime`, `lineItems[]` (`productId`, `expiryTime`, `autoRenewingPlan.autoRenewEnabled`, offer phase), `latestOrderId`, `linkedPurchaseToken`, `testPurchase`, acknowledgement state, and contextual objects for paused / canceled / on-hold / in-grace / expired states (reason fields live in those contexts). `subscriptionState` enum: `UNSPECIFIED(0)`, `PENDING(1)`, `ACTIVE(2)`, `PAUSED(3)`, `IN_GRACE_PERIOD(4)`, `IN_ACCOUNT_HOLD(5)`, `EXPIRED(6)`, `ON_HOLD(7)`, `CANCELED(8)` — **pin the exact wires at Phase 4 from the live reference.** | VERIFIED FACT (structure + semantic state set) |
| G8 | Acknowledge | Subscriptions must be **acknowledged within 3 days** or Google refunds/revokes them. v2 exposes an acknowledge action (`.../purchases/subscriptionsv2/tokens/{token}:acknowledge`). Adapter responsed: acknowledge after a successful verified application, and track "acknowledged" per token. | VERIFIED FACT (Play Billing lifecycle requirement) |
| G9 | user binding | The API returns no rider id. The device must send a stable, server-issued purchase-owner identifier with the purchase (Play Billing `ObfuscatedAccountId`), which the verify request must compare against the caller. **Implementation decision**: mirror Apple's `appAccountToken` rule — require equality, reject cross-user. | VERIFIED FACT (obfuscated account id exists) → IMPLEMENTATION DECISION (require equality, reject mismatch) |
| G10 | Renewal token chaining | Renewals reuse the **same purchase token** for the subscription; `linkedPurchaseToken` links a resubscription to the previous token. The stable external subscription id in our schema is the **purchase token** string for the whole subscription lifetime; renewals update the period instead of appending rows. | IMPLEMENTATION DECISION |

### 4.3 Lifecycle, cancellation, refund, grace, hold, pause, test

| # | Area | Finding | Tag |
|---|------|---------|-----|
| G11 | Lifecycle via API | Encode `subscriptionState` + `lineItems[].expiryTime` + `autoRenewingPlan.autoRenewEnabled` + context reasons → existing `SubscriptionStatus`: ACTIVE→active, IN_GRACE_PERIOD→past_due(preferred) or active-with-grace (OPEN, see O1), ON_HOLD/PAUSED→past_due for dunning/account-hold style behavior, CANCELED keeps access until `expiryTime`, EXPIRED/voided→expired/revoked. | IMPLEMENTATION DECISION |
| G12 | RTDN | **Real-time Developer Notifications** arrive via **Google Cloud Pub/Sub** (push subscription → our HTTPS endpoint). `message.data` is Base64 JSON: `{ version, packageName, eventTimeMillis, (subscriptionNotification | oneTimeProductNotification | voidedPurchaseNotification | pendingRefundReviewNotification | testNotification) }`. `subscriptionNotification` = `{ version, notificationType (int), purchaseToken, subscriptionId }`, types `1 RECOVERED, 2 RENEWED, 3 CANCELED, 4 PURCHASED, 5 ON_HOLD, 6 IN_GRACE_PERIOD, 7 RESTARTED, 8 PRICE_CHANGE_CONFIRMED (deprecated), 9 DEFERRED, 10 PAUSED, 11 PAUSE_SCHEDULED, 12 REVOKED, 13 EXPIRED` (newer docs add price-change type 19 — pin against the live reference at Phase 10). | VERIFIED FACT (official reference; several mirrors agree) |
| G13 | RTDN auth | Pub/Sub push sends a signed **OIDC token** in `Authorization: Bearer <jwt>` (Google identity token for the subscription's service account). The endpoint must verify the token (issuer accounts.google.com, audience = our service account) AND that the `subscription` field names ours — then always **refetch** authoritative state via subscriptionsv2.get instead of trusting the payload. | VERIFIED FACT (Google Pub/Sub push auth documented behavior) → IMPLEMENTATION DECISION |
| G14 | Notification idempotency | Official Play Billing guidance: **check the uniqueness of `messageId` and ensure duplicate notifications are not processed** — this preserves API quota and prevents duplicate entitlement work. Our idempotency: dedupe on Pub/Sub `messageId` at the HTTP layer, and keep supplier-state application idempotent on the `purchaseToken` + event identity so a replay never double-writes. | VERIFIED FACT (messageId uniqueness requirement) → IMPLEMENTATION DECISION |
| G15 | Refund / void | `voidedPurchaseNotification` (productType: subscription / one-time; refundType) and the Voided Purchases pull API exist. A voided subscription stops granting → map to `REVOKED`/`EXPIRED`, terminal per existing logic. | VERIFIED FACT → IMPLEMENTATION DECISION |
| G16 | Grace / hold / pause | Play supports grace period and account hold features (per-context states `IN_GRACE_PERIOD`, `IN_ACCOUNT_HOLD`, `ON_HOLD`, `PAUSED` in subscriptionsv2). Deciding exactly which commercial state each maps to is listed as an OPEN PRODUCT DECISION (O2) because it changes the dunning UX, not the security posture. | VERIFIED FACT (fields exist) → OPEN PRODUCT DECISION (exact mapping) |
| G17 | Environment / testing | Google has **no separate "sandbox" API**: the same `subscriptionsv2.get` serves all purchases. Test purchases (test tracks, license testers, internal testing) are distinguished by `testPurchase` and by the package name/track used. Environment safety therefore means: enforce the configured `packageName`, refuse `testPurchase` on a production runtime, and never let the client declare the environment. | VERIFIED FACT (test infrastructure) → IMPLEMENTATION DECISION (server-side `packageName` + `testPurchase` gating; keep the existing environment enum) |
| G18 | Product ids | Play product ids are strings set in Play Console (the reference's `lineItems[].productId`). The provisional catalog ids are not registered and will not exist in Play. Real ids must be mapped through the catalog, never inline. | OPEN PRODUCT DECISION (real Play product ids) |

---

## 5. Cross-provider facts that decide the implementation

| # | Fact | Consequence for CycleCoach | Tag |
|---|------|---------------------------|-----|
| X1 | Neither provider returns a durable rider id in the verified purchase; both support a client-supplied opaque account token at purchase time (Apple `appAccountToken`, Play obfuscated account id) that the server controls. | The mobile app must stamp the **server-issued user id** into the purchase at buy time, and the adapter must compare it to the caller — otherwise cross-user replay is undetectable later. This is an extension of the existing `expected_user_id` contract: verification must now also read a token:rider *match inside the provider payload*. | IMPLEMENTATION DECISION |
| X2 | Both providers key subscription identity by stable strings we can store in `provider_subscription_id` (Apple `originalTransactionId`, Play purchase token). Renewals keep that identity; only the period moves. | Existing `Subscription`/`Entitlement` reconciliation (upsert by provider+external id, period rewrite) is correct as-is. No new subscription table needed for these two providers. | VERIFIED FACT → IMPLEMENTATION DECISION |
| X3 | Both providers gate environment server-side (Apple `environment` + separate base URL; Google `packageName` + `testPurchase`). Neither trusts a client claim. | Existing `VerificationEnvironment` and `_gate_environment` stand. Adapt to `testPurchase`+`packageName` for Play, `environment` for Apple. | IMPLEMENTATION DECISION |
| X4 | Both providers' push channels are authenticated (Apple JWS signedPayload; Google OIDC Pub/Sub push) and both require *refetch authoritative state* patterns for trust (Apple: verify JWS then trust; Google: verify OIDC then refetch). | The webhook foundation (Phase 10) must do signature/OIDC verification before parse, then call the same adapter verification paths rather than trusting the event body. Idempotency keys: Apple transaction/original-transaction ids; Google Pub/Sub `messageId`. | IMPLEMENTATION DECISION |
| X5 | Apple `price`/`currency` are explicitly not for revenue use; Play API returns no authoritative price either. | Keep the catalog price-less (existing decision). No price handling in adapters. | VERIFIED FACT |
| X6 | Real verification requires real registered products and credentials. | Until the account holder provides them, the registry must stay **empty in shipped config** so `PROVIDER_UNAVAILABLE` remains the honest result. Adapters register only when configured+enabled+validated. | IMPLEMENTATION DECISION |

## 6. Open product decisions

- **O1** — Apple grace-period mapping: should CycleCoach treat in-grace as `PAST_DUE` (entitlements preserved through current period — current semantics) or as `ACTIVE` until the grace period expires? Both are defensible; product decides.
- **O2** — Google account-hold/pause mapping: `IN_ACCOUNT_HOLD`, `ON_HOLD`, and `PAUSED` will each surface to the rider as either `PAST_DUE` or a new surface. Product decides whether dunning/clients need a distinct state.
- **O3** — Real catalog: registered App Store product id(s) + subscription group, Play product id(s), each mapped in `STORE_PRODUCTS` (currently `cyclecoach_pro_monthly`/`cyclecoach_pro_yearly`, provisional and not registerable in either store until replaced).
- **O4** — Bundle id / package name values the server must enforce (`bid` for Apple, `packageName` for Play), to be supplied per environment.
- **O5** — Credential provisioning & rotation: who holds issu/KID/.p8 (Apple) and the service-account JSON (Google) in each environment; rotation schedule.
- **O6** — Whether the Phase 10 notification foundation should use the existing provider event table (add a webhook journal only if genuinely needed) **before or after** the adapters ship. Spec default: minimal secure webhook endpoint + documented remainder.
- **O7** — Acknowledgement policy for late/offline verification (Google 3-day acknowledge requirement) — synchronous during verify, or a retry sweep in a future worker.

## 7. Enrollment = what Phase 1 requires

1. Map `appAccountToken` / obfuscated account id flows onto the mobile purchase-state machine (Phase 14) so every purchase carries a server-stamped rider id.
2. Extend the adapter result with provider-native subscription identity + environment and the token:rider proof (smallest change to `VerifiedPurchase`).
3. Add fail-closed registration gated on config; keep the empty-registry test green in environments without credentials.
4. Keep both providers' event identity in the deterministic provider event id so the existing duplicate/ordering machinery is reused, not re-invented.
5. Write provider contract tests against deterministic fixtures (Apple signed JWS payloads built with the App Store public test key; Google fake HTTP client returning pinned `SubscriptionPurchaseV2` bodies) so CI never depends on live stores.

---

## 8. Phase 1 — mapping providers onto the existing architecture

### 8.1 The pipeline already fits

Every step of `process_purchase` has a concrete Apple and Google destination, so
the trust boundary is preserved without a second verification system:

| Existing step | Apple | Google |
|---|---|---|
| provider name | `app_store` (enum exists) | `play_store` (enum exists) |
| catalog check | `plan_for_product(product_id)` | `plan_for_product(product_id)` |
| `verify_purchase(purchase_token, expected_user_id)` | token = StoreKit **transaction id** (or original transaction id / order id); adapter calls App Store Server API, verifies the returned JWS, then checks `bundleId`, `productId`, `environment`, and `appAccountToken == expected_user_id` | token = Play **purchase token**; adapter calls `purchases.subscriptionsv2.get`, then checks `packageName`, `lineItems[].productId`, `testPurchase`/track, and obfuscated account id == expected_user_id; acknowledges when it applies a grant |
| product/plan bind | verified product == requested; catalog plan == verified plan (unchanged) | identical |
| environment gate | `environment` from the JWS (`Sandbox`/`Production`) → `VerificationEnvironment` | `testPurchase` + enforced `packageName` → `SANDBOX`/`PRODUCTION` (Google has no separate sandbox API) |
| external subscription identity | `originalTransactionId` → `provider_subscription_id` | **purchase token** → `provider_subscription_id` (stable across renewals; renewals only extend the period) |
| event identity | per-transaction `transactionId` + `signedDate` | per-state timestamp (renewal → `expiryTime`, cancel → context time, expiry → `expiredAt`) |
| apply | `apply_provider_event` (unchanged) | identical |

### 8.2 Commercial state mapping

| Provider value | `SubscriptionStatus` | Entitlement effect |
|---|---|---|
| Apple status `1` / `SUBSCRIBED` / `DID_RENEW` | `active` (intro → trialing stays `active` for now) | grants through `expiresDate` |
| Apple status `4` grace / `DID_FAIL_TO_RENEW` | `past_due` | preserves through current period |
| Apple status `3` billing retry | `past_due` | preserves through current period |
| Apple `DID_CHANGE_RENEWAL_STATUS` (auto-renew off) | `canceled`, `cancel_at_period_end=True` | active until current `expiresDate` |
| Apple `EXPIRED` (any subtype) | `expired` | none |
| Apple `REFUND` w/ `revocationDate` / `REVOKE` | `revoked` | terminal |
| Google `ACTIVE` (`RENEWED`/`RECOVERED`/`RESTARTED`/`PURCHASED`) | `active` | grants through `lineItems[].expiryTime` |
| Google `IN_GRACE_PERIOD` | `past_due` (pending O1) | preserves |
| Google `ON_HOLD` / `IN_ACCOUNT_HOLD` / `PAUSED` | `past_due` boundary, pending O2 | dunning; account hold is a revoke-at-now signal, pause is revoke-at-schedule |
| Google `CANCELED` | `canceled` + `cancel_at_period_end=True` while `expiryTime` is future | active until period end |
| Google `EXPIRED` / voided | `expired` / `revoked` | none |

Only `originalTransactionId` (Apple) and the purchase token (Google) are stored
in `Subscription.provider_subscription_id`. No new subscription table; renewal
extends the same row, exactly what `apply_provider_event` already does.

### 8.3 The single interface extension (backward-compatible)

`VerifiedPurchase` gains one optional field:

```
provider_transaction_id: str | None = None
```

- Apple: the signing `transactionId` of the specific transaction.
- Google: a per-state stamp (the anchor timestamp that changed, or empty when
  the state has no distinct id).
- Defaults to `None`, so every existing constructor, fake, and test is
  unaffected and continues to exercise the legacy event identity.

The derived provider event id changes from the readable
`verify:<provider>:<subscription>:<occurred_at>` to a **bounded digest**:

```
verify:<provider>:<hex-40 sha256 of "subscription|transaction|occurred_at">
```

Why: (a) two distinct Apple renewals can share an millisecond `signedDate`, so a
hyphen-less `occurred_at` only id is collision-prone; (b) the readable form can
exceed `Subscription.last_provider_event_id String(128)` once a real
`transactionId` is added; (c) determinism is preserved — identical provider
state produces the identical id (duplicate-ignored), a changed state produces a
new id ordered by `occurred_at`. This is a deliberate, documented change to the
WS-PV §3 event-id description; no existing behavior test reads the literal id
string.

### 8.4 Deliberately unchanged

- No `is_pro` on `User`, no client authority, no price/currency, no second
  subscription system.
- Restore stays re-verification; verify/restore keep auth + 30/h limit.
- Empty registry stays the honest result wherever credentials are unconfigured.
- **No migration yet** — but one schema risk is recorded: Play purchase tokens
  may exceed `String(128)`. If a real token does (confirmed at Phase 4 with a
  real fixture from `subscriptionsv2.get`), that is the genuine Phase 18
  migration. A new provider table is not needed for either store.
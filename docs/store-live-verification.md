# CycleCoach — Store Live Sandbox / Test-Environment Verification (WS-RP Phase 6)

Status: **BLOCKED on live-store access.** The fixture/CI gates are green
(**full suite 1216 passed, 0 failed, 0 errors**; ruff/mypy/migration clean);
the live portions of this runbook require real Apple App Store Connect and
Google Play Console access, plus a public HTTPS endpoint, none of which are
provisioned yet. This document is the complete runbook and the record of
what is verified.

## Labeling convention

Every factual statement below carries a label:

| Label | Meaning |
|---|---|
| **VERIFIED LIVE** | Proven against real provider infrastructure in this session. |
| **FIXTURE-VERIFIED** | Proven by the automated test suite (fixtures + mocked providers), not live. |
| **IMPLEMENTATION DECISION** | A deliberate design choice in the code (not a store fact). |
| **OPEN DEPLOYMENT DECISION** | Requires an operator/account-holder to decide, configure, or provide. |

Do not treat any **FIXTURE-VERIFIED** or **IMPLEMENTATION DECISION** item as a
live or production claim.

---

## 1. Where we are

- `docs/store-provider-audit.md` — the store-facts audit (billing claims are
  out of scope; treat as provider documentation, not verified facts).
- `docs/purchase-providers.md` — Phase 3–5 implementation, adapters,
  verification pipeline, and webhook endpoints (all **FIXTURE-VERIFIED**).
- `docs/purchase-verification.md` — the verification flow (Phase 4).
- This document — Phase 6 live sandbox verification.

### Phase 6 objective (restated)

Move from "fixture-verified" to "live sandbox/test-environment verified"
using REAL provider test infrastructure, without touching production
purchases, without exposing real credentials, and without weakening
production security.

### Current session result

- Automated suite and gates: **re-run green** (see §13).
- Live purchases, real RTDN delivery, live signature/OIDC verification,
  real Webhook-to-store delivery: **NOT performed** — blocked (see §14).
- Public-infrastructure reachability probe: permissive read-only
  (see §15) — public key infrastructure only, not a purchase.

---

## 2. Products required

**OPEN DEPLOYMENT DECISION** — the account holder must register store-side
products and bind them in the server catalog. The internal provisional ids
are:

| Internal id (`store_products.py`) | Plan |
|---|---|
| `cyclecoach_pro_monthly` | PRO |
| `cyclecoach_pro_yearly` | PRO |

- **FIXTURE-VERIFIED** — `STORE_PRODUCTS` maps both to `Plan.PRO`; the
  `ProviderProduct` registry (`PROVIDER_PRODUCTS`) is deliberately EMPTY in
  every shipped config. Unknown provider product ids resolve to nothing
  (fail closed, `provider_product_for`).
- **OPEN DEPLOYMENT DECISION** — a real store product must be created in each
  store and then registered server-side via `register_provider_product(...)`
  with the exact provider-side product id string. Until that happens, NO live
  purchase can grant entitlement, by design.

### Verification checklists used by the harness

The harness below checks, per provider, the exact numbered items from the
phase request (product exists, id matches catalog, bundle/package matches,
environment recognized, purchase succeeds, real token received, token fits
1024, webhook arrives, signature verified, event normalized, pipeline
executes, subscription+entitlement created, duplicates idempotent).

---

## 3. Apple sandbox setup (runbook)

**OPEN DEPLOYMENT DECISION** — requires an Apple Developer Program account and
App Store Connect access. All steps below are the account-holder's; none are
automated by this repository.

1. In App Store Connect → "My Apps" → create (or use) the CycleCoach test app.
2. Record the **bundle id** (`APPLE_BUNDLE_ID`), e.g. `com.cyclecoach.app`.
   **IMPLEMENTATION DECISION** — the adapter enforces
   `bundleId == APPLE_BUNDLE_ID` from the JWS payload; a foreign bundle is
   rejected 422. **FIXTURE-VERIFIED**.
3. App Store Connect → Users and Access → Integrations → App Store Connect
   API: generate an API key (`.p8`, ES256). The visible info is Issuer ID
   (`APPLE_ISSUER_ID`) and Key ID (`APPLE_KEY_ID`). **IMPLEMENTATION
   DECISION** — the `.p8` private key is used only to sign the caller JWT in
   memory; never logged, echoed, or committed.
4. Apple App Store Connect → In-App Purchases: create a **subscription
   product** with an identifier equal to the catalog-bound provider product
   id (e.g. `cyclecoach_pro_monthly`). Set a test price tier and the
   auto-renewable subscription details. **OPEN DEPLOYMENT DECISION** the
   identifier string, when registered server-side, MUST be byte-identical to
   the store value.
5. Apple App Store Connect → Users and Access → Sandbox: add **testers**
   (at least one Apple ID per tester; see §8 test accounts). Sandbox testers
   can only make purchases on physical devices / the StoreKit testing in Xcode
   — not in a normal non-test flow, and **IMPLEMENTATION DECISION** the server
   must never rely on a client-declared environment flag.
6. On a physical iOS device signed into the sandbox tester Apple ID: install
   the build, open the paywall, buy `cyclecoach_pro_monthly`. The purchase
   exchange talks to **Sandbox** (not production). **VERIFIED LIVE — 0**
   (not yet; requires the sandbox purchase to be performed).

### Apple: server-required environment

| Variable | Meaning | Example |
|---|---|---|
| `APPLE_STORE_ENABLED` | adapter on | `true` |
| `APPLE_API_ENVIRONMENT` | which Server API the SERVER calls | `sandbox` for live sandbox |
| `APPLE_BUNDLE_ID` | the enforced bundle | `com.cyclecoach.app` |
| `APPLE_ISSUER_ID` | App Store Connect API issuer | from the API key page |
| `APPLE_KEY_ID` | App Store Connect API key id | from the API key page |
| `APPLE_PRIVATE_KEY_PATH` | path to `.p8` (outside Git) | `/run/secrets/apple.p8` |
| `APPLE_ROOT_CERTS_PATH` | Apple root-cert PEM bundle | `/run/secrets/apple-roots.pem` |
| `APPLE_WEBHOOKS_ENABLED` | accept server notifications | `true` |

**IMPLEMENTATION DECISION** — `APPLE_API_ENVIRONMENT` picks the base URL
(`api.storekit-sandbox.apple.com` vs `api.storekit.apple.com`); the purchase's
own `environment` field is what decides eligibility, never the client.
**FIXTURE-VERIFIED**.

### Apple sandbox behavior to expect (limits)

- Renewals: sandbox subscriptions renew quickly (a few minutes per period, per
  Apple's documented timelines) and can auto-renew; cancellations/refunds are
  partially available via the sandbox tester UI and StoreKit test transactions.
- `originalTransactionId`, `transactionId` are numeric strings → fit
  VARCHAR(1024) trivially. **FIXTURE-VERIFIED** (schema type in §12).
- The x5c chain in sandbox notifications is signed by Apple's sandbox/root
  certificates. The adapter verifies ES256 + x5c to the configured root.

---

## 4. Google Play test-track setup (runbook)

**OPEN DEPLOYMENT DECISION** — requires Google Play Console access.

1. Google Play Console → app → Testing → Open/Closed/Internal test track:
   add the CycleCoach build. License testers must be added to the test track
   (they pay nothing).
2. Record the **package name** (`GOOGLE_PACKAGE_NAME`), e.g.
   `com.cyclecoach.app`. **IMPLEMENTATION DECISION** — the adapter rejects a
   response whose `packageName` != configured. **FIXTURE-VERIFIED**.
3. Play Console → Monetize → Products → Subscriptions: create the
   subscription product (identifier byte-identical to the catalog-bound
   provider product id, e.g. `cyclecoach_pro_monthly`).
4. Play Console → API access (linked Google Cloud project) → service
   account: create/download the **service-account JSON** (type
   `service_account`). Grant it the **Android Publisher** role so
   `purchases.subscriptionsv2.get` works. Store outside Git
   (`GOOGLE_APPLICATION_CREDENTIALS_PATH`).
5. Pub/Sub: create a topic + push subscription whose push endpoint is the
   public HTTPS URL `.../api/v1/webhooks/google`. Enable the
   **OIDC-token push** (Pub/Sub push endpoint auth) with audience equal to the
   subscription URL. **IMPLEMENTATION DECISION** — the server requires
   `GOOGLE_RTDN_AUTH_MODE=oidc` in production; `none` is development-only.
   **FIXTURE-VERIFIED** (`test_production_config.py`).
6. On the test-track device signed into the license test account: install from
   the test track, buy `cyclecoach_pro_monthly` (test purchase, no real
   charge). This yields the **real purchase token**. **VERIFIED LIVE — 0**
   (not yet).

### Google: server-required environment

| Variable | Meaning | Example |
|---|---|---|
| `GOOGLE_PLAY_ENABLED` | adapter on | `true` |
| `GOOGLE_PACKAGE_NAME` | enforced package | `com.cyclecoach.app` |
| `GOOGLE_APPLICATION_CREDENTIALS_PATH` | service-account JSON (outside Git) | `/run/secrets/gplay.json` |
| `GOOGLE_WEBHOOKS_ENABLED` | accept RTDN push | `true` |
| `GOOGLE_RTDN_AUTH_MODE` | `oidc` (production-required) | `oidc` |
| `GOOGLE_RTDN_OIDC_AUDIENCE` | Pub/Sub subscription URL | `projects/<p>/subscriptions/<s>` |
| `GOOGLE_RTDN_OIDC_ISSUER` | expected `iss` | `https://accounts.google.com` |
| `GOOGLE_RTDN_JWKS_URI` | Google JWKS | `https://www.googleapis.com/oauth2/v3/certs` |

**IMPLEMENTATION DECISION** — the purchase token IS the stable subscription
identity (`provider_subscription_id`); RTDN notification fields are NEVER
authoritative; the adapter always re-fetches `subscriptionsv2.get` for the
token in the envelope. **FIXTURE-VERIFIED**.

### Google sandbox behavior to expect

- `testPurchase=true` in the v2 response → environment SANDBOX.
  **FIXTURE-VERIFIED**.
- Purchase tokens are opaque base64-ish strings, typically < 512 chars in
  practice, but **OPEN DEPLOYMENT DECISION** — the true length must be
  recorded live; the schema wide column (1024) exists so truncation is
  impossible. If a live token ever exceeds 1024 characters, this runbook's
  rule is: **STOP, do not truncate, widen the column in a new migration**.
- Google test tracks do NOT drive real-time "cancellation" lifecycle events
  the way production does; refunds/revocations may be simulated partially via
  Play Console or may be unavailable. **KNOWN SANDBOX LIMITATION** (see §11).

---

## 5. Webhook URLs, DNS, TLS (Phase 6 §4)

**OPEN DEPLOYMENT DECISION** — the store pushes must reach the deployed test
endpoint over public HTTPS. Exact URLs configured in the stores:

- Apple (App Store Connect → App Store Server Notifications V2 URL):
  `https://<public-host>/api/v1/webhooks/apple`
- Google (Pub/Sub push subscription endpoint):
  `https://<public-host>/api/v1/webhooks/google`

Requirements (hard):

- TLS valid, terminated at a public edge; **no HTTP fallback**, **no disabled
  certificate verification**, **no insecure tunnel config that would be reused
  for production**.
- **FIXTURE-VERIFIED** — the route code itself does not force either transport
  (plain ASGI); TLS is an operational property of the deployment. The
  production config gate refuses to boot on insecure defaults (`config` checks
  #4/#5: CORS https-only, log level, DB url, etc.); nothing in the app turns
  off cert verification.
- The route logic (rate limit → config gates → auth → provider lookup →
  capped body → parse → adapter) is **FIXTURE-VERIFIED** for both endpoints.

---

## 6. Apple signed-notification verification path (Phase 6 §5)

The live test must prove the full chain:

```
signedPayload
  → JWS parse
  → x5c extraction
  → certificate chain
  → configured Apple root
  → ES256 signature verification
  → trusted notification
```

- **FIXTURE-VERIFIED** — `AppleStoreProvider.process_event` performs exactly
  this chain (JWS + x5c + root anchor + ES256 + `bundleId`/`environment`/
  product checks) before emitting an event; `test_apple_store_provider.py`
  and `tests/test_webhooks.py` cover good, tampered, wrong-root, wrong-bundle,
  expired-cert, and malformed-JWS cases with fixture AppleSigner tokens.
- **VERIFIED LIVE — 0** — a real sandbox `signedPayload` has not been
  observed in this environment; this exact chain must be re-proven with the
  real store push once infra exists.

## 7. Google OIDC verification path (Phase 6 §6)

```
Pub/Sub
  → Authorization: Bearer <OIDC JWT>
  → JWT signature
  → Google JWKS
  → issuer validation
  → audience validation
  → accepted
```

- **FIXTURE-VERIFIED** — `GoogleOidcPushVerifier` verifies the OIDC
  push token against the configured JWKS, enforces `aud` and `iss`, and
  bounds RSA key size (1024–16384 bits); tests cover a valid signed token,
  wrong issuer, wrong audience, insecure algorithm, unknown kid, malformed
  JWK, expired token. The verifier is module-singleton with a JWKS cache.
- **VERIFIED LIVE (public-key path only)** — Google's public JWKS URI
  (`https://www.googleapis.com/oauth2/v3/certs`) returned HTTP 200 with 2
  RS256 RSA keys whose `n` lengths (342 hex chars ≈ 1368 bits) fall inside
  the verifier's allowed key-size bounds, so the live key set is parseable
  by the production check. No Google-issued OIDC push token was available to
  verify against those keys, so end-to-end OIDC token acceptance is NOT yet
  live-verified.
- There is no unauthenticated fallback: `GOOGLE_RTDN_AUTH_MODE=none` is a
  development setting refused by the production gate.

---

## 8. Test accounts

- **Apple**: one or more Sandbox testers (Apple IDs added under Users and
  Access → Sandbox). Tester may buy any sandbox-priced product. Device must be
  signed out of the production App Store account before sandbox purchase
  (Apple device policy).
- **Google**: license testers on the selected test track (Open testing makes
  them external; Closed/Internal makes them internal). Testers buy the test
  product without charge; adding a tester's email to the track is the grant.
- **Impact on the app**: the purchase is linked to the tester's account.
  The server sees the store purchase token/user UUID binding and maps it to a
  CycleCoach user (`externalAccountIdentifiers.obfuscatedExternalAccountId`);
  the test user must log in to the app with a CycleCoach account whose UUID
  matches the app-account-token / obfuscated id.

**VERIFIED LIVE — 0** — no testers/accounts created in this session.

---

## 9. Purchase → entitlement E2E (Phase 6 §7)

The behavior the live test must demonstrate (all pipeline steps already
**FIXTURE-VERIFIED** in `test_purchase_verification.py`,
`test_subscriptions_entitlements.py`, `test_webhooks.py`):

| Step | Fixture status | Live status |
|---|---|---|
| Before: no subscription/entitlement | FIXTURE-VERIFIED | VERIFIED LIVE — 0 |
| Provider confirms purchase | FIXTURE-VERIFIED | 0 |
| Server verification succeeds | FIXTURE-VERIFIED | 0 |
| Webhook arrives | FIXTURE-VERIFIED | 0 |
| `apply_provider_event` | FIXTURE-VERIFIED | 0 |
| Subscription active, entitlement active | FIXTURE-VERIFIED | 0 |
| Pro feature unlocks for the client | FIXTURE-VERIFIED (entitlement source row + `GET /subscriptions/plan`) | 0 |

**IMPLEMENTATION DECISION** — the webhook and the verification route call the
same `apply_provider_event`; there is exactly one subscription system.

---

## 10. Duplicate, out-of-order, cancellation, expiration (Phase 6 §8–11)

- **Duplicate delivery**: same event twice/triple, plus duplication after a
  restart. **FIXTURE-VERIFIED** — deterministic event digest + duplicate-ignore
  (`uq_entitlements_subscription_feature`, advisor lock) tested in
  `test_subscriptions_entitlements.py` and `test_webhooks.py`; a duplicate does
  not double-apply or corrupt state. **VERIFIED LIVE — 0** (needs real pushes,
  or natural macOS/iOS sandbox retries to observe).
- **Out-of-order**: equal timestamp + different event ids must BOTH apply
  (not arbitrary discard). **FIXTURE-VERIFIED** — `_stale()` ordering pin is
  tested for both Apple and Google, both orders. **VERIFIED LIVE — 0**.
- **Cancellation / auto-renew-off**: `cancel_at_period_end` must be
  represented while the paid period stays active (no immediate revocation if
  expiry is future). **FIXTURE-VERIFIED** (Apple: `AUTO_RENEW_DISABLED`
  subtype; Google: `cancel_at_period_end` from v2 refetch). **VERIFIED
  LIVE — 0** (sandbox may not exercise it fully).
- **Expiration / revocation**: transition driven by provider state → webhook →
  refetch → entitlement change; no client assertion can cause the transition.
  **FIXTURE-VERIFIED** for EXPIRED/REVOKED/REFUND. **VERIFIED LIVE — 0**
  (Google test tracks do not reliably drive revocation events).

---

## 11. Known sandbox limitations

- Apple sandbox testers cannot clear In-App Purchases history except by
  removing the tester; periodic renewals are fast and may fire many
  notifications.
- Apple sandbox does NOT reliably deliver refund/revocation notifications.
- Google test tracks do not drive cancel/refund/revocation lifecycle reliably;
  and `testPurchase` pushes are rare/not always emitted.
- Store-side product registration may lag code changes; byte-identical ids are
  only guaranteed by the operator.
- Real purchase tokens / payloads may contain large cert data and JSON; the
  webhook body cap is 1 MiB (**FIXTURE-VERIFIED**), which stores' real
  envelopes fit in practice — to be re-measured live.

---

## 12. Real purchase-token storage test (Phase 6 §3 — mandatory live)

Procedure once a real Google purchase token exists:

1. Record the token's exact length.
2. Assert `len(token) <= 1024`. **IMPLEMENTATION DECISION** — the schema
   column is `provider_subscription_id VARCHAR(1024)` (migration
   `0015_store_webhooks`), and the model `_require_text` limit is 1024.
   Truncation is not allowed anywhere. **FIXTURE-VERIFIED**.
3. Insert via the real verification/webhook path; SELECT it back and assert
   byte-identical; assert verification still succeeds; assert the event is
   idempotent on a second delivery.
4. If length > 1024: **STOP**. Never silently alter behavior. Create a
   migration widening the column to a justified safe size and adjust the
   model limit, then re-run the whole ladder (`test_migrations.py`).

---

## 13. Automated gates (this session — re-run)

Run all of these serially, no parallel pytest:

```
ruff format --check <changed files>   # excludes baseline alembic files by design
ruff check app tests alembic
mypy app
pytest tests/test_migrations.py       # upgrade -> downgrade to base -> upgrade to head
pytest -q --no-header -p no:cacheprovider   # FULL suite, single process
```

Session results:

- ruff format/check: **clean** (22 changed files formatted; all checks pass).
- mypy: **Success, no issues, 112 files**.
- migration ladder: **2 passed** (upgrade → downgrade to base → re-upgrade to
  head; `provider_subscription_id` verified VARCHAR(1024)).
- full suite (Phase 6 gate, single serial process): **1216 passed, 0 failed,
  0 errors in 33:11** after a clean Postgres/Redis container restart. Earlier
  serial attempts showed Windows `ConnectionResetError [WinError 64]` /
  `Could not refresh instance` DB flakes while the 5-day-old WSL-relayed
  containers and a stale dev server (PID 464) were up; the affected tests
  passed individually once the stale server was stopped, and the entire suite
  passed cleanly after Postgres/Redis were restarted (root cause: WSL TCP
  relay connection drops under sustained churn, not code).

---

## 14. Blockers (exact)

1. **No Apple App Store Connect access / API keys / sandbox app / tester** —
   nothing to buy on, nothing to sign with, no real `.p8` to exercise the
   caller-JWT and x5c chain.
2. **No Google Play Console access / service account / test-track app /
   license testers** — no real purchase token, no real `subscriptionsv2.get`,
   no real RTDN.
3. **No public HTTPS endpoint / DNS / TLS** — Apple and Google pushes cannot
   reach the webhook routes; there is no tunnel to forward them.
4. **Provider product registry still empty** — the store-side products are not
   registered (`register_provider_product` never invoked with the real id), so
   even a live token would fail product binding by design.
5. **No test accounts of any kind** provisioned.

Status: **BLOCKED** — exact blockers above. Nothing in this phase is a
production claim; the code is only **FIXTURE-VERIFIED** + the runbook's
partial public-infrastructure probe below.

---

## 15. Public-infrastructure reachability probe (read-only, this session)

This is NOT purchase verification. It exercises read-only public endpoints
only (no credentials), to shake out deployment plumbing before real access
exists:

- Google OIDC JWKS (`https://www.googleapis.com/oauth2/v3/certs`) — HTTP 200,
  2 RS256 RSA keys fetched; `n` lengths (≈1368 bits) inside the verifier's
  1024–16384 bound, so the published live key set passes the production
  key-size check. No Google-issued OIDC token existed to complete a signature
  verification. **VERIFIED LIVE (key-path only).**
- Apple App Store Server API sandbox + production endpoints
  (`https://api.storekit-sandbox.apple.com`, `https://api.storekit.apple.com`)
  — reachable, TLS valid, HTTP 401 without credentials (authorization
  failure, not connectivity). No purchase, no signed payload. **VERIFIED LIVE
  (reachability only).**

Recorded as **VERIFIED LIVE (public-key/reachability only)**, NOT as
purchase/system live verification.

---

## 16. Cleanup procedure (once live verification runs)

- Remove the sandbox-created purchase states from the local dev database
  (`cyclecoach`/`cyclecoach_test`): delete the test user's `entitlements` and
  `subscriptions` rows (or recreate the scratch DB). **IMPLEMENTATION
  DECISION** — store rows are keyed to the user UUID; deleting the user
  cascades (FK CASCADE) — **FIXTURE-VERIFIED** (`test_migrations.py` asserts
  the CASCADE).
- Rotate/delete any `.p8` and service-account JSON that were placed on the
  test host after the run.
- Remove test track/testers and sandbox testers, or leave them if the project
  wants a persistent sandbox.
- Confirm `git status`, `git diff`, and `git ls-files` show no credential
  material (`.p8`, `*service-account*`, `credentials*.json`, `.env*`) — the
  repo `.gitignore` now matches those patterns.

---

## 17. Verified session facts (automated suite)

- Webhook fixtures: Apple x5c/JWS, wrong-root, wrong-bundle, wrong-env,
  unsupported-type; Google OIDC key/aud/iss failures; oversized body 413;
  unconfigured 503; duplicate events; equal-timestamp ordering. All green —
  final Phase 6 gate run: **1216 passed, 0 failed, 0 errors** (serial, single
  process). The only instability seen all session was the Windows
  `WinError 64` DB-connection drop while stale WSL-relayed containers / a
  stale dev server were running; a clean container restart eliminated it and
  the full suite then passed in 33:11 with no failures.
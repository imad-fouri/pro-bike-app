# 08 — Security Model

Auth: Argon2id passwords, JWT access (15m) + rotating refresh, secure WS handshake. AuthZ: owner/member/organizer/admin checks server-side on every endpoint + WS channel. Never trust client GPS/metrics blindly — validate ranges. Rate-limit auth/location/chat/GPX. GPX: size cap, schema validation, XXE-safe XML, virus-scan hook. PII/location encrypted in transit (TLS), at-rest encryption, audit log for location access + admin actions. Subscriptions verified via store webhooks. Abuse: chat report/block, location session caps.

## Phase 8.4 additions (ADR-15)

- **Push tokens are credentials, stored in plaintext by necessity.** A token must
  be *presented* to a provider, so unlike `refresh_hash` it cannot be hashed.
  Everything else about it is treated as a secret: no response schema has a token
  field on the way out (`PushDeviceOut` is built explicitly, not model-validated, so
  adding a column cannot expose one), `redact()` blanks it by name, and it is never
  logged, never placed in an exception message, and never included in a push
  payload.
- **Device ownership is server-derived from the JWT.** `PushDeviceRegister` has no
  `user_id` field at all; a client that supplied one gets a 422, so it cannot
  register a push target on someone else's account.
- **`UNIQUE(provider, token)` prevents cross-account push leakage.** Without it, a
  rider signing out of A and into B on one phone would leave A's row live and A
  would keep pushing to a device now showing B's notifications. A held token is
  therefore transferred to the new account, and if that account already holds the
  same physical device the two rows merge.
- **404, not 403, for another rider's notification or device**, byte-identical to a
  random uuid, with the row scoped in the WHERE clause rather than fetched and
  checked. No route is an existence oracle, and an IDOR attempt does not modify the
  victim's row.
- **A blocked DM produces no notification row for either party**, including on
  retry, so an unread badge cannot reveal that a blocked rider tried to write.
  Filtering on read would not be sufficient.
- **Push payloads carry a pointer, never content.** Only `notification_id`,
  `notification_type`, and `deep_link`. A push is rendered on a lock screen and
  mirrored to a paired watch, so nothing a recipient is not already entitled to
  read may appear in one; the client re-fetches through an authorized call on tap.
- **`params` holds public display strings only** — an actor display name, a team
  name. Never a message body, coordinate, email address, or token. Enforced as an
  invariant by the payload builder rather than by convention.
- **Deep links are allowlisted and UUID-validated client-side**, so a compromised
  or buggy server cannot use a notification to navigate to an arbitrary URL.
- **Localization keys are a storage contract.** A row references its key, so
  renaming one would break history on a rider's device.
- **Notification type names are not a capability.** The actor is never notified
  about their own action, and ride/training/AI types are deliberately absent
  because no authorization story exists for them yet.

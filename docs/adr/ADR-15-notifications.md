# ADR-15 — Push Notifications: Foundation, Provider-Neutral Seam, and the Notification Center

Status: accepted (Phase 8.4)
Date: 2026-10-03
Related: ADR-12 (social relationships & privacy), ADR-13 (teams, membership,
non-cascading blocks), ADR-14 (chat and the DM privacy boundary), ADR-08
(location), `docs/03-database-model.md`, `docs/04-api-map.md`,
`docs/06-realtime-social.md`

## 1. Context

Phases 8.1–8.3 produced a social graph, teams, and chat. Every one of those
surfaces emits an event that a rider on a bicycle would want to know about: a
friend request arrived, someone accepted, a team invitation, a member was
removed, a message landed in a DM or a team channel.

Until now nothing carried those events. The app had to be open and foregrounded
to see them, which is precisely when a rider is not looking.

The tempting design is to wire up FCM and APNs. That is the wrong first move,
for three reasons:

- It makes the business logic untestable. Anything requiring a vendor SDK,
  network, or credential is a test that cannot run in CI.
- It commits us to a vendor before the shape of the problem is settled.
- It obscures the part that actually matters. The hard questions in push are not
  transport questions; they are *privacy* questions. What is allowed to appear on
  a lock screen? Does a blocked rider's message generate a notification? Who may
  read whose notification list? None of those are answered by a transport SDK.

So Phase 8.4 builds the thing that decides those questions, and deliberately
does not build the transport.

## 2. Decision: a notification is a row; push is an accelerant

Two tables, `push_devices` and `notifications`. The row is the record; the push
is a shortcut to the notification center. Nothing is ever *only* a push.

This inversion is the single most load-bearing decision in the phase. It means:

- A rider with push disabled, on a platform with no provider, or on a flaky
  network still has a complete, correct notification history.
- Provider failure can never lose an event, because the row was written first.
- The privacy rules below are enforced in exactly one place — the code that
  decides whether a row may exist — rather than being re-implemented per
  provider.

Rejected: push-only delivery (no history, no fallback, untestable), and a
separate `notification_preferences` table (the project already keeps per-user
settings on `user_profiles`; a third mechanism for one row per user would be
inconsistent with that).

### 2.1 Content is a localization key, never rendered copy

A notification row stores `l10n_key` plus a small `params` map. It does not
store a title or a body.

The server cannot know which of EN/FR/AR the recipient reads, and a
server-rendered string can only ever be correct in one of them. Storing the key
means a rider who switches language sees their history in the new language, and
it means fixing a wording mistake is a client deploy rather than a backfill.

Keys are part of the storage contract: a row references its key, so renaming
one would break history already on a rider's device. They are derived from the
enum value (`notifications.type.<value>`) so they cannot drift from the type.

`params` holds only strings that are *already public* — an actor's display name,
a team's name. It must never hold a message body, a coordinate, an email
address, or a token. The client's deep-link parser and the payload builder both
treat this as an invariant rather than a convention.

## 3. Decision: device identity is a client-generated `device_id`

A device row is keyed on `UNIQUE(user_id, provider, device_id)`, where
`device_id` is a stable per-installation identifier the client generates and
persists.

The alternative — identifying a device by its push token — is wrong because FCM
and APNs both rotate tokens, sometimes without the app being involved. Using the
token as the identity would insert a new row on every rotation and orphan the
previous one forever. With `device_id` as the identity, rotation is an **UPDATE**,
and a rider accumulates one row per phone rather than one row per token lifetime.

The token column is nonetheless unique (`UNIQUE(provider, token)`), which is a
separate and equally important constraint: a token is a device's address, and
without cross-account uniqueness a rider who signs out of A and into B on one
phone would leave A's row live and A would keep pushing to a device now showing
B's notifications.

The interaction between these two constraints is the subtle part, and it is
handled in `_reconcile_token_clash`:

- A token already held by **another account** is a legitimate state — a phone
  handed to a new rider, or a provider reassigning an address. The row is
  **transferred**, not refused. Refusing would leave the previous rider
  receiving pushes on a device showing someone else's session, which is the exact
  leak the constraint exists to prevent.
- If the transferring rider **already holds a row for that same physical
  device**, the two rows must **merge**: the caller's freshly-registered row
  wins and the stale one is deleted. A token and a device can each belong to
  exactly one row, so one row has to go, and it is not the one just registered.

Both paths (fresh insert and re-activation) route through that one helper, and
both guard their commit. An early version guarded only the insert, which meant
rotating an existing device onto a held token raised an uncaught
`IntegrityError` — a 500. The live smoke caught it; see §8.

### 3.1 Tokens are stored in plaintext, and that is the point

A push token must be *presented* to a provider to be useful, so unlike
`refresh_hash` it cannot be hashed. Storing it in the clear is therefore forced
by the function, not a lapse.

Everything else about it is treated as a credential:

- No response schema has a token field on the way out. `PushDeviceOut` is built
  explicitly rather than by validating the model, so adding a column to
  `push_devices` cannot accidentally expose it.
- `redact()` blanks the column by name.
- It is never logged, never placed in an exception message, and never included
  in a push payload.
- Ownership is server-derived from the JWT. `PushDeviceRegister` has no `user_id`
  field at all, so a client cannot register a push target on someone else's
  account; a supplied one is a 422.

A device belonging to another rider answers **404, not 403**, and identically to
a guessed id — the WHERE clause is scoped by `user_id`, so existence is never
disclosed.

## 4. Decision: DM and team-channel messages are different notification types

`chat_message` and `chat_message_team` are separate enum values even though both
originate in the same table and both concern a message.

They are separate because their **authorization differs**, and merging them would
make the privacy rule unenforceable. A team-channel recipient is a live team
member. A DM recipient must *also* clear the block policy — a block that stopped
the message but left a notification saying "new message" would leak the fact that
someone tried to write, which is exactly what a rider who blocked someone asked
to stop.

### 4.1 The block policy, applied to notifications

**A blocked DM creates no notification row, for either party.**

Not a row that is filtered on read, and not a row that is stored but not
delivered — no row. The event never becomes a notification, so there is nothing
to leak through an unread badge, a push, or a future export.

Consequences that fall out of this, all verified by the smoke:

- A blocked send is refused **and** leaves no trace, including on retry. A retry
  that appears to succeed would otherwise be inferable.
- Unblocking restores DM notifications normally; nothing needs backfilling,
  because nothing was suppressed.
- **A block does not sever a team channel**, and team notifications still cross a
  block. This is ADR-13 §6 and ADR-14 §2.1 carrying into notifications: a block
  is a personal-interaction boundary, and broadcasting it into a shared space
  would tell every other member that someone had blocked someone — information
  the blocker never consented to share.
- A **removed** team member stops receiving team notifications, even if they can
  still read history.

### 4.2 The sender is never notified about their own action

Uniform across every emitted type: the actor's notification count does not move
when the actor performs the action. Emitting one would be noise, and would make
the unread badge a poor signal.

## 5. Decision: authorization is checked before the row exists

Notification reads are scoped by `recipient_user_id` in the WHERE clause, never
fetch-then-check. Another rider's notification id returns 404, byte-identical to
a random uuid, and the victim's row is not modified.

This is why a notification is not simply "a chat message with a flag": the
notification row is its own authorization boundary, and it has to be enforced
without reference to the chat table.

## 6. Decision: offset pagination, and a partial index for unread

History is offset-paged in the standard envelope (`items`, `total`, `page`,
`page_size`), with `page_size` capped.

Cursor pagination was considered and rejected: unlike message history, a
notification center is bounded and read deliberately, so pages do not shift
under the reader in practice, and the standard envelope gives the UI a total for
free.

The unread count is separated from the list endpoint because the app bar polls it
on every foreground. It is served by a partial index over unread rows only
(`WHERE read_at IS NULL`), so the count is an index-only scan that does not
degrade as history grows.

Idempotency is a **partial** unique index on `dedupe_key`. A plain unique
constraint would collide on repeated NULLs before PostgreSQL 15, so the
`WHERE dedupe_key IS NOT NULL` predicate is what lets system notices coexist with
deduplicated ones. Keys derive from an immutable business id — never a timestamp,
which would defeat deduplication entirely.

`actor_user_id` is `ON DELETE RESTRICT`, exactly like `messages.sender_user_id` in
0009: a notification must not vanish because its actor was removed.

## 7. Decision: the provider seam, and nothing behind it

`app/notifications/` mirrors `app/ai/provider.py`, which is this project's proven
answer to "one abstraction, several vendors, refuse to guess":

- a `Protocol`, so an implementation inherits nothing;
- a status → (exception, retryable) mapper, so retry policy is data rather than a
  chain of `if`s;
- a factory that never raises;
- a `set_provider` seam for tests.

**Only `FakePushProvider` is implemented.** `PushProvider` the enum names `fcm`
and `apns` so a device row can record which ecosystem issued a token; nothing in
this phase contacts either. No SDK, credential, or native configuration is
added. Adding a real provider later is a new class in this package and nothing
else.

`get_provider()` **never raises**. With nothing configured it returns
`UnconfiguredPushProvider`, which reports every device as `skipped`. That is the
normal state in CI, in review, and in production before a key is set — raising
would turn "push is not configured yet" into an error on every notification.

Retry policy is deliberately asymmetric: only `PushUnavailable` is retried. A
timeout is not, because the delivery may well have succeeded and a retry would
double-notify the rider. An invalid response is not, because the provider
answered and asking again identically cannot help.

### 7.1 The payload is a pointer, never a copy

`SAFE_PAYLOAD_KEYS` is exactly three: `notification_id`, `notification_type`,
`deep_link`. `PushMessage.safe_payload()` builds the map from those named fields
rather than from a free-form `data` dict, so a future field cannot widen the
payload by being added to the dataclass.

The reasoning is physical: a push lands on a lock screen, is mirrored to a paired
watch, and is visible to whoever is holding the phone. It therefore carries an id
and a route, and the client re-fetches through an authorized API call on tap.
Anything the recipient is not already entitled to read has no business being
rendered outside the app.

Deep links are allowlisted and UUID-based in the client, so a compromised or
buggy server cannot use a notification to navigate to an arbitrary URL.

## 8. What the live smoke found that the test suite could not

The suite's session factory sets `expire_on_commit=False`. Production does not.
Running against the real session configuration surfaced three defects that
551 passing tests could not:

1. **`register_device` read `user.id` after a commit.** The commit expires the
   ORM object, so reading an attribute afterwards is a lazy load outside a greenlet
   — a `MissingGreenlet` 500. Only reachable on the conflict path.
2. **The re-activation write was unguarded.** Rotating a device onto a token
   another account held raised an uncaught `IntegrityError` — a 500 — because only
   the insert path had a handler.
3. **The transfer collided with the caller's own device row.** Fixing (2)
   surfaced `uq_push_devices_user_device`, because transferring a token to a
   rider who already had that device produces two rows for one device. The merge
   rule in §3.1 exists because of this.

All three are now covered by regression tests, and the smoke asserts the
behaviour end-to-end across two runs against reused state.

## 9. Consequences

**Accepted:**

- Fan-out is synchronous and in-request. `PUSH_INLINE_FANOUT_LIMIT` (20) is the
  seam where a worker will take over; beyond it the row is written and delivery is
  deferred. Worker infrastructure is deliberately absent — it belongs with the
  Redis work, not here.
- Device rows are pruned after `PUSH_DEVICE_STALE_DAYS` (90), because reinstalls
  accumulate rows that are never revisited.
- Notification persistence and provider delivery are coupled in one call path. If
  a future worker changes the second, it must not change the first.

**Rejected, with reasons:**

- *Real FCM/APNs now* — the transport is the easy half; the privacy half is what
  this phase exists to get right, and it is testable only without it.
- *A push `title`/`body` rendered server-side* — see §2.1.
- *Per-type mute preferences* — a real product need, but it needs the transport
  to be useful and belongs with detailed preferences (FUTURE).
- *Notification deletion/archival* — unread state plus bounded history is enough
  for now; a retention policy is a product decision.

**FUTURE, explicitly not in this phase:** `FCMProvider`, `APNsProvider`, native
configuration, the delivery worker, `group_ride_*` / `training_reminder` /
`ai_coach_event` types, per-type preferences, and account deletion (which would
need to decide whether a deleted rider's notifications are deleted, tombstoned,
or retained — ADR-15 §9).

## 10. Where the absence of ride notifications is a decision

`NotificationType` deliberately omits `group_ride_invitation`,
`group_ride_reminder`, `ride_starting`, `training_reminder`, and
`ai_coach_event`. None is emitted.

Adding an enum value is cheap. Adding one with no authorization story is not: a
ride reminder has to decide who may be told a ride is starting, against which
ride session, and whether a rider who left the ride still receives it. Group
Rides are out of scope, so there is no session to authorize against. The absence
is recorded in the enum's docstring so a later phase has an obvious home for it.

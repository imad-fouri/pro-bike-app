# Phase 8.4 Report — Push Notifications Foundation

Status: complete
Date: 2026-10-03
ADR: `docs/adr/ADR-15-notifications.md`
Predecessor: Phase 8.3 (chat) — unchanged in behavior, one 500 fixed
Architecture audit: `docs/phase-8.4-architecture-audit.md`

## 1. What shipped

A notification **row** for every social and chat event, a device registry, a
notification center and settings UI in EN/FR/AR, and a provider seam with a fake
behind it.

**No push transport.** No FCM, no APNs, no SDK, no credential, no native
configuration. `PushProvider` the enum names both so a device row can record which
ecosystem issued a token; nothing in this phase contacts either. Adding a real
provider later is a new class in `app/notifications/` and nothing else.

The reasoning is in ADR-15 §7, but the short version: the transport is the easy
half. The hard questions — what may appear on a lock screen, does a blocked
rider's message produce a notification, who may read whose list — have no
transport dependency and are testable only if the transport is absent.

## 2. The decision everything else follows from

**The row is the record; push is an accelerant.**

A rider with push disabled, on a platform with no provider, or on a bad network
still has complete, correct history, and provider failure cannot lose an event
because the row is committed before delivery is attempted. It also means the
privacy rules have exactly one place to live: the code deciding whether a row may
exist.

## 3. Privacy outcomes

- **A blocked DM creates no notification row, for either party.** Not a row that
  is filtered on read — no row. A block that stopped the message but left a "new
  message" badge would leak that someone tried to write, which is what the rider
  asked to stop.
- **A refused blocked send leaves no trace, including on retry.** A retry that
  appeared to succeed would otherwise be inferable.
- **Unblocking restores DM notifications**, with nothing to backfill.
- **A block does not sever a team channel**, and team notifications still cross a
  block; a **removed** member stops receiving them. Carrying ADR-13 §6 and ADR-14
  §2.1 forward: a block is a personal-interaction boundary, and broadcasting it
  would tell every other member that someone had blocked someone.
- **The actor is never notified about their own action.**
- **Push payloads are pointers, never copies**: `notification_id`,
  `notification_type`, `deep_link`. A push renders on a lock screen and mirrors to
  a paired watch, so the client re-fetches through an authorized call on tap.
- **`params` holds public display strings only** — actor display name, team name.
  Never a message body, coordinate, email, or token.
- **404, not 403**, for another rider's notification or device, byte-identical to
  a guessed uuid, scoped in the WHERE clause.
- **Deep links are allowlisted and UUID-validated client-side**, so a compromised
  or buggy server cannot navigate a rider to an arbitrary URL.
- **Tokens are stored in plaintext by necessity** — a token must be *presented* to
  a provider, so unlike `refresh_hash` it cannot be hashed. They are never
  returned, never logged, and never in a payload. `PushDeviceOut` is built
  explicitly rather than model-validated, so adding a column cannot expose one.

## 4. Device identity

Keyed on a **client-generated `device_id`**, not on the token: FCM and APNs both
rotate tokens, sometimes without the app involved, so token-as-identity would
insert a row per rotation and orphan the previous one forever. Rotation becomes an
UPDATE.

`UNIQUE(provider, token)` is enforced separately and is equally load-bearing — it
is what stops the previous rider receiving pushes after a re-login. The two
constraints interact, and that interaction is the interesting part:

- A token held by **another account** is a legitimate state (a phone handed to a
  new rider). The row is **transferred**, not refused — refusing would leave the
  old account pushing to a device showing the new rider's session.
- If the caller **already holds that physical device**, the rows **merge**: the
  caller's freshly-registered row wins, the stale one is deleted. A token and a
  device can each belong to exactly one row.

## 5. Schema

Migration `0010_notifications`, additive after `0009_chat`.

- `push_devices` — `uq_push_devices_user_device`, `uq_push_devices_provider_token`,
  non-empty CHECKs, partial index on enabled devices. `user_id` CASCADE.
- `notifications` — partial UNIQUE `dedupe_key` (partial because a plain unique
  collides on repeated NULLs before PG 15), `(recipient_user_id, created_at DESC)`
  for history, **partial index on `read_at IS NULL`** so the polled unread count is
  an index-only scan that does not degrade as history grows.
- `actor_user_id` is **RESTRICT**, matching `messages.sender_user_id` in 0009: a
  notification must not vanish because its actor was removed.
- No `notification_preferences` table — per-user settings already live on
  `user_profiles`.
- No `title`/`body` columns — `l10n_key` + `params`, so history re-renders in the
  rider's current language.

Nine types emitted. `chat_message` and `chat_message_team` are separate *because
their authorization differs* — merging them would make the block policy
unenforceable.

Deliberately absent: `group_ride_*`, `training_reminder`, `ai_coach_event`. A ride
reminder would have to authorize against a ride session, and Group Rides are out of
scope. The absence is recorded in the enum docstring as a decision.

## 6. Flutter

- `notifications_test.dart` — 60 tests.
- Domain, repository, `PushRegistrar` seam, providers, notification center,
  settings page, widgets, routes, corrected home bell.
- Deep links allowlisted and UUID-validated, with a pending-intent queue so a
  notification that cold-starts the app still routes.
- `AppLocalizations` gained interpolation, a `tryX` form for parameterised strings,
  and a **safe fallback** for an unknown `l10n_key` — an unrecognized key renders
  a generic notice rather than throwing, because keys are a storage contract and
  an older client must survive a newer server.

## 7. Verification

| Gate | Result |
| --- | --- |
| `pytest tests` | **551 passed**, 1 warning |
| `pytest test_notifications_api.py` | 43 passed |
| `pytest test_chat_api.py` | 41 passed |
| `pytest test_notifications + test_chat + test_migrations` | 86 passed |
| `ruff check app tests alembic` | clean |
| `mypy app` | clean, 85 files |
| `alembic upgrade head` on a **virgin** database | clean, ends at `0010_notifications` |
| `flutter analyze` | clean |
| `dart format --set-exit-if-changed lib test` | clean, 125 files |
| `flutter test` | **428 passed** |
| `flutter build web --release` | succeeded |
| `live_smoke_notifications.py` | **101/101 across 2 runs**, virgin DB, exit 0 |

## 8. What the live smoke found that 551 tests could not

The suite's session factory sets `expire_on_commit=False`. **Production does not.**
Running against the real session configuration surfaced three defects, all in
device registration, all invisible to the suite:

1. **`register_device` read `user.id` after a commit.** The commit expires the ORM
   object, so the read became a lazy load outside a greenlet — a `MissingGreenlet`
   500. Only reachable on the token-conflict path.
2. **The re-activation write was unguarded.** Rotating a device onto a token
   another account holds raised an uncaught `IntegrityError` — a 500 — because only
   the insert path had a handler.
3. **The transfer collided with the caller's own device row.** Fixing (2) surfaced
   `uq_push_devices_user_device`, because transferring a token to a rider who
   already has that device would create two rows for one device. The merge rule in
   §4 exists because of this.

All three now have regression tests. Two further smoke failures were bugs in the
smoke script itself (a doubled URL prefix, and asserting a notification for the
actor who performed the action) — recorded because a test that asserts the wrong
thing is worse than no test.

The smoke runs **twice by default**, sharing the database but nothing else, because
Phase 8.1 shipped a bug that a first run against virgin state masked. Each run
needs five fresh accounts against a 10/hour/IP registration limit, so it can run
at most twice per server start; the script says so explicitly rather than
crashing.

## 9. Deferred, each for a stated reason

- **`FCMProvider` / `APNsProvider`, native configuration** — the seam is the
  deliverable; the vendors are a later phase.
- **Delivery worker, Redis pub/sub** — fan-out is synchronous and in-request up to
  `PUSH_INLINE_FANOUT_LIMIT` (20), which is the seam a worker takes over. Worker
  infrastructure belongs with the Redis work.
- **WebSocket / SSE delivery** — polling, behind a transport interface, as chat
  does.
- **Per-type mute preferences** — a real need, but useless without a transport, and
  it needs the detailed-preference decision.
- **Notification deletion / archival / retention** — unread state plus bounded
  history suffices; retention is a product decision.
- **Ride, training, and AI notification types** — no authorization story exists.
- **Account deletion** — would have to decide whether a deleted rider's
  notifications are deleted, tombstoned, or retained. Deliberately undecided;
  `actor_user_id` RESTRICT keeps a notification attributable in the meantime.
- **Device row pruning** — `PUSH_DEVICE_STALE_DAYS` (90) is configured; no
  scheduled job runs yet, since there is no worker.

## 10. Known limitations

- **The rate limiter is an in-process dict.** With N uvicorn workers the effective
  limit is `limit * N`, and it resets on restart. The same limitation Phase 8.3
  accepted; a shared Redis limiter belongs with the Redis work. Documented at the
  limit table in `app/api/v1/notifications.py`.
- **Provider delivery and row persistence share one call path.** If a future worker
  changes the second it must not change the first.
- **`PUSH_INLINE_FANOUT_LIMIT` (20)** bounds *delivery*, not persistence. Past 20
  recipients in one fan-out, push is deferred to the FUTURE worker and logged as
  `deliver_deferred`; the notification rows are still written, because they are
  the record. `FCMProvider`/`APNsProvider` would be the first thing to notice this
  seam, since with only a fake behind it the deferral is currently invisible in
  behaviour.

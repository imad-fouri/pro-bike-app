# 06 — Realtime: WS, Group Rides, Location Privacy, Route Sync, Chat

## WebSocket
`wss://.../ws/v1?token=` → channels: `ride:{id}`, `team:{id}`, `location:{session_id}`, `route:{route_id}`. Redis pub/sub fan-out, presence heartbeats, per-channel authZ on subscribe + on every publish. Rate-limited. Chat independent of GPS engine.

## Group rides
Lifecycle: scheduled→live→finished. Organizer picks route version; participants join; live location + progress + ride chat. Finish closes location sessions automatically.

## Location privacy (load-bearing)
Scopes: nobody|friends|team|ride-participants|selected. Durations: 30m/2h/until-end/custom. `location_sessions(granter,grantee_scope,expires_at,revoked_at)`; `location_updates` written ~5s throttled. Read path enforces `Friend + grant + active session`; last-known-location only with timestamp; precise coords never leak to unauthorized (coarse or 404). Abuse: per-user rate limit, max sessions, audit log.

## Route sync
`route_versions` monotonic. Organizer publishes v(n+1) → WS event `{route_id,version_no,updated_at,changes}` → clients fetch if behind; concurrent edit → 409 + merge prompt. No silent corruption.

## Chat
Text+timestamps+sender+read-receipts, ride/team channels, route-share/invite/system message types. Schema reserves `reply_to, reactions, attachments` for later.

**Phase 8.3 shipped the chat foundation (ADR-14).** What exists today:

- `conversations` / `conversation_members` / `messages`, one model serving team
  channels and DMs. **Exactly one channel per team**, enforced by a partial
  unique index. A channel taxonomy is deferred to the group-ride phase.
- Team channels and direct messages, both fully working. DM creation requires no
  friendship; a block closes DMs in **both** directions and touches nothing
  about a team channel.
- Ordering by a dense per-conversation `seq`, cursor pagination
  (`before_seq`/`limit`, no `total`), monotonic read high-water marks.
- 15-minute server-side edit window, soft delete only. No admin moderation, no
  reporting, no hard deletion.
- Authorization failures are **404, byte-identical** to "does not exist", so no
  route is an existence oracle.

**Realtime is HTTP polling (~7s) behind a `ChatTransport` interface**, not
WebSockets. The transport emits message *snapshots*, not deltas, so swapping in
SSE or a websocket later is a new implementation of one interface rather than a
rewrite of the conversation screen. `app/websocket/manager.py` remains a stub.

Not yet built, and each deferred for a stated reason rather than by omission:
ride-scoped channels (they need a group-ride session to authorize against),
route-share and invite message types, `reply_to`/reactions/attachments, and Redis
pub/sub fan-out. The message envelope carries no media
or location variant, so location sharing cannot be treated as an implicit
capability of messaging.

## Phase 8.4 — notifications (ADR-15)

Every event Phase 8.1–8.3 produces now has somewhere to land. A friend request, an
accepted request, a team invitation or join request, a removal, an archive, and a
message in either kind of conversation all create a `notifications` row.

**The row is the record; push is an accelerant.** A rider with push disabled, on a
platform with no provider, or on a bad network still has complete history, and
provider failure cannot lose an event because the row is written first. This
inverts the usual order and is the reason the privacy rules below have exactly
one place to live.

**Content is a localization key plus params**, never rendered copy. The server
does not know which of EN/FR/AR the recipient reads, and a rendered string can
only be correct in one of them. Keys are derived from the enum value, so they
cannot drift from the type — and because a row references its key, renaming one
would break history already on a rider's device, so they are treated as a storage
contract.

**`chat_message` and `chat_message_team` are separate types** even though both
concern a message, because their authorization differs. A team-channel recipient
is a live team member; a DM recipient must *also* clear the block policy.

**A blocked DM creates no notification row, for either party** — not a filtered
row, no row. A block that stopped the message but left a "new message" badge would
leak the fact that someone tried to write, which is exactly what the rider asked
to stop. A refused send leaves no trace including on retry, and unblocking
restores notifications with nothing to backfill.

**A block does not sever a team channel**, and team notifications still cross a
block; a removed member stops receiving them. This carries ADR-13 §6 and ADR-14
§2.1 forward: a block is a personal-interaction boundary, and broadcasting it into
a shared space would tell every other member that someone had blocked someone.

The **actor is never notified about their own action**, so the unread badge stays
a useful signal.

Delivery is a provider seam with only a fake behind it: `PushProvider` the enum
names `fcm`/`apns` so a device row can record which ecosystem issued a token, but
nothing in this phase contacts either, and no SDK, credential, or native
configuration was added. `get_provider()` never raises — with nothing configured
it reports every device as skipped, which is the normal state in CI and before a
key is set.

**Push payloads are pointers, never copies**: `notification_id`,
`notification_type`, `deep_link`. A push is visible on a lock screen and a paired
watch, so the client re-fetches through an authorized call on tap, and deep links
are allowlisted and UUID-based client-side.

Like chat, this surface is **polling, not WebSockets** — an unread-count endpoint
the app bar polls each foreground, backed by a partial index over unread rows only.
Realtime push transport and the delivery worker are both FUTURE; fan-out is
synchronous and in-request up to `PUSH_INLINE_FANOUT_LIMIT` (20), which is the seam
where a worker takes over. No ride notifications exist, because a ride reminder
would have to authorize against a ride session and Group Rides are out of scope.

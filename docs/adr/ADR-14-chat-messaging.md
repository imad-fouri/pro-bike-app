# ADR-14 — Chat: Team Channels, Direct Messages, and Their Privacy Boundary

Status: accepted (Phase 8.3)
Date: 2026-10-03
Related: ADR-12 (social relationships & privacy), ADR-13 (teams, membership,
non-cascading blocks), ADR-08 (location), `docs/03-database-model.md`,
`docs/04-api-map.md`, `docs/06-realtime-social.md`

## 1. Context

Phase 8.1 built a one-to-one social graph; Phase 8.2 built *n*-ary teams. Both
left a gap: a team has no shared space, and two riders who are not friends have
no way to talk.

That gap is where the privacy decisions live, and they are not symmetric. A team
channel is visible to everyone in the team by definition. A DM is between two
specific people and is the thing a rider blocks. Treating them the same would
mean either exposing DMs to teams or starving teams of a channel.

Three questions had to be answered before any code:

- Does a block reach a team channel?
- What does a DM require before it may be opened?
- What happens to history when an account is deleted, a rider is removed from a
  team, or a message is deleted?

Everything below follows from those.

## 2. Decision: three tables, one model

`conversations`, `conversation_members`, `messages` — one set serving both kinds.
`kind` (`team` | `direct`) plus a nullable `team_id` is the only difference.

Rejected: separate `team_channels` and `direct_conversations`. Authorization,
ordering, idempotency, and read state are identical for both; two tables would
duplicate all four and force every future chat feature to be written twice.

### 2.1 A block bounds direct messages only

**A block refuses DM creation and DM sending, in both directions. It does not
touch a team channel.**

A block is a personal-interaction boundary: "I do not want this person talking to
me." It is not a statement about teams, and it is not the blocker's to broadcast.
Severing a team channel would remove the blocked rider from a space they hold
independently — and would tell every other member that someone had blocked
someone, which is information the blocker never consented to share. ADR-13 §6
established that blocks do not cascade into team association; the same reasoning
applies here.

The DM side is stricter than 8.1's social reachability, and deliberately so: a
block closes DMs in **both** directions, not just from the blocker. A blocked
rider cannot keep writing to someone who put up a wall.

History is **retained**, not deleted. Unblocking restores the exact thread rather
than leaving a gap.

### 2.2 A DM requires no friendship

Friendship is not a precondition for opening a DM.

Friendship is opt-in and frequently absent between people who plainly want to
coordinate: two riders who met once at a crit have no reason to exchange friend
requests first. Gating DMs on it would also create an oracle — "can these two DM
each other" becomes a proxy for "are these two friends", which leaks the social
graph through an endpoint that has no business knowing about it.

Rejected: gate DMs on friendship or on `message_permission`. Both put a social
relationship in front of a personal one.

### 2.3 Team authorization is re-derived live, never read from the roster

For a team channel, the live `team_memberships` row is read inside the
transaction. `conversation_members` is a roster snapshot and is **never** the
authority on its own.

This matters because a rider removed from a team keeps their `conversation_members`
row — that is what keeps their past messages attributable. A roster-only check
would therefore keep granting access to someone the team has already expelled.
For a DM the participant row *is* the authority, plus the block rule.

The inbox applies the same rule at read time: a channel the viewer has no
standing for is omitted rather than listed as a row that fails on open.

### 2.4 Nothing is ever hard-deleted

`messages.sender_user_id` and `conversation_members.user_id` are `ON DELETE
RESTRICT`. Message history is never cascade-deleted, and every retained message
stays attributable to its author. Account deletion is expected to tombstone the
user (ADR-12) rather than remove the row.

A deleted message keeps its row, its `seq`, and its position in the ordering. Only
its body is replaced with `[deleted]`. A hard delete would break the cursor
pagination of every rider who had already loaded that page, and would silently
change what a past conversation said.

## 3. One channel per team

A partial unique index on `team_id WHERE kind = 'team'` makes a second channel for
the same team impossible at the storage layer.

Custom channels (announcements, ride-specific threads) are **deferred** to the
group-ride phase, which is where a channel taxonomy becomes necessary and where
per-channel authorization would be worth its complexity. Building it now would
mean inventing a permission model with no requirement behind it.

## 4. `seq` is the order, and it is dense

Each conversation has a `next_seq` counter. Allocation happens under a
per-conversation advisory lock, and `UNIQUE(conversation_id, seq)` is the final
arbiter.

Ordering is by `seq`, never by `created_at`. Concurrent sends can share a
timestamp, and a history ordered by an ambiguous key is a history that reorders
itself under a rider reading it.

`next_seq` is a column rather than `MAX(seq)+1` so allocation is an O(1) update
under the lock instead of an aggregate over the whole table on every send.

## 5. Idempotency is client-supplied and mandatory

`client_message_id` is a required field on send. The server deduplicates on
`(conversation_id, sender_user_id, client_message_id)`.

A retry after a dropped response is the normal case on mobile, and only a
caller-held id can make it checkable — a server-generated id cannot survive the
timeout it was meant to cover. First creation returns **201**; a replay returns
**200** with `duplicate: true`, so a client cannot mistake a retry for a second
message.

Scoping the key to `(conversation, sender)` is deliberate: two riders on a
phone-tossed id must not collide.

## 6. Cursor pagination for history

Message history is `{items, has_more, next_before_seq}` — **no `total`**.

Offset pagination over an append-only table is unstable while new messages
arrive at the head: a rider scrolling back through history would see rows shift,
duplicate, or skip. Keyset pagination on `seq` is stable by construction, and
`ix_messages_conversation_seq` serves it directly.

There is deliberately no total count: `COUNT(*)` over full history on every page
is the wrong trade for a table that only grows, and no screen needs the number.

The **inbox** stays offset-paged. A conversation list is bounded, changes rarely,
and does not shift under the rider while they page it.

## 7. Read state is a high-water mark

`conversation_members.last_read_seq` is a `seq`, not a timestamp — immune to clock
skew, and it maps directly onto the ordering key.

Updates are monotonic by construction (`GREATEST` in the UPDATE). Two tabs, or a
slow request racing a fast one, would otherwise let a stale reply re-open messages
the rider has already read. The monotonicity is enforced in SQL rather than in a
Python comparison so it does not depend on some lock happening to be held.

The mark is clamped to the newest message, so a client cannot claim to have read
messages that do not exist.

## 8. Edit and delete

- **15-minute edit window**, enforced server-side. A client computing it from its
  own clock would disagree with the server across device timezones.
- **Soft delete only.** The row survives; the body becomes `[deleted]`.
- Both are author-only. A non-author gets **404**, the same answer as a message
  that does not exist — a 403 would confirm the id is real.

There is **no admin moderation, no reporting, and no hard deletion** in this
phase. Every one of those is a policy decision about someone else's content, and
none of them has a requirement behind it yet. A moderation surface built before
the moderation policy exists is a surface that will be wrong.

## 9. Authorization failures are 404, never 403

A caller with no standing gets the same answer as a conversation that does not
exist — byte-identical, not merely the same status.

Any other shape leaks. A 403 on a conversation id confirms the id is real; a
distinct code for "blocked" confirms a block exists; a different status for
"missing" versus "forbidden" makes an endpoint a probe for what exists on the
platform. This is the same oracle ADR-13 §7 closed for teams, applied to chat.

403 is reserved for cases where the caller already demonstrably has access —
posting to a team they are a member of whose archived state they can see.

A blocked rider and a non-existent rider produce identical 404s, so a block
cannot be detected by timing or by status.

## 10. A message carries public identity only

Sender identity is read from `social_profiles`, never `users`. A message list
must not be able to surface an email address or any other account-private column.

`message_type` has exactly two values: `text` and `system`. `ride`, `route`,
`location`, `workout`, and media variants are deliberately absent — adding them
now would create the appearance of support for location sharing, which ADR-12 §3
and ADR-14 forbid treating as an implicit capability of "messaging".

## 11. Realtime is polling first, behind an interface

The initial transport is HTTP polling every ~7 seconds, behind a
`ChatTransport` interface so the conversation screen never learns which one it is
using.

A websocket or SSE layer is a deployment decision (connection count, sticky
sessions, a broker) that the app does not dictate. The interface exists so that
decision can be made later without rewriting the screen.

The interface emits **snapshots, not deltas**. A delta protocol needs ordering and
gap repair; the server already exposes "newest N messages" as one call, and
re-reading a page is both cheaper to implement and impossible to get out of order.

Polling errors are swallowed: a failed poll ends that cycle and the last good
snapshot stays on screen. Losing the conversation because the network blinked is
worse than showing slightly stale text, and an error dialog every seven seconds
in a lift is worse still.

Rate limiting uses the existing in-process limiter, which is not distributed
across workers. That is a known and accepted limit for this phase; a shared Redis
limiter belongs with the Redis infrastructure work.

## 12. Account deletion tombstones; it does not cascade

`users` deletion is `RESTRICT`-ed by `messages.sender_user_id` and
`conversation_members.user_id`. The account-deletion path is expected to
tombstone the user (as ADR-12 established for the social graph) rather than
remove the row, so that chat history survives and stays attributable.

A cascading delete here would silently erase history other riders still hold a
copy of, in a thread they did not author and cannot consent to.

## 13. Flutter: polling behind an abstraction, honest sends

- The client holds **one `client_message_id` per send attempt** and reuses it
  across retries of that attempt. Generating one per call would defeat the
  mechanism the field exists for.
- The composer clears on **success**, not on tap. An optimistic clear followed by
  a failed send loses the rider's text.
- History pages are **merged by message id** and sorted by `seq`. A poll and a
  pull-to-refresh can return the same message; a naive append renders it twice.
- `can_edit` is used exactly as the server reports it. The client never re-derives
  the window from a device clock.
- Older history is loaded behind an explicit button rather than an automatic
  scroll prefetch: an auto-prefetch fires again after every merge and quietly
  walks the entire history over several screens of scrolling.

## 14. Re-check under the lock

Team standing and archive status are re-read **inside** the conversation lock on
every write, not just at the start of the request.

A request admitted just before a removal or an archive commits must still be
refused. Checking once at the start of the request leaves a window — small, but
real, and it is exactly the window where a "you were removed one second ago"
message would look like a bug rather than a race.

## 15. Consequences

Accepted costs:

- Every message page is a query per conversation for the latest message plus one
  for the unread count. Acceptable at inbox scale; a materialized counter is the
  fix if the inbox grows.
- The in-process rate limiter is not shared across workers.
- Polling costs a request per open conversation per interval. Bounded by how few
  conversations a rider actually has open at once.
- 15 minutes is short enough to be annoying and long enough to fix a typo. It is
  a single constant (`EDIT_WINDOW_MINUTES`) and is the first thing to revisit.

Deferred deliberately: admin moderation and reporting, hard deletion, media and
attachments, push notifications, websockets/SSE, Redis pub/sub, group rides,
live location, synchronized rides, and a channel taxonomy. Each needs a policy
decision, not more code.
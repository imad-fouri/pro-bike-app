# ADR-16 — Group Rides: Organizer Authority, Immutable Route Pinning, and Ephemeral Live Location

Status: accepted (Phase 9)
Date: 2026-10-05
Related: ADR-08 (location), ADR-09 (maps and routes), ADR-12 (social
relationships & privacy), ADR-13 (teams, membership, non-cascading blocks),
ADR-14 (chat and the DM privacy boundary), ADR-15 (notifications),
`docs/03-database-model.md`, `docs/04-api-map.md`, `docs/06-realtime-social.md`

## 1. Context

Phases 8.1–8.4 produced friendships, blocks, teams, chat, and a durable
notification inbox. Each is individually sound and each was explicitly built so
that a *later* phase could attach to it without re-deciding its foundations.
Two of them left a visible seam:

- `ConversationKind` carried a note that a channel taxonomy is "deferred to the
  group-ride phase, which is where ride-specific conversations actually become
  necessary" (`app/models/chat.py` §1).
- `NotificationType` carried three values — `group_ride_invitation`,
  `group_ride_accepted`, `group_ride_reminder` — explicitly deferred, with the
  reasoning that adding a value is cheap but adding one with no authorization
  story is not (`app/models/notifications.py`).

What a group ride needs beyond those seams is a coordination surface: one
rider commits to a specific ride, at a specific time, on a specific route, with
a specific set of other people, and can see where those people are while it is
happening.

That last clause is the dangerous one. Live location is the most sensitive
signal this product will ever handle: it is a real-time, continuously
revealing, physically-exact stream about where a person is. ADR-12 and ADR-14
have already established the project's posture — history is retained but
reading is denied while a block stands, and a team channel is governed by live
membership rather than by roster rows. Live location must be *stricter* than
either, not merely equal to them.

The temptation is to model a group ride as a "team with a date", reusing
`teams` and adding a `starts_at`. That is wrong: it would make ride membership
and team membership the same fact, so leaving a team would silently eject a
rider from a ride they are currently in the middle of, and it would make a
finished ride's roster permanently govern a live privacy decision.

## 2. Decision: a group ride is its own aggregate with its own roster

`group_rides` and `group_ride_participants` are new tables. Team membership is
*not* reused and does not cascade into ride membership in either direction.

The roster is deliberately a **single** table rather than the
invitations-plus-memberships pair that teams use. Teams need two because an
applicant and an inviter have opposite permissions. A group ride has only one
way in — the organizer invites — so a separate invitations table would be a
second source of truth about who is on the ride, and the mandate is one
authoritative roster.

`group_ride_participants.status` carries five explicit states:

| State | Meaning |
|---|---|
| `invited` | Invited, has not responded. Not a participant. |
| `joined` | Confirmed participant. The only state that grants visibility. |
| `declined` | Turned down the invitation. |
| `left` | Was `joined`, withdrew. Consent withdrawal. |
| `removed` | Was `joined`, ejected by the organizer. |

`declined`/`left`/`removed` are three distinct facts, not one: a rider who
declined was never in, a rider who left exercised consent, and a rider who was
removed was acted upon. Collapsing them would make the notification and audit
stories lie.

`UNIQUE(group_ride_id, user_id)` holds for the table's whole life, not just for
active states. One row per (ride, user) ever means "am I on this ride?" is an
index-only lookup and double-participation is structurally impossible. A
re-invitation after `declined` transitions that row back to `invited` and clears
`responded_at`; the decline is not preserved, deliberately, because retaining
"this rider said no" has no privacy value and some embarrassment cost.

## 3. Decision: the smallest lifecycle that is still honest

Four states, four organizer-only transitions, no reversal:

```
open ──start──▶ started ──complete──▶ completed
  │                 │
  └───cancel────────┴────────cancel────▶ cancelled
```

`completed` and `cancelled` are terminal. There is no `draft`: creating a ride
produces an `open` ride, because a two-phase "draft then publish" would need a
visibility concept this domain does not have.

The roster is **frozen at `started`**. Nobody may be added after the ride
begins. A participant may always *withdraw* while the ride is `open` or
`started` — withdrawal is a consent right and is never gated on the
organizer. This asymmetry is the point: you can always take yourself out, but
nobody can be added to a ride that is already rolling.

Organizer-only fields are `status`, `title`, `description`, `starts_at`,
`meeting_point`, `route_id`, and `route_version`. A participant who can set any
of those is a second organizer.

`starts_at` is a **scheduled time, not a gate**. It does not close
invitations, refuse joins, or auto-start a ride; the `started` transition is the
only thing that begins a ride, and only the organizer can perform it. A
time-based cutoff would have to answer questions this domain deliberately does
not — whether a rider who accepted an invitation before the cutoff may still
join a ride the organizer has not started yet, and whether the organizer may
start a ride whose nominal start has passed. Answering either by reflex would
lock people out of rides that have not happened. The roster freeze at `started`
is a *state*, and that is what does the actual work.

## 4. Decision: a ride pins an immutable route version

`route_id` and `route_version` are stored as a pair, with a `CHECK` forcing
both-or-neither. `routes` already separates the planned route from immutable
`route_versions`, and `rides` already pins the pair (ADR-09).

Pinning matters more for a *group* than for a solo ride. If a ride held only
`route_id`, editing the route after the organizer invited twelve people would
silently change the ride twelve people agreed to. The pair makes the agreement
refer to geometry that cannot change underneath them.

The ride does not copy geometry. A new `route_version` is a new version; a ride
that must follow it is a different fact and is out of scope.

The pair is enforced by a **composite foreign key** against
`route_versions(route_id, version_no)`, not by a service-layer check and not by a
FK on `route_id` alone. This is the load-bearing part. A FK on `route_id` proves
only that the route exists, so "route A version 99" would be accepted for a route
whose only version is 1 — the ride would then point at geometry that was never
created. `route_versions` already carries `UNIQUE(route_id, version_no)`, which
is exactly the target this needs. The composite `ON DELETE SET NULL` nulls *both*
columns, so the `CHECK` still holds when the route (and its versions) go away:
a deleted route leaves a ride with no route, never a dangling version number.

Route visibility is *not* re-litigated here. The organizer may only pin a route
they can already act on; a ride does not widen its route's audience. The
mandate's "no non-participant visibility" is enforced by the live-location rule
in §6, not by mutating route privacy.

## 5. Decision: one channel per ride, on the existing conversation tables

`ConversationKind` gains `group_ride`, and `conversations` gains a nullable
`group_ride_id` with a **plain** unique index on that column. The existing
`CHECK` becomes a three-way disjunction.

The plain unique index, rather than the partial `WHERE kind = 'group_ride'`
index the team channel uses, is deliberate and not a shortcut. A ride channel has
exactly one non-null `group_ride_id`, so uniqueness on the column is exactly the
guarantee — whereas a partial index on `conversations.group_id` is needed for
teams only because team channels share that column with DMs. PostgreSQL treats
NULLs as distinct in a unique index, so a plain unique index places no constraint
at all on the team and DM rows, which all carry NULL. The result is the same
one-channel-per-ride guarantee with a smaller, predicate-free index — and no
predicate to keep in step with the enum.

A further consequence of the plain form is that the downgrade is trivial: drop
the one index. A partial index on an enum column cannot simply be dropped during
the downgrade's temporary `kind → varchar` conversion, because the predicate
stops parsing, which is why `uq_conversations_team_channel` has to be dropped
and recreated around that cast. Avoiding a new predicate avoids a new
version-specific hazard.

This reuses ordering (`seq`), pagination (`before_seq`), idempotent send
(`client_message_id`), read state, and the edit/delete window rather than
reimplementing them. It also means a ride's messages are attributable and
retained under the same rules as every other message — authors are `RESTRICT`,
so a participant's departure never erases what they said.

Authorization differs from a team channel in exactly one way, and it is the
load-bearing difference: a team channel's authority is live *team* membership,
whereas a ride channel's authority is a `joined` row in
`group_ride_participants`. A rider removed from a team keeps their roster row
so history stays attributable; the same is true of a ride, which means the
roster row alone would wrongly keep granting access. Reading and writing both
re-resolve live `joined` status.

Conversation membership rows are synced best-effort on read, exactly as
`_sync_team_roster` does for teams, so attribution stays correct without making
roster sync a correctness dependency.

### Two things the shared conversation tables had to be taught about group channels

A group channel is a group channel. Two assumptions in the Phase 8.3 code were
written for a world containing exactly one kind of group channel, and adding a
second one broke both. Both were latent before this phase and are only reachable
with three or more members, which is why a two-rider ride passes every test
except these.

**`peer` is a DM concept, not a group concept.** The conversation view computes a
`peer` — "the other person" — by selecting every member who is not the viewer, and
reads it with `scalar_one_or_none()`. For a DM that is exactly one row. For a
three-rider ride it is two, and the read raises `MultipleResultsFound`, so the
response is a 500. Worse, it fails on `GET /chat`, taking the rider's whole inbox
down rather than just the one conversation. The fix is to key on `DIRECT` rather
than on "not `TEAM`": the earlier `is_team` check correctly protected team
channels and left ride channels exposed, because at the time "not a team" did
imply "a DM". A ride channel now reports `peer_user_id: null` rather than an
arbitrary one of the other riders, which would mislabel every message in it.

**`ConversationOut` must be able to name a group channel that is not a team
channel.** `GET /chat` validates every inbox row against `ConversationOut`, whose
`kind` enum listed only `team` and `direct`. A `group_ride` row is a validation
error, and one bad row fails the whole page — so the phase's central guarantee
(a rider on a ride can talk to the other riders) would have broken the inbox
instead of appearing in it. The response enum gains `GROUP_RIDE`, and
`ConversationOut` gains `group_ride_id`, `group_ride_title`, and
`group_ride_status`.

The title is re-read live on every inbox page rather than snapshotted, so a
renamed ride cannot leave a stale title in somebody's inbox. It exists because
every `team_*` field is null on a ride channel: without it the inbox row has
neither a team name nor a peer name and renders as an unlabelled box.

### The inbox filters standing in SQL, and the count follows

`GET /chat` previously dropped conversations the viewer had lost standing for
*after* paginating, which meant `total` counted rows the page then hid — a page
of 20 showing 3 items while claiming a total of 20 reads as data loss. The filter
is now part of the query, as two `NOT EXISTS`-shaped `or_` clauses, so `total` and
the page come from one row set.

The ride clause matches on a live `joined` roster row rather than on the
`conversation_members` row the query already joined, for the same reason the ride
endpoint does: a rider who withdrew an instant ago still holds a member row
(delegated roster sync deliberately never removes one), so trusting it would keep
advertising a channel that answers 404 — and would confirm the ride exists. Ride
membership is as revocable as team membership and must be enforced at read time
rather than by keeping the inbox clean.

## 6. Decision: live location is ephemeral, opt-in, and read-only to non-sharers

**No table is created for live location.** Location is written to Redis and
expires on its own.

This is the decision most worth defending, because a `group_ride_locations`
table is the obvious implementation and it is wrong. A table makes GPS history
permanent by accident: every row is a fact about where a person was, retained
indefinitely, discoverable in backups, and subject to every future "just add an
index" request. It also makes "stop sharing" mean "delete some rows", which is
a weaker and more error-prone promise than "there is nothing left to find".

The Redis shape is one hash per ride, `gr9:share:{ride_id}`, field per user,
value a `lat|lon|accuracy|ts` string, with a TTL on the whole key. Three
consequences follow directly:

- **Stop is immediate.** Stopping deletes the field. Stopping also revokes
  consent; there is no "keep publishing so my existing blob stays correct".
- **Staleness is a read-time computation.** Redis hash fields have no
  individual TTL, so freshness is computed from `ts` against
  `STALE_AFTER_SECONDS`. A sharer who disappears stops advancing `ts` and falls
  out of the read on its own, with no sweeper job.
- **Retention is bounded by construction.** When the last sharer stops or the
  key's TTL lapses, no record remains that the ride ever had a location map.

Consent is explicit, per ride, and revocable:

- Location sharing is **off for everyone by default**. Creating a ride,
  accepting an invitation, and joining a ride all grant **zero** location
  consent. This is stated per the mandate and enforced by the absence of any
  code path that publishes a coordinate without an explicit share start.
- Only a `joined` participant may publish, and only while the ride is `open` or
  `started`.
- Only a `joined` participant may read, and only for riders who have
  themselves published. A non-participant gets the same 404 a missing ride
  gets — the endpoints are never an existence oracle.
- Every publish is rate limited server-side by the Phase 8.6 bounded limiter,
   and `ts` is assigned **by the server**. A client cannot backdate a fix, cannot
   resurrect a stopped sharer's field by replaying an old timestamp, and cannot
   forge a future one to dodge the stale filter.

Heading and speed are deliberately absent. They are the two fields a client can
most easily fabricate and a rider least able to audit, and nothing in the
mandate needs them; adding them later would mean revisiting this decision rather
than widening it.

The value is a delimited string rather than JSON because there is nothing to save
and nothing to extend: four scalars, written once and read within five minutes. A
malformed value is skipped rather than treated as fatal — one rider's bad write
must not blank the map for everyone else, and a value that expires in minutes
does not justify a repair path.

### Blocks apply here, and apply harder than in teams

A team channel deliberately does *not* cascade a block: blocking a rider does
not evict them from a team (ADR-13). Live location takes the opposite
position. A block in *either* direction removes that pair from the location
map, and the blocked party cannot publish into a ride the blocker is on. A
block is an explicit "do not know where I am" signal, and a real-time map is
the one surface where honoring it matters more than preserving group
functionality. This is a deliberate, documented divergence from ADR-13, not an
oversight.

Coordinates are never logged. Phase 8.6 made the logging layer reject
latitude/longitude keys, and the group-ride service follows the same rule: it
logs ride ids, user ids, and staleness decisions, never a coordinate.

### Redis being unavailable must not lie

`/ready` does not gate on Redis (Phase 8.6), so Redis is treated as
best-effort infrastructure. For live location that is not good enough: if Redis
is down and the read endpoint answers "nobody is sharing", the client renders
an empty map that looks like a fact about the world when it is a fact about the
server. So both the publish and the read return **503** when Redis is
unreachable. A rider sees "live locations unavailable", which is true.

## 7. Decision: notifications reuse the existing seam

Four new `NotificationType` values, all with an authorization story written
before the value existed:

- `group_ride_invitation` — recipient is the single invited roster row.
- `group_ride_accepted` — recipient is the organizer; the inverse direction of
  the invitation, which is why it is a separate type rather than a second
  delivery of the first.
- `group_ride_started` — recipients are the `joined` roster, minus the actor.
- `chat_message_group_ride` — a fourth authorization basis for a message: joined
  roster, as distinct from live team members (`chat_message_team`) and
  block-cleared pairs (`chat_message`). One type with three different recipient
  rules would make the privacy rule unenforceable.

(`group_ride_reminder` and `ride_starting` remain unimplemented: they require a
scheduler, which ADR-03 has and which is out of scope here.)

Every group-ride notification goes through `notification_service.notify` with an
`entity_type='group_ride'`, an `entity_id`, and a `dedupe_key` derived from
business identity rather than a wall-clock instant — so a retried accept or start
cannot double-notify. Payloads carry substitution arguments (rider name, ride
title) and never a coordinate, a message body, or a token, matching ADR-15 §6.

The invitation's dedupe key carries the timestamp that particular invitation
stamped on the roster row. This is not a detail: `UNIQUE(group_ride_id, user_id)`
holds for the life of the ride (§2), so the roster row's id is *stable* across a
decline and a re-invitation, and a key built from it alone makes the second
invitation a silent duplicate of the first. The rider is re-invited, the
organizer's screen says "invited", and no notification ever arrives — which from
the rider's side is indistinguishable from having been ignored. The generation
stamp is what makes a re-invitation a new event without making a retry a new
event.

The Flutter client already degrades an unknown `l10n_key` to a generic
sentence rather than rendering the raw key, so a newer server cannot break an
older app by adding a type. That property is what makes adding enum values
safe, and it is tested.

## 8. Consequences

- Two new tables, one conversation column, two enum values (one
  `conversation_kind`, four `notification_type`). One forward migration, one
  clean downgrade, one head.
- Adding a value to `conversation_kind` and `notification_type` is not
  reversible with `DROP TYPE`. The downgrade therefore recreates both types
  with their original value lists, after dropping dependent rows and columns.
  PostgreSQL 16 permits `ALTER TYPE ... ADD VALUE` inside a transaction, so
  upgrade is a single atomic step — but a value added in a transaction cannot
  then be *used* in a predicate or `CHECK` in that same transaction. Every
  predicate that mentions a new value is therefore written as
  `kind::text = 'group_ride'`, which parses and is not sensitive to the type.
- Because live location is not in the database, there is no location history to
  export, no location index to grow, and no way for a future phase to
  accidentally start retaining GPS breadcrumbs. If a product requirement ever
  needs a location *history*, it must be a separate, explicitly consented
  feature with its own retention policy — not an accident of this one.
- Live location is per-ride and ephemeral, so it does not survive a restart,
  which is the correct trade for a feature whose privacy promise is "short-lived
  and revocable".

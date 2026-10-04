# 03 — PostgreSQL Domain Model (essentials)

Conventions: UUID PKs, `created_at/updated_at`, soft-delete `deleted_at` on user content, unique + check constraints, indexes on all FKs + `(user_id, started_at)` for rides, GIST later with PostGIS.

Tables: users, profiles, cycling_disciplines, bikes, bike_types, goals, training_methodologies, training_zones, training_plans, training_sessions, workouts, rides, gps_tracks (1:1 ride), gps_points (ride_id, seq, lat/lon, ele, time, accuracy, hr/cad/power), routes, route_versions (route_id, version_no UNIQUE, geojson/gpx_ref, changelog), waypoints, friends/friend_requests, teams/team_memberships, group_rides/group_ride_participants, location_sessions (grantee_scope, expires_at, revoked_at) + location_updates, chats/chat_members/messages (future: reply_to, attachments), notifications, challenges/achievements, subscriptions/payments, devices/sensor_connections, performance_metrics (raw|derived|estimate flag), recovery_metrics, ai_recommendations (grounded inputs JSON).

Key rules:
- Location visibility = join against active, unexpired, unrevoked session + scope check. Enforced in SQL view + service, tested.
- Route edits create new `route_versions` row (optimistic locking on `version_no`); never overwrite.
- Metrics rows carry `provenance: raw|derived|estimate` — API must surface it (AI rule).
- Every change = Alembic migration (Rule 7).

## Phase 8.1 — social tables (migration `0007_social`, ADR-12)

Three additive tables. No existing table is altered.

**`social_profiles`** — the *public* projection of identity, 1:1 with `users` via
`user_id` (unique, FK CASCADE). The private account profile stays in
`user_profiles` and is never exposed.

| Column | Type | Notes |
|---|---|---|
| `username` | `varchar(30)` UNIQUE, NULL | canonical lowercase form; NULL = unclaimed (many NULLs allowed) |
| `display_name` | `varchar(80)` NOT NULL | seeded from `user_profiles.display_name`, never invented |
| `bio` | `varchar(500)` | |
| `avatar_url` | `varchar(512)` | http(s) only, validated in the service |
| `cycling_category` | `varchar(32)` | free-text label, not an enum |
| `country_code` | `varchar(2)` | ISO-3166 alpha-2 label |
| `city` | `varchar(120)` | free-text label — **never coordinates** |
| `profile_visibility` | `varchar(16)` | CHECK in (`public`,`friends`,`private`) |
| `allow_friend_requests` | enum `friend_requests_policy` | (`everyone`,`nobody`) |
| `search_visibility` | enum `search_visibility` | (`discoverable`,`hidden`) |

Indexes: `ix_social_profiles_username`, `ix_social_profiles_user_id`.

**`friend_relationships`** — one row per unordered pair.

- `user_a_id < user_b_id` enforced by CHECK `ck_friend_relationships_canonical`,
  so A-B and B-A can never be two rows.
- `UNIQUE(user_a_id, user_b_id)` — the concurrency arbiter for insert.
- CHECK `ck_friend_relationships_no_self`; FK CASCADE on all three user columns.
- `status` enum `relationship_status` (`pending`, `accepted`). **No `rejected`
  state** — reject and cancel delete the row.
- `requested_by_user_id` records direction while pending.
- Indexes: `(user_a_id, status)`, `(user_b_id, status)`, `(requested_by_user_id, status)`.

**`user_blocks`** — directional block pairs, separate from relationships.

- `UNIQUE(blocker_user_id, blocked_user_id)`, CHECK no-self, FK CASCADE.
- Blocking annihilates the pair's `friend_relationships` row in the same
  transaction. Unblocking restores nothing — the rider starts from a new request.
- Indexes: `ix_user_blocks_blocker`, `ix_user_blocks_blocked`.

**No coordinates, no email, no tokens** in any of the three tables. The public
surface is enumerable exactly as listed above; anything added later is a
deliberate change to the privacy boundary (ADR-12 §2.1).

Concurrency: every pair mutation takes
`pg_advisory_xact_lock(hashtext(f"social:{low}:{high}"))` before reading or
writing, so send/accept/reject/cancel/remove/block are mutually exclusive per
pair with no lock ordering and no deadlock (ADR-12 §5).

## Phase 8.2 — team tables (migration `0008_teams`, ADR-13)

Four new tables. No existing table is altered, so 8.1 social rows and 8.2 team
rows cannot interfere.

**`teams`** — the team as a public entity, discoverable on its own terms.

| Column | Type | Notes |
|---|---|---|
| `owner_user_id` | FK `users.id` CASCADE, NOT NULL | creator; always also holds the OWNER membership, written in the same transaction |
| `name` | `varchar(80)` NOT NULL | |
| `handle` | `varchar(30)` UNIQUE, NULL | canonical lowercase; NULL = unclaimed (many NULLs allowed) |
| `description` | `varchar(500)` | |
| `avatar_url` | `varchar(512)` | http(s) only, validated in the service |
| `category` | `varchar(32)` | free-text label, mirrors `social_profiles.cycling_category` |
| `visibility` | enum `team_visibility` | (`public`, `private`) |
| `status` | enum `team_status` | (`active`, `archived`) |
| `member_count` | `Integer` NOT NULL default 1 | denormalized; CHECK `>= 0` |
| `created_at` / `updated_at` / `archived_at` | `timestamptz` | |

Indexes: `ix_teams_owner_user_id`, `ix_teams_visibility_status`, `ix_teams_name`.

**`team_memberships`** — the ONLY authorization source for teams.

- `role` enum `team_role` (`owner`, `admin`, `member`); `status` enum
  `team_membership_status` (`active`).
- `UNIQUE(team_id, user_id)` — one row per rider per team.
- **Partial unique index** `uq_team_memberships_single_owner` on `team_id WHERE
  role = 'owner'`: exactly one OWNER per team, enforced by storage.
- Indexes: `ix_team_memberships_team_id`, `ix_team_memberships_user_id`.

**`team_join_requests`** — an applicant asking to join a private team.

- `UNIQUE(team_id, user_id)`, optional `message varchar(280)`.
- **Deleted** on accept or reject: a rejected request is not a fact worth
  storing (same rule as 8.1 friend requests, ADR-12 §2.4).
- Indexes on `team_id`, `user_id`.

**`team_invitations`** — a manager offering entry. Retained in every terminal
state so both sides have a durable record.

- `status` enum `team_invitation_status` (`pending`, `accepted`, `declined`,
  `revoked`), plus `responded_at`.
- CHECK `ck_team_invitations_no_self` (`invited_user_id != invited_by_user_id`).
- **Partial unique index** `uq_team_invitations_pending_pair` on
  `(team_id, invited_user_id) WHERE status = 'pending'`: at most one pending
  offer per pair, while a team MAY re-invite someone who previously declined.
- Indexes: `ix_team_invitations_team_id`, `ix_team_invitations_invited_user_id`,
  `ix_team_invitations_invitee_status`.

**No coordinates, no email, no tokens** in any of the four. A member list is
assembled by joining `social_profiles` (the public projection), never `users`,
so a roster cannot leak an email address.

### Concurrency

Two advisory transaction locks (ADR-13 §7):
- `pg_advisory_xact_lock(hashtext(f"team:{team_id}"))` on every membership
  mutation, so concurrent add/remove/leave cannot corrupt `member_count`.
- `pg_advisory_xact_lock(hashtext(f"teampair:{team_id}:{user_id}"))` on
  join/request/invite/accept for one pair.

Single lock per transaction: no ordering, no deadlock. The partial unique
indexes remain the final arbiter for inserts.

### Block semantics (non-cascading)

A block between two riders **refuses new team association actions** (join,
request, invite) and **deletes nothing** — no membership row, no friendship row.
Existing membership survives a block; unblocking recreates nothing. See ADR-13
§6.

## Phase 8.3 — chat (migration `0009_chat`, ADR-14)

Three additive tables on top of `0008_teams`. No existing table is modified, so
8.3 cannot regress 8.1 or 8.2.

One set of tables serves both team channels and DMs. `kind` plus a nullable
`team_id` is the only difference between them, because authorization, ordering,
idempotency, and read state are otherwise identical; separate `team_channels` and
`direct_conversations` tables would duplicate all four.

**`conversations`**

| Column | Type | Notes |
|---|---|---|
| `id` | `UUID` PK | |
| `kind` | enum `conversation_kind` (`team`, `direct`) | |
| `team_id` | `UUID` → `teams.id` **ON DELETE CASCADE** | NULL for a DM |
| `created_by_user_id` | `UUID` → `users.id` **ON DELETE RESTRICT** | |
| `created_at` | `timestamptz` | |
| `archived_at` | `timestamptz` | unused in 8.3 |
| `next_seq` | `BigInteger` NOT NULL default 1 | allocation counter |

- CHECK `ck_conversations_kind_team`: a team conversation must have a `team_id`
  and a direct conversation must not. The kind/team pairing cannot drift.
- CHECK `ck_conversations_next_seq` (`>= 1`).
- **Partial unique index** `uq_conversations_team_channel` on `team_id WHERE
  kind = 'team'`: **exactly one channel per team**, enforced by storage.
- Indexes: `ix_conversations_team_id`, `ix_conversations_created_at`.

`team_id` cascades because a deleted team takes its channel with it — a deleted
team has no members left to read it. `created_by_user_id` restricts because a
tombstoned author keeps a channel attributable.

**`conversation_members`**

| Column | Type | Notes |
|---|---|---|
| `id` | `UUID` PK | |
| `conversation_id` | `UUID` → `conversations.id` **ON DELETE CASCADE** | |
| `user_id` | `UUID` → `users.id` **ON DELETE RESTRICT** | |
| `joined_at` | `timestamptz` | |
| `last_read_seq` | `BigInteger` NOT NULL default 0 | read high-water mark |

- `UNIQUE(conversation_id, user_id)` — one row per rider per conversation.
- CHECK `ck_conversation_members_last_read_seq` (`>= 0`).
- Index `ix_conversation_members_user_id`.

For a DM these rows **are** the participant list, and that is what stops a DM
becoming a group chat by accident. For a team channel they are a roster
snapshot, never the authority — see below.

**`messages`**

| Column | Type | Notes |
|---|---|---|
| `id` | `UUID` PK | |
| `conversation_id` | `UUID` → `conversations.id` **ON DELETE CASCADE** | |
| `sender_user_id` | `UUID` → `users.id` **ON DELETE RESTRICT** | **never cascades** |
| `body` | `text` | |
| `message_type` | enum `message_type` (`text`, `system`) | no media/location/ride variants |
| `client_message_id` | `UUID` | client-generated idempotency key |
| `seq` | `BigInteger` | the ordering key |
| `created_at` | `timestamptz` | |
| `edited_at` / `deleted_at` | `timestamptz` | soft mutations only |

- `UNIQUE(conversation_id, sender_user_id, client_message_id)` — a retry is the
  same message.
- `UNIQUE(conversation_id, seq)` — no two messages may share a position.
- CHECK `ck_messages_seq` (`>= 1`), `ck_messages_body_nonempty` (`length > 0`).
- Index `ix_messages_conversation_seq` — this is exactly what a cursor page
  reads (`WHERE conversation_id = ? ORDER BY seq DESC`).
- Index `ix_messages_sender`.

**Nothing in chat ever hard-deletes a message.** `sender_user_id` is RESTRICT, so
history survives account deletion and stays attributable to its author; account
deletion is expected to tombstone the user instead. `conversation_members.user_id`
is RESTRICT for the same reason — an account deletion must not erase the
participation record while the messages it wrote remain.

**No coordinates, no email, no tokens.** Sender identity is read from
`social_profiles`, never `users`, so a message list cannot leak an email address.

### Concurrency

Two advisory transaction locks (ADR-14 §5):
- `pg_advisory_xact_lock(hashtext(f"chat:{conversation_id}"))` on every send,
  edit, and delete, so `seq` allocation is a serialized read-modify-write on
  `next_seq` rather than a lost-update race that surfaces as a 500.
- `pg_advisory_xact_lock(hashtext(f"dm:{low}:{high}"))` on DM find-or-create.

Single lock per transaction: no ordering, no deadlock. The unique constraints
remain the final arbiter, so a lost race re-reads the winning row and answers
from it rather than duplicating.

### Authorization is re-derived per request

Team authorization reads the **live `team_memberships` row** inside the
transaction; `conversation_members` is never trusted on its own for a team
channel. A rider removed from a team keeps their member row so history stays
attributable, which is exactly why the roster alone would wrongly keep granting
access. For a DM, the participant row *is* the authority. See ADR-14 §4, §14.

### Block semantics

A block refuses DM creation and DM sending **in both directions** and deletes
nothing — history is retained, and unblocking restores the exact thread. A block
does **not** touch a team channel: a block is a personal-interaction boundary,
and severance would broadcast the block to every other team member. See ADR-14 §2.

## Phase 8.4 — notifications (migration `0010_notifications`, ADR-15)

Additive after `0009_chat`. Two tables; no `notification_preferences`, because
per-user settings already live on `user_profiles`.

### `push_devices` — one delivery target per rider per installation

| Column | Notes |
| --- | --- |
| `id` | UUID PK |
| `user_id` | FK `users.id` **CASCADE** — a deleted rider leaves zero live targets |
| `platform` | enum `push_platform`: `android`, `ios` |
| `provider` | enum `push_provider`: `fcm`, `apns` — names the ecosystem only; Phase 8.4 contacts neither |
| `device_id` | client-generated, stable per installation, ≤128 |
| `token` | text, plaintext by necessity (must be presented to a provider) |
| `app_version`, `locale` | optional metadata |
| `enabled` | disable, distinct from revoke |
| `last_seen_at`, `created_at`, `updated_at` | |

Constraints and indexes:
- `uq_push_devices_user_device` UNIQUE (`user_id`, `provider`, `device_id`) — makes
  a provider token rotation an UPDATE rather than a new row per token lifetime.
- `uq_push_devices_provider_token` UNIQUE (`provider`, `token`) — a token is one
  device's address. Without cross-account uniqueness, signing out of A and into B
  on one phone leaves A's row live and A keeps pushing to B's screen.
- `ck_push_devices_device_id`, `ck_push_devices_token` — non-empty.
- `ix_push_devices_enabled_user` partial on `enabled` — target selection.

The two unique constraints interact, and the interaction is where the logic lives
(ADR-15 §3.1): a token held by another account is **transferred**, not refused,
and if the caller already holds that physical device the rows **merge**. Both
write paths guard their commit and route through one `_reconcile_token_clash`
helper.

`token` is never returned by any endpoint. `PushDeviceOut` is built explicitly
rather than by model validation, so adding a column cannot expose it.

### `notifications` — one in-app notification per recipient

| Column | Notes |
| --- | --- |
| `id` | UUID PK |
| `recipient_user_id` | FK `users.id` CASCADE |
| `actor_user_id` | FK `users.id` **RESTRICT** — a notification must not vanish because its actor was removed (same rule as `messages.sender_user_id` in 0009) |
| `type` | enum `notification_type` (see below) |
| `entity_type`, `entity_id` | nullable pointer to what the notification is about |
| `l10n_key` | stable key, derived as `notifications.type.<value>` |
| `params` | JSONB — public display strings only |
| `deep_link` | nullable, app-internal, allowlisted client-side |
| `dedupe_key` | nullable, ≤160 |
| `created_at`, `read_at` | `read_at IS NULL` means unread |

Indexes:
- `uq_notifications_dedupe_key` — **partial** UNIQUE on `dedupe_key` WHERE
  `dedupe_key IS NOT NULL`. Partial because a plain unique collides on repeated
  NULLs before PostgreSQL 15; the predicate is what lets system notices coexist
  with deduplicated ones. Keys derive from an immutable business id, never a
  timestamp.
- `ix_notifications_recipient_created` (`recipient_user_id`, `created_at DESC`) —
  offset-paged history.
- `ix_notifications_recipient_unread` partial WHERE `read_at IS NULL` — the app
  bar polls the count on every foreground; this makes it an index-only scan that
  does not degrade as history grows.
- `ix_notifications_recipient_type` (`recipient_user_id`, `type`).

No `title`/`body` columns: content is `l10n_key` + `params` so the client renders
in the rider's own locale. `params` must never hold a message body, a
coordinate, an email, or a token.

### Types emitted in Phase 8.4

`friend_request`, `friend_request_accepted`, `team_invitation`,
`team_join_request`, `team_member_removed`, `team_archived`, `chat_message`,
`chat_message_team`, `system`.

`chat_message` and `chat_message_team` are separate values because their
authorization differs — see the block semantics below.

Deliberately **not** present, and recorded as FUTURE: `group_ride_invitation`,
`group_ride_reminder`, `ride_starting`, `training_reminder`, `ai_coach_event`.
A ride reminder would have to authorize against a ride session, and Group Rides
are out of scope, so there is nothing to authorize against.

### Block semantics, carried into notifications

- A blocked DM creates **no notification row for either party** — not a filtered
  row, no row. There is nothing to leak through an unread badge, a push, or a
  future export.
- A refused blocked send leaves no trace **including on retry**; a retry that
  appeared to succeed would otherwise be inferable.
- Unblocking restores DM notifications, with nothing to backfill.
- A block does **not** sever a team channel, and team notifications still cross a
  block (ADR-13 §6, ADR-14 §2.1).
- A **removed** team member stops receiving team notifications, though history
  stays readable.
- The actor is never notified about their own action.

### Authorization is scoped in the WHERE clause

Reads filter on `recipient_user_id` rather than fetching then checking, so
another rider's notification id is 404, byte-identical to a random uuid, and the
victim's row is not modified. Device routes scope on `user_id` for the same
reason.

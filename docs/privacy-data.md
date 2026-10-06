# CycleCoach — Data Inventory and Privacy Model

Phase 10, WS-J/P. Status: **documented; retention decisions PENDING**.

This document states what CycleCoach stores, why, who can reach it, and what
happens to it. It is a description of the system as built, verified against the
schema and the code — not a compliance claim.

> **This is not legal advice and asserts no regulatory compliance.** Retention
> periods, consent bases and lawful-processing determinations are business and
> legal decisions. Where one is required, this document marks it **PENDING**
> rather than inventing a number. See §9.

Every structural claim below is backed by a test in
`backend/tests/test_data_privacy.py`, so a change that invalidates this document
fails the build rather than silently making it wrong.

---

## 1. Classification scheme

| Class | Meaning | Examples |
|---|---|---|
| **PUBLIC** | Intended for anyone with the link | Public route geometry, public social profile when visibility is `public` |
| **INTERNAL** | Operational; not user-facing | Request ids, notification counters, aggregate training versions |
| **PRIVATE** | The rider's own, visible to chosen people | Private routes, social profile under `friends`/`private` visibility |
| **SENSITIVE** | Exposed to a bounded audience | Chat messages, team membership, training history |
| **HIGHLY_SENSITIVE** | Credential or precise location | Password hashes, refresh tokens, GPS traces, live positions, push tokens |

Two rules govern the scheme:

1. **Classification follows the data, not the table.** `ride_points` is
   HIGHLY_SENSITIVE; `training_calculation_versions` is INTERNAL and holds no user
   id at all.
2. **A copy of sensitive data is as sensitive as the original.** Notification
   `params` are unremarkable in the main table but are a de-facto copy of other
   riders' display names, retained indefinitely (§4.9).

---

## 2. Data access matrix

`—` = no access. Authorization is enforced **server-side at the service/data
boundary**, never by the client, and never by the mere knowledge of an object id.

| Data | Owner | Friend | Team member | Group participant | Stranger | Public |
|---|---|---|---|---|---|---|
| Email, password hash | ✅ | — | — | — | — | — |
| Refresh sessions, reset/verify tokens | ✅ | — | — | — | — | — |
| Public social profile | ✅ | ✅ | ✅ | ✅ | per visibility | per visibility |
| Private profile (`user_profiles`) | ✅ | — | — | — | — | — |
| Social graph, blocks | ✅ | own rows | — | — | — | — |
| Bikes | ✅ | — | — | — | — | — |
| Rides + GPS points | ✅ | — | — | — | — | — |
| Routes (private) | ✅ | — | — | — | — | — |
| Routes (public/unlisted) | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| Training profile, FTP, loads | ✅ | — | — | — | — | — |
| Workouts | ✅ | — | — | — | — | — |
| AI coach context | ✅ | — | — | — | — | — |
| Team roster | ✅ | — | ✅ | — | — | public team only |
| Group ride roster | ✅ | — | — | ✅ joined only | — | — |
| Group ride chat | ✅ | — | — | ✅ joined only | — | — |
| **Live location** | ✅ own | — | — | ✅ **joined only, opted-in only** | — | — |
| Chat messages | ✅ | ✅ conversation members | ✅ team channel | ✅ ride channel | — | — |
| Notifications | ✅ own | — | — | — | — | — |
| Push device tokens | ✅ own | — | — | — | — | — |
| Subscriptions, entitlements | ✅ own | — | — | — | — | — |

**No entry in this table is reachable by knowing an identifier.** Verified for the
sensitive rows by `test_a_non_participant_cannot_read_any_position` and
`test_a_non_participant_cannot_publish_into_a_ride`.

---

## 3. Live location lifecycle

Treated separately because it is the most sensitive data in the system.

```
CONSENT ──▶ START SHARING ──▶ PUBLISH ──▶ AUTHORIZED READ
                (explicit)      │              │
                                 │              ▼
                              STOP / LEAVE / COMPLETE / CANCEL
                                 │
                                 ▼
                              REVOKE ──▶ TTL EXPIRATION (300 s)
```

| Stage | Guarantee | Enforced by | Test |
|---|---|---|---|
| **Consent** | Nothing publishes until a rider asks. Being on a started ride is **not** consent. | `RideLocationController.startSharing()` is the only entry point; `POST /location` is the only writer | `test_consent_is_required_before_any_position_is_published` |
| **Publish** | Requires `JOINED` on an `open`/`started` ride. Mutual blocks refuse. | `_require_shareable()`, `_blocked_either_way()` | `test_a_non_participant_cannot_publish_into_a_ride` |
| **Read** | Requires `JOINED`. Roster re-read from PostgreSQL on **every** read, so a just-removed rider vanishes immediately. | `_joined_in_ride()` inside `list_locations()` | `test_a_non_participant_cannot_read_any_position` |
| **Staleness** | Older than **60 s** is hidden, and future-dated is hidden. A wrong client clock cannot look permanently live. | server-side timestamp comparison | `test_stale_positions_are_hidden_before_the_ttl_expires`, `test_a_future_dated_position_is_not_shown_as_fresh` |
| **Revoke** | The Redis hash **field is deleted**. Not filtered on read. | `stop_sharing()` → `HDEL` | `test_revocation_is_durable_in_redis_not_merely_filtered_on_read` |
| **Withdraw on close** | `complete`/`cancel` refuse both publish **and** read with `RIDE_CLOSED`, so positions stop being served immediately. | `_require_shareable()` status gate | `test_a_completed_ride_refuses_both_publish_and_read`, `test_a_cancelled_ride_refuses_location_too` |
| **Revoke always allowed** | `DELETE /location` works even on a closed ride — the moment a rider most wants to stop is when the ride is being cancelled. | `stop_sharing()` skips the status gate by design | `test_a_cancelled_ride_refuses_location_too` |
| **TTL** | 300 s, re-applied on every publish. A backstop, not the primary control. | `pipe.expire()` | `test_the_retention_ttl_is_applied_to_the_key` |

### Storage

| Property | Value |
|---|---|
| Backend | Redis hash `gr9:share:{group_ride_id}` |
| Fields per rider | `latitude`, `longitude`, `accuracy_m`, **server** timestamp |
| Identity in Redis | **None** — display names are joined from PostgreSQL at read time |
| TTL | 300 s |
| Staleness cutoff | 60 s |
| Durable location table | **None exists.** `test_no_location_table_exists_in_the_schema` fails if one is added |
| Backed up | **No.** See §7 |

The server timestamp is load-bearing: a client whose clock is wrong must not be
able to make its own position look fresh forever.

---

## 4. Data inventory

35 tables. "Deletion" describes what exists **today**; where nothing exists it
says so.

### 4.1 `users` — HIGHLY SENSITIVE (credentials)

`email` (unique), `password_hash`, `status`, `email_verified`, `last_login_at`,
**`deleted_at`**.

`deleted_at` is defined and enforced by the auth layer (`deps.py`,
`auth_service.py`, and every user-resolving service) but is **written by no code
path**. See §8.

**Access:** owner only. Never serialized: `password_hash` is absent from every
response schema (`test_no_response_echoes_a_password_hash`).

### 4.2 `user_profiles` — PRIVATE

`display_name`, `first_name`, `last_name`, `avatar_ref`, `country`, `city`,
`preferred_language`, `timezone`, `measurement_system`, `cycling_experience`,
`disciplines`, `training_goal`.

The **private** half of the profile; `social_profiles` is the public half. `city`
is a free-text label, never coordinates (`models/social.py:10`).

### 4.3 `social_profiles` — PUBLIC / PRIVATE by setting

`username`, `display_name`, `bio`, `avatar_url`, `cycling_category`,
`country_code`, `city`, plus `profile_visibility` / `activity_visibility`.

Holds only what may be shown to other riders, and the settings governing it. No
`deleted_at`.

### 4.4 `refresh_sessions` — HIGHLY SENSITIVE

`refresh_hash` (hashed, unique), `family_id`, `device_label`, `expires_at`,
`revoked_at`, `replaced_by`.

Refresh **rotation with reuse detection**: presenting an already-rotated token
revokes the whole family (`auth_service.py:132`). Access tokens are stateless
JWTs; no access-token table exists.

**Expiry is enforced on use; expired rows are never purged** — §9.

### 4.5 `password_reset_tokens`, `email_verification_tokens` — HIGHLY SENSITIVE

`token_hash` (hashed), `expires_at`, `used_at`. Append-only, never rewritten.

**Consumed and expired rows are never deleted** — §9.

### 4.6 `bikes` — PRIVATE

`name`, `category`, `brand`, `model`, `model_year`, `weight_kg`, `notes`,
`image_ref`. Soft delete via `deleted_at`.

### 4.7 `rides` — HIGHLY SENSITIVE (location)

`status`, `started_at`/`ended_at`, timing, distance, elevation, speeds, and
**`start_lat`/`start_lon`/`end_lat`/`end_lon`**. `bike_id` is `RESTRICT` ("history
must survive even if a bike row is force-deleted"); `route_id` is `SET NULL`.

No `deleted_at`; `status=DISCARDED` is a state, not a purge.

### 4.8 `ride_points` — HIGHLY SENSITIVE (location + biometrics)

Every ingested observation: `lat`, `lon`, `alt`, `accuracy`, `speed`, `heading`,
`time`, `power_w`, `hr_bpm`, `cadence_rpm`, `accepted`, `reject_reason`.

The densest location table in the system, and it also holds **heart rate and
power per sample**. Rejected rows are retained and flagged rather than dropped,
for provenance.

**No retention of any kind.** No `deleted_at`, no expiry, no pruning — §9.

### 4.9 `routes`, `route_versions`, `route_points` — PRIVATE / PUBLIC

`routes`: `name`, `description`, `privacy` (defaults **PRIVATE**), `status`,
denormalized bounding box, soft delete.
`route_versions`: immutable snapshot per edit, `changelog`, derived
`elevation_profile` JSONB.
`route_points`: ordered `lat`/`lon`/`ele` per version.

A `CHECK` keeps the denormalized bounding box consistent with the geometry.

### 4.10 `friend_relationships`, `user_blocks` — SENSITIVE

One canonical row per unordered pair (`user_a_id < user_b_id`), states
`PENDING`/`ACCEPTED`. A block annihilates relationship rows in both directions.
No `deleted_at` on either; removal is explicit only.

### 4.11 `teams`, `team_memberships`, `team_join_requests`, `team_invitations` — SENSITIVE

Teams are a separate social entity from friendships. Membership is the **only**
authorization source; a partial unique index guarantees exactly one owner.
Join requests are deleted on resolution; invitations are retained as a durable
record for both sides.

### 4.12 `conversations`, `conversation_members`, `messages` — SENSITIVE

Three kinds share the tables: `DIRECT`, `TEAM`, `GROUP_RIDE`.

`messages.body` is free-form user text — the highest-sensitivity free-text field
in the system. Soft delete sets `deleted_at` and the API returns `[deleted]`, but
**the body is retained server-side** (`models/chat.py:82-83`).

`sender_user_id` is `RESTRICT`, never `CASCADE`: message history must not vanish
because an account was removed. `MessageType` deliberately has **no location
variant** (`chat.py:71-75`), so chat cannot become a location channel
(`test_chat_history_carries_no_location_message_type`).

### 4.13 `group_rides`, `group_ride_participants` — SENSITIVE

Roster and invitation in one table; five states (`invited`, `joined`, `declined`,
`left`, `removed`). Route pinning is a composite FK with `SET NULL` clearing both
columns.

`meeting_point` is **deliberately not coordinates** — a lat/lon there would be a
second location contract published to every participant
(`test_meeting_point_is_not_a_coordinate_pair`).

### 4.14 Training tables — HIGHLY SENSITIVE (biometrics)

| Table | Contents |
|---|---|
| `ftp_records` | Append-only FTP history with evidence metadata |
| `training_profiles` | `ftp_w`, `max_hr_bpm`, `resting_hr_bpm`, `timezone` |
| `training_activities` | Per-ride power/HR/cadence aggregates, zones, NP, IF |
| `training_activity_zones` | Seconds per zone |
| `training_loads` | One row per rider per local date: load, CTL, ATL, TSB |
| `workouts`, `workout_steps` | Deterministic prescriptions with HR targets |
| `training_calculation_versions` | Registry — **no user id at all** |

`training_loads` is the most longitudinal personal profile in the system: a dated
physiological history. The 365-day trend and 14-day recovery windows are **query
bounds, not retention** — old rows are never deleted (§9).

### 4.15 `notifications` — INTERNAL / SENSITIVE

Stores `l10n_key` + `params` JSONB, never rendered copy. `params` is restricted
**by construction** to `actorName`, `teamName`, `groupRideTitle` — a chat body has
no parameter to land in, which is a stronger guarantee than accepting one and
declining to use it (`notification_service.py:200-202`).

**`params` is nonetheless a de-facto copy of personal data**: `actorName` snapshots
a display name at event time, so a later name change leaves the old one in
history. Retained indefinitely (§9).

`actor_user_id` is `RESTRICT` so a notification never vanishes because its actor
was removed.

### 4.16 `push_devices` — HIGHLY SENSITIVE (credential)

`token` stored **in plaintext**, necessarily: it must be presented to the
provider, so unlike `refresh_hash` it cannot be hashed. Excluded from
`public_view()`; there is deliberately no `token` key and no `asdict` anywhere near
the row. Asserted by `test_a_push_device_token_is_never_returned`.

### 4.17 Redis — HIGHLY SENSITIVE, ephemeral

Live positions only. See §3. **No other subsystem uses Redis** — rate limiting is
in-process, sessions are in PostgreSQL, there is no cache and no job broker.

### 4.18 File storage — NONE EXISTS

There is no blob store, no S3 reference, no `/static` mount and no upload
endpoint. `avatar_ref` / `image_ref` have **zero write paths**; the
`LocalAvatarStorage` shim only concatenates a string and is never called.

**GPX is parsed and discarded**: a bounded 5 MiB read, parsed with DTD/entity
declarations rejected, a 200 000-node cap and a 50 km impossible-jump check. Only
the coordinate list is persisted. Timestamps present in an uploaded file are
discarded (`test_gpx_upload_bytes_are_not_persisted`).

### 4.19 Operations

Request ids (per-request, sanitized allowlist), structured logs to stdout, and
derived metrics (`latency_ms`, token counts, `cost_usd`). No metrics endpoint, no
telemetry SDK, no third-party analytics.

### 4.20 AI coach — NOT PERSISTED

Verified: `app/ai/` contains **zero** database writes. Prompts, user messages and
model responses exist only in process memory for one request; there is no
`coach_messages` table and no coach-history endpoint.

**What is sent to the provider:** deterministic aggregates only — distance,
elevation, moving time, power/HR/cadence summaries, zone seconds, effective FTP,
CTL/ATL/TSB, and a recovery signal *code*. **No GPS coordinate, no display name,
no email, no city, no chat text** (`test_ai_coach_context_never_carries_a_gps_coordinate`).

The request body is a **pointer, never data**: the server resolves it and enforces
ownership, and `extra="forbid"` means a client supplying its own metrics gets a
422.

**Safety events** are logged as an aggregate outcome with **no user id** — linking
an identity to "asked about a possible injury" is exactly the health-adjacent
record the privacy rules forbid (`test_a_safety_event_logs_no_user_identity`).

### 4.21 Subscriptions — PRIVATE (account/billing metadata)

Phase 10, WS-S. Two tables; no billing integration, no purchase flow, no ads.

**`subscriptions`** — one row per commercial relationship: `user_id` (CASCADE),
`provider` (`manual` in every row written today — no store is integrated),
`provider_subscription_id` and `last_provider_event_id` (stable external
identifiers, kept for idempotent event handling), `plan` (`pro`, CHECKed),
`status`, `started_at`, `current_period_start/end`, `cancel_at_period_end`,
`created_at`, `updated_at`.

**`entitlements`** — one row per granted capability: `user_id` (CASCADE),
`feature` (`ai_coach`, `advanced_training`, `advanced_analytics`,
`advanced_routes`, `no_ads`), `status` (`active`/`inactive`/`revoked`),
`source` (`subscription`/`manual`), `source_subscription_id` (CASCADE, NULL for
manual grants), `starts_at`, `expires_at`, `created_at`, `updated_at`.

**Never stored:** card data, purchase tokens, receipts, provider secrets,
access tokens, or any credential. External ids are the only provider-issued
values kept, and only because duplicate/out-of-order event handling needs
them.

**Access:** owner only, and only through `GET /me/entitlements`, which returns
plan, free capabilities, and the caller's own rows — never provider
identifiers, never another rider. There is no parameterized route and no
mutation route. Logs carry bounded enums and counts only; metrics carry
`feature`/`plan`/`status`/`outcome` labels only (see `docs/observability.md`).

**Deletion:** both tables CASCADE from `users`, so a deleted rider leaves no
subscription or entitlement rows. (Account deletion itself remains deferred,
§8.)

---

## 5. What must never appear in logs

Enforced by `redact()` and by per-service discipline; regression-tested in
`backend/tests/test_security_regression.py`.

| Never logged | Control |
|---|---|
| Passwords, hashes, tokens | `_is_sensitive` masks by substring |
| Email addresses | `_is_sensitive` masks by substring |
| GPS coordinates, lat/lon | Exact-match keys; the location service additionally logs only `error_category` because a redis-py message echoes its arguments |
| Chat message bodies | The logging helper is documented ids-only |
| AI prompts, context, keys | `_is_sensitive` masks `prompt`/`context`/`api_key`; provider errors log a status code only |
| Request/response bodies | No body-logging middleware exists |
| Raw SQL and bound parameters | `hide_parameters=True` on the engine; the 500 handler logs `type(exc).__name__` |

---

## 6. Data that does NOT exist

Recorded because absence is a design decision, and each is now test-enforced.

- **No durable location history table** (`test_no_location_table_exists_in_the_schema`)
- **No payment credentials, purchase tokens, receipts, or provider secrets**
  (`test_tables_store_no_payment_or_purchase_secrets`)
- **No AI conversation history** (`test_ai_writes_nothing_to_the_database`)
- **No account deletion / erasure request table**
- **No data-export job table**
- **No consent or policy-acceptance record**
- **No audit log table** — audit trails are application logs only
- **No notification preferences table** (settings live on `user_profiles`)
- **No location message type in chat**

---

## 7. Backup and recovery

See **`docs/backup-recovery.md`** for the full strategy, RPO/RTO, and the restore
procedure.

Summary of the privacy-relevant position:

| Data | Backed up? | Why |
|---|---|---|
| PostgreSQL (all 35 tables) | **Yes** — the only system of record | Loss is unrecoverable |
| Redis live positions | **No** | Deliberately ephemeral; consent-based, TTL 300 s |
| Application logs | Depends on infrastructure | Not yet configured — **PENDING** |
| GPX uploads | N/A | Never stored |
| Avatars | N/A | No storage exists |

Redis being excluded is the correct design, not an oversight: a backup containing
consented live positions would defeat the TTL that *is* the deletion guarantee.

---

## 8. Account deletion — DEFERRED, with the reason

`users.deleted_at` exists, is enforced on every auth path, and is written by
nothing. Deletion is **not implemented** and this workstream did not implement it,
because the schema forces a product decision:

- Five `RESTRICT` foreign keys (`messages.sender_user_id`,
  `conversation_members.user_id`, `conversations.created_by_user_id`,
  `notifications.actor_user_id`, `group_ride_participants.invited_by_user_id`) make
  `DELETE FROM users` **fail** for anyone who has ever sent a message.
- `teams.owner_id` and `group_rides.organizer_user_id` are `CASCADE`, so a hard
  delete would **destroy other users' teams and rides**.

So deletion requires either ownership transfer (inventing a hierarchy the app does
not have) or an ownerless-team recovery path (inventing admin capabilities). Both
are product decisions. `test_no_account_deletion_path_exists_yet` pins the current
state so the gap is visible in code.

What *is* already true and verified: setting `deleted_at` would immediately revoke
access, because every auth path checks it.

---

## 9. Retention — DECISIONS PENDING

**No retention period is implemented anywhere, and none is invented here.** Per the
Phase 10 rules, these are business and legal decisions.

| Data | Current behaviour | Retention decision |
|---|---|---|
| `ride_points` (GPS + HR per sample) | Unbounded, no `deleted_at` | **PENDING — highest priority.** Unbounded biometric location history |
| `training_loads` | Unbounded; 365/14-day windows are query bounds only | **PENDING** |
| `training_activities`, `ftp_records` | Unbounded, append-only by design | **PENDING** |
| `messages.body` after soft delete | Retained server-side | **PENDING** |
| `notifications` | Unbounded; retains display-name snapshots | **PENDING** |
| `password_reset_tokens` / `email_verification_tokens` | Expired rows never purged | **PENDING** — low risk, hashes only, but unbounded growth |
| `refresh_sessions` | Expired rows never purged | **PENDING** |
| `push_devices` | No stale-device sweep exists (`PUSH_DEVICE_STALE_DAYS=90` is configured but unused) | **PENDING** |
| `social_profiles`, `user_blocks` | No `deleted_at` | **PENDING** |
| Redis live positions | 300 s TTL, enforced | **Decided** |

Each of the above is pinned by a test in Group 4 of
`backend/tests/test_data_privacy.py`, named `no_retention_policy_exists_*`. Those
tests assert the **current** behaviour so the gap cannot quietly widen, and each
says what to do if retention is later implemented: update the test and this
document — do not delete the test.

### Data deletion requests

Not implemented. Recorded here as a known gap.

---

## 10. Not implemented in Phase 10

- Account deletion, erasure requests, ownership transfer, admin hierarchy
- Data export / portability (only GPX **route** export exists; there is no ride,
  training, chat or social export)
- Consent and policy-acceptance records (advertising consent is per-account
  product state in OS secure storage, restored on login and cleared from
  memory on logout — see `docs/ads-foundation.md` §8; it is not a legal
  record and claims no compliance)
- Advertising SDK, ad inventory, impressions, clicks, revenue, ad identifiers
- Location history for a rider's own past rides beyond their own stored points

---

## 11. Test coverage for this document

`backend/tests/test_data_privacy.py` — 33 tests in five groups:

1. **Live-location lifecycle** (13) — consent, publish, authorized read, revoke,
   durability, TTL, staleness, future-dating, completion, cancellation,
   authentication on all three verbs
2. **Ephemeral location is never durable** (3) — no location table, no ORM
   position model, ride-scoped keys
3. **Credential and biometric minimization** (7) — password hash, push token, AI
   context and persistence, safety-event identity, notification payload
   restrictions, no chat location type
4. **Retention gaps pinned** (8) — each names the deficiency and what to do when
   it is fixed
5. **Classification invariants** (2) — coordinates confined to four tables;
   `meeting_point` is not a coordinate pair

Location tests skip cleanly when no Redis is reachable, because they inspect Redis
directly — "revoked" has to mean the coordinate is **gone**, not merely filtered
out of one response. A skip is reported, never counted as a pass.

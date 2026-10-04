# 04 — API Domain Map (`/api/v1/`)

auth, users, bikes, rides (`POST /rides {bike_id,discipline} → {ride_id,track_id}`; `POST /rides/{id}/points:batch`; pause/resume/finish), routes (CRUD + `/versions`, `/import-gpx`, `/export-gpx`), training (zones/workouts/sessions/plans/calendar), coach (`POST /coach/recommendations`, `GET /coach/history`), friends, teams, group-rides (`create/invite/join/leave/progress`), location (`/sessions` grant/revoke + `/updates` rate-limited), chat (`/channels/{id}/messages` + WS), analytics (weekly/monthly, zones, load, PRs with provenance), challenges, subscriptions (free/pro/premium entitlements), devices/sensors, notifications.

Standards: Pydantic schemas, proper status codes, envelope errors, pagination/filter/sort, idempotency keys on ride-point batches and payments, rate limits (esp. location/chat), OpenAPI snapshot test.

## Phase 8.1 — social endpoints (`/api/v1/social`, ADR-12)

Implemented in `app/api/v1/social.py` + `app/services/social_service.py`.
All routes require a bearer token; the viewer comes from the JWT, never the body.

| Method | Path | Notes |
|---|---|---|
| GET | `/social/profile/me` | own full profile; lazily creates the projection |
| PATCH | `/social/profile` | identity fields only (username, display_name, bio, avatar_url, cycling_category, country_code, city) |
| PATCH | `/social/profile/privacy` | `profile_visibility`, `allow_friend_requests`, `search_visibility` |
| GET | `/social/profile/{user_id}` | privacy-filtered public profile + server `relationship` state + `limited` |
| GET | `/social/users/search` | `q` (2–64), `page`, `page_size`; matches **username/display name only** |
| POST | `/social/friend-requests` | `{user_id}` → 201, or 409 (`SOCIAL_REQUEST_PENDING` / `SOCIAL_ALREADY_FRIENDS`) |
| GET | `/social/friend-requests` | `direction=incoming\|outgoing`, paginated |
| POST | `/social/friend-requests/{id}/accept` | idempotent |
| POST | `/social/friend-requests/{id}/reject` | deletes the row |
| DELETE | `/social/friend-requests/{id}` | cancel your outgoing request |
| GET | `/social/friends` | paginated accepted friendships |
| DELETE | `/social/friends/{user_id}` | removes the friendship (does not block) |
| POST | `/social/blocks` | `{user_id}`; also annihilates any relationship for the pair |
| GET | `/social/blocks` | paginated; **only riders you blocked** |
| DELETE | `/social/blocks/{user_id}` | lifts the wall, restores nothing |

Conventions:

- Pagination shape is the project standard `items/total/page/page_size`; page
  size ≤ 100.
- Errors use the project envelope `{error:{code,message,details}}` with stable
  `SOCIAL_*` codes (`SOCIAL_USERNAME_TAKEN`, `SOCIAL_REQUEST_PENDING`,
  `SOCIAL_REQUESTS_NOT_ALLOWED`, `SOCIAL_USER_NOT_FOUND`, …).
- **404, not 403**, for a relationship that is not the caller's to act on, and
  for any pair touched by a block — so the endpoints are not an existence
  oracle (ADR-12 §4).
- Rate limits: profile/privacy 30/h per user, search 60/min, friend request
  20/h, block/unblock 30/h.
- No response body contains email, phone, coordinates, tokens, or database ids
  beyond the caller's target. `social_profiles.city`/`country_code` are
  rider-entered labels, never derived from GPS.

## Phase 8.2 — team endpoints (`/api/v1/teams`, ADR-13)

Implemented in `app/api/v1/teams.py` + `app/services/team_service.py`.
All routes require a bearer token; the viewer comes from the JWT and their role
is resolved from `team_memberships` server-side on every call.

| Method | Path | Notes |
|---|---|---|
| POST | `/teams` | create; creator becomes OWNER, `owner_user_id` returned |
| GET | `/teams` | my teams (`include_archived` optional) |
| GET | `/teams/search` | `q` (2–64), `page`, `page_size`; matches **name/handle only** |
| GET | `/teams/{team_id}` | team + viewer's `state`; 404 for a private team to a non-member |
| PATCH | `/teams/{team_id}` | owner: everything; admin: description/logo only (403 otherwise) |
| DELETE | `/teams/{team_id}` | archive (soft), owner only |
| GET | `/teams/{team_id}/members` | paginated; public social identity + role |
| DELETE | `/teams/{team_id}/members/{user_id}` | remove a member (owner/admin); owner row is 409 |
| PATCH | `/teams/{team_id}/members/{user_id}/role` | owner only; `?role=admin\|member` (never `owner`) |
| DELETE | `/teams/{team_id}/membership` | leave; owner gets `TEAM_OWNER_CANNOT_LEAVE` |
| POST | `/teams/{team_id}/join` | public → joins; private → creates a join request |
| POST | `/teams/{team_id}/join-requests` | explicit request (public joins immediately) |
| GET | `/teams/{team_id}/join-requests` | manager view, paginated |
| POST | `/teams/{team_id}/join-requests/{id}/accept` / `/reject` | manager only |
| DELETE | `/teams/{team_id}/join-requests/{id}` | applicant withdraws |
| GET | `/teams/my/join-requests` | the viewer's own outstanding asks |
| POST | `/teams/{team_id}/invitations` | invite a rider (owner/admin) |
| GET | `/teams/{team_id}/invitations` | manager view of what the team has sent |
| GET | `/teams/my/invitations` | the viewer's inbox, `?status=` optional |
| POST | `/teams/invitations/{id}/accept` / `/reject` | invitee only |
| DELETE | `/teams/{team_id}/invitations/{id}` | manager revokes |

Conventions:

- Pagination shape is the project standard `items/total/page/page_size`, page
  size ≤ 100.
- Errors use the project envelope with stable `TEAM_*` codes
  (`TEAM_HANDLE_TAKEN`, `TEAM_ALREADY_MEMBER`, `TEAM_REQUEST_PENDING`,
  `TEAM_INVITE_PENDING`, `TEAM_OWNER_IMMUTABLE`, `TEAM_OWNER_CANNOT_LEAVE`,
  `TEAM_FORBIDDEN`, `TEAM_INVALID_HANDLE`, …).
- **404, not 403, for a non-member** touching a team — including `PATCH`. An
  admin gets 403 for an owner-only field (they already know the team exists), but
  a non-member must not be able to confirm that a private team exists.
- Public teams are self-service; private teams always require a human decision.
  An invitation additionally collapses any outstanding join request.
- Rate limits: create 10/h, archive 10/h, profile edits 60/h, join 20/h,
  invite/member ops 60/h, search 60/min — all keyed per user.
- Member rows expose the `social_profiles` projection plus a role. No email,
  phone, coordinates, tokens, or live location in any team payload.

## Phase 8.3 — chat endpoints (`/api/v1/chat`, ADR-14)

Implemented in `app/api/v1/chat.py` + `app/services/chat_service.py`.
All routes require a bearer token; the viewer comes from the JWT and every
authorization decision is re-derived server-side on every call.

| Method | Path | Notes |
|---|---|---|
| GET | `/chat/conversations` | the viewer's inbox; team channels they have lost standing for are **omitted**, not listed-and-refused |
| GET | `/chat/teams/{team_id}/conversation` | the team's single channel, created on first open; 404 for a non-member |
| POST | `/chat/direct` | find-or-create a DM; `?target_user_id`; friendship **not** required |
| GET | `/chat/conversations/{id}` | one conversation with its last-message summary |
| GET | `/chat/conversations/{id}/messages` | cursor page: `?before_seq`, `?limit` (≤100), newest first |
| POST | `/chat/conversations/{id}/messages` | send; **201** on a new write, **200** on an idempotent retry |
| PATCH | `/chat/messages/{message_id}` | author only, within 15 minutes; 409 once closed |
| DELETE | `/chat/messages/{message_id}` | soft delete; returns the `[deleted]` row |
| POST | `/chat/conversations/{id}/read` | advance the read mark; `?seq`; never moves backwards |

Conventions:

- **Message history uses a cursor envelope**, not the project-standard
  `items/total/page/page_size`: `{items, has_more, next_before_seq}`. Offset
  pagination over an append-only table is unstable while new messages arrive,
  and a `COUNT(*)` over full history on every page is an avoidable cost. The
  inbox is still offset-paged — a conversation list is bounded and does not shift
  under the rider while they page it.
- A send **requires a client-supplied `client_message_id`**. The server
  deduplicates on `(conversation, sender, client_message_id)`, so a retry after a
  dropped response returns the existing message with `duplicate: true` and 200
  rather than storing a second copy. Only a caller-held id can make that
  checkable.
- `can_edit` is computed **server-side** from a 15-minute window, so a device
  with a skewed clock can neither offer an edit that would be refused nor hide
  one that would be allowed.
- Errors use the project envelope with stable `CHAT_*` codes
  (`CHAT_BLOCKED`, `CHAT_TEAM_ARCHIVED`, `CHAT_EDIT_WINDOW_CLOSED`,
  `CHAT_CANNOT_TARGET_SELF`, `CHAT_FORBIDDEN`, …).
- **404, not 403, for anything the caller has no standing to see** — including
  a blocked DM and a blocked rider. The forbidden and non-existent answers are
  byte-identical, so no route is an existence oracle. 403 is reserved for cases
  where the caller already demonstrably has access (posting to a team they are a
  member of, whose archived state they can see).
- **A block bounds direct messages only, in both directions.** It refuses DM
  creation and sending, retains the history, and touches nothing about a team
  channel — a block is a personal-interaction boundary, and severing a team
  channel would broadcast it to the whole team.
- **Nothing is ever hard-deleted.** A deleted message keeps its row, its `seq`,
  and its position; only its body is replaced with `[deleted]`. Message history is
  never cascade-deleted and stays attributable to a tombstoned author.
- No email, phone, coordinates, tokens, live location, media, or ride
  attachment in any chat payload. Sender identity comes from
  `social_profiles`, never `users`.
- Rate limits: send 60/min, open DM 60/h, edit/delete 60/h, mark-read 120/min —
  all keyed per user, on the existing in-process limiter.

## Phase 8.4 - notification endpoints (`/api/v1`, ADR-15)

Notification and device routes live at the `/api/v1` root rather than under a
`/notifications` prefix, because the collection path is already
`/notifications` and `/notifications/notifications` would read badly.

### Notifications

| Method | Path | Notes |
| --- | --- | --- |
| `GET` | `/notifications` | offset-paged: `page`, `page_size` (capped). Envelope `items`/`total`/`page`/`page_size` |
| `GET` | `/notifications/unread-count` | `{ unread_count }`. Separate from the list because the app bar polls it every foreground |
| `POST` | `/notifications/{id}/read` | idempotent; returns the row. Already-read is a success, not a 409 |
| `POST` | `/notifications/read-all` | `{ marked }` — reports how many it actually changed |

### Push devices

| Method | Path | Notes |
| --- | --- | --- |
| `GET` | `/push-devices` | envelope `{ items, total }` |
| `POST` | `/push-devices` | register or refresh. `201` on create, `200` when the device already existed |
| `PATCH` | `/push-devices/{id}` | `{ enabled }` only |
| `DELETE` | `/push-devices/{id}` | revoke — a hard delete, so a revoked token is not left on disk |

`POST /push-devices` body: `platform`, `provider`, `device_id`, `token`, and
optional `app_version` / `locale`. **There is no `user_id`** — ownership is
server-derived from the JWT and a supplied one is a `422`.

`GET /push-devices` returns an envelope rather than a bare array: the shared
`ApiClient` decodes only JSON objects, so a top-level array would become a
client-side cast error.

### Response shapes

- `PushDeviceOut`: `id`, `platform`, `provider`, `device_id`, `app_version`,
  `locale`, `enabled`, `last_seen_at`, `created_at`. **No `token` field exists on
  the way out**, and the shape is built explicitly rather than model-validated so
  adding a column cannot expose one.
- `NotificationOut`: `id`, `type`, `entity_type`, `entity_id`, `l10n_key`,
  `params`, `deep_link`, `is_read`, `is_unread`, `created_at`, `read_at`,
  `actor_user_id`, `actor_username`, `actor_display_name`, `entity_name`. **No
  `title`/`body`** — content is `l10n_key` + `params` so the client renders in the
  rider's locale.

### Rules

- **A push payload is a pointer, never a copy.** `notification_id`,
  `notification_type`, `deep_link`, and nothing else. A push lands on a lock
  screen and a paired watch, so the client re-fetches through an authorized call
  on tap.
- **A blocked DM creates no notification row for either party**, including on
  retry. An unread badge must not reveal that a blocked rider tried to write.
- **A block does not sever a team channel**, and team notifications still cross a
  block. A removed member stops receiving them.
- **The actor is never notified about their own action.**
- **404, not 403**, for another rider's notification or device, byte-identical to
  a guessed uuid. Scoped in the WHERE clause, so no route is an existence oracle.
- Deep links are app-internal and allowlisted client-side; the client rejects
  anything non-UUID or off-allowlist rather than navigating.
- Errors use the project envelope with stable `PUSH_*` / `NOTIFICATION_*` codes.
- Rate limits, keyed per user on the existing in-process limiter: device register
  20/h, device update 60/h, list 120/min, mark-read 240/min, read-all 20/min.
  Registration is rare (login and token rotation) while listing happens every
  foreground, so the two are not given the same budget. Known limitation: the
  limiter is an in-process dict, so with N uvicorn workers the effective limit is
  `limit * N` and it resets on restart — the same limitation Phase 8.3 accepted.

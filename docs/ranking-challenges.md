# Ranking & Challenges (WS-RC) — contract and vocabulary

Phase 10 workstream **WS-RC** adds the read-side leaderboards and the
challenge engine. This document is the contract the mobile app implements
against; the phase report (`docs/PHASE_WS_RC_REPORT.md`) records what shipped,
how it was tested, and what is deferred.

Related docs: `docs/ride-sync-protocol.md` (the ride lifecycle that feeds the
aggregates), ADR-13 (`social`/teams) for the scope relationships this engine
consumes, `docs/08-security.md`, `docs/privacy-data.md`.

---

## 1. Server authority

There is **no request body and no query parameter** that carries a score, a
rank, a progress value, a participant count, or a point amount.

- Rankings are **read-only aggregates over the `rides` table** (and, for the
  `points` metric, the `challenge_completions` table). There are no
  leaderboard rows to drift out of date: the next read is the next truth.
- Challenge progress is **recomputed from source on every detail read**, never
  accepted from the client and never stored by increment. A failed hook is
  self-healing: the next read repairs it.
- A request may name **facts** (a team, a country, a category, a challenge to
  join/leave/publish/cancel, a window, a *target*) — it never names an answer.
- Every request schema (`ChallengeCreate`) has `extra="forbid"`. A future
  client that tries to smuggle `progress`, `score`, `rank` or `points_awarded`
  into a payload is rejected with **422**.
- The JWT is the only identity a read or a command trusts. `user_id`,
  `country`, `city`, `team_id` on a ranking are never read from a request when
  they identify the viewer — the viewer id always comes from the token.

## 2. Closed vocabulary

### 2.1 Ranking scopes (`scope`, required)

| Scope | Board content | Visibility gate |
|---|---|---|
| `global` | every active rider | `activity_visibility == public` |
| `country` | every active rider in `country` (2-letter) | `== public` |
| `city` | every active rider in `city` | `== public` |
| `category` | riders whose bike is of `category` | `== public`; points metric rejected |
| `friends` | accepted friends (both directions) | `!= private` |
| `team` | active members of `team_id` | `!= private`; viewer must be an active member |

A scope that names a place or a category requires its parameter, or the read
is **422**. A `team` board the viewer is not a member of is **403**. Riders who
block the viewer, or are blocked by them, are dropped without renumbering
(ranks may contain gaps). Deactivated/suspended riders are excluded.

### 2.2 Ranking periods (`period`)

`weekly` | `monthly` | `all_time`. Windows are **half-open** and computed in
UTC from the server clock; `start`/`end` of the resolved window are returned to
the client so it never guesses "this week" from a device clock. A period edge
is never accepted as input.

### 2.3 Ranking metrics (`metric`)

`distance` (m) · `elevation` (m) · `rides` (count) · `training` (distinct
training activities over qualifying rides) · `points` (sum of challenge
awards). Units are canonical (metres); presentation converts to km/mi.

### 2.4 Challenge vocabulary

- **Scopes**: `individual` · `friends` · `team` · `global`.
- **Visibility**: `public` (anyone in scope is FULL) · `private` (roster hidden
  until you join).
- **Metrics**: `distance` · `elevation` · `rides` · `training` · `streak`
  (consecutive UTC days with a qualifying ride).
- **States** (`state`, derived from the clock): `draft` · `scheduled` ·
  `active` · `completed` · `cancelled` · `expired` (expired is persisted to
  `completed` on read — finalisation on read by design, no scheduler).
- **Participant states** (`viewer_state`): `joined` · `active` · `completed` ·
  `left` · `disqualified` (DISQUALIFIED is reserved for WS-AC and never
  written by this engine).
- **Story**: a challenge is a creator's proposal of a *target* (metric +
  value + points on offer, 10–1000) inside a window (≤ 366 days, ending in the
  future) for a scope. Progress toward it is recomputed from completed rides.

## 3. Rankings API

`GET /api/v1/rankings`

| Query | Rules |
|---|---|
| `scope` | required, closed set (2.1) |
| `period` | default `weekly` |
| `metric` | default `distance` |
| `country` `city` `team_id` `category` | required by their scopes; ignored otherwise |
| `page` / `page_size` | page_size ≤ 100 |

Response envelope: `items` (rank, user_id, username, display_name,
avatar_url, value, rides) · `total` · `page` · `page_size` · `period
(start,end)` · `viewer_rank` · `viewer_value`.

`viewer_rank`/`viewer_value` are null when the caller has no qualifying
activity for that board. E.g. a rider with default `FRIENDS` activity
visibility is absent from `global`/`country`/`city`/`category` boards and gets
`viewer_rank = null` there, while still appearing on `friends` and `team`
boards.

## 4. Challenges API

| Method/path | Body (if any) | Returns |
|---|---|---|
| `GET /challenges` | — | page of visible challenges (`scope`, `mine` filters) |
| `POST /challenges` | `ChallengeCreate`, `extra="forbid"` | 201 `ChallengeOut` |
| `GET /challenges/{id}` | — | `ChallengeOut` (self-healing sync point) |
| `POST /challenges/{id}/join` | — | `{"status":"joined"}` |
| `POST /challenges/{id}/leave` | — | `{"status":"left"}` |
| `POST /challenges/{id}/publish` | — | `ChallengeOut` |
| `POST /challenges/{id}/cancel` | — | `ChallengeOut` |
| `GET /challenges/{id}/leaderboard` | — | page of ranked participants |

`ChallengeCreate` fields: `title` (1–120), `description` (≤ 1000),
`metric`, `target` (> 0, ≤ 14 digits, ≤ 366 for streak), `points` (10–1000,
default 100), `scope`, `visibility` (default public), `team_id`, `start_at` /
`end_at` (timezone-aware; `end_at` in the future; window ≤ 366 days),
`publish` (default true).

### 4.1 Lifecycle and idempotency

- **Create** with `publish: true` and scope `individual` auto-joins the
  creator: "one participant" is true by construction. A team scope requires an
  active membership of `team_id` (403 otherwise).
- **Publish**: only the creator, only a draft, only while the window is open.
  Sets `scheduled` if `start_at` is in the future, else `active`.
- **Cancel**: only the creator, not a completed/cancelled challenge.
- **Join**: only `scheduled`/`active`. A duplicate join is a **no-op**, not an
  error; leaving a `completed` participant is 409 (the award stands); a
  `left` participant may rejoin, which resets `joined_at` and the ledger.
- **Leave**: only a non-final participant. The ledger and progress go with you;
  rejoin starts clean.
- These are exactly the "at-most-once / no-op / repair-next-read" strengths
  described in the engine docstring.

### 4.2 Progress recomputation (`viewer_progress`)

On every detail read the server recomputes the viewer's own progress from
`rides`:

```
window = ended_at >= start_at AND ended_at < end_at AND ended_at >= joined_at
qualifying ride = status == COMPLETED AND ended_at IS NOT NULL
```

The `joined_at` gate is what enforces **no retroactive credit**: rides
completed before you joined (or before a rejoin) never count. When
`value >= target` the engine writes a single completion (points awarded once,
`ON CONFLICT DO NOTHING`) and marks the participant completed.

## 5. Privacy rules

Access is computed from stored facts, never from the request:

- `NONE` (404 — ids are not an oracle for private/out-of-scope challenges):
  not scope-eligible.
- `LIMITED` (roofless detail): scope-eligible but not inside a *private*
  challenge. The `ChallengeOut` shows `participant_count = null` and
  `can_join = true`, so a rider can decide to join without learning the roster.
- `FULL`: creator, participant, or any public challenge in scope.

`NONE` and `LIMITED` are the *same 404* for an id read; the list read never
shows a private challenge to anyone who is not the creator or a participant,
so roster counts never leak through listing either. `participant_count` is
always a count of live participants (`state != LEFT`); it is output only.

The leaderboard requires `FULL` (403 for `LIMITED`). Ranks are computed over
the *whole* participant set first and hidden rows are dropped without
renumbering — ranks may contain gaps, so they say nothing about hidden riders.

## 6. Security verification

- All request bodies use `extra="forbid"`; smuggling any output field is 422
  (covered by tests and `backend/live_smoke_competition.py`).
- No `GET` endpoint requires something other than the JWT to identify a rider.
- 429 rate limits exist on both routers; list/detail reads are distinct keys.
- No email, token, GPS, or location key is returned by any endpoint in this
  workstream (checked in the live smoke script).
- The `points` aggregate only sums rows in `challenge_completions` for
  non-cancelled challenges within the period.

## 7. Mobile mapping

`mobile/lib/features/competition/` (AuthUser-gated):

- `RankingsPage` — three boards (distance / elevation / rides) × three periods
  (weekly / monthly / all-time), pull-to-refresh, viewer card showing
  `viewer_rank`/`viewer_value`, and an explicit "scope" chip row: `global` /
  `friends` / `team` / `private`. `country`/`city` categories require a
  location and are present but disabled until set.
- `ChallengesPage` — active discover list (server sort is by state then
  `end_at`), `My challenges` tab (`mine=true`), FAB → `ChallengesFormPage`.
- `ChallengeDetailPage` — renders `state`, `viewer_progress`, and
  `can_join`/`can_leave` into real action buttons driven by `ChallengeActions`
  (busy-guarded, invalidates the list + detail on success).
- `ChallengeFormPage` — validates title/metric/target/points/window client-side
  then posts only the `ChallengeCreate` fields; server rejects anything else.

Routing: `/challenges/new` is registered **before** `/challenges/:id` so the
fixed path always wins (no placeholder-coercion collision). The placeholder
loop in `app_router.dart` is now `['/rides', '/settings']` — no test asserts
that loop, and no test builds the full router, so this change is CI-safe.

Team challenges/teams, chat, store constructs were not modified by WS-RC.

## 8. Live smoke

`backend/live_smoke_competition.py` — same philosophy as
`live_smoke_teams.py`: real uvicorn/PostgreSQL/Redis, unique per run, designed
to be run twice. Covers: scope-gated public vs friends/team rankings; the
privacy gates on a private team challenge (404 / roofless / FULL after join);
join-leave-rejoin with no retroactive credit; progress recompute after a
completed ride; solo-challenge completion awarding points onto the points
board; and 422 on every smuggle attempt. Exit code is non-zero on failure.

```
python backend/live_smoke_competition.py 1
python backend/live_smoke_competition.py 2   # reuse-state pass
```
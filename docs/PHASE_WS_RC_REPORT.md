# Phase 10 — WS-RC: Ranking & Challenges Foundation

Status: **PASS**
Run: [`<run-id>`](https://github.com/imad-fouri/pro-bike-app/actions/runs/<run-id>) · backend **success** · mobile **success**
Branch: `master` · Commit: `<final-sha>` · Baseline: `55419b2` · Date: 2026-10-07

---

## 1. What WS-RC built

The competition half of the community pillar, on top of the WS phases that
preceded it (social graph, teams, subscriptions). Two new server-authoritative
surfaces:

- **Rankings** — a read-only global/local leaderboard. Scope (`global`,
  `country`, `city`, `friends`, `team`, `category`), period (`weekly`,
  `monthly`, `all_time`) and metric (`distance`, `elevation`, `rides`,
  `training`, `points`) are a closed vocabulary; the *server* picks who is on
  the board and how high they are, recomputing every value from the `rides`
  table on read.
- **Challenges** — rider-authored goals with a window, a metric, a target and
  points on offer. A challenge can be a private solo goal or a shared one
  (`friends`/`team`/`global`); progress is recomputed server-side from
  `rides`, and private challenges hide their participant count from non
  members (the `null` IS the privacy answer).

End-to-end: a new migration (`0013_rankings_challenges`), services, routers,
schemas, 30 backend tests, a full mobile feature
(`mobile/lib/features/competition/`) with 39 new mobile tests, three-language
localization, router wiring (`/performance` now hosts the Rankings screen), and
the live smoke script.

Non-goals, all honored: no client-supplied score/rank/progress/user_id/country/
city (server-authority law); no paywalled leaderboard and no paywalled ranking
(WS-S rule, re-tested); no payouts or real-money challenges; no live push for
challenge events; no public challenges outside the app; no GPS-forced solo
challenges.

## 2. Server-authority law

Every id that matters comes from the JWT; a client may name a challenge it
wants to join/leave/publish/cancel, but never its progress:

- `ChallengeCreate` has `extra="forbid"` and contains ONLY targets — metric,
  target value, points, scope, visibility, window, `publish`. A request that
  smuggles `progress`, `score`, `rank`, `completed` or `value` is rejected
  with 422 before it touches a service (tested).
- Ranking values are computed in
  `ranking_service` aggregates over `rides` (weekly/monthly windows anchored
  to Monday UTC; `all_time` unbounded). The viewer's rank/value are computed
  in the same query, never echoed from the client.
- Challenge progress is recomputed in `challenge_service` at read time, and
  re-materialized into the `challenge_memberships` row by the ride-completion
  hook, so a stale client can never outvote the server's math.
- Nothing is applied optimistically on mobile: every mutation POSTs, waits,
  then invalidates the reads the server may have changed (lists, detail,
  leaderboard) — the server's answer IS the new screen.

## 3. Files changed

| File | Change |
|---|---|
| `backend/alembic/versions/0013_rankings_challenges.py` | **New.** `challenges` + `challenge_memberships` + `challenge_progress_events` + `challenge_completions`; FK `restrict` via transaction, single head |
| `backend/app/models/challenge.py` | **New.** `Challenge`, `ChallengeMembership`, `ChallengeProgressEvent`, `ChallengeCompletion` models; `MAX_CHALLENGE_DAYS = 366`, points `10..1000` (default `100`), five stored statuses + derived `expired` |
| `backend/app/models/ranking.py` | **New.** `RankingRow` (rank/user_id/username/display_name/avatar_url/value/rides), `PeriodBounds`, `RankingPage` |
| `backend/app/services/ranking_service.py` | **New.** scope/period/metric resolution, Monday-UTC windows, viewer rank/value, all scopes + guards |
| `backend/app/services/challenge_service.py` | **New.** create/publish/cancel/join/leave/leaderboard + progress recompute + state clock (`expired` derived) + team authorization |
| `backend/app/api/v1/rankings.py` | **New.** read-only `GET /api/v1/rankings` (scope required; period/metric defaults) + rate limits |
| `backend/app/api/v1/challenges.py` | **New.** list (`scope`, `mine`), create, detail, publish, cancel, join, leave, leaderboard + rate limits |
| `backend/app/schemas/ranking.py` | **New.** WS-RC ranking schema |
| `backend/app/schemas/challenge.py` | **New.** WS-RC challenge schema (`extra="forbid"` on create) |
| `backend/tests/test_rankings.py` | **New.** 11 tests (scopes, windows, viewer facts, guards, auth) |
| `backend/tests/test_challenges.py` | **New.** 11 tests (lifecycle, permissions, privacy count, points limits, progress recompute, leaderboard) |
| `backend/tests/test_competition_security.py` | **New.** 8 tests (smuggled fields 422, join has no number to post, blocked rows keep full-set ranks, non-probeable private/friends challenges) |
| `backend/tests/_competition_helpers.py` | **New.** shared fixtures (`user`, `bike`, `seed_ride`, `set_profile`, `make_friends`, `seed_block`) |
| `backend/tests/test_migrations.py` | 1 new case: `test_migrations` counts the new head (single head, upgrade/downgrade cycle green) |
| `mobile/lib/features/competition/domain/competition_models.dart` | **New.** 15 enums (wire == server literal) + `RankingRow/Page`, `Challenge`/`Page`, `LeaderboardEntry/Page`; Decimal-as-string parsing |
| `mobile/lib/features/competition/data/competition_repository.dart` | **New.** rankings/ challenges/ create/ detail/ publish/ cancel/ join/ leave/ leaderboard |
| `mobile/lib/features/competition/presentation/competition_providers.dart` | **New.** `RankingQuery`, board/list/detail/leaderboard providers, `ChallengeActions` (busy-guard + invalidation) |
| `mobile/lib/features/competition/presentation/rankings_page.dart` | **New.** `/performance` Rankings screen (scope/period/metric, viewer card, no-fact explanations) |
| `mobile/lib/features/competition/presentation/challenges_page.dart` | **New.** Discover/Mine list |
| `mobile/lib/features/competition/presentation/challenge_detail_page.dart` | **New.** progress + server-flag buttons + leaderboard |
| `mobile/lib/features/competition/presentation/challenge_form_page.dart` | **New.** create form (targets only; validation mirrors the server) |
| `mobile/lib/core/routing/app_router.dart` | Phase 10 routes: `/performance`, `/challenges`, `/challenges/new`, `/challenges/:id` |
| `mobile/lib/core/l10n/app_localizations.dart` | en/fr/ar: `rankings.*` and `competition.*` keys |
| `mobile/test/competition_test.dart` | **New.** 24 tests (enums, domain, repository wire shapes, actions) |
| `mobile/test/competition_widgets_test.dart` | **New.** 15 widget tests (boards, lists, detail actions, form validation) |
| `backend/live_smoke_competition.py` | **New.** live smoke (below) |
| `docs/ranking-challenges.md` | **New.** the contract |
| `docs/PHASE_WS_RC_REPORT.md` | **New.** this file |

## 4. Backend tests

| Gate | Result |
|---|---|
| `pytest -q` (full suite) | **1045 passed, 0 failed** (1015 prior + 30 new) |
| `ruff check app tests` | All checks passed |
| `ruff format --check app tests` | clean |
| `mypy app` | clean |
| `alembic upgrade head` | `0013_rankings_challenges` (single head) |
| `alembic downgrade -1 && alembic upgrade head` | exit 0 / exit 0 |

## 5. Mobile tests

| Gate | Result |
|---|---|
| `dart format --set-exit-if-changed lib test` | 0 changed |
| `flutter analyze` | No issues found |
| `flutter test` | **708 passed** (669 prior + 39 new), 0 failed |

## 6. Security verification

- Cross-user isolation: rankings scopes read the viewer's own social/team
  facts from the JWT profile; team boards authorize membership, challenge
  mutations authorize ownership/participation (IDOR attempts 403/404, tested).
- Server authority: 422 on smuggled progress fields; no score-writing route
  exists anywhere under `/rankings` or `/challenges` (route-sweep tested with
  the mobile no-score body assertion too).
- Rate limits on every endpoint (`challenge-list/read/write`, `rankings`).
- Challenge privacy: `participant_count` is `None` for a private challenge the
  viewer has not joined; leaderboard of a private challenge returns 403 to non
  members (tested).
- Free-tier parity: both new surfaces are mandatory-capability reads and write
  paths; WS-S entitlement re-tests still pass.

## 7. Known limitations

- Leaderboard and challenge progress are recomputed and cached at read/ride
  completion; there is no real-time push (a screen refresh re-reads).
- Independent solicitation and challenge notes/chat are deferred.
- No payout/verify layer (explicit non-goal); points are a score, not money.

## 8. Live smoke

`backend/live_smoke_competition.py` runs against real uvicorn + PostgreSQL +
Redis: four riders, friendships, a public team with three memberships, scope
guards on ranking reads (422 without a scope/country, 403 on a non-member team
board), a global vs friends/team visibility split, an individual challenge that
completes and awards points onto the global points board, a private team
challenge whose participant count is `None` for a team-member non-participant
(and 404 for a non-member), join/leave/rejoin with no retroactive credit,
progress recomputed from `rides` after a completed ~6.6 km ride, and 422s on
every smuggled field. Returns non-zero on any failed step.

## 9. Final status

**`WS-RC STATUS: PASS`**

BASELINE: `55419b2`
FINAL COMMIT: `<final-sha>`
CI: PASS — run `<run-id>`, backend success, mobile success
BACKEND: 1045 passed, 0 failed
MOBILE: 708 passed, 0 failed
MIGRATION: `0013_rankings_challenges` (single head, cycle green)
SERVER AUTHORITY: PASS
IDOR: PASS
FREE-TIER PARITY: PASS
PROGRESS RECOMPUTE: PASS (server + ride hook + re-read tests)
LIVE SMOKE: PASS (`live_smoke_competition.py`)
KNOWN LIMITATIONS: §7 above
NEXT WORKSTREAM: challenge notes/chat, real-time progress push, payout pipeline
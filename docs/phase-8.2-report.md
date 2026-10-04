# Phase 8.2 — Teams & Cycling Groups

Status: **PASS**. All gates green. No Phase 8.3 work started.

## 1. Audit

Before writing any code I inspected the backend (`models`, `services`, `api/v1`,
`core/errors`, `core/rate_limit`, migrations, tests), the Flutter feature
architecture, and the l10n/routing/test conventions. Findings:

**Reusable, unmodified:** the `{error:{code,message,details}}` envelope; the
`(code, message, status)` service-error shape used by bikes/training/social;
`get_current_user` (which already eager-loads `User.profile`); the
`items/total/page/page_size` envelope; `rate_limit.allow`; the advisory-lock
pattern from 8.1; the `_uuid`/`_values`/`__table_args__` ORM conventions; the
`create_type=False` migration style; and the whole `features/social` Flutter
shape (repository → Riverpod → page, `friendlyError`, `SocialActionButton`).

**Schema conflicts identified and resolved:**

1. `teams.owner_user_id` vs an OWNER membership row duplicates ownership in two
   places. Resolved: both stored, written in one transaction, with a partial
   unique index making a second OWNER impossible (ADR-13 §2.2).
2. `UserBlock` could cascade into team rows. Resolved: explicitly non-cascading
   (§6 below), with the reasoning that distinguishes it from 8.1's friendship
   semantics.

**Proposed and delivered:** 4 tables, 17+ endpoints, 9 Flutter screens, 115
localized keys × 3 locales.

## 2. Backend

| File | Contents |
|---|---|
| `app/models/team.py` | `Team`, `TeamMembership`, `TeamJoinRequest`, `TeamInvitation` + 4 enums |
| `app/schemas/team.py` | create/update inputs, `TeamOut` / `PublicTeamOut`, member/request/invitation shapes, 4 page envelopes |
| `app/services/team_service.py` | 1077 lines: CRUD, discovery, membership, requests, invitations, roles, authorization helpers |
| `app/api/v1/teams.py` | 21 routes registered in `app/api/v1/__init__.py` |
| `alembic/versions/0008_teams.py` | additive on `0007_social`, reversible |

Authorization is resolved from the viewer's membership row on every call. No
function accepts a role, `actor_id`, or `is_admin` from a caller.

## 3. Database

Four tables, no existing table altered. Two load-bearing invariants are enforced
by **partial unique indexes**, because no CHECK constraint can express them:

- `uq_team_memberships_single_owner` on `team_id WHERE role='owner'` — exactly
  one owner per team.
- `uq_team_invitations_pending_pair` on `(team_id, invited_user_id) WHERE
  status='pending'` — at most one pending offer per pair, while a team may still
  re-invite someone who previously declined.

Plus `member_count` denormalized on `teams` and maintained inside the membership
transaction under an advisory lock.

## 4. API

21 routes under `/api/v1/teams`. Two deliberate deviations from the brief's
enumeration, both documented:

- **`DELETE /teams/{id}` archives (soft), matching bikes.** Team history matters
  more than a ride's.
- **Owner transfer is not implemented**, per instruction. The partial unique
  index makes a second owner impossible, so the role endpoint rejects `owner`
  with `TEAM_OWNER_IMMUTABLE` rather than improvising a transfer.

## 5. Security

- **Non-member → 404, admin → 403.** A non-member gets the same answer for a
  private team, a public team they cannot touch, and a nonexistent id.
- Member lists read `social_profiles`, never `users`, so a roster structurally
  cannot leak an email address.
- Search matches name/handle only, so it cannot enumerate a roster.
- No coordinates, GPS, or live location anywhere; asserted by test and by a
  standing in-app notice.
- Rate limits on every mutating route, keyed per user.

**One real defect found and fixed during this phase** — see §10.

## 6. Flutter

`mobile/lib/features/teams/`:

- **domain** `team.dart` (4 enums whose wire value *is* the domain value, 5
  models, 4 page types), `team_validators.dart`
- **data** `team_repository.dart` — all operations over the shared `ApiClient`
- **presentation** `team_providers.dart` (paged notifiers + `TeamActions`
  with the in-flight guard), `team_widgets.dart` (`friendlyTeamError`), and 10
  screens: my teams, search, profile, create/edit form, members, join requests,
  invitations inbox, invitations manager, settings, invite picker.

`/teams` replaced its placeholder; `/teams/:id*` routes added. **`/groups`
stays a placeholder** — group rides are explicitly out of scope.

Mutations mirror 8.1 exactly: a key already in flight short-circuits, each
button reads its busy flag from the same set, nothing is applied optimistically,
and the viewer's `state` (server-resolved) drives the action set rather than a
locally derived role.

## 7. Localization

**115 new keys × 3 locales**, parity verified by script: en/fr/ar all have
identical key sets, zero duplicates, no drift. Arabic renders RTL (asserted via
`Directionality.of`), and Arabic team prose contains no Latin words — only the
legitimate `https://` / `http(s)` in URL hints.

One defect fixed en route: five French values contained raw apostrophes
(`n'êtes`) that terminated the Dart string literal. Caught by
`flutter analyze`, escaped, and covered by the parity check.

## 8. Tests

| Suite | Result |
|---|---|
| Backend total | **466 passed** (380 baseline + 86 team) |
| `test_teams_api.py` | 60 — CRUD, handles, visibility, roles, IDOR matrix, join/invite lifecycle, blocked users, 4 concurrency races, pagination, rate limit, OpenAPI |
| `test_team_unit.py` | 27 — handle canonicalization, role ladder, state machine |
| Flutter total | **304 passed** (214 baseline + 90 team) |
| `teams_test.dart` | 33 — models, validators, repository paths, error mapping, l10n parity, routing contract |
| `teams_widgets_test.dart` | 57 — every screen's states, role-gated actions, duplicate-submit, EN/FR/AR, RTL, security |

No existing test was removed or weakened.

## 9. Migration verification

`0008_teams` is head. `test_migrations.py` was extended to assert the team
tables appear at head, that `0008 → 0007` removes exactly them **while leaving
the social tables intact**, that the full chain down to base and back up is
deterministic, and that the new partial indexes exist. Verified manually too:
`upgrade head` → `downgrade -1` → social tables intact → `upgrade head` →
team tables restored.

## 10. Defect found and fixed

**`PATCH /teams/{id}` returned 403 to a non-member — an existence oracle.**

A non-member attempting to rename a team received `TEAM_FORBIDDEN` (403) from
the admin-only field check, which confirms the team exists. That is exactly the
leak ADR-12 §4 and ADR-13 §7 forbid: it turns `PATCH` into a probe for which
team ids exist, including private teams.

Caught by the live smoke run, not by the test suite — the suite's IDOR test used
a team the stranger could legitimately see, so 404 was the expected answer and
the code path was never distinguishing.

Fixed: a non-member gets 404 before any field check; an admin still gets 403 for
an owner-only field (they already know the team exists, so nothing leaks).
Regression test `test_patch_on_a_private_team_is_404_not_403` asserts that a
private team and a public-but-not-yours team produce the *same* status.

## 11. Live smoke

`backend/live_smoke_teams.py` against real uvicorn + PostgreSQL + Redis, four
real accounts (A, B, C, D). **47/47 checks passed — twice**: once on a fresh
database and once against a reused database (68 existing tables, prior teams and
accounts present).

Covering: team creation and ownership, discovery, public self-service join,
duplicate-join refusal, role promotion, invitation lifecycle, admin vs owner
authority (6 distinct checks), private-team request flow, private-team
invisibility, the IDOR matrix, pagination, and the non-cascading block
invariants in both directions.

The reused-database run is deliberate. Phase 8.1 shipped a lazy-load 500 that a
first smoke run masked and only a second run against reused state exposed; the
same discipline is applied here, and it is what surfaced the 403 oracle in §10.

## 12. Documentation

- `docs/adr/ADR-13-teams-groups-membership.md` — new. Includes the
  non-cascading block invariant (§6) as its own section.
- `docs/03-database-model.md` — appended the four team tables with constraints,
  indexes, and the concurrency + block notes.
- `docs/04-api-map.md` — appended the 21 endpoints with conventions.
- `docs/phase-8.2-report.md` — this file.

## 13. Known limitations

- `member_count` is denormalized, therefore a consistency risk. Mitigated by the
  team advisory lock and a concurrency test, but it is state that could drift if
  a future code path ever adds a membership row outside the service.
- Join requests have no message history after resolution (deleted by design).
- Search results load the first page only; it does not append on scroll like the
  friend list does.
- No team avatar upload — `avatar_url` accepts an http(s) URL only.
- No offline behaviour; team screens show an error rather than cached content.
- No realtime updates; teams refresh on pull-to-refresh and on re-entry.
- `member_count` is only correct for teams created through the service.

## 14. Deferred (Phase 8.3+)

Team chat, direct messages, group rides, live/realtime location, push
notifications, social feed, leaderboards, and **owner transfer**. ADR-13 §10
constrains them: group rides attach to a team but team membership is never an
implicit location grant, team communication must respect both the block rule and
removal, and owner transfer needs a single atomic operation under the team lock.

---

## Gates

| Gate | Result |
|---|---|
| Backend `pytest` | **466 passed** |
| Backend `ruff` / `mypy` | clean |
| Migration `0008` + `0008 → 0007` downgrade | pass |
| `flutter test` | **304 passed** (214 baseline preserved) |
| `flutter analyze` | no issues |
| `dart format` | clean (107 files) |
| `flutter build web` | built |
| `flutter build apk --debug` | built |
| Live smoke — fresh DB | **47/47** |
| Live smoke — reused DB | **47/47** |

## Final status

**PHASE 8.2 STATUS: PASS**
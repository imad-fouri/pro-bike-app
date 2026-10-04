# Phase 8.1 — Social Identity & Relationships Foundation

Status: **PASS**. All gates green. No Phase 8.2 work started.

## 1. Objective

Finish the social foundation: public rider identity, mutual friendship with a
request lifecycle, blocks, and a privacy boundary — on the backend and in the
Flutter client, with no location exposure anywhere.

Scope was explicit: no teams, groups, chat, group rides, live location, realtime
location, or push notifications. All remain deferred.

## 2. Existing backend implementation

Phase 8.1's backend was already implemented and green before this continuation.
Verified, not assumed:

| File | State |
|---|---|
| `app/models/social.py` | `social_profiles`, `friend_relationships`, `user_blocks` |
| `alembic/versions/0007_social.py` | additive on `0006_training_foundation` |
| `app/schemas/social.py` | two profile shapes + paginated envelopes |
| `app/services/social_service.py` | profiles, requests, friendships, blocks |
| `app/api/v1/social.py` | 15 routes, registered in `app/api/v1/__init__.py` |
| `tests/test_social_api.py` / `test_social_unit.py` | 35 + 28 tests |

Baseline confirmed by running it: **379 backend tests passing**, `0007` upgrade
and `0007 → 0006` downgrade green.

## 3. Mobile implementation

New feature module `mobile/lib/features/social/`, following the existing
`data/ domain/ presentation/` pattern (bikes, routes, training):

**domain/**
- `social_profile.dart` — `RelationshipState`, `ProfileVisibility`,
  `FriendRequestsPolicy`, `SearchVisibility`, `SocialProfile`, `PublicProfile`,
  `FriendRequest`, `SocialFriend`, `BlockedUser`, `SocialPage<T>`. The wire
  value *is* the domain value: no renaming, mapping, or invention. An unknown
  server value degrades to `NONE` instead of crashing.
- `social_validators.dart` — client-side mirrors of the backend rules, returning
  l10n keys. UX only; the server stays authoritative.

**data/**
- `social_repository.dart` — all 14 backend operations over the shared
  `ApiClient`. Every call is `auth: true`; no raw HTTP anywhere in the UI layer.

**presentation/**
- `social_providers.dart` — repository provider, profile providers, four paged
  `AsyncNotifier`s sharing one paging base, and `SocialActions` for mutations.
- `social_widgets.dart` — avatar, handle, relationship badge, loading/empty/error
  surfaces, `friendlyError` (the single error→sentence mapper), and
  `SocialActionButton` (duplicate-submission guard wired to real state).
- Eight screens: my social profile, edit profile, privacy settings, user
  profile, user search, friends, friend requests, blocked users.

### Relationship state is consumed, never derived

The action set is a pure function of the server's `relationship` value
(`UserProfilePage.actionsFor`), so a stale screen cannot offer an action the
server would reject and a fresh one cannot hide an action it would accept.
`NONE → Add friend`, `OUTGOING_PENDING → Cancel`, `INCOMING_PENDING → requests
screen` (accept/reject need request ids, which only that screen carries),
`FRIENDS → Remove/Block`, `BLOCKED → Unblock`, `BLOCKED_BY_USER → no action`.

After every mutation the client awaits the response, then invalidates every
affected provider. Nothing is applied optimistically, so "Friends" cannot
survive a removal.

### Duplicate-submission protection

Two layers. `SocialActions` keeps an in-flight key set and short-circuits a
repeated call, and each button reads its `busy` flag from that same set — so
the disabled button and the guard cannot disagree. Keys are namespaced by
action and user id, so a block and a friend request on the same rider never
share a lock, and two different riders are not serialized against each other.
The backend's unique constraint is still the real arbiter.

## 4. Routes

`/friends` replaced its `PlaceholderPage`. Added:

| Route | Screen |
|---|---|
| `/profile/social` | my social profile |
| `/profile/social/edit` | edit identity |
| `/profile/social/privacy` | privacy settings |
| `/friends` | friends list |
| `/friends/search` | rider search (350 ms debounce) |
| `/friends/requests` | incoming / outgoing tabs |
| `/friends/blocked` | blocked users |
| `/users/:userId` | another rider's profile |

No pre-existing route was removed or renamed. `/profile` gained a row linking to
`/profile/social`.

## 5. API integration

`Repository → Provider/Notifier → UI`, no page constructs a request. The
repository issues exactly the endpoints in `docs/04-api-map.md`; the search
query is URL-encoded, so a handle containing a space or `&` cannot split the
query string. Search sends only `q`/`page`/`page_size` — no email, no id, no
location parameter exists to send.

## 6. Localization

104 new keys in English, French, and Arabic, all in the existing
`AppLocalizations` map. Verified by script: key parity across all three locales,
no duplicates. Arabic renders RTL through the framework; a test asserts the
layout direction and that Arabic social prose contains no Latin words (country
codes and `https://` in hints are intentional). Arabic cycling terminology
follows the app ("رحلة" for a ride, not "قيادة").

## 7. Privacy

- No location, GPS, live position, or route position in any social model, page,
  or response. Two tests enforce this structurally: one rejects banned key
  fragments in every wire payload, one asserts no social screen renders a
  location icon or distance.
- The city field is labelled "your city, not your location", and both the
  profile and privacy screens state that friendship does not share location.
- `limited: true` is respected: withheld sections render a locked notice, never
  an empty section that implies the rider wrote no bio.

## 8. Security

- Every social request carries the bearer token; the viewer is never client
  supplied. Asserted by test over all request paths.
- Errors map to localized sentences; raw codes and status lines never reach the
  user. The four not-found codes collapse to one message so the client cannot
  become the existence oracle the API deliberately avoids.
- No social screen renders an email address (regex assertion over rendered text).
- Search is debounced to respect the 60/min server limit.
- Relationship state is server-authoritative; the client never constructs it.

## 9. Tests

**Backend: 380 passing** (379 baseline + 1 regression test added below).
`ruff` clean, `mypy` clean.

**Flutter: 214 passing** — 117 baseline, all still green, plus 97 new:
- `test/social_test.dart` (50) — model parsing, enum wire values, validators,
  repository path coverage, paging, all eight relationship transitions, provider
  invalidation, duplicate-submit guards, error mapping, localization parity.
- `test/social_widgets_test.dart` (47) — per-state action sets, confirmation
  dialogs, loading/empty/error/retry, debounce, Arabic RTL, privacy controls,
  token and email assertions.

Two behaviours were pinned by tests that failed first:
- Riverpod 3 retries failing providers with a backoff. In a widget test that
  leaves the screen on "Loading" and an error assertion can never observe the
  failure, so the harness disables retry (`retry: (_, _) => null`).
- The mutation gate is a `Completer`, not a sleep, so the in-flight UI is
  observed deterministically rather than raced.

## 10. Migration verification

`0007_social` is head; `command.upgrade head` → `downgrade -1` → `upgrade head`
is asserted by `tests/test_migrations.py` (social tables present, then removed,
then deterministically re-applied). No migration was created or altered in this
phase.

## 11. Live smoke

`backend/live_smoke_social.py` against a real uvicorn process, real PostgreSQL,
and real Redis — nothing in `app/` stubbed. Three real accounts (A, B, C).

**41/41 checks passed**, covering all 20 acceptance steps: profile load, search
(found, and *not* matchable by email), profile view, request, incoming request,
accept, friends state and list, remove, re-request, reject, re-request, block
(including annihilation of the pending request), request refused while blocked,
`BLOCKED_BY_USER`, unblock, re-request, **friendship not silently restored**,
third-party isolation, privacy (private/hidden/friends-only/nobody), blocked
list, pagination, and 401 on all four unauthenticated reads.

## 12. Defect found and fixed

**A real backend bug, surfaced by the smoke run.**

`_active_user()` resolved a `User` without eagerly loading `User.profile`, but
`ensure_profile()` reads `user.profile.display_name` to seed the public
projection. That read is sync IO on an async session, so it raised
`MissingGreenlet` → HTTP 500 the first time a rider who had **never edited their
own profile** was resolved as somebody else's target.

Why the 63 existing social tests missed it: their helper always called
`GET /social/profile/me` first, which creates the row, so the vulnerable branch
was never taken. It took a second smoke run against a reused database — where
the projection genuinely did not exist — to hit it.

Fix: `selectinload(User.profile)` in `_active_user()`, with a comment stating it
is a correctness requirement, not an optimisation. Regression test added:
`test_viewing_a_rider_who_never_edited_their_profile` registers a second rider
**without** touching their social profile, then views them and sends them a
request.

## 13. Documentation

- `docs/adr/ADR-12-social-relationships-privacy.md` — new. Mutual model,
  canonical pair, block semantics, privacy filter, why live location is
  excluded, authorization principles, concurrency, future integration.
  (The code has cited "ADR-12" since it was written; this file now exists.)
- `docs/03-database-model.md` — appended a Phase 8.1 section: the three tables
  with columns, constraints, indexes, and the no-coordinates rule.
- `docs/04-api-map.md` — appended the 15 social endpoints, conventions, error
  codes, rate limits.
- `docs/phase-8-report.md` — this file.

## 14. Known limitations

- `cycling_category` is a free-text label server-side. The client translates the
  known vocabulary and falls back to the server's own string otherwise; a full
  category enum is deferred.
- The friends and requests lists are paginated and load more on scroll, but the
  search results page does not yet append pages — it loads the first page only.
- Friend requests have no pagination cursor beyond `page`/`page_size`, and no
  "load more" affordance inside a tab.
- No avatar upload: `avatar_url` accepts an http(s) URL only.
- No offline social behaviour. Everything requires connectivity, which is
  acceptable for a network-dependent feature but means the social screens show
  an error rather than cached content when offline.
- The client has no separate socket/notifier for social updates; the screens
  refresh on pull-to-refresh and on screen re-entry. Fine at this scale.

## 15. Deferred (Phase 8.2+)

Teams and team membership, groups, chat and messaging, group rides, live
location and realtime location, push notifications, challenges. All of these are
constrained by ADR-12 §7 — notably, a team block must not destroy a personal
friendship, and location must arrive as an explicit revocable grant rather than
a profile field.

---

## Gates

| Gate | Result |
|---|---|
| Backend `pytest` | 380 passed |
| Backend `ruff` / `mypy` | clean |
| Migration `0007` + downgrade | pass |
| `flutter test` | 214 passed (117 baseline preserved) |
| `flutter analyze` | no issues |
| `dart format` | clean |
| `flutter build web` | built |
| `flutter build apk --debug` | built |
| Live smoke | 41/41 |

## Final status

**PHASE 8.1 STATUS: PASS**
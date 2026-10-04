# Phase 8.3 — Team Chat & Direct Messages

Status: **PASS**. All gates green. Phase 8.4 not started.

## 1. Audit

Before writing code I inspected the backend (`models`, `services`, `api/v1`,
`core/errors`, `core/rate_limit`, migrations, tests), the Flutter feature
architecture, and the l10n/routing/test conventions.

**Reusable, unmodified:** the `{error:{code,message,details}}` envelope; the
`(code, message, status)` service-error shape; `get_current_user`; the
`items/total/page/page_size` envelope for list endpoints; `rate_limit.allow`; the
advisory-lock pattern from 8.1/8.2; the `_uuid`/`_values`/`__table_args__` ORM
conventions; the `create_type=False` migration style; and the whole
`features/social` + `features/teams` Flutter shape (repository → Riverpod →
page, `friendlyError`, `SocialErrorView`, `SocialActionButton`,
`SocialAvatar`, `TeamAvatar`).

**Two schema conflicts identified and resolved before any code:**

1. A `conversation_members` roster row survives a rider being removed from a
   team — it has to, or their past messages lose their author. That makes the
   roster useless as an authorization source. Resolved: team authorization reads
   the **live `team_memberships` row** inside the transaction; the roster is a
   snapshot only (ADR-14 §2.3).
2. A block between two riders could plausibly be read as "remove them from any
   shared space". Resolved: **blocks bound direct messages only.** A block is a
   personal-interaction boundary, and severing a team channel would broadcast the
   block to every other member (ADR-14 §2.1).

**Proposed and delivered:** 3 tables, 9 endpoints, 3 Flutter screens, 30
localized keys × 3 locales.

## 2. Backend

| File | Contents |
|---|---|
| `app/models/chat.py` | `Conversation`, `ConversationMember`, `Message` + 2 enums |
| `app/schemas/chat.py` | send/edit inputs, `ConversationOut`/`MessageOut`, cursor + offset page envelopes |
| `app/services/chat_service.py` | 943 lines: discovery, authorization, ordering, idempotency, read state, edit/delete |
| `app/api/v1/chat.py` | 9 routes registered in `app/api/v1/__init__.py` |
| `alembic/versions/0009_chat.py` | additive on `0008_teams`, reversible |

No function accepts a role, an actor id, or an authorization flag from a caller.
Every decision is re-derived from the JWT user on every call.

## 3. Database

Three tables, **no existing table altered** — 8.3 cannot regress 8.1 or 8.2.

Three load-bearing invariants enforced by storage rather than by application code:

- `uq_conversations_team_channel` on `team_id WHERE kind='team'` — **exactly one
  channel per team**.
- `UNIQUE(conversation_id, sender_user_id, client_message_id)` — a retry is the
  same message.
- `UNIQUE(conversation_id, seq)` — no two messages share a position.

Plus `messages.sender_user_id` and `conversation_members.user_id` as
**`ON DELETE RESTRICT`**: history is never cascade-deleted and stays
attributable to a tombstoned author.

## 4. API

9 routes under `/api/v1/chat`. Two deliberate deviations from the project
standard, both recorded in the API map:

- **Message history uses a cursor envelope**, not `items/total/page/page_size`.
  Offset pagination over an append-only table is unstable while new messages land
  at the head; a `COUNT(*)` over full history on every page is an avoidable cost.
  The inbox stays offset-paged — a conversation list is bounded and does not shift
  under the rider.
- **A send requires a client-supplied `client_message_id`.** 201 on a new write,
  200 with `duplicate: true` on a replay. Only a caller-held id can make a retry
  after a dropped response checkable.

## 5. Privacy invariants

| Rule | Enforcement |
|---|---|
| Unauthorized is 404, byte-identical to "does not exist" | service `_require_participant`; a blocked rider and a non-existent one return the same body |
| Team authorization is re-derived live | `team_memberships` read inside the transaction, never the roster |
| Re-checked under the lock | archive status and standing re-read inside the conversation lock |
| A block closes DMs in both directions | `_blocked_either_way` on create and send |
| A block does **not** touch a team channel | no code path consults a block for a team conversation |
| A DM needs no friendship | `open_direct` has no friendship precondition |
| Nothing is hard-deleted | soft delete only; `RESTRICT` on the author FK |
| Public identity only | sender read from `social_profiles`, never `users` |
| No location, media, or ride type | `message_type` is `text` or `system` — nothing else exists to render |

403 is reserved for cases where the caller already demonstrably has access
(posting to a team they are a member of whose archived state they can see).

## 6. Flutter

| File | Contents |
|---|---|
| `lib/features/chat/domain/chat.dart` | `Conversation`, `ChatMessage`, `MessagePage`, `SendResult`, `ConversationPage` |
| `lib/features/chat/domain/chat_validators.dart` | body validation, page sizes, `newClientMessageId()` |
| `lib/features/chat/data/chat_repository.dart` | real backend calls via `ApiClient`, no mocks |
| `lib/features/chat/data/chat_transport.dart` | `ChatTransport` interface + `PollingChatTransport` |
| `lib/features/chat/presentation/chat_providers.dart` | inbox provider, `ConversationNotifier` thread, `ChatActions` |
| `lib/features/chat/presentation/chat_inbox_page.dart` | `/chat` |
| `lib/features/chat/presentation/conversation_page.dart` | `/chat/:id` — history, composer, edit/delete |
| `lib/features/chat/presentation/chat_widgets.dart` | `MessageBubble`, `ConversationTile`, `friendlyChatError` |

Entry points added to existing surfaces: a **"Team channel"** tile on every team
profile the viewer is a member of, and a **"Message"** button on every rider
profile except the blocked pair and oneself.

Four client-side decisions that are each a bug the obvious implementation has:

- **One `client_message_id` per send attempt**, reused across retries. Generating
  one per call would defeat the mechanism the field exists for.
- **The composer clears on success, not on tap.** An optimistic clear followed by
  a failed send loses the rider's text.
- **History merges by message id and sorts by `seq`.** A poll and a
  pull-to-refresh can return the same message; a naive append renders it twice.
- **`can_edit` is used exactly as reported.** The client never re-derives the
  15-minute window from a device clock.

## 7. Bugs found and fixed during implementation

1. **`mark_read` raised `MissingGreenlet` on the no-op path.** The original
   version called `db.rollback()` when the incoming mark was stale, then read
   `member.last_read_seq` — which expires every ORM object on rollback, so the
   attribute access needed IO outside greenlet context and the request 500'd.
   This fired on every "mark read something already read", which is the common
   case when two tabs are open. Fixed by rewriting the update as a single
   `GREATEST` in SQL (which is also the correct way to make monotonicity
   independent of lock scope) and re-reading the value with an explicit query.
   Regression test: `test_the_read_mark_never_moves_backwards`.

2. **`_find_direct` produced invalid SQL.** The query joined
   `conversation_members` twice without an alias, and PostgreSQL rejected it:
   *"table name conversation_members specified more than more than once"*. Every
   DM creation 500'd. Fixed with `aliased(ConversationMember)`. The scan it
   replaced also loaded every direct conversation in the platform and compared
   member sets in Python; the join is an index lookup.
   Regression test: `test_direct_conversation_is_find_or_create`.

3. **A whitespace-only message body produced a 500.** `"   "` passed Pydantic's
   `min_length=1`, was stripped to `""` by the service, and then violated
   `ck_messages_body_nonempty` — surfacing as a constraint violation rather than
   a validation error. Fixed with a `field_validator` that rejects a
   body-that-trims-to-nothing at the schema boundary, so the rider gets a 422.
   Regression test: `test_blank_and_oversized_bodies_are_rejected`.

4. **`DISTINCT ON` emitted a deprecation warning** under the installed SQLAlchemy.
   Replaced with a grouped `MAX(seq)` subquery joined back to `messages`, which
   is the same query plan and is not deprecated.

Two more were caught before they shipped, by writing the assertion first:
`last_read_seq` being able to run past the newest message, and the read mark
moving backwards.

## 8. Tests

**Backend: 505 passed** (466 at the Phase 8.2 baseline, +39 chat).
`ruff check`, `ruff format`, and `mypy` all clean.

`tests/test_chat_api.py` — 38 tests against real PostgreSQL with real concurrent
connections. Grouped by the rule each defends: one-channel-per-team,
live team authorization, the 404-not-403 matrix, cursor pagination, idempotent
retry, concurrent `seq` allocation, the monotonic read mark, the edit window,
soft delete, and the block/DM policy.

`tests/test_migrations.py` extended: the `0009` tables, their columns, and the
six named constraints that make the invariants above impossible to lose in a
later edit.

**Flutter: 368 passed** (304 at the Phase 8.2 baseline, +64 chat).
`flutter analyze` and `dart format --set-exit-if-changed` clean. Web release
build and debug APK both build.

`test/chat_test.dart` covers the domain models, validators (including 500
generated client ids for uniqueness), the repository (idempotent retry stores
one message), the polling transport (a failed poll never reaches the stream;
`dispose` stops the timer), the thread notifier (merge-by-id, out-of-order
ordering, retry does not duplicate a bubble), the inbox and conversation screens
in EN/FR/AR including RTL, the error map, the l10n key set, and routing.

## 9. Live smoke

`backend/live_smoke_chat.py` against real uvicorn + PostgreSQL + Redis, four
real accounts (A, B, C, D). **57/57 checks passed — three times**: twice against a
reused database and once against a freshly created one.

Covering: one channel per team, the non-member 404 matrix, byte-identical
forbidden/missing responses, dense `seq` allocation across senders, idempotent
retry (201 then 200 `duplicate`), cursor paging walked to exhaustion, the
capped page size, the monotonic and clamped read mark, author-only edit and
delete, soft delete preserving order, DM find-or-create, friendship not required,
a block closing DMs in both directions without an oracle, a block **not**
severing a team channel, unblocking restoring the exact history, live team
removal cutting off access and sending while retaining history, an archived team
refusing new messages while staying readable, input validation, the privacy
scan, rate limiting, and unauthenticated access.

The reused-database runs are deliberate. Phase 8.1 shipped a lazy-load 500 that
a first smoke run masked and only a second run against reused state exposed.

Two smoke bugs were found and fixed by the smoke itself — both were bugs in the
*script*, not the server, and both were the kind that would have silently passed:
the cursor walk was not seeding `seen` with the first page it had already
fetched, and the unread assertion ran after a step that deliberately marked
everything read.

## 10. Documentation

- `docs/adr/ADR-14-chat-messaging.md` — new. 15 sections, including the block
  scope (§2.1), the no-friendship DM rule (§2.2), live team authorization
  (§2.3), never-hard-delete (§2.4), cursor pagination (§6), and the accepted
  costs (§15).
- `docs/03-database-model.md` — appended the three chat tables with constraints,
  indexes, the concurrency locks, the live-authorization note, and block
  semantics.
- `docs/04-api-map.md` — appended the 9 endpoints, the cursor-envelope
  deviation, and every privacy convention.
- `docs/06-realtime-social.md` — updated: the chat section now describes what
  shipped and what is deferred, and records that realtime is polling behind an
  interface rather than WebSockets.

## 11. Verification summary

| Gate | Result |
|---|---|
| Backend tests | 505 passed |
| `ruff check` / `ruff format` | clean |
| `mypy` | clean, 77 files |
| Migration head | `0009_chat`, reversible, upgrade→downgrade→base→upgrade verified |
| Flutter tests | 368 passed |
| `flutter analyze` | clean |
| `dart format --check` | clean |
| Web release build | passes |
| Debug APK build | passes |
| Live smoke (fresh DB) | 57/57 |
| Live smoke (reused DB, twice) | 57/57 each |

## 12. Deliberately not built

Admin moderation and reporting; hard deletion; media and attachments; push
notifications; WebSockets/SSE; Redis pub/sub; group rides and ride-scoped
channels; live location; synchronized rides; a channel taxonomy; distributed
rate limiting. Each needs a policy decision rather than more code, and each is
recorded with its reason in ADR-14 §15.
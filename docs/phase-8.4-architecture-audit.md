# Phase 8.4 — Architecture Audit: Push Notifications Foundation

Status: **BLOCKED — DESIGN ISSUE REQUIRES DECISION**

Audit date: 2026-10-03
Scope: audit only. No production code, migration, test, dependency, or
configuration was modified.

---

## 1. Executive summary

CycleCoach is a modular monolith: one FastAPI backend, one PostgreSQL system of
record, one Flutter client. Phases 8.1–8.3 built a social graph, an *n*-ary team
layer, and chat. Each shipped as additive migrations with reversible downgrades.

**What exists and is reusable.** The backend already contains every primitive a
notification foundation needs, and — importantly — already contains the
*architectural precedent* for provider neutrality. `app/ai/provider.py` defines a
`Protocol`-based provider seam with a status→(exception, retryable) mapper, a
factory that refuses to return an unconfigured provider, an explicit
`set_provider()` test override, and `app/ai/fake.py` as a test double. Phase 8.4
should mirror this exactly rather than invent a new shape.

Also present and reusable without modification: the JWT `get_current_user`
dependency, the `{error:{code,message,details}}` envelope, the
`(code, message, status)` service-error shape, the `redact()` logging helper
(which **already blanks any key named `token`**), the advisory-lock pattern, the
`create_type=False` + `.create(checkfirst=True)` migration convention, the
`items/total/page/page_size` list envelope, `rate_limit.allow`, and the Flutter
`features/<name>/{domain,data,presentation}` shape with `ApiClient`, Riverpod
providers, hand-written EN/FR/AR localization, and a `ChatTransport` abstraction
that is already snapshot-based and push-agnostic.

**What is missing.** All of it. There is no notification or push subsystem
anywhere in the repository — no FCM, no APNs, no device tokens, no push
providers, no notification service, no notification tables, no Android
notification permission, no iOS push entitlement, no notification channels, no
local-notification package, no deep-link intent filter, and no app-lifecycle
observer. This is a genuinely greenfield feature inside a mature codebase.

**Architectural readiness.** The *shape* is ready. The *worker* is not: the only
worker artifact is a 12-line stub whose `redis_settings` is `None`. More
importantly, three decisions cannot be made from the codebase alone, and each
materially changes the data model or the security posture:

1. **FCM/APNs credential ownership** — whether the project adopts FCM as the
   primary transport (which drags in a `google-services.json`, the Google Services
   Gradle plugin, and a FlutterFire dependency) or designs provider-neutral
   abstractions with a fake-only implementation in Phase 8.4.
2. **Token storage** — push tokens are bearer credentials for a third-party
   service. Hash-then-store (the existing `refresh_hash` precedent) makes
   delivery impossible; store-plaintext raises a different concern.
3. **Account deletion and notification retention** — there is **no
   account-deletion endpoint or service anywhere in the repository**. `users.deleted_at`
   exists as a column and is read by auth, but nothing ever writes it.

**Major blockers.** The three open decisions above, plus one latent defect found
during the audit (below) that must be corrected rather than inherited.

**Major risks.**

- A notification is a **cross-boundary disclosure**. Phase 8.3 spent its entire
  design budget making sure a blocked DM is indistinguishable from a
  non-existent one. A push saying "A sent you a message" re-opens exactly that
  oracle through a channel the API never sees. This is the single largest risk in
  the phase.
- The router is **rebuilt on every auth-state change** and `initialLocation` is
  hard-coded to `/splash`, so a notification tap that cold-starts the app loses
  its destination. Deep links cannot be made reliable without changing routing
  behavior.
- `AppLocalizations.get()` performs a **non-null assertion on lookup**, so a
  missing key is a runtime crash, not a fallback. Notification text has ~7 new
  keys × 3 locales, and a single omission in one locale crashes that locale.

---

## 2. Current infrastructure audit

Every path and symbol below was read during this audit. Nothing is inferred.

| Concern | Path | Symbol | Behavior | Reuse |
|---|---|---|---|---|
| Auth dependency | `backend/app/api/deps.py` | `get_current_user` | Bearer → `decode_access_token` → `select(User)` with `selectinload(User.profile)`; 401 if missing/`deleted_at`; **403** if `status != ACTIVE` | Reuse as-is for every notif route |
| IDOR guard | `backend/app/api/deps.py` | `get_user_by_id_strict` | 403 unless `user_id == requester.id` | Precedent for device ownership |
| Token crypto | `backend/app/core/security.py` | `hash_token`, `new_refresh_token`, `tokens_equal` | Refresh tokens are **opaque, sha256-hashed, never stored plaintext**; `hmac.compare_digest` for equality | **Strong precedent for push-token storage** |
| Access token claims | `backend/app/core/security.py` | `create_access_token` | `sub`, `jti`, `type`, `iat`, `exp` only — **no session or device binding** | Blocks device-scoped logout; see §24 |
| DB session | `backend/app/db/session.py` | `get_db` | AsyncSession per request | Reuse as-is |
| Errors | `backend/app/core/errors.py` | `error_body`, `register_error_handlers` | `{error:{code,message,details}}`; 422 `VALIDATION_ERROR`; 500 never leaks a traceback | Reuse for all notif errors |
| Service errors | `app/services/*.py` | `ChatError`, `SocialError`, `TeamError` | `(code, message, status)` → router maps to `HTTPException(detail={...})` | Copy the pattern |
| Logging | `backend/app/core/logging.py` | `redact`, `setup_logging` | Blanks keys named `password, token, secret, location, latitude, longitude, api_key, authorization, message, prompt, …` | **`token` is already redacted** — a push token is safe by default |
| Rate limiting | `backend/app/core/rate_limit.py` | `allow(key, limit, window_s)` | In-process sliding window over a module-level dict | Reuse; limitation in §17 |
| Redis | `backend/app/redis/client.py` | `get_redis`, `check_redis` | Lazy `aioredis.from_url`, `decode_responses=True`; **health check only, no caching logic** | Reuse the client for ARQ |
| Workers | `backend/app/workers/settings.py` | `ping`, `WorkerSettings` | **Stub.** `functions=[ping]`, `redis_settings = None` | **Insufficient** — see §15 |
| WebSockets | `backend/app/websocket/manager.py` | `ConnectionManager`, `manager` | Stub, docstring says "auth + channels arrive in Phase 11/12" | Do not touch (non-goal) |
| Migrations | `backend/alembic/versions/0001…0009` | — | `create_type=False` + explicit `.create(checkfirst=True)` on every enum; `_uuid`/`_values` ORM helpers | Copy `0009_chat` exactly |
| Migration tests | `backend/tests/test_migrations.py` | `test_migration_upgrade_downgrade_upgrade` | upgrade head → step down through every revision → `base` → upgrade head | Extend with `PHASE84` |
| Config | `backend/app/core/config.py` | `Settings` | pydantic-settings, `env_file=".env"`, `extra="ignore"`; AI has 12 dedicated fields | Add notif fields the same way |
| AI provider seam | `backend/app/ai/provider.py` | `AIProvider`, `get_provider`, `set_provider`, `_status_error` | `Protocol` + factory + status→(exc, retryable) + retry loop | **The pattern to copy for push** |
| AI fake | `backend/app/ai/fake.py` | `FakeAIProvider`, `RecordingProvider` | Records calls, injectable draft/error | **Copy for `FakePushProvider`** |
| AI error types | `backend/app/ai/types.py` | `AIError`, `ProviderUnavailable`, `ProviderTimeout`, `ProviderInvalidResponse` | Frozen dataclasses cross the seam | Copy shape |
| Email outbox | `backend/app/services/email.py` | `email_service.outbox` | Test-only outbox double exposed as a `conftest` fixture | Precedent for a push test double |
| CI | `.github/workflows/ci.yml` | — | Backend: ruff, format, mypy, `alembic upgrade head`, pytest, `downgrade -1 && upgrade head`. Mobile: format, analyze, test | Gates in §38 |
| Flutter HTTP | `mobile/lib/core/network/api_client.dart` | `ApiClient` | One client, `auth: true` attaches the bearer; `ApiException(status, code, message)` | Reuse; no new client |
| Flutter storage | `mobile/lib/core/storage/token_storage.dart` | `TokenStorage`, `SecureTokenStorage`, `MemoryTokenStorage` | OS-backed secure storage; **never `shared_preferences`** | Good home for a device id |
| Flutter auth state | `mobile/lib/features/auth/presentation/auth_state.dart` | `AuthStatus`, `AuthNotifier` | `unknown → loading → authenticated \| unauthenticated \| error`; `restore()` in a microtask | Gate registration on `authenticated` |
| Flutter routing | `mobile/lib/core/routing/app_router.dart` | `routerProvider` | **`GoRouter` constructed inside the provider**, watching `authProvider`; `initialLocation: '/splash'`; `redirect` gates on auth | **Needs care — see §20** |
| Flutter localization | `mobile/lib/core/l10n/app_localizations.dart` | `AppLocalizations.get(key)`, `L10nX` | Single `Map<locale, Map<key, String>>`; `_values[lang][key] ?? _values['en'][key]!` | Reuse; **no interpolation** — see §21 |
| Flutter lifecycle | — | — | **No `AppLifecycleState`, no `WidgetsBindingObserver` anywhere** | Must be created |
| Deep linking | — | — | **None.** Android manifest has only `MAIN`/`LAUNCHER`; no `app_links`; no iOS associated domains | Must be created |
| Android config | `mobile/android/app/src/main/AndroidManifest.xml` | — | Location + foreground-service permissions only; **no `POST_NOTIFICATIONS`** | Add |
| Android build | `mobile/android/app/build.gradle.kts` | — | Stock Flutter template; **no Google Services plugin**, no `google-services.json` | Required only if FCM is adopted |
| iOS config | `mobile/ios/Runner/Info.plist`, `AppDelegate.swift` | — | Location usage strings, `UIBackgroundModes=[location]`; **no `remote-notification`, no APNs entitlement file** | Add if APNs is adopted |
| Drift | `mobile/lib/features/ride/data/ride_tables.dart` | `LocalRides`, `LocalPoints`, `CachedRoutes` | Local-first **ride recording only** | Do not reuse as a notification store |
| Test harness | `mobile/test/chat_test.dart` | `FakeChat`, `ScriptedChatTransport`, `harness` | Stateful fake + scripted stream + l10n/RTL/Riverpod wrapper | Copy the pattern |

---

## 3. Existing notification infrastructure

**NOT PRESENT.**

Verified by exhaustive search across `backend/app/**`, `backend/alembic/**`,
`backend/tests/**`, `backend/pyproject.toml`, `docker-compose.yml`,
`mobile/lib/**`, `mobile/pubspec.yaml`, `mobile/android/**`,
`mobile/ios/**` for: `firebase`, `Firebase`, `fcm`, `FCM`, `apns`, `APNs`,
`push`, `notification`, `Notification`, `device_token`, `getToken`,
`onMessage`, `POST_NOTIFICATIONS`, `getInitialMessage`, `onTokenRefresh`.

The only matches were false positives: a regex in `app/ai/safety.py` matching the
word "push" in rider prose, `RefreshSession.device_label` (a display string, not
a device registry), and `training_calc.py` token caps.

Specifically confirmed absent:

| Item | State |
|---|---|
| FCM / Firebase SDK or Gradle plugin | **NOT PRESENT** |
| `google-services.json` | **NOT PRESENT** |
| APNs entitlement / `.entitlements` file | **NOT PRESENT** |
| `UIBackgroundModes: remote-notification` | **NOT PRESENT** (only `location`) |
| `POST_NOTIFICATIONS` Android permission | **NOT PRESENT** |
| Device-token table | **NOT PRESENT** |
| Notification table or preferences table | **NOT PRESENT** |
| Push provider abstraction | **NOT PRESENT** |
| `FakePushProvider` | **NOT PRESENT** |
| Notification-center UI | **NOT PRESENT** |
| Local notification package | **NOT PRESENT** (`flutter_local_notifications` absent) |
| Firebase messaging package | **NOT PRESENT** (`firebase_messaging` absent) |
| Android notification channels | **NOT PRESENT** |
| iOS notification categories | **NOT PRESENT** |
| Deep links / App Links / Universal Links | **NOT PRESENT** |
| App lifecycle observer | **NOT PRESENT** |

Two adjacent findings that are **not** notification infrastructure but do affect
this phase:

- `mobile/lib/features/home/home_page.dart:33-36` renders a
  `Icons.notifications_none_rounded` bell that navigates to `/friends`. A
  pre-existing placeholder that actively misleads. Phase 8.4 must either repoint
  it or remove it — leaving it means shipping a bell that opens the friend list.
- `AppLocalizations.get()` ends in `_values['en'][key]!`. A key missing from
  **every** locale is a hard crash, not a fallback. This matters more for
  notifications than for existing screens, because notification text is rendered
  from a server-supplied key (see §21).

---

## 4. Proposed domain model

Three tables are the right shape, but not three is the only defensible reading.

**Determination: all three are required, but `notification_preferences` should be
folded into `users`/a settings column rather than given its own table in
Phase 8.4.** Reasoning:

### `push_devices` — REQUIRED

There is nowhere else to put it. A user may have several devices; each has its
own token, platform, app version, and enabled flag. `refresh_sessions` is not a
substitute: it is revoked on logout, keyed to the auth family, and carries a
`device_label` display string rather than a delivery target.

| Column | Type | Notes |
|---|---|---|
| `id` | `UUID` PK | `_uuid()` default |
| `user_id` | `UUID` → `users.id` **ON DELETE CASCADE** | indexed |
| `platform` | enum `push_platform` (`android`, `ios`) | CHECK implicit |
| `provider` | enum `push_provider` (`fcm`, `apns`) | see §14 — provider-neutral seam |
| `device_id` | `String(128)` | **client-generated stable id**, not a vendor id |
| `token` | `Text` | see §32 for the plaintext-vs-hash decision |
| `app_version` | `String(32)` nullable | diagnostics only |
| `locale` | `String(8)` nullable | push payload localization |
| `enabled` | `Boolean` NOT NULL default true | soft disable, keeps history |
| `last_seen_at` | `timestamptz` nullable | staleness sweep |
| `created_at` / `updated_at` | `timestamptz` | |

Constraints:

- **`UNIQUE(user_id, provider, device_id)`** — one row per device per provider.
  A re-registration updates the token in place instead of accumulating dead rows.
  This is the single most important constraint: without it, every app launch
  creates a row.
- **`UNIQUE(provider, token)`** — a token belongs to exactly one account. This
  makes multi-account-on-one-device safe: logging in as B **transfers** the row
  rather than duplicating it, which prevents A receiving B's notifications.
- Index `ix_push_devices_user_id`.

Privacy: the token is a bearer credential for a third-party service. It must
never appear in a log, a response body, or an error message. `redact()` already
covers the log case because the column is named `token`.

### `notifications` — REQUIRED

| Column | Type | Notes |
|---|---|---|
| `id` | `UUID` PK | |
| `recipient_user_id` | `UUID` → `users.id` **ON DELETE CASCADE** | indexed; the owner |
| `actor_user_id` | `UUID` → `users.id` **ON DELETE RESTRICT** | nullable for system; **tombstone** |
| `type` | enum `notification_type` | see §12 |
| `entity_type` | `String(32)` nullable | `conversation`, `team`, `friend_request` |
| `entity_id` | `UUID` nullable | |
| `l10n_key` | `String(64)` | **localization key, not rendered text** |
| `params` | `JSONB` NOT NULL default `{}` | substitution args, e.g. `{"actor":"Imad"}` |
| `deep_link` | `String(256)` nullable | **validated against a route allowlist** |
| `dedupe_key` | `String(160)` nullable | **UNIQUE** idempotency |
| `created_at` | `timestamptz` | |
| `read_at` | `timestamptz` nullable | NULL = unread |

Indexes:

- `ix_notifications_recipient_created (recipient_user_id, created_at DESC)` —
  serves both the list and unread counts.
- `UNIQUE(dedupe_key)` **partial, `WHERE dedupe_key IS NOT NULL`** — the
  idempotency guarantee. A plain unique would collide on repeated NULLs in
  PostgreSQL before v15's `NULLS NOT DISTINCT`, so it must be partial.
- Index `ix_notifications_recipient_unread (recipient_user_id) WHERE read_at IS NULL`
  — a partial index making the unread count O(unread) instead of O(history).

Delete behavior: `recipient_user_id` CASCADE (a deleted rider has no inbox);
`actor_user_id` RESTRICT with tombstoning (a notification must not vanish because
its actor was deleted — see §23, **OPEN DECISION**).

**No `expiration`.** A notification center is a history, not a queue. Adding a TTL
now creates an unexplained-disappearing-message bug surface for no benefit.

### `notification_preferences` — DEFERRED, do not add a table

The brief lists it in scope; the codebase argues against it *in Phase 8.4*.

Preferences are per-user, singleton, and small. The project already stores exactly
this kind of thing on `user_profiles` (`profile_visibility`,
`activity_visibility`, `allow_friend_requests`) rather than in a side table.
Introducing a third storage location for one row per user is inconsistent with
the established convention, and a separate table implies a second migration and
a second code path for a value that is read once per notification.

Recommended for Phase 8.4: **all notifications on by default, with a single
`chat_notifications` boolean on `users`** — no. That alters `users`, and the
phase should stay additive. Instead: **defer preferences entirely; ship every
notification type enabled.** Add `notification_preferences` as a real table in a
later phase, once there is a product requirement for per-type granularity, and
model it as its own singleton table then.

This is **OPEN DECISION** — see §34. If the product owner requires per-type
opt-outs at launch, the table is required and the design above stands unchanged
apart from its addition.

---

## 5. Push device model

Multi-device, one row per (user, provider, device_id).

**Why a client-generated `device_id` and not the vendor token as identity.** FCM
and APNs tokens *rotate*. Using the token as the identity means every rotation
creates a new row and the old row lives forever, accumulating stale delivery
targets. A client-generated UUID (already possible — the `uuid` package is a
dependency, used by `ride_recorder.dart`) is stable across token rotation, so a
rotation becomes an `UPDATE`.

**Lifecycle decisions:**

| Event | Behavior |
|---|---|
| First registration | `INSERT` with a client `device_id` |
| Same device, new token (rotation) | `UPDATE` the token; bump `updated_at`/`last_seen_at`. `UNIQUE(user_id, provider, device_id)` makes this safe under concurrency |
| Same device, app reinstall | Reinstall usually yields a new FCM token but the app's stored `device_id` is gone → new `INSERT`. Old row is swept by `last_seen_at` |
| Same physical device, second account logs in | `UNIQUE(provider, token)` transfers the row to the new `user_id`. Prevents A receiving B's pushes |
| Logout | **Do NOT delete.** Tokens are revoked by the client on logout; a disabled row remains and is swept by staleness. See §24 — device-scoped revocation is blocked because the access token carries no session id |
| Disable (user action) | `enabled = false`. Row retained |
| Invalid token (provider says so) | `enabled = false` + `disabled_reason = 'invalid_token'` |
| Account deletion | **OPEN DECISION** — see §23 |

**Staleness sweep.** A periodic `last_seen_at < now() - 90d AND enabled` →
disable. Without it, `push_devices` grows monotonically with every reinstall a
rider ever does.

**Should raw tokens be stored?** See §32 and §34 — **OPEN DECISION**. The
precedent cuts both ways: `refresh_hash` is hashed because the server can verify
without the plaintext, whereas a push token must be *presented* to FCM/APNs, so it
must be recoverable. Hashing is therefore not available for the delivery path.
Recommendation: store plaintext in the `token` column, never log it (already
guaranteed by `redact()`), never return it in any API response, and rely on
PostgreSQL-at-rest encryption plus column-level access restriction for at-rest
protection.

---

## 6. Notification model and content strategy

**Determination: option C, hybrid — and the hybrid is asymmetric.**

- **Stored**: `type`, `entity_type`, `entity_id`, `l10n_key`, `params`, `deep_link`.
- **Never stored**: a rendered title or body.

The notification center renders from `l10n_key` + `params` against the client's
locale. This is required, not optional, because `UserProfile.preferred_language`
exists and the app supports EN/FR/AR: a server-rendered string can only ever be
correct in one language.

**The asymmetry — and it is the important part.** Push payloads are rendered by
**the operating system**, not by the app. FCM data messages can be rendered
client-side; FCM *notification* messages and APNs *alert* payloads are rendered by
the OS in a locale the **server** cannot observe. So:

- Push payload carries `l10n_key` + `params` in a **data message**, plus a
  generic, pre-translated-at-registration-locale fallback title. The
  device's `locale` column (§5) lets the server pick a fallback the OS can
  render without the app running.
- The in-app center renders fully client-side from `l10n_key`.

**A hard constraint the codebase imposes.** `AppLocalizations.get(key)` takes a
bare key with **no parameter interpolation**. So `params` cannot be interpolated
by the existing helper. Options:

1. Extend `get()` to accept optional named args. **Touches shared l10n** — every
   existing call site still compiles, so it is additive, but it modifies a file
   every feature depends on.
2. Pre-compose in the widget: `t.get(key).replace('{actor}', params['actor'])`.
   Ugly but zero blast radius.

**OPEN DECISION** — recommend (1), because hand-rolled `String.replace` in three
locales is exactly how RTL text gets corrupted, and because a missing
interpolation facility will be reinvented badly by every future feature.

Also note `get()`'s `_values['en'][key]!` non-null assertion: a notification
`l10n_key` the client does not know **crashes the client**. The client must
therefore treat an unknown `l10n_key` as "render the generic fallback", never
call `get()` with it blindly. This is a hard client-side rule, and it should be
enforced in a single `NotificationTitle`/`NotificationBody` widget rather than
scattered across the list.

---

## 7. Notification types

Phase 8.4 taxonomy. Only the first seven ship; the rest are placeholders for the
extensibility proof.

| Type | Trigger | Recipient | Category |
|---|---|---|---|
| `friend_request` | `social_service.send_request` | Target | 8.1 |
| `friend_request_accepted` | `social_service.accept_request` | Requester | 8.1 |
| `team_invitation` | `team_service.invite_user` | Invitee | 8.2 |
| `team_join_request` | `team_service.request_to_join` | Team managers | 8.2 |
| `team_member_removed` | `team_service.remove_member` | Removed rider | 8.2 |
| `team_archived` | `team_service.archive_team` | Team members | 8.2 |
| `chat_message` | `chat_service.send_message` | Conversation recipients except sender | 8.3 |
| `chat_message_team` | `chat_service.send_message` (team channel) | Live team members | 8.3 |
| `system` | Internal/ops | Explicit user | — |

`chat_message` and `chat_message_team` are separate types because their
authorization is different, and the difference is the whole privacy story (§13).

**FUTURE / OUT OF SCOPE** — enumerated in the enum's docstring, never created,
never emitted:

- `group_ride_invitation`, `group_ride_reminder`, `ride_starting` — require a
  group-ride session to authorize against, which is an explicit non-goal.
- `training_reminder` — requires scheduled-workout automation, out of scope.
- `ai_coach_event` — **representation only**. No emission path is written.
- `account_security` (password changed, new device login) — genuinely useful but
  outside the stated taxonomy; noted as a natural extension.

---

## 8. Privacy and authorization audit

**The governing principle: a notification is a disclosure that bypasses the API.**
Every rule in Phase 8.3 exists to make unauthorized reads return 404. A push
carries the *fact* of an event to a device, whether or not the recipient could
have read it. So notification authorization is **not** a reuse of
`_require_participant` — it must re-derive the same facts at creation time, and
the payload must then carry nothing that would distinguish the allowed case from
the refused one.

**Required verifications, each with its enforcement point:**

- **Notifications MUST NOT bypass chat authorization.** Enforced in the emission
  path in `chat_service.send_message`: the recipient set is computed from
  `_conversation_members` **and** re-checked through the same live-authorization
  logic `_require_participant` uses. For a team channel this means re-reading
  `team_memberships`, because a `conversation_members` row survives removal
  (`chat_service._require_participant:185-195`).
- **MUST NOT bypass team authorization.** Same live-membership re-read. A removed
  member gets no push, even though their roster row still exists.
- **MUST NOT reveal private conversation existence.** A refused recipient produces
  **no row at all** — not a suppressed push. A row that exists but is never pushed
  is still an existence leak through the notification center.
- **MUST NOT reveal blocked DM activity.** Suppress in **both** directions while
  the block stands. The sender must not learn they are blocked by a missing push.
- **MUST NOT expose GPS/location.** No notification type carries coordinates. The
  `message` key is already in `_SENSITIVE`, so `redact()` blanks it.
- **MUST NOT expose private user data.** Payload carries ids and a l10n key only.

**Detailed behavior:**

| Situation | Notification behavior |
|---|---|
| **Block before event** | Blocked DM send produces no notification for either party |
| **Block after event** | Existing notifications stay (they were valid when created); new ones suppressed. Suppressing retroactively would leak that a block changed |
| **Unblock** | No backfill. Notifications resume; the retained DM becomes reachable again |
| **Team channel while blocked** | **Delivery continues to both.** A block is a personal boundary, not team severance (ADR-14 §2.1). A push saying "Imad: hello" in a team channel is exactly the kind of broadcast the block policy forbids on the message path, so it must not be created on the push path either |
| **Team removal** | No further team notifications; a `team_member_removed` notification goes to the removed rider |
| **Team archive** | `team_archived` to members; no further chat notifications (the send path already 403s, so no message notification is created) |
| **Conversation archival** | No Phase 8.4 path archives conversations. `conversations.archived_at` exists but is unused |
| **Account deletion** | **OPEN DECISION** — see §23 |

**The oracle problem, stated precisely.** If A blocks B, and B stops receiving
"A sent you a message", B learns something. Three defenses, all required:

1. Suppress **both** directions, so the sender learns nothing either.
2. For **team** channels, keep delivering — a gap there would otherwise be a
   block-detection channel.
3. Never include the *reason* for non-delivery in any response body.

Point 2 is subtle and important: naive "block ⇒ silence" would make a team channel
a block oracle. The block policy deliberately keeps team channels open, and push
must match the message path exactly or it becomes the leak.

---

## 9. Push delivery architecture

Mirroring `app/ai/provider.py` exactly, because that file is the project's proven
answer to this problem.

```
Domain service (chat/team/social)      ← never imports a provider
        ↓  emits a NotificationEvent (id, type, recipient, l10n_key, params, deep_link)
NotificationService                    ← creates the notifications row, decides recipients
        ↓
PushDelivery (Protocol)                ← app/notifications/delivery.py
        ├── FakePushProvider           ← tests + local dev; the only one in 8.4 by default
        ├── FcmPushProvider            ← FUTURE / gated on §32 decision
        └── ApnsPushProvider           ← FUTURE / gated on §32 decision
```

**`PushDelivery` protocol:**

```python
class PushDelivery(Protocol):
    name: str
    async def send(self, msg: PushMessage, targets: list[PushDevice]) -> PushBatchResult: ...
```

`PushBatchResult` must distinguish, per device:

- `delivered` — accepted by the provider
- `invalid_token` — **permanent**; disable the device row
- `transient` — retryable
- `skipped` — disabled device

This mirrors `_status_error` in `app/ai/provider.py:37-43`, which already maps an
HTTP status to `(exception, retryable)`.

**Error taxonomy** (mirroring `app/ai/types.py`): `PushProviderUnavailable`,
`PushTimeout`, `PushInvalidResponse`, plus `PushInvalidToken` as a *result* rather
than an exception, since one invalid token in a 500-device batch must not fail the
batch.

**Provider selection:** a `get_delivery()` factory that raises `PushUnavailable`
when nothing is configured — the same shape as `get_provider()`, so the
notification service degrades to in-app-only rather than failing.

**Observability:** log event name, device **count**, outcome class, provider
name, latency. Never the token, never `params` (which contain actor display
names).

**Required dependencies:** none for the architecture. `httpx` is already present
and is sufficient for both FCM HTTP v1 and APNs HTTP/2. Adding a vendor SDK
would violate the neutrality the phase exists to establish.

---

## 10. Worker / queue architecture

**Audit result: the existing worker infrastructure is a stub and is insufficient
as-is.**

`app/workers/settings.py` is 12 lines: a `ping` function and
`redis_settings = None`. `arq>=0.26` is a declared dependency and Redis is a
running service, but **no job has ever been defined or run**.

The brief says "prefer reuse of existing worker infrastructure if it is actually
present and appropriate", and "if insufficient, mark the issue clearly instead of
inventing infrastructure."

**Judgment: this does NOT trigger stop-rule 7.** Completing the stub is not
"introducing a second queue system" — it is finishing the one the project already
declared. `arq` is already in `pyproject.toml`; wiring it introduces **zero new
dependencies** and **zero new infrastructure**. Refusing to do so would leave push
delivery synchronous inside a request, which is worse.

**Decision: C, hybrid.**

| Path | Delivery |
|---|---|
| `notifications` **row** creation | **Synchronous**, inside the originating transaction's commit boundary |
| **Push** delivery | **Enqueued to ARQ** after commit |

Rationale: the row is the durable record and must be transactionally consistent
with the business event. Push delivery is best-effort, slow, and retryable — it
must never hold a request open or roll back a chat send because FCM is slow.

**Enqueue point:** *after* `await db.commit()` in the originating service. Never
before — an enqueue that fires for a rolled-back transaction delivers a push
about something that did not happen. This matches the established pattern at
`social_service.py:437` (commit, then the caller proceeds).

**Job payload:** `{"notification_ids": [...]}`, not the rendered content. The
worker re-reads the rows, so a message edited or deleted between enqueue and
delivery sends what is true at delivery time.

**Retry:** ARQ's built-in exponential backoff, max ~3 attempts. Transient
provider errors only.

**Dead-letter:** a notification that exhausts retries stays **unread and
undelivered** in the center. There is no separate dead-letter table: the
notification row *is* the record, and the rider can still read it in-app. This
avoids a table whose only purpose is to duplicate the notification table.

**Idempotency at the worker:** re-reading rows by id means a duplicate job is
harmless. Delivery state is not persisted per-device in Phase 8.4 (see §31).

**No Redis Pub/Sub, no WebSockets.** Explicitly out of scope.

---

## 11. Idempotency

**Key: a deterministic business key, never a timestamp.**

The natural key is already in the system: `messages.client_message_id`, a
caller-supplied UUID whose uniqueness is enforced by
`UNIQUE(conversation_id, sender_user_id, client_message_id)`. For a chat
notification:

```
dedupe_key = "chat_message:" + str(message.id)
```

That is stable across a worker retry, an API retry, and a duplicate enqueue,
because it derives from the immutable row id rather than from time or from
anything the caller could vary.

For events without a natural id, derive from the business fact:
`"team_invitation:" + team_id + ":" + invitee_id`. Never `now()`, never a UUID
generated at emit time — those defeat the purpose.

**Storage guarantee:** partial `UNIQUE(dedupe_key) WHERE dedupe_key IS NOT NULL`.
An `INSERT` that violates it raises `IntegrityError`; the service catches it,
re-reads the existing row, and returns it — **exactly the pattern already used
for messages** (`chat_service.send_message`, the `IntegrityError` → re-read →
`duplicate=True` path) and for friend requests
(`social_service.send_request:425-436`).

**Multi-device is not an idempotency concern.** One notification, N device rows,
N sends. The dedupe key is per *notification*, not per device.

---

## 12. Rate limiting

Reusing `rate_limit.allow`, with limits chosen for this surface rather than copied
from chat.

| Operation | Limit | Why |
|---|---|---|
| Register a device | 10/hour/user | A device registers on login and on token rotation. Rare. A high limit would let a bug create unbounded rows |
| Refresh a device token | 60/hour/user | Token rotation can fire on reinstall and OS upgrade |
| List notifications | 60/min/user | Same class as other paged reads; generous because a screen may poll |
| Mark one read | 120/min/user | High — a "mark all as read" style sweep is many rapid calls |
| Mark all read | 10/min/user | Rare, bulk, and expensive (one UPDATE) |
| Notification creation | Enforced **per-recipient** at the service, not per-route | A team channel message fans out to N members; the route limit would wrongly punish the sender for a large team |
| Push fanout | Provider-side only | Server-side limiting of *outbound provider calls* would drop legitimate notifications; the batch size is the control |

**Known limitation, unchanged:** `rate_limit.allow` is a module-level dict
per process. With multiple uvicorn workers the effective limit is
`limit × workers`, and it resets on restart. This is the same limitation Phase
8.3 accepted. It matters more here because device registration is the one place
an attacker would want to amplify — mitigated by the `UNIQUE(user_id, provider,
device_id)` constraint, which caps the damage regardless of what the limiter
lets through.

---

## 13. Flutter architecture

```
mobile/lib/features/notifications/
├── domain/
│   ├── notification.dart          Notification, NotificationType, NotificationPage
│   └── notification_validators.dart  deep-link parsing + allowlist
├── data/
│   ├── notification_repository.dart  ApiClient calls
│   ├── push_device_repository.dart   register / refresh / disable
│   └── push_registration.dart        platform abstraction (no Firebase import)
└── presentation/
    ├── notification_providers.dart   list, unread count, read actions, registration
    ├── notification_center_page.dart /notifications
    ├── notification_settings_page.dart
    └── notification_widgets.dart     NotificationTile, unread badge, type icons
```

**Rules enforced by this layout:**

- **No Firebase import outside `data/push_registration.dart`.** Screens depend on
  a `PushRegistration` interface, so `FakePushRegistration` is substitutable in
  tests and the provider choice is one file.
- **`ChatTransport` is NOT modified.** It is snapshot-based and push-agnostic by
  design (`chat_transport.dart:16-17`: *"a delta protocol would need ordering and
  gap repair"*). Push signals *"something changed"*; it does not carry messages.
  The notification feature invalidates chat providers on receipt — it does not
  push payloads into the transport.
- **No notification logic in chat screens.** A chat screen may `invalidate` a
  notification provider; it must not import one to render a message.
- Deep links route through `go_router` only — no direct navigation from a
  notification widget.
- Reuse the Phase 8.3 atoms (`SocialErrorView`, `SocialActionButton`,
  `friendlyError` pattern) so the rider cannot tell phases apart.

**Unread badge:** the home bell (`home_page.dart:33-36`) becomes the real entry
point, or is removed. Leaving it wired to `/friends` while a notification center
exists is a defect.

---

## 14. App lifecycle

Currently **no lifecycle observer exists**. Phase 8.4 must add one. Note the
audit constraint: `main.dart` is minimal and `CycleCoachApp` is a
`ConsumerWidget`, so an observer must be a separate widget (e.g. a
`NotificationLifecycleObserver` inside `ProviderScope`) rather than inside
`CycleCoachApp`, to avoid rebuilding on every auth change.

| Event | Behavior |
|---|---|
| App open (cold) | `AuthNotifier.restore()` resolves **first**. Register the device only once `status == authenticated`. Queue any tap that arrived earlier (§15) |
| Foreground | Refresh unread count. **Do not** register again — the token is already stored |
| Background | Suspend polling. Rely on push. Do not hold timers |
| Terminated | Push wakes the app. Handle the tap from the launch payload |
| Tap | Parse `deep_link`, navigate **after** authorization is restored |
| Dismiss | Nothing server-side. The notification stays unread in the center |
| Logout | Unregister/disable the device **best-effort**; on failure mark locally so it is never re-sent. See §24 |
| Log back in | Re-register; the same `device_id` updates in place via the unique constraint |
| Token rotates | `FirebaseMessaging.onTokenRefresh` → `PATCH`/`POST` with the same `device_id` |
| Notification before auth restore | **Queue it.** A cold start from a push lands while `AuthStatus` is `unknown`/`loading`, and the router force-redirects to `/splash`. Queue and replay after restore |

---

## 15. Deep link architecture

**A deep link never grants authorization. It only navigates; the destination screen
re-authorizes exactly as it would without the link.**

This is not a stylistic rule — it is already how the app works.
`team_profile_page.dart` and `user_profile_page.dart` both fetch from the server
on open, so an unauthorized destination renders its own 404. Phase 8.4 must not
introduce a path that trusts the link.

| Case | Behavior |
|---|---|
| Validation | Allowlist: `/friends/requests`, `/teams/{id}`, `/chat/{conversationId}`, `/notifications`. **Reject anything else** and fall back to `/notifications` |
| Unauthenticated | Queue; after restore, if unauthenticated → `/onboarding` |
| Cold start | Queue until `authenticated`, then navigate |
| Background tap | Navigate immediately (auth is live) |
| Foreground tap | Navigate immediately |
| Stale/deleted entity | Destination screen's own 404/error view. **The notification center must not pre-check existence** — that would turn the notification list into an existence oracle |
| Blocked entity | Same — the chat screen already 404s a blocked DM |
| Missing entity | Same |

**Critical constraint: cold-start deep links do not work in the current
architecture.** `routerProvider` constructs a **new `GoRouter`** whenever
`authProvider` changes, with a hard-coded `initialLocation: '/splash'`
(`app_router.dart:52-55`). A push tap that cold-starts the app therefore has its
destination discarded when auth resolves and the router is rebuilt.

Two viable resolutions:

- **Option A (recommended):** hoist the `GoRouter` so it is created once, and
  make the redirect **replay** a pending deep link after auth resolves. Small,
  local change to `app_router.dart`.
- **Option B:** keep the rebuild and have the notification feature navigate
  post-restore rather than relying on `initialLocation`. Avoids touching the
  router but couples notifications to auth timing.

Option B is less invasive but leaves a latent bug for any future deep-link
consumer. **OPEN DECISION** — recommend Option A.

---

## 16. Localization

`AppLocalizations` is a hand-written `Map<locale, Map<key, String>>` with EN/FR/AR
and an `L10nX on BuildContext` extension. `get(key)` performs
`_values[lang][key] ?? _values['en'][key]!` — **a non-null assertion**.

**Strategy:**

- Keys are deterministic and stable: `notification.friend_request.title`,
  `notification.chat_message.body`. Notification type names never change once
  shipped, because a renamed key silently breaks every stored notification row.
- **No server-rendered prose.** `l10n_key` + `params` only (§6).
- The **push** fallback is localized server-side using the device's `locale`
  column, because the OS renders that text and the app is not running.
- EN/FR/AR parity asserted by a test that iterates every `notification.*` key
  across all three locales — the Phase 8.3 pattern.
- **Arabic must leak no Latin.** Existing tests already assert this with
  `RegExp(r'[A-Za-z]')`.
- **French apostrophes must be escaped** (`'`) or the string will not compile.
- **Notification type names must be stable** — this is the one place where a
  "nice" key rename would silently break historical rows.

**Interpolation:** see §6. `get()` has no parameter support; recommend adding it.

---

## 17. Security

| Concern | Position |
|---|---|
| Push token storage | Plaintext in a `token` column; never logged (`redact()` covers the key name), never returned by any endpoint. **OPEN DECISION** — see §32 |
| API authorization | Every route uses `get_current_user`. Device mutations are scoped `WHERE id = ? AND user_id = viewer.id` → 404 on mismatch, matching the Phase 8.3 no-oracle convention |
| Device ownership | A device id belonging to another rider returns **404**, not 403 |
| Revocation | `enabled = false`; a disabled device receives nothing. Per-device *token* revocation is a client concern (`deleteToken`) |
| Payload contents | `notification_id`, `type`, `l10n_key`, `params` (actor display name only), `deep_link`. **No email, no GPS, no chat body, no tokens, no secrets** |
| Logs | Event name, counts, outcome class, latency. `redact()` blanks `token`/`message`/`location` by key |
| PII | Actor display name is public social data (`social_profiles`), already exposed in chat/team payloads |
| Enumeration | Notification ids are UUIDs; all reads are `WHERE recipient_user_id = viewer.id`. A guessed id → 404 |
| IDOR | Covered by the ownership scope above |
| Rate limiting | §12 |

**The most important security property:** a push payload must never contain enough
information to distinguish "you were blocked" from "there was no message". It
carries a notification **id** and a **type** — and no notification is created for
a blocked conversation at all.

---

## 18. Account deletion

**Audit result: there is no account-deletion path in the repository.**
Searched `app/services/**` and `app/api/v1/**` for `delete_account`,
`deleted_at =`, and account-delete routes: none.

`users.deleted_at` is a `timestamptz` column that `get_current_user`
(`deps.py:37`) and `auth_service` read to reject the token, but **nothing ever
writes it**. `user_profiles`, `refresh_sessions`, and `password_reset_tokens`
all cascade from `users`; `messages.sender_user_id` is RESTRICT and the 0009
migration comment states the deletion path "is expected to tombstone the user
instead".

Therefore, for Phase 8.4:

| Item | Recommendation | Basis |
|---|---|---|
| `push_devices` | **CASCADE** from `users.id`. A deleted account must have zero live push targets; keeping a token would deliver to a device that can no longer authenticate | No competing precedent; `refresh_sessions` already cascades |
| `notifications` (received) | **CASCADE** | Same reasoning |
| `actor_user_id` | **RESTRICT**, tombstoned | Matches `messages.sender_user_id` exactly (ADR-14 §2.4). A notification must not vanish because its actor was deleted |
| Retention | No retention policy invented | — |

**OPEN DECISION — blocking.** Stop-rule 3: *"account deletion behavior is undefined
and materially affects data retention."* That is exactly the state. Phase 8.4 can
choose CASCADE for the recipient (unambiguously correct) but **cannot** settle
actor tombstoning or whether a 30-day grace period applies without the product
owner's input. The audit marks this OPEN and does not resolve it.

---

## 19. Migration plan

**`0010_notifications`**, `down_revision = "0009_chat"`. Head is confirmed at
`0009_chat`, and every prior revision follows the same additive pattern.

Structure, copying `0009_chat`:

- `push_platform` enum: `create_type=False` + `.create(checkfirst=True)`
- `push_provider` enum: same
- `notification_type` enum: same
- Tables `push_devices`, `notifications`
- `ALTER TABLE users ADD COLUMN` — **none**. No existing table is touched.
- Indexes and the partial `UNIQUE(dedupe_key) WHERE dedupe_key IS NOT NULL`

**Explicitly NOT in this migration: `notification_preferences`** (§4, deferred).

| Property | Expectation |
|---|---|
| Downgrade | `drop_table` both, then `ENUM.drop(checkfirst=True)` — matches `0009_chat` |
| Fresh chain | `upgrade head` from `0001` on an empty DB |
| Reused DB | `upgrade head` against a populated DB with existing chat/team data |
| Regressions | `0009_chat` untouched; the `test_migrations` step-down chain gains one step |

**Extend `tests/test_migrations.py`** with `PHASE84 = {"push_devices",
"notifications"}`, a `NOTIFICATION_COLUMNS` map, and a `_constraints` assertion
for the two unique indexes — the Phase 8.3 pattern, which exists precisely so a
constraint cannot be lost in a later edit.

---

## 20. API design

Derived from `app/api/v1/chat.py` and `social.py` conventions, not from the brief's
suggested routes.

| Method | Path | Auth | Notes |
|---|---|---|---|
| `GET` | `/api/v1/notifications` | `get_current_user` | Standard `items/total/page/page_size`; `?page`, `?page_size` ≤ 100; `?unread_only=true` |
| `GET` | `/api/v1/notifications/unread-count` | `get_current_user` | `{unread_count: int}` — separate because the app bar polls it and a full page would be wasteful |
| `POST` | `/api/v1/notifications/{id}/read` | `get_current_user` | Idempotent; returns the updated row; **404** for another rider's id |
| `POST` | `/api/v1/notifications/read-all` | `get_current_user` | Returns `{marked: n}`; 200 even at zero |
| `GET` | `/api/v1/push-devices` | `get_current_user` | The rider's own devices; **never returns `token`** |
| `POST` | `/api/v1/push-devices` | `get_current_user` | Register/refresh. **Idempotent** on `(user_id, provider, device_id)` → 200 on update, 201 on insert |
| `PATCH` | `/api/v1/push-devices/{id}` | `get_current_user` | `enabled` only; **404** if not the owner |
| `DELETE` | `/api/v1/push-devices/{id}` | `get_current_user` | Hard delete — a revoked token has no reason to persist. **404** if not the owner |

**No public endpoint creates a notification.** Notification creation is internal.
A public `POST /notifications` would be an unauthenticated-content broadcast
vector and is explicitly rejected.

**Errors:** `NOTIFICATION_NOT_FOUND`, `PUSH_DEVICE_NOT_FOUND`,
`PUSH_DEVICE_INVALID`, `PUSH_DEVICE_PLATFORM_UNSUPPORTED`,
`PUSH_DEVICE_DISABLED`. 404 for anything not yours — never 403, matching the
Phase 8.3 convention.

**Idempotency:** device registration is naturally idempotent via the unique
constraint. `POST /notifications/{id}/read` and `read-all` are idempotent by
construction.

---

## 21. Pagination

**Standard `items/total/page/page_size` — NOT cursor.**

Reason: unlike message history, a notification center is **bounded and bounded on
purpose**. It is the one list a rider pages through deliberately, and new
notifications arrive at the *head* only while they are reading page 1 — the
unstable-offset problem Phase 8.3 solved with cursors does not arise for a list
they scroll once. The existing envelope also gives the UI a total for free.

`next_seq`-style cursor pagination would be wrong here: it adds a key the rest of
the project does not use, for no benefit.

**Unread count must not scan history.** A `partial index
ix_notifications_recipient_unread ON notifications(recipient_user_id) WHERE
read_at IS NULL` makes the count an index-only scan over *unread* rows only.
Without it, `COUNT(*) WHERE read_at IS NULL` degrades linearly with total
notifications — the concrete likely bottleneck in this feature (§31).

---

## 22. Observability

Audit of `app/core/logging.py`: `_SENSITIVE` already includes `token`, `secret`,
`password`, `authorization`, `message`, `location`, `latitude`, `longitude`. A
push token is therefore redacted **by column name** with no change needed.

**Log:** registration (device id, platform, provider — never the token),
provider attempt (notification id, device count), provider result (outcome class,
latency), invalid token (device id + reason), retry (attempt number), permanent
failure (notification id, provider, error class).

**Never log:** the raw token, `params` (contains actor display names), message
content, or any rendered notification text. Follow the `chat_service._log`
discipline: *"the discipline is the point — message content must not reach the
log pipeline at all."*

---

## 23. Test architecture

**Backend** (mirroring `test_chat_api.py` grouping: each test name states the rule
it defends):

- Model/constraint: `UNIQUE(user_id, provider, device_id)`; `UNIQUE(provider, token)`;
  partial `UNIQUE(dedupe_key)`; enum values
- Migration: extend `test_migrations.py` — `PHASE84`, columns, the two unique
  constraints, full step-down chain
- API: register, duplicate register (200 not 201), list, unread-count, mark read,
  read-all, device disable
- **IDOR**: another rider's notification → 404; another rider's device → 404;
  guessed UUID → 404
- **Block**: no notification for a blocked DM in either direction; the sender gets
  no error indicating a block; unblock resumes
- **Team**: removed member receives nothing; team channel notifications still
  delivered while blocked; archived team stops chat notifications
- Device ownership: multi-device fanout; same token as a second account transfers
- Idempotency: duplicate event → one notification; worker re-run → no duplicate
- Read state: mark one, mark all, unread counts
- Pagination: envelope shape, `unread_only`, page cap
- Provider: `FakePushProvider` records calls; `invalid_token` disables the device;
  transient error retries; timeout raises
- **Privacy**: payload contains no email/GPS/body/token; suppressed recipients
  produce **no row** (not a hidden row)
- Rate limits: registration, read-all
- OpenAPI: paths present under `/api/v1/notifications` and `/api/v1/push-devices`

**Flutter** (`test/notifications_test.dart`, mirroring `test/chat_test.dart`):

- Domain parsing incl. unknown `l10n_key` → generic fallback (not a crash)
- Repository: idempotent registration, read, read-all
- Providers: unread count, mark read, registration lifecycle
- Widgets: notification list, empty state, error+retry, unread badge
- Deep links: valid, malformed, wrong-entity, blocked, queued-before-auth
- Lifecycle: foreground refresh, logout unregister, token rotation
- Localization: EN/FR/AR parity; **no Latin leakage in Arabic**; French apostrophes
- RTL: Arabic notification list renders mirrored

---

## 24. Live smoke plan

`backend/live_smoke_notifications.py`, four accounts (A, B, C, D), run against a
**fresh** and a **reused** database. Scenarios:

1. A registers a device → 201; duplicate registration → 200, same id
2. B registers a second device → A now has 2 rows, both returned without tokens
3. Social friend request A→C → C has one `friend_request`, unread count 1
4. Accept → requester has one `friend_request_accepted`
5. Team invite → invitee has one `team_invitation`
6. **Blocked DM**: A blocks C; a DM send produces **no notification** for either
   party; unblocking → a subsequent send produces notifications again
7. **Team channel while blocked**: A and C are both in team X and blocked; A
   sends in the channel → **both** receive notifications
8. Removed member: remove B from team X → a subsequent channel message produces
   **no** notification for B
9. Archived team → `team_archived` delivered; subsequent channel sends produce
   none
10. Mark read → unread count decrements; read-all → zero; read-all again → 0
11. IDOR: B reads A's notification id → 404; B disables A's device → 404
12. Pagination: >100 notifications, verify envelope and no overlap
13. Deep-link payload safety: assert no email, GPS, chat body, or token in any
    notification JSON
14. Disabled device receives no delivery attempt (via `FakePushProvider` records)
15. Rate limit: burst registration → 429
16. Unauthenticated → 401 on every route
17. Migration fresh + reused

Deliberately **not** in the smoke: real FCM/APNs calls. `FakePushProvider` records
attempts so delivery assertions are deterministic and offline.

---

## 25. Concurrency audit

| Race | Required mechanism |
|---|---|
| Two registrations of the same device simultaneously | `UNIQUE(user_id, provider, device_id)`; loser catches `IntegrityError`, re-reads, returns 200. **No advisory lock needed** |
| Same token registered by two accounts | `UNIQUE(provider, token)`; the row transfers to the later account |
| Duplicate notification creation | Partial `UNIQUE(dedupe_key)`; loser re-reads. **No lock** |
| Read racing creation | Independent rows; no interaction |
| Worker retry | Job carries ids; re-reading is idempotent |
| Device disabled during delivery | Delivery re-reads `enabled` per device at send time; a row disabled mid-flight may receive one last push. Acceptable — the window is milliseconds and the alternative is a lock held across a network call |
| Logout racing delivery | **Not fully solvable** — the access token has no session binding (§26). Best effort: client-side `deleteToken` + server-side disable |
| Token refresh racing push | The refresh `UPDATE`s the token; an in-flight push may use the old token and get `invalid_token` → device disabled → next refresh re-enables. **Self-healing, but produces a spurious disable.** Worth a test |

**Avoid:** an advisory lock on notification creation. The unique index is the
arbiter; a lock would only add contention.

---

## 26. Performance

Concrete likely bottlenecks, in order:

1. **Unread count** — O(history) without the partial index. **Mitigated by
   `ix_notifications_recipient_unread WHERE read_at IS NULL`.** This is the one
   real risk.
2. **Multi-device fanout** — one provider call per device. A rider with 3 devices
   triples cost. Acceptable; batch-cap at 500 devices per call.
3. **Team fanout** — a message to a 500-member team creates 499 notification rows
   **synchronously** inside the chat transaction. This is the largest write
   amplification in the feature. Mitigation: enqueue row creation for
   `len(recipients) > N` (recommend N = 20) so a small team stays transactional
   and a large one goes async. **This is a real design decision, not premature
   optimization.**
4. **Recipient resolution** — computed per event from
   `conversation_members`/`team_memberships`; already indexed.
5. **Retention** — `notifications` grows monotonically. A retention policy is
   **deferred** (§4) but the table needs an eye on it before it matters.

Not optimizing: provider batching, connection pooling, or notification archiving.

---

## 27. Dependency audit

**No new backend dependency is required.** `httpx` (FCM HTTP v1, APNs HTTP/2),
`arq`, and `redis` are all already declared. The AI provider precedent already
proved vendor-SDK-free is acceptable.

| Package | Purpose | Why existing is insufficient | Mandatory? |
|---|---|---|---|
| `firebase_admin` (py) | FCM HTTP v1 | `httpx` suffices; a vendor SDK would break neutrality | **Optional** — recommend against |
| `httpx` (already present) | Both providers | — | Present |
| `arq` (already present) | Job queue | — | Present, needs wiring |

**Mobile:**

| Package | Purpose | Mandatory? |
|---|---|---|
| `firebase_messaging` | FCM token + handlers | **Only if FCM is adopted** — **OPEN DECISION** |
| `flutter_local_notifications` | Foreground display, Android channels, iOS categories | Recommended when push ships; **deferrable** if 8.4 ships in-app-only |
| `uuid` (already present) | Client `device_id` | Present |
| `flutter_secure_storage` (already present) | Store `device_id` | Present |

**Native configuration required:**
- Android: `POST_NOTIFICATIONS` permission; notification channels; **and, for
  FCM only**, the Google Services Gradle plugin + `google-services.json`.
- iOS: `aps-environment` entitlement, `remote-notification` background mode, and
  an APNs key uploaded to the provider. **Entitlements cannot be created without
  an Apple developer team ID.**

**Risk note:** `firebase_messaging` pulls the FlutterFire toolchain and forces a
`google-services.json` into the repo — which CI, where no Firebase project
exists, cannot build. This is the strongest argument for the
abstractions-with-fake-first approach and is a principal driver of the §32
decision.

---

## 28. Documentation plan (for the implementation phase)

- `docs/adr/ADR-15-push-notifications.md` — new
- `docs/phase-8.4-report.md` — new
- `docs/03-database-model.md` — append `push_devices` + `notifications`
- `docs/04-api-map.md` — append the 8 endpoints
- `docs/06-realtime-social.md` — record push vs. polling transport
- `docs/02-security-privacy.md` (if it exists) — token handling; **verify existence
  during implementation**

---

## 29. Scope control

### IN SCOPE
Notification domain model; push device registration; multi-device; device
enable/disable/revocation; in-app notification center; unread/read state;
notification creation service; provider-neutral push abstraction; fake push
provider; Android push architecture; iOS push architecture; deep-link
architecture; authorization/privacy checks; idempotency; delivery failure
handling; rate limiting; token/device lifecycle; EN/FR/AR localization; backend
tests; Flutter tests; live smoke; migration; documentation.

### OUT OF SCOPE — must remain out
WebSockets · Redis Pub/Sub · live location · location sharing · synchronized
group rides · group ride sessions · GPS broadcasting · background GPS · chat
media · image messaging · file attachments · voice · video · reactions ·
comments · feed · leaderboards · BLE · Garmin · Polar · Komoot · payments ·
subscriptions · ads · AI Coach notification *emission* (representation only) ·
training automation · route sync · ride sync · social presence · typing
indicators · chat delivery receipts · per-message read receipts · admin
moderation · notification analytics · marketing campaigns · email · SMS ·
notification preferences table (deferred, §4).

**Audit confirmation:** nothing in this proposal requires any of the above.
`app/websocket/manager.py` and `app/workers/settings.py` are the two files most
tempted by scope creep; `manager.py` must remain untouched.

---

## 30. Decision matrix

| # | Decision | Current state | Recommended | Reason | Risk | Status |
|---|---|---|---|---|---|---|
| 1 | Push provider architecture | None | `PushDelivery` Protocol + factory, mirroring `app/ai/provider.py` | Proven in-repo precedent | Low | **Decided** |
| 2 | FCM adoption | No SDK, no `google-services.json` | **Defer FCM provider; ship abstraction + fake** | Avoids CI-breaking Firebase toolchain | Med | **OPEN DECISION** |
| 3 | APNs adoption | No entitlement, no team ID | Defer; design for it | Needs an Apple developer account | Med | **OPEN DECISION** |
| 4 | Worker vs synchronous | Stub, `redis_settings = None` | Hybrid: row sync, push via ARQ | Row must be transactional; push must not block | Low | **Decided** |
| 5 | `push_devices` table | None | Required; `UNIQUE(user_id, provider, device_id)` + `UNIQUE(provider, token)` | Nowhere else to store delivery targets | Low | **Decided** |
| 6 | `notifications` table | None | Required; partial `UNIQUE(dedupe_key)` | Core of the center + idempotency | Low | **Decided** |
| 7 | `notification_preferences` | None | **Defer**; no table in 8.4 | Single-row per-user data; project stores this on `user_profiles` | Low | **OPEN DECISION** (product) |
| 8 | Notification content | None | Hybrid: `l10n_key` + `params` stored; push fallback localized by device locale | OS renders push text; server cannot observe device locale | Med | **Decided** |
| 9 | l10n interpolation | `get()` has no args | Extend `get()` with named args | Prevents hand-rolled `replace` corrupting RTL | Low | **OPEN DECISION** |
| 10 | Deep links | None | Allowlist + post-auth replay; hoist `GoRouter` | Router rebuild currently discards cold-start destination | Med | **OPEN DECISION** |
| 11 | Token storage | `refresh_hash` precedent is hashed | Store plaintext; never log; never return | Push token must be *presented* to the provider, so hashing is unavailable | Med | **OPEN DECISION** |
| 12 | Device lifecycle | None | Client `device_id`; rotation = UPDATE; `last_seen_at` sweep | Vendor tokens rotate; identity must not | Low | **Decided** |
| 13 | Idempotency | Message precedent available | `dedupe_key` from immutable row id; partial UNIQUE | Deterministic across retries | Low | **Decided** |
| 14 | Rate limiting | In-process | Reuse `allow()` with surface-specific limits | Same known per-process limitation | Low | **Decided** |
| 15 | Pagination | Offset standard; cursor in chat | Offset `items/total/page/page_size` | Bounded list; stable for its use | Low | **Decided** |
| 16 | Block behavior | ADR-14 §2.1 locked | Suppress DM notifications both directions; **continue team-channel delivery** | Must match the message path or push becomes the oracle | **High** | **Decided** — do not change |
| 17 | Team behavior | Live re-check established | Re-read `team_memberships` at emit | Roster rows outlive membership | Low | **Decided** |
| 18 | Account deletion | **No endpoint exists anywhere** | CASCADE recipient; RESTRICT+tombstone actor | Matches `messages.sender_user_id` | **High** | **OPEN DECISION** (stop-rule 3) |
| 19 | Migration | Head `0009_chat` | `0010_notifications`, additive, no `users` alteration | Established convention | Low | **Decided** |
| 20 | Dependencies | None for backend | Zero new backend deps; `firebase_messaging` only if FCM adopted | Neutrality; CI | Low | **Decided** |
| 21 | Notification expiry | `conversations.archived_at` unused | No TTL | A center is history, not a queue | Low | **Decided** |
| 22 | Large-team fanout | None | Sync ≤20 recipients, enqueue above | Write amplification in the chat transaction | Med | **OPEN DECISION** |

---

## 31. Security / privacy matrix

| Scenario | Expected result | Authorization | Notification allowed? | Sensitive data exposed? | Reason |
|---|---|---|---|---|---|
| Friend request | Target notified | Public profile + request policy | **Yes** | Display name only | Normal 8.1 event |
| Friend request accepted | Requester notified | Relationship exists | **Yes** | Display name only | — |
| Team invitation | Invitee notified | Inviter must be a manager | **Yes** | Team name only | Normal 8.2 event |
| Team join request | Managers notified | Requester must be a member | **Yes** | Team name, username | — |
| Team member removed | Removed rider notified | Remover must be owner/admin | **Yes** | Team name only | Tells them what they must know |
| Team archived | Members notified | Owner-only action | **Yes** | Team name only | — |
| Chat message (DM) | Recipient notified | Participant + **no block either way** | **Yes** | Actor name, **never the body** | 8.3 policy |
| **Blocked DM** | **No notification either party** | Block check at emit | **No — no row created** | Nothing | Must not reveal a block or a message |
| Blocked DM, sender view | No error indicating a block | — | — | — | Absence must be symmetric |
| Unblocked DM | Notifications resume | — | **Yes** | As above | History retained |
| **Team channel while blocked** | **Both parties notified** | Live team membership | **Yes** | Actor name, never the body | Block does not sever team; silence would be an oracle |
| Removed team member | No further team notifications | Live membership gone | **No** | Nothing | Roster row survives; must not be trusted |
| Archived team | No new chat notifications | Team archived | **No** (except the archive notice) | — | Send path already 403s |
| Private conversation | No notification to a non-participant | Membership required | **No** | Nothing | — |
| Guessed notification id | 404 | `recipient_user_id = viewer` | — | Nothing | Not an existence oracle |
| Guessed device id | 404 | `user_id = viewer` | — | Nothing | Tokens never returned |
| Disabled device | No delivery attempt | `enabled = false` | **No** | — | — |
| Logged-out device | Best-effort disable | Client revokes | Unreliable | — | Access token has no session binding |
| Deleted account | Recipient rows cascade; actor tombstoned | **OPEN DECISION** | — | — | No deletion path exists |

---

## 32. Implementation plan (proposed, NOT executed)

| Step | Deliverable |
|---|---|
| 1 | Resolve the three OPEN DECISIONS (§30) — **gate** |
| 2 | `alembic/versions/0010_notifications.py` — additive, reversible |
| 3 | `app/models/notifications.py` — enums + 2 tables |
| 4 | Extend `tests/test_migrations.py` — `PHASE84`, columns, constraints |
| 5 | `app/schemas/notifications.py` — outputs, page envelope, device register input |
| 6 | `app/core/config.py` — notif settings (mirroring the 12 AI fields) |
| 7 | `app/notifications/types.py` + `delivery.py` — `PushDelivery`, error taxonomy, factory (mirroring `app/ai/`) |
| 8 | `app/notifications/fake.py` — `FakePushProvider` |
| 9 | `app/services/notification_service.py` — creation, dedupe, read state |
| 10 | Emission hooks in `chat_service` / `team_service` / `social_service`, after commit |
| 11 | `app/api/v1/notifications.py` — 8 routes |
| 12 | `app/workers/settings.py` — wire ARQ + `deliver_push` job |
| 13 | Flutter `domain/` + `data/` |
| 14 | Flutter `presentation/` — center, badge, settings |
| 15 | Deep links — allowlist + post-auth replay (router change if Option A) |
| 16 | Localization — ~30 keys × 3 locales + interpolation |
| 17 | Backend tests |
| 18 | Flutter tests |
| 19 | `backend/live_smoke_notifications.py` |
| 20 | Documentation + final gates |

---

## 33. Final implementation gates

**Backend:** all existing tests pass · all new tests pass · **no test deletions to
hide failures** · ruff clean · mypy clean · `alembic upgrade head` · `alembic
downgrade -1 && upgrade head` · full chain to `base` and back · fresh DB · reused
DB · 505 baseline still green.

**Flutter:** all existing tests pass · all new tests pass · `flutter analyze` clean
· `dart format --set-exit-if-changed` clean · web build · APK build · 368 baseline
still green.

**Security:** authorization tests · IDOR tests · device-ownership tests ·
block/privacy tests · **no sensitive push payloads** · **no token leakage in logs
or responses** · **no notification enumeration** · suppressed recipients produce no
row.

**Live:** fresh-DB smoke · reused-DB smoke · repeated smoke · **no known script
defects** (the Phase 8.3 smoke had two; the same discipline applies).

**Documentation:** ADR-15 · `phase-8.4-report.md` · DB model · API map · realtime
doc.

---

## 34. Stop-rule assessment

| Stop-rule | Triggered? |
|---|---|
| 1. Existing notification infra conflicts with design | **No** — nothing exists |
| 2. FCM/APNs strategy needs a product decision | **YES** — §30 #2, #3 |
| 3. Account deletion undefined and materially affects retention | **YES** — §18, §30 #18 |
| 4. Push token storage/security unresolved | **YES** — §30 #11 |
| 5. Notification privacy conflicts with block policy | **No** — a compliant design exists and is specified (§8). **But** it is binding and must not be simplified |
| 6. Team notification behavior needs a product decision | **No** — ADR-14 §2.1 already settles it |
| 7. Worker insufficient → new infrastructure required | **No** — completing an existing ARQ stub is not new infrastructure |
| 8. Migration would require destructive changes to `0009_chat` | **No** — purely additive |
| 9. Deep-link authorization cannot be guaranteed | **No** — destinations already re-authorize; the router needs care but not a redesign |
| 10. A dependency introduces an architectural conflict | **No**, provided FCM stays deferred (§27) |
| 11. Unrelated regression in existing tests | **No** — 505 backend / 368 Flutter green |
| 12. Would require WebSockets, live location, or group rides | **No** |

**Three stop-rules are triggered (2, 3, 4).** None requires inventing
infrastructure; all three are decisions only the product owner can make.

---

## 35. Defects found during this audit

Reported, **not fixed** — this run is audit-only.

| # | Location | Defect | Severity | Recommendation |
|---|---|---|---|---|
| D1 | `mobile/lib/features/home/home_page.dart:33-36` | A notification bell icon navigates to `/friends`. Once a notification center exists this is actively misleading | Medium | Repoint or remove in 8.4 |
| D2 | `backend/app/services/chat_service.py:172-174` | Docstring says *"A historical message stays readable to both parties even after a block … only sending is refused."* The code at 200-202 **refuses reads** (404) | Medium | Behavior matches the brief; the **docstring is stale** and states the opposite of the code |
| D3 | `backend/tests/test_chat_api.py:210` | Test named `test_a_block_stops_dm_sending_but_keeps_history_readable` asserts the opposite (reads refused) | Low | **Test is correct; the name is misleading.** Rename |
| D4 | `mobile/lib/core/l10n/app_localizations.dart:1892` | `get()` ends `_values['en'][key]!` — a key absent from **all** locales is a hard crash | Medium | Widen the notification blast radius; needs a safe fallback in the notification renderer regardless |
| D5 | `mobile/lib/core/routing/app_router.dart:52-55` | `GoRouter` rebuilt on every auth change with `initialLocation: '/splash'` | Low today | Blocks cold-start deep links (§15) |
| D6 | `backend/app/workers/settings.py:12` | `redis_settings = None`; no job has ever run | Low | Wire in 8.4 |

**D2/D3 are the most important to record**: Phase 8.4 notification authorization
must match the **code**, not the docstring. The code refuses reads during a block;
the docstring says otherwise. Anyone implementing from the docstring would build
a notification path on the wrong premise.

---

## 36. Final report

```
# PHASE 8.4 ARCHITECTURE AUDIT — FINAL

Status:
    BLOCKED — DESIGN ISSUE REQUIRES DECISION

Files inspected:
    ~150. Key paths:
      backend/app/ai/{provider,types,fake,service}.py          (provider precedent)
      backend/app/api/deps.py, app/core/{security,errors,logging,rate_limit,config}.py
      backend/app/db/session.py, app/redis/client.py, app/workers/settings.py
      backend/app/websocket/manager.py
      backend/app/models/{user,chat,team,social}.py
      backend/app/services/{chat,team,social,auth}_service.py
      backend/app/api/v1/{chat,teams,social,auth,profile}.py
      backend/alembic/versions/{0008,0009}_*.py
      backend/tests/{conftest,test_chat_api,test_migrations}.py
      backend/pyproject.toml, docker-compose.yml, .github/workflows/ci.yml
      mobile/pubspec.yaml, lib/main.dart
      mobile/lib/core/{network/api_client,storage/token_storage,l10n/app_localizations,routing/app_router}.dart
      mobile/lib/features/{auth/presentation/auth_state,home/home_page,chat/**}.dart
      mobile/android/app/src/main/AndroidManifest.xml, android/app/build.gradle.kts
      mobile/ios/Runner/{Info.plist,AppDelegate.swift}
      mobile/test/chat_test.dart
      docs/adr/ADR-14-chat-messaging.md, docs/03-database-model.md, docs/04-api-map.md

Files changed:
    ONLY docs/phase-8.4-architecture-audit.md

Production code changed:
    NO

Migration changed:
    NO

Dependencies changed:
    NO

Phase 8.3 regression:
    NONE FOUND
    (Baseline re-verified this run: 505 backend tests pass, 368 Flutter tests pass,
     flutter analyze clean, alembic head = 0009_chat.)
    Six defects were recorded in §35 — all pre-existing, none introduced by this
    audit, none fixed. D2/D3 are documentation/naming only; behavior is correct.

Major findings:
    1. ZERO notification infrastructure exists — FCM, APNs, device tokens,
       providers, channels, entitlements, deep links, and lifecycle observers are
       all absent. Verified by exhaustive search, not inferred from dependencies.
    2. The project ALREADY has the right architecture to copy: app/ai/provider.py
       is a Protocol-based provider seam with a status→retryable mapper, a
       refusing factory, a set_provider() test hook, and a FakeAIProvider.
       Phase 8.4 should mirror it rather than invent anything.
    3. THE WORKER IS A STUB. app/workers/settings.py is 12 lines with
       redis_settings = None. arq is declared and Redis runs, but no job has ever
       been defined. This does NOT trigger stop-rule 7 — completing a declared
       stub adds no new infrastructure and no new dependency.
    4. THERE IS NO ACCOUNT-DELETION PATH ANYWHERE. users.deleted_at is read by
       auth but never written. Phase 8.4 cannot settle notification retention
       without the owner's decision.
    5. THE ROUTER IS REBUILT ON EVERY AUTH CHANGE with initialLocation
       hard-coded to /splash, so cold-start notification taps lose their
       destination. Deep links need a router decision.
    6. AppLocalizations.get() has a non-null assertion and NO interpolation
       support. A missing key is a crash, and notification text needs
       parameterized strings in three locales including RTL.
    7. A stale docstring in chat_service.py:172-174 states the OPPOSITE of what
       the code does about reading a blocked DM. Any 8.4 work implemented from
       the docstring would build on the wrong privacy premise.

Open decisions:
    1. FCM/APNs adoption — or abstractions + fake only in 8.4. Drives native
       config, google-services.json, CI buildability, and mobile dependencies.
       (Recommended: abstractions + fake; defer both providers.)
    2. Account deletion and notification retention — no deletion path exists.
       CASCADE for recipient rows is unambiguous; actor tombstoning and any
       grace period are not. (Stop-rule 3.)
    3. Push token storage — plaintext vs. hash. Hashing is unavailable because a
       push token must be PRESENTED to the provider, unlike refresh_hash which is
       only ever verified. (Recommended: plaintext, never logged, never returned,
       at-rest encryption.)

    Secondary, resolvable during implementation:
    4. l10n interpolation support (extend `get()` vs. compose in the widget).
    5. Deep-link router strategy (hoist GoRouter vs. navigate post-restore).
    6. Large-team fanout threshold (sync ≤20 recipients, enqueue above).

Recommended implementation sequence:
    1. Resolve the three open decisions — this is a gate, not a step.
    2. alembic/versions/0010_notifications.py (additive, reversible, no ALTER on users)
    3. app/models/notifications.py (2 tables, 3 enums, partial UNIQUE dedupe_key)
    4. Extend tests/test_migrations.py (PHASE84, columns, constraints, step-down chain)
    5. app/schemas/notifications.py
    6. app/core/config.py (notif settings, mirroring the 12 AI fields)
    7. app/notifications/{types,delivery}.py (PushDelivery Protocol, mirroring app/ai/)
    8. app/notifications/fake.py (FakePushProvider)
    9. app/services/notification_service.py (creation, dedupe, read state)
    10. Emission hooks in chat/team/social services — AFTER commit, re-deriving authorization
    11. app/api/v1/notifications.py (8 routes, 404-not-403)
    12. app/workers/settings.py (wire ARQ + deliver_push job)
    13. Flutter domain/ + data/ (no Firebase import outside push_registration.dart)
    14. Flutter presentation/ (center, unread badge, repoint or remove the home bell — D1)
    15. Deep links (allowlist + post-auth replay)
    16. Localization (~30 keys x 3 locales, + interpolation if approved)
    17. Backend tests (IDOR, block, device ownership, idempotency, privacy leak)
    18. Flutter tests (deep links, lifecycle, RTL, authorization failures)
    19. backend/live_smoke_notifications.py (fresh + reused + repeated)
    20. ADR-15, phase-8.4-report.md, DB/API/realtime doc updates, final gates

Strict non-goals confirmed:
    YES
    (WebSockets, Redis Pub/Sub, live location, location sharing, synchronized
    group rides, group ride sessions, GPS broadcasting, background GPS, chat
    media, image messaging, file attachments, voice, video, reactions, comments,
    feed, leaderboards, BLE, Garmin, Polar, Komoot, payments, subscriptions, ads,
    AI Coach notification emission, training automation, route sync, ride sync,
    social presence, typing indicators, chat delivery receipts, per-message read
    receipts, admin moderation, notification analytics, marketing campaigns,
    email, SMS. app/websocket/manager.py remains untouched.)

STOP.
```
# Phase 7 — Repository Audit (integration map)

Date: 2026-09-29 | Phase: 7 | Status: Complete | Read-only, no code changed

Phase 7 was required to start with an audit of the existing system so the Coach
layer is added *around* the deterministic engine rather than through it. This is
that audit, reduced to the facts that constrain the implementation.

## 1. Backend — what already exists

| Concern | Where | Constraint for Phase 7 |
|---|---|---|
| App assembly | `app/main.py` → `create_app()`, mounts `app.api.v1.v1` | Register one new router in `app/api/v1/__init__.py`. |
| Settings | `app/core/config.py`, `Settings(BaseSettings)`, `model_config = SettingsConfigDict(env_file=".env", extra="ignore")` | Add `AI_*` fields to this class. Env-driven, no secrets in code. |
| Errors | `app/core/errors.py` — `error_body(code, message, details)`, handlers for validation / HTTP / unhandled | Services raise `{code, message}` via `HTTPException(detail={...})`; reuse that exact shape. |
| Auth | `app/api/deps.py::get_current_user` → `User` (profile eager-loaded); `security.decode_access_token` | Every Coach route depends on it. Never accept a user id from the body. |
| DB session | `app/db/session.py::get_db` → `AsyncSession` | Same dependency; no second session factory. |
| Ownership | `ride_service.get_owned`, `route_service.get_owned` / `get_readable`, `bike_service.get_owned` — one `select`, owner predicate + id predicate, `scalar_one_or_none()`, single `raise <Domain>Error("<ENTITY>_NOT_FOUND", ..., 404)` | Foreign resources are **404, never 403**. The Coach must not leak existence. |
| Rate limit | `app/core/rate_limit.py::allow(key, limit, window_s)` — in-memory sliding window over `_buckets` | Reuse `allow()`. Do not introduce a second limiter. The autouse `_clear_rate_limits` fixture resets `_buckets` between tests. |
| Logging | `app/core/logging.py` — `logging.getLogger("cyclecoach")`, `request_id` ContextVar, `redact()` | `redact()` is defined but **never called** anywhere. Coach logging must use it, and must not log prompts, messages, or location. |
| Training engine | `app/services/training_calc.py` (pure, no I/O) + `training_service.py` | Authoritative source of every metric and every bounded prescription. |
| Calculation registry | `training_calc.CALCULATION_VERSIONS`, mirrored to `training_calculation_versions` by `0006` | Every Coach observation cites a version. |
| HTTP client | `httpx>=0.27` is an existing **runtime** dependency | The real provider uses `httpx`. No new SDK dependency needed or wanted. |
| LLM SDKs | none — no `openai`, `anthropic`, `langchain`, `litellm`, or any LLM package | Greenfield. |
| Entitlements | none — no entitlement/premium/subscription/billing table, column, setting, or helper anywhere | No billing, no fake `is_pro`. |
| Tests | `tests/conftest.py`: **real PostgreSQL** (`cyclecoach_test`, `TEST_DATABASE_URL` override), `Base.metadata.create_all`, `httpx.AsyncClient(transport=ASGITransport(app=app))`, autouse `_clear_rate_limits`, `asyncio_mode = "auto"` | No shared auth fixture — each test file has private `_user(client, data)` helpers. Follow that. |
| Migration head | `0006_training_foundation`; `tests/test_migrations.py` exercises downgrade/upgrade on a scratch DB `cyclecoach_migtest` | A Phase 7 migration must keep that test green. |

Existing endpoints Coach can read from: the eleven `/api/v1/training/*` routes and
the five `/api/v1/workouts` routes, plus `/api/v1/rides/*` and `/api/v1/routes/*`
for the ride→route linkage.

Ride↔route linkage is two nullable columns on `rides`: `route_id` (FK,
`ondelete="SET NULL"`, indexed) and `route_version` (plain `Integer` holding
`route_versions.version_no`, pinned at association time, never rewritten). There
is no eager load — the Coach resolves the route itself through
`route_service.get_readable` + `get_version`.

## 2. Mobile — what already exists

| Concern | Where | Constraint for Phase 7 |
|---|---|---|
| State | Riverpod (no Bloc) — `mobile/pubspec.yaml`, `lib/features/auth/presentation/auth_state.dart` | Add a `features/coach/` module in the same style. No new state library. |
| API client | `lib/core/network/api_client.dart` — `ApiClient`, `ApiException`, bearer injection | Coach repository takes the existing client; it does not create one. |
| Client provider | `apiClientProvider` lives in `auth_state.dart` | Inject it; do not relocate it. |
| Router | `lib/core/routing/app_router.dart` | Add `/coach` following the existing route conventions. |
| Localization | `lib/core/l10n/app_localizations.dart` — hand-rolled, **not** `intl`; `en`, `fr`, `ar` with RTL delegates | Add keys to the same maps; do not introduce `intl` or a second l10n system. |
| Theme | `lib/core/theme/app_theme.dart`, `app_colors.dart` | Reuse tokens; no ad-hoc colors. |
| Repository pattern | `lib/features/training/data/training_repository.dart` | Mirror it. |
| Tests | `flutter_test` only, `MockClient` from `http/testing`, `ProviderContainer` overrides, `tall()` surface harness | No `mocktail`/`build_runner` codegen — stay with the existing harness. |
| Existing Coach code | none — no `lib/features/coach/`, no AI settings, no AI strings | Greenfield. |

## 3. Gaps the audit surfaced

1. **`redact()` is unwired.** The helper exists precisely for this problem and is
   never called. Coach logging is the first real consumer.
2. **`X-Request-ID` is echoed but not adopted.** `app/main.py` returns the inbound
   header while the `request_id` ContextVar is always freshly generated, so the
   two can disagree. Not a Phase 7 defect to fix, but Coach logs must be read
   using the ContextVar value, not the response header.
3. **The rate limiter is in-memory only.** It is per-process and resets on
   restart. Acceptable for Phase 7 given the single-process deployment, and
   explicitly noted as a limitation rather than silently relying on it as a
   global limit.
4. **`request_id` in the log format is `-` outside a request.** Coach log lines
   emitted from background/ARQ paths will show `-`; that is expected.

## 4. Audit conclusion

Nothing in the current system anticipates an LLM: no table, column, setting,
dependency, endpoint, or string. Phase 7 is additive — a new `app/ai/` package,
a new `app/schemas/coach.py`, one new router, one new mobile feature module — and
it changes no Phase 1–6 behaviour. No migration is required, because Phase 7
persists no conversations and no coach state.

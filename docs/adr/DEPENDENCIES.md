# Dependency policy record (Phase 1)

Every dependency needs: why, fit, no-duplicate check.

## Backend (`backend/pyproject.toml`)
- fastapi — API framework, OpenAPI native. No alternative.
- uvicorn[standard] — ASGI server for local/dev.
- pydantic>=2, pydantic-settings — schemas + typed env config.
- sqlalchemy[asyncio], asyncpg — async Postgres access.
- alembic — migrations (Rule 7).
- redis — cache/pub-sub/rate-limit/ARQ broker.
- arq — async workers (ADR-03).
- httpx — test client + future provider calls.
- pyjwt — access-token JWT (HS256). Refresh tokens are opaque (ADR-04).
- argon2-cffi — Argon2id password hashing (OWASP-recommended; never plaintext).
- email-validator — EmailStr validation in auth schemas.
- psycopg[binary] — sync driver for Alembic migrations only (runtime uses asyncpg).
- pytest, pytest-asyncio — testing foundation.
- ruff — lint+format single tool (no black/isort/flake8 duplicates).
- mypy — type checks (gradual, foundation only).

Rejected: celery (heavier than arq), structlog (stdlib logging suffices Phase 1), django (wrong paradigm).

## Flutter (`mobile/pubspec.yaml`)
- flutter_riverpod — state (ADR-02). No bloc/provider/GetX alongside.
- go_router — declarative routing, deep-link ready.
- intl — ICU formatting, arb codegen base.
- flutter_localizations (SDK) — RTL + delegates.
- (test) flutter_test (SDK) only.

Rejected: bloc (ADR-02), get (anti-clean-arch), dio/hive/geolocator (needed Phase 4–5, NOT Phase 1 — do not add early).

## Phase 2 additions
Backend: pyjwt + argon2-cffi + email-validator (see above). No OAuth provider SDK
yet (email+password only; OAuth is a later phase).
Flutter:
- flutter_secure_storage — ONLY secure token store. Why: Android Keystore /
  EncryptedSharedPreferences, iOS Keychain. shared_preferences would leave
  refresh tokens extractable. MemoryTokenStorage exists solely as a test double.
- http (+ http/testing MockClient) — minimal API transport behind ApiClient.
  No dio: interceptors unneeded at this scale; http keeps the dependency tree
  small and MockClient makes state tests hermetic.

## Phase 4 additions
Backend: none (Haversine + engine are pure stdlib).
Flutter:
- drift (+ drift_dev/build_runner dev-only codegen) — local-first ride store
  (ADR-07). Typed tables, versioned migrations, in-memory test DBs.
- sqlite3_flutter_libs — SQLite on Android/iOS for drift.
- geolocator — positions + permission states (ADR-08). No background-service
  plugin yet (deferred until device-measured need).
- path_provider + uuid — direct deps (DB file path, client UUIDs for
  idempotency keys).

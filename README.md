# CycleCoach — Train • Ride • Explore

Worldwide GPS-first cycling platform (Flutter + FastAPI + Postgres + Redis).
Phase 2: auth + cyclist profile. No GPS/training/AI/chat/sensors yet.

## Architecture
`mobile/` (Riverpod + go_router, dark-first theme) → `/api/v1/` (modular FastAPI
monolith) → Postgres (Alembic) + Redis (cache/pub-sub/ARQ). Workers: ARQ.
Decisions: `docs/adr/ADR-02-state-management.md` (Riverpod),
`docs/adr/ADR-03-workers.md` (ARQ), `docs/adr/ADR-04-auth-tokens.md` (JWT+rotation),
`docs/adr/DEPENDENCIES.md`.

## Structure
```text
backend/app/{core,api/v1,db,models,schemas,services,repositories,workers,websocket,redis}
mobile/lib/{core/{theme,l10n,routing,config},features,shared/widgets}
docker-compose.yml  postgres:16 + redis:7
.github/workflows/ci.yml
```

## Requirements
Python 3.12, Flutter stable (3.44 / Dart 3.12 verified), Docker + Compose.

## Local setup
```powershell
docker compose up -d                      # postgres + redis
cd backend; copy .env.example .env        # edit SECRET_KEY for real envs
pip install -e ".[test]"                  # + ruff mypy for dev
alembic upgrade head                      # Phase 1: foundation only, no tables
uvicorn app.main:app --reload             # docs at /docs, health at /api/v1/health
cd ..\mobile; flutter pub get; flutter run --dart-define=APP_ENV=dev --dart-define=API_BASE=http://localhost:8000
```

## Env vars
`ENVIRONMENT, LOG_LEVEL, API_PREFIX, DATABASE_URL, REDIS_URL, SECRET_KEY, CORS_ORIGINS,
ACCESS_TOKEN_MINUTES, REFRESH_TOKEN_DAYS, PASSWORD_RESET_MINUTES, EMAIL_VERIFY_HOURS`
— see `backend/.env.example`. Never commit `.env`.

## Auth (Phase 2)
`POST /api/v1/auth/{register,login,refresh,logout,logout-all}`,
`GET /api/v1/auth/me`, `GET|PATCH /api/v1/profile`,
password-reset + email-verify under `/auth/`. Access JWT 15 min, opaque rotating
refresh 30 d (reuse burns family). Tests need Postgres: `TEST_DATABASE_URL`
(default `.../cyclecoach_test`). Mobile tokens live in secure storage only.

## Bikes (Phase 3)
Domain: `bikes` (owner FK, 13-category enum, active/archived, NUMERIC kg/km,
odometer baseline, OCC `version`, soft-delete). Decisions: `docs/adr/ADR-05-bikes.md`.
API: `POST|GET /api/v1/bikes` (page/page_size, category/status filters,
sort/order), `GET|PATCH|DELETE /api/v1/bikes/{id}`,
`POST .../{id}/{archive,restore}`. DELETE = idempotent soft delete; foreign
bikes → 404. PATCH accepts `expected_version` → 409 on conflict.
Mileage (Phase 4+): `initial_distance_km + SUM(rides.distance)`.
Mobile: `/bikes`, `/bikes/new`, `/bikes/:id`, `/bikes/:id/edit` (auth-guarded).

## Rides (Phase 4)
Raw observations vs derived metrics separated; provenance preserved.
Engine: Haversine, 25 m accuracy gate, duplicate/jump/speed rejection,
3 m elevation threshold, moving ≥1 m/s, pause-gap reset (docs/05, ADRs 06–08).
Tables: `rides` (bike FK RESTRICT, per-user idempotency UUID), `ride_points`
(raw+rejected preserved, per-ride idempotency keys). Migration `0004_rides`.
API: `POST|GET /api/v1/rides`, `POST .../{id}/points` (≤500/chunk),
`.../{id}/{pause,resume,finish,discard}`; finish recomputes deterministically.
Sync: local Drift DB is truth while recording → 100-pt chunks →
resume-from-last → finalize (`docs/ride-sync-protocol.md`).
Mobile: `/ride/start|recording|summary|recovery`, metric/imperial UI,
permissions only at ride start. Locked-screen multi-hour recording is
DESIGNED + configured, NOT device-verified here (see ADR-08).

## Testing / quality
```powershell
cd backend; pytest -q; ruff check app tests; ruff format --check app tests; mypy app
cd ..\mobile; dart format lib test; flutter analyze; flutter test
```

## CI
`ci.yml`: backend (ruff+format+mypy+pytest), mobile (format+analyze+test). No deploy in Phase 1.

## Workflow
One phase at a time. DB change → Alembic migration. API change → tests. Failing gate = BLOCKED.

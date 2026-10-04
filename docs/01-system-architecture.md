# 01 — System & Backend Architecture

```text
Flutter (iOS/Android; web/desktop-ready)
  | HTTPS + WSS (JWT access ~15min, refresh rotation)
  v
FastAPI — modular routers per domain (/api/v1/*)
  |-- PostgreSQL (system of record, Alembic migrations)
  |-- Redis (cache, pub/sub, rate-limit buckets, WS fan-out)
  +-- Workers (Arq/Celery): training-engine, ai-coach, route-processing,
       notifications, analytics aggregations
```

## FastAPI module layout (Phase 1 target)

```text
backend/
├── app/
│   ├── main.py  versioned app factory, /api/v1
│   ├── core/    config, security, logging, errors, pagination, redis, deps
│   ├── modules/
│   │   ├── auth/ users/ bikes/ rides/ routes/ training/ analytics/
│   │   ├── coach/ friends/ teams/ group_rides/ location/ chat/
│   │   ├── challenges/ subscriptions/ devices/ notifications/
│   │   └── common/ (gpx, geo, pagination mixins)
│   ├── realtime/  ws manager, channels, auth, presence
│   ├── workers/   tasks per domain
│   └── db/      base, session, migrations (Alembic)
├── tests/  unit + integration + api + ws + privacy
└── pyproject.toml
```

Rules: no cross-module direct DB writes except via service; every router uses Pydantic v2 schemas, consistent error envelope `{code,message,details}`, pagination `?page&limit&sort&filter`, OpenAPI contract test blocks drift.

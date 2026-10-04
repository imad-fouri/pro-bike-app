# ADR-03 — Background Workers: ARQ

Date: 2026-09-27 | Status: Accepted | Phase 1

## Decision: ARQ (async Redis Queue)

## Why not Celery
Celery is mature but sync-oriented, heavier broker semantics, more ops surface. CycleCoach workloads (GPS batch processing, training calc, AI jobs, notifications) are asyncio-native and Redis-backed already.

## Why ARQ
- Native asyncio, first-class FastAPI integration (shared Redis pool, same event loop model).
- Minimal ops: Redis only, no RabbitMQ.
- Typed jobs, retries, cron, job results — enough for Phases 4–8.
- Lightweight, maintained, easy to test (mock queue in pytest).

## Consequence
- `backend/app/workers/` holds ARQ `WorkerSettings` + task stubs (no business jobs in Phase 1).
- Redis connection shared between API cache/rate-limit/pub-sub and ARQ.
- If we outgrow ARQ (complex DAGs, multi-broker), revisit with new ADR. No Celery dependency added.

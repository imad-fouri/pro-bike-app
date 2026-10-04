# 10 — Roadmap & ADRs

Phases 0–19 per master prompt. Phase 1 next: Flutter scaffold + FastAPI scaffold + docker (Postgres+Redis) + config/logging/errors + CI + testing foundation.

ADR-01: Postgres+PostGIS(later)+Redis (relational integrity + geo future + realtime fan-out).
ADR-02: Riverpod vs Bloc — decide Phase 1, one only.
ADR-03: Modular FastAPI monolith first, split workers later (not microservices prematurely).
ADR-04: Route versioning with optimistic locking (prevents silent corruption).
ADR-05: Deterministic training rules + LLM explanation layer (safety).
ADR-06: Offline-first with idempotency keys (ride survival).
ADR-07: Abstracted map/routing provider.

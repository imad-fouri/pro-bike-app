# ADR-02 — Flutter State Management: Riverpod

Date: 2026-09-27 | Status: Accepted | Phase 1

## Decision: flutter_riverpod (Riverpod 2.x)

## Context
Needed: scalable feature isolation, async state (GPS streams, WS, offline sync queue), testability, long-term maintenance.

## Evaluation

| Criterion | Riverpod | Bloc |
|---|---|---|
| Async (StreamProvider/FutureProvider) for GPS/WS | Excellent, first-class | Good, needs extra boilerplate |
| Testability (override providers) | Excellent, no BuildContext needed | Good, bloc_test mature |
| Feature isolation / DI | ProviderScope overrides per feature/test | Good via RepositoryProvider |
| Boilerplate at 20+ features | Low | High (events/states per feature) |
| Background/isolate friendliness | Plain Dart providers, easy | Event-driven, heavier mapping |
| Maintainability | Compile-safe, refactor-friendly | Verbose but explicit |

Bloc is strong for explicit event-state flows (e.g. checkout wizards). CycleCoach is stream-heavy (location, sensors, chat, sync queue) where Riverpod providers compose better with less code.

## Consequence
- One state solution: Riverpod only. No Bloc, no GetX, no Provider mixed in.
- Async GPS/WS/sync state as StreamProvider/StateNotifier(AsyncNotifier).
- Tests override providers; no widget logic in presentation beyond watch/read.
- Revisit only if team hits concrete scaling limit (record new ADR).

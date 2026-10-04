# ADR-07 — Flutter Local Persistence: Drift (SQLite)

Date: 2026-09-27 | Status: Accepted | Phase 4

## Decision: Drift over sqflite / Hive / Isar.

Phase-0 note (§05) mentioned a Hive outbox; we deliberately deviate:

| Need | Drift | sqflite | Hive | Isar |
|---|---|---|---|---|
| Transactions (ride + 10k points atomic) | Yes | Manual SQL | No real txn | Yes |
| Typed queries + migrations | Yes (versioned) | Manual | No | Partial |
| Background-isolate access | Yes (same file) | Yes | Risky | Yes |
| Testability (in-memory) | `NativeDatabase.memory()` | Fiddly | OK | OK |
| Maintenance (2026) | Active | Active | Stagnant | Uncertain future |

## Consequence
- `drift` + `sqlite3_flutter_libs`; codegen via `drift_dev`/`build_runner`
  (dev-only). Tables: `local_rides`, `local_points`, `sync_state`.
- Local ride is source of truth while recording; backend is system of record
  after finalize. Sync engine uploads in chunks of 100 (configurable).
- Recovery: on launch, any local ride in recording/paused → recovery screen.

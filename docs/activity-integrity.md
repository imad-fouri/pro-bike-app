# Activity Integrity (WS-AC) - contract and vocabulary

WS-AC is a deterministic integrity foundation, not a complete anti-cheat system.

## 1. Why it exists

Ranking and challenges ("competition") aggregate *rides*. The aggregation code
is server-authoritative and provable only if what it aggregates is also
server-authoritative. WS-AC closes that gap: every completed ride gets a
**verdict** (`accepted` / `suspicious` / `rejected`), computed deterministically
by the server at the moment of finalisation, persisted on the ride, and used by
every competition surface through exactly one predicate.

Properties that are non-negotiable:

- **Deterministic** - the same ride facts always produce the same verdict. No
  probability, no ML, no fragmentation, no background queue in v1.
- **Total** - evaluation is a total function over stored facts; there is no
  "didn't run" state that silently sneaks a ride into an aggregate.
- **Fail-closed** - any absence (no verdict yet, an evaluation fault) means
  *not eligible*. The failure mode of "forgot to evaluate" is exclusion, never
  silent inclusion.
- **Explainable** - every verdict is a small, fixed list of rule ids, kept
  server-side, signed by the rule-set version that produced it.
- **Unforgeable by the client** - there is no request field that names a
  verdict, a score, eligibility, or evidence. Request schemas ignore smuggled
  fields; responses only expose the verdict to its owner.

## 2. Anti-cheat boundary

```
eligible activity
        |
        v
integrity evaluation          <- runs in ride_service._finalize
        |
        +---- accepted        -> aggregated, progress applied, points held
        +---- suspicious      -> retained, NOT aggregated (WS-AC)
        +---- rejected        -> retained, never aggregated (WS-AC)
```

- Evaluation runs inline in `_finalize`, **before** the `COMPLETED` commit, so
  the verdict lands atomically with the finish: a ride is either
  completed-with-verdict or still in progress, never one without the other
  (`backend/app/services/ride_service.py`).
- The verdict is written once and never rewritten by a re-run of the finish
  path; a new rule set ships as a new `calculation_version`, history keeps the
  version that produced it (`backend/app/models/ride.py:111`).
- The only code that defines "qualifying activity" is
  `app/services/activity_integrity.py`; both the ranking engine and the
  challenge engine import `QUALIFYING_RIDE` / `qualifying_rides()` from it, so
  gating that one predicate gates every scope, period, metric, challenge
  progress, completion and point award with zero engine changes.

## 3. Verdict vocabulary

`IntegrityStatus` (`backend/app/models/ride.py:35`) is a stored enum with three
values:

| Status       | Meaning                                                        | Eligibility |
| ------------ | -------------------------------------------------------------- | ----------- |
| `accepted`   | internally consistent and structurally sound                     | eligible    |
| `suspicious` | retained for history, excluded for now ("retained but not aggregated initially") | not eligible |
| `rejected`   | retained for audit, never aggregated                             | not eligible |

`None` (no verdict yet) is the value for every in-progress ride. It is **not**
eligible, by the fail-closed rule.

## 4. Qualifying activity

`QUALIFYING_RIDE` (`backend/app/services/activity_integrity.py:115`) is exactly:

- the ride row is `COMPLETED`,
- `ended_at` is present (a competition window needs an end instant),
- `integrity_status == ACCEPTED`.

Ownership is the foreign key on `rides.user_id`, never a request field.
Visibility, blocking, tombstones and geography are *scope* concerns that stay in
the engines (they differ per leaderboard); eligibility itself is common.

## 5. Rules catalog (v1)

`CALCULATION_VERSION = "v1"`. Rule ids are fixed, ordered, and greppable; the
output `rules_triggered` reports them in the emission order below.

**REJECT rules** - structural impossibilities. The request schema (`PointIn`)
and `gps_engine` already forbid them, so on the publicly reachable stack these
are defensive: if any write path, bug, or back-door ever stores such points, the
ride is categorically excluded for audit, never aggregated.

| Rule | Condition |
| ---- | --------- |
| `GPS_COORDINATE_INVALID` | any accepted point with lat/lon outside [-90,90]/[-180,180] |
| `GPS_POINT_ORDER_INVALID` | `seq` not strictly increasing over the accepted points |
| `GPS_TIME_SEQUENCE_INVALID` | `recorded_at` not strictly increasing over the accepted points |
| `GPS_SPEED_IMPLAUSIBLE` | `rides.max_speed_m_s > SPEED_MAX_M_S` (80 m/s) |

**SUSPICIOUS rules** - internally consistent but unverifiable-as-real riding.
They keep the point data and the ride in history, and the ride out of the
aggregate.

| Rule | Condition |
| ---- | --------- |
| `RIDE_NO_ACCEPTED_POINTS` | a completed ride with zero accepted points |
| `GPS_DISTANCE_MISMATCH` | the stored cumulative distance before finalise differs from a fresh recompute of the accepted points by more than 1 m |

Severity composes: any REJECT rule => `REJECTED`; else any SUSPICIOUS rule =>
`SUSPICIOUS`; else `ACCEPTED` (`activity_integrity.py:179`).

### Dropped from the v1 sketch: `DURATION_INCONSISTENT`

A moving-vs-elapsed rule was evaluated and dropped. The legitimate WS-RC hook
test future-dates points 5 s apart in near-zero wall time (moving ≈ 10 s >
elapsed ≈ 0.3 s), and real short rides behave the same; the rule would
false-reject valid existing tests and truthful short rides. Deferred, see §12.

## 6. Constants and tolerance

| Constant | Value | Source |
| -------- | ----- | ------ |
| `SPEED_MAX_M_S` | 80.0 | `gps_engine.py` |
| `DUPLICATE_DISTANCE_M` | 1.0 m | `gps_engine.py` (the repository's existing "same fix" resolution constant) |
| distance-mismatch tolerance | `Decimal(DUPLICATE_DISTANCE_M)` | bound retain; the engine folds incrementally during ingest and re-folds at finish, both rounded to the centimetre, so a healthy ride differs by at most a few centimetres |

The coordinate bounds are duplicated next to the rule (`_LAT_BOUNDS` /
`_LON_BOUNDS`) and are identical to `schemas.ride.PointIn` and the engine, so
the three can never drift silently.

## 7. Evaluation contract

`evaluate(ride, accepted_points, *, pre_finalize_distance_m=None) ->
IntegrityResult` (`activity_integrity.py:144`).

- **Pure and total**: no database, no history, no ML. It needs only the ride
  row and the server-accepted observations, ordered by seq.
- `accepted_points` are the accepted `RidePoint`s rebuilt as `Observation`s.
- `pre_finalize_distance_m` is the last stored cumulative total before the
  finish-path recompute (last fold), and is only available on the finish path;
  the mismatch check is call-order independent because it recomputes distance
  internally.
- The result carries `status`, `rules_triggered` (stable emission order),
  `calculation_version`, and `evaluated_at`.

**Fail-closed on fault**: if `evaluate` raises, `_finalize` writes
`REJECTED` + `["EVALUATION_FAULT"]` + version `v1`, records
`integrity_evaluate outcome=error` in the metrics registry, and never produces
`ACCEPTED` (`ride_service.py`). An evaluation fault is distinguishable from a
rule verdict in logs and metrics.

## 8. Persistence

Four nullable columns on `rides`
(`backend/app/models/ride.py:117`):

| Column | Type | Notes |
| ------ | ---- | ----- |
| `integrity_status` | enum(`integrity_status`) | lowercase values via the shared `_values` convention |
| `integrity_calculation_version` | `String(32)` | `"v1"` for every v1 verdict |
| `integrity_rules_triggered` | `JSONB` | empty array on accept; else rule ids in emission order |
| `integrity_evaluated_at` | timestamptz | set on finalise |

The verdict is server-only; `RideOut` exposes only `integrity_status`
(read-only), never the rules, version, evaluation time, points, or evidence.

## 9. Migration `0014_activity_integrity`

- Adds the four nullable columns; `integrity_status` uses a
  `postgresql.ENUM` created explicitly with `checkfirst=True` before the
  `ALTER TABLE ADD COLUMN` (an `op.add_column` references the type, it does not
  create it), matching the 0013 column-enum posture; no separate
  `CREATE TYPE` elsewhere, no un-droppable leftovers. Downgrade drops the
  columns, then the type.
- **Backfill is a provenance label, not a recalculation**: every completed
  ride with a `NULL` verdict is stamped `accepted` / `v1` / `[]` /
  `integrity_evaluated_at = updated_at`. This preserves eligibility of the
  rides that legitimately predate WS-AC; it does not invent a rule evaluation.
  Newly finalized rides after deploy are evaluated normally.
- Idempotency is preserved: no constraint is loosened; the existing
  `uq_rides_user_client_uuid`, per-point dedupe keys, and challenge progress
  ledgers are untouched.

## 10. Security surface

- **No input authority.** `RideCreate`, `PointIn` and `PointsChunk` have no
  integrity fields; FastAPI/Pydantic v2 ignores extras (`extra="ignore"`), so a
  body smuggling `integrity_status`, `integrity_score`, `verified`,
  `eligible_for_ranking`, or `eligible_for_challenges` is silently dropped and
  the verdict is still whatever the server computed. Covered by
  `test_integrity.py::test_client_cannot_declare_a_verdict` and the live smoke.
- **No leakage.** Only the verdict is visible to the owner. `integrity_rules_triggered`,
  `integrity_calculation_version`, `integrity_evaluated_at`, evidence, and GPS
  never appear on any response. Rankings and challenge payloads do not mention
  integrity at all. Covered by `test_integrity.py::test_integrity_surface_leaks_no_rules_or_evidence`
  and live smoke checks 3/8.
- **No bypass header or partner flag.** The evaluation cannot be skipped or
  overwritten by any request, including the ride-finish replay (409).

## 11. Observability

- `record_competition_event(event="integrity_evaluate", outcome=...)` with the
  bounded three-way outcome from the enum (`accepted` / `suspicious` /
  `rejected`) plus `error` on fault.
- Metrics labels are bounded (`app/core/metrics.py`); ride ids, coordinates,
  and rule sets never touch a metric, log, or response. A metrics failure must
  never become a 500 (all call sites use the documented `metrics_record_failed`
  pattern).

## 12. Explicitly deferred (documented as future work)

- Cross-ride replay/similarity heuristics, team-wide or route-copy detection.
- Machine learning / behavioural scoring.
- Auto-ban / appeals / moderation workflow.
- Sensor validation (HR strap, power meter, cadence) - no sensor hardware yet.
- `DURATION_INCONSISTENT` and any moving-vs-elapsed rule (§5).
- Silent recalculation of historical rides (deliberately avoided: new rule sets
  ship as new versions and only newly finalized rides are evaluated).

These are documented here and in the phase report precisely so that a later
phase can pick them up without re-litigating the v1 boundary.

## 13. Test plan

- **Rules**: `tests/test_integrity.py` exercises every v1 rule and severity
  composition on fabricated in-memory rides with no database.
- **Integration**: acceptable verdict is ranked and counted; suspicious and
  rejected rides are excluded from rankings and challenge progress/completions,
  including the seeded fixtures the API cannot produce.
- **Security**: smuggling is ignored; the response surface leaks nothing;
  double-finish is a stable 409.
- **Migrations**: `tests/test_migrations.py` runs the full
  upgrade → downgrade → upgrade ladder including the new `0014` ⇄ `0013` step
  and asserts the `integrity_*` columns.
- **Live smoke**: `live_smoke_activity_integrity.py 1` and `2` (see §14).

## 14. Live smoke

`backend/live_smoke_activity_integrity.py` runs against a real uvicorn,
PostgreSQL and Redis and proves, end to end: the client cannot declare a
verdict; a healthy ride is `accepted`, ranked, and counted exactly once against
a challenge with no retroactive credit; a zero-point ride is `suspicious` and
excluded; a 50 km `REJECTED` fixture (seeded only because the API cannot produce
one) is excluded from distance and challenge progress; the response surface
leaks no rules/version/evidence/GPS/identity; and re-finishing is a stable 409.
Two consecutive runs (`1`, `2`) both pass 17/17. The WS-RC smoke still passes
40/40 on the same server.

## 15. Definition

WS-AC is a deterministic integrity foundation, not a complete anti-cheat system.
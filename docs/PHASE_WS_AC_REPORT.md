# Phase 11 — WS-AC: Activity Integrity & Anti-Cheat Foundation

Status: **PASS**
Branch: `master` · Baseline: `203aa74` · Date: 2026-10-07

WS-AC is a deterministic integrity foundation, not a complete anti-cheat system.

---

## 1. What WS-AC built

The competition half of the community pillar (rankings + challenges, WS-RC) was
server-authoritative on the *aggregation* side but trusted the `rides` table
innocently: anything the server itself had stored as a completed ride was
aggregatable. WS-AC closes that hole with a deterministic per-ride integrity
verdict that ranks and challenges are forced to respect through one predicate.

- Every completed ride is evaluated at finalisation, **atomically with the
  `COMPLETED` commit**, by a pure, total, deterministic function:
  `app/services/activity_integrity.evaluate()`.
- The verdict is a stored enum (`accepted` / `suspicious` / `rejected`) plus a
  rule-set version and a server-side list of the rules that fired.
- `QUALIFYING_RIDE` — the *single* definition of "qualifying activity" — now
  requires `integrity_status == ACCEPTED`, and both the ranking engine and the
  challenge engine import it, so every scope, period, metric, progress row,
  completion and point award is gated with zero engine changes.
- Fail-closed: unevaluated (in-progress) rides and evaluation faults are never
  eligible. `suspicious` is retained-but-not-aggregated; `rejected` is
  retained-for-audit and never aggregated.
- The client cannot name a verdict, score, eligibility flag, or evidence on any
  request; responses expose only the verdict to its owner.

## 2. Baseline and provenance

- Baseline commit: `203aa74` (WS-RC live smoke verification). No existing
  commits rewritten; the new work is additive on top.
- Migration `0014_activity_integrity` is the new single head
  (`down_revision == 0013_rankings_challenges`).
- The dev database was migrated (`alembic upgrade head`) during this phase;
  the backfill stamped the 7 pre-existing completed rides as
  `accepted` / `v1`, verified by direct query. This is a **provenance label,
  not a recalculation**: it preserves eligibility of legitimately predating
  rides without inventing a rule evaluation.

## 3. Implementation

| File | Change |
|---|---|
| `backend/app/services/activity_integrity.py` | **New engine.** `IntegrityResult` (frozen dataclass + `eligible`), rule ids, `evaluate()` (pure/total), `CALCULATION_VERSION = "v1"`, `QUALIFYING_RIDE` / `qualifying_rides()` |
| `backend/app/services/ride_service.py` | `_finalize` captures the last stored cumulative distance, evaluates before the commit, writes the four verdict fields, and fail-closes with `["EVALUATION_FAULT"]` + `integrity_evaluate outcome=error` on any exception |
| `backend/app/models/ride.py` | `IntegrityStatus` enum + 4 nullable `integrity_*` columns on `rides` |
| `backend/app/schemas/ride.py` | `RideOut.integrity_status` read-only output |
| `backend/app/api/v1/rides.py` | `_out` exposes the verdict; nothing else |
| `backend/alembic/versions/0014_activity_integrity.py` | **New migration.** 4 columns, explicit enum create (`checkfirst`), provenance backfill, clean downgrade |
| `backend/tests/test_integrity.py` | **New.** 18 tests: rules, integration, security |
| `backend/tests/test_migrations.py` | Extended with `INTEGRITY_COLUMNS` ladder assertions + `0014 ⇄ 0013` step |
| `backend/tests/_competition_helpers.py` | `seed_ride` labels `accepted/v1`; new `seed_integrity_ride` for the unproducible verdicts |
| `backend/live_smoke_activity_integrity.py` | **New live smoke**, 17 checks/run, run twice |
| `docs/activity-integrity.md` | **New.** contract, vocabulary, rules catalog, constants, security surface |
| `docs/PHASE_WS_AC_REPORT.md` | This report |

No mobile files changed: the mobile surface is unchanged (ride upload + shared
leaderboard). The verdict is server-side; the app learns it by re-reading the
ride it just finished.

## 4. Rules (v1)

`CALCULATION_VERSION = "v1"`.

REJECT (structural impossibilities — defensive against any write-path bug or
back-door; the request schema already forbids them): `GPS_COORDINATE_INVALID`,
`GPS_POINT_ORDER_INVALID`, `GPS_TIME_SEQUENCE_INVALID`, `GPS_SPEED_IMPLAUSIBLE`
(> `SPEED_MAX_M_S` = 80 m/s).

SUSPICIOUS (reachable): `RIDE_NO_ACCEPTED_POINTS` (a completed ride with zero
accepted points), `GPS_DISTANCE_MISMATCH` (last-stored-update distance differs
from a finish-path recompute of the accepted points by more than 1 m).

Severity composes: any REJECT ⇒ `REJECTED`; else any SUSPICIOUS ⇒
`SUSPICIOUS`; else `ACCEPTED`. `rules_triggered` lists every rule that fired in
a stable emission order.

`DURATION_INCONSISTENT` was deliberately **dropped** from v1: the legitimate
WS-RC hook test and truthful short rides future-date points 5 s apart in
near-zero wall time, so a moving-vs-elapsed rule would false-reject them. It is
documented as deferred (§8).

## 5. Backend tests

Full suite: **1063 passed** (`pytest -q -p no:warnings`), exit 0.

- `tests/test_integrity.py` — 18 new tests:
  - *Rules (pure, no DB)*: healthy accept, determinism, each REJECT rule,
    no-points and distance-mismatch suspicious rules, the 1 m tolerance,
    reject-wins severity, all-rules emission order.
  - *Integration*: accepted ⇒ ranked; no-points ⇒ suspicious and excluded;
    seeded REJECTED + SUSPICIOUS excluded from rankings and from challenge
    progress/completions; finish-twice ⇒ stable 409 and unchanged verdict;
    verdict survives ride detail.
  - *Security*: smuggled `integrity_status`/`eligible_*` on ride-create and
    points-chunk are ignored; the response surface (ride detail, rankings,
    challenges) leaks no rules/version/evidence/GPS/identity.
- `tests/test_migrations.py` — the upgrade ⇒ downgrade ⇒ upgrade ladder now
  includes `0014` (creates the enum, adds columns, backfills) and the
  `0014 → 0013` step (drops columns + type); the head/re-upgrade assertions
  cover the 4 `INTEGRITY_COLUMNS`.
- WS-AC did not regress the 30 WS-RC tests (`test_rankings.py`,
  `test_challenges.py`, `test_competition_security.py`), all rides/security/
  privacy/observability suites — all green, all reported in the 1063.

## 6. Security verification

- **No input authority**: `RideCreate`, `PointIn`, `PointsChunk` carry no
  integrity fields; smuggled extras are ignored (Pydantic v2 default), so a
  client cannot declare its own verdict. Verified in unit tests and the live
  smoke.
- **No leakage**: `RideOut` shows only the verdict; `integrity_rules_triggered`,
  `integrity_calculation_version`, `integrity_evaluated_at`, evidence and GPS
  never reach a response. Rankings and challenge payloads do not mention
  integrity at all. Verified by sweep (`test_integrity.py` + smoke check 8).
- **Fail-closed**: unevaluated rides (`None`) are not eligible; an evaluation
  exception yields `REJECTED` + `["EVALUATION_FAULT"]` + `outcome=error`, never
  `ACCEPTED`.
- **Determinism / idempotency**: the same facts ⇒ the same verdict; a verdict
  is written once and a re-`finish` is 409; existing idempotency constraints
  (`uq_rides_user_client_uuid`, per-point dedupe, challenge progress ledgers)
  are untouched.
- **Bounded observability**: `competition_events_total{event="integrity_evaluate",
  outcome=accepted|suspicious|rejected|error}`. No ids, coordinates, or rule
  sets in metrics/logs; metric failures can never 500 a request.

## 7. Migration

- `0014_activity_integrity`: `add_column` ×4 (enum created explicitly with
  `checkfirst=True` before the ALTER — `op.add_column` references the type, it
  does not create it), nullable so a rolling deploy is safe; backfill UPDATE
  stamps completed rides `accepted`/`v1`/`[]`/`evaluated_at=updated_at`;
  downgrade drops the columns then the type. Idempotency constraints intact.
- The explicit-enum hazard was caught by the migration test in this phase:
  alembic's `add_column` does not auto-create a `postgresql.ENUM`; fixed and
  verified on the ladder (`2 passed`) and on the live dev database.

## 8. Known limitations / deferred (documented, not invented)

Cross-ride replay/similarity heuristics, ML/behavioural scoring, auto-ban /
appeals / moderation, sensor (HR/power/cadence) validation, `DURATION_INCONSISTENT`,
and silent recalculation of historical rides. Documented in detail in
`docs/activity-integrity.md` §12. No NotificationType was added (PG enums
cannot be dropped); no paywall or monetization gate was introduced.

## 9. Live smoke

`backend/live_smoke_activity_integrity.py` against the real dev server:

```
RUN 1:  17/17 checks passed
RUN 2:  17/17 checks passed   (reused DB state, fresh accounts)
```

Proves end-to-end: the client cannot declare a verdict (bike/ride/points
smuggle vectors ignored); a healthy ride is `accepted`, ranked, and counted
exactly once against a challenge with no retroactive credit (`ended_at >=
joined_at`); a zero-point ride is `suspicious` and excluded; a 50 km
`REJECTED` fixture (seeded only because the API cannot produce one) is excluded
from distance and challenge progress; no rules/version/evidence/GPS/identity on
any response surface; re-finish ⇒ stable 409; unauthenticated access ⇒ 401.

The WS-RC competition smoke (`live_smoke_competition.py`) still passes
**40/40** on the same server and database after the WS-AC changes.

## 10. Final status

**PASS** — baseline `203aa74`, migration `0014` applied forward and backward,
full backend suite 1063/1063 green, ruff/mypy clean on every changed file (the
only repo-wide lint residues are two pre-existing `F541`s in the untouched
`live_smoke_observability.py`), both WS-AC smoke runs and the WS-RC smoke green,
and every server-authority rule verified. Ranking and challenge eligibility is
now a single, deterministic, fail-closed gate.
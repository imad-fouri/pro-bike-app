# ADR-10 — Training Engine: Deterministic Calculations, Provenance, and What We Refuse to Compute

Date: 2026-09-28 | Status: Accepted | Phase: 6

## Context

Phase 6 turns recorded rides into training metrics. The hard requirement is not
"show some numbers", it is that **every number a rider sees can be traced to an
exact, testable formula** — and that the system says "I don't know" instead of
inventing data. This ADR fixes the formulas, the versioning discipline, the
storage model, and the boundary against AI.

Three facts drive the design:

1. **There are no sensors yet.** The app records GPS only. Power, heart rate and
   cadence arrive as `NULL`. The engine must therefore be *absent-tolerant*: it
   has to produce a complete, honest, useful answer for a phone-only ride and a
   richer one when data exists, with the same code path.
2. **A number without provenance is a liability.** If we store `power_load` we
   must also store which FTP produced it, which version of the formula ran, and
   whether the FTP was measured or estimated — otherwise a future formula change
   silently rewrites history.
3. **Bugs in this domain are safety bugs.** Over-prescribing training hurts
   people. So the maths is a pure, testable module with no I/O, and the
   prescriptive logic is bounded by hard caps.

## 1. Pure calculation module, no I/O

`app/services/training_calc.py` has **no database, no HTTP, no clock, no
randomness**. Every function is a pure transform, so tests assert exact values
with no fixtures and the same input always yields the same output.

Consequences, and they are deliberate:

- The service layer is responsible for reading samples, resolving FTP, and
  persisting; the module is only maths.
- The same code powers the API, a CLI, and future batch recomputation.
- `analyze_activity()` is covered by a **differential test** against a
  deliberately naive reference implementation (list slicing, no `deque`,
  no segmentation). That test caught two real off-by-one bugs in the rolling
  window during development; it is the single most valuable test in the phase.

Ride/GPS truth is **not** duplicated here. Distance, elevation, moving time and
point acceptance stay canonical in `gps_engine` / `ride_service` (ADR-06); the
training module consumes those numbers and adds only the fitness layer.

## 2. Versioned calculations — the registry is the contract

`training_calc.CALCULATION_VERSIONS` is the single source of truth. Twelve
versions are registered (`coggan_7zone_v1`, `hremax_5zone_v1`,
`karvonen_5zone_v1`, `np_30s_v1`, `if_ftp_v1`, `tss_style_v1`,
`trimp_5zone_v1`, `ftp_20min_095_v1`, `ftp_ramp_095_v1`, `ewma_42_7_v1`,
`load_delta_7d_v1`, `activity_analysis_v1`), each with its parameters.

Rules:

- **The version string travels with the number.** `training_activities` stores
  the analysis version; `training_loads` stores the trend version; every FTP
  record stores the protocol version that produced it.
- **Changing a formula means adding a version**, never editing one. Historical
  rows keep their version and stay reproducible.
- The registry is **mirrored into `training_calculation_versions`** by migration
  `0006`, and a drift test asserts `set(CALCULATION_VERSIONS) == set(table)`. A
  new formula cannot be added without registering it.
- The client can read the registry (`GET /training/calculation-versions`) so the
  app can show "which maths produced this" instead of an unexplained score.
  That endpoint is **code-first**: it reports `CALCULATION_VERSIONS` and uses the
  table only to flag a version's `active`/`retired` state. A freshly deployed
  formula is therefore visible to clients immediately, without waiting for a
  migration — but an unseeded row can never invent a version, because the
  description always comes from the code registry.

## 3. Formulas (exact, as implemented)

Constants: `MAX_SAMPLE_GAP_S = 10`, `NP_WINDOW_S = 30`,
`MIN_ANALYZED_SECONDS = 60`, plausible FTP `50–1500 W`, plausible HR `25–250 bpm`.

**Segmentation.** Samples are sorted by time and split wherever the gap exceeds
10 s. A gap is a pause, a tunnel, or a dropped link. **No average, rolling
window or zone attribution is ever bridged across a gap**, and the warm-up of
each segment is paid again after every resume.

**Normalized power** (`np_30s_v1`). Time-weighted fourth-power mean of 30 s
rolling average power. An interval contributes only once the trailing 30 s
window behind it is fully covered, so for a ride starting at `t0` the first
contributing interval ends at `t0 + 30`; the 29 s warm-up is excluded rather than
diluted. A 20 s sample burst yields `NULL`, not `0`.

**Power zones** (`coggan_7zone_v1`), fractions of FTP, **closed intervals**
(Coggan puts 90 % of FTP in tempo and 105 % in threshold, so the upper bound
belongs to the lower zone):

| Zone | Name | % FTP |
|---|---|---|
| 1 | active_recovery | 0–55 |
| 2 | endurance | 55–75 |
| 3 | tempo | 75–90 |
| 4 | threshold | 90–105 |
| 5 | vo2max | 105–120 |
| 6 | anaerobic | 120–150 |
| 7 | neuromuscular | 150+ |

**Heart rate zones**, five zones at 0–60 / 60–70 / 70–80 / 80–90 / 90+ for both
`%HRmax` (`hremax_5zone_v1`) and Karoven/HRR (`karvonen_5zone_v1`). HRR is
preferred when resting HR is known because it is more accurate; `%HRmax` is used
when only a maximum is known; **with neither threshold, heart rate zones are
unavailable and are not guessed from age.**

**Intensity factor** (`if_ftp_v1`): `NP / effective FTP`. Requires both power
data and an effective FTP; otherwise `NULL`.

**Power load** (`tss_style_v1`): `power_seconds / 3600 × IF² × 100`. This is a
CycleCoach formula. **It is not TrainingPeaks TSS and the API states so**, and
the two numbers are not interchangeable. The time base is `power_seconds` (time
covered by power samples), never the whole sensor window, so a heart-rate-only
stretch cannot inflate power load.

**Heart rate load** (`trimp_5zone_v1`): minutes in zone × zone weight (1…5). A
**separate scale with its own column**. Power load and HR load are never summed,
averaged or presented as one "training load".

**Load trend** (`ewma_42_7_v1`): each constant relaxes toward the day's load from
its *own* previous value — `CTL_t = CTL_{t-1} + (load_t − CTL_{t-1})/42`, likewise
ATL over 7 days. `TSB = CTL_{t-1} − ATL_{t-1}`. Gaps count as zero load, so the
series is continuous. These are **fitness proxies, explicitly not medical or
readiness measures**, and the API/UI must not phrase them as such.

**Recovery signals** (`load_delta_7d_v1`): this week's load versus the previous
week, with a ±20 % band. Stable codes for the client to localize:
`insufficient_data` (fewer than 7 days with load), `load_increased`,
`load_decreased`, `load_stable`, `load_stale` (no load for 14+ days). **A signal
always ships with its evidence** (both window totals, the ratio, days since load)
so a coach or the rider can audit it. These are *observations about load*, not
readiness, illness, or recovery verdicts.

**Bounded prescriptions.** `suggest_intensity_target()` caps a single-session
load increase at 15 % and refuses to increase when the session would exceed 30 %
of the current weekly load. With no baseline it returns `unavailable` and
`no_baseline` rather than a number. This is the whole of Phase 6's prescriptive
logic: **bounded, explainable, and never a leap of faith.**

## 4. FTP provenance and precedence

`ftp_records` is **append-only**. A value is never edited in place and never
silently replaces a previous one.

`resolve_effective_ftp()` picks the value a calculation must use, totally
ordered so the result never depends on row order:

1. Records with an implausible value (outside 50–1500 W) are ignored entirely.
2. A **confirmed** source (`manual`, `test_20min`, `test_ramp`, `imported`)
   always beats `estimated`. An estimate never overrules something the rider or a
   test established.
3. Within a group, the **most recent `effective_at`** wins. Recency first: a rider
   who lowered FTP by hand last month means it, even though an older record came
   from a test protocol.
4. Ties break on source rank, then on `created_at` (newest first), then on `id` —
   so the outcome is deterministic even when two records share a date.

Ordering on `id` alone would not be a tie-break: `id` is a UUID, so its order is
arbitrary rather than chronological. A same-day pair of records must resolve to
the newer one, which is what `created_at` provides.

Adding a record does **not** rewrite stored metrics: existing activities keep the
FTP and version they were computed with. Re-deriving under a new FTP is an
explicit, visible action.

**Test protocols.**

- `ftp_20min_095_v1` — 0.95 × the best 20-minute average power, requiring a fully
  covered 20-minute window (10+ minutes of usable data at minimum). Not a
  guess about a protocol that was not performed.
- `ftp_ramp_095_v1` — 0.95 × the best 10-minute average power. Stored with
  `approximation: true`, surfaced in the UI as an approximation, and documented as
  less reliable than a 20-minute test. A protocol whose method is an estimate
  must say so in its own data, not just in prose.

## 5. Storage model

```
users 1─1 training_profiles            (ftp_w, thresholds, tz override, hr model)
users 1─n ftp_records                  (append-only, source + evidence)
users 1─n training_activities 1─n training_activity_zones
users 1─n training_loads               (per local date, one row)
training_calculation_versions          (registry mirror, read-only)
users 1─n workouts 1─n workout_steps   (deterministic prescriptions)
```

Decisions:

- **`training_profiles` holds no unit preference.** Metric/imperial already lives
  on `user_profiles.measurement_system`; duplicating it invites divergence.
  `training_profiles.timezone` is an *override* that falls back to the profile
  timezone, used to bucket loads by local date.
- **Domain separation holds.** A `TrainingActivity` references a `Ride` but is a
  separate row: it owns the derived metrics and provenance, and it survives
  changes to how rides are summarised. Rides are never widened to hold training
  columns. One training activity per ride (partial unique index).
- **`training_loads` is separate from activities.** Load is an aggregate over
  days; mixing it into per-activity rows would force recomputation of history to
  answer "what did I do last Tuesday?".
- **Canonical units are metric** (W, bpm, s, m) and `NUMERIC` for persisted
  calculations, matching docs/03 §43; the client converts for display.
- `workouts` use the same **optimistic concurrency** rule as routes: `PATCH`
  requires `expected_version`, mismatch → `409 VERSION_CONFLICT`.

## 6. Ride → training handoff, and the honest gap

A completed ride feeds the engine automatically and idempotently, so the athlete
never has to press "compute". Re-analysis after a profile change is a separate,
explicit call, because it overwrites derived values and must be visible.

`ride_points` gains **optional** `power_w`, `hr_bpm`, `cadence_rpm` (additive,
nullable, in migration `0006`). Only **accepted** points are analysed, matching
the canonical ride metrics, so a rejected GPS fix cannot contribute power.

**Phase 6 has no sensor hardware.** No BLE, no smart trainer, no HR strap
integration — those are later phases. The columns and the ingest path exist so the
pipeline is real and end-to-end testable, and they stay `NULL` in the field until
a sensor phase. We report this explicitly in the phase report rather than
implying a live integration. There is no estimation of power from GPS/speed,
which would be fabrication.

## 7. Privacy and security

Inherited unchanged and not re-litigated: owner-only access on every training
resource, foreign resources return **404** (no existence oracle), read-only
history rows, no public athlete profile, no location in any training payload,
writes rate limited, and every error uses the shared envelope
`{"error": {"code", "message", "details"}}`. Load and zone data are exactly as
private as the rides they came from.

## 8. API surface (justified, minimal)

| Endpoint | Why it exists |
|---|---|
| `GET/PUT /training/profile` | Thresholds + FTP pointer; the input to every calculation. |
| `GET/POST /training/ftp-records` | Append-only FTP history with provenance. |
| `GET /training/activities`, `GET /{id}` | Metric history and detail. |
| `POST /training/activities/{ride_id}/reanalyze` | Explicit re-derivation after a profile change. |
| `GET /training/loads` | Daily/weekly load + CTL/ATL/TSB series. |
| `GET /training/recovery` | Load-change signals **with evidence**. |
| `GET /training/summary` | One aggregate read for the dashboard, instead of three round trips. |
| `GET /training/calculation-versions` | Transparency: which maths produced which number. |
| `GET/POST/PATCH/DELETE /workouts` | Deterministic prescriptions with optimistic concurrency. |

Deliberately **absent**: adaptive plans, periodization generators, AI endpoints,
and anything that would invent a metric.

## 9. Performance

Analysis is O(n) over samples with a 30-sample rolling window. A 5-hour 1 Hz
stream (~18 000 samples) is asserted to complete well inside an API budget by a
test that would fail on a regression. The 30 s window is never re-scanned, which
is what an earlier draft of the FTP helper effectively did.

## 10. What Phase 6 explicitly does not do

Adaptive/periodized plan generation, AI interpretation, social, subscriptions,
BLE sensors, live location, and readiness or medical claims. The engine is built
so those can be added *on top* of a stable, versioned, deterministic base —
never as a replacement for it.

# 07 — Training Engine & AI Coach

## Training engine (deterministic, safe) — IMPLEMENTED in Phase 6

Inputs: completed rides with their derived training metrics, discipline, goal /
event date, availability. A **rules engine, not an LLM**, decides progression,
rest and taper. Missed or shortened sessions trigger **bounded** adaptation —
never blindly pushing forward, never an unsafe jump. Zones come from the versioned
methodology config plus the rider's FTP / heart rate thresholds.

**The authoritative specification of every formula, threshold and boundary is
[ADR-10](adr/ADR-10-training-engine.md), and the implementation is
`backend/app/services/training_calc.py` (a pure module: no I/O, no clock, no
randomness).** Summary:

| Concern | Decision (ADR-10 §3) | Version |
|---|---|---|
| Segmentation | Gap > 10 s ends a segment; nothing is averaged across it | — |
| Normalized power | Time-weighted 4th-power mean of 30 s rolling power; warm-up excluded | `np_30s_v1` |
| Power zones | Coggan 7 zones at 0/55/75/90/105/120/150 % FTP, closed intervals | `coggan_7zone_v1` |
| HR zones | 5 zones at 60/70/80/90 % of HRmax **or** of HRR (preferred) | `hremax_5zone_v1`, `karvonen_5zone_v1` |
| Intensity factor | `NP / effective FTP`; `NULL` if either is missing | `if_ftp_v1` |
| Power load | `power_seconds/3600 × IF² × 100` — **not TrainingPeaks TSS** | `tss_style_v1` |
| HR load | 5-zone TRIMP-style, a **separate scale**, never summed with power load | `trimp_5zone_v1` |
| Load trend | 42/7-day EWMA (CTL/ATL/TSB) — fitness proxies, not medical measures | `ewma_42_7_v1` |
| Recovery signal | This week vs last week, ±20 % band, shipped **with evidence** | `load_delta_7d_v1` |
| FTP | 0.95 × best 20-min average; ramp variant stored as an explicit approximation | `ftp_20min_095_v1`, `ftp_ramp_095_v1` |

Non-negotiable rules:

- **Missing input ⇒ `NULL`, never `0`, never a guess.** No power data means no
  intensity factor; no heart rate thresholds means no HR zones. We never estimate
  power from GPS speed, and never guess max HR from age.
- **Every stored number carries its formula version** plus the FTP and its
  provenance, so a change to the maths cannot silently rewrite history.
- **FTP is append-only** and resolution is totally ordered: confirmed sources
  beat `estimated`, then most recent `effective_at` wins, then source rank, then
  `created_at`, then `id`. `id` is a UUID, so it only guarantees determinism —
  recency comes from the two date columns.
- **Progression is capped** (≤ 15 % single-session load increase) and refuses to
  act without a baseline.

`RIDE`, `TRAINING ACTIVITY`, `WORKOUT`, `ROUTE` and (future) `TRAINING PLAN` are
separate domains. A completed ride automatically produces a training activity;
rides are never widened to hold training columns.

## AI Coach (interpretation layer) — NOT in Phase 6, design retained

The AI layer interprets structured data; it never computes it. It reads
ride/training history, load, distance/elevation, HR/cadence/power, recovery
signals and goals, and returns a recommendation + explanation + confidence +
provenance labels — e.g. for a 200 km gravel event 12 weeks out at 180 km/week:
"extend long ride +10 %/week, keep Z2, add climbing, force recovery after high
load." If data is missing it says `estimate`; it never fabricates. It runs behind
a worker queue with eval tests (groundedness, no hallucinated metrics) and its
output is advisory.

**Status: deferred to a later phase.** ADR-05 already fixes the split — a
deterministic rules engine decides, an LLM explains. Phase 6 builds the base that
decision-making and explanation both stand on. Adding AI now would mean
un-versioned, un-testable advice shipped on top of metrics that are still moving.

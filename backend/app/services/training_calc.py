"""Phase 6 training engine: pure, deterministic, versioned calculations.

Rules for this module (ADR-10):

* **No I/O, no clock, no randomness.** Every function is a pure transform of its
  arguments, so the same input always produces the same output and results can be
  asserted in unit tests without a database.
* **Every formula carries a version string.** The version is persisted next to
  the number, so any stored metric can be traced back to the exact math that
  produced it. Changing a formula means adding a version, never editing one.
* **Missing inputs are ``None``, never ``0``.** A ride without power meters has
  no intensity factor, and that absence is reported explicitly together with a
  basis label (``measured`` / ``derived`` / ``estimated`` / ``unavailable``).
* **Canonical units.** Watts, bpm, seconds, meters, km/h internally converted at
  the edges; the API layer converts km/mi for display (docs/03 §43).

This module deliberately knows nothing about SQLAlchemy, FastAPI or rides. The
ride/GPS truth stays in :mod:`app.services.gps_engine`; this module only turns
sensor samples and profiles into training metrics.
"""

import math
from collections import deque
from dataclasses import dataclass
from datetime import date, timedelta
from itertools import pairwise
from typing import Any, Final

# ---------------------------------------------------------------------------
# Analysis constants
# ---------------------------------------------------------------------------

#: Samples further apart than this start a new analysis segment (pause, tunnel,
#: dropped Bluetooth link). NP windows are never bridged across a segment.
MAX_SAMPLE_GAP_S: Final = 10.0
#: Normalized power rolling window.
NP_WINDOW_S: Final = 30.0
#: Sanity floor so a misconfigured profile cannot yield absurd ratios.
MIN_PLAUSIBLE_FTP_W: Final = 50.0
MAX_PLAUSIBLE_FTP_W: Final = 1500.0
MIN_PLAUSIBLE_HR: Final = 25.0
MAX_PLAUSIBLE_HR: Final = 250.0
#: An analysis needs at least this much sensor coverage to be reported at all.
MIN_ANALYZED_SECONDS: Final = 60.0

EPS: Final = 1e-9

#: TRIMP-style weight per heart rate zone (1..5).
HR_ZONE_WEIGHTS: Final[dict[int, int]] = {1: 1, 2: 2, 3: 3, 4: 4, 5: 5}

# ---------------------------------------------------------------------------
# Version registry — the single source of truth for calculation versions.
# ---------------------------------------------------------------------------

POWER_ZONES_VERSION: Final = "coggan_7zone_v1"
HR_MAX_ZONES_VERSION: Final = "hremax_5zone_v1"
HRR_ZONES_VERSION: Final = "karvonen_5zone_v1"
NP_VERSION: Final = "np_30s_v1"
IF_VERSION: Final = "if_ftp_v1"
POWER_LOAD_VERSION: Final = "tss_style_v1"
HR_LOAD_VERSION: Final = "trimp_5zone_v1"
FTP_20MIN_VERSION: Final = "ftp_20min_095_v1"
FTP_RAMP_VERSION: Final = "ftp_ramp_095_v1"
EWMA_VERSION: Final = "ewma_42_7_v1"
RECOVERY_VERSION: Final = "load_delta_7d_v1"
ACTIVITY_ANALYSIS_VERSION: Final = "activity_analysis_v1"

#: Seeded into ``training_calculation_versions`` and asserted by a drift test.
CALCULATION_VERSIONS: Final[dict[str, dict[str, Any]]] = {
    POWER_ZONES_VERSION: {
        "kind": "zones",
        "title": "Power zones (Coggan, 7 zones)",
        "summary": "Zone boundaries as a fraction of FTP.",
        "params": {
            "boundaries": [0.55, 0.75, 0.90, 1.05, 1.20, 1.50],
            "reference": "ftp_w",
        },
    },
    HR_MAX_ZONES_VERSION: {
        "kind": "zones",
        "title": "Heart rate zones (% of HRmax, 5 zones)",
        "summary": "Zone boundaries as a fraction of maximum heart rate.",
        "params": {"boundaries": [0.60, 0.70, 0.80, 0.90], "reference": "max_hr_bpm"},
    },
    HRR_ZONES_VERSION: {
        "kind": "zones",
        "title": "Heart rate zones (Karoven/HRR, 5 zones)",
        "summary": "Zone boundaries as a fraction of heart rate reserve (max - resting).",
        "params": {
            "boundaries": [0.60, 0.70, 0.80, 0.90],
            "reference": "heart_rate_reserve_bpm",
        },
    },
    NP_VERSION: {
        "kind": "metric",
        "title": "Normalized power (30 s rolling)",
        "summary": "Fourth-power mean of 30 s rolling average power, time-weighted.",
        "params": {
            "window_s": NP_WINDOW_S,
            "max_sample_gap_s": MAX_SAMPLE_GAP_S,
            "requires_full_window": True,
        },
    },
    IF_VERSION: {
        "kind": "metric",
        "title": "Intensity factor",
        "summary": "normalized_power / effective_ftp.",
        "params": {"requires": ["normalized_power", "effective_ftp"]},
    },
    POWER_LOAD_VERSION: {
        "kind": "load",
        "title": "Power training load (TSS-style)",
        "summary": (
            "duration_hours * IF^2 * 100. CycleCoach-specific formula; not "
            "TrainingPeaks TSS and not comparable with it."
        ),
        "params": {"formula": "power_seconds/3600 * intensity_factor^2 * 100"},
    },
    HR_LOAD_VERSION: {
        "kind": "load",
        "title": "Heart rate training load (5-zone TRIMP-style)",
        "summary": "Minutes in zone x zone weight (1..5). Never summed with power load.",
        "params": {"weights": [1, 2, 3, 4, 5]},
    },
    FTP_20MIN_VERSION: {
        "kind": "ftp",
        "title": "FTP from a 20 minute test",
        "summary": "0.95 x best 20 minute average power (fully covered window).",
        "params": {"factor": 0.95, "window_s": 1200, "min_window_s": 600},
    },
    FTP_RAMP_VERSION: {
        "kind": "ftp",
        "title": "FTP from a ramp test (approximation)",
        "summary": (
            "0.95 x best 10 minute average power. Explicitly an approximation for a "
            "3x10 min ramp protocol; a 20 minute test is preferred."
        ),
        "params": {"factor": 0.95, "window_s": 600, "min_window_s": 480, "approximation": True},
    },
    EWMA_VERSION: {
        "kind": "load",
        "title": "Chronic/acute load (42/7 day EWMA)",
        "summary": "Exponentially weighted load. Fitness proxy, not a medical measure.",
        "params": {"ctl_days": 42, "atl_days": 7, "tsb": "ctl_prev - atl_prev"},
    },
    RECOVERY_VERSION: {
        "kind": "signal",
        "title": "Load-change recovery signal",
        "summary": "7 day load compared with the preceding 7 days; +/-20% band.",
        "params": {"window_days": 7, "increase_ratio": 1.20, "decrease_ratio": 0.80},
    },
    ACTIVITY_ANALYSIS_VERSION: {
        "kind": "manifest",
        "title": "Activity analysis manifest",
        "summary": "Bundle of the versions used to produce one activity's metrics.",
        "params": {
            "power_zones": POWER_ZONES_VERSION,
            "hr_max_zones": HR_MAX_ZONES_VERSION,
            "hrr_zones": HRR_ZONES_VERSION,
            "normalized_power": NP_VERSION,
            "intensity_factor": IF_VERSION,
            "power_load": POWER_LOAD_VERSION,
            "hr_load": HR_LOAD_VERSION,
        },
    },
}


# ---------------------------------------------------------------------------
# Zone models
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ZoneSpec:
    number: int
    name: str
    min_frac: float
    max_frac: float | None  # None = open-ended top zone


#: Coggan 7-zone power model. `max_frac` is the exclusive upper bound.
POWER_ZONES: Final[tuple[ZoneSpec, ...]] = (
    ZoneSpec(1, "active_recovery", 0.00, 0.55),
    ZoneSpec(2, "endurance", 0.55, 0.75),
    ZoneSpec(3, "tempo", 0.75, 0.90),
    ZoneSpec(4, "threshold", 0.90, 1.05),
    ZoneSpec(5, "vo2max", 1.05, 1.20),
    ZoneSpec(6, "anaerobic", 1.20, 1.50),
    ZoneSpec(7, "neuromuscular", 1.50, None),
)

#: Five-zone %HRmax model.
HR_MAX_ZONES: Final[tuple[ZoneSpec, ...]] = (
    ZoneSpec(1, "active_recovery", 0.00, 0.60),
    ZoneSpec(2, "endurance", 0.60, 0.70),
    ZoneSpec(3, "tempo", 0.70, 0.80),
    ZoneSpec(4, "threshold", 0.80, 0.90),
    ZoneSpec(5, "vo2max", 0.90, None),
)

#: Five-zone heart-rate-reserve (Karoven) model.
HRR_ZONES: Final[tuple[ZoneSpec, ...]] = (
    ZoneSpec(1, "active_recovery", 0.00, 0.60),
    ZoneSpec(2, "endurance", 0.60, 0.70),
    ZoneSpec(3, "tempo", 0.70, 0.80),
    ZoneSpec(4, "threshold", 0.80, 0.90),
    ZoneSpec(5, "vo2max", 0.90, None),
)


@dataclass(frozen=True)
class SensorSample:
    """One instant observation. ``t`` is epoch seconds (UTC).

    Every sensor field is optional and independent: a phone-only recording has
    no power, and a missing field must not invalidate the rest of the sample.
    """

    t: float
    power_w: float | None = None
    hr_bpm: float | None = None
    cadence_rpm: float | None = None


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def _round(value: float | None, digits: int) -> float | None:
    if value is None or not math.isfinite(value):
        return None
    return round(value + 0.0, digits)


def valid_ftp(value: float | None) -> float | None:
    """FTP must exist and be physiologically plausible to be used as a divisor."""
    if value is None or not math.isfinite(value):
        return None
    if not (MIN_PLAUSIBLE_FTP_W <= value <= MAX_PLAUSIBLE_FTP_W):
        return None
    return value


def valid_hr(value: float | None) -> float | None:
    if value is None or not math.isfinite(value):
        return None
    if not (MIN_PLAUSIBLE_HR <= value <= MAX_PLAUSIBLE_HR):
        return None
    return value


def _zone_for_fraction(fraction: float, zones: tuple[ZoneSpec, ...]) -> int:
    # Zones are closed intervals: Coggan puts 90% of FTP in tempo and 105% in
    # threshold, so the upper bound belongs to the lower zone.
    for zone in zones:
        if zone.max_frac is None or fraction <= zone.max_frac + EPS:
            return zone.number
    return zones[-1].number


def power_zone_number(power_w: float, ftp_w: float) -> int:
    """Zone number for a power sample. Caller must have validated ``ftp_w``."""
    return _zone_for_fraction(power_w / ftp_w, POWER_ZONES)


def hr_zone_number(
    hr_bpm: float, *, max_hr_bpm: float | None, resting_hr_bpm: float | None, model: str
) -> int:
    """Zone number for a heart rate sample under the selected model."""
    if model == "hrr":
        reserve = (max_hr_bpm or 0.0) - (resting_hr_bpm or 0.0)
        if reserve <= 0:
            return 0
        fraction = (hr_bpm - (resting_hr_bpm or 0.0)) / reserve
        return _zone_for_fraction(fraction, HRR_ZONES)
    if not max_hr_bpm or max_hr_bpm <= 0:
        return 0
    return _zone_for_fraction(hr_bpm / max_hr_bpm, HR_MAX_ZONES)


def select_hr_model(
    max_hr_bpm: float | None, resting_hr_bpm: float | None, override: str | None = None
) -> str | None:
    """``"hr_max"`` / ``"hrr"`` / ``None`` when no model can be applied.

    Karoven needs both thresholds; %HRmax needs the maximum. With neither, heart
    rate zones are unavailable rather than guessed.
    """
    if override in ("hr_max", "hrr"):
        if override == "hr_max" and valid_hr(max_hr_bpm):
            return "hr_max"
        if override == "hrr" and valid_hr(max_hr_bpm) and valid_hr(resting_hr_bpm):
            return "hrr"
        return None
    if valid_hr(max_hr_bpm) and valid_hr(resting_hr_bpm):
        return "hrr"
    if valid_hr(max_hr_bpm):
        return "hr_max"
    return None


# ---------------------------------------------------------------------------
# Segmentation
# ---------------------------------------------------------------------------


def _usable(samples: list[SensorSample]) -> list[SensorSample]:
    """Sort by time, drop non-finite timestamps, cap implausible sensor values."""
    out: list[SensorSample] = []
    for s in samples:
        if not math.isfinite(s.t):
            continue
        power = s.power_w if s.power_w is not None and math.isfinite(s.power_w) else None
        hr = s.hr_bpm if s.hr_bpm is not None and math.isfinite(s.hr_bpm) else None
        cad = s.cadence_rpm if s.cadence_rpm is not None and math.isfinite(s.cadence_rpm) else None
        out.append(SensorSample(t=s.t, power_w=power, hr_bpm=hr, cadence_rpm=cad))
    out.sort(key=lambda s: s.t)
    return out


def segment_samples(
    samples: list[SensorSample], max_gap_s: float = MAX_SAMPLE_GAP_S
) -> list[list[SensorSample]]:
    """Split samples into contiguous runs, breaking on gaps longer than ``max_gap_s``.

    A gap means the rider paused, went through a tunnel, or the sensor link
    dropped. Power averages are never interpolated across it.
    """
    ordered = _usable(samples)
    if not ordered:
        return []
    segments: list[list[SensorSample]] = [[ordered[0]]]
    for previous, current in pairwise(ordered):
        if current.t - previous.t <= max_gap_s + EPS:
            segments[-1].append(current)
        else:
            segments.append([current])
    return segments


# ---------------------------------------------------------------------------
# Normalized power
# ---------------------------------------------------------------------------


def normalized_power(segments: list[list[SensorSample]]) -> tuple[float | None, float]:
    """Fourth-power mean of 30 s rolling average power.

    Returns ``(np, np_seconds)``. An interval only contributes once the trailing
    30 second window behind it is *fully* covered by samples, so the warm-up of
    each segment cannot inflate the result: for a ride starting at ``t0`` the
    first contributing interval ends at ``t0 + 30``. ``np`` is ``None`` when no
    full window exists (e.g. a 20 s sample burst), never 0.
    """
    sum_p4 = 0.0
    np_seconds = 0.0
    for segment in segments:
        window: deque[tuple[float, float]] = deque()
        for index, current in enumerate(segment):
            if current.power_w is not None:
                window.append((current.t, current.power_w))
            cutoff = current.t - NP_WINDOW_S
            while window and window[0][0] < cutoff - EPS:
                window.popleft()
            if index < 1 or not window or current.power_w is None:
                continue
            dt = current.t - segment[index - 1].t
            if dt <= EPS or dt > MAX_SAMPLE_GAP_S + EPS:
                continue
            if window[0][0] > cutoff + EPS:  # window not yet full (warm-up)
                continue
            mean = math.fsum(p for _, p in window) / len(window)
            sum_p4 += (mean**4) * dt
            np_seconds += dt
    if np_seconds <= 0:
        return None, 0.0
    return (sum_p4 / np_seconds) ** 0.25, np_seconds


# ---------------------------------------------------------------------------
# Activity analysis
# ---------------------------------------------------------------------------


def analyze_activity(
    samples: list[SensorSample],
    *,
    ftp_w: float | None = None,
    max_hr_bpm: float | None = None,
    resting_hr_bpm: float | None = None,
    hr_model_override: str | None = None,
) -> dict[str, Any]:
    """Deterministically derive every activity metric from sensor samples.

    ``ftp_w`` should be the *effective* FTP resolved from the profile/records; the
    caller labels the result's basis. Metrics that cannot be derived from the
    supplied data are returned as ``None``.
    """
    ftp = valid_ftp(ftp_w)
    max_hr = valid_hr(max_hr_bpm)
    resting_hr = valid_hr(resting_hr_bpm)
    hr_model = select_hr_model(max_hr, resting_hr, hr_model_override)

    segments = segment_samples(samples)
    analyzed_seconds = 0.0
    power_seconds = 0.0
    hr_seconds = 0.0
    cadence_seconds = 0.0
    power_weighted = 0.0
    hr_weighted = 0.0
    cadence_weighted = 0.0
    max_power = 0.0
    max_hr_observed = 0.0
    power_zone_seconds = {z.number: 0.0 for z in POWER_ZONES}
    hr_zone_seconds = {z.number: 0.0 for z in HR_MAX_ZONES}
    trimp = 0.0
    segment_count = 0

    for segment in segments:
        if len(segment) < 2:
            continue
        segment_count += 1
        for previous, current in pairwise(segment):
            dt = current.t - previous.t
            if dt <= EPS or dt > MAX_SAMPLE_GAP_S + EPS:
                continue
            analyzed_seconds += dt
            if current.power_w is not None and current.power_w >= 0:
                power_seconds += dt
                power_weighted += current.power_w * dt
                max_power = max(max_power, current.power_w)
                if ftp is not None:
                    number = power_zone_number(current.power_w, ftp)
                    power_zone_seconds[number] += dt
            if current.hr_bpm is not None and current.hr_bpm > 0:
                hr_seconds += dt
                hr_weighted += current.hr_bpm * dt
                max_hr_observed = max(max_hr_observed, current.hr_bpm)
                if hr_model is not None:
                    number = hr_zone_number(
                        current.hr_bpm,
                        max_hr_bpm=max_hr,
                        resting_hr_bpm=resting_hr,
                        model=hr_model,
                    )
                    if number:
                        hr_zone_seconds[number] += dt
                        trimp += HR_ZONE_WEIGHTS[number] * (dt / 60.0)
            if current.cadence_rpm is not None and current.cadence_rpm >= 0:
                cadence_seconds += dt
                cadence_weighted += current.cadence_rpm * dt

    enough = analyzed_seconds >= MIN_ANALYZED_SECONDS
    np_value, np_seconds = normalized_power(segments)

    average_power = power_weighted / power_seconds if power_seconds > 0 else None
    average_hr = hr_weighted / hr_seconds if hr_seconds > 0 else None
    average_cadence = cadence_weighted / cadence_seconds if cadence_seconds > 0 else None

    intensity_factor: float | None = None
    power_load: float | None = None
    if np_value is not None and ftp is not None and ftp > 0:
        intensity_factor = np_value / ftp
        # Load integrates the *power* window, not the whole sensor window.
        power_load = (power_seconds / 3600.0) * (intensity_factor**2) * 100.0

    hr_load = trimp if enough and hr_seconds > 0 and hr_model is not None else None

    return {
        "analysis_version": ACTIVITY_ANALYSIS_VERSION,
        "versions": {
            "analysis": ACTIVITY_ANALYSIS_VERSION,
            "power_zones": POWER_ZONES_VERSION,
            "hr_zones": HRR_ZONES_VERSION if hr_model == "hrr" else HR_MAX_ZONES_VERSION,
            "normalized_power": NP_VERSION,
            "intensity_factor": IF_VERSION,
            "power_load": POWER_LOAD_VERSION,
            "hr_load": HR_LOAD_VERSION,
        },
        "sufficient_data": enough,
        "segment_count": segment_count,
        "sample_count": len(samples),
        "analyzed_seconds": _round(analyzed_seconds, 1),
        "power_seconds": _round(power_seconds, 1),
        "hr_seconds": _round(hr_seconds, 1),
        "cadence_seconds": _round(cadence_seconds, 1),
        "np_seconds": _round(np_seconds, 1),
        "average_power_w": _round(average_power, 1),
        "max_power_w": _round(max_power if power_seconds > 0 else None, 1),
        "normalized_power_w": _round(np_value, 1),
        "intensity_factor": _round(intensity_factor, 4),
        "power_load": _round(power_load, 1),
        "average_hr_bpm": _round(average_hr, 1),
        "max_hr_bpm": _round(max_hr_observed if hr_seconds > 0 else None, 1),
        "average_cadence_rpm": _round(average_cadence, 1),
        "hr_load": _round(hr_load, 1),
        "hr_zone_model": hr_model,
        "effective_ftp_w": _round(ftp, 1),
        "power_zone_seconds": {n: _round(v, 1) or 0.0 for n, v in power_zone_seconds.items()},
        "hr_zone_seconds": {n: _round(v, 1) or 0.0 for n, v in hr_zone_seconds.items()},
        "has_power": power_seconds > 0,
        "has_heart_rate": hr_seconds > 0,
        "has_cadence": cadence_seconds > 0,
    }


# ---------------------------------------------------------------------------
# FTP resolution and test protocols
# ---------------------------------------------------------------------------

#: Higher wins when two FTP records are equally recent. ``estimated`` is always
#: the lowest rank: it never outranks a confirmed value.
FTP_SOURCE_RANK: Final[dict[str, int]] = {
    "manual": 0,
    "imported": 1,
    "test_ramp": 2,
    "test_20min": 3,
    "estimated": -1,
}

#: Sources that a rider has explicitly confirmed (test, manual entry, import).
CONFIRMED_FTP_SOURCES: Final[frozenset[str]] = frozenset(
    {"manual", "imported", "test_ramp", "test_20min"}
)


@dataclass(frozen=True)
class FtpResolution:
    ftp_w: float | None
    source: str | None
    record_id: Any | None
    effective_at: date | None
    confirmed: bool


def resolve_effective_ftp(records: list[dict[str, Any]]) -> FtpResolution:
    """Pick the FTP a calculation must use, deterministically.

    ``records`` are append-only ``ftp_records`` rows as dicts with
    ``id``/``source``/``value_w``/``effective_at``. Rules:

    1. records with an unusable value are ignored;
    2. a confirmed source (manual/test/import) always beats ``estimated``;
    3. within a group the most recent ``effective_at`` wins;
    4. ties break on source rank, then on ``created_at`` so that re-entering the
       same value twice resolves to the newest row, then on ``id`` so the result
       is total even if ``created_at`` is missing.

    Existing stored metrics are never rewritten when a new record arrives — they
    keep the FTP they were computed with (see ``training_activities``).
    """
    usable: list[dict[str, Any]] = []
    for record in records:
        value = valid_ftp(float(record["value_w"]))
        if value is None:
            continue
        confirmed = str(record["source"]) in CONFIRMED_FTP_SOURCES
        usable.append({**record, "value_w": value, "confirmed": confirmed})
    if not usable:
        return FtpResolution(None, None, None, None, False)

    def sort_key(record: dict[str, Any]) -> tuple[Any, ...]:
        # Recency first: a rider who lowered FTP by hand last month means it,
        # regardless of the fact that an older record came from a test protocol.
        return (
            1 if record["confirmed"] else 0,
            record["effective_at"],
            FTP_SOURCE_RANK.get(str(record["source"]), -2),
            # created_at before id: ids are UUIDs, so string order is not
            # insertion order and two same-day edits would resolve randomly.
            str(record.get("created_at") or ""),
            str(record["id"]),
        )

    best = max(usable, key=sort_key)
    return FtpResolution(
        ftp_w=float(best["value_w"]),
        source=str(best["source"]),
        record_id=best["id"],
        effective_at=best["effective_at"],
        confirmed=bool(best["confirmed"]),
    )


def _best_window_average(
    segments: list[list[SensorSample]], window_s: float, min_span_s: float
) -> tuple[float | None, float, float | None, float | None]:
    """Highest time-weighted average power over a fully covered window."""
    best_avg: float | None = None
    best_start: float | None = None
    best_end: float | None = None
    for segment in segments:
        window: deque[tuple[float, float]] = deque()
        for index, current in enumerate(segment):
            if current.power_w is not None:
                window.append((current.t, current.power_w))
            cutoff = current.t - window_s
            while window and window[0][0] < cutoff - EPS:
                window.popleft()
            if index < 1 or not window:
                continue
            if window[0][0] > cutoff + EPS:  # window not yet full (warm-up)
                continue
            span = current.t - window[0][0]
            if span < min_span_s - EPS:
                continue
            average = math.fsum(p for _, p in window) / len(window)
            if best_avg is None or average > best_avg:
                best_avg = average
                best_start = window[0][0]
                best_end = current.t
    return best_avg, window_s, best_start, best_end


def ftp_from_20min_test(
    samples: list[SensorSample],
) -> tuple[float | None, dict[str, Any]]:
    """0.95 x best 20 minute average power. Not an estimate of a protocol that
    was not actually performed — requires 10+ minutes of usable power data."""
    segments = segment_samples(samples)
    best, _, start, end = _best_window_average(segments, 1200.0, 600.0)
    if best is None:
        return None, {"version": FTP_20MIN_VERSION, "status": "insufficient_data"}
    return 0.95 * best, {
        "version": FTP_20MIN_VERSION,
        "status": "ok",
        "best_window_average_w": _round(best, 1),
        "factor": 0.95,
        "window_start_t": start,
        "window_end_t": end,
        "approximation": False,
    }


def ftp_from_ramp_test(
    samples: list[SensorSample],
) -> tuple[float | None, dict[str, Any]]:
    """0.95 x best 10 minute average power — **approximation only**.

    Intended for a 3 x 10 min ramp protocol. A 20 minute test is more reliable
    and should be preferred; the result is stored with
    ``approximation: true`` so no UI or report can present it as measured.
    """
    segments = segment_samples(samples)
    best, _, start, end = _best_window_average(segments, 600.0, 480.0)
    if best is None:
        return None, {"version": FTP_RAMP_VERSION, "status": "insufficient_data"}
    return 0.95 * best, {
        "version": FTP_RAMP_VERSION,
        "status": "ok",
        "best_window_average_w": _round(best, 1),
        "factor": 0.95,
        "window_start_t": start,
        "window_end_t": end,
        "approximation": True,
    }


# ---------------------------------------------------------------------------
# Load trend (CTL / ATL / TSB) and recovery signals
# ---------------------------------------------------------------------------

CTL_DAYS: Final = 42
ATL_DAYS: Final = 7
#: Weekly load change beyond +/- this ratio raises a load-change signal.
LOAD_CHANGE_RATIO: Final = 0.20
#: Days of contiguous history before chronic load is worth reporting.
MIN_TREND_DAYS: Final = 7
STALE_AFTER_DAYS: Final = 14


def load_trend(
    daily_loads: list[tuple[date, float]], start: date, end: date
) -> list[dict[str, Any]]:
    """42/7 day exponentially weighted load per local day.

    ``daily_loads`` are (local_date, power_load) pairs; missing days count as 0.
    ``start``/``end`` bound the returned series (both inclusive).
    """
    if end < start:
        return []
    loads = {d: float(v) for d, v in daily_loads}
    rows: list[dict[str, Any]] = []
    ctl = 0.0
    atl = 0.0
    day = start
    while day <= end:
        load = loads.get(day, 0.0)
        previous_ctl, previous_atl = ctl, atl
        # Each constant relaxes toward today's load from its own previous value.
        ctl = previous_ctl + (load - previous_ctl) / CTL_DAYS
        atl = previous_atl + (load - previous_atl) / ATL_DAYS
        rows.append(
            {
                "date": day,
                "load": _round(load, 1) or 0.0,
                "ctl": _round(ctl, 2) or 0.0,
                "atl": _round(atl, 2) or 0.0,
                # Form over fitness: yesterday's chronic minus yesterday's acute.
                "tsb": _round(previous_ctl - previous_atl, 2) or 0.0,
            }
        )
        day += timedelta(days=1)
    return rows


def recovery_signals(daily_loads: list[tuple[date, float]], today: date) -> dict[str, Any]:
    """Evidence-based load-change signals. Never a medical or readiness claim.

    Codes are stable identifiers; the client localizes them:
    ``insufficient_data``, ``load_increased``, ``load_decreased``,
    ``load_stable``, ``load_stale``.
    """
    loads = {d: float(v) for d, v in daily_loads}
    active_days = [d for d, v in loads.items() if v > 0]
    evidence: dict[str, Any] = {"window_days": 7}
    if len(active_days) < MIN_TREND_DAYS:
        return {
            "version": RECOVERY_VERSION,
            "status": "unavailable",
            "code": "insufficient_data",
            "signals": ["insufficient_data"],
            "evidence": {**evidence, "active_days": len(active_days), "required_days": 7},
        }

    recent = math.fsum(loads.get(today - timedelta(days=offset), 0.0) for offset in range(7))
    previous = math.fsum(loads.get(today - timedelta(days=7 + offset), 0.0) for offset in range(7))
    last_load = max(active_days)
    days_since_load = (today - last_load).days
    signals: list[str] = []
    if previous > 0:
        ratio = recent / previous
        if ratio >= 1.0 + LOAD_CHANGE_RATIO:
            signals.append("load_increased")
        elif ratio <= 1.0 - LOAD_CHANGE_RATIO:
            signals.append("load_decreased")
        else:
            signals.append("load_stable")
    else:
        signals.append("load_stable")
    if days_since_load > STALE_AFTER_DAYS:
        signals.append("load_stale")

    if "load_increased" in signals:
        code = "load_increased"
    elif "load_decreased" in signals:
        code = "load_decreased"
    elif "load_stale" in signals:
        code = "load_stale"
    else:
        code = "load_stable"
    return {
        "version": RECOVERY_VERSION,
        "status": "ok",
        "code": code,
        "signals": signals,
        "evidence": {
            **evidence,
            "recent_load": _round(recent, 1) or 0.0,
            "previous_load": _round(previous, 1) or 0.0,
            "change_ratio": _round(recent / previous if previous > 0 else None, 3),
            "days_since_load": days_since_load,
        },
    }


# ---------------------------------------------------------------------------
# Prescriptions (deterministic, bounded)
# ---------------------------------------------------------------------------

#: Hard cap on a single-session load jump. The rules engine must never push a
#: rider harder than this in one step.
MAX_SESSION_LOAD_DELTA: Final = 0.15


def suggest_intensity_target(
    current_load: float | None,
    *,
    weekly_load: float | None = None,
    max_delta: float = MAX_SESSION_LOAD_DELTA,
) -> dict[str, Any]:
    """Bounded next-session load target.

    Deterministic and explainable: at most ``max_delta`` above the current
    session load, never below zero, and ``None`` when there is no baseline to
    reason about. No AI, no guessing from missing data.
    """
    if current_load is None or current_load < 0:
        return {
            "version": ACTIVITY_ANALYSIS_VERSION,
            "status": "unavailable",
            "target_load": None,
            "reason": "no_baseline",
            "evidence": {"current_load": current_load},
        }
    target = current_load * (1.0 + max_delta)
    reason = "bounded_increase"
    if weekly_load is not None and weekly_load > 0 and current_load * 7 > weekly_load * 0.30:
        target = current_load
        reason = "weekly_cap"
    return {
        "version": ACTIVITY_ANALYSIS_VERSION,
        "status": "ok",
        "target_load": _round(target, 1),
        "reason": reason,
        "evidence": {
            "current_load": _round(current_load, 1),
            "weekly_load": _round(weekly_load, 1),
            "max_delta": max_delta,
        },
    }

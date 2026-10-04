"""Training engine unit tests — pure calculations, no database, no clock.

Every assertion here is an exact expected value derived from the documented
formula, so a change in the maths fails loudly (ADR-10).
"""

from datetime import date, timedelta
from itertools import pairwise

import pytest

from app.services import training_calc as tc
from app.services.training_calc import FtpResolution, SensorSample

T0 = 1_700_000_000.0


def ramp(n: int, *, start: float = T0, step: float = 1.0, power, hr=None, cad=None):
    """One sample per ``step`` seconds with a per-index value callable."""
    return [
        SensorSample(
            t=start + i * step,
            power_w=power(i) if callable(power) else power,
            hr_bpm=hr(i) if callable(hr) else hr,
            cadence_rpm=cad(i) if callable(cad) else cad,
        )
        for i in range(n)
    ]


def constant(
    seconds: float,
    *,
    power=None,
    hr=None,
    cad=None,
    step: float = 1.0,
    start: float = T0,
):
    """A flat ride of ``seconds`` sampled every ``step`` seconds."""
    n = int(seconds / step) + 1
    return ramp(n, start=start, step=step, power=power, hr=hr, cad=cad)


# ---------------------------------------------------------------------------
# Version registry
# ---------------------------------------------------------------------------


def test_every_registered_version_is_documented():
    for entry in tc.CALCULATION_VERSIONS.values():
        assert entry["kind"] in {"zones", "metric", "load", "ftp", "signal", "manifest"}
        assert entry["title"].strip()
        assert entry["summary"].strip()
        assert isinstance(entry["params"], dict)


def test_manifest_lists_only_registered_versions():
    manifest = tc.CALCULATION_VERSIONS[tc.ACTIVITY_ANALYSIS_VERSION]["params"]
    for referenced in manifest.values():
        assert referenced in tc.CALCULATION_VERSIONS


def test_zone_models_match_their_versioned_boundaries():
    assert [z.max_frac for z in tc.POWER_ZONES] == [0.55, 0.75, 0.90, 1.05, 1.20, 1.50, None]
    assert [z.max_frac for z in tc.HR_MAX_ZONES] == [0.60, 0.70, 0.80, 0.90, None]
    assert [z.max_frac for z in tc.HRR_ZONES] == [0.60, 0.70, 0.80, 0.90, None]
    assert tc.CALCULATION_VERSIONS[tc.POWER_ZONES_VERSION]["params"]["boundaries"] == [
        0.55,
        0.75,
        0.90,
        1.05,
        1.20,
        1.50,
    ]


# ---------------------------------------------------------------------------
# Segmentation
# ---------------------------------------------------------------------------


def test_gap_splits_segments_and_is_never_bridged():
    samples = constant(20) + constant(20, start=T0 + 600)
    segments = tc.segment_samples(samples)
    assert len(segments) == 2


def test_unsorted_input_is_ordered_deterministically():
    forward = constant(30)
    backward = list(reversed(forward))
    assert tc.analyze_activity(forward, ftp_w=250) == tc.analyze_activity(backward, ftp_w=250)


def test_single_sample_yields_no_time_coverage():
    result = tc.analyze_activity([SensorSample(t=T0, power_w=200)], ftp_w=250)
    assert result["analyzed_seconds"] == 0.0
    assert result["normalized_power_w"] is None
    assert result["intensity_factor"] is None
    assert result["power_load"] is None


# ---------------------------------------------------------------------------
# Normalized power
# ---------------------------------------------------------------------------


def test_constant_power_normalized_power_equals_constant():
    result = tc.analyze_activity(constant(600, power=200), ftp_w=200)
    assert result["normalized_power_w"] == pytest.approx(200.0, abs=0.1)
    assert result["average_power_w"] == pytest.approx(200.0, abs=0.1)
    assert result["intensity_factor"] == pytest.approx(1.0, abs=0.001)


def test_normalized_power_exceeds_average_for_variable_effort():
    # 1 h alternating 100 W / 300 W: NP is strictly above the 200 W average.
    samples = ramp(3601, step=1.0, power=lambda i: 300.0 if i % 2 else 100.0)
    result = tc.analyze_activity(samples, ftp_w=200)
    assert result["average_power_w"] == pytest.approx(200.0, abs=0.5)
    assert result["normalized_power_w"] > 200.0
    assert result["intensity_factor"] > 1.0


def test_warmup_is_excluded_from_normalized_power():
    # 20 s of power can never fill a 30 s window -> NP unavailable, not 0.
    result = tc.analyze_activity(constant(20, power=250), ftp_w=250)
    assert result["np_seconds"] == 0.0
    assert result["normalized_power_w"] is None


def naive_normalized_power(samples):
    """Deliberately simple reference: no deque, no segmentation.

    For every interval, average the samples inside the trailing 30 s window,
    weight it by the interval length, and take the fourth root of the mean of
    the fourth powers. Used to prove the optimised implementation agrees.
    """
    total_p4 = 0.0
    total_s = 0.0
    for index in range(1, len(samples)):
        current, previous = samples[index], samples[index - 1]
        if current.power_w is None:
            continue
        window = [
            s.power_w
            for s in samples[: index + 1]
            if s.power_w is not None and s.t >= current.t - tc.NP_WINDOW_S
        ]
        if not window or current.t - min(s.t for s in samples[: index + 1]) < tc.NP_WINDOW_S:
            continue
        dt = current.t - previous.t
        total_p4 += (sum(window) / len(window)) ** 4 * dt
        total_s += dt
    return (total_p4 / total_s) ** 0.25 if total_s else None


@pytest.mark.parametrize(
    "profile",
    [
        lambda i: 200.0,
        lambda i: 100.0 if i <= 60 else 200.0,
        lambda i: 150.0 + 100.0 * (i % 37),
        lambda i: 300.0 if i % 3 == 0 else 80.0,
    ],
)
def test_normalized_power_matches_a_naive_reference(profile):
    samples = ramp(400, step=1.0, power=profile)
    value, _ = tc.normalized_power(tc.segment_samples(samples))
    assert value == pytest.approx(naive_normalized_power(samples), rel=1e-9)


def test_np_seconds_exclude_the_warmup_of_every_segment():
    # A 120 s ride: the trailing 30 s window is first complete at t=30, so
    # intervals 30..120 (91 s) contribute and the 29 s warm-up does not.
    value, seconds = tc.normalized_power(tc.segment_samples(constant(120, power=180)))
    assert seconds == pytest.approx(91.0)
    assert value == pytest.approx(180.0)


def test_warmup_is_excluded_after_every_resume():
    # 200 s split by a 10 min pause: each segment pays its own warm-up.
    segments = tc.segment_samples(
        constant(200, power=180) + constant(200, power=180, start=T0 + 600)
    )
    _, seconds = tc.normalized_power(segments)
    assert seconds == pytest.approx(2 * 171.0)


# ---------------------------------------------------------------------------
# Intensity factor and power load
# ---------------------------------------------------------------------------


def test_tss_style_load_matches_documented_formula():
    # 1 h at FTP (IF 1.0) -> 100 points.
    result = tc.analyze_activity(constant(3600, power=250), ftp_w=250)
    assert result["power_load"] == pytest.approx(100.0, rel=0.01)


def test_power_load_uses_power_window_not_whole_sensor_window():
    # 30 min of power, then 30 min of heart rate only: load covers 0.5 h.
    power_half = constant(1800, power=250)
    hr_half = ramp(1801, start=T0 + 1800, step=1.0, power=None, hr=140)
    result = tc.analyze_activity(power_half + hr_half, ftp_w=250)
    assert result["power_seconds"] == pytest.approx(1800.0, abs=2.0)
    assert result["analyzed_seconds"] == pytest.approx(3600.0, abs=2.0)
    assert result["power_load"] == pytest.approx(50.0, rel=0.02)


def test_intensity_factor_requires_ftp():
    samples = constant(600, power=250)
    assert tc.analyze_activity(samples, ftp_w=None)["intensity_factor"] is None
    assert tc.analyze_activity(samples, ftp_w=None)["power_load"] is None
    # Implausible FTP is ignored rather than producing an absurd ratio.
    assert tc.analyze_activity(samples, ftp_w=5)["intensity_factor"] is None


def test_impractical_sample_counts_are_not_invented():
    # 40 samples, all power 0 W: no time coverage -> every metric stays None.
    zero = constant(39, power=0)
    result = tc.analyze_activity(zero, ftp_w=250)
    assert result["analyzed_seconds"] == pytest.approx(39.0)
    assert result["power_seconds"] == pytest.approx(39.0)
    assert result["average_power_w"] == pytest.approx(0.0)
    assert result["sufficient_data"] is False


# ---------------------------------------------------------------------------
# Zone classification
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("power", "expected"),
    [
        (10, 1),
        (137, 1),  # 54.8% of 250
        (138, 2),  # 55.2% -> endurance
        (187, 2),
        (188, 3),
        (225, 3),
        (226, 4),
        (262, 4),  # 104.8%
        (263, 5),  # 105.2%
        (300, 5),
        (301, 6),
        (375, 6),
        (376, 7),  # 150.4%
        (900, 7),
    ],
)
def test_power_zone_boundaries_are_exact(power, expected):
    assert tc.power_zone_number(power, 250) == expected


def test_power_zone_seconds_sum_to_power_seconds():
    result = tc.analyze_activity(constant(600, power=lambda i: 150 + (i % 300)), ftp_w=250)
    total = sum(result["power_zone_seconds"].values())
    assert total == pytest.approx(result["power_seconds"], abs=1.0)


def test_power_zones_unavailable_without_ftp():
    result = tc.analyze_activity(constant(600, power=200), ftp_w=None)
    assert set(result["power_zone_seconds"].values()) == {0.0}


def test_hr_zones_use_percent_of_max_when_only_max_known():
    result = tc.analyze_activity(constant(600, hr=160), max_hr_bpm=180, resting_hr_bpm=None)
    assert result["hr_zone_model"] == "hr_max"
    assert result["hr_zone_seconds"][4] > 0  # 160/180 = 88.9% -> threshold


def test_hr_zones_prefer_karoven_when_reserve_is_known():
    result = tc.analyze_activity(constant(600, hr=140), max_hr_bpm=180, resting_hr_bpm=60)
    assert result["hr_zone_model"] == "hrr"
    # (140 - 60) / (180 - 60) = 66.7% of reserve -> zone 2.
    assert result["hr_zone_seconds"][2] > 0
    assert result["hr_zone_seconds"][4] == 0.0


def test_hr_zones_unavailable_without_thresholds():
    result = tc.analyze_activity(constant(600, hr=140))
    assert result["hr_zone_model"] is None
    assert result["hr_load"] is None
    assert set(result["hr_zone_seconds"].values()) == {0.0}


def test_trimp_weights_every_minute_in_zone():
    # 10 min at (150-50)/(180-50) = 76.9% of reserve -> zone 3 (weight 3).
    result = tc.analyze_activity(constant(600, hr=150), max_hr_bpm=180, resting_hr_bpm=50)
    assert result["hr_zone_model"] == "hrr"
    assert result["hr_zone_seconds"][3] == pytest.approx(599.0, abs=1.0)
    assert result["hr_load"] == pytest.approx(30.0, rel=0.01)


def test_power_and_hr_load_are_independent_values():
    result = tc.analyze_activity(
        constant(3600, power=200, hr=140),
        ftp_w=200,
        max_hr_bpm=180,
        resting_hr_bpm=60,
    )
    # 1 h at FTP with power -> 100 power points.
    assert result["power_load"] == pytest.approx(100.0, rel=0.01)
    # 1 h at 66.7% of HRR (zone 2, weight 2) -> 120 heart rate points.
    assert result["hr_load"] == pytest.approx(120.0, rel=0.01)
    # Same workout, different scales: the two loads are never summed anywhere.
    assert result["power_load"] != result["hr_load"]


def test_cadence_is_averaged_but_never_required():
    result = tc.analyze_activity(constant(600, cad=88), ftp_w=250)
    assert result["average_cadence_rpm"] == pytest.approx(88.0, abs=0.1)
    assert result["has_cadence"] is True
    assert result["normalized_power_w"] is None


# ---------------------------------------------------------------------------
# FTP records
# ---------------------------------------------------------------------------


def record(source, value, day, ident):
    return {"id": ident, "source": source, "value_w": value, "effective_at": day}


def test_confirmed_ftp_always_beats_estimated():
    records = [
        record("estimated", 300, date(2026, 1, 1), "a"),
        record("manual", 250, date(2026, 1, 1), "b"),
    ]
    resolved = tc.resolve_effective_ftp(records)
    assert (resolved.ftp_w, resolved.source, resolved.confirmed) == (250.0, "manual", True)


def test_estimated_used_only_when_nothing_is_confirmed():
    resolved = tc.resolve_effective_ftp([record("estimated", 300, date(2026, 1, 1), "a")])
    assert (resolved.ftp_w, resolved.source, resolved.confirmed) == (300.0, "estimated", False)


def test_most_recent_confirmed_record_wins():
    records = [
        record("test_20min", 300, date(2026, 1, 1), "a"),
        record("manual", 250, date(2026, 3, 1), "b"),
    ]
    assert tc.resolve_effective_ftp(records).source == "manual"


def test_ties_break_deterministically_and_ignore_bad_values():
    same_day = date(2026, 2, 1)
    records = [
        record("manual", 250, same_day, "z"),
        record("test_20min", 260, same_day, "y"),
        record("manual", 1, same_day, "a"),  # below the plausible floor
        record("imported", 5000, same_day, "b"),  # above the plausible ceiling
    ]
    resolved = tc.resolve_effective_ftp(records)
    assert resolved.source == "test_20min"
    assert resolved.ftp_w == 260.0
    # Same input, same answer — no reliance on row order.
    assert tc.resolve_effective_ftp(list(reversed(records))) == resolved


def test_no_usable_records_resolves_to_nothing():
    assert tc.resolve_effective_ftp([]) == FtpResolution(None, None, None, None, False)


def test_ftp_20min_test_is_95_percent_of_best_window():
    samples = constant(1200, power=300, step=1.0)
    ftp, evidence = tc.ftp_from_20min_test(samples)
    assert ftp == pytest.approx(285.0, rel=0.01)
    assert evidence["version"] == tc.FTP_20MIN_VERSION
    assert evidence["approximation"] is False


def test_ftp_20min_test_needs_enough_data():
    ftp, evidence = tc.ftp_from_20min_test(constant(300, power=300))
    assert ftp is None
    assert evidence["status"] == "insufficient_data"


def test_ramp_test_is_labelled_an_approximation():
    ftp, evidence = tc.ftp_from_ramp_test(constant(600, power=300, step=1.0))
    assert ftp == pytest.approx(285.0, rel=0.01)
    assert evidence["approximation"] is True
    assert evidence["version"] == tc.FTP_RAMP_VERSION


def test_ftp_test_picks_the_best_window_not_the_average():
    # 10 min easy, 20 min hard, 10 min easy.
    samples = (
        constant(600, power=150, step=1.0)
        + constant(1200, power=320, step=1.0, start=T0 + 600)
        + constant(600, power=150, step=1.0, start=T0 + 1800)
    )
    ftp, _ = tc.ftp_from_20min_test(samples)
    assert ftp == pytest.approx(0.95 * 320, rel=0.01)


# ---------------------------------------------------------------------------
# Load trend
# ---------------------------------------------------------------------------


def test_zero_load_history_stays_zero():
    rows = tc.load_trend([], date(2026, 1, 1), date(2026, 1, 7))
    assert len(rows) == 7
    assert all(r["ctl"] == 0.0 and r["atl"] == 0.0 and r["tsb"] == 0.0 for r in rows)


def test_single_spike_decays_on_both_time_constants():
    day = date(2026, 1, 1)
    rows = tc.load_trend([(day, 100.0)], day, date(2026, 2, 11))
    spike = rows[0]
    # A resting day: CTL moves toward load via ATL (7 d) — strictly monotonic.
    assert spike["load"] == 100.0
    assert rows[1]["ctl"] < spike["ctl"]
    assert rows[1]["atl"] < spike["atl"]
    # 42 days on the 7-day constant: the acute value has decayed by >99.9%.
    assert rows[-1]["atl"] < 0.5
    # The chronic value decays far more slowly than the acute one.
    assert rows[-1]["ctl"] > rows[-1]["atl"]
    assert rows[-1]["ctl"] < 1.0


def test_tsb_is_yesterdays_ctl_minus_atl():
    day = date(2026, 1, 1)
    rows = tc.load_trend([(day, 50.0)] * 10, day, date(2026, 1, 5))
    for previous, current in pairwise(rows):
        assert current["tsb"] == pytest.approx(previous["ctl"] - previous["atl"], abs=0.01)


def test_load_trend_is_continuous_across_gaps():
    day = date(2026, 3, 1)
    rows = tc.load_trend([(day, 10.0)], day, date(2026, 3, 11))
    assert [r["date"] for r in rows] == [day + timedelta(days=i) for i in range(11)]


def test_load_trend_rejects_inverted_range():
    assert tc.load_trend([], date(2026, 1, 5), date(2026, 1, 1)) == []


# ---------------------------------------------------------------------------
# Recovery signals
# ---------------------------------------------------------------------------


def test_signals_need_a_week_of_history():
    result = tc.recovery_signals([(date(2026, 1, 1), 50.0)], date(2026, 1, 7))
    assert result["status"] == "unavailable"
    assert result["code"] == "insufficient_data"
    assert result["evidence"]["active_days"] == 1


def test_load_increase_signal():
    today = date(2026, 5, 20)
    loads = [(today - timedelta(days=offset), 100.0) for offset in range(7)]
    loads += [(today - timedelta(days=7 + offset), 50.0) for offset in range(7)]
    result = tc.recovery_signals(loads, today)
    assert result["code"] == "load_increased"
    assert result["evidence"]["recent_load"] == pytest.approx(700.0)
    assert result["evidence"]["previous_load"] == pytest.approx(350.0)
    assert result["evidence"]["change_ratio"] == pytest.approx(2.0)


def test_load_decrease_signal():
    today = date(2026, 5, 20)
    loads = [(today - timedelta(days=offset), 50.0) for offset in range(7)]
    loads += [(today - timedelta(days=7 + offset), 100.0) for offset in range(7)]
    assert tc.recovery_signals(loads, today)["code"] == "load_decreased"


def test_stable_load_inside_the_20_percent_band():
    today = date(2026, 5, 20)
    loads = [(today - timedelta(days=offset), 100.0) for offset in range(7)]
    loads += [(today - timedelta(days=7 + offset), 90.0) for offset in range(7)]
    assert tc.recovery_signals(loads, today)["code"] == "load_stable"


def test_stale_load_is_reported_separately():
    today = date(2026, 5, 20)
    # Loads stopped 16 days ago: enough history, but nothing recent.
    loads = [(today - timedelta(days=offset), 100.0) for offset in range(16, 41)]
    result = tc.recovery_signals(loads, today)
    assert "load_stale" in result["signals"]
    assert result["code"] == "load_stale"
    assert result["evidence"]["days_since_load"] == 16


# ---------------------------------------------------------------------------
# Bounded prescriptions
# ---------------------------------------------------------------------------


def test_suggestion_is_capped_at_15_percent():
    result = tc.suggest_intensity_target(100.0)
    assert result["target_load"] == pytest.approx(115.0)
    assert result["reason"] == "bounded_increase"


def test_suggestion_refuses_to_invent_a_baseline():
    result = tc.suggest_intensity_target(None)
    assert result["status"] == "unavailable"
    assert result["target_load"] is None
    assert result["reason"] == "no_baseline"


def test_weekly_cap_prevents_an_increase():
    result = tc.suggest_intensity_target(100.0, weekly_load=1000.0)
    assert result["target_load"] == pytest.approx(100.0)
    assert result["reason"] == "weekly_cap"


# ---------------------------------------------------------------------------
# Determinism / performance
# ---------------------------------------------------------------------------


def test_analysis_is_reproducible():
    samples = ramp(5000, power=lambda i: 120 + (i % 400), hr=lambda i: 110 + (i % 60))
    first = tc.analyze_activity(samples, ftp_w=250, max_hr_bpm=185, resting_hr_bpm=55)
    second = tc.analyze_activity(samples, ftp_w=250, max_hr_bpm=185, resting_hr_bpm=55)
    assert first == second


@pytest.mark.parametrize("samples_per_hour", [3600, 18000])
def test_large_activities_stay_fast(samples_per_hour: int):
    """A 5 h 1 Hz stream (~18k samples) must stay well inside an API budget."""
    import time

    samples = ramp(
        5 * samples_per_hour,
        step=1.0,
        power=lambda i: 150 + (i % 300),
        hr=lambda i: 120 + (i % 50),
    )
    started = time.perf_counter()
    result = tc.analyze_activity(samples, ftp_w=260, max_hr_bpm=185, resting_hr_bpm=55)
    elapsed = time.perf_counter() - started
    assert result["normalized_power_w"] is not None
    assert elapsed < 2.0, f"analysis took {elapsed:.2f}s for {len(samples)} samples"

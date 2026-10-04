"""GPS engine unit tests — fixed coordinates, no live GPS."""

import pytest

from app.services.gps_engine import (
    EngineState,
    Observation,
    haversine_m,
    process,
    recompute,
)

# ~10 m per latitude step (111195 m/deg). Longitude steps are shorter
# (×cos(lat)); jump test uses longitude deliberately.
STEP = 0.00009
LAT, LON, T0 = 33.0, -6.0, 1_700_000_000.0


def obs(seq, dlat=0.0, dlon=0.0, dt=None, **kw):
    if dt is None:
        dt = seq * 5.0
    return Observation(seq=seq, lat=LAT + dlat, lon=LON + dlon, recorded_at=T0 + dt, **kw)


def test_haversine_known_distance():
    assert haversine_m(0, 0, 0, 1) == pytest.approx(111194.93, abs=0.5)
    assert haversine_m(LAT, LON, LAT, LON) == 0.0


def test_first_point_anchors_no_segment():
    s = EngineState()
    v = process(s, obs(0))
    assert (v.accepted, v.reason) == (True, "ok")
    assert s.distance_m == 0.0 and s.summary()["accepted_points"] == 1


def test_invalid_coordinates_rejected():
    s = EngineState()
    assert process(s, obs(0, dlat=100.0)).reason == "invalid_coords"
    assert process(s, obs(0, dlon=200.0)).reason == "invalid_coords"
    assert s.accepted_count == 0


def test_poor_accuracy_rejected_not_metered():
    s = EngineState()
    process(s, obs(0))
    v = process(s, obs(1, dlat=STEP, accuracy=50.0))
    assert v.reason == "bad_accuracy"
    assert s.distance_m == 0.0


def test_duplicate_seq_rejected():
    s = EngineState()
    process(s, obs(0))
    assert process(s, obs(0, dlat=STEP)).reason == "duplicate"
    assert s.distance_m == 0.0


def test_timestamp_regression_rejected():
    s = EngineState()
    process(s, obs(0))
    process(s, obs(1, dlat=STEP))
    assert process(s, obs(2, dlon=2 * STEP, dt=5.0)).reason == "time_regression"


def test_jump_rejected_baseline_survives():
    s = EngineState()
    process(s, obs(0))
    v = process(s, obs(1, dlon=0.01, dt=1.0))  # ~1 km in 1 s
    assert v.reason == "jump"
    assert s.distance_m == 0.0
    # Next good fix judged against the last GOOD fix, not the glitch.
    v2 = process(s, obs(2, dlat=STEP))
    assert v2.accepted and s.distance_m == pytest.approx(10.0, abs=0.5)


def test_distance_and_moving_time():
    s = EngineState()
    process(s, obs(0))
    process(s, obs(1, dlat=STEP))  # ~10 m in 5 s → moving
    process(s, obs(2, dlat=2 * STEP))  # ~10 m in 5 s → moving
    assert s.distance_m == pytest.approx(20.0, abs=1.0)
    assert s.moving_s == pytest.approx(10.0, abs=0.1)
    assert s.max_speed_m_s == pytest.approx(2.0, abs=0.1)


def test_slow_segment_counts_elapsed_not_moving():
    s = EngineState()
    process(s, obs(0))
    process(s, obs(1, dlat=STEP / 10, dt=10.0))  # ~1 m in 10 s → stationary
    assert s.moving_s == 0.0
    assert s.summary()["elapsed_seconds"] == 10


def test_elevation_threshold_3m():
    s = EngineState()
    process(s, obs(0, alt=100.0))
    process(s, obs(1, dlon=STEP, dt=5.0, alt=102.0))  # +2 m noise
    assert s.gain_m == 0.0
    process(s, obs(2, dlon=2 * STEP, dt=10.0, alt=105.0))  # +5 total → counts
    assert s.gain_m == pytest.approx(5.0, abs=0.01)
    process(s, obs(3, dlat=3 * STEP, alt=101.0))  # −4 → loss
    assert s.loss_m == pytest.approx(4.0, abs=0.01)


def test_pause_gap_adds_no_distance():
    s = EngineState()
    process(s, obs(0))
    process(s, obs(1, dlat=STEP))
    s.reset_segment()  # resume after 1 h / 100 m pause gap
    v = process(s, obs(2, dlat=STEP + 0.0009, dt=3605.0))
    assert v.accepted and v.distance_m == 0.0
    assert s.distance_m == pytest.approx(10.0, abs=0.5)


def test_bad_gps_speed_rejects_point():
    s = EngineState()
    process(s, obs(0))
    assert process(s, obs(1, dlat=STEP, speed=500.0)).reason == "bad_speed"


def test_recompute_deterministic_and_empty():
    assert recompute([])["distance_m"] == 0.0
    pts = [obs(0), obs(1, dlat=STEP), obs(2, dlat=2 * STEP)]
    a, b = recompute(pts), recompute(list(reversed(pts)))
    assert a == b and a["distance_m"] == pytest.approx(20.0, abs=1.0)


def test_single_point_ride_zeros():
    s = EngineState()
    process(s, obs(0))
    summary = s.summary()
    assert summary["moving_seconds"] == 0 and summary["average_speed_m_s"] == 0.0

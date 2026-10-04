"""Deterministic GPS processing engine (pure — no DB, no I/O).

Pipeline per observation: validate → quality gates → accepted point →
metrics. Rejected observations never touch metrics. Thresholds documented
in docs/05 + ADR-08; provenance raw/derived/estimated preserved.
"""

import math
from dataclasses import dataclass

EARTH_RADIUS_M = 6_371_000.0
# Quality policy (documented, tested, configurable via GpsConfig).
ACCURACY_REJECT_M = 25.0  # GPS accuracy worse than this: stored, not metered
JUMP_SPEED_REJECT_M_S = 80.0  # implied segment speed above: impossible jump
SPEED_MAX_M_S = 80.0  # GPS-reported speed above: ignored for metrics
ELEVATION_THRESHOLD_M = 3.0  # smaller deltas are noise, not climbing
MOVING_THRESHOLD_M_S = 1.0  # segment speed at/above: moving time
DUPLICATE_DISTANCE_M = 1.0  # same seq/time within 1 m: duplicate


@dataclass(frozen=True)
class GpsConfig:
    accuracy_reject_m: float = ACCURACY_REJECT_M
    jump_speed_reject_m_s: float = JUMP_SPEED_REJECT_M_S
    elevation_threshold_m: float = ELEVATION_THRESHOLD_M
    moving_threshold_m_s: float = MOVING_THRESHOLD_M_S


@dataclass(frozen=True)
class Observation:
    """Raw GPS observation. Provenance: raw."""

    seq: int
    lat: float
    lon: float
    recorded_at: float  # epoch seconds
    alt: float | None = None
    accuracy: float | None = None
    speed: float | None = None  # GPS-reported instantaneous speed (raw)
    heading: float | None = None


@dataclass
class EngineState:
    """Mutable fold state. Reset segment baseline on resume (no pause jump)."""

    last_lat: float | None = None
    last_lon: float | None = None
    last_time: float | None = None
    last_seq: int | None = None
    ele_baseline: float | None = None
    first_time: float | None = None
    distance_m: float = 0.0
    gain_m: float = 0.0
    loss_m: float = 0.0
    moving_s: float = 0.0
    max_speed_m_s: float = 0.0
    accepted_count: int = 0

    def reset_segment(self) -> None:
        """Call on resume: next point starts a new segment (pause gap ignored).

        Elevation baseline persists (altitude is absolute, not relative).
        """
        self.last_lat = self.last_lon = self.last_time = None

    def summary(self) -> dict:
        elapsed = (
            (self.last_time - self.first_time)
            if self.first_time is not None and self.last_time is not None
            else 0.0
        )
        avg = self.distance_m / self.moving_s if self.moving_s > 0 else 0.0
        return {
            "distance_m": round(self.distance_m, 2),
            "elevation_gain_m": round(self.gain_m, 2),
            "elevation_loss_m": round(self.loss_m, 2),
            "moving_seconds": int(self.moving_s),
            "elapsed_seconds": int(elapsed),
            "average_speed_m_s": round(avg, 3),
            "max_speed_m_s": round(self.max_speed_m_s, 3),
            "accepted_points": self.accepted_count,
        }


@dataclass
class Verdict:
    accepted: bool
    reason: str  # ok | invalid_coords | bad_accuracy | duplicate | out_of_order
    # time_regression | jump | bad_speed | paused
    distance_m: float = 0.0
    dt_s: float = 0.0
    moving: bool = False


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance. Never naive lat/lon deltas."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(a))


def process(state: EngineState, obs: Observation, cfg: GpsConfig = GpsConfig()) -> Verdict:
    """Validate + gate one observation, folding metrics on acceptance."""
    if not (-90.0 <= obs.lat <= 90.0 and -180.0 <= obs.lon <= 180.0):
        return Verdict(False, "invalid_coords")
    if obs.accuracy is not None and obs.accuracy > cfg.accuracy_reject_m:
        return Verdict(False, "bad_accuracy")
    if state.last_seq is not None and obs.seq <= state.last_seq:
        # Same seq (retry/provider dupe) or reordered late point.
        if obs.seq == state.last_seq:
            return Verdict(False, "duplicate")
        return Verdict(False, "out_of_order")
    if state.last_time is not None and obs.recorded_at <= state.last_time:
        return Verdict(False, "time_regression")
    if obs.speed is not None and not (0 <= obs.speed <= SPEED_MAX_M_S):
        return Verdict(False, "bad_speed")

    if state.last_lat is None or state.last_time is None:
        # First point (or first after resume): anchor, no segment.
        state.last_lat, state.last_lon = obs.lat, obs.lon
        state.last_time = obs.recorded_at
        state.last_seq = obs.seq
        if state.first_time is None:
            state.first_time = obs.recorded_at
        if obs.alt is not None and state.ele_baseline is None:
            state.ele_baseline = obs.alt
        state.accepted_count += 1
        return Verdict(True, "ok")

    assert state.last_lon is not None
    dist = haversine_m(state.last_lat, state.last_lon, obs.lat, obs.lon)
    dt = obs.recorded_at - state.last_time
    if dt <= 0:
        return Verdict(False, "time_regression")
    if dist < DUPLICATE_DISTANCE_M and dt < 1.0:
        # Same fix re-delivered (Android/iOS callback duplication).
        state.last_seq = obs.seq
        return Verdict(False, "duplicate")
    implied = dist / dt
    if implied > cfg.jump_speed_reject_m_s:
        # Impossible jump: do NOT advance baseline (keeps one glitch from
        # poisoning the next good fix), but record seq so a retry isn't
        # misread as out-of-order… actually keep last_seq so the *next*
        # point is judged against the last good fix. Do not update anything.
        return Verdict(False, "jump", distance_m=round(dist, 2), dt_s=round(dt, 3))

    moving = implied >= cfg.moving_threshold_m_s
    state.distance_m += dist
    if moving:
        state.moving_s += dt
    state.max_speed_m_s = max(state.max_speed_m_s, implied)
    if obs.alt is not None:
        if state.ele_baseline is None:
            state.ele_baseline = obs.alt
        else:
            delta = obs.alt - state.ele_baseline
            if delta >= cfg.elevation_threshold_m:
                state.gain_m += delta
                state.ele_baseline = obs.alt
            elif delta <= -cfg.elevation_threshold_m:
                state.loss_m += -delta
                state.ele_baseline = obs.alt
    state.last_lat, state.last_lon = obs.lat, obs.lon
    state.last_time = obs.recorded_at
    state.last_seq = obs.seq
    state.accepted_count += 1
    return Verdict(True, "ok", distance_m=round(dist, 2), dt_s=round(dt, 3), moving=moving)


def recompute(points: list[Observation], cfg: GpsConfig = GpsConfig()) -> dict:
    """Deterministic full summary from accepted-ordered points (finish path)."""
    state = EngineState()
    for obs in sorted(points, key=lambda o: o.seq):
        process(state, obs, cfg)
    return state.summary()

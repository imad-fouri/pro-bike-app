"""Route metrics: pure, deterministic, no I/O (docs/05 constants reused).

Provenance markers (§15):
  distance / elevation gain-loss / profile   -> DERIVED from stored points
  estimated_duration_s                       -> ESTIMATED (documented formula)
  difficulty                                 -> ESTIMATED, rule-based (never AI)
"""

from dataclasses import dataclass
from itertools import pairwise

from app.services.gps_engine import ELEVATION_THRESHOLD_M, haversine_m

# --- ESTIMATED duration model (documented, deterministic) -------------------
BASE_SPEED_M_S: dict[str, float] = {
    "road": 6.94,  # 25 km/h flat, endurance effort
    "gravel": 5.56,  # 20 km/h mixed surface
    "mountain_bike": 4.44,  # 16 km/h technical
    "touring": 5.00,  # 18 km/h loaded
    "bikepacking": 4.72,  # 17 km/h loaded off-road
    "commuting": 4.44,  # 16 km/h stop-and-go
    "e_bike": 6.11,  # 22 km/h assisted
    "other": 5.56,
}
DEFAULT_SPEED_M_S = 5.56
VERTICAL_ASCENT_M_S = 0.1667  # VAM ≈ 600 m/h climbing
DESCENT_SPEED_FACTOR = 1.5  # descending is faster than flat

# --- Rule-based difficulty (documented factors: distance, gain, grade) ------
# Label: "rule_based_v1". Not a universal cycling-difficulty claim.
EXTREME_DISTANCE_KM = 150.0
EXTREME_GAIN_M = 2500.0
EXTREME_GRADE_PCT = 12.0
HARD_DISTANCE_KM = 90.0
HARD_GAIN_M = 1200.0
HARD_GRADE_PCT = 8.0
EASY_DISTANCE_KM = 40.0
EASY_GAIN_M = 400.0
EASY_GRADE_PCT = 5.0

MAX_PROFILE_POINTS = 400  # downsampled elevation profile cache bound
MIN_GRADE_SEGMENT_M = 10.0  # ignore sub-10 m segments (noise)

DIFFICULTY_BASIS = "rule_based_v1"
DURATION_BASIS = "estimated"


@dataclass(frozen=True)
class GeoPoint:
    lat: float
    lon: float
    ele: float | None = None


@dataclass(frozen=True)
class RouteMetrics:
    distance_m: float
    elevation_gain_m: float | None  # None => no usable elevation data
    elevation_loss_m: float | None
    highest_point_m: float | None
    lowest_point_m: float | None
    max_grade_pct: float | None
    estimated_duration_s: int | None
    difficulty: str | None
    profile: list[list[float]]  # [[distance_m, ele_m], …] downsampled


def dedupe_consecutive(points: list[GeoPoint]) -> list[GeoPoint]:
    """Drop exact consecutive duplicates (identical lat/lon/ele)."""
    out: list[GeoPoint] = []
    for p in points:
        if out and out[-1].lat == p.lat and out[-1].lon == p.lon and out[-1].ele == p.ele:
            continue
        out.append(p)
    return out


def elevation_profile(points: list[GeoPoint], dists: list[float]) -> list[list[float]]:
    """distance → elevation samples, capped at MAX_PROFILE_POINTS."""
    samples = [
        (round(d, 1), round(float(p.ele), 1)) for d, p in zip(dists, points) if p.ele is not None
    ]
    if not samples:
        return []
    if len(samples) <= MAX_PROFILE_POINTS:
        return [[float(d), float(e)] for d, e in samples]
    step = (len(samples) - 1) / (MAX_PROFILE_POINTS - 1)
    picked = [samples[round(i * step)] for i in range(MAX_PROFILE_POINTS)]
    return [[float(d), float(e)] for d, e in picked]


def compute(points: list[GeoPoint], activity_type: str = "other") -> RouteMetrics:
    pts = dedupe_consecutive(points)
    if len(pts) < 2:
        return RouteMetrics(0.0, None, None, None, None, None, None, None, [])

    dists: list[float] = [0.0]
    total = 0.0
    for a, b in pairwise(pts):
        total += haversine_m(a.lat, a.lon, b.lat, b.lon)
        dists.append(total)

    # --- elevation (DERIVED, 3 m hysteresis identical to the ride engine) ---
    eles = [p.ele for p in pts]
    has_ele = sum(1 for e in eles if e is not None) >= 2
    gain = loss = 0.0
    high = low = None
    max_grade: float | None = None
    if has_ele:
        ref: float | None = None
        for e in eles:
            if e is None:
                continue
            high = e if high is None else max(high, e)
            low = e if low is None else min(low, e)
            if ref is None:
                ref = e
                continue
            if e - ref >= ELEVATION_THRESHOLD_M:
                gain += e - ref
                ref = e
            elif ref - e >= ELEVATION_THRESHOLD_M:
                loss += ref - e
                ref = e
        for i, (a, b) in enumerate(pairwise(pts)):
            if a.ele is None or b.ele is None:
                continue
            seg = dists[i + 1] - dists[i]
            if seg < MIN_GRADE_SEGMENT_M:
                continue
            grade = (float(b.ele) - float(a.ele)) / seg * 100.0
            max_grade = grade if max_grade is None else max(max_grade, grade)

    # --- ESTIMATED duration --------------------------------------------------
    duration = estimate_duration_s(
        total,
        activity_type,
        gain if has_ele else None,
    )

    # --- difficulty (ESTIMATED, rule-based) ----------------------------------
    difficulty = _difficulty(total, gain if has_ele else None, max_grade if has_ele else None)

    return RouteMetrics(
        distance_m=round(total, 2),
        elevation_gain_m=round(gain, 2) if has_ele else None,
        elevation_loss_m=round(loss, 2) if has_ele else None,
        highest_point_m=round(high, 2) if high is not None else None,
        lowest_point_m=round(low, 2) if low is not None else None,
        max_grade_pct=round(max_grade, 2) if max_grade is not None else None,
        estimated_duration_s=duration,
        difficulty=difficulty,
        profile=elevation_profile(pts, dists),
    )


def _difficulty(distance_m: float, gain_m: float | None, max_grade_pct: float | None) -> str:
    km = distance_m / 1000.0
    if (
        km >= EXTREME_DISTANCE_KM
        or (gain_m is not None and gain_m >= EXTREME_GAIN_M)
        or (max_grade_pct is not None and max_grade_pct >= EXTREME_GRADE_PCT)
    ):
        return "extreme"
    if (
        km >= HARD_DISTANCE_KM
        or (gain_m is not None and gain_m >= HARD_GAIN_M)
        or (max_grade_pct is not None and max_grade_pct >= HARD_GRADE_PCT)
    ):
        return "hard"
    if (
        km < EASY_DISTANCE_KM
        and (gain_m is None or gain_m < EASY_GAIN_M)
        and (max_grade_pct is None or max_grade_pct < EASY_GRADE_PCT)
    ):
        return "easy"
    return "moderate"


def estimate_duration_s(
    distance_m: float, activity_type: str, elevation_gain_m: float | None
) -> int | None:
    """ESTIMATED moving time: flat segment + climbing (VAM 600 m/h) + descent."""
    if distance_m <= 0:
        return None
    speed = BASE_SPEED_M_S.get(activity_type, DEFAULT_SPEED_M_S)
    seconds = distance_m / speed
    if elevation_gain_m:
        seconds += elevation_gain_m / VERTICAL_ASCENT_M_S
    return round(seconds)

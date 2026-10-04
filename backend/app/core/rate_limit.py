"""Rate-limit abstraction (token bucket interface). Redis-backed in later phases."""

import time

_buckets: dict[str, list[float]] = {}


def allow(key: str, limit: int = 60, window_s: int = 60) -> bool:
    """In-memory fallback for Phase 1; Redis implementation comes with infra."""
    now = time.time()
    hits = [t for t in _buckets.get(key, []) if now - t < window_s]
    if len(hits) >= limit:
        _buckets[key] = hits
        return False
    hits.append(now)
    _buckets[key] = hits
    return True

"""Rate-limit abstraction (token bucket interface). Redis-backed in later phases."""

import time

#: Hard bound on tracked keys: an attacker varying the key (e.g. rotating
#: source IPs against an IP-keyed endpoint) must not grow process memory
#: without limit. Eviction is oldest-inserted-first; limits stay best-effort
#: per process, which the callers already document.
_MAX_KEYS = 10000

#: Amortized reaping: a full sweep at most once per second, so the common
#: path stays O(current key hits).
_sweep_at = 0.0
_SWEEP_INTERVAL_S = 1.0

# key -> (hit timestamps, window_s). The window travels with the key because
# endpoints use different windows; sweeping one key under another key's
# window would silently widen its limit.
_buckets: dict[str, tuple[list[float], int]] = {}


def _count_hit(key: str) -> None:
    """Count a refused request, per ENDPOINT and never per key.

    The limiter key contains a user id or a client IP — `auth:login:203.0.113.9`,
    `coach:<uuid>`. Recording that would turn an abuse-resistance mechanism into a
    log of who is being rate-limited, which is both an identity disclosure and an
    unbounded time series. So only the leading segment — the endpoint family — is
    used, and only as a count.

    Never raises: a metrics failure must not turn a 429 into a 500.
    """
    try:
        from app.core.metrics import record_rate_limit

        endpoint = key.split(":", 1)[0] if ":" in key else "unknown"
        record_rate_limit(endpoint)
    except Exception as exc:  # noqa: BLE001 - never break a 429 over a metric
        # `exc` is never logged by value: a metrics refusal message could echo a
        # label, and a label is where an identity would first appear.
        import logging

        logging.getLogger("cyclecoach").warning(
            "metrics_record_failed", extra={"error_type": type(exc).__name__}
        )


def _reap(now: float) -> None:
    """Drop keys with no in-window hits (under their OWN window); cap total."""
    global _sweep_at
    if now - _sweep_at < _SWEEP_INTERVAL_S:
        return
    _sweep_at = now
    live: dict[str, tuple[list[float], int]] = {}
    for key, (hits, window_s) in _buckets.items():
        fresh = [t for t in hits if now - t < window_s]
        if fresh:
            live[key] = (fresh, window_s)
    if len(live) > _MAX_KEYS:
        # Oldest-inserted-first: dicts preserve insertion order.
        for key in list(live)[: len(live) - _MAX_KEYS]:
            del live[key]
    _buckets.clear()
    _buckets.update(live)


def allow(key: str, limit: int = 60, window_s: int = 60) -> bool:
    """In-memory fallback for Phase 1; Redis implementation comes with infra."""
    now = time.time()
    _reap(now)
    hits, _ = _buckets.get(key, ([], window_s))
    hits = [t for t in hits if now - t < window_s]
    if len(hits) >= limit:
        _buckets[key] = (hits, window_s)
        _count_hit(key)
        return False
    hits.append(now)
    _buckets[key] = (hits, window_s)
    if len(_buckets) > _MAX_KEYS:
        # The sweep above caps stale state, but the just-touched key is added
        # after it: trim oldest-inserted keys (never the current one) so the
        # bound holds exactly, even mid-burst.
        for old in _buckets:
            if old != key:
                del _buckets[old]
                if len(_buckets) <= _MAX_KEYS:
                    break
    return True

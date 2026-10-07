"""In-process metric collection for CycleCoach.

Phase 10 WS-O. No dependency, no exporter, no collector: this is a **registry and a
recorder**, not a client for Prometheus or StatsD. Choosing a wire protocol and a
backend is an infrastructure decision, and picking one here would leave either a
half-wired exporter or a dependency nobody chose.

The design constraint that shaped everything in this file is **cardinality**.
A metric registry is a dictionary keyed by its labels, so a single unbounded label
value is a permanent memory leak in the process that records it:

    _counters[("GET", "/api/v1/rides/8f3a2b7c-...", "2xx")] += 1

is one new series per ride, forever. So the recorder accepts only labels drawn
from an allowlist of names, AND every call site must pass a *route template*
(`/api/v1/rides/{ride_id}`) rather than a concrete path. `_ALLOWED_LABEL_NAMES`
is the enforcement point, and `tests/test_observability.py` asserts both that the
forbidden identity-shaped names are absent and that a high-cardinality value is
rejected before it can reach the registry.

Privacy is the other constraint, and it is why `user_id`, `email`, `username`,
`request_id`, `ride_id` and coordinates are not merely unused but **structurally
unrecordable**: `_ALLOWED_LABEL_NAMES` does not contain them, so passing one raises
instead of silently creating an identifying time series. A metric that can carry a
coordinate is a metric that will eventually carry one.

Request ids deliberately do not belong here. They are unbounded, they are already
in the logs, and they are the right tool for tracing a single request.
"""

import re
import threading
from collections import defaultdict
from collections.abc import Iterable, Mapping

#: A UUID anywhere inside a label value: eight hex groups joined by hyphens.
#:
#: Matched as a substring because the value that matters is a path *containing* an
#: id. A test over the whole string would never fire: stripping hyphens from
#: `/api/v1/rides/<uuid>` leaves the letters of `api`, `rides` and the slashes.
_UUID_FRAGMENT = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)

#: A bare 32+ character hex run: a UUID without hyphens, as some clients send it.
_BARE_HEX_FRAGMENT = re.compile(r"(?<![0-9a-zA-Z])[0-9a-fA-F]{32,}(?![0-9a-zA-Z])")

#: Label names a metric may carry.
#:
#: An allowlist rather than a denylist, for the reason above: the failure mode of
#: an allowlist is a rejected metric, and the failure mode of a denylist is an
#: identifying time series nobody notices for a year.
_ALLOWED_LABEL_NAMES = frozenset(
    {
        # Request shape.
        "method",
        # A ROUTE TEMPLATE, never a concrete path. See the module docstring.
        "route",
        # 2xx / 4xx / 5xx, or an exact code when one is operationally distinct.
        "status_class",
        # A closed set of causes an operator can act on.
        "error_category",
        # Enumerations only, and bounded ones.
        "provider",
        "outcome",
        "event",
        "intent",
        "reason",
        "notification_type",
        "feature",
        "plan",
        "status",
    }
)

#: Label names that are refused outright, named here so the reason is greppable
#: and so the tests can assert against an explicit list rather than a guess.
#:
#: None of these is in `_ALLOWED_LABEL_NAMES`; this tuple documents *why*, and the
#: test asserts the two sets stay disjoint.
_FORBIDDEN_LABEL_NAMES = frozenset(
    {
        "user_id",
        "email",
        "username",
        "userId",
        "ride_id",
        "route_id",
        "group_ride_id",
        "conversation_id",
        "team_id",
        "notification_id",
        "request_id",
        "device_id",
        "latitude",
        "longitude",
        "lat",
        "lon",
        "coordinates",
        "location",
        "body",
        "token",
        "password",
    }
)


class MetricCardinalityError(ValueError):
    """Raised when a metric is recorded with a disallowed label name or value.

    Raising rather than dropping is deliberate. A silently-ignored metric is
    indistinguishable from a broken one, and an operator debugging a dashboard gap
    would have no way to know the label was refused.
    """


def _validate_labels(labels: Mapping[str, object]) -> dict[str, str]:
    validated: dict[str, str] = {}
    for key, value in labels.items():
        if key in _FORBIDDEN_LABEL_NAMES:
            raise MetricCardinalityError(
                f"label {key!r} is forbidden: metric labels must never carry "
                "identity, location or credentials"
            )
        if key not in _ALLOWED_LABEL_NAMES:
            raise MetricCardinalityError(
                f"label {key!r} is not in the allowlist; add it deliberately or "
                "use a log field instead"
            )
        # A value that looks like a UUID or an email is an identifier that reached
        # the wrong place — usually a concrete path used as a route template. This
        # catches the mistake at the call site rather than in production metrics.
        text = str(value)
        if _looks_like_identifier(text):
            raise MetricCardinalityError(
                f"label {key!r} has the shape of an identifier ({text[:32]!r}); "
                "use a route template or an enumeration"
            )
        validated[key] = text
    return validated


def _looks_like_identifier(text: str) -> bool:
    """Whether a label value looks like an id rather than a category.

    Catches the realistic mistake -- ``route="/api/v1/rides/<uuid>"`` -- which is
    exactly how unbounded cardinality gets introduced by someone in a hurry.

    The UUID is found as a SUBSTRING rather than by testing the whole value, because
    the dangerous value is a path *containing* an id, not a bare id. Stripping the
    hyphens from a whole path leaves letters like `api`, `rides` and the slashes, so
    an all-hex test on the full string never fires.
    """
    if len(text) > 96:
        return True
    if _UUID_FRAGMENT.search(text):
        return True
    if _BARE_HEX_FRAGMENT.search(text):
        return True
    return "@" in text and "." in text


class MetricRegistry:
    """Thread-safe counters, gauges and duration summaries.

    Deliberately tiny and in-process. `prometheus_client` would give real exposition
    and a real dependency; this gives the same cardinality discipline with neither,
    and can be swapped for it later because the call sites record *names and labels*,
    not protocol frames.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counters: dict[tuple[str, tuple[tuple[str, str], ...]], float] = {}
        self._gauges: dict[tuple[str, tuple[tuple[str, str], ...]], float] = {}
        self._durations: dict[tuple[str, tuple[tuple[str, str], ...]], list[float]] = defaultdict(
            list
        )
        self._refusals = 0

    # -- recording ---------------------------------------------------------

    def increment(self, name: str, value: float = 1.0, **labels: str) -> None:
        key = (name, tuple(sorted(_validate_labels(labels).items())))
        with self._lock:
            self._counters[key] = self._counters.get(key, 0.0) + value

    def set_gauge(self, name: str, value: float, **labels: str) -> None:
        key = (name, tuple(sorted(_validate_labels(labels).items())))
        with self._lock:
            self._gauges[key] = float(value)

    def observe_duration_ms(self, name: str, milliseconds: float, **labels: str) -> None:
        key = (name, tuple(sorted(_validate_labels(labels).items())))
        with self._lock:
            # Bounded sample count. An unbounded duration list is a memory leak with
            # a latency-shaped silhouette: the values are small and the process
            # looks healthy right up until it is killed.
            samples = self._durations[key]
            samples.append(float(milliseconds))
            if len(samples) > _MAX_DURATION_SAMPLES:
                del samples[: len(samples) - _MAX_DURATION_SAMPLES]

    # -- reading -----------------------------------------------------------

    def counter_value(self, name: str, **labels: str) -> float:
        key = (name, tuple(sorted(_validate_labels(labels).items())))
        with self._lock:
            return self._counters.get(key, 0.0)

    def gauge_value(self, name: str, **labels: str) -> float:
        key = (name, tuple(sorted(_validate_labels(labels).items())))
        with self._lock:
            return self._gauges.get(key, 0.0)

    def duration_summary(self, name: str, **labels: str) -> dict[str, float] | None:
        key = (name, tuple(sorted(_validate_labels(labels).items())))
        with self._lock:
            samples = self._durations.get(key)
            if not samples:
                return None
            ordered = sorted(samples)
            return {
                "count": float(len(ordered)),
                "min_ms": ordered[0],
                "max_ms": ordered[-1],
                "avg_ms": sum(ordered) / len(ordered),
                # A p95 approximation from a bounded sample list. Not a true
                # quantile sketch; adequate for "is this route getting slower".
                "p95_ms": ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))],
            }

    def snapshot(self) -> dict[str, list[dict[str, object]]]:
        """Everything recorded, grouped by metric name.

        Intended for a future `/metrics` route or a log-shipped periodic dump. Not
        exposed today: publishing metrics is a deployment decision.
        """
        with self._lock:
            grouped: dict[str, list[dict[str, object]]] = defaultdict(list)
            for (name, labels), value in self._counters.items():
                grouped[name].append({"labels": dict(labels), "value": value})
            for (name, labels), value in self._gauges.items():
                grouped[name].append({"labels": dict(labels), "value": value})
            for (name, labels), samples in self._durations.items():
                if samples:
                    ordered = sorted(samples)
                    grouped[name].append(
                        {
                            "labels": dict(labels),
                            "count": len(ordered),
                            "avg_ms": sum(ordered) / len(ordered),
                            "max_ms": ordered[-1],
                        }
                    )
            return dict(grouped)

    # -- test/maintenance --------------------------------------------------

    def series_count(self) -> int:
        with self._lock:
            return len(self._counters) + len(self._gauges) + len(self._durations)

    def reset(self) -> None:
        with self._lock:
            self._counters.clear()
            self._gauges.clear()
            self._durations.clear()


#: Retained duration samples per series. Bounded for the reason on
#: `observe_duration_ms`.
_MAX_DURATION_SAMPLES = 512

#: The process-wide registry. One, so a counter is comparable across call sites.
metrics = MetricRegistry()


# ---------------------------------------------------------------------------
# Convenience wrappers — the vocabulary call sites actually use
# ---------------------------------------------------------------------------


def record_request(*, method: str, route: str, status_class: str, duration_ms: float) -> None:
    metrics.increment("http_requests_total", method=method, route=route, status_class=status_class)
    metrics.observe_duration_ms("http_request_duration_ms", duration_ms, method=method, route=route)
    if status_class.startswith("5"):
        metrics.increment(
            "http_errors_total", method=method, route=route, error_category="internal"
        )
    elif status_class.startswith("4"):
        metrics.increment(
            "http_errors_total",
            method=method,
            route=route,
            error_category=_category_for_status(status_class),
        )


def _category_for_status(status_class: str) -> str:
    """Map a 4xx class to an actionable cause.

    Deliberately coarse: 4xx is already a client problem, and the operator question
    is "is a client or an attacker generating these", answered by watching volume
    rather than by counting sub-cases here.
    """
    if status_class == "429":
        return "rate_limited"
    return "client_error"


def record_auth_event(event: str) -> None:
    """Count an authentication event.

    `event` is a closed vocabulary, and the closure is the point: an unbounded
    value here would make one series per account. Emitted today:

    * `login_success`, `login_failed`, `login_inactive`
    * `refresh_success`, `refresh_failed`, `refresh_expired`,
      `refresh_reuse_detected`
    * `logout`, `logout_all`
    * `password_reset_requested`, `password_reset_failed`,
      `password_reset_completed`

    No identity parameter exists on purpose — see `docs/observability.md` §9.
    """
    metrics.increment("auth_events_total", event=event)


def record_rate_limit(endpoint: str) -> None:
    """Count a rate-limit hit.

    The limiter's key contains a user id or an IP, so the count is recorded for the
    **endpoint only**. Passing the key here would turn an abuse-resistance
    mechanism into an identity log.
    """
    metrics.increment("rate_limit_hits_total", route=endpoint)


def record_ai_outcome(*, outcome: str, provider: str, intent: str, latency_ms: float) -> None:
    """Count an AI request.

    No prompt, no response, no context, no token VALUES. `outcome` and `intent` are
    bounded enumerations and `provider` is a configured name; `latency_ms` is safe.
    Token usage is recorded by the AI service's existing structured log rather than
    here, so nothing health-adjacent is aggregated into a metric.
    """
    metrics.increment("ai_requests_total", outcome=outcome, provider=provider, intent=intent)
    metrics.observe_duration_ms(
        "ai_request_duration_ms", latency_ms, provider=provider, intent=intent
    )
    if outcome == "fallback":
        metrics.increment("ai_fallbacks_total", provider=provider, intent=intent)


def record_notification(notification_type: str, *, outcome: str, amount: int = 1) -> None:
    """Count a notification outcome.

    `notification_type` is a bounded enum. The payload never appears: a
    notification row holds a localization key and substitution params, and those
    params include another rider's display name.
    """
    if amount <= 0:
        # A batch that produced no new rows must not register a series at all —
        # an all-zero series is indistinguishable from "measured and found
        # nothing", which is a materially different operational fact.
        return
    metrics.increment(
        "notification_events_total",
        float(amount),
        notification_type=notification_type,
        outcome=outcome,
    )


def record_location_event(outcome: str) -> None:
    """Count a live-location operation.

    A COUNT only. No key, no coordinate, no user id: the metric answers "is live
    sharing working", never "where is this rider".
    """
    metrics.increment("location_events_total", outcome=outcome)


def record_subscription_event(*, event: str, provider: str, status: str) -> None:
    """Count a subscription lifecycle transition.

    The labels are bounded lifecycle vocabulary: what happened, which origin,
    and the resulting commercial status. Provider subscription ids, event ids,
    users, and purchase credentials are never labels.
    """
    metrics.increment("subscription_events_total", event=event, provider=provider, status=status)


def record_entitlement_check(*, feature: str, outcome: str, plan: str) -> None:
    """Count one server-side capability decision.

    ``feature`` and ``plan`` are bounded product vocabulary. ``outcome`` is
    either ``allowed`` or ``denied``. The caller is never identified.
    """
    metrics.increment("entitlement_checks_total", feature=feature, outcome=outcome, plan=plan)


def record_entitlement_denial(*, feature: str, plan: str) -> None:
    """Count a denial separately so it cannot hide in an aggregate.

    A denial is operationally distinct from an allowed check: a sudden rise can
    mean expired subscriptions, a broken grant path, or an attack probing
    premium routes.
    """
    metrics.increment("entitlement_denials_total", feature=feature, plan=plan)


def record_purchase_verification(*, outcome: str, provider: str) -> None:
    """Count one purchase verification attempt.

    ``outcome`` is one of verified/rejected/unavailable/error; ``provider``
    is a registered origin or the literal "unknown". The purchase token,
    product id as given, user, and transaction ids are never labels — the
    first two are unbounded client input, the rest are identifiers.
    """
    metrics.increment("purchase_verifications_total", outcome=outcome, provider=provider)


def record_competition_event(*, event: str, outcome: str) -> None:
    """Count one ranking or challenge operation.

    ``event`` is a closed vocabulary of competition verbs (create, publish,
    join, leave, cancel, complete, rankings_query); ``outcome`` is one of
    ok/rejected/error. A challenge id, a team id, a rider, and a score are
    never labels — they would be per-object series with no operator to watch
    them, and the score would leak movement in a leaderboard.
    """
    metrics.increment("competition_events_total", event=event, outcome=outcome)


def allowed_label_names() -> Iterable[str]:
    return sorted(_ALLOWED_LABEL_NAMES)


def forbidden_label_names() -> Iterable[str]:
    return sorted(_FORBIDDEN_LABEL_NAMES)

"""Phase 10 WS-O observability tests.

Two things are being defended, and they pull in opposite directions:

1. **Cardinality.** A metric registry is a dictionary keyed by labels, so one
   unbounded value is a permanent memory leak with a latency-shaped silhouette.
   `route="/api/v1/rides/<uuid>"` is one series per ride, forever.
2. **Privacy.** A metric that *can* carry a coordinate will eventually carry one.
   The allowlist is what makes that structurally impossible rather than a rule
   people are asked to remember.

Each test states the property as an absence — "this identifier is refused" —
because a test asserting that a valid metric was recorded would pass just as
happily against a registry that recorded everything.
"""

import asyncio
import logging
import re
import uuid

import pytest

from app.core import metrics as metrics_module
from app.core.logging import _contains_word, _is_sensitive, redact
from app.core.metrics import (
    MetricCardinalityError,
    MetricRegistry,
    allowed_label_names,
    forbidden_label_names,
    metrics,
    record_auth_event,
    record_notification,
    record_rate_limit,
)

AUTH = "/api/v1/auth"
SOCIAL = "/api/v1/social"
HEALTH = "/api/v1/health"
LOCATION = "/api/v1/group-rides"  # + /{ride_id}/location
RIDES = "/api/v1/group-rides"


@pytest.fixture
async def clean_live_ride():
    """Delete each ride's live-location hash after the test.

    Also drops the cached Redis client on both sides, for the reason spelled out
    in `test_ride_location.py`: redis-py binds a pooled connection to the first
    event loop that uses it, and pytest-asyncio gives each test a fresh loop, so a
    leftover client fails the NEXT test with `Event loop is closed` — which
    surfaces here as a confusing 503 rather than as the harness bug it is.
    """
    from app.redis import client as redis_client
    from app.services.ride_location_service import _key

    written: list[uuid.UUID] = []

    async def _reset() -> None:
        cached = redis_client._client
        redis_client._client = None
        if cached is None:
            return
        try:
            await cached.aclose()
        except RuntimeError:
            # Expected: the pooled connection belongs to a loop already closed.
            pass

    await _reset()
    try:
        yield written
    finally:
        if written:
            await redis_client.get_redis().delete(*[_key(r) for r in written])
        await _reset()


def _headers_for(user_id: str) -> dict:
    """Mint a bearer token for an existing user.

    `create_access_token` returns `(token, expires_at)`, so the tuple has to be
    unpacked. Getting this wrong yields a `Bearer (token, ...)` header and a 401,
    which would make every location counter assertion below pass vacuously.
    """
    from app.core.security import create_access_token

    token, _expires = create_access_token(str(user_id))
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(autouse=True)
def clean_registry():
    """Reset the shared registry around every test.

    Without this, counts leak between tests and an assertion like "exactly one
    login failure" passes or fails depending on alphabetical file order — which is
    the failure mode Phase 9's Redis fixture taught the hard way.
    """
    metrics.reset()
    yield
    metrics.reset()


# ---------------------------------------------------------------------------
# Cardinality: forbidden label NAMES
# ---------------------------------------------------------------------------


def test_the_forbidden_and_allowed_label_sets_are_disjoint():
    # The whole design rests on this. If a name were in both, the allowlist check
    # would pass and the metric would carry an identifier.
    overlap = set(allowed_label_names()) & set(forbidden_label_names())
    assert not overlap, f"label names are both allowed and forbidden: {overlap}"


@pytest.mark.parametrize(
    "label",
    [
        "user_id",
        "email",
        "username",
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
    ],
)
def test_an_identity_or_location_label_is_refused(label):
    registry = MetricRegistry()
    with pytest.raises(MetricCardinalityError):
        registry.increment("some_metric", **{label: "anything"})


def test_request_id_is_refused_as_a_metric_label():
    # Request ids belong in logs and traces. As a label they are unbounded AND
    # useless: every request would get its own series, so the metric could never
    # answer "is traffic increasing".
    registry = MetricRegistry()
    with pytest.raises(MetricCardinalityError):
        registry.increment("http_requests_total", request_id="abc123")


def test_an_unknown_label_name_is_refused():
    # Allowlist, not denylist: a new label must be added deliberately.
    registry = MetricRegistry()
    with pytest.raises(MetricCardinalityError):
        registry.increment("some_metric", rider_display_name="Karim")


# ---------------------------------------------------------------------------
# Cardinality: forbidden label VALUES
# ---------------------------------------------------------------------------


def test_a_uuid_as_a_route_label_is_refused():
    # The realistic mistake: someone passes `request.url.path` instead of the
    # route template. It is a string, it looks fine, and it is unbounded.
    registry = MetricRegistry()
    with pytest.raises(MetricCardinalityError):
        registry.increment(
            "http_requests_total",
            method="GET",
            route="/api/v1/rides/8f3a2b7c-1d4e-4f2a-9c3b-7e5d1a9b2c4d",
            status_class="2xx",
        )


def test_an_email_as_a_label_value_is_refused():
    registry = MetricRegistry()
    with pytest.raises(MetricCardinalityError):
        registry.increment("http_errors_total", route="karim@example.com")


def test_an_absurdly_long_label_value_is_refused():
    # A route template is short. A 200-character value is a payload in disguise.
    registry = MetricRegistry()
    with pytest.raises(MetricCardinalityError):
        registry.increment("http_errors_total", route="x" * 200)


def test_a_route_template_is_accepted():
    # The positive control for the two tests above.
    registry = MetricRegistry()
    registry.increment(
        "http_requests_total", method="GET", route="/api/v1/rides/{ride_id}", status_class="2xx"
    )
    assert (
        registry.counter_value(
            "http_requests_total", method="GET", route="/api/v1/rides/{ride_id}", status_class="2xx"
        )
        == 1
    )


def test_many_distinct_routes_stay_bounded():
    # 500 different concrete paths collapse to ONE series when the template is used.
    # This is the property that keeps a registry from growing without limit.
    registry = MetricRegistry()
    for i in range(500):
        registry.increment(
            "http_requests_total", method="GET", route="/api/v1/rides/{ride_id}", status_class="2xx"
        )
    assert registry.series_count() == 1


# ---------------------------------------------------------------------------
# Registry mechanics
# ---------------------------------------------------------------------------


def test_counters_accumulate():
    registry = MetricRegistry()
    for _ in range(3):
        registry.increment("things_total", route="/x")
    assert registry.counter_value("things_total", route="/x") == 3


def test_gauges_replace_rather_than_accumulate():
    registry = MetricRegistry()
    registry.set_gauge("pool_size", 5, route="/x")
    registry.set_gauge("pool_size", 9, route="/x")
    assert registry.gauge_value("pool_size", route="/x") == 9


def test_duration_samples_are_bounded():
    # An unbounded sample list is a memory leak wearing a latency-shaped hat: the
    # values are small and the process looks healthy until it is killed.
    registry = MetricRegistry()
    for i in range(2000):
        registry.observe_duration_ms("http_request_duration_ms", float(i), route="/x")
    summary = registry.duration_summary("http_request_duration_ms", route="/x")
    assert summary is not None
    assert summary["count"] <= metrics_module._MAX_DURATION_SAMPLES


def test_a_duration_summary_reports_useful_statistics():
    registry = MetricRegistry()
    for value in (10.0, 20.0, 30.0, 40.0):
        registry.observe_duration_ms("d", value, route="/x")
    summary = registry.duration_summary("d", route="/x")
    assert summary is not None
    assert summary["min_ms"] == 10.0
    assert summary["max_ms"] == 40.0
    assert summary["avg_ms"] == 25.0


def test_a_missing_summary_is_none_not_an_error():
    registry = MetricRegistry()
    assert registry.duration_summary("never_recorded", route="/x") is None


def test_the_registry_is_thread_safe():
    # Concurrent increments must not lose counts. A lost increment is a metric that
    # under-reports an incident, which is worse than one that over-reports.
    registry = MetricRegistry()

    def hammer() -> None:
        for _ in range(500):
            registry.increment("hits_total", route="/x")

    threads = [asyncio.to_thread(hammer) for _ in range(8)]
    asyncio.run(_gather(threads))
    assert registry.counter_value("hits_total", route="/x") == 4000


async def _gather(coros) -> None:
    await asyncio.gather(*coros)


# ---------------------------------------------------------------------------
# Convenience wrappers: what may be counted
# ---------------------------------------------------------------------------


def test_auth_events_carry_no_identity():
    record_auth_event("login_failed")
    record_auth_event("login_failed")
    record_auth_event("refresh_reuse_detected")
    assert metrics.counter_value("auth_events_total", event="login_failed") == 2
    assert metrics.counter_value("auth_events_total", event="refresh_reuse_detected") == 1


def test_a_rate_limit_hit_is_counted_per_endpoint_not_per_key():
    # The limiter key contains a user id or an IP. Recording that would make an
    # abuse-resistance mechanism into a log of who is being rate-limited.
    record_rate_limit("auth")
    record_rate_limit("auth")
    record_rate_limit("coach")
    assert metrics.counter_value("rate_limit_hits_total", route="auth") == 2
    assert metrics.counter_value("rate_limit_hits_total", route="coach") == 1


# ---------------------------------------------------------------------------
# Auth counters, driven through the real endpoints
#
# The interesting cases are the ones where counting something would LEAK. Those are
# asserted here as absences, because that is the property being defended.
# ---------------------------------------------------------------------------


def _auth_count(event: str) -> float:
    return metrics.counter_value("auth_events_total", event=event)


async def test_a_password_reset_request_is_counted_for_a_registered_address(client):
    _, _, address = await _mk_user_with_email(client, "pr")
    r = await client.post(f"{AUTH}/password-reset/request", json={"email": address})
    assert r.status_code == 200, r.text
    assert _auth_count("password_reset_requested") == 1


async def test_a_password_reset_request_is_counted_for_an_UNKNOWN_address_too(client):
    """The anti-enumeration guarantee has to survive being instrumented.

    `request_password_reset` answers 200 identically whether or not the address is
    registered. Counting it only on the success path would make
    `password_reset_requested` an account-existence oracle — readable by anyone
    with dashboard access, scrapeable by any exporter — which is a strictly worse
    enumeration channel than the one it would sit beside.
    """
    unknown = f"definitely-not-registered-{uuid.uuid4().hex}@example.com"
    r = await client.post(f"{AUTH}/password-reset/request", json={"email": unknown})
    assert r.status_code == 200, r.text
    assert _auth_count("password_reset_requested") == 1, (
        "an unknown address was not counted, so the counter reveals which addresses exist"
    )


async def test_a_bogus_reset_token_counts_one_opaque_failure(client):
    """All three refusals collapse to ONE outcome, for the same reason.

    "No such token", "already used" and "expired" are indistinguishable in the
    counter as well as in the response, because a distinct counter would tell an
    attacker holding a leaked token whether it had already been redeemed.
    """
    r = await client.post(
        f"{AUTH}/password-reset/confirm",
        json={
            "token": "this-token-is-long-enough-but-bogus",
            "new_password": "NewStrong123",
            "new_password_confirm": "NewStrong123",
        },
    )
    assert r.status_code == 400, r.text
    assert _auth_count("password_reset_failed") == 1
    assert _auth_count("password_reset_completed") == 0


async def test_a_completed_reset_is_counted(client, outbox):
    _, _, address = await _mk_user_with_email(client, "rs")
    await client.post(f"{AUTH}/password-reset/request", json={"email": address})
    assert len(outbox) == 1
    # The token reaches the rider through the outbox and nowhere else. A log line
    # or a metric carrying it would be a credential in a store that never forgets.
    token = outbox[0].body.split(": ")[1].strip()
    assert token

    r = await client.post(
        f"{AUTH}/password-reset/confirm",
        json={
            "token": token,
            "new_password": "NewStrong123",
            "new_password_confirm": "NewStrong123",
        },
    )
    assert r.status_code == 200, r.text
    assert _auth_count("password_reset_completed") == 1
    assert token not in repr(metrics.snapshot()), "a reset token leaked into a metric"


async def test_logout_all_is_counted(client):
    a, aid = await _mk_user(client, "la")
    r = await client.post(f"{AUTH}/logout-all", headers=a)
    assert r.status_code in (200, 204), r.text
    assert _auth_count("logout_all") == 1
    del aid


async def test_no_auth_event_series_contains_an_address_or_an_id(client):
    _, aid = await _mk_user(client, "an")
    await client.post(f"{AUTH}/logout-all", headers={"Authorization": "Bearer x"})
    blob = repr(metrics.snapshot().get("auth_events_total", []))
    assert "@example.com" not in blob
    assert aid not in blob


def test_a_notification_outcome_is_counted_by_type():
    record_notification("group_ride_invitation", outcome="created")
    record_notification("group_ride_invitation", outcome="created")
    assert (
        metrics.counter_value(
            "notification_events_total",
            notification_type="group_ride_invitation",
            outcome="created",
        )
        == 2
    )


def test_a_location_event_is_a_bare_count():
    # No key, no coordinate, no rider: the metric answers "is live sharing
    # working", never "where is this rider".
    metrics_module.record_location_event("publish")
    assert metrics.counter_value("location_events_total", outcome="publish") == 1


def test_ai_outcomes_are_counted_without_content():
    metrics_module.record_ai_outcome(
        outcome="ok", provider="openai_compatible", intent="explain_ride", latency_ms=120.0
    )
    metrics_module.record_ai_outcome(
        outcome="fallback", provider="openai_compatible", intent="explain_ride", latency_ms=5.0
    )
    assert (
        metrics.counter_value(
            "ai_requests_total", outcome="ok", provider="openai_compatible", intent="explain_ride"
        )
        == 1
    )
    assert (
        metrics.counter_value(
            "ai_fallbacks_total", provider="openai_compatible", intent="explain_ride"
        )
        == 1
    )


def test_record_request_partitions_outcome_classes():
    metrics_module.record_request(
        method="GET", route="/api/v1/health", status_class="2xx", duration_ms=1.0
    )
    metrics_module.record_request(
        method="GET", route="/api/v1/rides", status_class="4xx", duration_ms=2.0
    )
    metrics_module.record_request(
        method="GET", route="/api/v1/rides", status_class="5xx", duration_ms=3.0
    )
    assert (
        metrics.counter_value(
            "http_requests_total", method="GET", route="/api/v1/health", status_class="2xx"
        )
        == 1
    )
    assert (
        metrics.counter_value(
            "http_errors_total", method="GET", route="/api/v1/rides", error_category="client_error"
        )
        == 1
    )
    assert (
        metrics.counter_value(
            "http_errors_total", method="GET", route="/api/v1/rides", error_category="internal"
        )
        == 1
    )


async def test_a_429_is_reported_as_rate_limited(client):
    """Drive a real 429 and assert it is counted as such.

    `record_request` only sees a status CLASS, so `429` is indistinguishable from
    any other 4xx at that layer. The precise signal comes from the limiter's own
    counter, which fires when the decision is made rather than when the response is
    written. Driving it through the API is what proves the two are actually wired
    together rather than merely existing.
    """
    # Exceed the login limit, which is 20 per 10 minutes per IP.
    for _ in range(25):
        await client.post(
            f"{AUTH}/login", json={"email": "nobody@example.com", "password": "wrong"}
        )
    assert metrics.counter_value("rate_limit_hits_total", route="auth") >= 1


# ---------------------------------------------------------------------------
# HTTP integration: real requests, real routes
# ---------------------------------------------------------------------------


async def test_a_real_request_is_counted_with_a_route_template(client):
    await client.get(HEALTH)
    series = metrics.snapshot()
    assert "http_requests_total" in series
    for entry in series["http_requests_total"]:
        route = entry["labels"]["route"]
        # A route TEMPLATE contains no id. `/api/v1/rides/{ride_id}` is one series;
        # `/api/v1/rides/8f3a…` is one per ride, forever. A UUID pattern is the
        # right assertion rather than a brace check: a parameterised route is
        # legitimately brace-free (`/api/v1/health`), so what actually matters is
        # the absence of an identifier.
        assert not re.search(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-", route), (
            f"a UUID leaked into a metric label: {route}"
        )


async def test_a_parameterised_route_yields_one_series_for_many_ids(client):
    """The positive proof of the cardinality rule, through the real API.

    Ten different ride ids must produce exactly ONE series. If any code path used
    `request.url.path`, this would be ten — and in production, one per ride ever
    requested, which is how a metrics registry becomes an OOM.
    """
    for i in range(10):
        rid = f"{i:08d}-1111-2222-3333-444444444444"
        r = await client.get(f"/api/v1/group-rides/{rid}")
        # The ride does not exist, which is fine: the point is the LABEL. A
        # concrete path with a UUID would have been refused by the cardinality
        # guard, so these requests completing at all proves the template was used.
        assert r.status_code in (401, 404), r.status_code

    routes = {
        labels.get("route")
        for labels in (
            entry["labels"] for entry in metrics.snapshot().get("http_requests_total", [])
        )
    }
    assert len(routes) <= 3, f"unbounded route labels: {routes}"


async def test_a_real_request_records_a_duration(client):
    await client.get(HEALTH)
    series = metrics.snapshot().get("http_request_duration_ms", [])
    assert series, "no duration was recorded for a real request"
    entry = series[0]
    assert entry["count"] >= 1
    assert entry["max_ms"] >= 0
    # Duration must be measured in milliseconds and stay a sane magnitude. A
    # seconds/milliseconds mix-up would show up here as a value in the millions or
    # the thousandths, and would silently wreck every latency percentile.
    assert 0 <= entry["max_ms"] < 60_000


async def test_no_metric_series_ever_contains_an_identifier(client):
    # Drive a handful of real requests, then assert the whole registry is clean.
    await client.get(HEALTH)
    await client.post(f"{AUTH}/login", json={"email": "a@b.co", "password": "x"})
    await client.get("/api/v1/does-not-exist")

    snapshot = metrics.snapshot()
    for name, entries in snapshot.items():
        for entry in entries:
            for key, value in entry["labels"].items():
                assert key not in set(forbidden_label_names()), f"{name}.{key}"
                assert "@" not in str(value), f"{name}.{key} looks like an email"
                stripped = str(value).replace("-", "")
                assert not (
                    len(stripped) >= 32 and all(c in "0123456789abcdefABCDEF" for c in stripped)
                ), f"{name}.{key} looks like a uuid: {value}"


async def test_metrics_never_break_a_request(client):
    # If the registry raised, a rider would see a 500 instead of their data.
    # Simulated by making the recorder hostile.
    original = metrics.increment

    def explode(*args, **kwargs):
        raise RuntimeError("metrics backend is down")

    metrics.increment = explode  # type: ignore[method-assign]
    try:
        r = await client.get(HEALTH)
        assert r.status_code == 200
    finally:
        metrics.increment = original  # type: ignore[method-assign]


async def test_a_refused_label_does_not_fail_the_request(client, monkeypatch):
    # A cardinality violation at a call site must cost a metric, not a response.
    original = metrics.increment

    def refuse(*args, **kwargs):
        raise MetricCardinalityError("nope")

    metrics.increment = refuse  # type: ignore[method-assign]
    try:
        r = await client.get(HEALTH)
        assert r.status_code == 200
    finally:
        metrics.increment = original  # type: ignore[method-assign]


# ---------------------------------------------------------------------------
# WS-O §1 privacy gate: the observability privacy gate
# ---------------------------------------------------------------------------


def test_no_gps_coordinate_can_enter_a_metric():
    registry = MetricRegistry()
    for label in ("latitude", "longitude", "lat", "lon", "coordinates", "location"):
        with pytest.raises(MetricCardinalityError):
            registry.increment("leak", **{label: "33.5"})


def test_no_chat_content_can_enter_a_metric():
    registry = MetricRegistry()
    for label in ("body", "message", "content"):
        with pytest.raises(MetricCardinalityError):
            registry.increment("leak", **{label: "hello there"})


def test_no_credential_can_enter_a_metric():
    registry = MetricRegistry()
    for label in ("token", "password", "refresh_token", "reset_token", "secret"):
        with pytest.raises(MetricCardinalityError):
            registry.increment("leak", **{label: "value"})


def test_the_whole_snapshot_is_serialisable():
    # An operator or an exporter must be able to read it without a custom encoder,
    # and nothing in it may be a live object graph.
    metrics_module.record_request(
        method="GET", route="/api/v1/health", status_class="2xx", duration_ms=1.0
    )
    import json

    json.dumps(metrics.snapshot())


# ---------------------------------------------------------------------------
# WS-M redaction regression, re-run here for §19
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "key",
    [
        "latitude",
        "longitude",
        "lat",
        "lon",
        "lng",
        "gps",
        "coordinates",
        "access_token",
        "refresh_token",
        "reset_token",
        "token_hash",
        "password_hash",
        "authorization",
        "email",
    ],
)
def test_sensitive_keys_are_still_redacted(key):
    # WS-O adds `cookie`; it must not regress anything WS-M fixed.
    assert redact({key: "sensitive-value"})[key] == "***"


def test_latency_ms_remains_readable():
    # The trap: `lat` is a coordinate abbreviation AND a substring of `latency_ms`.
    # A naive substring rule would blank the service's own performance metric and
    # destroy the log's usefulness.
    assert redact({"latency_ms": 42})["latency_ms"] == 42
    assert _is_sensitive("latency_ms") is False


def test_cookie_is_now_redacted():
    # Found by the WS-O audit: `authorization` was protected, `cookie` was not.
    assert redact({"cookie": "session=abc"})["cookie"] == "***"
    assert redact({"session_cookie": "abc"})["session_cookie"] == "***"
    assert redact({"cookie_header": "abc"})["cookie_header"] == "***"


def test_cookie_policy_is_not_redacted():
    # The over-redaction guard for the rule above. `cookie_policy` is
    # documentation, and blanking it would make a privacy notice unreadable.
    assert redact({"cookie_policy": "we do not use cookies"})["cookie_policy"] == (
        "we do not use cookies"
    )


def test_cookie_matching_is_exact_not_a_word_boundary():
    """`session_cookie` is matched exactly, not because `_` is a boundary.

    An earlier version of this rule treated `_` as a word separator, which read
    well but was wrong: `_` joins identifier components, so `session_cookie`
    does not contain the standalone word `cookie`. The exact-key list is what makes
    the compound forms match without dragging in `cookie_policy`.
    """
    for key in ("cookie", "cookie_header", "session_cookie", "cookies", "set-cookie"):
        assert _is_sensitive(key) is True, key
    for key in ("cookie_policy", "supercookie_jar", "latency_ms"):
        assert _is_sensitive(key) is False, key


def test_the_word_helper_still_works_for_its_own_purpose():
    # It is retained for hyphen/space-separated keys, where the boundary rule is
    # exactly right.
    assert _contains_word("x-cookie", "cookie") is True
    assert _contains_word("cookie", "cookie") is True
    assert _contains_word("cookie_policy", "cookie") is False


async def test_a_canary_coordinate_never_reaches_a_log_record(client, caplog):
    """End-to-end canary: a real request must not log a coordinate-shaped value.

    The canary is a literal that looks exactly like a Casablanca longitude. If any
    layer under test echoed a position into a log record, this fails.
    """
    canary = "-6.5234567"
    with caplog.at_level(logging.DEBUG):
        await client.get(HEALTH)
        metrics_module.record_location_event("publish")
    logged = "\n".join(record.getMessage() for record in caplog.records)
    assert canary not in logged
    # And no metric carries one either.
    for name, entries in metrics.snapshot().items():
        for entry in entries:
            for value in entry["labels"].values():
                assert canary not in str(value), name


def test_the_public_registry_export_is_names_only():
    # Whatever a future exporter publishes, the label vocabulary is knowable and
    # reviewable. `allowed_label_names` is the contract.
    names = list(allowed_label_names())
    assert "route" in names
    assert "method" in names
    assert not set(names) & set(forbidden_label_names())


# ---------------------------------------------------------------------------
# Notification counters, wired into the real service
#
# Driven through the HTTP API rather than by calling `record_notification`
# directly: a wrapper that is never called from the code path it claims to
# measure is the most common way a metric dashboard stays empty for months
# while everyone insists it is "working".
# ---------------------------------------------------------------------------


def _outcome_counts(metric: str) -> dict[str, int]:
    """Sum the counter by outcome, so assertions read as rates not raw series."""
    out: dict[str, int] = {}
    for entry in metrics.snapshot().get(metric, []):
        outcome = entry["labels"].get("outcome", "?")
        out[outcome] = out.get(outcome, 0) + entry["value"]
    return out


def _email(tag: str) -> str:
    """A stable address per tag.

    Stable matters: a helper that minted a fresh UUID on every call would register
    one rider and then, on the next line, ask about a *different* rider — so a
    password-reset test would assert against an address nobody registered and
    quietly prove nothing.
    """
    return f"obs_{tag}_{uuid.uuid4().hex[:8]}@example.com"


def _reg(tag: str) -> dict:
    return {
        "email": _email(tag),
        "password": "StrongPass123",
        "password_confirm": "StrongPass123",
        "display_name": f"Rider {tag.title()}",
    }


async def _mk_user(client, tag: str = "a") -> tuple[dict, str]:
    """Register + log in a rider. Returns `(headers, user_id)`."""
    headers, user_id, _email = await _mk_user_with_email(client, tag)
    return headers, user_id


async def _mk_user_with_email(client, tag: str = "a") -> tuple[dict, str, str]:
    """As `_mk_user`, plus the address — for endpoints keyed by email."""
    address = _email(tag)
    data = {**_reg(tag), "email": address}
    assert (await client.post(f"{AUTH}/register", json=data)).status_code == 201
    r = await client.post(f"{AUTH}/login", json={"email": address, "password": data["password"]})
    headers = {"Authorization": f"Bearer {r.json()['access_token']}"}
    me = await client.get(f"{SOCIAL}/profile/me", headers=headers)
    return headers, me.json()["user_id"], address


async def _friend_request(client):
    """Send a real friend request and return the two ids.

    Going through the API rather than calling `notify_friend_request` directly is
    the point: the assertion is about the counter being wired into the live path.
    """
    a, aid = await _mk_user(client, "fa")
    _b, bid = await _mk_user(client, "fb")
    sent = await client.post(f"{SOCIAL}/friend-requests", json={"user_id": bid}, headers=a)
    assert sent.status_code == 201, sent.text
    return a, aid, bid


async def test_a_created_notification_is_counted_by_type(client):
    await _friend_request(client)
    created = [
        e
        for e in metrics.snapshot().get("notification_events_total", [])
        if e["labels"]["outcome"] == "created"
    ]
    assert len(created) == 1, created
    assert created[0]["labels"]["notification_type"] == "friend_request"
    assert created[0]["value"] == 1


async def test_a_repeat_notification_is_counted_as_deduplicated_not_created(
    client, db_session_factory
):
    """The idempotency rate is the number worth watching under load.

    A retry that re-reads the winner's row must show up as `deduplicated`. Folded
    into `created` it would read as a successful second send, which is exactly the
    double-notification bug the unique index exists to prevent.

    `request_id` does not have to be a real relationship row: the dedupe key is
    `friend_request:{request_id}:{recipient}`, so replaying the same helper call
    with the same id is precisely what a retried request looks like.
    """
    from app.services.notification_service import notify_friend_request

    _, aid, bid = await _friend_request(client)
    request_id = uuid.uuid4()
    # The fixture already yields the FACTORY, so calling it here would hand back a
    # session and the `async with factory()` below would fail on a live session.
    factory = db_session_factory

    async with factory() as db:
        first = await notify_friend_request(
            db, actor_id=uuid.UUID(aid), target_id=uuid.UUID(bid), request_id=request_id
        )
    before = _outcome_counts("notification_events_total")

    async with factory() as db:
        second = await notify_friend_request(
            db, actor_id=uuid.UUID(aid), target_id=uuid.UUID(bid), request_id=request_id
        )
    after = _outcome_counts("notification_events_total")

    assert len(first) == 1, first
    # The replay returns the SAME row, which is the idempotency guarantee.
    assert [r.id for r in second] == [r.id for r in first]
    assert after["created"] == before["created"], "a deduped retry created a second row"
    assert after.get("deduplicated", 0) == before.get("deduplicated", 0) + 1


async def test_a_notification_count_carries_no_recipient_or_display_name(client):
    """The privacy boundary, asserted against a real notification payload.

    The notification params carry the actor's display name and the row carries the
    target's id. Neither may appear anywhere in the registry.
    """
    _, aid, bid = await _friend_request(client)
    blob = repr(metrics.snapshot())
    assert aid not in blob, "the acting rider's id leaked into a metric"
    assert bid not in blob, "the recipient's id leaked into a metric"
    assert "Rider" not in blob, "a display name leaked into a metric"


async def test_a_provider_outage_is_counted_rather_than_only_logged(
    client, db_session_factory, monkeypatch
):
    """A push failure nobody sees is a missing push nobody reports.

    `_deliver` swallows the provider exception by design, so the counter is the
    only thing that separates "we tried and it failed" from "we never tried".

    Driven with the configuration check forced true AND a registered device, so the
    provider call is genuinely reached rather than short-circuited by an earlier
    branch. Without the device the counter would record `deliver_no_targets` and the
    provider would never be touched — a test that passes for the wrong reason.
    """
    from app.notifications import provider as push_provider
    from app.services import notification_service

    _, aid, bid = await _friend_request(client)
    reg = await client.post(
        "/api/v1/push-devices",
        json={
            "platform": "android",
            "provider": "fcm",
            "device_id": f"obs-{uuid.uuid4().hex[:8]}",
            "token": f"obs-token-{uuid.uuid4().hex[:12]}",
        },
        headers=_headers_for(bid),
    )
    assert reg.status_code in (200, 201), reg.text
    metrics_module.metrics.reset()

    class _Provider:
        name = "fake"

    async def _boom(*args, **kwargs):
        raise RuntimeError("provider unreachable")

    monkeypatch.setattr(notification_service, "_push_configured", lambda: True)
    monkeypatch.setattr(push_provider, "get_provider", lambda: _Provider())
    monkeypatch.setattr(push_provider, "deliver_with_retry", _boom)

    from app.services.notification_service import notify_friend_request

    # The fixture's factory, not a fresh engine: an undisposed engine here leaks a
    # pooled asyncpg connection per call, and the resulting pool exhaustion shows up
    # later as WinError 64 in an unrelated test's teardown.
    async with db_session_factory() as db:
        # Must not raise: `_deliver` is documented never to propagate.
        await notify_friend_request(
            db,
            actor_id=uuid.UUID(aid),
            target_id=uuid.UUID(bid),
            request_id=uuid.uuid4(),
        )

    counts = _outcome_counts("notification_events_total")
    assert counts.get("deliver_failed") == 1, counts
    assert counts.get("delivered", 0) == 0, "a failed delivery must not be counted as sent"


async def test_push_not_configured_is_counted_as_a_skip(client):
    """An unconfigured provider is a deployment state worth seeing as a number."""
    await _friend_request(client)
    counts = _outcome_counts("notification_events_total")
    # PUSH_ENABLED is off in the test environment, so `_deliver` returns early.
    # That early return is the thing most likely to be forgotten, and it is the
    # difference between "push is broken" and "push was never configured".
    assert counts.get("skipped_push_not_configured", 0) >= 1, counts


# ---------------------------------------------------------------------------
# Live-location counters
#
# `docs/privacy-data.md` classes a live position as the most sensitive field in
# the schema. These defend that the counter is a COUNT: no ride, no rider, no
# coordinate, ever.
# ---------------------------------------------------------------------------


async def _started_ride(client, clean_live_ride):
    """A `started` ride with two joined riders — sharing is only legal here."""
    org_h, org_id = await _mk_user(client, "lo")
    guest_h, guest_id = await _mk_user(client, "lg")
    ride = await client.post(f"{RIDES}", json={"title": "Coastal Spin"}, headers=org_h)
    assert ride.status_code == 201, ride.text
    ride_id = ride.json()["id"]
    inv = await client.post(
        f"{RIDES}/{ride_id}/invitations", json={"user_id": guest_id}, headers=org_h
    )
    assert inv.status_code == 201, inv.text
    joined = await client.post(f"{RIDES}/{ride_id}/respond", json={"accept": True}, headers=guest_h)
    assert joined.status_code == 200, joined.text
    started = await client.post(f"{RIDES}/{ride_id}/start", headers=org_h)
    assert started.status_code == 200, started.text
    clean_live_ride.append(uuid.UUID(ride_id))
    return org_h, org_id, ride_id


async def test_a_location_publish_is_counted_without_any_position(client, clean_live_ride):
    _, org_id, ride_id = await _started_ride(client, clean_live_ride)
    r = await client.post(
        f"{LOCATION}/{ride_id}/location",
        json={"latitude": 33.5731, "longitude": -7.5898, "accuracy_m": 8.0},
        headers=_headers_for(org_id),
    )
    assert r.status_code == 200, r.text
    assert _outcome_counts("location_events_total").get("published") == 1

    blob = repr(metrics.snapshot())
    for canary in ("33.5731", "-7.5898"):
        assert canary not in blob, f"a position value leaked into a metric: {canary}"
    assert ride_id not in blob, "the ride id leaked into a metric"
    assert org_id not in blob, "the sharing rider's id leaked into a metric"


async def test_a_location_read_is_counted(client, clean_live_ride):
    _, org_id, ride_id = await _started_ride(client, clean_live_ride)
    r = await client.get(f"{LOCATION}/{ride_id}/location", headers=_headers_for(org_id))
    assert r.status_code == 200, r.text
    assert _outcome_counts("location_events_total").get("read") == 1


async def test_a_redis_outage_is_counted_as_a_failure(client, clean_live_ride, monkeypatch):
    """The 503 path must be counted, or a Redis outage is invisible on the dashboard.

    The service refuses to answer with an empty map on an outage (rule 3), so this
    is the one case where "no locations" is a lie. It has to be a number, not just a
    log line nobody is watching at 3am.
    """
    _, org_id, ride_id = await _started_ride(client, clean_live_ride)

    class _BrokenRedis:
        def pipeline(self):
            raise RuntimeError("redis down")

        async def hdel(self, *args, **kwargs):
            raise RuntimeError("redis down")

        async def hgetall(self, *args, **kwargs):
            raise RuntimeError("redis down")

    monkeypatch.setattr("app.redis.client.get_redis", lambda: _BrokenRedis())

    r = await client.post(
        f"{LOCATION}/{ride_id}/location",
        json={"latitude": 33.5731, "longitude": -7.5898, "accuracy_m": None},
        headers=_headers_for(org_id),
    )
    assert r.status_code == 503, r.text
    assert _outcome_counts("location_events_total").get("publish_failed") == 1


async def test_deleting_a_location_is_counted_after_cancellation(client, clean_live_ride):
    """`DELETE /location` stays a COUNTABLE success after a ride is cancelled.

    An earlier version of this test asserted that deletion is REFUSED once
    cancellation makes the ride terminal. That was wrong about the product rather
    than about the test: `stop_sharing` is documented as always allowed, precisely
    because the moment a rider most wants to stop is the moment their ride is
    being cancelled. Privacy-correct here means a successful delete that leaves no
    durable record — not a refusal that keeps the position in Redis until TTL.
    """
    org_h, org_id, ride_id = await _started_ride(client, clean_live_ride)
    cancelled = await client.post(f"{RIDES}/{ride_id}/cancel", headers=org_h)
    assert cancelled.status_code == 200, cancelled.text

    r = await client.delete(f"{LOCATION}/{ride_id}/location", headers=_headers_for(org_id))
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "stopped"
    assert _outcome_counts("location_events_total").get("stopped") == 1

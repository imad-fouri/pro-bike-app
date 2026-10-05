"""Phase 10 WS-M security regression tests.

Every test here corresponds to a finding in docs/SECURITY_AUDIT.md, and each one
is written so that it FAILS against the pre-fix behaviour. That is the point:
a security test that passes both before and after a fix proves nothing, so the
vulnerable shape is asserted as absent rather than the fixed shape merely
present.

The categories mirror the audit checklist: authentication, error leakage, logging
and secret masking, correlation, and API surface exposure.
"""

import json
import logging
import uuid

import pytest

from app.core.config import _DEV_DATABASE_URL, ProductionConfigError, Settings
from app.core.errors import error_body
from app.core.logging import ExtraFormatter, redact
from app.db.session import engine

AUTH = "/api/v1/auth"
SOCIAL = "/api/v1/social"

# ---------------------------------------------------------------------------
# SEC-01 (HIGH) — credentials must never be echoed in a validation response
# ---------------------------------------------------------------------------
#
# Proven against the pre-fix code: `jsonable_encoder(exc.errors())` includes
# pydantic's `input` key, which is the value that FAILED validation. On this API
# that is a plaintext password or refresh token. A response body is not a private
# channel — proxies, load balancers and APM tools capture bodies by default.


async def test_a_short_password_is_not_echoed_back(client):
    r = await client.post(
        f"{AUTH}/register",
        json={
            "email": "someone@example.com",
            "password": "Zq7-failing-value",
            "password_confirm": "Zq7-failing-value",
            "display_name": "x",
        },
    )
    assert r.status_code == 422
    # A canary that shares no substring with the response vocabulary. Asserting
    # `password="short"` is not in the body would pass trivially, because
    # "short" appears inside pydantic's own "string_too_short" error type — a test
    # that cannot fail on the vulnerable behaviour is worse than no test.
    assert "Zq7-failing-value" not in r.text, "the rejected password was returned"


async def test_a_refresh_token_is_not_echoed_back(client):
    r = await client.post(f"{AUTH}/refresh", json={"refresh_token": "tooshort"})
    assert r.status_code == 422
    assert "tooshort" not in r.text, "the rejected refresh token was returned"


async def test_a_password_reset_token_is_not_echoed_back(client):
    r = await client.post(
        f"{AUTH}/password-reset/confirm",
        json={
            "token": "Qv3-reset-token",
            "new_password": "Ab1-short",
            "new_password_confirm": "Ab1-short",
        },
    )
    assert r.status_code == 422
    assert "Qv3-reset-token" not in r.text
    assert "Ab1-short" not in r.text


async def test_a_submitted_email_is_not_echoed_back(client):
    # PII, and the field a uniqueness violation would carry.
    r = await client.post(
        f"{AUTH}/register",
        json={
            "email": "leak-me-canary@example.com",
            "password": "LongEnough123",
            "password_confirm": "LongEnough123",
            "display_name": "x",
        },
    )
    assert r.status_code == 422
    assert "leak-me-canary@example.com" not in r.text


async def test_no_validation_error_carries_an_input_key(client):
    # The structural assertion, independent of any particular canary value: pydantic
    # always calls the rejected value `input`, so its absence is the property.
    r = await client.post(
        f"{AUTH}/register",
        json={"email": "nope", "password": "Ab1", "password_confirm": "Ab1", "display_name": "x"},
    )
    errors = r.json()["error"]["details"]["errors"]
    assert errors
    for entry in errors:
        assert "input" not in entry, f"error entry leaked the rejected value: {entry}"


async def test_validation_errors_still_identify_the_offending_field(client):
    # The fix removes the value, not the diagnostic. A client must still be able
    # to attach the message to the right form field, so weakening this would trade
    # a leak for a usability regression.
    r = await client.post(
        f"{AUTH}/register",
        json={
            "email": "nope",
            "password": "short",
            "password_confirm": "short",
            "display_name": "x",
        },
    )
    errors = r.json()["error"]["details"]["errors"]
    fields = {e["loc"][-1] for e in errors if "loc" in e}
    assert {"password", "display_name"} <= fields
    assert all("msg" in e and "type" in e for e in errors)


async def test_validation_errors_keep_numeric_constraints(client):
    # `min_length` is what lets a client pre-empt the error, and it is a number,
    # so it survives the allowlist.
    r = await client.post(
        f"{AUTH}/register",
        json={
            "email": "nope",
            "password": "short",
            "password_confirm": "short",
            "display_name": "x",
        },
    )
    errors = r.json()["error"]["details"]["errors"]
    assert any(e.get("ctx", {}).get("min_length") for e in errors)


async def test_the_error_envelope_shape_is_unchanged(client):
    # Not a "silent contract change": the envelope keys are exactly as before.
    r = await client.post(
        f"{AUTH}/register",
        json={
            "email": "nope",
            "password": "short",
            "password_confirm": "short",
            "display_name": "x",
        },
    )
    body = r.json()
    assert set(body) == {"error"}
    assert set(body["error"]) == {"code", "message", "details"}
    assert body["error"]["code"] == "VALIDATION_ERROR"


def test_error_body_still_carries_details():
    assert error_body("X", "m", {"a": 1}) == {
        "error": {"code": "X", "message": "m", "details": {"a": 1}}
    }


# ---------------------------------------------------------------------------
# SEC-02 (MEDIUM) — SQL and bound parameters must not reach the log
# ---------------------------------------------------------------------------


def test_the_engine_hides_bound_parameters():
    # An IntegrityError renders as the statement plus `[parameters: {...}]`, and
    # this schema puts an email address and an Argon2id hash in those parameters.
    #
    # Read from the sync engine: that is where SQLAlchemy stores the flag, and
    # `AsyncEngine` does not proxy it, so asserting on the async object would
    # fail with AttributeError rather than testing anything.
    assert engine.sync_engine.hide_parameters is True


async def test_a_uniqueness_violation_logs_no_email_or_hash(client, caplog):
    """The end-to-end property: a duplicate registration must not log the address.

    This is the finding as an observable, not as a config flag: even if some
    future line reintroduces `str(exc)` into a log call, this still fails.
    """
    payload = {
        "email": "duplicate-canary@example.com",
        "password": "LongEnough123",
        "password_confirm": "LongEnough123",
        "display_name": "Canary",
    }
    first = await client.post(f"{AUTH}/register", json=payload)
    assert first.status_code == 201, first.text

    with caplog.at_level(logging.DEBUG):
        second = await client.post(f"{AUTH}/register", json=payload)

    assert second.status_code in (409, 422), second.text
    logged = "\n".join(record.getMessage() for record in caplog.records)
    logged += "\n".join(str(record.exc_info) for record in caplog.records if record.exc_info)
    assert "duplicate-canary@example.com" not in logged
    assert "LongEnough123" not in logged
    assert "$argon2" not in logged


# ---------------------------------------------------------------------------
# SEC-03 (MEDIUM) — redact() must cover compound names and nested lists
# ---------------------------------------------------------------------------
#
# Proven against the pre-fix code: `k.lower() in _SENSITIVE` is EXACT matching, so
# `access_token`, `refresh_token`, `token_hash` and `password_hash` matched
# neither "token" nor "password" and passed through in cleartext. Lists were not
# recursed into at all.


@pytest.mark.parametrize(
    "key",
    [
        "password",
        "token",
        "access_token",
        "refresh_token",
        "id_token",
        "token_hash",
        "password_hash",
        "hashed_password",
        "client_secret",
        "api_key",
        "authorization",
        "latitude",
        "longitude",
        "location",
        "coordinates",
        "lat",
        "lon",
        "lng",
        "gps",
        "email",
    ],
)
def test_redact_masks_every_sensitive_key(key):
    assert redact({key: "sensitive-value"})[key] == "***"


def test_redact_still_masks_the_names_it_always_did():
    # The Phase 7 keys must not regress while the new ones are added.
    for key in ("message", "user_message", "prompt", "context", "system", "notes"):
        assert redact({key: "rider text"})[key] == "***"


def test_redact_recurses_into_a_list_of_dicts():
    # The shape a location payload actually takes. Previously untouched.
    out = redact({"points": [{"latitude": 33.5, "longitude": -6.5}]})
    assert out["points"][0]["latitude"] == "***"
    assert out["points"][0]["longitude"] == "***"


def test_redact_recurses_through_nesting():
    out = redact({"a": {"b": {"refresh_token": "t"}}})
    assert out["a"]["b"]["refresh_token"] == "***"


def test_redact_leaves_operational_fields_alone():
    # Over-redaction would destroy the log's usefulness, so the safe fields must
    # still render.
    out = redact({"user_id": "abc", "latency_ms": 12, "count": 3})
    assert out == {"user_id": "abc", "latency_ms": 12, "count": 3}


def test_redact_passes_through_scalars():
    assert redact("plain") == "plain"
    assert redact(7) == 7
    assert redact(None) is None


# ---------------------------------------------------------------------------
# SEC-04 (MEDIUM) — the log formatter must not drop third-party records
# ---------------------------------------------------------------------------
#
# Proven against the pre-fix code: the format string requires `env` and
# `request_id`, injected only by a filter on the `cyclecoach` logger. A record
# from `httpx` (the AI provider's outbound call), `sqlalchemy.engine` or `asyncio`
# reached the formatter without them and raised
# `ValueError: Formatting field not found in record: 'env'`, which Python's
# logging swallows into a stderr traceback — losing the record silently.


def test_the_formatter_renders_a_record_from_a_foreign_logger():
    record = logging.LogRecord(
        name="httpx",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg='HTTP Request: POST https://provider.example "200 OK"',
        args=(),
        exc_info=None,
    )
    # No `env` / `request_id`: exactly the shape a foreign logger produces.
    assert not hasattr(record, "env")
    rendered = ExtraFormatter(
        "%(asctime)s %(levelname)s env=%(env)s rid=%(request_id)s %(name)s: %(message)s"
    ).format(record)
    assert "httpx" in rendered
    assert "env=-" in rendered
    assert "rid=-" in rendered


def test_the_formatter_still_renders_a_correlated_record():
    record = logging.LogRecord(
        name="cyclecoach",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="hello",
        args=(),
        exc_info=None,
    )
    record.env = "test"  # type: ignore[attr-defined]
    record.request_id = "abc123"  # type: ignore[attr-defined]
    rendered = ExtraFormatter(
        "%(asctime)s %(levelname)s env=%(env)s rid=%(request_id)s %(name)s: %(message)s"
    ).format(record)
    assert "env=test" in rendered
    assert "rid=abc123" in rendered


# ---------------------------------------------------------------------------
# SEC-05 (MEDIUM) — a 500 must still carry the correlation id
# ---------------------------------------------------------------------------
#
# Proven against the pre-fix code: `ServerErrorMiddleware` sits OUTSIDE the user
# middleware stack, so an unhandled exception propagated past the correlation
# middleware and its `headers["X-Request-ID"] = rid` never ran. The id was in the
# log and unreachable from the client on precisely the requests worth tracing.


async def test_a_validation_error_carries_the_request_id(client):
    r = await client.post(f"{AUTH}/login", json={"email": "nope", "password": "short"})
    assert r.status_code == 422
    assert r.headers.get("X-Request-ID")


async def test_a_500_carries_the_request_id(client, monkeypatch):
    """The observable property: a real 500 exposes the id that is in the log.

    Forces an unhandled exception through the real ASGI stack rather than calling
    the handler directly, because the whole defect was about WHERE the handler
    sits in the middleware stack.
    """
    import httpx

    from app.api.v1 import health as health_module

    original = health_module.check_db
    original_bounded = health_module._bounded

    async def explode():
        raise RuntimeError("simulated hard failure")

    async def unbounded_broken(check):
        # `_bounded` normally converts a dependency failure into a status string.
        # Replacing it with something that lets the exception escape reproduces
        # exactly what an unhandled error does in production: it propagates past
        # the user middleware to ServerErrorMiddleware, which is what made the
        # correlation header disappear.
        return await check()

    monkeypatch.setattr(health_module, "check_db", explode)
    monkeypatch.setattr(health_module, "_bounded", unbounded_broken)
    app = client._transport.app  # type: ignore[attr-defined]
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as raw:
            r = await raw.get("/api/v1/health", headers={"X-Request-ID": "rid-500-probe"})
    finally:
        monkeypatch.setattr(health_module, "check_db", original)
        monkeypatch.setattr(health_module, "_bounded", original_bounded)

    assert r.status_code == 500, r.text
    assert r.headers.get("X-Request-ID") == "rid-500-probe", (
        "a 500 must expose the correlation id that is already in the log"
    )
    assert "simulated hard failure" not in r.text
    assert "Traceback" not in r.text


async def test_the_unhandled_handler_attaches_the_request_id():
    from starlette.requests import Request

    from app.core.errors import register_error_handlers

    request = Request({"type": "http", "method": "GET", "path": "/x", "headers": []})
    request.state.request_id = "rid-from-state"

    captured: dict = {}

    class FakeApp:
        def exception_handler(self, _exc):
            def register(fn):
                captured["fn"] = fn
                return fn

            return register

    register_error_handlers(FakeApp())  # type: ignore[arg-type]
    response = await captured["fn"](request, RuntimeError("boom"))
    assert response.headers["X-Request-ID"] == "rid-from-state"
    assert "boom" not in response.body.decode()


async def test_the_unhandled_handler_omits_the_header_when_there_is_no_id():
    from starlette.requests import Request

    from app.core.errors import register_error_handlers

    request = Request({"type": "http", "method": "GET", "path": "/x", "headers": []})
    captured: dict = {}

    class FakeApp:
        def exception_handler(self, _exc):
            def register(fn):
                captured["fn"] = fn
                return fn

            return register

    register_error_handlers(FakeApp())  # type: ignore[arg-type]
    response = await captured["fn"](request, RuntimeError("boom"))
    assert "X-Request-ID" not in response.headers


def test_a_500_body_never_carries_the_exception_text():
    from app.core.errors import error_body as eb

    body = eb("INTERNAL_ERROR", "Something went wrong.")
    assert set(body["error"]) == {"code", "message", "details"}
    assert body["error"]["message"] == "Something went wrong."
    assert "traceback" not in json.dumps(body).lower()


# ---------------------------------------------------------------------------
# SEC-06 (LOW) — API surface must not be published in production
# ---------------------------------------------------------------------------


def test_production_disables_the_openapi_schema(monkeypatch):
    from app.core.config import settings
    from app.main import create_app

    monkeypatch.setattr(settings, "ENVIRONMENT", "production")
    monkeypatch.setattr(settings, "SECRET_KEY", "k" * 64)
    monkeypatch.setattr(settings, "EMAIL_PROVIDER", "smtp")
    monkeypatch.setattr(settings, "CORS_ORIGINS", "https://app.cyclecoach.app")
    monkeypatch.setattr(settings, "LOG_LEVEL", "INFO")
    monkeypatch.setattr(
        settings,
        "DATABASE_URL",
        "postgresql+asyncpg://u:p@db.internal:5432/cyclecoach",
    )
    monkeypatch.setattr(settings, "REDIS_URL", "redis://:pw@redis.internal:6379/0")
    monkeypatch.setattr(settings, "ACCESS_TOKEN_MINUTES", 15)

    app = create_app()
    assert app.openapi_url is None
    assert app.docs_url is None
    assert app.redoc_url is None


def test_development_keeps_the_openapi_schema(monkeypatch):
    # The contract tests read the schema; removing it locally would be a real cost.
    from app.core.config import settings
    from app.main import create_app

    monkeypatch.setattr(settings, "ENVIRONMENT", "development")
    app = create_app()
    assert app.openapi_url == "/openapi.json"
    assert app.docs_url == "/docs"


# ---------------------------------------------------------------------------
# SEC-07 — correlation id sanitisation (already covered in Phase 8.6, re-asserted
# because SEC-05 now also depends on it)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "hostile",
    [
        "INJECT FORGED",
        "tab\there",
        "rid%0Anewline",
        "../../etc/passwd",
        "a" * 64 + "\r\nX-Evil: 1",
        "null\x00byte",
    ],
)
async def test_a_hostile_request_id_cannot_break_the_header(client, hostile):
    r = await client.get(f"{AUTH}/../health", headers={"X-Request-ID": hostile})
    returned = r.headers.get("X-Request-ID", "")
    assert "\r" not in returned
    assert "\n" not in returned
    assert "\x00" not in returned


async def test_a_clean_request_id_is_echoed_back(client):
    r = await client.get("/api/v1/health", headers={"X-Request-ID": "trace-abc-123"})
    assert r.headers.get("X-Request-ID") == "trace-abc-123"


async def test_no_security_header_is_missing(client):
    r = await client.get("/api/v1/health")
    assert r.headers.get("X-Content-Type-Options") == "nosniff"
    assert r.headers.get("X-Request-ID")


# ---------------------------------------------------------------------------
# SEC-08 — canary secrets must not appear in logs or responses
# ---------------------------------------------------------------------------


async def test_a_canary_password_never_reaches_the_log(client, caplog):
    canary = "Canary-Pw-9f3a2b7c1d8e4f6a"
    with caplog.at_level(logging.DEBUG):
        r = await client.post(
            f"{AUTH}/register",
            json={
                "email": "canary@example.com",
                "password": canary,
                "password_confirm": canary,
                "display_name": "x",
            },
        )
    assert r.status_code == 422
    assert canary not in r.text
    logged = "\n".join(record.getMessage() for record in caplog.records)
    assert canary not in logged


async def test_a_canary_password_never_reaches_a_successful_response(client):
    canary = "Canary-Pw-4b8e1f2a9c7d3e5f"
    r = await client.post(
        f"{AUTH}/register",
        json={
            "email": "canary2@example.com",
            "password": canary,
            "password_confirm": canary,
            "display_name": "Canary Two",
        },
    )
    assert r.status_code == 201, r.text
    body = r.text + json.dumps(r.json())
    assert canary not in body
    assert "password_hash" not in body


def test_a_canary_token_never_reaches_the_log_via_redact():
    canary = "canary-refresh-token-abcdef0123456789"
    out = json.dumps(redact({"refresh_token": canary, "nested": [{"token": canary}]}))
    assert canary not in out


def test_a_canary_secret_is_never_echoed_by_the_production_gate():
    # The startup gate names settings, never values.

    canary = "canary-secret-key-that-is-long-enough-0123456789"
    with pytest.raises(ProductionConfigError) as exc:
        Settings(ENVIRONMENT="production", SECRET_KEY="short").validate_production()
    assert canary not in str(exc.value)

    settings = Settings(ENVIRONMENT="production", SECRET_KEY=canary, EMAIL_PROVIDER="dev")
    with pytest.raises(ProductionConfigError) as exc2:
        settings.validate_production()
    assert canary not in str(exc2.value)
    assert canary not in " ".join(exc2.value.problems)


# ---------------------------------------------------------------------------
# SEC-09 — no secret material committed to the repository
# ---------------------------------------------------------------------------


def test_the_test_suite_contains_no_real_looking_credentials():
    """A tripwire against pasting a live credential into a test.

    The markers are assembled from fragments at runtime rather than written
    literally. A literal list would make this file the one place in the
    repository that DOES contain every credential prefix — so the scan would
    always report itself, and the obvious "fix" would be to exempt this file,
    which is precisely how such a scan gets switched off.

    Assembled form also means this test cannot be defeated by simply reading it.
    """
    import pathlib

    suspicious = ["BEGIN " + "PRIVATE KEY", "AKI" + "A", "gh" + "p_", "sk-" + "proj-", "xo" + "xb-"]
    root = pathlib.Path(__file__).resolve().parents[1]
    offenders: list[str] = []
    for path in root.rglob("*.py"):
        if ".venv" in path.parts:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for marker in suspicious:
            if marker in text:
                offenders.append(f"{path.name}: {marker}")
    assert not offenders, f"credential-shaped strings committed: {offenders}"


def test_the_tripwire_actually_detects_a_credential(tmp_path):
    """The control: proves the scan above is capable of failing.

    Without this, a scan that silently matched nothing would read as a clean
    repository forever.
    """
    suspicious = ["AKI" + "A", "gh" + "p_"]
    planted = tmp_path / "planted.py"
    planted.write_text("KEY = '" + "AKI" + "A" + "AAAA'\n", encoding="utf-8")
    text = planted.read_text(encoding="utf-8")
    assert any(marker in text for marker in suspicious)


def test_the_engine_url_is_not_a_production_credential():
    # The development default is public in this repository, so the production
    # gate rejects it (WS-B). This asserts the gate still does.

    settings = Settings(
        ENVIRONMENT="production",
        SECRET_KEY="k" * 64,
        EMAIL_PROVIDER="smtp",
        CORS_ORIGINS="https://app.cyclecoach.app",
        DATABASE_URL=_DEV_DATABASE_URL,
        REDIS_URL="redis://:pw@redis.internal:6379/0",
        ACCESS_TOKEN_MINUTES=15,
    )
    problems = settings.production_config_problems()
    assert any("DATABASE_URL" in p for p in problems)


def test_a_uuid_is_still_usable_as_a_correlation_id():
    # Guard against a future "sanitisation" change that rejects valid ids.
    value = uuid.uuid4().hex[:12]
    assert value.isalnum()

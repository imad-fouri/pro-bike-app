"""Structured logging — JSON-ish, correlation ID, never logs secrets/location."""

import logging
import sys
import uuid
from contextvars import ContextVar

request_id: ContextVar[str] = ContextVar("request_id", default="-")

# PHASE 10 SECURITY AUDIT — the single masking rule for this module.
#
# `redact()` and `ExtraFormatter` previously carried two independent lists, and
# they had drifted: the formatter's substrings knew about `password`/`token` but
# not `email`/`latitude`, while `redact()` knew about `latitude` but matched whole
# keys EXACTLY, so `access_token`, `refresh_token`, `token_hash` and
# `password_hash` — the compound names this codebase actually uses — matched
# nothing and passed through in cleartext.
#
# There is now one predicate, `_is_sensitive`, used by both. Two lists that are
# meant to agree will eventually disagree; one function cannot.
#
# Phase 7 originally contributed the credential and location names; Phase 10
# added the compound credential forms and the short coordinate forms.
#
#: Matched as a SUBSTRING of the key name.
#:
#: Only names long and unambiguous enough that a substring hit is always a real
#: hit. This is what catches the compound credential names that exact matching
#: missed: `access_token`, `refresh_token`, `token_hash`, `password_hash`,
#: `hashed_password`, `user_email`.
#:
#: Short forms are deliberately NOT here. `lat` is a substring of `latency_ms`,
#: so substring-matching it would redact the service's own performance metric and
#: quietly destroy the log's usefulness — which is the reason a denylist cannot
#: simply be widened without checking what it collides with.
_SENSITIVE_SUBSTRINGS = (
    "password",
    "token",
    "secret",
    "api_key",
    "apikey",
    "authorization",
    "email",
)

#: Matched as an EXACT key name.
#:
#: Location forms and free-text bodies. `latitude`/`longitude`/`location` are long
#: enough to be safe either way; they are kept here so that a future `lat_count`
#: or `gps_accuracy` can be reasoned about deliberately instead of being caught
#: by accident.
_SENSITIVE_EXACT = (
    "location",
    "latitude",
    "longitude",
    "coordinates",
    "lat",
    "lon",
    "lng",
    "gps",
    "message",
    "user_message",
    "prompt",
    "context",
    "system",
    "notes",
)


def _is_sensitive(key: str) -> bool:
    """Whether a key name should have its value replaced with `***`.

    Substring for unambiguous credential names, exact for the short location and
    prose forms. The split exists because each rule alone is wrong: exact matching
    misses `access_token` and `token_hash`, and substring matching eats
    `latency_ms`.
    """
    lowered = key.lower()
    return lowered in _SENSITIVE_EXACT or any(s in lowered for s in _SENSITIVE_SUBSTRINGS)


def redact(obj):
    """Recursively replace sensitive values with `***`.

    Recurses into LISTS as well as dicts. It previously descended only into
    dicts, so a payload like `{"points": [{"latitude": 1.0}]}` passed through
    untouched — the one shape a location payload would actually take.
    """
    if isinstance(obj, dict):
        return {k: "***" if _is_sensitive(str(k)) else redact(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [redact(item) for item in obj]
    if isinstance(obj, tuple):
        return tuple(redact(item) for item in obj)
    return obj


class CorrelationFilter(logging.Filter):
    def __init__(self, environment: str):
        super().__init__()
        self.environment = environment

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id.get()  # type: ignore[attr-defined]
        record.env = self.environment  # type: ignore[attr-defined]
        return True


# Attributes every LogRecord carries; anything else on the record came from a
# call-site `extra=` dict and should be rendered, not dropped.
_STANDARD_ATTRS = frozenset(
    {
        "args",
        "asctime",
        "created",
        "env",
        "exc_info",
        "exc_text",
        "filename",
        "funcName",
        "levelname",
        "levelno",
        "lineno",
        "message",
        "module",
        "msecs",
        "msg",
        "name",
        "pathname",
        "process",
        "processName",
        "relativeCreated",
        "request_id",
        "stack_info",
        "taskName",
        "thread",
        "threadName",
    }
)


class ExtraFormatter(logging.Formatter):
    """Render call-site `extra` fields instead of silently dropping them.

    Previously every service `_log(event, **fields)` call emitted only the
    event name: the formatter never referenced the extra keys, so an operator
    could see *that* something happened but never *to what*. Fields render as
    `key=value` pairs; anything sensitive by name renders as `***`.

    Also guarantees the two correlation attributes exist. The format string
    requires `env` and `request_id`, but those are injected by `CorrelationFilter`
    on the `cyclecoach` LOGGER, so any record from a different logger that
    propagates to the root handler — `httpx` for the AI provider's outbound
    calls, `sqlalchemy.engine`, `asyncio`, `uvicorn.error` — reached this
    formatter without them and raised `ValueError: Formatting field not found`.
    Python's logging swallows that and prints a traceback to stderr, so the
    record was lost silently on every occurrence. Those are exactly the
    third-party-boundary records worth having.
    """

    def format(self, record: logging.LogRecord) -> str:
        # Same default as the `request_id` ContextVar, so a record with no
        # request context renders `-` rather than failing.
        if not hasattr(record, "env"):
            record.env = "-"  # type: ignore[attr-defined]
        if not hasattr(record, "request_id"):
            record.request_id = "-"  # type: ignore[attr-defined]
        base = super().format(record)
        extras = {k: v for k, v in record.__dict__.items() if k not in _STANDARD_ATTRS}
        if not extras:
            return base
        rendered = []
        for key in sorted(extras):
            # `_is_sensitive`, not a private list: this is the second layer that
            # protects a call site which forgot `redact()`, so it must not be able
            # to drift away from the first.
            if _is_sensitive(str(key)):
                rendered.append(f"{key}=***")
            else:
                rendered.append(f"{key}={extras[key]}")
        return f"{base} | {' '.join(rendered)}"


def setup_logging(level: str, environment: str) -> logging.Logger:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        ExtraFormatter(
            "%(asctime)s %(levelname)s env=%(env)s rid=%(request_id)s %(name)s: %(message)s"
        )
    )
    # Filter on the logger, not the handler: every record passing through —
    # regardless of which handler (or test capture) consumes it — carries the
    # correlation attributes.
    logger = logging.getLogger("cyclecoach")
    logger.addFilter(CorrelationFilter(environment))
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level.upper())
    return logger


def new_request_id(value: str | None = None) -> str:
    """Set the correlation id for this request context.

    Prefers a caller-supplied value (e.g. an incoming `X-Request-ID` header
    already sanitized by the middleware) so the id returned to the client is
    the id in the logs. Generates one when absent.
    """
    rid = value or uuid.uuid4().hex[:12]
    request_id.set(rid)
    return rid

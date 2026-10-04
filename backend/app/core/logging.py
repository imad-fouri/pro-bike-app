"""Structured logging — JSON-ish, correlation ID, never logs secrets/location."""

import logging
import sys
import uuid
from contextvars import ContextVar

request_id: ContextVar[str] = ContextVar("request_id", default="-")

_SENSITIVE = (
    "password",
    "token",
    "secret",
    "location",
    "latitude",
    "longitude",
    # Phase 7 — the coach layer's raw material. These are redacted by key so a
    # payload logged by mistake cannot leak rider text or a provider key.
    "api_key",
    "apikey",
    "authorization",
    "message",
    "user_message",
    "prompt",
    "context",
    "system",
    "notes",
)


def redact(obj: dict) -> dict:
    if isinstance(obj, dict):
        return {
            k: "***" if k.lower() in _SENSITIVE else redact(v) if isinstance(v, dict) else v
            for k, v in obj.items()
        }
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

# Defense in depth: even if a call site forgets redact(), these never render.
_SENSITIVE_SUBSTRINGS = ("password", "token", "secret", "api_key", "apikey", "authorization")


class ExtraFormatter(logging.Formatter):
    """Render call-site `extra` fields instead of silently dropping them.

    Previously every service `_log(event, **fields)` call emitted only the
    event name: the formatter never referenced the extra keys, so an operator
    could see *that* something happened but never *to what*. Fields render as
    `key=value` pairs; anything sensitive by name renders as `***`.
    """

    def format(self, record: logging.LogRecord) -> str:
        base = super().format(record)
        extras = {k: v for k, v in record.__dict__.items() if k not in _STANDARD_ATTRS}
        if not extras:
            return base
        rendered = []
        for key in sorted(extras):
            if any(s in key.lower() for s in _SENSITIVE_SUBSTRINGS):
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

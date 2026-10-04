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


def setup_logging(level: str, environment: str) -> logging.Logger:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        logging.Formatter(
            "%(asctime)s %(levelname)s env=%(env)s rid=%(request_id)s %(name)s: %(message)s"
        )
    )
    handler.addFilter(CorrelationFilter(environment))
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level.upper())
    return logging.getLogger("cyclecoach")


def new_request_id() -> str:
    rid = uuid.uuid4().hex[:12]
    request_id.set(rid)
    return rid

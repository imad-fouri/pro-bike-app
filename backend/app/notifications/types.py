"""Provider-neutral types that cross the push boundary (ADR-15 §3).

Nothing here knows what a vendor is. A provider takes a [PushMessage] and a list
of targets and returns per-target outcomes; authorization, idempotency, and
persistence all happen above it, so no provider can decide who may be told
what — which is the whole reason the seam exists.

The payload carried by a [PushMessage] is deliberately impoverished. A push lands
on a lock screen, is mirrored to a paired watch, and is visible to whoever is
holding the phone. It therefore carries an id, a type, and a route — and nothing
that a lock screen could use to embarrass or endanger someone. The client
re-fetches whatever it needs through an authorized API call on tap.
"""

from dataclasses import dataclass, field
from typing import Any

# The four things a push payload may contain. A push must be a *pointer*, never a
# copy: anything the rider is not already entitled to read has no business being
# rendered outside the app.
SAFE_PAYLOAD_KEYS = frozenset({"notification_id", "notification_type", "deep_link"})


class PushError(Exception):
    """Base class for provider failures.

    Carries no token, no payload, and never a provider credential — these are
    logged as metadata and surfaced to the caller as a stable error code.
    """


class PushUnavailable(PushError):
    """No usable provider configured, or the provider refused the connection."""


class PushTimeout(PushError):
    """The provider did not answer inside the configured timeout."""


class PushInvalidResponse(PushError):
    """The provider answered, but not with something usable."""


class DeliveryOutcome(str):
    """Per-device result classes.

    `INVALID_TOKEN` is an outcome rather than an exception on purpose: one dead
    token inside a 500-device batch must disable that device, not fail the batch
    and lose the other 499 deliveries.
    """

    DELIVERED = "delivered"
    INVALID_TOKEN = "invalid_token"
    TRANSIENT = "transient"
    SKIPPED = "skipped"


@dataclass(frozen=True)
class PushDeviceTarget:
    """One delivery target.

    [token] is included because a provider needs it, and is therefore never
    logged, never put in an exception message, and never returned from an API.
    """

    device_id: str
    token: str
    platform: str
    provider: str


@dataclass(frozen=True)
class PushMessage:
    """What to deliver.

    [data] is restricted to [SAFE_PAYLOAD_KEYS]; `safe_payload` enforces that at
    construction time rather than trusting every call site to remember.
    """

    notification_id: str
    notification_type: str
    deep_link: str | None = None
    title: str = ""
    body: str = ""
    data: dict[str, Any] = field(default_factory=dict)

    def safe_payload(self) -> dict[str, Any]:
        """The exact map that crosses the provider boundary.

        Built from the named fields rather than from `data` so a future field
        cannot widen the payload by being added to the dataclass. An empty value
        is dropped so the provider is not asked to render a blank string.
        """
        payload: dict[str, Any] = {
            "notification_id": self.notification_id,
            "notification_type": self.notification_type,
        }
        if self.deep_link:
            payload["deep_link"] = self.deep_link
        return payload


@dataclass(frozen=True)
class PushDeviceResult:
    device_id: str
    outcome: str

    @property
    def delivered(self) -> bool:
        return self.outcome == DeliveryOutcome.DELIVERED

    @property
    def invalid_token(self) -> bool:
        return self.outcome == DeliveryOutcome.INVALID_TOKEN


@dataclass(frozen=True)
class PushBatchResult:
    provider: str
    results: tuple[PushDeviceResult, ...] = ()

    def count(self, outcome: str) -> int:
        return sum(1 for r in self.results if r.outcome == outcome)

    @property
    def invalid_device_ids(self) -> tuple[str, ...]:
        return tuple(r.device_id for r in self.results if r.invalid_token)

    @property
    def all_delivered(self) -> bool:
        return all(r.delivered for r in self.results)

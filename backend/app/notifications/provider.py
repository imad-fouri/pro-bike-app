"""The push provider seam (ADR-15 §3).

Mirrors `app/ai/provider.py` deliberately, because that file is the project's
proven answer to "one abstraction, several vendors, refuse to guess":

* a `Protocol`, so an implementation is not forced to inherit anything;
* a status → (exception, retryable) mapper, so retry policy is data rather than
  a chain of `if`s inside a provider;
* a factory that raises when nothing usable is configured, so the notification
  service degrades to in-app-only instead of failing;
* a `set_provider` seam for tests.

Only one implementation exists in Phase 8.4: `FakePushProvider`. FCM and APNs are
FUTURE and require no SDK, credential, or native configuration here — the point of
the seam is that adding them later is a new class in this package and nothing
else.

**FCMProvider** and **APNsProvider** are named in the docstring above and
implemented nowhere. That is the intended state, not a gap.
"""

import asyncio
from typing import Protocol, runtime_checkable

from app.core.config import settings
from app.notifications.types import (
    DeliveryOutcome,
    PushBatchResult,
    PushDeviceResult,
    PushDeviceTarget,
    PushError,
    PushInvalidResponse,
    PushMessage,
    PushTimeout,
    PushUnavailable,
)

_RETRY_SLEEP_S = 0.25


@runtime_checkable
class PushProvider(Protocol):
    """The seam. Business services never see an implementation of this."""

    name: str

    async def send(self, message: PushMessage, targets: list[PushDeviceTarget]) -> PushBatchResult:
        """Deliver to every target, or raise a [PushError] subclass.

        Per-device failures are RESULTS, not exceptions: a batch that half
        succeeded must still report what happened to the half that did not.
        """
        ...


def _status_error(status: int) -> tuple[type[PushError], bool]:
    """Map an HTTP status to (exception, retryable).

    Mirrors `app/ai/provider.py::_status_error`. 429 is retryable even though it
    is a 4xx, because a push provider throttling us is a "later" condition and
    dropping a rider's message notification over it would be worse than waiting.
    """
    if status in (408, 429) or status >= 500:
        return PushUnavailable, True
    if status in (401, 403):
        # Our credential is wrong; retrying cannot help.
        return PushUnavailable, False
    return PushInvalidResponse, False


class UnconfiguredPushProvider:
    """The provider installed when the operator has configured no transport.

    Reports every device as `skipped` rather than raising. Raising here would
    turn "no push configured yet" into an error on every notification, when the
    correct behavior is that the in-app notification simply has no push
    counterpart. Phase 8.4 ships with no provider by default, so this is the
    normal state in CI, in review, and in production before a key is set.
    """

    name = "unconfigured"

    async def send(self, message: PushMessage, targets: list[PushDeviceTarget]) -> PushBatchResult:
        return PushBatchResult(
            provider=self.name,
            results=tuple(
                PushDeviceResult(device_id=t.device_id, outcome=DeliveryOutcome.SKIPPED)
                for t in targets
            ),
        )


_override: PushProvider | None = None


def set_provider(provider: PushProvider | None) -> None:
    """Install a provider explicitly (tests) or clear it to use config."""
    global _override
    _override = provider


def get_provider() -> PushProvider:
    """The configured provider, or the unconfigured one.

    Deliberately never raises. An unconfigured provider must not be able to fail
    an in-app notification: the row is the record, and push is an accelerant.
    """
    if _override is not None:
        return _override
    if settings.PUSH_PROVIDER == "none":
        return UnconfiguredPushProvider()
    # FUTURE: 'fcm' and 'apns' return real providers here. Phase 8.4 ships
    # neither, so an unknown name falls back rather than raising — a
    # misconfiguration should not break notifications.
    return UnconfiguredPushProvider()


async def deliver_with_retry(
    provider: PushProvider,
    message: PushMessage,
    targets: list[PushDeviceTarget],
) -> PushBatchResult:
    """Retry only the transient failures.

    A `PushInvalidResponse` is not retried: the provider answered and the answer
    was unusable, so asking again with the same request cannot help. A timeout
    is not retried either — the delivery may well have succeeded, and a retry
    would double-notify the rider. Only an unavailable provider is worth another
    attempt.
    """
    last: PushError | None = None
    for attempt in range(settings.PUSH_RETRY_LIMIT + 1):
        try:
            return await provider.send(message, targets)
        except PushTimeout:
            raise
        except PushInvalidResponse:
            raise
        except PushUnavailable as exc:
            last = exc
        if attempt < settings.PUSH_RETRY_LIMIT:
            await asyncio.sleep(_RETRY_SLEEP_S)
    raise last or PushUnavailable("Push provider unavailable.")


__all__ = [
    "PushProvider",
    "UnconfiguredPushProvider",
    "_status_error",
    "deliver_with_retry",
    "get_provider",
    "set_provider",
]

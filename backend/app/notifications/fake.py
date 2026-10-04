"""Deterministic fake push provider — the only provider in Phase 8.4 (ADR-15 §3).

It behaves like a provider so the service's fan-out, idempotency, payload-safety,
and device-disabling paths can be exercised offline and without a network. It
contacts nothing.

It is a *test double*, not the production default: the production default is
`UnconfiguredPushProvider`. This one records, and can be told to fail, which is
what makes assertions about delivery possible at all.

Two deliberate properties:

* **It records the payload it was handed.** That is what lets a test assert the
  safe-payload rule — that no message body, coordinate, or token can reach the
  provider boundary — rather than merely trusting the code that builds it.
* **It never records a token.** [sent] keeps device ids, not tokens, so a test
  that dumps the provider's state cannot leak one into test output.
"""

from app.notifications.types import (
    DeliveryOutcome,
    PushBatchResult,
    PushDeviceResult,
    PushDeviceTarget,
    PushMessage,
)


class FakePushProvider:
    """Records what it was asked to deliver and optionally fails."""

    name = "fake"

    def __init__(
        self,
        *,
        outcome: str = DeliveryOutcome.DELIVERED,
        error: Exception | None = None,
    ) -> None:
        #: One entry per `send` call, holding the message and target ids.
        self.sent: list[tuple[PushMessage, tuple[str, ...]]] = []
        self._outcome = outcome
        self._error = error

    async def send(self, message: PushMessage, targets: list[PushDeviceTarget]) -> PushBatchResult:
        # Device ids only — a token must never reach anything a test could print.
        self.sent.append((message, tuple(t.device_id for t in targets)))
        if self._error is not None:
            raise self._error
        return PushBatchResult(
            provider=self.name,
            results=tuple(
                PushDeviceResult(device_id=t.device_id, outcome=self._outcome) for t in targets
            ),
        )

    # --- assertions helpers ---------------------------------------------------

    @property
    def call_count(self) -> int:
        return len(self.sent)

    @property
    def last_payload(self) -> dict:
        """The exact payload handed to the provider on the last call."""
        return self.sent[-1][0].safe_payload()

    @property
    def last_targets(self) -> tuple[str, ...]:
        return self.sent[-1][1]

    def payloads(self) -> list[dict]:
        return [m.safe_payload() for m, _ in self.sent]


class InvalidatingPushProvider(FakePushProvider):
    """Reports every token as permanently invalid.

    Exists because "the provider says your token is dead" is the one path that
    must disable a device row, and it needs a fixture that says so.
    """

    name = "fake_invalidating"

    def __init__(self) -> None:
        super().__init__(outcome=DeliveryOutcome.INVALID_TOKEN)


class FailingPushProvider(FakePushProvider):
    """Always raises — the transient-failure and retry path."""

    name = "fake_failing"

    def __init__(self, error: Exception | None = None) -> None:
        super().__init__(error=error or RuntimeError("provider exploded"))

"""Notification foundation: provider seam and transport-neutral types (ADR-15)."""

from app.notifications.fake import (
    FailingPushProvider,
    FakePushProvider,
    InvalidatingPushProvider,
)
from app.notifications.provider import (
    PushProvider,
    UnconfiguredPushProvider,
    deliver_with_retry,
    get_provider,
    set_provider,
)
from app.notifications.types import (
    SAFE_PAYLOAD_KEYS,
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

__all__ = [
    "SAFE_PAYLOAD_KEYS",
    "DeliveryOutcome",
    "FailingPushProvider",
    "FakePushProvider",
    "InvalidatingPushProvider",
    "PushBatchResult",
    "PushDeviceResult",
    "PushDeviceTarget",
    "PushError",
    "PushInvalidResponse",
    "PushMessage",
    "PushProvider",
    "PushTimeout",
    "PushUnavailable",
    "UnconfiguredPushProvider",
    "deliver_with_retry",
    "get_provider",
    "set_provider",
]

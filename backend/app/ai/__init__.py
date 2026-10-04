"""CycleCoach AI Coach layer (Phase 7, ADR-11).

An explanation layer over the deterministic training engine. It reads
owner-scoped metrics produced elsewhere and describes them; it never computes a
metric and never widens a prescription beyond the engine's caps.
"""

from app.ai.provider import get_provider, set_provider
from app.ai.safety import SafetyCategory, classify_message
from app.ai.types import (
    AIError,
    AIRequest,
    AIResponse,
    AIUsage,
    ProviderInvalidResponse,
    ProviderTimeout,
    ProviderUnavailable,
)

__all__ = [
    "AIError",
    "AIRequest",
    "AIResponse",
    "AIUsage",
    "ProviderInvalidResponse",
    "ProviderTimeout",
    "ProviderUnavailable",
    "SafetyCategory",
    "classify_message",
    "get_provider",
    "set_provider",
]

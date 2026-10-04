"""Provider-neutral types that cross the AI boundary (ADR-11 §2).

Nothing here knows what a vendor is. A provider takes an `AIRequest` and returns
an `AIResponse` carrying raw text; parsing and validation happen above it, so no
provider can decide what counts as a valid answer.
"""

from dataclasses import dataclass


class AIError(Exception):
    """Base class for provider failures.

    Carries no prompt, no message, and never a key — these are logged as
    metadata and surfaced to the caller as a stable error code.
    """


class ProviderUnavailable(AIError):
    """No usable provider configured, or the provider refused the connection."""


class ProviderTimeout(AIError):
    """The provider did not answer inside AI_TIMEOUT_SECONDS."""


class ProviderInvalidResponse(AIError):
    """The provider answered, but not with something usable."""


@dataclass(frozen=True)
class AIUsage:
    input_tokens: int = 0
    output_tokens: int = 0

    @property
    def total(self) -> int:
        return self.input_tokens + self.output_tokens


@dataclass(frozen=True)
class AIRequest:
    system: str
    context_json: str
    user_message: str
    max_input_tokens: int
    max_output_tokens: int
    temperature: float = 0.2

    def estimate_input_tokens(self) -> int:
        """Character-based estimate, used only when a provider reports no usage.

        Deliberately an estimate (ADR-11 §7): ~4 characters per token is the
        common rule of thumb and is never presented as a measured count.
        """
        chars = len(self.system) + len(self.context_json) + len(self.user_message)
        return chars // 4 + 1

    def prompt_text(self) -> str:
        return f"{self.system}\n{self.context_json}\n{self.user_message}"


@dataclass(frozen=True)
class AIResponse:
    content: str
    provider: str
    model: str
    usage: AIUsage = AIUsage()
    latency_ms: int = 0

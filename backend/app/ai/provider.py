"""The provider seam (ADR-11 §2).

One protocol, two implementations, and a factory that can only return a
provider the operator actually configured. When nothing usable is configured
this raises `ProviderUnavailable`, and the Coach falls back to its deterministic
answer — which is the normal state in CI, in review, and in production before a
key is set.
"""

import asyncio
from typing import Protocol, runtime_checkable

import httpx

from app.ai.types import (
    AIRequest,
    AIResponse,
    AIUsage,
    ProviderInvalidResponse,
    ProviderTimeout,
    ProviderUnavailable,
)
from app.core.config import settings

_RETRY_SLEEP_S = 0.25


@runtime_checkable
class AIProvider(Protocol):
    name: str

    async def generate(self, request: AIRequest) -> AIResponse:
        """Return the provider's raw reply, or raise an `AIError` subclass."""
        ...


def _status_error(status: int) -> tuple[type[Exception], bool]:
    """Map an HTTP status to (exception, retryable)."""
    if status in (408, 429) or status >= 500:
        return ProviderUnavailable, True
    if status in (401, 403):
        return ProviderUnavailable, False
    return ProviderInvalidResponse, False


class OpenAICompatibleProvider:
    """Any endpoint speaking the OpenAI `/chat/completions` shape.

    Built on `httpx`, which is already a runtime dependency: no vendor SDK is
    added, because the request we need is small and a dependency is a liability
    in a phase whose whole point is provider neutrality.
    """

    name = "openai_compatible"

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str,
        timeout_s: int,
        retry_limit: int,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._base_url = base_url.rstrip("/")
        self._timeout_s = timeout_s
        self._retry_limit = max(0, retry_limit)

    async def generate(self, request: AIRequest) -> AIResponse:
        payload = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": request.system},
                {"role": "user", "content": f"{request.context_json}\n\n{request.user_message}"},
            ],
            "max_tokens": request.max_output_tokens,
            "temperature": request.temperature,
            "response_format": {"type": "json_object"},
        }
        headers = {"Authorization": f"Bearer {self._api_key}", "Content-Type": "application/json"}

        last: Exception | None = None
        for attempt in range(self._retry_limit + 1):
            try:
                return await self._call(payload, headers, request)
            except ProviderTimeout:
                raise
            except ProviderUnavailable as exc:
                last = exc
            except ProviderInvalidResponse:
                raise
            if attempt < self._retry_limit:
                await asyncio.sleep(_RETRY_SLEEP_S)
        raise last or ProviderUnavailable("Provider unavailable.")

    async def _call(self, payload: dict, headers: dict[str, str], request: AIRequest) -> AIResponse:
        url = f"{self._base_url}/chat/completions"
        try:
            async with httpx.AsyncClient(timeout=self._timeout_s) as client:
                response = await client.post(url, json=payload, headers=headers)
        except httpx.TimeoutException as exc:
            raise ProviderTimeout("Provider timed out.") from exc
        except httpx.HTTPError as exc:
            raise ProviderUnavailable("Provider unreachable.") from exc

        if response.status_code >= 400:
            exc_type, _ = _status_error(response.status_code)
            raise exc_type(f"Provider returned HTTP {response.status_code}.")

        try:
            body = response.json()
            content = body["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise ProviderInvalidResponse("Provider response was not usable JSON.") from exc
        if not isinstance(content, str) or not content.strip():
            raise ProviderInvalidResponse("Provider returned an empty reply.")

        usage_body = body.get("usage") or {}
        usage = _usage_from(usage_body, request)
        return AIResponse(
            content=content,
            provider=self.name,
            model=self._model,
            usage=usage,
            latency_ms=int(response.elapsed.total_seconds() * 1000),
        )


def _usage_from(body: object, request: AIRequest) -> AIUsage:
    """Parse the provider's usage block, or charge conservatively.

    Unknown or malformed usage assumes the estimated input *plus the full
    output allowance*: a provider that omits usage must never be cheaper than
    one that reports it honestly, or the daily budget could be bypassed by
    staying silent. Negative counts and booleans (a `bool` is an `int` in
    Python) are malformed, not zero.
    """
    if isinstance(body, dict):
        in_count = _count(body.get("prompt_tokens"))
        out_count = _count(body.get("completion_tokens"))
        if in_count is not None and out_count is not None:
            return AIUsage(in_count, out_count)
    return AIUsage(request.estimate_input_tokens(), request.max_output_tokens)


def _count(value: object) -> int | None:
    """A usable token count, or None when the value cannot be trusted."""
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


_override: AIProvider | None = None


def set_provider(provider: AIProvider | None) -> None:
    """Install a provider explicitly (tests) or clear it to use config."""
    global _override
    _override = provider


def get_provider() -> AIProvider:
    if _override is not None:
        return _override
    if not settings.AI_ENABLED:
        raise ProviderUnavailable("AI is disabled.")
    if settings.AI_PROVIDER == "none":
        raise ProviderUnavailable("No AI provider is configured.")
    if settings.AI_PROVIDER == "openai_compatible":
        if not settings.AI_API_KEY or not settings.AI_MODEL:
            raise ProviderUnavailable("AI provider is missing a key or model.")
        return OpenAICompatibleProvider(
            api_key=settings.AI_API_KEY,
            model=settings.AI_MODEL,
            base_url=settings.AI_BASE_URL,
            timeout_s=settings.AI_TIMEOUT_SECONDS,
            retry_limit=settings.AI_REQUEST_RETRY_LIMIT,
        )
    raise ProviderUnavailable(f"Unknown AI provider: {settings.AI_PROVIDER!r}.")

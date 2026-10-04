"""Deterministic fake provider — the only provider used by tests (ADR-11 §2).

It is a *test double*, not the production fallback: the production fallback is
`app/ai/fallback.py`, which is assembled from real context. This one merely
behaves like a provider so the service's parse/validate/limit paths can be
exercised offline and without a network.
"""

import json

from app.ai.types import AIRequest, AIResponse, AIUsage

_DEFAULT_SUMMARY = "The recorded data for this request is summarised below."
_DEFAULT_CAUTION_CODE = "not_medical_advice"
_DEFAULT_CAUTION_TEXT = (
    "CycleCoach is a training tool, not a medical professional. Do not use it "
    "to diagnose or treat an injury or illness."
)


def _first_metric_key(request: AIRequest) -> str:
    """Pick a real metric key out of the context so validation has something to pass."""
    try:
        context = json.loads(request.context_json.split("\n", 1)[1])
    except (ValueError, IndexError):
        return "summary"
    for metric in context.get("metrics", []):
        key = metric.get("key")
        if isinstance(key, str) and key:
            return key
    return "summary"


def default_draft(request: AIRequest) -> dict:
    return {
        "summary": _DEFAULT_SUMMARY,
        "observations": [
            {
                "metric": _first_metric_key(request),
                "text": "This value is taken directly from the recorded data.",
            }
        ],
        "recommendations": [],
        "cautions": [{"code": _DEFAULT_CAUTION_CODE, "text": _DEFAULT_CAUTION_TEXT}],
        "referenced_entities": [],
    }


class FakeAIProvider:
    name = "fake"

    def __init__(
        self,
        *,
        draft: dict | None = None,
        raw: str | None = None,
        error: Exception | None = None,
        usage: AIUsage | None = None,
    ) -> None:
        self._draft = draft
        self._raw = raw
        self._error = error
        self._usage = usage or AIUsage(120, 80)
        self.calls: list[AIRequest] = []

    async def generate(self, request: AIRequest) -> AIResponse:
        self.calls.append(request)
        if self._error is not None:
            raise self._error
        if self._raw is not None:
            content = self._raw
        else:
            payload = self._draft if self._draft is not None else default_draft(request)
            content = json.dumps(payload)
        return AIResponse(
            content=content,
            provider=self.name,
            model="fake-model",
            usage=self._usage,
            latency_ms=1,
        )


class RecordingProvider(FakeAIProvider):
    """Alias kept explicit so tests read clearly when they only record calls."""

    name = "recording"

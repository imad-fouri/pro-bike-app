"""Coach orchestration: safety, limits, provider, validation, fallback, logging.

The order of operations is the design (ADR-11 §5, §6):

    rate limit → safety classification → build prompt → provider → validate

Safety runs *before* the provider, so a medical or dangerous-training message
never reaches a model at all. Validation runs *after* it, so a model can never
widen the engine's caps. Every provider failure ends at the deterministic
fallback rather than an error page: a rider with the service down still gets
their own numbers, and the response says so in a stable caution code.
"""

import logging
import time
import uuid
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.ai import fallback as fallback_mod
from app.ai import prompts
from app.ai import provider as provider_mod
from app.ai.context import (
    BuiltContext,
    build_ride_context,
    build_weekly_context,
    build_workout_context,
    to_prompt_dict,
)
from app.ai.safety import SafetyCategory, classify_message
from app.ai.types import (
    AIRequest,
    AIResponse,
    AIUsage,
    ProviderInvalidResponse,
    ProviderTimeout,
    ProviderUnavailable,
)
from app.ai.validate import CoachParseError, parse_draft, validate_draft
from app.core.config import settings
from app.core.logging import redact
from app.core.rate_limit import allow
from app.models.user import User
from app.schemas.coach import CoachCaution, CoachIntent, CoachProvenance, CoachResponse

log = logging.getLogger("cyclecoach")

RATE_WINDOW_S = 3600

# Money is Decimal, always. Floats cannot represent $0.000001-scale token
# prices exactly, and the budget comparison must not depend on binary rounding.
# Six decimal places preserve the historical micro-dollar quantum.
_MICRO_USD = Decimal("0.000001")
_daily_spend_usd: dict[str, Decimal] = {}


class CoachError(Exception):
    def __init__(self, code: str, message: str, status: int = 422) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


def reset_budget() -> None:
    """Test seam — clears the in-process daily cost accumulator."""
    _daily_spend_usd.clear()


def _today() -> str:
    return datetime.now(UTC).date().isoformat()


def _spend_today() -> Decimal:
    return _daily_spend_usd.get(_today(), Decimal(0))


def _price(value: float) -> Decimal:
    """A configured price as an exact Decimal. Negative prices are meaningless
    (they would pay the operator to call the model) and are clamped to zero
    rather than allowed to subsidise spend."""
    return max(Decimal(str(value)), Decimal(0))


def _estimate_cost(usage: AIUsage) -> Decimal:
    """Each leg contributes per its own price. A zero price contributes zero —
    it does not disable the other leg, and it does not disable the budget
    check, which compares the accumulated total against the limit on its own."""
    units_in = Decimal(max(usage.input_tokens, 0)) / 1000
    units_out = Decimal(max(usage.output_tokens, 0)) / 1000
    cost = units_in * _price(settings.AI_INPUT_COST_PER_1K_TOKENS) + units_out * _price(
        settings.AI_OUTPUT_COST_PER_1K_TOKENS
    )
    return cost.quantize(_MICRO_USD, rounding=ROUND_HALF_UP)


def _enforce_limits(user: User) -> None:
    if not allow(f"coach:{user.id}", settings.AI_MAX_REQUESTS_PER_USER, RATE_WINDOW_S):
        raise CoachError("AI_RATE_LIMITED", "Too many coach requests. Try again later.", 429)
    # No price precondition: with zero pricing the accumulated spend is zero
    # and the comparison below simply never trips, but the mechanism always
    # runs. Gating this on either price once let output-only pricing bypass
    # the daily budget entirely.
    limit = _price(settings.AI_DAILY_COST_LIMIT)
    if limit > 0 and _spend_today() >= limit:
        raise CoachError("AI_RATE_LIMITED", "The coach has reached its daily limit.", 429)


async def build_context(
    db: AsyncSession,
    user: User,
    intent: CoachIntent,
    ride_id: uuid.UUID | None,
    workout_id: uuid.UUID | None,
) -> BuiltContext:
    """Resolve the referenced entity server-side, or refuse."""
    if intent is CoachIntent.EXPLAIN_RIDE:
        if ride_id is None:
            raise CoachError("AI_CONTEXT_INVALID", "explain_ride requires a ride reference.")
        return await build_ride_context(db, user, ride_id)
    if intent is CoachIntent.EXPLAIN_WORKOUT:
        if workout_id is None:
            raise CoachError("AI_CONTEXT_INVALID", "explain_workout requires a workout reference.")
        return await build_workout_context(db, user, workout_id)
    if intent is CoachIntent.WEEKLY_SUMMARY:
        if ride_id is not None or workout_id is not None:
            raise CoachError(
                "AI_CONTEXT_INVALID", "weekly_summary does not take an entity reference."
            )
        return await build_weekly_context(db, user)
    if intent is CoachIntent.TRAINING_QUESTION:
        if workout_id is not None:
            return await build_workout_context(db, user, workout_id)
        if ride_id is not None:
            return await build_ride_context(db, user, ride_id)
        return await build_weekly_context(db, user)
    raise CoachError("AI_UNSUPPORTED_INTENT", f"Unsupported intent: {intent!r}.")


def _note(response: CoachResponse, code: str, locale: str) -> CoachResponse:
    response.cautions.append(CoachCaution(code=code, text=fallback_mod.fallback_note(code, locale)))
    return response


def _provenance(built: BuiltContext) -> list[CoachProvenance]:
    return [
        CoachProvenance(metric=m.key, entity=m.source, version=m.version, source=m.source)
        for m in built.context.metrics
    ]


async def respond(
    db: AsyncSession,
    user: User,
    intent: CoachIntent,
    message: str,
    built: BuiltContext,
    locale: str,
) -> CoachResponse:
    _enforce_limits(user)

    category = classify_message(message)
    if category is not SafetyCategory.NONE:
        # No user id: linking an identity to "asked about a possible injury" is
        # exactly the health-adjacent record the privacy rules are about. The
        # aggregate count is what makes the safety path auditable.
        _log(
            intent,
            f"safety_{category.value}",
            "none",
            AIUsage(),
            None,
            model="",
        )
        return fallback_mod.build_safety_response(category, intent, locale)

    if not settings.AI_ENABLED:
        return _note(fallback_mod.build_fallback(built, intent, locale), "AI_DISABLED", locale)

    try:
        engine = provider_mod.get_provider()
    except ProviderUnavailable:
        return _note(
            fallback_mod.build_fallback(built, intent, locale), "AI_PROVIDER_UNAVAILABLE", locale
        )

    system, context_json, message_block = prompts.build_prompt(
        intent,
        to_prompt_dict(built, intent),
        message.strip()[: settings.AI_MAX_MESSAGE_CHARS],
        locale,
    )
    request = AIRequest(
        system=system,
        context_json=context_json,
        user_message=message_block,
        max_input_tokens=settings.AI_MAX_INPUT_TOKENS,
        max_output_tokens=settings.AI_MAX_OUTPUT_TOKENS,
    )

    started = time.monotonic()
    try:
        reply = await engine.generate(request)
    except ProviderTimeout:
        return _degraded(built, intent, locale, "AI_TIMEOUT", engine.name, started)
    except ProviderUnavailable:
        return _degraded(built, intent, locale, "AI_PROVIDER_UNAVAILABLE", engine.name, started)
    except ProviderInvalidResponse:
        return _degraded(built, intent, locale, "AI_INVALID_RESPONSE", engine.name, started)

    _log(intent, "ok", engine.name, reply.usage, started, model=reply.model)
    return _finish(reply, built, intent, locale, started, engine.name)


def _degraded(
    built: BuiltContext,
    intent: CoachIntent,
    locale: str,
    code: str,
    provider_name: str,
    started: float,
) -> CoachResponse:
    _log(intent, "fallback", provider_name, AIUsage(), started, model="", extra={"reason": code})
    return _note(fallback_mod.build_fallback(built, intent, locale), code, locale)


def _finish(
    reply: AIResponse,
    built: BuiltContext,
    intent: CoachIntent,
    locale: str,
    started: float,
    provider_name: str,
) -> CoachResponse:
    try:
        draft = parse_draft(reply.content)
    except CoachParseError:
        _log(
            intent,
            "invalid_response",
            provider_name,
            AIUsage(),
            started,
            model=reply.model,
            extra={"reason": "parse_failed"},
        )
        return _note(
            fallback_mod.build_fallback(built, intent, locale), "AI_INVALID_RESPONSE", locale
        )

    result = validate_draft(draft, built)
    if result.rejected:
        _log(
            intent,
            "rejected",
            provider_name,
            AIUsage(),
            started,
            model=reply.model,
            extra={"reason": result.reason},
        )
        return _note(fallback_mod.build_fallback(built, intent, locale), "AI_REJECTED", locale)

    if result.pruned_summary:
        # validate_draft reports the prune; replacing the text is the service's
        # job, because only it knows the locale and the intent.
        result.draft.summary = fallback_mod.build_fallback(built, intent, locale).summary
    if not result.draft.cautions:
        result.draft.cautions = [
            CoachCaution(
                code="not_medical_advice",
                text=fallback_mod.not_medical_advice(locale),
            )
        ]

    return CoachResponse(
        intent=intent,
        summary=result.draft.summary,
        observations=result.draft.observations,
        recommendations=result.draft.recommendations,
        cautions=result.draft.cautions,
        referenced_entities=result.draft.referenced_entities,
        provenance=_provenance(built),
        fallback_used=False,
        prompt_version=prompts.PROMPT_VERSIONS[intent],
        context=built.context,
    )


def _log(
    intent: CoachIntent,
    outcome: str,
    provider_name: str,
    usage: AIUsage,
    started: float | None,
    *,
    model: str = "",
    extra: dict | None = None,
) -> None:
    """Metadata only: never the message, the context, the key, or a location."""
    cost = _estimate_cost(usage)
    if cost:
        _daily_spend_usd[_today()] = _spend_today() + cost
    latency_ms = int((time.monotonic() - started) * 1000) if started else 0
    metadata: dict = {
        "provider": provider_name,
        "model": model or settings.AI_MODEL,
        "intent": intent.value,
        "prompt_version": prompts.PROMPT_VERSIONS[intent],
        "outcome": outcome,
        "latency_ms": latency_ms,
        "input_tokens": usage.input_tokens,
        "output_tokens": usage.output_tokens,
        "cost_usd": float(cost),
    }
    if extra:
        metadata.update(extra)
    log.info("coach", extra=redact(metadata))
    # Counted with the same four bounded dimensions the log line carries. No prompt,
    # no response, no context, no token VALUES — usage stays in the log, because a
    # token count aggregated into a metric is a content-volume fingerprint.
    try:
        from app.core.metrics import record_ai_outcome

        record_ai_outcome(
            outcome=outcome,
            provider=provider_name,
            intent=intent.value,
            latency_ms=latency_ms,
        )
    except Exception as exc:  # noqa: BLE001 - never break a reply over a metric
        # Type only: a metrics failure message could quote a label value.
        log.warning("metrics_record_failed", extra={"error_type": type(exc).__name__})

"""Output validation — the check the model cannot argue with (ADR-11 §1, §4).

The provider's reply is never trusted. It is parsed into `CoachDraft`, then:

- a **cap violation is fatal** — the whole response is discarded and the
  deterministic answer is used, because a too-aggressive prescription is the
  failure mode that actually hurts someone;
- an **unknown metric key is dropped** — the rest of the answer survives;
- a **number that is not in the context is dropped** — same, item by item.

Pruning rather than rejecting for the last two is deliberate: a factual slip
should cost one sentence, not the whole answer.
"""

import json
import re
from dataclasses import dataclass, field
from decimal import Decimal

from pydantic import ValidationError

from app.ai.context import MAX_SESSION_INCREASE_PCT, BuiltContext
from app.schemas.coach import (
    CoachCaution,
    CoachContextOut,
    CoachDraft,
    CoachEntityRef,
    CoachObservation,
    CoachRecommendation,
)
from app.services import training_calc as calc

# Counts, zone indices and unit conversions are structure, not claims about the
# rider. Anything else must be traceable to the context.
_STRUCTURAL_NUMBERS = frozenset(
    {0, 1, 2, 3, 4, 5, 6, 7, 10, 12, 14, 20, 21, 24, 28, 30, 42, 52, 60, 90, 100, 365, 1000}
)
_NUMBER_RE = re.compile(r"-?\d+(?:\.\d+)?")
_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)
_TARGET_TOLERANCE = 0.05


class CoachParseError(Exception):
    """The provider's text was not a usable draft. Never carries its content."""


@dataclass
class ValidationResult:
    draft: CoachDraft
    rejected: bool = False
    reason: str | None = None
    dropped_observations: int = 0
    dropped_recommendations: int = 0
    dropped_entities: int = 0
    pruned_summary: bool = False
    notes: list[str] = field(default_factory=list)


def parse_draft(content: str) -> CoachDraft:
    """Parse provider text into a strict draft, or raise `CoachParseError`."""
    text = _FENCE_RE.sub("", content.strip()).strip()
    try:
        payload = json.loads(text)
    except ValueError as exc:
        raise CoachParseError("Provider reply was not JSON.") from exc
    if not isinstance(payload, dict):
        raise CoachParseError("Provider reply was not a JSON object.")
    try:
        return CoachDraft.model_validate(payload)
    except ValidationError as exc:
        raise CoachParseError(
            f"Provider reply did not match the schema: {exc.error_count()} errors."
        ) from exc


def _allowed_numbers(context: CoachContextOut) -> set[float]:
    values = {float(n) for n in _STRUCTURAL_NUMBERS}
    for metric in context.metrics:
        if metric.value is not None:
            values.add(float(metric.value))
    for text in (*context.notes, *context.unavailable):
        for match in _NUMBER_RE.findall(text):
            values.add(float(match))
    return values


def _numbers_in(text: str) -> set[float]:
    return {float(match) for match in _NUMBER_RE.findall(text)}


def _is_grounded(value: float, allowed: set[float]) -> bool:
    for candidate in allowed:
        if candidate == value:
            return True
        if abs(candidate) >= 1 and abs(value - candidate) <= abs(candidate) * 0.01:
            return True
    return False


def _cap_violation(draft: CoachDraft, built: BuiltContext) -> str | None:
    """Return a reason string when a recommendation exceeds the engine's caps."""
    engine_target = calc.suggest_intensity_target(
        built.latest_load, weekly_load=built.weekly_load
    ).get("target_load")

    for rec in draft.recommendations:
        if rec.load_change_pct is not None:
            if rec.load_change_pct > MAX_SESSION_INCREASE_PCT:
                return "load_increase_above_session_cap"
            if rec.load_change_pct < Decimal(-100):
                return "load_change_out_of_range"
        if rec.load_target is not None:
            if rec.load_target < 0:
                return "negative_load_target"
            if engine_target is None:
                return "load_target_without_baseline"
            if float(rec.load_target) > float(engine_target) + _TARGET_TOLERANCE:
                return "load_target_above_engine_ceiling"
    return None


def validate_draft(draft: CoachDraft, built: BuiltContext) -> ValidationResult:
    context = built.context
    violation = _cap_violation(draft, built)
    if violation is not None:
        return ValidationResult(draft=draft, rejected=True, reason=violation)

    result = ValidationResult(draft=draft)
    allowed = _allowed_numbers(context)
    known_metrics = {m.key for m in context.metrics}

    observations: list[CoachObservation] = []
    for observation in draft.observations:
        if observation.metric not in known_metrics:
            result.dropped_observations += 1
            result.notes.append(f"dropped_observation:{observation.metric}")
            continue
        if not _text_is_grounded(observation.text, allowed):
            result.dropped_observations += 1
            result.notes.append(f"dropped_ungrounded_observation:{observation.metric}")
            continue
        observations.append(observation)

    recommendations: list[CoachRecommendation] = []
    for rec in draft.recommendations:
        if not _text_is_grounded(rec.text, allowed):
            result.dropped_recommendations += 1
            result.notes.append("dropped_ungrounded_recommendation")
            continue
        recommendations.append(rec)

    cautions: list[CoachCaution] = [
        caution for caution in draft.cautions if _text_is_grounded(caution.text, allowed)
    ]

    permitted = {(e.type, e.id) for e in (built.subject, *built.extra_entities)}
    entities: list[CoachEntityRef] = []
    for entity in draft.referenced_entities:
        if (entity.type, entity.id) in permitted:
            entities.append(entity)
        else:
            result.dropped_entities += 1
            result.notes.append(f"dropped_unknown_entity:{entity.type}")

    summary = draft.summary
    if not _text_is_grounded(summary, allowed):
        result.pruned_summary = True
        result.notes.append("pruned_ungrounded_summary")

    result.draft = CoachDraft(
        summary=summary,
        observations=observations,
        recommendations=recommendations,
        cautions=cautions,
        referenced_entities=entities,
    )
    return result


def _text_is_grounded(text: str, allowed: set[float]) -> bool:
    return all(_is_grounded(value, allowed) for value in _numbers_in(text))

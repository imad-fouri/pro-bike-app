"""Coach API schemas (Phase 7).

`CoachDraft` below is deliberately part of the *API* schemas: the AI layer parses
and validates the provider's JSON straight into it, so the model is held to
exactly the shape the client is promised. One contract, no translation layer to
drift (ADR-11 §3, §4).
"""

import uuid
from decimal import Decimal
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class CoachIntent(str, Enum):
    EXPLAIN_RIDE = "explain_ride"
    EXPLAIN_WORKOUT = "explain_workout"
    WEEKLY_SUMMARY = "weekly_summary"
    TRAINING_QUESTION = "training_question"


class CoachEntityRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["ride", "route", "workout", "profile", "activity", "week"]
    id: str | None = None
    label: str = Field(default="", max_length=120)


class CoachMetricRef(BaseModel):
    """A deterministic number, with the formula that produced it.

    The model is never handed a value that is not in one of these, and any
    number it emits must match one of these.
    """

    model_config = ConfigDict(extra="forbid")

    key: str = Field(max_length=64)
    value: Decimal | None = None
    unit: str = Field(max_length=16)
    version: str | None = Field(default=None, max_length=64)
    source: str = Field(max_length=64)


class CoachProvenance(BaseModel):
    model_config = ConfigDict(extra="forbid")

    metric: str = Field(max_length=64)
    entity: str = Field(max_length=64)
    version: str | None = Field(default=None, max_length=64)
    source: str = Field(max_length=64)


class CoachObservation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    metric: str = Field(max_length=64)
    text: str = Field(min_length=1, max_length=400)


class CoachRecommendation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=400)
    # Prescriptions are structured so they can be range-checked against the
    # engine's caps before they are ever shown (ADR-10 §3, ADR-11 §1).
    load_change_pct: Decimal | None = None
    load_target: Decimal | None = None


class CoachCaution(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(max_length=64)
    text: str = Field(min_length=1, max_length=400)


class CoachDraft(BaseModel):
    """Exactly what the model is required to emit, and nothing else."""

    model_config = ConfigDict(extra="forbid")

    summary: str = Field(min_length=1, max_length=1200)
    observations: list[CoachObservation] = Field(default_factory=list, max_length=12)
    recommendations: list[CoachRecommendation] = Field(default_factory=list, max_length=6)
    cautions: list[CoachCaution] = Field(default_factory=list, max_length=6)
    referenced_entities: list[CoachEntityRef] = Field(default_factory=list, max_length=12)


class CoachContextOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    metrics: list[CoachMetricRef] = Field(default_factory=list, max_length=64)
    notes: list[str] = Field(default_factory=list, max_length=20)
    unavailable: list[str] = Field(default_factory=list, max_length=20)


class CoachResponse(BaseModel):
    intent: CoachIntent
    summary: str
    observations: list[CoachObservation] = Field(default_factory=list)
    recommendations: list[CoachRecommendation] = Field(default_factory=list)
    cautions: list[CoachCaution] = Field(default_factory=list)
    referenced_entities: list[CoachEntityRef] = Field(default_factory=list)
    provenance: list[CoachProvenance] = Field(default_factory=list)
    fallback_used: bool = False
    prompt_version: str | None = None
    context: CoachContextOut


class ContextReference(BaseModel):
    """A pointer, never data. The server resolves it and enforces ownership."""

    model_config = ConfigDict(extra="forbid")

    ride_id: uuid.UUID | None = None
    workout_id: uuid.UUID | None = None


class CoachMessageRequest(BaseModel):
    # extra="forbid" is load-bearing: a client that tries to send its own
    # metrics gets a 422 rather than silently-ignored numbers (ADR-11 §8).
    model_config = ConfigDict(extra="forbid")

    intent: CoachIntent
    message: str = Field(min_length=1, max_length=1000)
    context_reference: ContextReference | None = None
    locale: Literal["en", "fr", "ar"] = "en"


class CoachExplainRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message: str = Field(default="", max_length=1000)
    locale: Literal["en", "fr", "ar"] = "en"


class CoachLimits(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_message_chars: int
    max_input_tokens: int
    max_output_tokens: int
    max_requests_per_user: int
    timeout_seconds: int


class CoachStatusOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool
    provider: str
    model: str
    fallback_only: bool
    prompt_versions: dict[str, str]
    limits: CoachLimits

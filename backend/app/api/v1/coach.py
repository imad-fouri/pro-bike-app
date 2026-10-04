"""Coach API (Phase 7). Owner-only, bounded, and never an existence oracle.

A foreign ride or workout is 404 here exactly as it is everywhere else: the
coach must not be usable to discover that someone else's resource exists
(ADR-11 §6). The Coach always answers — provider trouble produces the
deterministic answer with `fallback_used: true`, never an error page.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai import prompts
from app.ai.service import CoachError, build_context, respond
from app.api.deps import get_current_user
from app.core.config import settings
from app.core.rate_limit import allow
from app.db.session import get_db
from app.models.user import User
from app.schemas.coach import (
    CoachExplainRequest,
    CoachIntent,
    CoachLimits,
    CoachMessageRequest,
    CoachResponse,
    CoachStatusOut,
    ContextReference,
)
from app.services.ride_service import RideError
from app.services.route_service import RouteError
from app.services.training_service import TrainingError

router = APIRouter(prefix="/coach", tags=["coach"])

LOCALES = ("en", "fr", "ar")


def _fail(exc: Exception) -> HTTPException:
    """Domain and coach errors share the existing {code, message} detail shape."""
    return HTTPException(
        status_code=exc.status,  # type: ignore[attr-defined]
        detail={"code": exc.code, "message": exc.message},  # type: ignore[attr-defined]
    )


async def _coach(
    db: AsyncSession,
    user: User,
    intent: CoachIntent,
    message: str,
    ride_id: uuid.UUID | None,
    workout_id: uuid.UUID | None,
    locale: str,
) -> CoachResponse:
    try:
        built = await build_context(db, user, intent, ride_id, workout_id)
        return await respond(db, user, intent, message, built, locale)
    except (TrainingError, RideError, RouteError, CoachError) as exc:
        raise _fail(exc) from exc


def _check_locale(locale: str) -> str:
    if locale not in LOCALES:
        raise HTTPException(
            status_code=422,
            detail={"code": "AI_CONTEXT_INVALID", "message": "Unsupported locale."},
        )
    return locale


@router.post("/message", response_model=CoachResponse)
async def post_message(
    body: CoachMessageRequest,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> CoachResponse:
    """General entry point. The body carries a pointer, never metrics."""
    ref: ContextReference = body.context_reference or ContextReference()
    if ref.ride_id is not None and ref.workout_id is not None:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "AI_CONTEXT_INVALID",
                "message": "Give either a ride or a workout, not both.",
            },
        )
    return await _coach(
        db, user, body.intent, body.message, ref.ride_id, ref.workout_id, body.locale
    )


@router.post("/ride/{ride_id}/explain", response_model=CoachResponse)
async def explain_ride(
    ride_id: uuid.UUID,
    body: CoachExplainRequest,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> CoachResponse:
    return await _coach(
        db, user, CoachIntent.EXPLAIN_RIDE, body.message, ride_id, None, body.locale
    )


@router.post("/workout/{workout_id}/explain", response_model=CoachResponse)
async def explain_workout(
    workout_id: uuid.UUID,
    body: CoachExplainRequest,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> CoachResponse:
    return await _coach(
        db, user, CoachIntent.EXPLAIN_WORKOUT, body.message, None, workout_id, body.locale
    )


@router.get("/weekly-summary", response_model=CoachResponse)
async def weekly_summary(
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
    locale: str = "en",
) -> CoachResponse:
    return await _coach(db, user, CoachIntent.WEEKLY_SUMMARY, "", None, None, _check_locale(locale))


@router.get("/status", response_model=CoachStatusOut)
async def status(user: Annotated[User, Depends(get_current_user)]) -> CoachStatusOut:
    """Whether Coach can explain, or will only report the deterministic answer."""
    if not allow(f"coach-status:{user.id}", 120, 60):
        raise HTTPException(status_code=429, detail="Too many requests.")
    return CoachStatusOut(
        enabled=settings.AI_ENABLED,
        provider=settings.AI_PROVIDER if settings.ai_configured else "none",
        model=settings.AI_MODEL if settings.ai_configured else "",
        fallback_only=not settings.ai_configured,
        prompt_versions={
            intent.value: version for intent, version in prompts.PROMPT_VERSIONS.items()
        },
        limits=CoachLimits(
            max_message_chars=settings.AI_MAX_MESSAGE_CHARS,
            max_input_tokens=settings.AI_MAX_INPUT_TOKENS,
            max_output_tokens=settings.AI_MAX_OUTPUT_TOKENS,
            max_requests_per_user=settings.AI_MAX_REQUESTS_PER_USER,
            timeout_seconds=settings.AI_TIMEOUT_SECONDS,
        ),
    )

"""Chat API (Phase 8.3). Thin layer over `chat_service`, ADR-14.

Every route authenticates first and delegates authorization to the service. The
router deliberately holds no policy of its own: a second place where "who may
read this conversation" is decided is a second place for it to be decided
differently.

Status codes carry meaning here:

* 404 for anything the caller has no standing to see — a missing conversation
  and a forbidden one are the same answer, so no route is an existence oracle.
* 201 only for a message that was actually written. An idempotent retry returns
  200 with `duplicate=true`, so a client cannot mistake a replay for a second
  message.
* 409 when the 15-minute edit window has closed.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core.rate_limit import allow
from app.db.session import get_db
from app.models.user import User
from app.schemas.chat import (
    ConversationOut,
    ConversationPage,
    MessageCreate,
    MessageOut,
    MessagePage,
    MessageUpdate,
    SendResult,
)
from app.services import chat_service
from app.services.chat_service import MAX_HISTORY, ChatError

router = APIRouter(prefix="/chat", tags=["chat"])

MAX_CONVERSATIONS = 100


def _fail(exc: ChatError) -> HTTPException:
    return HTTPException(status_code=exc.status, detail={"code": exc.code, "message": exc.message})


def _limited(key: str, limit: int, window_s: int) -> None:
    if not allow(key, limit, window_s):
        raise HTTPException(status_code=429, detail="Too many requests.")


# ---------------------------------------------------------------------------
# Conversations
# ---------------------------------------------------------------------------


@router.get("/conversations", response_model=ConversationPage)
async def list_conversations(
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=MAX_CONVERSATIONS),
) -> ConversationPage:
    """The rider's inbox. Team channels they no longer have standing for are
    omitted rather than returned as rows that would fail on open."""
    items, total = await chat_service.list_conversations(db, user, page, page_size)
    return ConversationPage(
        items=[ConversationOut.model_validate(v) for v in items],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/teams/{team_id}/conversation", response_model=ConversationOut)
async def team_conversation(
    team_id: uuid.UUID,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ConversationOut:
    """The team's single channel, created on first open.

    404 for a non-member as well as for a missing team: the channel is a team
    member's channel, and confirming it exists would leak team existence.
    """
    try:
        view = await chat_service.team_conversation(db, user, team_id)
    except ChatError as exc:
        raise _fail(exc) from exc
    return ConversationOut.model_validate(view)


@router.post("/direct", status_code=201, response_model=ConversationOut)
async def open_direct(
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
    target_user_id: uuid.UUID = Query(...),
) -> ConversationOut:
    """Find-or-create a DM. Idempotent: opening an existing DM returns it.

    Friendship is not required and a block is answered with 404, so this route
    cannot be used to discover who follows whom (ADR-14 §2).
    """
    _limited(f"chat-dm-open:{user.id}", 60, 3600)
    try:
        view = await chat_service.open_direct(db, user, target_user_id)
    except ChatError as exc:
        raise _fail(exc) from exc
    return ConversationOut.model_validate(view)


@router.get("/conversations/{conversation_id}", response_model=ConversationOut)
async def get_conversation(
    conversation_id: uuid.UUID,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ConversationOut:
    try:
        view = await chat_service.get_conversation(db, user, conversation_id)
    except ChatError as exc:
        raise _fail(exc) from exc
    return ConversationOut.model_validate(view)


# ---------------------------------------------------------------------------
# Messages
# ---------------------------------------------------------------------------


@router.get("/conversations/{conversation_id}/messages", response_model=MessagePage)
async def list_messages(
    conversation_id: uuid.UUID,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
    before_seq: int | None = Query(
        default=None, ge=1, description="Return messages older than this sequence."
    ),
    limit: int = Query(default=50, ge=1, le=MAX_HISTORY),
) -> MessagePage:
    """Cursor page of history, newest first.

    Paging by `seq` rather than offset keeps the page stable while new messages
    arrive, which is the normal case on a screen the rider is actively reading.
    """
    try:
        page = await chat_service.list_messages(
            db, user, conversation_id, before_seq=before_seq, limit=limit
        )
    except ChatError as exc:
        raise _fail(exc) from exc
    return MessagePage.model_validate(page)


@router.post("/conversations/{conversation_id}/messages", response_model=SendResult)
async def send_message(
    conversation_id: uuid.UUID,
    body: MessageCreate,
    response: Response,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> SendResult:
    """Send a message. 201 on a new write, 200 on an idempotent retry.

    `client_message_id` is required: a retry after a dropped response must not
    produce a second message, and only a caller-held id can make that checkable.
    """
    _limited(f"chat-send:{user.id}", 60, 60)
    try:
        view, duplicate = await chat_service.send_message(
            db, user, conversation_id, body.body, body.client_message_id
        )
    except ChatError as exc:
        raise _fail(exc) from exc
    # A replay of an already-stored message is not a new write, so the status
    # code differs: 201 means "your message was stored", 200 means "already had it".
    response.status_code = 200 if duplicate else 201
    return SendResult(message=MessageOut.model_validate(view), duplicate=duplicate)


@router.patch("/messages/{message_id}", response_model=MessageOut)
async def edit_message(
    message_id: uuid.UUID,
    body: MessageUpdate,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> MessageOut:
    """Edit own message inside the 15-minute window; 409 once it has closed."""
    _limited(f"chat-edit:{user.id}", 60, 3600)
    try:
        view = await chat_service.edit_message(db, user, message_id, body.body)
    except ChatError as exc:
        raise _fail(exc) from exc
    return MessageOut.model_validate(view)


@router.delete("/messages/{message_id}", response_model=MessageOut)
async def delete_message(
    message_id: uuid.UUID,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> MessageOut:
    """Soft-delete own message. The row stays, its body becomes `[deleted]`."""
    _limited(f"chat-delete:{user.id}", 60, 3600)
    try:
        view = await chat_service.delete_message(db, user, message_id)
    except ChatError as exc:
        raise _fail(exc) from exc
    return MessageOut.model_validate(view)


@router.post("/conversations/{conversation_id}/read")
async def mark_read(
    conversation_id: uuid.UUID,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
    seq: int = Query(..., ge=0, description="Highest sequence the rider has read."),
) -> dict:
    """Advance the read high-water mark. Never moves backwards."""
    _limited(f"chat-read:{user.id}", 120, 60)
    try:
        return await chat_service.mark_read(db, user, conversation_id, seq)
    except ChatError as exc:
        raise _fail(exc) from exc

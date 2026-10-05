"""Phase 8.4 notification service (ADR-15).

The only place a notification is ever created. Business services call the
targeted helpers here; none of them knows what a push provider is.

**The privacy rule that governs everything in this module.** A notification is a
disclosure that bypasses the API. Phase 8.3 spent its design budget making a
blocked DM indistinguishable from a non-existent conversation; a push saying "A
sent you a message" would re-open exactly that oracle through a channel the API
never sees. So:

* Recipients are resolved here, from live authorization, not from whatever the
  caller passes in.
* A recipient who must not be told about an event gets **no row at all** — not a
  row that is quietly never pushed. A row that exists but is silent is still a
  leak through the notification center.
* Every notification carries a pointer (id, type, route), never content.
* Idempotency is a deterministic key derived from an immutable business id.
"""

import logging
import uuid
from datetime import UTC, datetime

from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging import redact
from app.models.chat import ConversationKind, ConversationMember
from app.models.group_ride import (
    GroupRide,
    GroupRideParticipant,
    GroupRideParticipantStatus,
)
from app.models.notifications import (
    Notification,
    NotificationType,
    PushDevice,
    PushPlatform,
    PushProvider,
)
from app.models.social import SocialProfile, UserBlock
from app.models.team import Team, TeamMembership, TeamRole
from app.models.user import User
from app.notifications import provider as push_provider
from app.notifications.types import PushDeviceTarget, PushMessage

log = logging.getLogger("cyclecoach")

MAX_BODY = 4000
MAX_LIST = 100


class NotificationError(Exception):
    def __init__(self, code: str, message: str, status: int = 422) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


def _now() -> datetime:
    return datetime.now(UTC)


def _log(event: str, **fields: object) -> None:
    """Ids and counts only.

    `params` is never logged: it holds display names, and a log line is not the
    place a rider's name belongs. `redact` also blanks any key named `token`, so
    a future field with that name is safe by default.
    """
    log.info(f"notification.{event}", extra=redact(dict(fields)))


def _rows_affected(result: object) -> int:
    """Rows changed by an UPDATE/DELETE, or 0.

    SQLAlchemy types `Result` without `rowcount`, but an UPDATE/DELETE always
    returns a `CursorResult`. Centralising the cast keeps the intent readable at
    the call site instead of repeating a cast four times.
    """
    return int(getattr(result, "rowcount", 0) or 0)


# ---------------------------------------------------------------------------
# Authorization helpers
#
# These deliberately re-derive the same facts Phase 8.3 derives. They are not a
# reuse of `chat_service._require_participant` because that raises, and a
# notification needs a BOOLEAN: "may this rider be told?" — not "or else 404".
# ---------------------------------------------------------------------------


async def _blocked_either_way(db: AsyncSession, a: uuid.UUID, b: uuid.UUID) -> bool:
    """A block in either direction.

    Parenthesised explicitly rather than relying on `&`/`|` precedence: the
    mixed-operator form is correct today but is exactly the kind of thing a later
    edit silently re-breaks, and a precedence slip here would suppress — or
    worse, allow — notifications for a blocked pair.
    """
    res = await db.execute(
        select(UserBlock.id).where(
            ((UserBlock.blocker_user_id == a) & (UserBlock.blocked_user_id == b))
            | ((UserBlock.blocker_user_id == b) & (UserBlock.blocked_user_id == a))
        )
    )
    return res.scalar_one_or_none() is not None


async def _live_team_members(db: AsyncSession, team_id: uuid.UUID) -> list[uuid.UUID]:
    """Current members of a team.

    Deliberately NOT `conversation_members`: a removed rider keeps their chat
    roster row so history stays attributable, which means the roster would
    wrongly keep notifying them (ADR-14 §2.3).
    """
    res = await db.execute(select(TeamMembership.user_id).where(TeamMembership.team_id == team_id))
    return list(res.scalars())


async def _joined_in_ride(db: AsyncSession, ride_id: uuid.UUID) -> list[uuid.UUID]:
    """Riders currently ON a ride.

    Deliberately NOT `conversation_members`, and deliberately only `joined`: a
    rider who withdrew or was removed keeps their chat roster row so history
    stays attributable, and an `invited` rider has not accepted yet. Either would
    make the roster wrongly keep notifying them (ADR-16 §5, §7).
    """
    res = await db.execute(
        select(GroupRideParticipant.user_id).where(
            GroupRideParticipant.group_ride_id == ride_id,
            GroupRideParticipant.status == GroupRideParticipantStatus.JOINED,
        )
    )
    return list(res.scalars())


async def _dm_recipients(
    db: AsyncSession, conversation_id: uuid.UUID, sender_id: uuid.UUID
) -> list[uuid.UUID]:
    """Who may be told about a DM.

    Every other participant, minus anyone blocked either way. Suppression is
    symmetric on purpose: if only the blocker were silenced, the blocked party
    could infer that a block existed by watching which messages produce a
    notification.
    """
    peer = await db.execute(
        select(ConversationMember.user_id).where(
            ConversationMember.conversation_id == conversation_id,
            ConversationMember.user_id != sender_id,
        )
    )
    candidates = [u for u in peer.scalars().all()]
    allowed: list[uuid.UUID] = []
    for user_id in candidates:
        if await _blocked_either_way(db, sender_id, user_id):
            _log(
                "dm_recipient_suppressed",
                conversation_id=str(conversation_id),
                # The id of the SUPPRESSED rider is not logged: this line exists
                # to count, not to record who is blocked by whom.
                suppressed=1,
            )
            continue
        allowed.append(user_id)
    return allowed


# ---------------------------------------------------------------------------
# Business-event entry points
#
# One helper per event, so a business service names the EVENT and never has to
# remember which type, params, deep link, or dedupe key goes with it. That is
# what keeps the privacy rule in one file: there is exactly one place that knows
# a blocked DM produces no notification.
# ---------------------------------------------------------------------------


async def notify_chat_message(
    db: AsyncSession,
    *,
    conversation_id: uuid.UUID,
    kind: ConversationKind,
    team_id: uuid.UUID | None,
    group_ride_id: uuid.UUID | None = None,
    message_id: uuid.UUID,
    sender_id: uuid.UUID,
) -> list[Notification]:
    """Notify about a new chat message.

    Takes plain values rather than the `Conversation` ORM instance: the caller
    runs this after a commit that has expired its session, so an attribute read
    here would be a lazy load outside a greenlet.

    The message BODY is deliberately not a parameter. It must not reach a
    notification under any circumstance, and not accepting it is a stronger
    guarantee than accepting it and carefully declining to use it.
    """
    sender_profile = (
        await db.execute(select(SocialProfile).where(SocialProfile.user_id == sender_id))
    ).scalar_one_or_none()
    actor_name = (sender_profile.display_name if sender_profile else None) or (
        sender_profile.username if sender_profile else None
    )

    params: dict[str, str] = {}
    if actor_name:
        params["actorName"] = actor_name

    is_team = kind == ConversationKind.TEAM
    team_name = None
    if is_team and team_id is not None:
        team = (await db.execute(select(Team).where(Team.id == team_id))).scalar_one_or_none()
        team_name = team.name if team else None
        if team_name:
            params["teamName"] = team_name

    if kind == ConversationKind.GROUP_RIDE and group_ride_id is not None:
        # The RIDE TITLE, not the channel: a rider who gets three notifications
        # in one ride should be able to tell which ride each belongs to, and a
        # ride has no team to borrow a name from.
        ride = (
            await db.execute(select(GroupRide).where(GroupRide.id == group_ride_id))
        ).scalar_one_or_none()
        if ride is not None:
            params["groupRideTitle"] = ride.title

    if is_team:
        # Guaranteed NOT NULL by the binding CHECK, but the ORM type is
        # nullable, so narrow it here rather than casting.
        if team_id is None:  # pragma: no cover — the CHECK prevents it
            return []
        recipients = [uid for uid in await _live_team_members(db, team_id) if uid != sender_id]
        type_ = NotificationType.CHAT_MESSAGE_TEAM
        # No block filter: Phase 8.3 keeps a team channel open across a block,
        # and filtering push while leaving messages flowing would make the
        # channel a block detector.
    elif kind == ConversationKind.GROUP_RIDE:
        # A third recipient basis (ADR-16 §5). Same no-block-filter reasoning as a
        # team channel, and for a stronger reason: a block is a personal
        # boundary, and suppressing one rider's push inside a ride channel would
        # tell the rest of the ride that something was wrong.
        if group_ride_id is None:  # pragma: no cover — the CHECK prevents it
            return []
        recipients = [uid for uid in await _joined_in_ride(db, group_ride_id) if uid != sender_id]
        type_ = NotificationType.CHAT_MESSAGE_GROUP_RIDE
    else:
        recipients = await _dm_recipients(db, conversation_id, sender_id)
        type_ = NotificationType.CHAT_MESSAGE

    return await notify(
        db,
        recipient_ids=recipients,
        type_=type_,
        actor_id=sender_id,
        entity_type="conversation",
        entity_id=conversation_id,
        params=params,
        deep_link=f"/chat/{conversation_id}",
        # Derived from the immutable message id, so a retry — of the request, of
        # a future worker, of a duplicated event — re-reads the same row.
        dedupe_key=f"chat_message:{message_id}",
    )


async def notify_friend_request(
    db: AsyncSession, *, actor_id: uuid.UUID, target_id: uuid.UUID, request_id: uuid.UUID
) -> list[Notification]:
    """Notify the intended recipient of a friend request.

    The recipient is the request's own target, passed explicitly rather than
    derived, because there is exactly one correct recipient and guessing it from
    the relationship row would be a chance to notify the wrong rider.
    """
    if actor_id == target_id:
        return []
    return await notify(
        db,
        recipient_ids=[target_id],
        type_=NotificationType.FRIEND_REQUEST,
        actor_id=actor_id,
        entity_type="friend_request",
        entity_id=request_id,
        params=await _actor_params(db, actor_id),
        deep_link="/friends/requests",
        dedupe_key=f"friend_request:{request_id}",
    )


async def notify_friend_request_accepted(
    db: AsyncSession, *, actor_id: uuid.UUID, target_id: uuid.UUID, request_id: uuid.UUID
) -> list[Notification]:
    """Tell the original requester their request was accepted."""
    if actor_id == target_id:
        return []
    return await notify(
        db,
        recipient_ids=[target_id],
        type_=NotificationType.FRIEND_REQUEST_ACCEPTED,
        actor_id=actor_id,
        entity_type="friend_request",
        entity_id=request_id,
        params=await _actor_params(db, actor_id),
        deep_link=f"/users/{actor_id}",
        dedupe_key=f"friend_request_accepted:{request_id}",
    )


async def notify_team_invitation(
    db: AsyncSession,
    *,
    actor_id: uuid.UUID,
    team_id: uuid.UUID,
    team_name: str,
    invitee_id: uuid.UUID,
    invitation_id: uuid.UUID,
) -> list[Notification]:
    """Tell one invited rider. Never fan out to the whole team."""
    params = await _actor_params(db, actor_id)
    params["teamName"] = team_name
    return await notify(
        db,
        recipient_ids=[invitee_id],
        type_=NotificationType.TEAM_INVITATION,
        actor_id=actor_id,
        entity_type="team_invitation",
        entity_id=invitation_id,
        params=params,
        deep_link=f"/teams/{team_id}",
        dedupe_key=f"team_invitation:{invitation_id}",
    )


async def notify_group_ride_invitation(
    db: AsyncSession,
    *,
    actor_id: uuid.UUID,
    group_ride_id: uuid.UUID,
    title: str,
    invitee_id: uuid.UUID,
    participant_id: uuid.UUID,
    invitation_key: str,
) -> list[Notification]:
    """Tell one invited rider. Never fan out to the ride.

    `uq_group_ride_participants_pair` means the roster row's id is STABLE for the
    life of the ride, so keying dedupe on it alone would make every re-invitation
    a silent duplicate of the first one — the rider would be re-invited and never
    told, which is worse than not re-inviting at all. `invitation_key` carries the
    timestamp this particular invitation stamped on the row, so a genuine
    re-invite is a new event while a retried request reuses the same key
    (ADR-16 §7).
    """
    params = await _actor_params(db, actor_id)
    params["groupRideTitle"] = title
    return await notify(
        db,
        recipient_ids=[invitee_id],
        type_=NotificationType.GROUP_RIDE_INVITATION,
        actor_id=actor_id,
        entity_type="group_ride",
        entity_id=group_ride_id,
        params=params,
        deep_link=f"/group-rides/{group_ride_id}",
        dedupe_key=f"group_ride_invitation:{participant_id}:{invitation_key}",
    )


async def notify_group_ride_accepted(
    db: AsyncSession,
    *,
    actor_id: uuid.UUID,
    group_ride_id: uuid.UUID,
    title: str,
) -> list[Notification]:
    """Tell the ORGANIZER that a rider accepted. Exactly one recipient.

    Not a fan-out: the organizer already knows who they invited, and telling the
    whole roster who just joined turns the roster into a roster.
    """
    ride = (await db.execute(select(GroupRide).where(GroupRide.id == group_ride_id))).scalar_one_or_none()
    if ride is None:
        return []
    params = await _actor_params(db, actor_id)
    params["groupRideTitle"] = title
    return await notify(
        db,
        recipient_ids=[ride.organizer_user_id],
        type_=NotificationType.GROUP_RIDE_ACCEPTED,
        actor_id=actor_id,
        entity_type="group_ride",
        entity_id=group_ride_id,
        params=params,
        deep_link=f"/group-rides/{group_ride_id}",
        dedupe_key=f"group_ride_accepted:{group_ride_id}:{actor_id}",
    )


async def notify_group_ride_started(
    db: AsyncSession,
    *,
    actor_id: uuid.UUID,
    group_ride_id: uuid.UUID,
    title: str,
) -> list[Notification]:
    """Tell every JOINED rider the ride has begun.

    The one Phase 9 fan-out, and it is bounded by the roster rather than by a
    team: only riders who accepted are told, so an `invited` rider who never
    responded cannot learn the ride started, and a `withdrawn` rider does not
    receive a push for a ride they left.
    """
    recipients = [uid for uid in await _joined_in_ride(db, group_ride_id) if uid != actor_id]
    if not recipients:
        return []
    params = await _actor_params(db, actor_id)
    params["groupRideTitle"] = title
    return await notify(
        db,
        recipient_ids=recipients,
        type_=NotificationType.GROUP_RIDE_STARTED,
        actor_id=actor_id,
        entity_type="group_ride",
        entity_id=group_ride_id,
        params=params,
        deep_link=f"/group-rides/{group_ride_id}",
        # Keyed on the ride, not the roster: every rider's "the ride started"
        # is the same event, and a per-recipient key would let a retry after
        # someone joined mid-ride notify them of a start they missed.
        dedupe_key=f"group_ride_started:{group_ride_id}",
    )


async def notify_team_join_request(
    db: AsyncSession,
    *,
    actor_id: uuid.UUID,
    team_id: uuid.UUID,
    team_name: str,
    request_id: uuid.UUID,
) -> list[Notification]:
    """Tell the team's managers about a join request.

    Only OWNER and ADMIN roles — the same set the join-requests endpoint serves,
    so a rider with no management authority is not told about it.
    """
    managers = await _team_managers(db, team_id)
    managers = [uid for uid in managers if uid != actor_id]
    if not managers:
        return []
    params = await _actor_params(db, actor_id)
    params["teamName"] = team_name
    return await notify(
        db,
        recipient_ids=managers,
        type_=NotificationType.TEAM_JOIN_REQUEST,
        actor_id=actor_id,
        entity_type="team_join_request",
        entity_id=request_id,
        params=params,
        deep_link=f"/teams/{team_id}/join-requests",
        dedupe_key=f"team_join_request:{request_id}",
    )


async def notify_team_member_removed(
    db: AsyncSession,
    *,
    actor_id: uuid.UUID,
    team_id: uuid.UUID,
    team_name: str,
    member_id: uuid.UUID,
) -> list[Notification]:
    """Tell a removed rider they were removed.

    Sent AFTER the membership row is gone, which is the point: it is the one
    notification that must reach someone who no longer has any standing in the
    team, so its deep link resolves to the team profile and that profile's own
    404 is what the rider sees. The notification tells them what they must know;
    it does not grant them access.
    """
    if actor_id == member_id:
        return []
    params = await _actor_params(db, actor_id)
    params["teamName"] = team_name
    return await notify(
        db,
        recipient_ids=[member_id],
        type_=NotificationType.TEAM_MEMBER_REMOVED,
        actor_id=actor_id,
        entity_type="team",
        entity_id=team_id,
        params=params,
        deep_link=f"/teams/{team_id}",
        dedupe_key=f"team_member_removed:{team_id}:{member_id}",
    )


async def notify_team_archived(
    db: AsyncSession, *, actor_id: uuid.UUID, team_id: uuid.UUID, team_name: str
) -> list[Notification]:
    """Tell every remaining member that the team was archived.

    Keyed per team (plus per recipient in `_create_one`): archiving is terminal
    — there is no un-archive path — so a retry of the same archive must return
    the existing rows rather than duplicating them, and a second archive of the
    same team is the same event. An unkeyed call would collapse the fan-out,
    because the `IS NULL` pre-check would match the first row written.
    """
    members = await _live_team_members(db, team_id)
    params = {"teamName": team_name}
    return await notify(
        db,
        recipient_ids=members,
        type_=NotificationType.TEAM_ARCHIVED,
        actor_id=actor_id,
        entity_type="team",
        entity_id=team_id,
        params=params,
        deep_link=f"/teams/{team_id}",
        dedupe_key=f"team_archived:{team_id}",
    )


async def _actor_params(db: AsyncSession, actor_id: uuid.UUID) -> dict[str, str]:
    """Public display name of the actor, for substitution.

    Read from `social_profiles`, never `users`: a notification renders on a lock
    screen, so a column that never leaves the chat payload must not appear here
    either.
    """
    profile = (
        await db.execute(select(SocialProfile).where(SocialProfile.user_id == actor_id))
    ).scalar_one_or_none()
    if profile is None:
        return {}
    name = profile.display_name or profile.username
    return {"actorName": name} if name else {}


async def _team_managers(db: AsyncSession, team_id: uuid.UUID) -> list[uuid.UUID]:
    res = await db.execute(
        select(TeamMembership.user_id).where(
            TeamMembership.team_id == team_id,
            TeamMembership.role.in_([TeamRole.OWNER, TeamRole.ADMIN]),
        )
    )
    return list(res.scalars())


# ---------------------------------------------------------------------------
# Creation
# ---------------------------------------------------------------------------


async def notify(
    db: AsyncSession,
    *,
    recipient_ids: list[uuid.UUID],
    type_: NotificationType,
    actor_id: uuid.UUID | None = None,
    entity_type: str | None = None,
    entity_id: uuid.UUID | None = None,
    params: dict | None = None,
    deep_link: str | None = None,
    dedupe_key: str | None = None,
    deliver: bool = True,
) -> list[Notification]:
    """Create one notification per recipient and attempt delivery.

    Idempotent per `(dedupe_key, recipient)`: a repeat of the same business event
    re-reads the existing row rather than creating a second one. Callers must
    supply a `dedupe_key` derived from an immutable business id — never a
    timestamp, which would make every retry a new notification.
    """
    if not recipient_ids:
        return []

    created: list[Notification] = []
    for recipient_id in dict.fromkeys(recipient_ids):  # de-dupe, keep order
        row = await _create_one(
            db,
            recipient_id=recipient_id,
            type_=type_,
            actor_id=actor_id,
            entity_type=entity_type,
            entity_id=entity_id,
            params=params or {},
            deep_link=deep_link,
            dedupe_key=dedupe_key,
        )
        if row is not None:
            created.append(row)

    if created:
        _log(
            "created",
            type_=type_.value,
            recipients=len(created),
            deduped=len(recipient_ids) - len(created),
        )
    if deliver and created:
        await _deliver(db, created)
    return created


async def _create_one(
    db: AsyncSession,
    *,
    recipient_id: uuid.UUID,
    type_: NotificationType,
    actor_id: uuid.UUID | None,
    entity_type: str | None,
    entity_id: uuid.UUID | None,
    params: dict,
    deep_link: str | None,
    dedupe_key: str | None,
) -> Notification | None:
    """Insert one row, or return the existing one for this dedupe key.

    The `IntegrityError` path is the same shape as `chat_service.send_message`
    and `social_service.send_request`: the unique index is the arbiter, and a
    lost race answers from the winner's row instead of duplicating.
    """
    # Keyed per recipient as well as per event: the index is global, so two
    # recipients of the same fan-out must not collide on one key.
    #
    # An unkeyed call must skip the pre-check entirely: `dedupe_key == None`
    # compiles to `IS NULL`, which would match an unrelated unkeyed row and
    # collapse the whole fan-out into a single recipient.
    key = f"{dedupe_key}:{recipient_id}" if dedupe_key else None

    if key is not None:
        existing = await db.execute(select(Notification).where(Notification.dedupe_key == key))
        prior = existing.scalar_one_or_none()
        if prior is not None:
            return prior

    row = Notification(
        recipient_user_id=recipient_id,
        actor_user_id=actor_id,
        type=type_,
        entity_type=entity_type,
        entity_id=entity_id,
        l10n_key=type_.l10n_key,
        params=params,
        deep_link=deep_link,
        dedupe_key=key,
        created_at=_now(),
    )
    db.add(row)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        if key is None:
            return None
        winner = await db.execute(select(Notification).where(Notification.dedupe_key == key))
        return winner.scalar_one_or_none()
    await db.refresh(row)
    return row


# ---------------------------------------------------------------------------
# Delivery
# ---------------------------------------------------------------------------


async def _deliver(db: AsyncSession, rows: list[Notification]) -> None:
    """Attempt push delivery for freshly created rows.

    Never raises into the caller. A notification that exists but was not pushed
    is still readable in-app, which is strictly better than a chat send that
    fails because a push provider is slow.

    The rows are already written and are never skipped — they are the record. What
    is bounded is the *delivery*: past `PUSH_INLINE_FANOUT_LIMIT` recipients the
    push work is left to the FUTURE worker rather than turning one chat send into
    N synchronous provider round-trips. Skipping the rows instead would lose
    events, which is the one thing this design exists to prevent.
    """
    if not _push_configured():
        _log("deliver_skipped", reason="push_not_configured", rows=len(rows))
        return

    if len(rows) > settings.PUSH_INLINE_FANOUT_LIMIT:
        # Recorded rather than silent, so a deferred fan-out is observable before
        # someone wonders why a busy team channel pushed nothing.
        _log(
            "deliver_deferred",
            reason="fan_out_above_inline_limit",
            rows=len(rows),
            limit=settings.PUSH_INLINE_FANOUT_LIMIT,
        )
        return

    provider = push_provider.get_provider()
    for row in rows:
        targets = await _targets_for(db, row.recipient_user_id)
        if not targets:
            _log("deliver_no_targets", notification_id=str(row.id))
            continue
        message = PushMessage(
            notification_id=str(row.id),
            notification_type=row.type.value,
            deep_link=row.deep_link,
            # Title/body are empty on purpose: the OS renders push text in a
            # locale the server cannot observe. The client renders the real text
            # from l10n_key when it receives the message.
        )
        try:
            result = await push_provider.deliver_with_retry(provider, message, targets)
        except Exception as exc:  # noqa: BLE001 — delivery must never propagate
            _log(
                "deliver_failed",
                notification_id=str(row.id),
                provider=getattr(provider, "name", "?"),
                error_category=type(exc).__name__,
            )
            continue
        await _apply_device_results(db, result)


def _push_configured() -> bool:
    return bool(settings.PUSH_ENABLED)


async def _targets_for(db: AsyncSession, user_id: uuid.UUID) -> list[PushDeviceTarget]:
    res = await db.execute(
        select(PushDevice).where(PushDevice.user_id == user_id, PushDevice.enabled.is_(True))
    )
    return [
        PushDeviceTarget(
            device_id=d.device_id,
            token=d.token,
            platform=d.platform.value,
            provider=d.provider.value,
        )
        for d in res.scalars()
    ]


async def _apply_device_results(db: AsyncSession, result) -> None:
    """Disable devices the provider reported as permanently invalid.

    A dead token that keeps its row enabled is retried on every notification
    forever. `enabled = false` rather than DELETE, so the device's history stays
    auditable and re-registration can revive it.
    """
    invalid = result.invalid_device_ids
    if not invalid:
        return
    await db.execute(
        update(PushDevice)
        .where(PushDevice.device_id.in_(list(invalid)))
        .values(enabled=False, updated_at=_now())
    )
    await db.commit()
    _log("devices_disabled", count=len(invalid), provider=result.provider)


# ---------------------------------------------------------------------------
# Device registration
# ---------------------------------------------------------------------------


async def register_device(
    db: AsyncSession,
    user: User,
    *,
    platform: PushPlatform,
    provider: PushProvider,
    device_id: str,
    token: str,
    app_version: str | None = None,
    locale: str | None = None,
) -> tuple[PushDevice, bool]:
    """Register or refresh one device. Returns `(device, created)`.

    Idempotent on `(user_id, provider, device_id)`: a re-registration updates the
    existing row, which is what makes both an app relaunch and a token rotation
    a no-op instead of a new row each time.

    Ownership is always [user], taken from the JWT. A caller can never register a
    device on someone else's account.

    `user_id` is captured up front and used everywhere. The commit and the
    rollback below both expire every ORM object in the session, so reading
    `user.id` after either one would be a lazy load outside a greenlet — a 500.
    The test suite does not catch this because its session factory sets
    `expire_on_commit=False`; only the live smoke runs against the real session
    configuration.
    """
    user_id = user.id
    now = _now()
    existing = await db.execute(
        select(PushDevice).where(
            PushDevice.user_id == user_id,
            PushDevice.provider == provider,
            PushDevice.device_id == device_id,
        )
    )
    row = existing.scalar_one_or_none()

    if row is not None:
        # Re-activation: a device the rider had disabled comes back on the next
        # registration, which is what they asked for by opening the app.
        _apply_registration(row, token, platform, app_version, locale, now)
        try:
            await db.commit()
        except IntegrityError:
            # Rotating onto a token another account already holds. The uniqueness
            # guard is doing its job, so the write has to be reconciled rather than
            # left to surface as a 500.
            return await _reconcile_token_clash(
                db, user_id, provider, token, device_id, platform, app_version, locale, now
            )
        await db.refresh(row)
        _log("device_refreshed", device_id=device_id, provider=provider.value)
        return row, False

    row = PushDevice(
        user_id=user_id,
        platform=platform,
        provider=provider,
        device_id=device_id,
        token=token,
        app_version=app_version,
        locale=locale,
        enabled=True,
        last_seen_at=now,
        created_at=now,
        updated_at=now,
    )
    db.add(row)
    try:
        await db.commit()
    except IntegrityError:
        return await _reconcile_token_clash(
            db, user_id, provider, token, device_id, platform, app_version, locale, now
        )

    await db.refresh(row)
    _log("device_registered", device_id=device_id, provider=provider.value)
    return row, True


async def _reconcile_token_clash(
    db: AsyncSession,
    user_id: uuid.UUID,
    provider: PushProvider,
    token: str,
    device_id: str,
    platform: PushPlatform,
    app_version: str | None,
    locale: str | None,
    now: datetime,
) -> tuple[PushDevice, bool]:
    """Recover from a write rejected by a uniqueness constraint.

    Two constraints are in play, and which one fired decides the outcome:

    * `uq_push_devices_provider_token` — this token is already stored, possibly
      under a different account.
    * `uq_push_devices_user_device` — this rider already has a row for this
      physical device.

    A token already belonging to a DIFFERENT account is a legitimate state — a
    rider signing in as someone else on one phone, or a provider reassigning a
    token — so the row MOVES rather than the write being refused. Refusing would
    leave the previous account pushing to a device now showing the new account,
    which is the exact leak the transfer prevents.

    When the caller already holds a row for the same physical device the two rows
    have to MERGE: the caller's freshly-registered row is authoritative and the
    stale one is dropped, because a token and a device can each belong to exactly
    one row.

    Always returns `created=False`: the row existed before this call.
    """
    await db.rollback()
    mine = (
        await db.execute(
            select(PushDevice).where(
                PushDevice.user_id == user_id,
                PushDevice.provider == provider,
                PushDevice.device_id == device_id,
            )
        )
    ).scalar_one_or_none()
    clash = await db.execute(
        select(PushDevice).where(PushDevice.provider == provider, PushDevice.token == token)
    )
    other = clash.scalar_one_or_none()

    if other is not None and other.user_id != user_id:
        if mine is not None:
            # Same device, new token, previously owned by someone else: keep ours
            # and discard the row that was holding the token.
            #
            # The DELETE is flushed on its own first. SQLAlchemy emits updates
            # ahead of deletes within a flush, so writing the new token in the
            # same flush would transiently violate the very constraint being
            # resolved.
            _log("device_token_reclaimed", provider=provider.value)
            await db.delete(other)
            await db.flush()
            _apply_registration(mine, token, platform, app_version, locale, now)
            await db.commit()
            await db.refresh(mine)
            return mine, False
        _log("device_transferred", provider=provider.value)
        other.user_id = user_id
        other.device_id = device_id
        other.platform = platform
        other.app_version = app_version
        other.locale = locale
        other.enabled = True
        other.last_seen_at = now
        other.updated_at = now
        await db.commit()
        await db.refresh(other)
        return other, False

    if mine is not None:
        return mine, False
    raise NotificationError(  # pragma: no cover — a constraint says one exists
        "PUSH_DEVICE_CONFLICT", "That device could not be registered.", 409
    )


def _apply_registration(
    row: PushDevice,
    token: str,
    platform: PushPlatform,
    app_version: str | None,
    locale: str | None,
    now: datetime,
) -> None:
    row.token = token
    row.platform = platform
    row.app_version = app_version
    row.locale = locale
    row.enabled = True
    row.last_seen_at = now
    row.updated_at = now


async def list_devices(db: AsyncSession, user: User) -> list[PushDevice]:
    res = await db.execute(
        select(PushDevice).where(PushDevice.user_id == user.id).order_by(PushDevice.created_at)
    )
    return list(res.scalars())


async def set_device_enabled(
    db: AsyncSession, user: User, device_id: uuid.UUID, enabled: bool
) -> PushDevice:
    """Enable or disable one of the rider's own devices.

    Scoped by `user_id` in the WHERE clause rather than fetched-then-checked, so
    another rider's device id is indistinguishable from a missing one.
    """
    res = await db.execute(
        update(PushDevice)
        .where(PushDevice.id == device_id, PushDevice.user_id == user.id)
        .values(enabled=enabled, updated_at=_now())
    )
    if _rows_affected(res) == 0:
        raise NotificationError("PUSH_DEVICE_NOT_FOUND", "That device is not available.", 404)
    await db.commit()
    row = (await db.execute(select(PushDevice).where(PushDevice.id == device_id))).scalar_one()
    _log("device_updated", device_id=device_id, enabled=enabled)
    return row


async def delete_device(db: AsyncSession, user: User, device_id: uuid.UUID) -> None:
    """Revoke one of the rider's own devices.

    A hard delete, unlike a disable: a revoked token has no reason to remain
    stored, and keeping it would leave a credential on disk that the rider has
    asked to be rid of.
    """
    res = await db.execute(
        delete(PushDevice).where(
            PushDevice.id == device_id,
            PushDevice.user_id == user.id,
        )
    )
    if _rows_affected(res) == 0:
        raise NotificationError("PUSH_DEVICE_NOT_FOUND", "That device is not available.", 404)
    await db.commit()
    _log("device_revoked", device_id=device_id)


# ---------------------------------------------------------------------------
# Listing and read state
# ---------------------------------------------------------------------------


async def list_notifications(
    db: AsyncSession,
    user: User,
    *,
    page: int,
    page_size: int,
    unread_only: bool = False,
) -> tuple[list[dict], int]:
    """The rider's own notifications, newest first.

    Always scoped `recipient_user_id = user`: there is no code path that reads a
    notification the viewer did not receive, so a guessed id cannot enumerate
    anyone else's activity.
    """
    conditions = [Notification.recipient_user_id == user.id]
    if unread_only:
        conditions.append(Notification.read_at.is_(None))

    total = (
        await db.execute(select(func.count()).select_from(Notification).where(*conditions))
    ).scalar_one()

    rows = (
        (
            await db.execute(
                select(Notification)
                .where(*conditions)
                .order_by(Notification.created_at.desc(), Notification.id)
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        )
        .scalars()
        .all()
    )
    return [await _view(db, row) for row in rows], total


async def unread_count(db: AsyncSession, user: User) -> int:
    """Unread total.

    A COUNT over the partial index `ix_notifications_recipient_unread`, so the
    cost tracks the number of UNREAD rows rather than total history. It is polled
    by the app bar on every foreground, so a linear scan here would be the one
    genuinely expensive query in the feature.
    """
    res = await db.execute(
        select(func.count())
        .select_from(Notification)
        .where(
            Notification.recipient_user_id == user.id,
            Notification.read_at.is_(None),
        )
    )
    return int(res.scalar_one())


async def mark_read(db: AsyncSession, user: User, notification_id: uuid.UUID) -> dict:
    """Mark one notification read. Idempotent.

    Re-marking an already-read notification is a no-op rather than an error: a
    client retrying a tap it already handled must not see a failure.

    The write is committed explicitly. The session's implicit rollback on close
    would otherwise discard the UPDATE, and the endpoint would answer 200 with
    `is_read: true` for a row that is still unread on the next read — which is the
    kind of bug that only shows up once a rider looks at the list afterwards.
    """
    res = await db.execute(
        update(Notification)
        .where(
            Notification.id == notification_id,
            Notification.recipient_user_id == user.id,
            Notification.read_at.is_(None),
        )
        .values(read_at=_now())
    )
    if _rows_affected(res) == 0:
        # Either already read, or not this rider's. Distinguishing the two would
        # confirm the id exists, so re-read with the ownership scope and report
        # whatever the rider is allowed to see.
        owned = await db.execute(
            select(Notification).where(
                Notification.id == notification_id,
                Notification.recipient_user_id == user.id,
            )
        )
        row = owned.scalar_one_or_none()
        if row is None:
            raise NotificationError(
                "NOTIFICATION_NOT_FOUND", "That notification is not available.", 404
            )
        # Build the view BEFORE rolling back. The rollback expires every ORM
        # object in the session, so rendering `row` afterwards would be a lazy
        # load outside a greenlet — the same trap as the chat send path.
        view = await _view(db, row)
        await db.rollback()
        return view
    await db.commit()
    row = (
        await db.execute(
            select(Notification).where(
                Notification.id == notification_id,
                Notification.recipient_user_id == user.id,
            )
        )
    ).scalar_one()
    _log("marked_read", notification_id=str(notification_id))
    return await _view(db, row)


async def mark_all_read(db: AsyncSession, user: User) -> int:
    """Mark every unread notification read. Idempotent, and returns the count."""
    res = await db.execute(
        update(Notification)
        .where(
            Notification.recipient_user_id == user.id,
            Notification.read_at.is_(None),
        )
        .values(read_at=_now())
    )
    await db.commit()
    marked = _rows_affected(res)
    _log("marked_all_read", marked=marked)
    return marked


async def _view(db: AsyncSession, row: Notification) -> dict:
    """Build the wire shape, resolving public identity for the actor."""
    actor_username = actor_display = None
    if row.actor_user_id is not None:
        profile = await db.execute(
            select(SocialProfile).where(SocialProfile.user_id == row.actor_user_id)
        )
        found = profile.scalar_one_or_none()
        if found is not None:
            actor_username = found.username
            actor_display = found.display_name
    return row.public_view(
        actor_username=actor_username,
        actor_display_name=actor_display,
        entity_name=(row.params or {}).get("teamName"),
    )

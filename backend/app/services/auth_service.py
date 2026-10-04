"""Auth service: registration, login, rotation, logout, reset, verify."""

import logging
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import security
from app.core.config import settings
from app.models.user import (
    EmailVerificationToken,
    PasswordResetToken,
    RefreshSession,
    User,
    UserProfile,
    UserStatus,
)
from app.schemas.auth import LoginIn, RegisterIn
from app.services.email import OutboxMessage, email_service

log = logging.getLogger("cyclecoach")


def _now() -> datetime:
    return datetime.now(UTC)


class AuthError(Exception):
    def __init__(self, code: str, message: str, status: int = 401) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


async def _get_user_by_email(db: AsyncSession, email: str) -> User | None:
    res = await db.execute(select(User).where(User.email == email.lower()))
    return res.scalar_one_or_none()


async def register(db: AsyncSession, data: RegisterIn) -> User:
    if data.password != data.password_confirm:
        raise AuthError("PASSWORD_MISMATCH", "Passwords do not match.", 422)
    if (msg := security.validate_password_strength(data.password)) is not None:
        raise AuthError("WEAK_PASSWORD", msg, 422)
    if await _get_user_by_email(db, data.email) is not None:
        raise AuthError("EMAIL_TAKEN", "An account with this email already exists.", 409)
    now = _now()
    user = User(
        email=data.email.lower(),
        password_hash=security.hash_password(data.password),
        status=UserStatus.ACTIVE,
        email_verified=False,
        created_at=now,
        updated_at=now,
    )
    db.add(user)
    await db.flush()
    db.add(
        UserProfile(
            user_id=user.id,
            display_name=data.display_name,
            updated_at=now,
        )
    )
    await db.commit()
    await db.refresh(user)
    return user


async def _new_session(
    db: AsyncSession, user: User, device_label: str | None
) -> tuple[str, RefreshSession]:
    token, token_hash = security.new_refresh_token()
    session = RefreshSession(
        user_id=user.id,
        family_id=uuid.uuid4(),
        refresh_hash=token_hash,
        device_label=device_label,
        created_at=_now(),
        expires_at=_now() + timedelta(days=settings.REFRESH_TOKEN_DAYS),
    )
    db.add(session)
    await db.commit()
    return token, session


async def login(db: AsyncSession, data: LoginIn) -> tuple[User, str, str]:
    user = await _get_user_by_email(db, data.email)
    if user is None or not security.verify_password(data.password, user.password_hash):
        # Deliberately no identity: an observer must not learn which half failed.
        log.info("auth.login_failed")
        raise AuthError("INVALID_CREDENTIALS", "Invalid email or password.", 401)
    if user.status != UserStatus.ACTIVE or user.deleted_at is not None:
        log.info("auth.login_inactive", extra={"user_id": str(user.id)})
        raise AuthError("ACCOUNT_INACTIVE", "This account is not active.", 403)
    user.last_login_at = _now()
    user.updated_at = _now()
    refresh, _ = await _new_session(db, user, data.device_label)
    access, _ = security.create_access_token(str(user.id))
    await db.commit()
    return user, access, refresh


async def refresh(db: AsyncSession, presented: str) -> tuple[str, str]:
    """Rotate. Reuse of an already-rotated token revokes the whole family."""
    digest = security.hash_token(presented)
    # Row lock: two concurrent presentations of one token must serialize, so
    # the second sees the revoked row and trips reuse detection instead of
    # minting a second live descendant.
    res = await db.execute(
        select(RefreshSession).where(RefreshSession.refresh_hash == digest).with_for_update()
    )
    session = res.scalar_one_or_none()
    if session is None:
        log.info("auth.refresh_invalid")
        raise AuthError("INVALID_REFRESH", "Invalid refresh token.", 401)
    if session.revoked_at is not None:
        # Compromise containment: burn the family, force re-login everywhere.
        fam = await db.execute(
            select(RefreshSession).where(RefreshSession.family_id == session.family_id)
        )
        for s in fam.scalars():
            s.revoked_at = s.revoked_at or _now()
        await db.commit()
        log.info(
            "auth.refresh_reused",
            extra={"family_id": str(session.family_id), "user_id": str(session.user_id)},
        )
        raise AuthError("REFRESH_REUSED", "Session compromised. Please log in again.", 401)
    if session.expires_at < _now():
        raise AuthError("REFRESH_EXPIRED", "Session expired. Please log in again.", 401)
    user = await db.get(User, session.user_id)
    if user is None or user.status != UserStatus.ACTIVE or user.deleted_at is not None:
        raise AuthError("ACCOUNT_INACTIVE", "This account is not active.", 403)
    new_token, new_hash = security.new_refresh_token()
    new_session = RefreshSession(
        user_id=user.id,
        family_id=session.family_id,
        refresh_hash=new_hash,
        device_label=session.device_label,
        created_at=_now(),
        expires_at=_now() + timedelta(days=settings.REFRESH_TOKEN_DAYS),
    )
    db.add(new_session)
    await db.flush()
    session.revoked_at = _now()
    session.replaced_by = new_session.id
    await db.commit()
    access, _ = security.create_access_token(str(user.id))
    return access, new_token


async def logout(db: AsyncSession, presented: str) -> None:
    res = await db.execute(
        select(RefreshSession).where(RefreshSession.refresh_hash == security.hash_token(presented))
    )
    session = res.scalar_one_or_none()
    if session is not None and session.revoked_at is None:
        session.revoked_at = _now()
        await db.commit()


async def logout_all(db: AsyncSession, user: User) -> int:
    res = await db.execute(
        select(RefreshSession).where(
            RefreshSession.user_id == user.id, RefreshSession.revoked_at.is_(None)
        )
    )
    count = 0
    for s in res.scalars():
        s.revoked_at = _now()
        count += 1
    await db.commit()
    log.info("auth.logout_all", extra={"user_id": str(user.id), "revoked": count})
    return count


async def request_password_reset(db: AsyncSession, email: str) -> None:
    """Anti-enumeration: identical behavior whether or not the email exists."""
    user = await _get_user_by_email(db, email)
    if user is None:
        return
    token, token_hash = security.new_refresh_token()
    db.add(
        PasswordResetToken(
            user_id=user.id,
            token_hash=token_hash,
            expires_at=_now() + timedelta(minutes=settings.PASSWORD_RESET_MINUTES),
            created_at=_now(),
        )
    )
    await db.commit()
    email_service.send(
        OutboxMessage(
            to=user.email,
            subject="CycleCoach password reset",
            body=f"Use this one-time token: {token}",
        )
    )


async def confirm_password_reset(db: AsyncSession, token: str, new_password: str) -> None:
    res = await db.execute(
        select(PasswordResetToken).where(
            PasswordResetToken.token_hash == security.hash_token(token)
        )
    )
    row = res.scalar_one_or_none()
    if row is None or row.used_at is not None or row.expires_at < _now():
        raise AuthError("INVALID_RESET_TOKEN", "Invalid or expired reset token.", 400)
    if (msg := security.validate_password_strength(new_password)) is not None:
        raise AuthError("WEAK_PASSWORD", msg, 422)
    user = await db.get(User, row.user_id)
    assert user is not None
    user.password_hash = security.hash_password(new_password)
    user.updated_at = _now()
    row.used_at = _now()
    # A password change must close every takeover path: any OTHER outstanding
    # reset token for this user dies here too, not just the presented one.
    others = await db.execute(
        select(PasswordResetToken).where(
            PasswordResetToken.user_id == user.id, PasswordResetToken.used_at.is_(None)
        )
    )
    for other in others.scalars():
        other.used_at = _now()
    # Burn all sessions: password change logs out every device.
    fam = await db.execute(
        select(RefreshSession).where(
            RefreshSession.user_id == user.id, RefreshSession.revoked_at.is_(None)
        )
    )
    for s in fam.scalars():
        s.revoked_at = _now()
    await db.commit()


async def issue_email_verification(db: AsyncSession, user: User) -> None:
    token, token_hash = security.new_refresh_token()
    db.add(
        EmailVerificationToken(
            user_id=user.id,
            token_hash=token_hash,
            expires_at=_now() + timedelta(hours=settings.EMAIL_VERIFY_HOURS),
            created_at=_now(),
        )
    )
    await db.commit()
    email_service.send(
        OutboxMessage(
            to=user.email,
            subject="Verify your CycleCoach email",
            body=f"Use this token: {token}",
        )
    )


async def confirm_email(db: AsyncSession, user: User, token: str) -> None:
    res = await db.execute(
        select(EmailVerificationToken).where(
            EmailVerificationToken.user_id == user.id,
            EmailVerificationToken.token_hash == security.hash_token(token),
        )
    )
    row = res.scalar_one_or_none()
    if row is None or row.used_at is not None or row.expires_at < _now():
        raise AuthError("INVALID_VERIFY_TOKEN", "Invalid or expired verification token.", 400)
    row.used_at = _now()
    user.email_verified = True
    user.updated_at = _now()
    await db.commit()

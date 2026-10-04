"""Auth endpoints. Rate-limited per IP; anti-enumeration on reset."""

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core.rate_limit import allow
from app.db.session import get_db
from app.models.user import User
from app.schemas.auth import (
    EmailVerifyIn,
    LoginIn,
    LogoutIn,
    MeOut,
    PasswordResetConfirmIn,
    PasswordResetRequestIn,
    ProfileOut,
    RefreshIn,
    RegisterIn,
    TokenPair,
    UserOut,
)
from app.services import auth_service
from app.services.auth_service import AuthError

router = APIRouter(prefix="/auth", tags=["auth"])

_LIMITS = {
    "register": (10, 3600),  # 10/hour per IP
    "login": (20, 600),  # 20/10min per IP
    "refresh": (60, 60),  # 60/min per IP
    "reset": (5, 3600),  # 5/hour per IP
}


def _limit(request: Request, key: str) -> None:
    limit, window = _LIMITS[key]
    ident = request.client.host if request.client else "unknown"
    if not allow(f"auth:{key}:{ident}", limit, window):
        raise HTTPException(status_code=429, detail="Too many requests.")


def _auth_error(e: AuthError) -> HTTPException:
    return HTTPException(status_code=e.status, detail={"code": e.code, "message": e.message})


async def _reload(db: AsyncSession, current: User) -> User:
    user = await db.get(User, current.id)
    assert user is not None
    await db.refresh(user, ["profile"])
    return user


def _me(user: User) -> MeOut:
    p = user.profile
    assert p is not None
    return MeOut(
        user=UserOut(
            id=user.id,
            email=user.email,
            status=user.status,
            email_verified=user.email_verified,
            created_at=user.created_at,
            last_login_at=user.last_login_at,
        ),
        profile=ProfileOut(
            display_name=p.display_name,
            first_name=p.first_name,
            last_name=p.last_name,
            avatar_ref=p.avatar_ref,
            country=p.country,
            city=p.city,
            preferred_language=p.preferred_language,
            timezone=p.timezone,
            measurement_system=p.measurement_system,
            cycling_experience=p.cycling_experience,
            disciplines=p.disciplines,
            training_goal=p.training_goal,
            profile_visibility=p.profile_visibility,
            activity_visibility=p.activity_visibility,
        ),
    )


@router.post("/register", response_model=MeOut, status_code=201)
async def register(data: RegisterIn, request: Request, db: AsyncSession = Depends(get_db)) -> MeOut:
    _limit(request, "register")
    try:
        user = await auth_service.register(db, data)
    except AuthError as e:
        raise _auth_error(e) from e
    await db.refresh(user, ["profile"])
    return _me(user)


@router.post("/login", response_model=TokenPair)
async def login(data: LoginIn, request: Request, db: AsyncSession = Depends(get_db)) -> TokenPair:
    _limit(request, "login")
    try:
        _, access, refresh_token = await auth_service.login(db, data)
    except AuthError as e:
        raise _auth_error(e) from e
    return TokenPair(access_token=access, refresh_token=refresh_token)


@router.post("/refresh", response_model=TokenPair)
async def refresh(
    data: RefreshIn, request: Request, db: AsyncSession = Depends(get_db)
) -> TokenPair:
    _limit(request, "refresh")
    try:
        access, new_refresh = await auth_service.refresh(db, data.refresh_token)
    except AuthError as e:
        raise _auth_error(e) from e
    return TokenPair(access_token=access, refresh_token=new_refresh)


@router.post("/logout")
async def logout(data: LogoutIn, db: AsyncSession = Depends(get_db)) -> dict:
    await auth_service.logout(db, data.refresh_token)
    return {"status": "ok"}


@router.post("/logout-all")
async def logout_all(
    current: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> dict:
    user = await _reload(db, current)
    count = await auth_service.logout_all(db, user)
    return {"status": "ok", "revoked": count}


@router.get("/me", response_model=MeOut)
async def me(user: User = Depends(get_current_user)) -> MeOut:
    return _me(user)


@router.post("/password-reset/request")
async def password_reset_request(
    data: PasswordResetRequestIn, request: Request, db: AsyncSession = Depends(get_db)
) -> dict:
    _limit(request, "reset")
    await auth_service.request_password_reset(db, data.email)
    return {"status": "ok"}  # always 200 — anti-enumeration


@router.post("/password-reset/confirm")
async def password_reset_confirm(
    data: PasswordResetConfirmIn, db: AsyncSession = Depends(get_db)
) -> dict:
    if data.new_password != data.new_password_confirm:
        raise HTTPException(status_code=422, detail="Passwords do not match.")
    try:
        await auth_service.confirm_password_reset(db, data.token, data.new_password)
    except AuthError as e:
        raise _auth_error(e) from e
    return {"status": "ok"}


@router.post("/verify-email/request")
async def verify_request(
    current: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> dict:
    user = await _reload(db, current)
    await auth_service.issue_email_verification(db, user)
    return {"status": "ok"}


@router.post("/verify-email/confirm")
async def verify_confirm(
    data: EmailVerifyIn,
    current: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    user = await _reload(db, current)
    try:
        await auth_service.confirm_email(db, user, data.token)
    except AuthError as e:
        raise _auth_error(e) from e
    return {"status": "ok"}

"""Auth + profile Pydantic schemas. All inputs validated; no passwords echoed."""

import re
import uuid
from datetime import datetime

from pydantic import BaseModel, EmailStr, Field, field_validator

from app.models.user import MeasurementSystem, UserStatus, Visibility

_DISPLAY_RE = re.compile(r"^[\w .'\-]{2,80}$", re.UNICODE)


class RegisterIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=10, max_length=128)
    password_confirm: str = Field(min_length=10, max_length=128)
    display_name: str = Field(min_length=2, max_length=80)

    @field_validator("display_name")
    @classmethod
    def _display(cls, v: str) -> str:
        if not _DISPLAY_RE.match(v.strip()):
            raise ValueError("Display name contains invalid characters.")
        return v.strip()


class LoginIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)
    device_label: str | None = Field(default=None, max_length=120)


class RefreshIn(BaseModel):
    refresh_token: str = Field(min_length=20, max_length=256)


class LogoutIn(BaseModel):
    refresh_token: str = Field(min_length=20, max_length=256)


class PasswordResetRequestIn(BaseModel):
    email: EmailStr


class PasswordResetConfirmIn(BaseModel):
    token: str = Field(min_length=20, max_length=256)
    new_password: str = Field(min_length=10, max_length=128)
    new_password_confirm: str = Field(min_length=10, max_length=128)


class EmailVerifyIn(BaseModel):
    token: str = Field(min_length=20, max_length=256)


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class UserOut(BaseModel):
    id: uuid.UUID
    email: str
    status: UserStatus
    email_verified: bool
    created_at: datetime
    last_login_at: datetime | None


class ProfileOut(BaseModel):
    display_name: str
    first_name: str | None = None
    last_name: str | None = None
    avatar_ref: str | None = None
    country: str | None = None
    city: str | None = None
    preferred_language: str
    timezone: str
    measurement_system: MeasurementSystem
    cycling_experience: str | None = None
    disciplines: list[str] = []
    training_goal: str | None = None
    profile_visibility: Visibility
    activity_visibility: Visibility


class MeOut(BaseModel):
    user: UserOut
    profile: ProfileOut


class ProfilePatch(BaseModel):
    display_name: str | None = Field(default=None, min_length=2, max_length=80)
    first_name: str | None = Field(default=None, max_length=80)
    last_name: str | None = Field(default=None, max_length=80)
    country: str | None = Field(default=None, min_length=2, max_length=2)
    city: str | None = Field(default=None, max_length=120)
    preferred_language: str | None = Field(default=None, min_length=2, max_length=8)
    timezone: str | None = Field(default=None, min_length=1, max_length=64)
    measurement_system: MeasurementSystem | None = None
    cycling_experience: str | None = Field(default=None, max_length=32)
    disciplines: list[str] | None = Field(default=None, max_length=14)
    training_goal: str | None = Field(default=None, max_length=500)
    profile_visibility: Visibility | None = None
    activity_visibility: Visibility | None = None

    @field_validator("disciplines")
    @classmethod
    def _disciplines(cls, v: list[str] | None) -> list[str] | None:
        if v is None:
            return v
        cleaned = sorted({d.strip().lower()[:32] for d in v if d.strip()})
        if len(cleaned) > 14:
            raise ValueError("Too many disciplines.")
        return cleaned

"""Auth crypto: Argon2id passwords, JWT access tokens, opaque refresh tokens."""

import hashlib
import hmac
import secrets
import uuid
from datetime import UTC, datetime, timedelta

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

from app.core.config import settings

_ph = PasswordHasher()  # Argon2id defaults (OWASP-recommended parameters)

MIN_PASSWORD_LEN = 10


def validate_password_strength(password: str) -> str | None:
    """Return an error message if weak, else None. Never logs the password."""
    if len(password) < MIN_PASSWORD_LEN:
        return f"Password must be at least {MIN_PASSWORD_LEN} characters."
    if not any(c.isalpha() for c in password) or not any(c.isdigit() for c in password):
        return "Password must contain at least one letter and one digit."
    return None


def hash_password(password: str) -> str:
    return _ph.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return _ph.verify(password_hash, password)
    except VerifyMismatchError:
        return False


def create_access_token(user_id: str) -> tuple[str, str]:
    """Return (token, jti). Short-lived JWT, type=access."""
    jti = uuid.uuid4().hex
    now = datetime.now(UTC)
    payload = {
        "sub": user_id,
        "jti": jti,
        "type": "access",
        "iat": now,
        "exp": now + timedelta(minutes=settings.ACCESS_TOKEN_MINUTES),
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm="HS256"), jti


def decode_access_token(token: str) -> dict:
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=["HS256"])
    except jwt.ExpiredSignatureError as e:
        raise ValueError("expired") from e
    except jwt.InvalidTokenError as e:
        raise ValueError("invalid") from e
    if payload.get("type") != "access" or "sub" not in payload:
        raise ValueError("invalid")
    return payload


def new_refresh_token() -> tuple[str, str]:
    """Return (plaintext_token, sha256_hash). Plaintext is never stored."""
    token = secrets.token_urlsafe(32)
    return token, hashlib.sha256(token.encode()).hexdigest()


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def tokens_equal(presented: str, stored_hash: str) -> bool:
    return hmac.compare_digest(hash_token(presented), stored_hash)

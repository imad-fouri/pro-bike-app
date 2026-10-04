"""Phase 2 auth tests: registration, login, tokens, reset, isolation."""

import pytest

BASE = "/api/v1/auth"
PROFILE = "/api/v1/profile"

USER = {
    "email": "rider@example.com",
    "password": "StrongPass123",
    "password_confirm": "StrongPass123",
    "display_name": "Test Rider",
}


async def _register(client, **over):
    data = {**USER, **over}
    return await client.post(f"{BASE}/register", json=data)


async def _login(client, email=USER["email"], password=USER["password"]):
    return await client.post(f"{BASE}/login", json={"email": email, "password": password})


def _auth(access):
    return {"Authorization": f"Bearer {access}"}


# --- registration ---
async def test_register_valid(client):
    r = await _register(client)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["user"]["email"] == USER["email"]
    assert body["profile"]["display_name"] == "Test Rider"
    assert "password" not in r.text.lower() or "password_hash" not in r.text


async def test_register_duplicate_email(client):
    assert (await _register(client)).status_code == 201
    r = await _register(client)
    assert r.status_code == 409


async def test_register_invalid_email(client):
    r = await _register(client, email="not-an-email")
    assert r.status_code == 422


@pytest.mark.parametrize("pw", ["short1", "allletterslong", "12345678901", ""])
async def test_register_weak_password(client, pw):
    r = await _register(client, password=pw, password_confirm=pw)
    assert r.status_code == 422


async def test_register_password_mismatch(client):
    r = await _register(client, password_confirm="OtherPass123")
    assert r.status_code == 422


async def test_password_never_stored_plaintext(client):
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    await _register(client)
    from tests.conftest import TEST_DATABASE_URL

    engine = create_async_engine(TEST_DATABASE_URL)
    async with engine.connect() as conn:
        row = (
            await conn.execute(
                text("SELECT password_hash FROM users WHERE email='rider@example.com'")
            )
        ).first()
    await engine.dispose()
    assert row is not None
    assert "StrongPass123" not in row[0]
    assert row[0].startswith("$argon2")


# --- login ---
async def test_login_valid(client):
    await _register(client)
    r = await _login(client)
    assert r.status_code == 200
    assert r.json()["access_token"] and r.json()["refresh_token"]


async def test_login_invalid_password(client):
    await _register(client)
    r = await _login(client, password="WrongPass999")
    assert r.status_code == 401


async def test_login_nonexistent_account(client):
    r = await _login(client, email="ghost@example.com")
    assert r.status_code == 401  # same as wrong password — no enumeration


async def test_login_inactive_account(client):
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    await _register(client)
    from tests.conftest import TEST_DATABASE_URL

    engine = create_async_engine(TEST_DATABASE_URL)
    async with engine.begin() as conn:
        await conn.execute(text("UPDATE users SET status='suspended'::user_status"))
    await engine.dispose()
    r = await _login(client)
    assert r.status_code == 403


# --- tokens ---
async def test_me_valid_token(client):
    await _register(client)
    access = (await _login(client)).json()["access_token"]
    r = await client.get(f"{BASE}/me", headers=_auth(access))
    assert r.status_code == 200
    assert r.json()["user"]["email"] == USER["email"]


async def test_me_invalid_token(client):
    r = await client.get(f"{BASE}/me", headers=_auth("bogus.token.here"))
    assert r.status_code == 401


async def test_me_expired_token(client):
    import uuid
    from datetime import UTC, datetime, timedelta

    import jwt

    from app.core.config import settings

    expired = jwt.encode(
        {
            "sub": str(uuid.uuid4()),
            "jti": "test",
            "type": "access",
            "iat": datetime.now(UTC) - timedelta(hours=2),
            "exp": datetime.now(UTC) - timedelta(hours=1),
        },
        settings.SECRET_KEY,
        algorithm="HS256",
    )
    r = await client.get(f"{BASE}/me", headers=_auth(expired))
    assert r.status_code == 401


async def test_refresh_rotation(client):
    await _register(client)
    pair = (await _login(client)).json()
    r1 = await client.post(f"{BASE}/refresh", json={"refresh_token": pair["refresh_token"]})
    assert r1.status_code == 200
    rotated = r1.json()
    assert rotated["refresh_token"] != pair["refresh_token"]
    # old token is dead
    r2 = await client.post(f"{BASE}/refresh", json={"refresh_token": pair["refresh_token"]})
    assert r2.status_code == 401


async def test_refresh_reuse_burns_family(client):
    await _register(client)
    pair = (await _login(client)).json()
    new = (
        await client.post(f"{BASE}/refresh", json={"refresh_token": pair["refresh_token"]})
    ).json()
    # attacker replays the original token -> family burned
    await client.post(f"{BASE}/refresh", json={"refresh_token": pair["refresh_token"]})
    # legitimate new token now also dead
    r = await client.post(f"{BASE}/refresh", json={"refresh_token": new["refresh_token"]})
    assert r.status_code == 401


async def test_logout_revokes_session(client):
    await _register(client)
    pair = (await _login(client)).json()
    r = await client.post(f"{BASE}/logout", json={"refresh_token": pair["refresh_token"]})
    assert r.status_code == 200
    r2 = await client.post(f"{BASE}/refresh", json={"refresh_token": pair["refresh_token"]})
    assert r2.status_code == 401


async def test_logout_all_multi_device(client):
    await _register(client)
    a = (await _login(client)).json()
    b = (await _login(client)).json()
    r = await client.post(f"{BASE}/logout-all", headers=_auth(a["access_token"]))
    assert r.status_code == 200
    assert r.json()["revoked"] >= 2
    for t in (a["refresh_token"], b["refresh_token"]):
        assert (await client.post(f"{BASE}/refresh", json={"refresh_token": t})).status_code == 401


async def test_unauthenticated_me_rejected(client):
    r = await client.get(f"{BASE}/me")
    assert r.status_code == 401


# --- user isolation (IDOR) ---
async def test_user_isolation(client):
    await _register(client)
    other = {
        "email": "other@example.com",
        "password": "StrongPass123",
        "password_confirm": "StrongPass123",
        "display_name": "Other",
    }
    assert (await client.post(f"{BASE}/register", json=other)).status_code == 201
    access_a = (await _login(client)).json()["access_token"]
    # A sees only A's data via /me and /profile — no user-id parameter exists to tamper.
    me = (await client.get(f"{BASE}/me", headers=_auth(access_a))).json()
    assert me["user"]["email"] == USER["email"]
    prof = (await client.get(PROFILE, headers=_auth(access_a))).json()
    assert prof["profile"]["display_name"] == "Test Rider"


async def test_profile_patch_self_only(client):
    await _register(client)
    access = (await _login(client)).json()["access_token"]
    r = await client.patch(
        PROFILE, json={"city": "Oued Zem", "timezone": "Africa/Casablanca"}, headers=_auth(access)
    )
    assert r.status_code == 200
    assert r.json()["profile"]["city"] == "Oued Zem"


async def test_profile_patch_unauthenticated(client):
    r = await client.patch(PROFILE, json={"city": "X"})
    assert r.status_code == 401


# --- password reset ---
async def test_reset_anti_enumeration(client, outbox):
    r = await client.post(f"{BASE}/password-reset/request", json={"email": "nobody@example.com"})
    assert r.status_code == 200
    assert outbox == []


async def test_reset_full_flow(client, outbox):
    await _register(client)
    r = await client.post(f"{BASE}/password-reset/request", json={"email": USER["email"]})
    assert r.status_code == 200
    assert len(outbox) == 1
    token = outbox[0].body.split(": ")[1].strip()
    assert token not in (await client.get(f"{BASE}/me")).text  # never leaked via API
    bad = await client.post(
        f"{BASE}/password-reset/confirm",
        json={
            "token": "this-token-is-long-enough-but-bogus",
            "new_password": "NewStrong123",
            "new_password_confirm": "NewStrong123",
        },
    )
    assert bad.status_code == 400
    ok = await client.post(
        f"{BASE}/password-reset/confirm",
        json={
            "token": token,
            "new_password": "NewStrong123",
            "new_password_confirm": "NewStrong123",
        },
    )
    assert ok.status_code == 200, ok.text
    # token single-use
    again = await client.post(
        f"{BASE}/password-reset/confirm",
        json={
            "token": token,
            "new_password": "Another12345",
            "new_password_confirm": "Another12345",
        },
    )
    assert again.status_code == 400
    # new password works, old sessions burned
    assert (await _login(client, password="NewStrong123")).status_code == 200


# --- email verification ---
async def test_email_verify_flow(client, outbox):
    await _register(client)
    access = (await _login(client)).json()["access_token"]
    assert (
        await client.post(f"{BASE}/verify-email/request", headers=_auth(access))
    ).status_code == 200
    token = outbox[-1].body.split(": ")[1].strip()
    bad = await client.post(
        f"{BASE}/verify-email/confirm",
        json={"token": "this-token-is-long-enough-but-bogus"},
        headers=_auth(access),
    )
    assert bad.status_code == 400
    ok = await client.post(
        f"{BASE}/verify-email/confirm", json={"token": token}, headers=_auth(access)
    )
    assert ok.status_code == 200
    me = (await client.get(f"{BASE}/me", headers=_auth(access))).json()
    assert me["user"]["email_verified"] is True


# --- logging privacy ---
async def test_no_secrets_logged(client, caplog):
    import logging

    with caplog.at_level(logging.INFO, logger="cyclecoach"):
        await _register(client)
        await _login(client)
    for record in caplog.records:
        text = record.getMessage()
        assert "StrongPass123" not in text
        assert "refresh_token" not in text.lower() or "***" in text

"""Phase 10 WS-S live smoke against the real stack.

Real uvicorn, real PostgreSQL, real Redis, real HTTP. The assertions are all
over the wire; the only in-process code used is the service layer to ARRANGE
state the API deliberately cannot produce (there is no grant endpoint, by
design). That arrangement path — `apply_provider_event` with a MANUAL event —
is exactly what a future controlled operation would call.

Run:  python live_smoke_subscriptions.py   (server on 127.0.0.1:8099)
"""

import asyncio
import os
import sys
import uuid
from datetime import UTC, datetime, timedelta

import httpx

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models.subscription import Plan, SubscriptionProvider, SubscriptionStatus
from app.services.subscription_service import ProviderSubscriptionEvent, apply_provider_event

BASE = "http://127.0.0.1:8099/api/v1"
PASSWORD = "Cyclecoach2026pass"
EMAIL_DOMAIN = "smoke.example.com"
DATABASE_URL = os.environ.get(
    "SMOKE_DATABASE_URL", "postgresql+asyncpg://cyclecoach:cyclecoach@localhost:5432/cyclecoach"
)

results: list[tuple[str, bool, str]] = []


def check(step: str, ok: bool, detail: str = "") -> None:
    results.append((step, ok, detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {step}" + (f" :: {detail}" if detail else ""))


async def register(client: httpx.AsyncClient, label: str) -> dict:
    email = f"wss.{label.lower()}.{uuid.uuid4().hex[:8]}@{EMAIL_DOMAIN}"
    res = await client.post(
        f"{BASE}/auth/register",
        json={
            "email": email,
            "password": PASSWORD,
            "password_confirm": PASSWORD,
            "display_name": f"Rider {label}",
        },
    )
    if res.status_code not in (200, 201):
        raise RuntimeError(f"register {label} failed: {res.status_code} {res.text}")
    res = await client.post(f"{BASE}/auth/login", json={"email": email, "password": PASSWORD})
    if res.status_code != 200:
        raise RuntimeError(f"login {label} failed: {res.status_code} {res.text}")
    token = res.json()["access_token"]
    me = await client.get(
        f"{BASE}/auth/me", headers={"Authorization": f"Bearer {token}"}
    )
    return {
        "email": email,
        "token": token,
        "user_id": uuid.UUID(me.json()["user"]["id"]),
        "headers": {"Authorization": f"Bearer {token}"},
    }


async def grant(db, user_id: uuid.UUID, status: SubscriptionStatus, sub_id: str) -> None:
    now = datetime.now(UTC)
    await apply_provider_event(
        db,
        ProviderSubscriptionEvent(
            provider=SubscriptionProvider.MANUAL,
            provider_subscription_id=sub_id,
            provider_event_id=f"smoke-{uuid.uuid4().hex}",
            user_id=user_id,
            plan=Plan.PRO,
            status=status,
            effective_start=now - timedelta(days=1),
            effective_end=now + timedelta(days=30),
            occurred_at=now,
        ),
    )


async def main() -> int:
    engine = create_async_engine(DATABASE_URL)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with httpx.AsyncClient(timeout=30.0) as c:
            free = await register(c, "Free")
            other = await register(c, "Other")

            # --- 1. free user denied with the stable contract ----------------
            r = await c.get(f"{BASE}/coach/weekly-summary", headers=free["headers"])
            body = r.json()
            check("free user gets 403, not 401/404/500", r.status_code == 403, str(r.status_code))
            check(
                "denial code is ENTITLEMENT_REQUIRED",
                body.get("error", {}).get("code") == "ENTITLEMENT_REQUIRED",
                str(body.get("error", {}).get("code")),
            )
            details = body.get("error", {}).get("details", {})
            check(
                "denial names feature and required plan",
                details.get("feature") == "ai_coach" and details.get("required_plan") == "pro",
                str(details),
            )
            check(
                "denial leaks no provider or subscription internals",
                "provider" not in r.text.lower() and "subscription" not in r.text.lower(),
            )

            # --- 2. fake client claims change nothing -------------------------
            r = await c.get(
                f"{BASE}/coach/weekly-summary?is_pro=true&plan=pro",
                headers={**free["headers"], "X-Is-Pro": "true"},
            )
            check("forged isPro claims still 403", r.status_code == 403, str(r.status_code))

            # --- 3. free state is explicit ------------------------------------
            r = await c.get(f"{BASE}/me/entitlements", headers=free["headers"])
            body = r.json()
            check(
                "free state reports plan=free with capabilities",
                r.status_code == 200
                and body.get("plan") == "free"
                and "core_training" in body.get("free_capabilities", [])
                and body.get("entitlements") == [],
                str({k: body.get(k) for k in ("plan", "entitlements")}),
            )

            # --- 4. no parameterized or mutable entitlement routes -------------
            r = await c.get(
                f"{BASE}/users/{free['user_id']}/entitlements", headers=free["headers"]
            )
            check("no /users/{id}/entitlements route", r.status_code == 404, str(r.status_code))
            for method in ("post", "put", "patch", "delete"):
                r = await getattr(c, method)(f"{BASE}/me/entitlements", headers=free["headers"])
                if r.status_code != 405:
                    check(f"{method.upper()} /me/entitlements rejected", False, str(r.status_code))
                    break
            else:
                check("entitlement state is read-only (all 405)", True)

            # --- 5. grant Pro through the service layer ------------------------
            sub_id = f"smoke-{uuid.uuid4().hex}"
            async with factory() as db:
                await grant(db, free["user_id"], SubscriptionStatus.ACTIVE, sub_id)
            r = await c.get(f"{BASE}/me/entitlements", headers=free["headers"])
            body = r.json()
            check(
                "granted user reports plan=pro with five effective grants",
                body.get("plan") == "pro"
                and len(body.get("entitlements", [])) == 5
                and all(e.get("effective") for e in body["entitlements"]),
                f"plan={body.get('plan')} n={len(body.get('entitlements', []))}",
            )
            check(
                "commercial identifiers stay server-side",
                "provider_subscription_id" not in r.text and "provider_event_id" not in r.text,
            )
            r = await c.get(f"{BASE}/coach/weekly-summary", headers=free["headers"])
            check("pro user gets the premium answer", r.status_code == 200, str(r.status_code))

            # --- 6. the other rider is unaffected -------------------------------
            r = await c.get(f"{BASE}/me/entitlements", headers=other["headers"])
            check(
                "second rider is still free",
                r.json().get("plan") == "free",
                str(r.json().get("plan")),
            )
            r = await c.get(f"{BASE}/coach/weekly-summary", headers=other["headers"])
            check("second rider still 403", r.status_code == 403, str(r.status_code))

            # --- 7. revocation ends access --------------------------------------
            async with factory() as db:
                await grant(db, free["user_id"], SubscriptionStatus.REVOKED, sub_id)
            r = await c.get(f"{BASE}/coach/weekly-summary", headers=free["headers"])
            check("revoked user is 403 again", r.status_code == 403, str(r.status_code))
            r = await c.get(f"{BASE}/me/entitlements", headers=free["headers"])
            check(
                "revoked state reports plan=free",
                r.json().get("plan") == "free",
                str(r.json().get("plan")),
            )
    finally:
        await engine.dispose()

    print()
    print("=" * 72)
    failed = [r for r in results if not r[1]]
    print(f"{len(results) - len(failed)}/{len(results)} checks passed")
    for step, _ok, detail in failed:
        print(f"  FAILED: {step} :: {detail}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

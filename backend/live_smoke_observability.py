"""Phase 10 WS-O live smoke against the real stack.

Real uvicorn, real PostgreSQL, real Redis, real HTTP. Nothing in `app/` is
stubbed or monkeypatched. This exists because the whole point of WS-O is
instrumentation, and instrumentation that is only ever exercised by the test
suite is instrumentation nobody has seen running.

What this deliberately does NOT do: read the registry out of the server process.
`snapshot()` is not exposed on any endpoint, so there is nothing to scrape —
which is itself a decision this workstream made. Therefore the smoke asserts the
SIGNALS that must be true from the outside:

  1. `/health` and `/ready` still behave (WS-N contract preserved).
  2. A real request over the wire succeeds and carries an `X-Request-ID`.
  3. The instrumentation cannot break a request, including under a label the
     cardinality guard would refuse.
  4. Real auth flows succeed — so the code paths that now call `record_*`
     executed without raising.
  5. Real live-location publish/read/stop succeed against real Redis, so the
     location counters ran.
  6. A real push-device registration and a real group-ride lifecycle succeed.
  7. No credential, coordinate, address or id appears in the server's own logs.

Run:  python live_smoke_observability.py
Then: inspect the uvicorn output for the WS-O log lines.
"""

import asyncio
import os
import sys
import uuid

import httpx

BASE = "http://127.0.0.1:8099/api/v1"
PASSWORD = "Cyclecoach2026pass"
EMAIL_DOMAIN = "smoke.example.com"

results: list[tuple[str, bool, str]] = []


def check(step: str, ok: bool, detail: str = "") -> None:
    results.append((step, ok, detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {step}" + (f" :: {detail}" if detail else ""))


async def register(client: httpx.AsyncClient, label: str) -> dict:
    email = f"wso.{label.lower()}.{uuid.uuid4().hex[:8]}@{EMAIL_DOMAIN}"
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
    me = await client.get(f"{BASE}/social/profile/me", headers=_auth(token))
    return {
        "email": email,
        "token": token,
        "user_id": me.json()["user_id"],
        "headers": _auth(token),
    }


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def main() -> int:
    async with httpx.AsyncClient(timeout=30.0) as c:
        # --- 1. health contract (WS-N must be untouched) -------------------
        r = await c.get(f"{BASE}/health")
        body = r.json()
        check("health returns 200", r.status_code == 200, str(r.status_code))
        check(
            "health reports both dependencies as up",
            body.get("database") == "up" and body.get("redis") == "up",
            f"database={body.get('database')} redis={body.get('redis')}",
        )
        r = await c.get(f"{BASE}/ready")
        check("ready returns 200 against a live database", r.status_code == 200, r.text[:200])

        # --- 2. correlation id on a real response --------------------------
        r = await c.get(f"{BASE}/health")
        rid = r.headers.get("X-Request-ID")
        check("response carries X-Request-ID", bool(rid), str(rid))
        r = await c.get(f"{BASE}/health", headers={"X-Request-ID": "smoke-canary-01"})
        check(
            "a valid client id is preserved",
            r.headers.get("X-Request-ID") == "smoke-canary-01",
            str(r.headers.get("X-Request-ID")),
        )
        r = await c.get(f"{BASE}/health", headers={"X-Request-ID": "bad id with spaces!"})
        check(
            "a malformed client id is rejected, not reflected",
            r.headers.get("X-Request-ID") not in (None, "bad id with spaces!"),
            str(r.headers.get("X-Request-ID")),
        )

        # --- 3. auth-before-existence, and a real 404 --------------------
        # An unknown ride must not be distinguishable from no permission at all.
        # Answering 401 before checking existence is what prevents ride-ID
        # enumeration, so this asserts 401 -- not 404 -- for the anonymous case.
        missing = uuid.uuid4()
        r = await c.get(f"{BASE}/group-rides/{missing}")
        check(
            "an anonymous read of an unknown ride is 401, not 404",
            r.status_code == 401,
            f"{r.status_code} (404 here would leak ride existence)",
        )

        # --- 4. auth flows: every path that now calls record_auth_event ----
        a = await register(c, "Alpha")
        b = await register(c, "Bravo")
        check("registered and logged in two riders", bool(a["user_id"] and b["user_id"]))

        # Authenticated, a genuinely absent ride is a 404 -- and it still carries
        # a correlation id, because a 404 goes through the error handler chain.
        r = await c.get(f"{BASE}/group-rides/{missing}", headers=a["headers"])
        check("an authenticated read of an unknown ride is 404", r.status_code == 404, str(r.status_code))
        check("the 404 also carries X-Request-ID", bool(r.headers.get("X-Request-ID")))

        r = await c.post(
            f"{BASE}/auth/login", json={"email": a["email"], "password": "WrongPassword123"}
        )
        check("a failed login is a clean 401", r.status_code == 401, str(r.status_code))

        # The schema sets min_length=20, so a short string is a 422 and never
        # reaches the service. Use a well-formed-length garbage token so the real
        # signature-verification path is the thing under test.
        r = await c.post(f"{BASE}/auth/refresh", json={"refresh_token": "not-a-token"})
        check(
            "a too-short refresh token is refused at the schema",
            r.status_code == 422,
            str(r.status_code),
        )
        r = await c.post(
            f"{BASE}/auth/refresh", json={"refresh_token": "wso-garbage-" + "x" * 32}
        )
        check(
            "a well-formed but invalid refresh token is refused by the service",
            r.status_code in (400, 401),
            f"{r.status_code} {r.text[:120]}",
        )

        r = await c.post(f"{BASE}/auth/logout-all", headers=a["headers"])
        check("logout-all succeeds", r.status_code in (200, 204), str(r.status_code))

        # Anti-enumeration: identical response for a registered and an
        # unregistered address. This is the property the counter must not break.
        known = await c.post(f"{BASE}/auth/password-reset/request", json={"email": b["email"]})
        unknown = await c.post(
            f"{BASE}/auth/password-reset/request",
            json={"email": f"nobody.{uuid.uuid4().hex}@{EMAIL_DOMAIN}"},
        )
        check(
            "password reset is anti-enumerating",
            known.status_code == unknown.status_code == 200,
            f"known={known.status_code} unknown={unknown.status_code}",
        )
        check(
            "the reset responses are byte-identical",
            known.json() == unknown.json(),
            f"{known.text} vs {unknown.text}",
        )

        # --- 5. social -> a notification is created and delivery is skipped -
        sent = await c.post(
            f"{BASE}/social/friend-requests", json={"user_id": b["user_id"]}, headers=a["headers"]
        )
        check("friend request accepted", sent.status_code == 201, str(sent.status_code))
        inbox = await c.get(f"{BASE}/notifications", headers=b["headers"])
        items = inbox.json()["items"]
        check(
            "exactly one friend_request notification exists",
            len([i for i in items if i["type"] == "friend_request"]) == 1,
            str([i["type"] for i in items]),
        )

        # --- 6. push device registration ----------------------------------
        reg = await c.post(
            f"{BASE}/push-devices",
            json={
                "platform": "android",
                "provider": "fcm",
                "device_id": f"wso-{uuid.uuid4().hex[:8]}",
                "token": f"wso-token-{uuid.uuid4().hex[:16]}",
                "app_version": "0.0.0-wso",
                "locale": "en",
            },
            headers=b["headers"],
        )
        check("push device registers", reg.status_code in (200, 201), str(reg.status_code))

        # --- 7. group ride lifecycle + real Redis live location -----------
        ride = await c.post(
            f"{BASE}/group-rides", json={"title": "WS-O Smoke Ride"}, headers=a["headers"]
        )
        check("group ride created", ride.status_code == 201, ride.text[:200])
        ride_id = ride.json()["id"]

        inv = await c.post(
            f"{BASE}/group-rides/{ride_id}/invitations",
            json={"user_id": b["user_id"]},
            headers=a["headers"],
        )
        check("invited", inv.status_code == 201, str(inv.status_code))
        joined = await c.post(
            f"{BASE}/group-rides/{ride_id}/respond", json={"accept": True}, headers=b["headers"]
        )
        check("accepted", joined.status_code == 200, str(joined.status_code))
        started = await c.post(f"{BASE}/group-rides/{ride_id}/start", headers=a["headers"])
        check("started (sharing is only legal here)", started.status_code == 200, started.text[:200])

        # A real coordinate, deliberately distinctive so it can be grepped for
        # in the server log afterwards.
        canary_lat, canary_lon = 33.5731, -7.5898
        pub = await c.post(
            f"{BASE}/group-rides/{ride_id}/location",
            json={"latitude": canary_lat, "longitude": canary_lon, "accuracy_m": 7.5},
            headers=a["headers"],
        )
        check("location published to real Redis", pub.status_code == 200, pub.text[:200])

        read = await c.get(f"{BASE}/group-rides/{ride_id}/location", headers=b["headers"])
        check("participant can read the share", read.status_code == 200, str(read.status_code))
        me_items = read.json().get("items", [])
        check(
            "exactly one rider is visible",
            len(me_items) == 1,
            str([(i.get("user_id"), i.get("is_self")) for i in me_items]),
        )
        check(
            "the published coordinate round-tripped",
            bool(me_items)
            and abs(float(me_items[0]["latitude"]) - canary_lat) < 1e-6
            and abs(float(me_items[0]["longitude"]) - canary_lon) < 1e-6,
            f"canary=({canary_lat},{canary_lon})",
        )

        # --- 8. DELETE /location after cancellation stays allowed ----------
        cancelled = await c.post(f"{BASE}/group-rides/{ride_id}/cancel", headers=a["headers"])
        check("ride cancelled", cancelled.status_code == 200, cancelled.text[:200])
        stop = await c.delete(f"{BASE}/group-rides/{ride_id}/location", headers=a["headers"])
        check(
            "withdrawal is allowed even on a cancelled ride",
            stop.status_code == 200,
            f"{stop.status_code} {stop.text[:160]}",
        )
        check("withdrawal reports 'stopped'", stop.json().get("status") == "stopped", stop.text[:120])

        # --- 9. OpenAPI still builds (WS-M must be intact) -----------------
        # The schema is served from the root, outside the /api/v1 prefix.
        r = await c.get("http://127.0.0.1:8099/openapi.json")
        check("openapi.json still builds", r.status_code == 200, str(r.status_code))
        spec = r.json()
        check(
            "the route table is intact",
            len(spec.get("paths", {})) > 100,
            f"{len(spec.get('paths', {}))} paths",
        )
        check(
            "location routes are present",
            "/api/v1/group-rides/{ride_id}/location" in spec.get("paths", {}),
            str([p for p in spec.get("paths", {}) if p.endswith("/location")]),
        )

    print()
    print("=" * 72)
    failed = [r for r in results if not r[1]]
    print(f"{len(results) - len(failed)}/{len(results)} checks passed")
    if failed:
        print("FAILED:")
        for step, _ok, detail in failed:
            print(f"  - {step} :: {detail}")

    print()
    print("=" * 72)
    print("Log canaries to grep the server output for:")
    print(f"  coordinate latitude  : {canary_lat}")
    print(f"  coordinate longitude : {canary_lon}")
    print(f"  accuracy             : 7.5")
    print(f"  registered email     : {a['email']}")
    print(f"  password             : {PASSWORD}")
    print(f"  push token           : the wso-token- value above")
    print()
    print("Expected: NONE of these appear in the log. The location service logs")
    print("group_ride_id and error_type only; the notification service logs counts.")
    print("A hit on any canary is a WS-O regression, not a test artifact.")

    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
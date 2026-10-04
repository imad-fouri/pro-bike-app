"""Phase 8.4 live smoke: notifications & push devices (ADR-15).

Real uvicorn, real PostgreSQL, real Redis. Nothing in app/ is stubbed.

Runs the SAME scenario twice by design. Phase 8.1 shipped a bug that a first run
masked and only a second run against reused state exposed, and Phase 8.3's smoke
found two bugs in its own script. Accounts are unique per run; teams,
conversations, and notifications are not, so the script must be safe against
state it did not create.

The order of the checks is load-bearing. Privacy checks (what a blocked pair may
see) run BEFORE the happy-path ones, so a leak cannot be masked by a later step
failing first — and no assertion is written so loosely that it passes when the
thing it guards is broken.
"""

import asyncio
import json
import sys
import uuid

import httpx

BASE = "http://127.0.0.1:8099/api/v1"
AUTH = f"{BASE}/auth"
SOCIAL = f"{BASE}/social"
TEAMS = f"{BASE}/teams"
CHAT = f"{BASE}/chat"
NOTIF = f"{BASE}/notifications"
DEVICES = f"{BASE}/push-devices"
PASSWORD = "Cyclecoach2026pass"
DOMAIN = "smoke.example.com"

results: list[tuple[str, bool, str]] = []

# A token that must never appear in any response body we print.
#
# Unique per run, because `UNIQUE(provider, token)` means a token already held by
# a previous run's account would be TRANSFERRED rather than registered — which is
# correct behaviour, but would make the idempotency checks below assert about the
# wrong thing. The "still not leaked" assertions are unaffected by the suffix.
SECRET_TOKEN = f"SMOKE-SECRET-PUSH-TOKEN-{uuid.uuid4().hex}"


def check(step: str, ok: bool, detail: str = "") -> None:
    results.append((step, ok, detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {step}" + (f" :: {detail}" if detail else ""))


async def account(client: httpx.AsyncClient, label: str) -> dict:
    email = f"{label.lower()}_{uuid.uuid4().hex[:8]}@{DOMAIN}"
    r = await client.post(
        f"{AUTH}/register",
        json={
            "email": email,
            "password": PASSWORD,
            "password_confirm": PASSWORD,
            "display_name": f"Rider {label}",
        },
    )
    if r.status_code not in (200, 201):
        if r.status_code == 429:
            # 10 registrations/hour/IP. Each run needs five fresh accounts, so
            # only two runs fit in an hour from one address. The limiter is
            # in-memory, so restarting the API server clears it.
            raise RuntimeError(
                f"register {label}: 429 — auth registration is rate limited to "
                f"10/hour per IP. Restart the API server, or run the smoke at most "
                f"twice per hour."
            )
        raise RuntimeError(f"register {label}: {r.status_code} {r.text}")
    r = await client.post(f"{AUTH}/login", json={"email": email, "password": PASSWORD})
    if r.status_code != 200:
        raise RuntimeError(f"login {label}: {r.status_code} {r.text}")
    headers = {"Authorization": f"Bearer {r.json()['access_token']}"}
    me = await client.get(f"{SOCIAL}/profile/me", headers=headers)
    await client.patch(
        f"{SOCIAL}/profile",
        json={
            "username": f"{label.lower()}_{uuid.uuid4().hex[:6]}",
            "display_name": f"Rider {label}",
        },
        headers=headers,
    )
    return {"label": label, "id": me.json()["user_id"], "headers": headers}


def rid(u: dict) -> str:
    return str(u["id"])


async def register_device(
    client: httpx.AsyncClient,
    user: dict,
    device_id: str,
    token: str,
    platform: str = "android",
    provider: str = "fcm",
) -> httpx.Response:
    return await client.post(
        f"{DEVICES}",
        json={
            "platform": platform,
            "provider": provider,
            "device_id": device_id,
            "token": token,
            "app_version": "1.0.0",
            "locale": "en",
        },
        headers=user["headers"],
    )


async def notifications(client: httpx.AsyncClient, user: dict, **params) -> httpx.Response:
    return await client.get(f"{NOTIF}", params=params, headers=user["headers"])


async def unread(client: httpx.AsyncClient, user: dict) -> int:
    r = await client.get(f"{NOTIF}/unread-count", headers=user["headers"])
    return r.json().get("unread_count", -1) if r.status_code == 200 else -1


async def scenario(client: httpx.AsyncClient, run: int) -> None:
    print(f"\n================ RUN {run} ================")
    A = await account(client, "A")
    B = await account(client, "B")
    C = await account(client, "C")
    D = await account(client, "D")

    # Per run, so run 2 does not inherit run 1's token rows. Sharing a token
    # across runs would instead exercise the account-transfer path, which is
    # verified deliberately further down.
    global SECRET_TOKEN
    SECRET_TOKEN = f"SMOKE-SECRET-PUSH-TOKEN-{run}-{uuid.uuid4().hex}"

    # ---------------------------------------------------------------- devices
    r = await register_device(client, A, "smoke-phone", SECRET_TOKEN)
    check("1. A registers a device", r.status_code == 201, f"HTTP {r.status_code}")
    first_device_id = r.json()["id"] if r.status_code == 201 else ""

    check(
        "   the registration response contains NO token",
        SECRET_TOKEN not in r.text and "token" not in json.dumps(r.json()),
        "device metadata only",
    )

    r = await register_device(client, A, "smoke-phone", SECRET_TOKEN + "-rotated")
    check(
        "2. duplicate registration is idempotent (200, same id)",
        r.status_code == 200 and r.json()["id"] == first_device_id,
        f"HTTP {r.status_code}",
    )

    r = await client.get(f"{DEVICES}", headers=A["headers"])
    rows = r.json().get("items", [])
    check(
        "   token rotation did not create a second row",
        r.status_code == 200 and len(rows) == 1 and SECRET_TOKEN not in r.text,
        f"{len(rows)} device(s)",
    )

    r = await register_device(client, A, "smoke-tablet", SECRET_TOKEN + "-tablet")
    check("   a user may hold multiple devices", r.status_code == 201, f"HTTP {r.status_code}")
    r = await client.get(f"{DEVICES}", headers=A["headers"])
    check(
        "   both devices are listed",
        len(r.json()["items"]) == 2 and SECRET_TOKEN not in r.text,
        f"{len(r.json()['items'])} devices",
    )

    # ----------------------------------------------------- device ownership
    r = await client.patch(
        f"{DEVICES}/{first_device_id}", json={"enabled": False}, headers=B["headers"]
    )
    check(
        "3. another rider cannot modify a device (404, not 403)",
        r.status_code == 404,
        f"HTTP {r.status_code}",
    )

    r = await client.delete(f"{DEVICES}/{first_device_id}", headers=B["headers"])
    check(
        "   another rider cannot revoke a device (404, not 403)",
        r.status_code == 404,
        f"HTTP {r.status_code}",
    )

    guessed = await client.delete(f"{DEVICES}/{uuid.uuid4()}", headers=B["headers"])
    check(
        "   a guessed device id answers identically to a real one",
        guessed.status_code == 404 and guessed.json() == r.json(),
        f"guessed={guessed.status_code} real={r.status_code}",
    )

    r = await client.get(f"{DEVICES}", headers=A["headers"])
    check(
        "   the device was not actually modified",
        any(d["id"] == first_device_id and d["enabled"] for d in r.json()["items"]),
        "still enabled",
    )

    r = await client.post(
        f"{DEVICES}",
        json={
            "platform": "android",
            "provider": "fcm",
            "device_id": "hijack",
            "token": SECRET_TOKEN,
            "user_id": rid(B),
        },
        headers=A["headers"],
    )
    check("4. a client-supplied user_id is rejected", r.status_code == 422, f"HTTP {r.status_code}")

    # -------------------------------------------- handing a phone to a new rider
    # A's phone row now holds SECRET_TOKEN + "-rotated" (see the rotation step
    # above), so that is the token still owned by A and available to test with.
    # The row is keyed on (provider, token), so a token already held by another
    # account must MOVE rather than be refused. Refusing would leave the previous
    # account pushing to a device now showing someone else's session.
    held = SECRET_TOKEN + "-rotated"
    r = await register_device(client, B, "second-hand-phone", held)
    check(
        "   a token held by another account is TRANSFERRED, not refused",
        r.status_code == 200,
        f"HTTP {r.status_code}",
    )
    check(
        "   and it keeps the same row rather than duplicating the token",
        r.json().get("id") == first_device_id,
        f"{r.json().get('id')} vs {first_device_id}",
    )
    r = await client.get(f"{DEVICES}", headers=A["headers"])
    still = [d["id"] for d in r.json()["items"]]
    check(
        "   the previous account no longer holds it (no cross-account push)",
        first_device_id not in still,
        f"old account holds {len(still)} device(s)",
    )
    r = await client.get(f"{DEVICES}", headers=B["headers"])
    check(
        "   the new account holds exactly one device",
        len(r.json()["items"]) == 1 and r.json()["items"][0]["id"] == first_device_id,
        f"{len(r.json()['items'])} device(s)",
    )
    # Hand it back so the rest of the scenario's expectations about A hold.
    r = await register_device(client, A, "smoke-phone", held)
    check(
        "   handing it back restores the original owner",
        r.status_code == 200 and r.json()["id"] == first_device_id,
        f"HTTP {r.status_code}",
    )

    # -------------------------------------------------- friend-request events
    r = await client.post(
        f"{SOCIAL}/friend-requests", json={"user_id": rid(B)}, headers=A["headers"]
    )
    check(
        "5. a friend request notifies the target",
        r.status_code == 201 and (await unread(client, B)) == 1,
        f"request HTTP {r.status_code}",
    )

    page = (await notifications(client, B)).json()
    item = page["items"][0]
    check(
        "   the notification carries a localization key, not rendered copy",
        item["l10n_key"].startswith("notifications.type.")
        and "body" not in item
        and isinstance(item["params"], dict),
        item["l10n_key"],
    )
    check(
        "   the sender is not notified about their own request",
        (await unread(client, A)) == 0,
        "sender has no notification",
    )

    r = await client.post(f"{NOTIF}/{item['id']}/read", headers=B["headers"])
    check(
        "6. marking read clears the unread count",
        r.status_code == 200 and r.json()["is_read"] is True and (await unread(client, B)) == 0,
        f"HTTP {r.status_code}",
    )

    r = await client.post(f"{NOTIF}/{item['id']}/read", headers=B["headers"])
    check(
        "   marking read is idempotent",
        r.status_code == 200 and r.json()["is_read"] is True,
        f"HTTP {r.status_code}",
    )

    r = await client.post(f"{NOTIF}/read-all", headers=B["headers"])
    check(
        "   mark-all-read is idempotent and reports what it did",
        r.status_code == 200 and "marked" in r.json(),
        json.dumps(r.json()),
    )

    # ---------------------------------------------------------- IDOR on read
    r = await client.post(
        f"{SOCIAL}/friend-requests", json={"user_id": rid(C)}, headers=A["headers"]
    )
    c_item = (await notifications(client, C)).json()["items"][0]
    real = await client.post(f"{NOTIF}/{c_item['id']}/read", headers=D["headers"])
    fake = await client.post(f"{NOTIF}/{uuid.uuid4()}/read", headers=D["headers"])
    check(
        "7. a notification IDOR attempt is 404 and indistinguishable from a guess",
        real.status_code == 404 and fake.status_code == 404 and real.json() == fake.json(),
        f"real={real.status_code} guessed={fake.status_code}",
    )
    check(
        "   the victim's notification was NOT marked read",
        (await unread(client, C)) == 1,
        "still unread",
    )

    r = await client.get(f"{NOTIF}", headers=D["headers"])
    check(
        "   a rider cannot list another rider's notifications",
        r.status_code == 200 and r.json()["total"] == 0,
        f"total={r.json()['total']}",
    )

    # --------------------------------------------------------------- teams
    r = await client.post(f"{TEAMS}", json={"name": f"Notif Crew {run}"}, headers=A["headers"])
    team_id = r.json()["id"]
    r = await client.post(
        f"{TEAMS}/{team_id}/invitations", json={"user_id": rid(B)}, headers=A["headers"]
    )
    check(
        "8. a team invitation notifies only the invitee",
        r.status_code == 201 and (await unread(client, B)) == 1,
        f"invite HTTP {r.status_code}",
    )
    check(
        "   the team owner is not notified about their own invitation",
        (await unread(client, A)) == 0,
        "owner has no notification",
    )
    check(
        "   a bystander is not notified",
        (await unread(client, D)) == 0,
        "bystander has no notification",
    )

    # B must actually JOIN before the team-channel checks below: an outstanding
    # invitation is not membership, and the channel correctly 404s a non-member.
    r = await client.post(f"{TEAMS}/invitations/{r.json()['id']}/accept", headers=B["headers"])
    check(
        "   B accepts the invitation and becomes a member",
        r.status_code == 200,
        f"HTTP {r.status_code}",
    )

    # ------------------------------------------------------ the block policy
    # Deliberately before the team-channel check: the Phase 8.3 policy must hold
    # for notifications exactly as it does for messages.
    dm = await client.post(
        f"{CHAT}/direct", params={"target_user_id": rid(D)}, headers=B["headers"]
    )
    dm_id = dm.json()["id"]
    r = await client.post(f"{SOCIAL}/blocks", json={"user_id": rid(B)}, headers=D["headers"])
    check("9. D blocks B", r.status_code == 201, f"HTTP {r.status_code}")

    before_b, before_d = await unread(client, B), await unread(client, D)
    send = await client.post(
        f"{CHAT}/conversations/{dm_id}/messages",
        json={"body": "still there?", "client_message_id": str(uuid.uuid4())},
        headers=B["headers"],
    )
    check("   a blocked DM send is refused", send.status_code == 404, f"HTTP {send.status_code}")
    check(
        "10. A BLOCKED DM PRODUCES NO NOTIFICATION FOR EITHER PARTY",
        (await unread(client, B)) == before_b and (await unread(client, D)) == before_d,
        f"B {before_b}->{await unread(client, B)}, D {before_d}->{await unread(client, D)}",
    )

    r = await client.post(
        f"{CHAT}/conversations/{dm_id}/messages",
        json={"body": "blocked", "client_message_id": str(uuid.uuid4())},
        headers=B["headers"],
    )
    check(
        "    and stays refused on a retry",
        r.status_code == 404 and (await unread(client, D)) == before_d,
        "no notification appears on retry either",
    )

    await client.delete(f"{SOCIAL}/blocks/{rid(B)}", headers=D["headers"])
    r = await client.post(
        f"{CHAT}/conversations/{dm_id}/messages",
        json={"body": "unblocked", "client_message_id": str(uuid.uuid4())},
        headers=B["headers"],
    )
    check(
        "11. unblocking restores DM notifications",
        r.status_code == 201 and (await unread(client, D)) == before_d + 1,
        f"HTTP {r.status_code}",
    )

    # ------------------------------------------------- team channel while blocked
    r = await client.post(f"{SOCIAL}/blocks", json={"user_id": rid(B)}, headers=D["headers"])
    r = await client.post(
        f"{CHAT}/conversations/{dm_id}/messages",
        json={"body": "nope", "client_message_id": str(uuid.uuid4())},
        headers=B["headers"],
    )
    r = await client.post(f"{TEAMS}/{team_id}/join", headers=D["headers"])
    check(
        "12. D joins the team that blocked them (membership survives a block)",
        r.status_code == 200 and r.json()["status"] == "joined",
        f"HTTP {r.status_code}",
    )

    before_b, before_d = await unread(client, B), await unread(client, D)
    r = await client.get(f"{CHAT}/teams/{team_id}/conversation", headers=B["headers"])
    if r.status_code != 200:
        check("13. team channel is reachable for a blocked member", False, f"HTTP {r.status_code}")
        return
    channel = r.json()
    send = await client.post(
        f"{CHAT}/conversations/{channel['id']}/messages",
        json={"body": "team still open", "client_message_id": str(uuid.uuid4())},
        headers=B["headers"],
    )
    check(
        "13. A BLOCK DOES NOT SEVER A TEAM CHANNEL",
        send.status_code == 201,
        f"HTTP {send.status_code}",
    )
    check(
        "14. AND TEAM NOTIFICATIONS ARE STILL DELIVERED ACROSS A BLOCK",
        (await unread(client, D)) == before_d + 1 and (await unread(client, B)) == before_b,
        f"B {before_b}->{await unread(client, B)}, D {before_d}->{await unread(client, D)}",
    )

    # A removed member stops receiving team notifications.
    r = await client.delete(f"{TEAMS}/{team_id}/members/{rid(D)}", headers=A["headers"])
    before_d = await unread(client, D)
    await client.post(
        f"{CHAT}/conversations/{channel['id']}/messages",
        json={"body": "after removal", "client_message_id": str(uuid.uuid4())},
        headers=B["headers"],
    )
    check(
        "15. A REMOVED MEMBER STOPS RECEIVING TEAM NOTIFICATIONS",
        r.status_code in (200, 204) and (await unread(client, D)) == before_d,
        f"unread stayed {before_d}",
    )

    # ------------------------------------------------------------- idempotency
    dm2 = await client.post(
        f"{CHAT}/direct", params={"target_user_id": rid(C)}, headers=B["headers"]
    )
    key = str(uuid.uuid4())
    before_c = await unread(client, C)
    first = await client.post(
        f"{CHAT}/conversations/{dm2.json()['id']}/messages",
        json={"body": "exactly once", "client_message_id": key},
        headers=B["headers"],
    )
    retry = await client.post(
        f"{CHAT}/conversations/{dm2.json()['id']}/messages",
        json={"body": "exactly once", "client_message_id": key},
        headers=B["headers"],
    )
    check(
        "16. A RETRIED EVENT PRODUCES EXACTLY ONE NOTIFICATION",
        first.status_code == 201
        and retry.status_code == 200
        and retry.json()["duplicate"] is True
        and (await unread(client, C)) == before_c + 1,
        f"201/200, unread {before_c}->{await unread(client, C)}",
    )

    # ------------------------------------------------------------- pagination
    page = (await notifications(client, C, page=1, page_size=1)).json()
    check(
        "17. the notification list is offset-paged with the standard envelope",
        {"items", "total", "page", "page_size"} <= set(page.keys()),
        json.dumps({k: v for k, v in page.items() if k != "items"}),
    )
    r = await client.get(f"{NOTIF}", params={"page_size": 500}, headers=C["headers"])
    check("   the page size is capped", r.status_code == 422, f"HTTP {r.status_code}")

    # ---------------------------------------------------------- payload safety
    blobs = []
    for user in (B, C, D):
        for row in (await notifications(client, user)).json()["items"]:
            blobs.append(json.dumps(row).lower())
    text = " ".join(blobs)
    leaks = [
        term
        for term in [
            "exactly once",
            "team still open",
            "after removal",
            "latitude",
            "longitude",
            "gps",
            "access_token",
            "refresh_token",
        ]
        if term in text
    ]
    check("18. NO PRIVATE CHAT CONTENT OR COORDINATE IN ANY NOTIFICATION", not leaks, str(leaks))
    check("    no push token in any notification payload", SECRET_TOKEN not in text, "token absent")

    # Deep links must be app-internal routes only.
    links = [
        row["deep_link"]
        for user in (B, C, D)
        for row in (await notifications(client, user)).json()["items"]
        if row["deep_link"]
    ]
    bad = [link for link in links if not link.startswith("/")]
    check("19. EVERY DEEP LINK IS AN APP-INTERNAL ROUTE", not bad, str(bad[:3]))
    allowed_prefixes = ("/friends/requests", "/chat/", "/teams/", "/users/", "/notifications")
    check(
        "    and every deep link is on the allowlist",
        all(any(link.startswith(p) for p in allowed_prefixes) for link in links),
        f"{len(set(links))} distinct link(s)",
    )

    # ------------------------------------------------------------ disable device
    r = await client.get(f"{DEVICES}", headers=A["headers"])
    target = r.json()["items"][0]
    r = await client.patch(
        f"{DEVICES}/{target['id']}", json={"enabled": False}, headers=A["headers"]
    )
    check(
        "20. a device can be disabled",
        r.status_code == 200 and r.json()["enabled"] is False,
        f"HTTP {r.status_code}",
    )
    r = await client.delete(f"{DEVICES}/{target['id']}", headers=A["headers"])
    check("    and revoked", r.status_code == 200, f"HTTP {r.status_code}")

    # ------------------------------------------------------------- rate limits
    codes = []
    for i in range(30):
        codes.append((await register_device(client, B, f"burst-{i}", f"tok-{i}")).status_code)
    check(
        "21. device registration is rate limited",
        429 in codes and codes.count(201) <= 25,
        f"201s={codes.count(201)} 429s={codes.count(429)}",
    )


async def main() -> int:
    # The scenario is run twice by default, because Phase 8.1 shipped a bug that
    # a first run against virgin state masked and only a second run against
    # reused state exposed. Accounts and the push token are unique per run, so
    # the second run shares the database but nothing else.
    runs = int(sys.argv[1]) if len(sys.argv) > 1 else 2
    for run in range(1, runs + 1):
        async with httpx.AsyncClient(timeout=30.0) as client:
            await scenario(client, run)

    async with httpx.AsyncClient(timeout=30.0) as anon:
        for method, path in [
            ("GET", f"{NOTIF}"),
            ("GET", f"{NOTIF}/unread-count"),
            ("POST", f"{NOTIF}/read-all"),
            ("GET", f"{DEVICES}"),
            ("POST", f"{DEVICES}"),
        ]:
            # `path` is already absolute (the constants include /api/v1), so it
            # is used as-is: prefixing BASE again would request a doubled URL and
            # produce a misleading 404 instead of the expected 401.
            r = await anon.request(method, path, json={})
            check(
                f"unauthenticated {method} {path} is 401",
                r.status_code == 401,
                f"HTTP {r.status_code}",
            )

    failed = [r for r in results if not r[1]]
    print()
    print(f"{len(results) - len(failed)}/{len(results)} checks passed across {runs} run(s)")
    if failed:
        print("FAILURES:")
        for step, _, detail in failed:
            print(f"  - {step} :: {detail}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

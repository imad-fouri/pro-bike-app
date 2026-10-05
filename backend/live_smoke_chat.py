"""Phase 8.3 live smoke: team channels & direct messages (ADR-14).

Real uvicorn, real PostgreSQL, real Redis. Nothing in app/ is stubbed.

Runs the SAME scenario twice by design. Phase 8.1 shipped a bug that a first
run masked and only a second run against reused state exposed (a lazy-load 500
that fires only when a projection does not yet exist), and Phase 8.3 has the same
shape of hazard: the second run finds a team whose channel already exists, a DM
that was already opened, and an idempotency key that was already spent. Accounts
are unique per run; conversations are not, so the script must be safe against
state it did not create.

The order of the checks is load-bearing. Privacy checks (who can read what) run
before mutation checks, so a bug that leaks cannot be masked by a later step
failing first.
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
PASSWORD = "Cyclecoach2026pass"
DOMAIN = "smoke.example.com"

results: list[tuple[str, bool, str]] = []


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
        raise RuntimeError(f"register {label}: {r.status_code} {r.text}")
    r = await client.post(f"{AUTH}/login", json={"email": email, "password": PASSWORD})
    if r.status_code != 200:
        raise RuntimeError(f"login {label}: {r.status_code} {r.text}")
    headers = {"Authorization": f"Bearer {r.json()['access_token']}"}
    me = await client.get(f"{SOCIAL}/profile/me", headers=headers)
    uid = me.json()["user_id"]
    await client.patch(
        f"{SOCIAL}/profile",
        json={
            "username": f"{label.lower()}_{uuid.uuid4().hex[:6]}",
            "display_name": f"Rider {label}",
        },
        headers=headers,
    )
    return {"label": label, "id": uid, "headers": headers}


def rid(u: dict) -> str:
    return str(u["id"])


async def team_channel(client: httpx.AsyncClient, user: dict, team_id: str) -> dict:
    r = await client.get(f"{CHAT}/teams/{team_id}/conversation", headers=user["headers"])
    if r.status_code != 200:
        raise RuntimeError(f"team channel {team_id}: {r.status_code} {r.text}")
    return r.json()


async def send(
    client: httpx.AsyncClient,
    user: dict,
    conversation_id: str,
    body: str,
    client_id: str | None = None,
) -> httpx.Response:
    return await client.post(
        f"{CHAT}/conversations/{conversation_id}/messages",
        json={"body": body, "client_message_id": client_id or str(uuid.uuid4())},
        headers=user["headers"],
    )


async def scenario(client: httpx.AsyncClient, run: int) -> None:
    print(f"\n================ RUN {run} ================")
    A = await account(client, "A")
    B = await account(client, "B")
    C = await account(client, "C")
    D = await account(client, "D")

    # --- A creates a team and B joins ---------------------------------------
    r = await client.post(
        f"{TEAMS}",
        json={
            "name": f"Atlas CC {run}",
            "handle": f"atlas_{uuid.uuid4().hex[:6]}",
            "visibility": "public",
        },
        headers=A["headers"],
    )
    check("1. A creates a team", r.status_code == 201, f"HTTP {r.status_code}")
    if r.status_code != 201:
        return
    tid = r.json()["id"]
    r = await client.post(f"{TEAMS}/{tid}/join", headers=B["headers"])
    check(
        "2. B joins the team",
        r.status_code == 200 and r.json()["status"] == "joined",
        f"HTTP {r.status_code}",
    )

    # --- one channel per team -------------------------------------------------
    channel = await team_channel(client, A, tid)
    again = await team_channel(client, A, tid)
    from_b = await team_channel(client, B, tid)
    check(
        "3. EXACTLY ONE CHANNEL PER TEAM",
        channel["id"] == again["id"] == from_b["id"] and channel["kind"] == "team",
        f"{channel['id']}",
    )
    cid = channel["id"]

    r = await client.get(f"{CHAT}/teams/{tid}/conversation", headers=C["headers"])
    check(
        "   a non-member cannot open a team channel", r.status_code == 404, f"HTTP {r.status_code}"
    )

    # --- authorization is 404, never 403 ------------------------------------
    r = await client.get(f"{CHAT}/conversations/{cid}", headers=C["headers"])
    missing = await client.get(f"{CHAT}/conversations/{uuid.uuid4()}", headers=C["headers"])
    check(
        "4. UNAUTHORIZED AND MISSING ARE BOTH 404, AND IDENTICAL",
        r.status_code == 404 and missing.status_code == 404 and r.json() == missing.json(),
        f"{r.status_code}/{missing.status_code}",
    )

    r = await client.get(f"{CHAT}/conversations/{cid}/messages", headers=C["headers"])
    check("   a non-member cannot read the history", r.status_code == 404, f"HTTP {r.status_code}")
    r = await send(client, C, cid, "sneaking in")
    check("   a non-member cannot send", r.status_code == 404, f"HTTP {r.status_code}")

    # --- sending, ordering, idempotency --------------------------------------
    first = await send(client, A, cid, "bonjour l'équipe")
    check(
        "5. A sends a message",
        first.status_code == 201 and first.json()["duplicate"] is False,
        f"HTTP {first.status_code}",
    )
    check(
        "   the first message is seq 1",
        first.json()["message"]["seq"] == 1,
        f"seq={first.json()['message']['seq']}",
    )
    check(
        "   the sender is flagged as the viewer",
        first.json()["message"]["is_mine"] is True and first.json()["message"]["can_edit"] is True,
        "is_mine/can_edit from the server",
    )

    for i in range(4):
        await send(client, B if i % 2 else A, cid, f"message {i}")
    r = await client.get(f"{CHAT}/conversations/{cid}/messages", headers=A["headers"])
    seqs = [m["seq"] for m in r.json()["items"]]
    check("6. sequences are dense, monotonic, and newest-first", seqs == [5, 4, 3, 2, 1], f"{seqs}")

    key = str(uuid.uuid4())
    one = await send(client, A, cid, "exactly once", key)
    two = await send(client, A, cid, "exactly once", key)
    check(
        "7. RETRYING A CLIENT MESSAGE ID STORES ONE MESSAGE",
        one.status_code == 201
        and two.status_code == 200
        and two.json()["duplicate"] is True
        and two.json()["message"]["id"] == one.json()["message"]["id"],
        f"{one.status_code}/{two.status_code} duplicate={two.json().get('duplicate')}",
    )

    # The same client id from a different sender is a different message.
    r = await send(client, B, cid, "exactly once", key)
    check(
        "   the same client id from another sender is a new message",
        r.status_code == 201 and r.json()["message"]["id"] != one.json()["message"]["id"],
        f"HTTP {r.status_code}",
    )

    # --- cursor pagination ----------------------------------------------------
    r = await client.get(
        f"{CHAT}/conversations/{cid}/messages", params={"limit": 3}, headers=A["headers"]
    )
    page1 = r.json()
    check(
        "8. history is a CURSOR envelope with no total",
        set(page1.keys()) == {"items", "has_more", "next_before_seq"},
        json.dumps({k: v for k, v in page1.items() if k != "items"}),
    )
    check(
        "   page 1 is the newest three",
        [m["seq"] for m in page1["items"]] == [7, 6, 5] and page1["has_more"] is True,
        f"{[m['seq'] for m in page1['items']]}",
    )

    # Page one was already fetched above; seed `seen` with it so the walk below
    # measures the whole thread rather than only the pages after the first.
    seen: list[int] = [m["seq"] for m in page1["items"]]
    pages: list[str] = [f"first -> {[m['seq'] for m in page1['items']]}"]
    cursor = page1["next_before_seq"]
    while cursor is not None:
        r = await client.get(
            f"{CHAT}/conversations/{cid}/messages",
            params={"limit": 3, "before_seq": cursor},
            headers=A["headers"],
        )
        page = r.json()
        seqs = [m["seq"] for m in page.get("items", [])]
        pages.append(f"before={cursor} -> {seqs} (HTTP {r.status_code})")
        seen.extend(seqs)
        cursor = page.get("next_before_seq")
        if len(pages) > 10:
            break
    check(
        "   walking the cursor sees every message exactly once",
        sorted(seen) == list(range(1, 8)) and len(seen) == 7,
        f"{sorted(seen)} via {'; '.join(pages)}",
    )

    r = await client.get(
        f"{CHAT}/conversations/{cid}/messages", params={"limit": 500}, headers=A["headers"]
    )
    check("   history limit is capped", r.status_code == 422, f"HTTP {r.status_code}")

    # --- read state -----------------------------------------------------------
    r = await client.get(f"{CHAT}/conversations", headers=A["headers"])
    unread = r.json()["items"][0]["unread_count"] if r.json()["items"] else None
    check("9. unread count is derived from the read mark", unread == 7, f"unread={unread}")

    r = await client.post(
        f"{CHAT}/conversations/{cid}/read", params={"seq": 4}, headers=A["headers"]
    )
    check(
        "   marking read advances the high-water mark",
        r.status_code == 200 and r.json()["last_read_seq"] == 4,
        f"HTTP {r.status_code} {r.json().get('last_read_seq')}",
    )

    # Checked here, before the clamp below: the clamp deliberately marks
    # everything read, so asserting afterwards would only ever see zero.
    r = await client.get(f"{CHAT}/conversations", headers=A["headers"])
    check(
        "   unread drops to what is genuinely unread",
        r.json()["items"][0]["unread_count"] == 3,
        f"unread={r.json()['items'][0]['unread_count']}, expected 3",
    )

    r = await client.post(
        f"{CHAT}/conversations/{cid}/read", params={"seq": 2}, headers=A["headers"]
    )
    check(
        "   THE READ MARK NEVER MOVES BACKWARDS",
        r.status_code == 200 and r.json()["last_read_seq"] == 4,
        f"stale mark answered {r.json().get('last_read_seq')}",
    )

    r = await client.post(
        f"{CHAT}/conversations/{cid}/read", params={"seq": 9999}, headers=A["headers"]
    )
    check(
        "   the read mark cannot run past the last message",
        r.status_code == 200 and r.json()["last_read_seq"] == 7,
        f"clamped to {r.json().get('last_read_seq')}",
    )

    # --- edit and soft delete -------------------------------------------------
    own = (await client.get(f"{CHAT}/conversations/{cid}/messages", headers=A["headers"])).json()[
        "items"
    ][-1]
    foreign = (
        await client.get(f"{CHAT}/conversations/{cid}/messages", headers=A["headers"])
    ).json()["items"][0]
    r = await client.patch(
        f"{CHAT}/messages/{own['id']}", json={"body": "corrigé"}, headers=A["headers"]
    )
    check(
        "10. the author can edit inside the window",
        r.status_code == 200 and r.json()["body"] == "corrigé" and r.json()["is_edited"] is True,
        f"HTTP {r.status_code}",
    )

    r = await client.patch(
        f"{CHAT}/messages/{foreign['id']}", json={"body": "hijack"}, headers=A["headers"]
    )
    check(
        "    a non-author cannot edit (404, not 403)", r.status_code == 404, f"HTTP {r.status_code}"
    )

    r = await client.delete(f"{CHAT}/messages/{foreign['id']}", headers=A["headers"])
    check(
        "    a non-author cannot delete (404, not 403)",
        r.status_code == 404,
        f"HTTP {r.status_code}",
    )

    r = await client.delete(f"{CHAT}/messages/{own['id']}", headers=A["headers"])
    check(
        "11. delete is SOFT: the body is replaced, the row survives",
        r.status_code == 200 and r.json()["body"] == "[deleted]" and r.json()["is_deleted"] is True,
        f"HTTP {r.status_code} body={r.json().get('body')}",
    )

    r = await client.get(f"{CHAT}/conversations/{cid}/messages", headers=A["headers"])
    row = next(m for m in r.json()["items"] if m["id"] == own["id"])
    check(
        "    the deleted row keeps its place in the ordering",
        row["seq"] == own["seq"] and row["body"] == "[deleted]",
        f"seq={row['seq']}",
    )

    r = await client.patch(
        f"{CHAT}/messages/{own['id']}", json={"body": "back"}, headers=A["headers"]
    )
    check("    a deleted message cannot be edited", r.status_code == 404, f"HTTP {r.status_code}")

    # --- direct messages ------------------------------------------------------
    r = await client.post(f"{CHAT}/direct", params={"target_user_id": rid(C)}, headers=A["headers"])
    check(
        "12. A opens a DM with C (friendship is not required)",
        r.status_code == 201 and r.json()["kind"] == "direct",
        f"HTTP {r.status_code}",
    )
    dm = r.json()["id"]

    r = await client.post(f"{CHAT}/direct", params={"target_user_id": rid(C)}, headers=A["headers"])
    check(
        "    opening the same DM again returns the same conversation",
        r.status_code == 201 and r.json()["id"] == dm,
        f"HTTP {r.status_code}",
    )

    r = await client.post(f"{CHAT}/direct", params={"target_user_id": rid(A)}, headers=A["headers"])
    check("    you cannot DM yourself", r.status_code == 422, f"HTTP {r.status_code}")

    await send(client, A, dm, "salut C")
    r = await client.get(f"{CHAT}/conversations/{dm}/messages", headers=D["headers"])
    check("13. a stranger cannot read a DM", r.status_code == 404, f"HTTP {r.status_code}")
    r = await send(client, D, dm, "butting in")
    check("    a stranger cannot send into a DM", r.status_code == 404, f"HTTP {r.status_code}")

    # --- blocks: DMs only, and in both directions -----------------------------
    r = await client.post(f"{SOCIAL}/blocks", json={"user_id": rid(A)}, headers=C["headers"])
    check("14. C blocks A", r.status_code == 201, f"HTTP {r.status_code}")

    r = await client.post(f"{CHAT}/direct", params={"target_user_id": rid(A)}, headers=C["headers"])
    r2 = await client.post(
        f"{CHAT}/direct", params={"target_user_id": rid(C)}, headers=A["headers"]
    )
    check(
        "15. A BLOCK STOPS DM CREATION IN BOTH DIRECTIONS (404, no oracle)",
        r.status_code == 404 and r2.status_code == 404,
        f"blocker={r.status_code} blocked={r2.status_code}",
    )

    unknown = uuid.uuid4()
    r3 = await client.get(f"{CHAT}/conversations/{unknown}", headers=A["headers"])
    check(
        "    a blocked rider and a non-existent rider are indistinguishable",
        r.status_code == r3.status_code == 404,
        f"block={r.status_code} unknown={r3.status_code}",
    )

    r = await send(client, A, dm, "still there?")
    r2 = await send(client, C, dm, "no")
    check(
        "    a block stops DM sending in both directions",
        r.status_code == 404 and r2.status_code == 404,
        f"A={r.status_code} C={r2.status_code}",
    )

    r = await send(client, A, cid, "team still open")
    check(
        "16. A BLOCK DOES NOT SEVER A TEAM CHANNEL",
        r.status_code == 201,
        f"A is a team member, not C's friend-block victim: HTTP {r.status_code}",
    )

    r = await client.delete(f"{SOCIAL}/blocks/{rid(A)}", headers=C["headers"])
    check("17. C unblocks A", r.status_code == 200, f"HTTP {r.status_code}")
    r = await client.get(f"{CHAT}/conversations/{dm}/messages", headers=A["headers"])
    check(
        "    UNBLOCKING RESTORES THE EXACT HISTORY (nothing was deleted)",
        r.status_code == 200
        and len(r.json()["items"]) == 1
        and r.json()["items"][0]["body"] == "salut C",
        f"{len(r.json()['items']) if r.status_code == 200 else r.status_code} message(s)",
    )

    # --- team removal is a live authorization check, not a stale roster -------
    r = await client.delete(f"{TEAMS}/{tid}/members/{rid(B)}", headers=A["headers"])
    check("18. A removes B from the team", r.status_code in (200, 204), f"HTTP {r.status_code}")
    r = await client.get(f"{CHAT}/conversations/{cid}/messages", headers=B["headers"])
    check(
        "    REMOVING A RIDER CUTS OFF CHAT IMMEDIATELY",
        r.status_code == 404,
        f"HTTP {r.status_code}",
    )
    r = await send(client, B, cid, "still a member?")
    check("    and cuts off sending immediately", r.status_code == 404, f"HTTP {r.status_code}")
    r = await client.get(f"{CHAT}/conversations", headers=B["headers"])
    check(
        "    the inbox drops a channel the viewer has no standing for",
        all(c["id"] != cid for c in r.json()["items"]),
        f"{len(r.json()['items'])} conversation(s) left",
    )
    r = await client.get(f"{CHAT}/conversations/{cid}/messages", headers=A["headers"])
    check(
        "    B's earlier messages remain in the history",
        r.status_code == 200 and any(m["sender_user_id"] == rid(B) for m in r.json()["items"]),
        "history is retained, not rewritten on removal",
    )

    # --- an archived team keeps its history and refuses new messages ----------
    r = await client.post(f"{TEAMS}", json={"name": f"Frozen {run}"}, headers=C["headers"])
    frozen_tid = r.json()["id"]
    frozen = await team_channel(client, C, frozen_tid)
    await send(client, C, frozen["id"], "before the freeze")
    r = await client.delete(f"{TEAMS}/{frozen_tid}", headers=C["headers"])
    check("19. C archives a team", r.status_code == 200, f"HTTP {r.status_code}")
    r = await send(client, C, frozen["id"], "after the freeze")
    check(
        "    an ARCHIVED TEAM ACCEPTS NO NEW MESSAGES",
        r.status_code == 403 and r.json()["error"]["code"] == "CHAT_TEAM_ARCHIVED",
        f"HTTP {r.status_code}",
    )
    r = await client.get(f"{CHAT}/conversations/{frozen['id']}/messages", headers=C["headers"])
    check(
        "    an archived team's history stays readable",
        r.status_code == 200 and len(r.json()["items"]) == 1,
        f"HTTP {r.status_code}",
    )

    # --- validation -----------------------------------------------------------
    r = await send(client, A, cid, "   ")
    check("20. a whitespace-only body is rejected", r.status_code == 422, f"HTTP {r.status_code}")
    r = await send(client, A, cid, "x" * 4001)
    check("    an over-long body is rejected", r.status_code == 422, f"HTTP {r.status_code}")
    r = await client.post(
        f"{CHAT}/conversations/{cid}/messages",
        json={"body": "hi", "client_message_id": str(uuid.uuid4()), "sender": "me"},
        headers=A["headers"],
    )
    check("    an unknown body field is rejected", r.status_code == 422, f"HTTP {r.status_code}")

    # --- privacy: nothing private or locational ever leaves ------------------
    dm_body = (await client.get(f"{CHAT}/conversations/{dm}", headers=A["headers"])).json()
    history = (
        await client.get(f"{CHAT}/conversations/{cid}/messages", headers=A["headers"])
    ).json()
    leaks: list[str] = []
    for payload in (dm_body, history, channel):
        text = json.dumps(payload).lower()
        for key in [
            "email",
            "password",
            "token",
            "latitude",
            "longitude",
            "gps",
            "location",
            "position",
        ]:
            if key in text:
                leaks.append(key)
    check("21. no email / token / GPS key in any chat payload", not leaks, str(leaks))

    message = history["items"][0]
    check(
        "    a message carries public identity only",
        {"sender_username", "sender_display_name"} <= set(message) and "email" not in message,
        str(sorted(message.keys())),
    )

    # --- rate limiting --------------------------------------------------------
    codes = []
    for _ in range(70):
        codes.append((await send(client, A, cid, "spam")).status_code)
    check(
        "22. send is rate limited",
        429 in codes and codes.count(201) <= 61,
        f"201s={codes.count(201)} 429s={codes.count(429)}",
    )


async def main() -> int:
    run = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    async with httpx.AsyncClient(timeout=30.0) as client:
        await scenario(client, run)

    async with httpx.AsyncClient(timeout=30.0) as anon:
        for path in [
            "/chat/conversations",
            "/chat/conversations/00000000-0000-0000-0000-000000000000/messages",
        ]:
            r = await anon.get(f"{BASE}{path}")
            check(
                f"unauthenticated GET {path} is 401", r.status_code == 401, f"HTTP {r.status_code}"
            )
        r = await anon.post(f"{CHAT}/direct", params={"target_user_id": str(uuid.uuid4())}, json={})
        check(
            "unauthenticated POST /chat/direct is 401",
            r.status_code == 401,
            f"HTTP {r.status_code}",
        )

    failed = [r for r in results if not r[1]]
    print()
    print(f"{len(results) - len(failed)}/{len(results)} checks passed")
    if failed:
        print("FAILURES:")
        for step, _, detail in failed:
            print(f"  - {step} :: {detail}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

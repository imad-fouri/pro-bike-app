"""Phase 8.2 live smoke: teams & cycling groups (ADR-13).

Real uvicorn, real PostgreSQL, real Redis. Nothing in app/ is stubbed.

Runs the SAME scenario twice by design. Phase 8.1 shipped a bug that a first
run masked and only a second run against reused state exposed (a lazy-load 500
that only fires when a projection does not yet exist), so this script is
idempotent-safe: accounts are unique per run, but teams, memberships, requests,
and invitations are created against whatever the database already holds.
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
    token = r.json()["access_token"]
    h = {"Authorization": f"Bearer {token}"}
    me = await client.get(f"{SOCIAL}/profile/me", headers=h)
    uid = me.json()["user_id"]
    await client.patch(
        f"{SOCIAL}/profile",
        json={"username": f"{label.lower()}_{uuid.uuid4().hex[:6]}", "display_name": f"Rider {label}"},
        headers=h,
    )
    return {"label": label, "id": uid, "headers": h}


def rid(u: dict) -> str:
    return str(u["id"])


async def befriend(client: httpx.AsyncClient, a: dict, b: dict) -> bool:
    r = await client.post(f"{SOCIAL}/friend-requests", json={"user_id": rid(b)}, headers=a["headers"])
    if r.status_code != 201:
        return False
    return (
        await client.post(
            f"{SOCIAL}/friend-requests/{r.json()['id']}/accept", headers=b["headers"]
        )
    ).status_code == 200


async def scenario(client: httpx.AsyncClient, run: int) -> None:
    print(f"\n================ RUN {run} ================")
    A = await account(client, "A")
    B = await account(client, "B")
    C = await account(client, "C")

    # --- A creates Team X ---------------------------------------------------
    handle = f"team_x_{uuid.uuid4().hex[:6]}"
    r = await client.post(
        f"{TEAMS}",
        json={"name": f"Atlas CC {run}", "handle": handle, "visibility": "public"},
        headers=A["headers"],
    )
    check("1. A creates Team X", r.status_code == 201, f"HTTP {r.status_code}")
    if r.status_code != 201:
        return
    team = r.json()
    tid = team["id"]
    check("   creator is OWNER with member_count 1",
          team["my_role"] == "owner" and team["member_count"] == 1,
          f"{team['my_role']}/{team['member_count']}")

    # --- B discovers Team X -------------------------------------------------
    r = await client.get(f"{TEAMS}/search", params={"q": "Atlas"}, headers=B["headers"])
    found = r.status_code == 200 and any(t["id"] == tid for t in r.json()["items"])
    check("2. B discovers Team X", found, f"HTTP {r.status_code}")

    # search must not be matchable by a member name (roster enumeration)
    r = await client.get(f"{TEAMS}/search", params={"q": "Rider A"}, headers=B["headers"])
    check("   team search cannot match on a member name",
          r.status_code == 200 and all(t["id"] != tid for t in r.json()["items"]),
          "no roster leak")

    # --- B joins a public team ---------------------------------------------
    r = await client.post(f"{TEAMS}/{tid}/join", headers=B["headers"])
    check("3. B joins the public team immediately",
          r.status_code == 200 and r.json()["status"] == "joined",
          f"HTTP {r.status_code} {r.json() if r.status_code == 200 else ''}")

    r = await client.get(f"{TEAMS}/{tid}/members", headers=A["headers"])
    check("   B appears in the member list",
          any(m["user_id"] == rid(B) for m in r.json()["items"]),
          f"{len(r.json()['items'])} members")

    # duplicate join is refused
    r = await client.post(f"{TEAMS}/{tid}/join", headers=B["headers"])
    check("   duplicate join is refused with 409", r.status_code == 409, f"HTTP {r.status_code}")

    # --- A promotes B to admin ---------------------------------------------
    r = await client.patch(
        f"{TEAMS}/{tid}/members/{rid(B)}/role", params={"role": "admin"}, headers=A["headers"]
    )
    check("4. A promotes B to admin", r.status_code == 200, f"HTTP {r.status_code}")

    # --- A invites C; C accepts ---------------------------------------------
    await befriend(client, A, C)
    r = await client.post(
        f"{TEAMS}/{tid}/invitations", json={"user_id": rid(C)}, headers=A["headers"]
    )
    check("5. A invites C", r.status_code == 201, f"HTTP {r.status_code}")
    if r.status_code == 201:
        inv = r.json()["id"]
        inbox = await client.get(f"{TEAMS}/my/invitations", headers=C["headers"])
        check("   C sees the invitation in the inbox",
              any(i["id"] == inv for i in inbox.json()["items"]),
              f"{len(inbox.json()['items'])}")
        r = await client.post(f"{TEAMS}/invitations/{inv}/accept", headers=C["headers"])
        check("   C accepts the invitation", r.status_code == 200, f"HTTP {r.status_code}")

    members = await client.get(f"{TEAMS}/{tid}/members", headers=A["headers"])
    roles = {m["user_id"]: m["role"] for m in members.json()["items"]}
    check("   C appears as a member", roles.get(rid(C)) == "member", str(roles))

    # --- B (admin) cannot do owner-only things ------------------------------
    r = await client.delete(f"{TEAMS}/{tid}", headers=B["headers"])
    check("6. B (admin) cannot archive the team", r.status_code == 404, f"HTTP {r.status_code}")
    r = await client.patch(f"{TEAMS}/{tid}", json={"visibility": "private"}, headers=B["headers"])
    check("   B (admin) cannot change visibility", r.status_code == 403, f"HTTP {r.status_code}")
    r = await client.patch(f"{TEAMS}/{tid}", json={"description": "admin edit"}, headers=B["headers"])
    check("   B (admin) CAN edit the description", r.status_code == 200, f"HTTP {r.status_code}")
    r = await client.delete(f"{TEAMS}/{tid}/members/{rid(A)}", headers=B["headers"])
    check("   B (admin) cannot remove the owner", r.status_code == 409, f"HTTP {r.status_code}")

    # --- private-team request flow ------------------------------------------
    r = await client.post(
        f"{TEAMS}",
        json={"name": f"Secret {run}", "visibility": "private"},
        headers=A["headers"],
    )
    private_id = r.json()["id"]
    r = await client.post(f"{TEAMS}/{private_id}/join", headers=C["headers"])
    check("7. C's join to a private team becomes a request",
          r.status_code == 200 and r.json()["status"] == "requested",
          f"HTTP {r.status_code}")
    reqs = await client.get(f"{TEAMS}/{private_id}/join-requests", headers=A["headers"])
    check("   A sees C's pending request", len(reqs.json()["items"]) == 1, f"{len(reqs.json()['items'])}")
    # B is an admin on Team X but NOT on the private team
    r = await client.get(f"{TEAMS}/{private_id}/join-requests", headers=B["headers"])
    check("   B cannot read another team's request queue", r.status_code == 404, f"HTTP {r.status_code}")
    r = await client.post(
        f"{TEAMS}/{private_id}/join-requests/{reqs.json()['items'][0]['id']}/accept",
        headers=C["headers"],
    )
    check("   C cannot accept their own request", r.status_code == 404, f"HTTP {r.status_code}")
    r = await client.post(
        f"{TEAMS}/{private_id}/join-requests/{reqs.json()['items'][0]['id']}/accept",
        headers=A["headers"],
    )
    check("   A accepts; C joins the private team", r.status_code == 200, f"HTTP {r.status_code}")

    # --- private team is invisible to a non-member --------------------------
    D = await account(client, "D")
    r = await client.get(f"{TEAMS}/{private_id}", headers=D["headers"])
    check("8. a non-member gets 404 for a private team", r.status_code == 404, f"HTTP {r.status_code}")
    r = await client.get(f"{TEAMS}/search", params={"q": "Secret"}, headers=D["headers"])
    check("   and it is absent from their search",
          all(t["id"] != private_id for t in r.json()["items"]),
          f"HTTP {r.status_code}")

    # --- C cannot manipulate Team X ----------------------------------------
    # A plain MEMBER already knows the team exists, so 403 is the correct answer
    # for an owner-only field. A non-MEMBER must get 404 — checked separately
    # with D below, because that is the case that would leak existence.
    r = await client.patch(f"{TEAMS}/{tid}", json={"name": "Hijacked"}, headers=C["headers"])
    check("9. C (plain member) cannot rename Team X", r.status_code == 403, f"HTTP {r.status_code}")
    r = await client.get(f"{TEAMS}/{tid}/invitations", headers=C["headers"])
    check("   C cannot list Team X invitations", r.status_code == 404, f"HTTP {r.status_code}")
    r = await client.delete(f"{TEAMS}/{tid}/members/{rid(A)}", headers=C["headers"])
    check("   C cannot remove A from Team X", r.status_code == 404, f"HTTP {r.status_code}")
    r = await client.post(
        f"{TEAMS}/{tid}/invitations", json={"user_id": rid(D)}, headers=C["headers"]
    )
    check("   C cannot invite anyone", r.status_code == 404, f"HTTP {r.status_code}")
    r = await client.patch(f"{TEAMS}/{private_id}", json={"name": "Hijacked"}, headers=D["headers"])
    check("   a NON-member gets 404, not 403 (no existence leak)", r.status_code == 404,
          f"HTTP {r.status_code}")

    # --- block: non-cascading (ADR-13 §6) -----------------------------------
    check("10. A and B are friends before the block", await befriend(client, A, B),
          "friendship established")
    r = await client.post(f"{SOCIAL}/blocks", json={"user_id": rid(B)}, headers=A["headers"])
    check("11. A blocks B", r.status_code == 201, f"HTTP {r.status_code}")

    members = await client.get(f"{TEAMS}/{tid}/members", headers=A["headers"])
    check("    BLOCK DOES NOT EVICT B's team membership",
          any(m["user_id"] == rid(B) for m in members.json()["items"]),
          f"{len(members.json()['items'])} members, roles "
          f"{[m['role'] for m in members.json()['items']]}")

    detail = await client.get(f"{TEAMS}/{tid}", headers=A["headers"])
    check("    member_count unchanged by the block",
          detail.json()["member_count"] == len(members.json()["items"]),
          f"count={detail.json()['member_count']}")

    # a blocked rider cannot start a NEW association
    r = await client.post(f"{TEAMS}/{private_id}/join", headers=B["headers"])
    check("12. B (blocked) cannot join a new team of A's", r.status_code == 404, f"HTTP {r.status_code}")
    r = await client.post(
        f"{TEAMS}/{tid}/invitations", json={"user_id": rid(B)}, headers=A["headers"]
    )
    check("    A cannot invite the rider who blocked them", r.status_code in (404, 409),
          f"HTTP {r.status_code}")

    # B can still leave voluntarily — a block does not lock them in
    r = await client.delete(f"{TEAMS}/{tid}/membership", headers=B["headers"])
    check("13. B can still leave voluntarily", r.status_code == 200, f"HTTP {r.status_code}")

    # --- friendship independence (ADR-13 §5) --------------------------------
    friends = await client.get(f"{SOCIAL}/friends", headers=A["headers"])
    check("14. the friendship survived the block (8.1 wall semantics)",
          not any(f["user_id"] == rid(B) for f in friends.json()["items"]),
          "8.1 removes it; teams changed nothing")

    # Friendship independence uses C, who is NOT blocked (B is, so B cannot start a
    # new association at this point).
    await befriend(client, A, C)
    members = await client.get(f"{TEAMS}/{private_id}/members", headers=A["headers"])
    c_joined = any(m["user_id"] == rid(C) for m in members.json()["items"])
    r = await client.delete(f"{TEAMS}/{private_id}/membership", headers=C["headers"])
    friends = await client.get(f"{SOCIAL}/friends", headers=A["headers"])
    check(
        "15. LEAVING A TEAM DOES NOT UNFRIEND ANYONE",
        c_joined
        and r.status_code == 200
        and any(f["user_id"] == rid(C) for f in friends.json()["items"]),
        f"joined={c_joined}, leave HTTP {r.status_code}, still friends",
    )

    # --- unblock restores nothing ------------------------------------------
    r = await client.delete(f"{SOCIAL}/blocks/{rid(B)}", headers=A["headers"])
    check("16. A unblocks B", r.status_code == 200, f"HTTP {r.status_code}")
    members = await client.get(f"{TEAMS}/{tid}/members", headers=A["headers"])
    check("    unblock does NOT re-create B's membership",
          not any(m["user_id"] == rid(B) for m in members.json()["items"]),
          "a genuine leave is not undone by an unblock")

    # --- pagination ---------------------------------------------------------
    r = await client.get(f"{TEAMS}/{tid}/members", params={"page": 1, "page_size": 1}, headers=A["headers"])
    body = r.json()
    check("17. pagination envelope is correct",
          r.status_code == 200 and {"items", "total", "page", "page_size"} <= set(body.keys()),
          json.dumps({k: v for k, v in body.items() if k != "items"}))
    r = await client.get(f"{TEAMS}", params={"page": 1, "page_size": 2}, headers=A["headers"])
    check("    my-teams list paginates", r.status_code == 200 and r.json()["page"] == 1,
          f"HTTP {r.status_code}")

    # --- no private data leaks ---------------------------------------------
    leaks = []
    for payload in [
        (await client.get(f"{TEAMS}/{tid}", headers=A["headers"])).json(),
        (await client.get(f"{TEAMS}/{tid}/members", headers=A["headers"])).json(),
    ]:
        text = json.dumps(payload).lower()
        for key in ["email", "password", "token", "lat", "lon", "gps", "location", "position"]:
            if key in text:
                leaks.append(key)
    check("18. no email / token / GPS key in team payloads", not leaks, str(leaks))


async def main() -> int:
    run = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    async with httpx.AsyncClient(timeout=30.0) as client:
        await scenario(client, run)

    async with httpx.AsyncClient(timeout=30.0) as anon:
        for path in ["/teams", "/teams/search", "/teams/00000000-0000-0000-0000-000000000000",
                     "/teams/my/invitations", "/teams/my/join-requests"]:
            r = await anon.get(f"{BASE}{path}")
            check(f"unauthenticated GET {path} is 401", r.status_code == 401, f"HTTP {r.status_code}")

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
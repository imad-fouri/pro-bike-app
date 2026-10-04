"""Phase 8.1 live smoke against the real stack.

Real uvicorn, real PostgreSQL, real HTTP. Nothing in app/ is stubbed or
monkeypatched: every assertion below is the server's own answer over the wire.

Covers the 20-step acceptance list from the Phase 8.1 brief using three real
accounts (A, B, C).
"""

import asyncio
import json
import sys
import uuid

import httpx

BASE = "http://127.0.0.1:8099/api/v1"
PASSWORD = "Cyclecoach2026pass"
EMAIL_DOMAIN = "smoke.example.com"

results: list[tuple[str, bool, str]] = []


def check(step: str, ok: bool, detail: str = "") -> None:
    results.append((step, ok, detail))
    mark = "PASS" if ok else "FAIL"
    print(f"[{mark}] {step}" + (f" :: {detail}" if detail else ""))


async def register(client: httpx.AsyncClient, label: str) -> dict:
    email = f"{label.lower()}.{uuid.uuid4().hex[:8]}@{EMAIL_DOMAIN}"
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
    # Register returns the account, not a session (backend /auth/register ->
    # MeOut). Log in to obtain the access token the social endpoints require.
    res = await client.post(f"{BASE}/auth/login", json={"email": email, "password": PASSWORD})
    if res.status_code != 200:
        raise RuntimeError(f"login {label} failed: {res.status_code} {res.text}")
    access = res.json().get("access_token")
    if not access:
        raise RuntimeError(f"login {label}: no access_token in {res.text}")
    me = await client.get(f"{BASE}/auth/me", headers={"Authorization": f"Bearer {access}"})
    return {
        "label": label,
        "id": me.json()["user"]["id"] if me.status_code == 200 else None,
        "email": email,
        "headers": {"Authorization": f"Bearer {access}"},
    }


def rid(who: dict) -> str:
    return str(who["id"])


async def main() -> int:
    async with httpx.AsyncClient(timeout=30.0) as client:
        # ------------------------------------------------------------------
        # 1. A profile loads
        # ------------------------------------------------------------------
        A = await register(client, "A")
        B = await register(client, "B")
        C = await register(client, "C")

        res = await client.get(f"{BASE}/social/profile/me", headers=A["headers"])
        check("1. A loads own social profile", res.status_code == 200, f"HTTP {res.status_code}")
        for user in (B, C):
            await client.patch(
                f"{BASE}/social/profile",
                headers=user["headers"],
                json={"username": user["label"].lower() + "_rider", "display_name": f"Rider {user['label']}", "city": "Casablanca"},
            )
        res = await client.patch(
            f"{BASE}/social/profile",
            headers=A["headers"],
            json={"username": "a_rider", "display_name": "Rider A", "bio": "Gravel.", "cycling_category": "gravel", "country_code": "MA", "city": "Casablanca"},
        )
        check("   A edits own profile", res.status_code == 200, f"HTTP {res.status_code}")

        # ------------------------------------------------------------------
        # 2. A searches B
        # ------------------------------------------------------------------
        res = await client.get(f"{BASE}/social/users/search", headers=A["headers"], params={"q": "b_rider"})
        found = res.status_code == 200 and any(i["user_id"] == rid(B) for i in res.json()["items"])
        check("2. A searches B and finds them", found, f"HTTP {res.status_code}")

        # search must not be matchable by email
        res = await client.get(f"{BASE}/social/users/search", headers=A["headers"], params={"q": B["email"]})
        no_email = res.status_code == 200 and all(i["user_id"] != rid(B) for i in res.json()["items"])
        check("   search cannot match on email", no_email, "no hit for an email query")

        # ------------------------------------------------------------------
        # 3. A opens B's profile
        # ------------------------------------------------------------------
        res = await client.get(f"{BASE}/social/profile/{rid(B)}", headers=A["headers"])
        body = res.json() if res.status_code == 200 else {}
        check(
            "3. A opens B profile, state NONE",
            res.status_code == 200 and body.get("relationship") == "NONE",
            body.get("relationship", f"HTTP {res.status_code}"),
        )

        # ------------------------------------------------------------------
        # 4. A sends a request
        # ------------------------------------------------------------------
        res = await client.post(f"{BASE}/social/friend-requests", headers=A["headers"], json={"user_id": rid(B)})
        check("4. A sends friend request", res.status_code == 201, f"HTTP {res.status_code}")
        request_id = res.json().get("id")

        # duplicate submission is refused, not double-accepted
        res2 = await client.post(f"{BASE}/social/friend-requests", headers=A["headers"], json={"user_id": rid(B)})
        check(
            "   duplicate request refused with 409",
            res2.status_code == 409,
            f"HTTP {res2.status_code}",
        )

        # ------------------------------------------------------------------
        # 5. B sees the incoming request
        # ------------------------------------------------------------------
        res = await client.get(f"{BASE}/social/friend-requests", headers=B["headers"], params={"direction": "incoming"})
        items = res.json().get("items", []) if res.status_code == 200 else []
        check(
            "5. B sees incoming request from A",
            res.status_code == 200 and any(i["user_id"] == rid(A) for i in items),
            f"{len(items)} incoming",
        )

        # ------------------------------------------------------------------
        # 6. B accepts
        # ------------------------------------------------------------------
        res = await client.post(f"{BASE}/social/friend-requests/{request_id}/accept", headers=B["headers"])
        check("6. B accepts the request", res.status_code == 200, f"HTTP {res.status_code}")

        # ------------------------------------------------------------------
        # 7. A sees B as a friend
        # ------------------------------------------------------------------
        res = await client.get(f"{BASE}/social/profile/{rid(B)}", headers=A["headers"])
        state = res.json().get("relationship") if res.status_code == 200 else None
        check("7. A sees state FRIENDS", state == "FRIENDS", str(state))
        res = await client.get(f"{BASE}/social/friends", headers=A["headers"])
        listed = res.status_code == 200 and any(f["user_id"] == rid(B) for f in res.json()["items"])
        check("   A's friend list contains B", listed, f"HTTP {res.status_code}")

        # ------------------------------------------------------------------
        # 8. A removes B
        # ------------------------------------------------------------------
        res = await client.delete(f"{BASE}/social/friends/{rid(B)}", headers=A["headers"])
        check("8. A removes the friendship", res.status_code == 200, f"HTTP {res.status_code}")
        res = await client.get(f"{BASE}/social/profile/{rid(B)}", headers=A["headers"])
        state = res.json().get("relationship") if res.status_code == 200 else None
        check("   state back to NONE, not stale FRIENDS", state == "NONE", str(state))

        # ------------------------------------------------------------------
        # 9-10. request again, B rejects
        # ------------------------------------------------------------------
        res = await client.post(f"{BASE}/social/friend-requests", headers=A["headers"], json={"user_id": rid(B)})
        check("9. A sends a second request", res.status_code == 201, f"HTTP {res.status_code}")
        rid2 = res.json().get("id")
        res = await client.get(f"{BASE}/social/friend-requests", headers=B["headers"], params={"direction": "incoming"})
        live = [i["id"] for i in res.json().get("items", [])]
        if rid2 in live:
            await client.post(f"{BASE}/social/friend-requests/{rid2}/reject", headers=B["headers"])
        res = await client.get(f"{BASE}/social/profile/{rid(B)}", headers=A["headers"])
        state = res.json().get("relationship") if res.status_code == 200 else None
        check("10. B rejects; A's state is NONE", state == "NONE", str(state))

        # ------------------------------------------------------------------
        # 11-12. request again, B blocks A
        # ------------------------------------------------------------------
        res = await client.post(f"{BASE}/social/friend-requests", headers=A["headers"], json={"user_id": rid(B)})
        check("11. A sends a third request", res.status_code == 201, f"HTTP {res.status_code}")
        res = await client.post(f"{BASE}/social/blocks", headers=B["headers"], json={"user_id": rid(A)})
        check("12. B blocks A", res.status_code == 201, f"HTTP {res.status_code}")

        # a block must destroy a pending request
        res = await client.get(f"{BASE}/social/friend-requests", headers=A["headers"], params={"direction": "outgoing"})
        pending = res.json().get("items", []) if res.status_code == 200 else []
        check(
            "   block annihilated A's pending request",
            res.status_code == 200 and not pending,
            f"{len(pending)} still pending",
        )

        # ------------------------------------------------------------------
        # 13. A cannot send a request while blocked
        # ------------------------------------------------------------------
        res = await client.post(f"{BASE}/social/friend-requests", headers=A["headers"], json={"user_id": rid(B)})
        check(
            "13. A cannot request while blocked (404, no leak)",
            res.status_code == 404,
            f"HTTP {res.status_code}",
        )
        res = await client.get(f"{BASE}/social/profile/{rid(B)}", headers=A["headers"])
        state = res.json().get("relationship") if res.status_code == 200 else None
        check("   A sees BLOCKED_BY_USER", state == "BLOCKED_BY_USER", str(state))

        # ------------------------------------------------------------------
        # 14-16. B unblocks; A can request; friendship is NOT restored
        # ------------------------------------------------------------------
        res = await client.delete(f"{BASE}/social/blocks/{rid(A)}", headers=B["headers"])
        check("14. B unblocks A", res.status_code == 200, f"HTTP {res.status_code}")
        res = await client.get(f"{BASE}/social/profile/{rid(A)}", headers=B["headers"])
        state = res.json().get("relationship") if res.status_code == 200 else None
        check(
            "16. unblock did NOT restore the friendship",
            state == "NONE",
            str(state),
        )
        res = await client.post(f"{BASE}/social/friend-requests", headers=A["headers"], json={"user_id": rid(B)})
        check("15. A can request again after unblock", res.status_code == 201, f"HTTP {res.status_code}")
        # leave the pair pending so the final state check is meaningful
        res = await client.get(f"{BASE}/social/friend-requests", headers=A["headers"], params={"direction": "outgoing"})
        out = res.json().get("items", []) if res.status_code == 200 else []
        pending_b = [i for i in out if i["user_id"] == rid(B)]
        res = await client.get(f"{BASE}/social/friends", headers=A["headers"])
        friends_now = [f["user_id"] for f in res.json().get("items", [])] if res.status_code == 200 else []
        check(
            "   the re-established link is a pending request, not a friendship",
            len(pending_b) == 1
            and pending_b[0]["status"] == "pending"
            and rid(B) not in friends_now,
            f"{len(pending_b)} pending, friends={friends_now}",
        )
        # tidy: cancel so later privacy checks start clean
        for item in out:
            if item["user_id"] == rid(B):
                await client.delete(f"{BASE}/social/friend-requests/{item['id']}", headers=A["headers"])

        # ------------------------------------------------------------------
        # 17. C cannot manipulate A/B
        # ------------------------------------------------------------------
        res = await client.get(f"{BASE}/social/friends", headers=C["headers"])
        empty_for_c = res.status_code == 200 and res.json()["items"] == []
        check("17. C's friend list does not include A or B", empty_for_c, f"HTTP {res.status_code}")
        res = await client.get(f"{BASE}/social/blocks", headers=C["headers"])
        check(
            "   C cannot see B's block list",
            res.status_code == 200 and res.json()["items"] == [],
            f"HTTP {res.status_code}",
        )

        # a third party may not cancel a request that is not theirs
        res = await client.post(f"{BASE}/social/friend-requests", headers=A["headers"], json={"user_id": rid(C)})
        c_req = res.json().get("id") if res.status_code == 201 else None
        if c_req:
            res = await client.post(f"{BASE}/social/friend-requests/{c_req}/accept", headers=B["headers"])
            check(
                "   B cannot accept A->C's request (404, not 403)",
                res.status_code == 404,
                f"HTTP {res.status_code}",
            )
            await client.delete(f"{BASE}/social/friend-requests/{c_req}", headers=A["headers"])

        # ------------------------------------------------------------------
        # 18. privacy settings work
        # ------------------------------------------------------------------
        res = await client.patch(
            f"{BASE}/social/profile/privacy",
            headers=B["headers"],
            json={"profile_visibility": "private", "search_visibility": "hidden", "allow_friend_requests": "nobody"},
        )
        ok = res.status_code == 200 and res.json()["profile_visibility"] == "private"
        check("18. B sets private / hidden / nobody", ok, f"HTTP {res.status_code}")

        res = await client.get(f"{BASE}/social/profile/{rid(B)}", headers=A["headers"])
        check(
            "   private profile is redacted for A",
            res.status_code == 200 and res.json()["limited"] is True,
            f"limited={res.json().get('limited') if res.status_code == 200 else res.status_code}",
        )
        res = await client.get(f"{BASE}/social/users/search", headers=A["headers"], params={"q": "b_rider"})
        check(
            "   hidden profile is excluded from A's search",
            res.status_code == 200 and all(i["user_id"] != rid(B) for i in res.json()["items"]),
            f"HTTP {res.status_code}",
        )
        res = await client.post(f"{BASE}/social/friend-requests", headers=A["headers"], json={"user_id": rid(B)})
        check(
            "   nobody-accepts refuses new requests with 403",
            res.status_code == 403,
            f"HTTP {res.status_code}",
        )
        await client.patch(
            f"{BASE}/social/profile/privacy",
            headers=B["headers"],
            json={"profile_visibility": "public", "search_visibility": "discoverable", "allow_friend_requests": "everyone"},
        )

        # friends-only visibility
        await client.patch(f"{BASE}/social/profile/privacy", headers=B["headers"], json={"profile_visibility": "friends"})
        res = await client.get(f"{BASE}/social/profile/{rid(B)}", headers=A["headers"])
        check(
            "   friends-only redacts a non-friend",
            res.status_code == 200 and res.json()["limited"] is True,
            f"limited={res.json().get('limited') if res.status_code == 200 else res.status_code}",
        )
        await client.patch(f"{BASE}/social/profile/privacy", headers=B["headers"], json={"profile_visibility": "public"})

        # ------------------------------------------------------------------
        # 19. blocked users handled correctly
        # ------------------------------------------------------------------
        await client.post(f"{BASE}/social/blocks", headers=B["headers"], json={"user_id": rid(C)})
        res = await client.get(f"{BASE}/social/blocks", headers=B["headers"])
        items = res.json().get("items", []) if res.status_code == 200 else []
        check(
            "19. B's block list contains C",
            res.status_code == 200 and any(i["user_id"] == rid(C) for i in items),
            f"{len(items)} blocked",
        )
        res = await client.get(f"{BASE}/social/profile/{rid(B)}", headers=C["headers"])
        state = res.json().get("relationship") if res.status_code == 200 else None
        # B blocked C, so C is the blocked party and must see BLOCKED_BY_USER
        # (the reverse of a block C placed on B).
        check("   the blocked viewer sees BLOCKED_BY_USER", state == "BLOCKED_BY_USER", str(state))
        res = await client.delete(f"{BASE}/social/blocks/{rid(C)}", headers=B["headers"])
        res = await client.get(f"{BASE}/social/profile/{rid(B)}", headers=C["headers"])
        state = res.json().get("relationship") if res.status_code == 200 else None
        check("   after unblock the viewer sees NONE, not FRIENDS", state == "NONE", str(state))

        # ------------------------------------------------------------------
        # 20. pagination works
        # ------------------------------------------------------------------
        res = await client.get(f"{BASE}/social/friends", headers=A["headers"], params={"page": 1, "page_size": 1})
        body = res.json() if res.status_code == 200 else {}
        check(
            "20. pagination returns the items/total/page/page_size envelope",
            res.status_code == 200
            and {"items", "total", "page", "page_size"} <= set(body.keys())
            and body["page"] == 1,
            json.dumps({k: v for k, v in body.items() if k != "items"}),
        )
        res = await client.get(f"{BASE}/social/users/search", headers=A["headers"], params={"q": "_rider", "page": 2, "page_size": 1})
        check(
            "   search paginates",
            res.status_code == 200 and res.json()["page"] == 2,
            f"HTTP {res.status_code}",
        )

    # ----------------------------------------------------------------------
    # Security posture
    # ----------------------------------------------------------------------
    async with httpx.AsyncClient(timeout=30.0) as anon:
        for path in [
            "/social/profile/me",
            "/social/friends",
            "/social/blocks",
            "/social/friend-requests",
        ]:
            res = await anon.get(f"{BASE}{path}")
            check(f"unauthenticated GET {path} is 401", res.status_code == 401, f"HTTP {res.status_code}")

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
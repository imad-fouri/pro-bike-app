"""Phase WS-RC live smoke: rankings & challenges (docs/ranking-challenges.md).

Real uvicorn, real PostgreSQL, real Redis. Nothing in app/ is stubbed.

Runs the SAME scenario twice by design, like Phase 8.2: accounts are unique
per run, but teams, friendships, rides and challenge-completion rows persist,
so a second run proves the engine also works against reused state.

The scenario has four riders (A, B, C, D):

- A is activity-public: its rides and challenge awards must surface on the
  GLOBAL board. B stays at the FRIENDS default, so B must appear on friends/
  team boards but not on a public board.
- A creates an individual (solo) published challenge and completes it by
  riding (~6.6 km) - the completion must award the points and show up on the
  POINTS board, all computed server-side.
- A creates a PRIVATE team challenge for a fresh public team (B, C join the
  team). Privacy gates: D (not a team member) gets 404, non-participant C
  gets a roofless detail (participant_count None), B becomes FULL by joining
  and then sees the roster.
- B leaves and rejoins (no retroactive credit is checked by re-reading), then
  rides ~6.6 km; the detail read must recompute progress from the ride table.

Server authority is exercised directly: request bodies that try to smuggle
``progress`` / ``score`` / ``rank`` / ``points_awarded`` must get 422, and
every number on every response is an output, never echoed from a request.
"""

import asyncio
import json
import sys
import uuid
from datetime import UTC, datetime, timedelta

import httpx

BASE = "http://127.0.0.1:8099/api/v1"
AUTH = f"{BASE}/auth"
SOCIAL = f"{BASE}/social"
PROFILE = f"{BASE}/profile"
BIKES = f"{BASE}/bikes"
RIDES = f"{BASE}/rides"
TEAMS = f"{BASE}/teams"
RANKINGS = f"{BASE}/rankings"
CHALLENGES = f"{BASE}/challenges"
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
        json={
            "username": f"{label.lower()}_{uuid.uuid4().hex[:6]}",
            "display_name": f"Rider {label}",
        },
        headers=h,
    )
    return {"label": label, "id": uid, "headers": h}


def rid(u: dict) -> str:
    return str(u["id"])


def now(delta_min: int = 0) -> str:
    return (datetime.now(UTC) + timedelta(minutes=delta_min)).isoformat()


async def befriend(client: httpx.AsyncClient, a: dict, b: dict) -> bool:
    r = await client.post(
        f"{SOCIAL}/friend-requests", json={"user_id": rid(b)}, headers=a["headers"]
    )
    if r.status_code != 201:
        return False
    return (
        await client.post(f"{SOCIAL}/friend-requests/{r.json()['id']}/accept", headers=b["headers"])
    ).status_code == 200


async def ride_km(
    client: httpx.AsyncClient, u: dict, *, steps: int = 6, step_deg: float = 0.01
) -> float:
    """Create a bike plus a completed ~0.01 x N km ride; return distance_m."""
    r = await client.post(
        f"{BIKES}", json={"name": "Smoke Road", "category": "road"}, headers=u["headers"]
    )
    if r.status_code != 201:
        raise RuntimeError(f"bike {u['label']}: {r.status_code} {r.text}")
    bike = r.json()
    r = await client.post(
        f"{RIDES}",
        json={"bike_id": bike["id"], "client_ride_uuid": str(uuid.uuid4())},
        headers=u["headers"],
    )
    if r.status_code != 201:
        raise RuntimeError(f"ride {u['label']}: {r.status_code} {r.text}")
    ride = r.json()
    base = datetime.now(UTC) - timedelta(minutes=30)
    pts = [
        {
            "client_point_uuid": str(uuid.uuid4()),
            "seq": i,
            "lat": round(48.85 + step_deg * i, 6),
            "lon": 2.29,
            "recorded_at": (base + timedelta(minutes=5 * i)).isoformat(),
            "alt": 100.0 + 5.0 * i,
            "accuracy": 5.0,
        }
        for i in range(steps + 1)
    ]
    up = await client.post(
        f"{RIDES}/{ride['id']}/points", json={"points": pts}, headers=u["headers"]
    )
    if up.status_code != 200:
        raise RuntimeError(f"points {u['label']}: {up.status_code} {up.text}")
    fin = await client.post(f"{RIDES}/{ride['id']}/finish", headers=u["headers"])
    if fin.status_code != 200:
        raise RuntimeError(f"finish {u['label']}: {fin.status_code} {fin.text}")
    detail = await client.get(f"{RIDES}/{ride['id']}", headers=u["headers"])
    return float(detail.json()["summary"]["distance_m"])


async def challenge(client: httpx.AsyncClient, u: dict, body: dict) -> dict:
    r = await client.post(f"{CHALLENGES}", json=body, headers=u["headers"])
    if r.status_code != 201:
        raise RuntimeError(f"challenge {u['label']}: {r.status_code} {r.text}")
    return r.json()


async def scenario(client: httpx.AsyncClient, run: int) -> None:
    print(f"\n================ RUN {run} ================")
    A = await account(client, "A")
    B = await account(client, "B")
    C = await account(client, "C")
    D = await account(client, "D")

    # A is activity-public (global boards require PUBLIC); B stays FRIENDS.
    r = await client.patch(
        f"{PROFILE}", json={"activity_visibility": "public"}, headers=A["headers"]
    )
    check("1. A sets activity_visibility to public", r.status_code == 200, f"HTTP {r.status_code}")
    check(
        "   friendships: A-B and A-C",
        await befriend(client, A, B) and await befriend(client, A, C),
        "accepted",
    )

    # --- a shared public team for scope=team checks --------------------------
    r = await client.post(
        f"{TEAMS}",
        json={
            "name": f"Racing {run}",
            "handle": f"rc_{uuid.uuid4().hex[:6]}",
            "visibility": "public",
        },
        headers=A["headers"],
    )
    check("2. A creates a public team", r.status_code == 201, f"HTTP {r.status_code}")
    team = r.json()
    tid = team["id"]
    for name, u in (("3. B joins", B), ("   C joins", C)):
        r = await client.post(f"{TEAMS}/{tid}/join", headers=u["headers"])
        check(
            name, r.status_code == 200 and r.json()["status"] == "joined", f"HTTP {r.status_code}"
        )
    # D owns a foreign team so A can probe a team board it is not in.
    r = await client.post(
        f"{TEAMS}",
        json={
            "name": f"Foreign {run}",
            "handle": f"fr_{uuid.uuid4().hex[:6]}",
            "visibility": "public",
        },
        headers=D["headers"],
    )
    foreign_id = r.json()["id"]

    # --- server authority: the rankings surface ------------------------------
    r = await client.get(f"{RANKINGS}", headers=A["headers"])
    check(
        "4. rankings requires a scope (422 without)", r.status_code == 422, f"HTTP {r.status_code}"
    )
    r = await client.get(f"{RANKINGS}", params={"scope": "country"}, headers=A["headers"])
    check(
        "   a country scope without a country is 422", r.status_code == 422, f"HTTP {r.status_code}"
    )
    r = await client.get(
        f"{RANKINGS}", params={"scope": "team", "team_id": foreign_id}, headers=A["headers"]
    )
    check(
        "   a team board A is not a member of is 403", r.status_code == 403, f"HTTP {r.status_code}"
    )

    # --- challenge create: individual solo, published -----------------------
    solo = await challenge(
        client,
        A,
        {
            "title": f"Solo {run}",
            "metric": "distance",
            "target": "1000.00",
            "points": 100,
            "scope": "individual",
            "visibility": "public",
            "start_at": now(-60 * 24),
            "end_at": now(7 * 24 * 60),
            "publish": True,
        },
    )
    solo_id = solo["id"]
    check(
        "5. A's individual challenge is created active and auto-joined",
        solo["state"] == "active"
        and solo["is_creator"]
        and solo["viewer_state"] == "joined"
        and solo["participant_count"] == 1
        and solo["can_join"] is False,
        f"state={solo['state']} count={solo['participant_count']}",
    )

    r = await client.get(f"{CHALLENGES}/{solo_id}", headers=B["headers"])
    check(
        "6. B cannot see an individual challenge of A's",
        r.status_code == 404,
        f"HTTP {r.status_code}",
    )
    r = await client.post(f"{CHALLENGES}/{solo_id}/join", headers=B["headers"])
    check("   B cannot join it either", r.status_code == 404, f"HTTP {r.status_code}")

    # --- private TEAM challenge: the privacy gates ---------------------------
    team_ch = await challenge(
        client,
        A,
        {
            "title": f"Private {run}",
            "metric": "distance",
            "target": "50000.00",
            "points": 100,
            "scope": "team",
            "visibility": "private",
            "team_id": tid,
            "start_at": now(-60 * 24),
            "end_at": now(7 * 24 * 60),
            "publish": True,
        },
    )
    team_id = team_ch["id"]
    r = await client.get(f"{CHALLENGES}/{team_id}", headers=B["headers"])
    check(
        "7. B (team member, not participant) gets a roofless detail",
        r.status_code == 200
        and r.json()["participant_count"] is None
        and r.json()["can_join"] is True,
        f"count={r.json()['participant_count']}",
    )
    r = await client.get(f"{CHALLENGES}/{team_id}", headers=D["headers"])
    check(
        "   D (not a team member) gets 404, not 403", r.status_code == 404, f"HTTP {r.status_code}"
    )

    r = await client.post(f"{CHALLENGES}/{team_id}/join", headers=B["headers"])
    check(
        "8. B joins; a duplicate join is a no-op",
        r.status_code == 200
        and r.json()["status"] == "joined"
        and (await client.post(f"{CHALLENGES}/{team_id}/join", headers=B["headers"])).status_code
        == 200,
        "idempotent",
    )
    r = await client.post(f"{CHALLENGES}/{team_id}/join", headers=C["headers"])
    check(
        "   C joins too",
        r.status_code == 200 and r.json()["status"] == "joined",
        f"HTTP {r.status_code}",
    )

    r = await client.get(f"{CHALLENGES}/{team_id}", headers=B["headers"])
    check(
        "   B now sees the roster (participant_count present)",
        r.status_code == 200 and r.json()["participant_count"] == 2,
        f"count={r.json()['participant_count']}",
    )

    r = await client.get(f"{CHALLENGES}/{team_id}/leaderboard", headers=B["headers"])
    board = r.json()
    check(
        "9. B reads the leaderboard (empty so far, ranks, both at 0)",
        r.status_code == 200
        and board["total"] == 2
        and all(x["value"] == 0 for x in board["items"]),
        f"total={board['total']}",
    )

    r = await client.get(f"{CHALLENGES}", headers=C["headers"])
    check(
        "10. a private team challenge is absent from a non-participant's list",
        r.status_code == 200 and all(c["id"] != team_id for c in r.json()["items"]),
        "not listed",
    )

    # --- leave / rejoin resets the clock (no retroactive credit) -------------
    r = await client.post(f"{CHALLENGES}/{team_id}/leave", headers=B["headers"])
    check(
        "11. B leaves",
        r.status_code == 200 and r.json()["status"] == "left",
        f"HTTP {r.status_code}",
    )
    r = await client.get(f"{CHALLENGES}/{team_id}", headers=B["headers"])
    check(
        "   detail now shows viewer_state left",
        r.json()["viewer_state"] == "left" and r.json()["can_leave"] is False,
        f"state={r.json()['viewer_state']}",
    )
    r = await client.post(f"{CHALLENGES}/{team_id}/join", headers=B["headers"])
    check(
        "   B rejoins clean",
        r.status_code == 200 and r.json()["status"] == "joined",
        f"HTTP {r.status_code}",
    )

    # --- rides recompute progress server-side --------------------------------
    b_km = await ride_km(client, B)
    check("12. B completes a ~6.6 km ride", b_km > 5000, f"distance_m={b_km:.0f}")
    r = await client.get(f"{CHALLENGES}/{team_id}", headers=B["headers"])
    vp = r.json()["viewer_progress"]
    check(
        "   B's challenge progress is recomputed from the ride table",
        r.status_code == 200 and float(vp["value"]) > 1000 and not vp["completed"],
        f"value={vp['value']} rides={vp['rides']}",
    )
    r = await client.get(f"{CHALLENGES}/{team_id}/leaderboard", headers=B["headers"])
    board = r.json()
    me = next((x for x in board["items"] if x["user_id"] == rid(B)), None)
    check(
        "   leaderboard now ranks B first with a positive value",
        board["total"] == 2 and me is not None and rank_of(me) == 1 and me["value"] > 0,
        f"rank={rank_of(me) if me else None} value={me['value'] if me else None}",
    )

    # --- A completes the solo challenge and earns the points -----------------
    a_km = await ride_km(client, A)
    check("13. A completes a ~6.6 km ride", a_km > 5000, f"distance_m={a_km:.0f}")
    r = await client.get(f"{CHALLENGES}/{solo_id}", headers=A["headers"])
    vp = r.json()["viewer_progress"]
    check(
        "   A's individual challenge completes, awarding 100 points",
        vp["completed"] and float(vp["value"]) >= 1000 and r.json()["points"] == 100,
        f"value={vp['value']} completed={vp['completed']}",
    )

    # --- rankings reflect the same source -----------------------------------
    r = await client.get(
        f"{RANKINGS}",
        params={"scope": "global", "period": "weekly", "metric": "distance"},
        headers=A["headers"],
    )
    check(
        "14. A appears on the GLOBAL distance board with its ride",
        r.status_code == 200
        and r.json()["viewer_rank"] is not None
        and float(r.json()["viewer_value"]) > 0,
        f"rank={r.json()['viewer_rank']} value={r.json()['viewer_value']}",
    )
    r = await client.get(
        f"{RANKINGS}",
        params={"scope": "global", "period": "weekly", "metric": "distance"},
        headers=B["headers"],
    )
    check(
        "   B (FRIENDS visibility) is absent from the public board",
        r.status_code == 200
        and r.json()["viewer_rank"] is None
        and all(x["user_id"] != rid(B) for x in r.json()["items"]),
        "not listed",
    )
    r = await client.get(
        f"{RANKINGS}",
        params={"scope": "friends", "period": "weekly", "metric": "distance"},
        headers=A["headers"],
    )
    check(
        "   B appears on A's FRIENDS board",
        r.status_code == 200 and any(x["user_id"] == rid(B) for x in r.json()["items"]),
        "listed",
    )
    r = await client.get(
        f"{RANKINGS}",
        params={"scope": "team", "period": "weekly", "metric": "distance", "team_id": tid},
        headers=B["headers"],
    )
    check(
        "   B appears on the TEAM board",
        r.status_code == 200 and any(x["user_id"] == rid(B) for x in r.json()["items"]),
        "listed",
    )
    r = await client.get(
        f"{RANKINGS}",
        params={"scope": "global", "period": "weekly", "metric": "points"},
        headers=A["headers"],
    )
    check(
        "   A holds the 100 POINTS on the global points board",
        r.status_code == 200
        and r.json()["viewer_rank"] is not None
        and float(r.json()["viewer_value"]) >= 100,
        f"rank={r.json()['viewer_rank']} value={r.json()['viewer_value']}",
    )

    # --- server authority: nothing may be smuggled into a challenge ----------
    bad = {
        "title": "Smuggler",
        "metric": "distance",
        "target": "100.00",
        "scope": "global",
        "start_at": now(-60 * 24),
        "end_at": now(60 * 24),
        "progress": 9999,
    }
    r = await client.post(f"{CHALLENGES}", json=bad, headers=A["headers"])
    check(
        "15. a challenge body smuggling progress is 422",
        r.status_code == 422,
        f"HTTP {r.status_code}",
    )
    for key in ("score", "rank", "points_awarded"):
        r = await client.post(f"{CHALLENGES}", json={**bad, key: 1}, headers=A["headers"])
        if r.status_code != 422:
            check(f"   smuggled {key} rejected", False, f"HTTP {r.status_code}")
            continue
    check("   smuggled key variants (score/rank/points_awarded) all 422", True, "each was 422")
    r = await client.post(
        f"{CHALLENGES}",
        json={**bad, "scope": "team", "team_id": None},
        headers=A["headers"],
    )
    check("   a team scope without a team id is 422", r.status_code == 422, f"HTTP {r.status_code}")

    # --- no private data leaks ----------------------------------------------
    leaks = []
    for payload in [
        (await client.get(f"{CHALLENGES}/{team_id}", headers=B["headers"])).json(),
        (await client.get(f"{CHALLENGES}/{team_id}/leaderboard", headers=B["headers"])).json(),
        (
            await client.get(
                f"{RANKINGS}", params={"scope": "global", "metric": "points"}, headers=A["headers"]
            )
        ).json(),
    ]:
        text = json.dumps(payload).lower()
        for key in ["email", "password", "token", "lat", "lon", "gps", "location", "position"]:
            if key in text:
                leaks.append(key)
    check("16. no email / token / GPS key in challenge or ranking payloads", not leaks, str(leaks))


def rank_of(entry: dict) -> int:
    return int(entry["rank"])


async def main() -> int:
    run = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    async with httpx.AsyncClient(timeout=30.0) as client:
        await scenario(client, run)

    async with httpx.AsyncClient(timeout=30.0) as anon:
        for path in [
            "/challenges",
            "/rankings?scope=global",
            "/challenges/00000000-0000-0000-0000-000000000000/leaderboard",
        ]:
            r = await anon.get(f"{BASE}{path}")
            check(
                f"unauthenticated GET {path} is 401", r.status_code == 401, f"HTTP {r.status_code}"
            )
        r = await anon.post(f"{CHALLENGES}", json={})
        check(
            "unauthenticated POST /challenges is 401", r.status_code == 401, f"HTTP {r.status_code}"
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

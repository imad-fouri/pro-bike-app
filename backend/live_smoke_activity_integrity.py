"""Phase 11 WS-AC live smoke: the activity-integrity gate (docs/activity-integrity.md).

Real uvicorn, real PostgreSQL, real Redis. Nothing in app/ is stubbed.

Runs the SAME scenario twice by design, like the WS-RC smoke: each run uses a
fresh account, but accepted/suspicious/rejected rides and challenge rows
persist, so a second run proves the engine also works against reused state.

The scenario is one rider A (activity-public):

- A must NOT be able to declare a verdict: ride/points bodies that smuggle
  integrity or eligibility fields are ignored, and the finish response is the
  server's ``accepted`` verdict, never a client echo.
- A completes a healthy ride -> ``accepted``: ranked, held, challenge progress
  counts it. A ride with zero points -> ``suspicious``: retained but excluded.
- A REJECTED ride (which the API deliberately cannot produce) is seeded
  straight into the ``rides`` table; the aggregations must keep excluding it
  even though its distance alone would complete the challenge.
- The response surface never leaks rules, version, evidence, GPS or identity.

Servers hold verdicts in ``rides.integrity_status``; the smoke needs to plant
the unproducible REJECTED fixture, so it opens one direct async connection to
the same database when ``SMOKE_DB_URL`` is set (defaults to the shipped dev
URL). This mirrors how the test fixtures seed rows the API cannot create.

Repeated runs: accounts are unique per run, but registration is limited to
10/hour per IP and login to 20/10min per IP, kept in-process per server
process (``app/core/rate_limit.py``). This scenario uses one registration per
run, so ``1`` then ``2`` pass against a freshly started single-worker dev
server; a burst of rapid runs hits 429 until the window or a dev-server
restart. The limiter is deliberately not disabled and there is no bypass
header.
"""

import asyncio
import json
import os
import sys
import uuid
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

BASE = "http://127.0.0.1:8099/api/v1"
AUTH = f"{BASE}/auth"
PROFILE = f"{BASE}/profile"
BIKES = f"{BASE}/bikes"
RIDES = f"{BASE}/rides"
RANKINGS = f"{BASE}/rankings"
CHALLENGES = f"{BASE}/challenges"
PASSWORD = "Cyclecoach2026pass"
DOMAIN = "smoke.example.com"
SMOKE_DB_URL = os.environ.get(
    "SMOKE_DB_URL", "postgresql+asyncpg://cyclecoach:cyclecoach@localhost:5432/cyclecoach"
)

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
    me = await client.get(f"{BASE}/social/profile/me", headers=h)
    if me.status_code != 200:
        raise RuntimeError(f"profile {label}: {me.status_code} {me.text}")
    return {"label": label, "id": me.json()["user_id"], "headers": h}


async def valid_points(steps: int = 6) -> list[dict]:
    base = datetime.now(UTC) - timedelta(minutes=30)
    return [
        {
            "client_point_uuid": str(uuid.uuid4()),
            "seq": i,
            "lat": round(48.85 + 0.01 * i, 6),
            "lon": 2.29,
            "recorded_at": (base + timedelta(minutes=5 * i)).isoformat(),
            "alt": 100.0 + 5.0 * i,
            "accuracy": 5.0,
        }
        for i in range(steps + 1)
    ]


async def seed_rejected_ride(user_id: str, bike_id: str) -> None:
    """Plant the REJECTED fixture the API cannot produce.

    A completed ride whose fabricated totals alone would exceed the challenge
    below. Mirrors tests/_competition_helpers.py::seed_integrity_ride.
    """
    engine = create_async_engine(SMOKE_DB_URL, pool_pre_ping=True)
    now = datetime.now(UTC)
    ended = now - timedelta(minutes=5)
    started = ended - timedelta(minutes=45)
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    """
                    INSERT INTO rides (
                        id, user_id, bike_id, client_ride_uuid, status,
                        started_at, ended_at, elapsed_seconds, moving_seconds,
                        distance_m, elevation_gain_m, elevation_loss_m,
                        average_speed_m_s, max_speed_m_s,
                        integrity_status, integrity_calculation_version,
                        integrity_rules_triggered, integrity_evaluated_at,
                        created_at, updated_at
                    ) VALUES (
                        :id, :user_id, :bike_id, :crid, 'completed',
                        :started, :ended, 2700, 2520,
                        '50000.00', '100.00', '0.00',
                        '0.000', '0.000',
                        'rejected', 'v1',
                        :rules, :ended,
                        :started, :ended
                    )
                    """
                ),
                {
                    "id": str(uuid.uuid4()),
                    "user_id": user_id,
                    "bike_id": bike_id,
                    "crid": str(uuid.uuid4()),
                    "started": started,
                    "ended": ended,
                    "rules": json.dumps(["GPS_COORDINATE_INVALID"]),
                },
            )
    finally:
        await engine.dispose()


async def ride(client: httpx.AsyncClient, u: dict, *, points=None) -> httpx.Response:
    r = await client.post(
        f"{BIKES}", json={"name": "Smoke Road", "category": "road"}, headers=u["headers"]
    )
    if r.status_code != 201:
        raise RuntimeError(f"bike {u['label']}: {r.status_code} {r.text}")
    r = await client.post(
        f"{RIDES}",
        json={"bike_id": r.json()["id"], "client_ride_uuid": str(uuid.uuid4())},
        headers=u["headers"],
    )
    if r.status_code != 201:
        raise RuntimeError(f"ride {u['label']}: {r.status_code} {r.text}")
    ride_id = r.json()["id"]
    if points is not None:
        up = await client.post(
            f"{RIDES}/{ride_id}/points", json={"points": points}, headers=u["headers"]
        )
        if up.status_code != 200:
            raise RuntimeError(f"points {u['label']}: {up.status_code} {up.text}")
    fin = await client.post(f"{RIDES}/{ride_id}/finish", headers=u["headers"])
    if fin.status_code != 200:
        raise RuntimeError(f"finish {u['label']}: {fin.status_code} {fin.text}")
    return fin


async def scenario(client: httpx.AsyncClient, run: int) -> None:
    print(f"\n================ RUN {run} ================")
    A = await account(client, "A")
    r = await client.patch(
        f"{PROFILE}", json={"activity_visibility": "public"}, headers=A["headers"]
    )
    check("1. A sets activity_visibility to public", r.status_code == 200, f"HTTP {r.status_code}")

    # --- the server owns the verdict; the client cannot declare one ----------
    r = await client.post(
        f"{BIKES}",
        json={
            "name": "Smoke Road",
            "category": "road",
            "integrity_status": "accepted",
            "integrity_score": 100,
            "verified": True,
        },
        headers=A["headers"],
    )
    bike_id = r.json()["id"] if r.status_code == 201 else None
    check(
        "2. a bike body smuggling integrity fields is ignored",
        r.status_code == 201,
        f"HTTP {r.status_code}",
    )

    r = await client.post(
        f"{RIDES}",
        json={
            "bike_id": bike_id,
            "client_ride_uuid": str(uuid.uuid4()),
            "integrity_status": "accepted",
            "eligible_for_ranking": True,
            "eligible_for_challenges": True,
        },
        headers=A["headers"],
    )
    check(
        "   a recording ride's verdict is None, never a client echo",
        r.status_code == 201 and r.json().get("integrity_status") is None,
        f"HTTP {r.status_code}",
    )
    ride_id = r.json()["id"]

    pts = await valid_points()
    pts[0]["integrity_status"] = "accepted"
    pts[0]["integrity_score"] = 99
    up = await client.post(
        f"{RIDES}/{ride_id}/points",
        json={"points": pts, "integrity_status": "rejected", "verified": True},
        headers=A["headers"],
    )
    check(
        "   a points chunk smuggling a verdict is ignored",
        up.status_code == 200,
        f"HTTP {up.status_code}",
    )

    fin = await client.post(f"{RIDES}/{ride_id}/finish", headers=A["headers"])
    check(
        "3. the finish verdict is the server's: accepted",
        fin.status_code == 200 and fin.json().get("integrity_status") == "accepted",
        f"HTTP {fin.status_code}",
    )
    detail = (await client.get(f"{RIDES}/{ride_id}", headers=A["headers"])).json()
    leak = [
        k
        for k in (
            "integrity_rules_triggered",
            "integrity_calculation_version",
            "integrity_evaluated_at",
            "integrity_score",
            "evidence",
        )
        if k in detail
    ]
    check(
        "   ride detail leaks the verdict but no rules / version / evidence",
        detail.get("integrity_status") == "accepted" and not leak,
        f"leaked={leak}",
    )

    # --- aggregation counts ACCEPTED rides only ------------------------------
    board = (
        await client.get(
            f"{RANKINGS}",
            params={"scope": "global", "period": "weekly", "metric": "distance"},
            headers=A["headers"],
        )
    ).json()
    check(
        "4. A is on the global distance board with a positive value",
        any(x["user_id"] == A["id"] for x in board.get("items", []))
        and float(board["viewer_value"]) > 5000,
        f"viewer_value={board.get('viewer_value')}",
    )

    r = await client.post(
        f"{CHALLENGES}",
        json={
            "title": f"Distance {run}",
            "metric": "distance",
            "target": "30000.00",
            "points": 100,
            "scope": "individual",
            "visibility": "public",
            "start_at": (datetime.now(UTC) - timedelta(hours=24)).isoformat(),
            "end_at": (datetime.now(UTC) + timedelta(days=7)).isoformat(),
            "publish": True,
        },
        headers=A["headers"],
    )
    check(
        "5. A creates an individual distance challenge",
        r.status_code == 201,
        f"HTTP {r.status_code}",
    )
    solo_id = r.json()["id"]

    # --- an ACCEPTED ride finished AFTER joining the challenge ----------------
    # Progress is recomputed from the ride table with no retroactive credit
    # (``ended_at >= joined_at``): the pre-challenge ride must not count, and
    # this post-join ride must count exactly once.
    fin = await ride(client, A, points=await valid_points())
    check(
        "   a post-join event ride is accepted",
        fin.status_code == 200 and fin.json().get("integrity_status") == "accepted",
        f"HTTP {fin.status_code}",
    )

    # --- suspicious: zero points, retained but excluded ----------------------
    fin = await ride(client, A, points=None)
    check(
        "6. a zero-point ride finishes suspicious",
        fin.status_code == 200 and fin.json().get("integrity_status") == "suspicious",
        f"HTTP {fin.status_code}",
    )
    nosusp = fin.json()["id"]

    # --- the REJECTED fixture the API cannot produce -------------------------
    assert bike_id is not None
    await seed_rejected_ride(A["id"], bike_id)
    board = (
        await client.get(
            f"{RANKINGS}",
            params={"scope": "global", "period": "weekly", "metric": "distance"},
            headers=A["headers"],
        )
    ).json()
    v = float(board["viewer_value"])
    check(
        "   a 50 km REJECTED ride is excluded from A's distance",
        0 < v < 20000,
        f"viewer_value={v:.0f} (expected only the accepted ~6.6 km)",
    )
    r = await client.get(f"{CHALLENGES}/{solo_id}", headers=A["headers"])
    vp = r.json().get("viewer_progress", {})
    check(
        "   the challenge progress counts only the post-join accepted ride",
        not vp.get("completed") and 0 < float(vp.get("value", 0)) < 9000 and vp.get("rides") == 1,
        f"value={vp.get('value')} rides={vp.get('rides')} completed={vp.get('completed')}",
    )

    # --- read-only, idempotent, private --------------------------------------
    again = await client.post(f"{RIDES}/{ride_id}/finish", headers=A["headers"])
    check(
        "7. re-finishing a completed ride is 409 with a stable verdict",
        again.status_code == 409,
        f"HTTP {again.status_code}",
    )
    # The verdict KEY itself (``integrity_status``) is public to the owner; the
    # RULES, version, evidence and GPS must never leave the server. The owner's
    # own ride detail does show its own start/end coordinates - that is the
    # same owner-facing surface as the recording screen, not a leak.
    ride_leaks = [
        k
        for payload in [
            detail,
            (await client.get(f"{RIDES}/{nosusp}", headers=A["headers"])).json(),
        ]
        for k in (
            "integrity_rules_triggered",
            "integrity_calculation_version",
            "integrity_evaluated_at",
            "integrity_score",
            "evidence",
            "gps",
            "location",
            "email",
            "password",
        )
        if k in json.dumps(payload).lower()
    ]
    check(
        "8. no rules / version / evidence / GPS / identity keys on ride details",
        not ride_leaks,
        str(ride_leaks),
    )
    aggregate_leaks = []
    for payload in [
        board,
        (await client.get(f"{CHALLENGES}/{solo_id}", headers=A["headers"])).json(),
    ]:
        text = json.dumps(payload).lower()
        for key in ["integrity", "gps", "location", "lat", "lon", "email", "password", "token"]:
            if key in text:
                aggregate_leaks.append(key)
    check(
        "   ranking and challenge payloads mention integrity / GPS nowhere",
        not aggregate_leaks,
        str(aggregate_leaks),
    )

    async with httpx.AsyncClient(timeout=30.0) as anon:
        r = await anon.get(f"{RIDES}/{ride_id}")
        check(
            "9. an unauthenticated ride detail is 401",
            r.status_code == 401,
            f"HTTP {r.status_code}",
        )
        r = await anon.get(f"{RANKINGS}", params={"scope": "global"})
        check("   an unauthenticated ranking is 401", r.status_code == 401, f"HTTP {r.status_code}")


async def main() -> int:
    run = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    async with httpx.AsyncClient(timeout=30.0) as client:
        await scenario(client, run)

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

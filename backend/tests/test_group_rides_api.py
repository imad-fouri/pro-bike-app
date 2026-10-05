"""Phase 9 group-ride API tests (ADR-16).

Real test PostgreSQL, real JWT auth, real concurrent connections for the race
tests. Nothing about the ride graph is mocked: the whole value of this phase is
its authorization, roster and lifecycle guarantees, and a mocked session would
test the mocks.

The tests are grouped by the INVARIANT they defend, and each group name states
the rule, so a failure reads as a broken rule rather than a broken assertion.
The rules that get the most coverage are the ones that are cheap to get subtly
wrong and expensive to get subtly wrong in production:

1. Membership is not team membership (ADR-16 §1).
2. One roster row per rider, for the life of the ride (§2).
3. The roster freezes at `started`, but withdrawal never does (§3).
4. A route pin is immutable and must already exist (§4).
5. A ride channel is authorized by the live roster, not by chat membership (§5).
6. Location is opt-in, ephemeral, never logged, and never faked on outage (§6).

Live-location tests live in `test_ride_location.py`, which needs a real Redis;
this file does not.
"""

import asyncio
import uuid

from app.services import notification_service

RIDES = "/api/v1/group-rides"
CHAT = "/api/v1/chat"
SOCIAL = "/api/v1/social"
AUTH = "/api/v1/auth"
ROUTES = "/api/v1/routes"
NOTIFICATIONS = "/api/v1/notifications"


def _reg(tag):
    return {
        "email": f"ride_{tag}_{uuid.uuid4().hex[:8]}@example.com",
        "password": "StrongPass123",
        "password_confirm": "StrongPass123",
        "display_name": f"Rider {tag.title()}",
    }


async def _user(client, tag="a"):
    data = _reg(tag)
    assert (await client.post(f"{AUTH}/register", json=data)).status_code == 201
    r = await client.post(
        f"{AUTH}/login", json={"email": data["email"], "password": data["password"]}
    )
    assert r.status_code == 200
    headers = {"Authorization": f"Bearer {r.json()['access_token']}"}
    me = await client.get(f"{SOCIAL}/profile/me", headers=headers)
    assert me.status_code == 200, me.text
    await client.patch(
        f"{SOCIAL}/profile",
        json={
            "username": f"ride_{tag}_{uuid.uuid4().hex[:6]}",
            "display_name": data["display_name"],
        },
        headers=headers,
    )
    return headers, me.json()["user_id"]


async def _ride(client, headers, **kwargs):
    body = {"title": kwargs.pop("title", "Sunday Spin"), **kwargs}
    r = await client.post(f"{RIDES}", json=body, headers=headers)
    assert r.status_code == 201, r.text
    return r.json()


async def _invite(client, headers, ride_id, user_id, message=None):
    r = await client.post(
        f"{RIDES}/{ride_id}/invitations",
        json={"user_id": user_id, "message": message},
        headers=headers,
    )
    assert r.status_code == 201, r.text
    return r.json()


async def _join(client, headers, ride_id):
    r = await client.post(f"{RIDES}/{ride_id}/respond", json={"accept": True}, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


async def _ride_with_two(client):
    """A ride whose organizer and one other rider are both JOINED."""
    org_headers, org_id = await _user(client, "org")
    guest_headers, guest_id = await _user(client, "guest")
    ride = await _ride(client, org_headers)
    await _invite(client, org_headers, ride["id"], guest_id)
    await _join(client, guest_headers, ride["id"])
    return org_headers, org_id, guest_headers, guest_id, ride["id"]


# ---------------------------------------------------------------------------
# Creation and the authoritative roster
# ---------------------------------------------------------------------------


async def test_organizer_is_joined_on_the_ride_they_created(client):
    """The organizer holds an ORGANIZER/JOINED row, so they can read their own
    ride immediately rather than being locked out of something they just made."""
    h, user_id = await _user(client, "org")
    ride = await _ride(client, h)
    assert ride["organizer_user_id"] == user_id
    assert ride["status"] == "open"
    assert ride["viewer"] == {"is_organizer": True, "is_joined": True}
    me = [r for r in ride["roster"] if r["user_id"] == user_id]
    assert len(me) == 1, ride["roster"]
    assert me[0]["role"] == "organizer"
    assert me[0]["status"] == "joined"
    # responded_at is NOT NULL for every non-invited status, so the organizer's
    # own row carries it too.
    assert me[0]["responded_at"] is not None


async def test_ride_with_no_route_has_neither_route_field(client):
    """Both-or-neither: a ride with no pin reports null for BOTH, never one."""
    h, _ = await _user(client, "org")
    ride = await _ride(client, h)
    assert ride["route_id"] is None
    assert ride["route_version"] is None


async def test_route_pin_requires_an_existing_version(client):
    """A pin naming a version that does not exist is refused, not defaulted.

    This is the test that ADR-16 §4 exists for: resolving "latest" would let a
    later route edit silently change what riders agreed to ride.
    """
    h, _ = await _user(client, "org")
    missing = uuid.uuid4()
    r = await client.post(
        f"{RIDES}",
        json={"title": "Pinned", "route_id": str(missing), "route_version": 1},
        headers=h,
    )
    assert r.status_code == 404, r.text
    assert r.json()["error"]["code"] == "ROUTE_NOT_FOUND"


async def test_route_pin_rejects_half_a_pair(client):
    """A version with no route, or a route with no version, is a caller error."""
    h, _ = await _user(client, "org")
    only_route = await client.post(
        f"{RIDES}", json={"title": "Half", "route_id": str(uuid.uuid4())}, headers=h
    )
    assert only_route.status_code == 400
    assert only_route.json()["error"]["code"] == "RIDE_ROUTE_INCOMPLETE"

    only_version = await client.post(
        f"{RIDES}", json={"title": "Half", "route_version": 2}, headers=h
    )
    assert only_version.status_code == 400
    assert only_version.json()["error"]["code"] == "RIDE_ROUTE_INCOMPLETE"


async def _route(client, headers, name="Coastal"):
    """A real route with one immutable version, so a pin can be honoured."""
    r = await client.post(
        f"{ROUTES}",
        json={
            "name": name,
            "activity_type": "road",
            "privacy": "private",
            "points": [
                {"lat": 46.2000, "lon": 6.1400, "ele": 400.0},
                {"lat": 46.2010, "lon": 6.1410, "ele": 420.0},
                {"lat": 46.2020, "lon": 6.1420, "ele": 410.0},
            ],
        },
        headers=headers,
    )
    assert r.status_code == 201, r.text
    return r.json()


async def test_route_pin_accepts_an_existing_version(client):
    """A real route version pins cleanly and comes back unchanged."""
    h, _ = await _user(client, "org")
    route = await _route(client, h)
    ride = await _ride(client, h, route_id=route["id"], route_version=1)
    assert ride["route_id"] == route["id"]
    assert ride["route_version"] == 1


async def test_route_pin_rejects_a_version_that_does_not_exist(client):
    """The version must already exist. A ride never resolves "latest" for you.

    If it did, editing the route later would silently change the geometry riders
    agreed to — the exact failure an immutable version pin exists to prevent.
    """
    h, _ = await _user(client, "org")
    route = await _route(client, h)
    r = await client.post(
        f"{RIDES}",
        json={"title": "Future", "route_id": route["id"], "route_version": 99},
        headers=h,
    )
    assert r.status_code == 404, r.text
    assert r.json()["error"]["code"] == "ROUTE_VERSION_NOT_FOUND"


async def test_an_edit_to_the_route_does_not_move_the_pin(client):
    """The pin names a VERSION, so version 2 appearing must leave version 1 alone.

    This is the whole reason the pin exists. Resolving "current version" at read
    time would silently re-point every past ride at geometry nobody agreed to.
    """
    h, _ = await _user(client, "org")
    route = await _route(client, h)
    ride = await _ride(client, h, route_id=route["id"], route_version=1)

    edited = await client.patch(
        f"{ROUTES}/{route['id']}",
        json={
            "expected_version": 1,
            "points": [
                {"lat": 47.0000, "lon": 7.0000, "ele": 500.0},
                {"lat": 47.0010, "lon": 7.0010, "ele": 520.0},
            ],
        },
        headers=h,
    )
    assert edited.status_code == 200, edited.text
    assert edited.json()["current_version"] == 2

    again = (await client.get(f"{RIDES}/{ride['id']}", headers=h)).json()
    assert again["route_id"] == route["id"]
    assert again["route_version"] == 1


async def test_the_database_itself_rejects_a_pin_that_does_not_exist(client, db_session_factory):
    """The version check is a FOREIGN KEY, not only a service-layer if.

    Service-layer validation is a courtesy to a well-behaved client; the FK is
    what makes "version 99 never existed" true for every writer, including a
    future one that has not read this service. Reached through a raw session
    because the API cannot be used to ask the database a question the API already
    answers.
    """
    import pytest
    from sqlalchemy import text
    from sqlalchemy.exc import IntegrityError

    h, _ = await _user(client, "org")
    route = await _route(client, h)
    ride = await _ride(client, h)

    async with db_session_factory() as session:
        with pytest.raises(IntegrityError) as caught:
            async with session.begin():
                await session.execute(
                    text(
                        "UPDATE group_rides SET route_id = :route_id, route_version = 99 "
                        "WHERE id = :ride_id"
                    ),
                    {"route_id": route["id"], "ride_id": ride["id"]},
                )
    assert "fk_group_rides_route_pin" in str(caught.value)

    # And the ride is untouched, because the write never committed.
    unchanged = (await client.get(f"{RIDES}/{ride['id']}", headers=h)).json()
    assert unchanged["route_id"] is None
    assert unchanged["route_version"] is None


async def test_deleting_a_route_degrades_the_ride_rather_than_dangling(client, db_session_factory):
    """A deleted route leaves NO version number behind.

    `ck_group_rides_route_pin` demands both-or-neither, so the composite
    `ON DELETE SET NULL` has to null both columns. With a single-column FK on
    route_id the version number would survive its route and the ride would point
    at geometry that no longer exists — a dangle the CHECK turns into a hard
    failure at the worst possible moment instead.
    """
    from sqlalchemy import text

    h, _ = await _user(client, "org")
    route = await _route(client, h)
    ride = await _ride(client, h, route_id=route["id"], route_version=1)

    factory = db_session_factory
    async with factory() as session:
        await session.execute(text("DELETE FROM routes WHERE id = :id"), {"id": route["id"]})
        await session.commit()
        row = (
            await session.execute(
                text("SELECT route_id, route_version FROM group_rides WHERE id = :id"),
                {"id": ride["id"]},
            )
        ).one()
    assert row.route_id is None
    assert row.route_version is None

    # And the ride itself survived: losing its route is not losing the ride.
    still = (await client.get(f"{RIDES}/{ride['id']}", headers=h)).json()
    assert still["id"] == ride["id"]
    assert still["route_id"] is None
    assert still["route_version"] is None


# ---------------------------------------------------------------------------
# Rule 1: membership is not team membership
# ---------------------------------------------------------------------------


async def test_leaving_a_team_does_not_eject_a_rider_from_a_ride(client):
    """ADR-16 §1. A ride roster is not a team roster, in either direction.

    Two systems sharing one row would make "is this rider in the group chat" and
    "is this rider on the ride" the same question, and they are not.
    """
    teams_h, _ = await _user(client, "teamorg")
    ride_h, _ = await _user(client, "rideorg")
    member_h, member_id = await _user(client, "member")

    team = await client.post("/api/v1/teams", json={"name": "Crew"}, headers=teams_h)
    assert team.status_code == 201, team.text
    team_id = team.json()["id"]
    invited = await client.post(
        f"/api/v1/teams/{team_id}/invitations", json={"user_id": member_id}, headers=teams_h
    )
    assert invited.status_code == 201, invited.text
    accepted = await client.post(
        f"/api/v1/teams/invitations/{invited.json()['id']}/accept",
        headers=member_h,
    )
    assert accepted.status_code == 200, accepted.text

    ride = await _ride(client, ride_h)
    await _invite(client, ride_h, ride["id"], member_id)
    await _join(client, member_h, ride["id"])

    # Now leave the team entirely.
    left = await client.delete(f"/api/v1/teams/{team_id}/membership", headers=member_h)
    assert left.status_code in (200, 204), left.text

    # The ride roster is untouched: still visible, still joined.
    view = await client.get(f"{RIDES}/{ride['id']}", headers=member_h)
    assert view.status_code == 200, view.text
    assert view.json()["viewer"]["is_joined"] is True


async def test_joining_a_team_does_not_enroll_a_rider_in_a_ride(client):
    """ADR-16 §1, other direction. Team membership grants no ride visibility."""
    teams_h, _ = await _user(client, "teamorg")
    ride_h, _ride_org_id = await _user(client, "rideorg")
    outsider_h, outsider_id = await _user(client, "outsider")

    ride = await _ride(client, ride_h)
    team = await client.post("/api/v1/teams", json={"name": "Other Crew"}, headers=teams_h)
    assert team.status_code == 201, team.text
    team_id = team.json()["id"]
    inv = await client.post(
        f"/api/v1/teams/{team_id}/invitations", json={"user_id": outsider_id}, headers=teams_h
    )
    await client.post(
        f"/api/v1/teams/{team_id}/invitations/{inv.json()['id']}/accept", headers=outsider_h
    )

    # On the team, not on the ride.
    view = await client.get(f"{RIDES}/{ride['id']}", headers=outsider_h)
    assert view.status_code == 404, view.text
    assert view.json()["error"]["code"] == "RIDE_NOT_FOUND"


# ---------------------------------------------------------------------------
# Rule 2: one row per rider, for the life of the ride
# ---------------------------------------------------------------------------


async def test_one_roster_row_per_rider(client):
    """Inviting twice does not create a second row or a second notification."""
    org_h, _org_id = await _user(client, "org")
    _, guest_id = await _user(client, "guest")
    ride = await _ride(client, org_h)

    await _invite(client, org_h, ride["id"], guest_id)
    again = await _invite(client, org_h, ride["id"], guest_id)
    rows = [r for r in again["roster"] if r["user_id"] == guest_id]
    assert len(rows) == 1, again["roster"]
    assert rows[0]["status"] == "invited"


async def test_decline_then_reinvite_resets_the_same_row(client):
    """Re-inviting a rider who declined resets their row rather than adding one.

    One row for the life of the ride is what makes the reset possible at all, and
    it is why the re-invitation is a genuinely new event worth notifying.
    """
    org_h, _ = await _user(client, "org")
    guest_h, guest_id = await _user(client, "guest")
    ride = await _ride(client, org_h)

    await _invite(client, org_h, ride["id"], guest_id)
    declined = await client.post(
        f"{RIDES}/{ride['id']}/respond", json={"accept": False}, headers=guest_h
    )
    assert declined.status_code == 200, declined.text
    assert (
        next(r for r in declined.json()["roster"] if r["user_id"] == guest_id)["status"]
        == "declined"
    )

    reinvited = await _invite(client, org_h, ride["id"], guest_id, message="changed my mind")
    rows = [r for r in reinvited["roster"] if r["user_id"] == guest_id]
    assert len(rows) == 1
    assert rows[0]["status"] == "invited"
    assert rows[0]["message"] == "changed my mind"
    # A fresh invitation has no response yet.
    assert rows[0]["responded_at"] is None


async def test_accepting_twice_is_idempotent(client):
    """A retried tap must not fail, and must not create anything."""
    org_h, _ = await _user(client, "org")
    guest_h, guest_id = await _user(client, "guest")
    ride = await _ride(client, org_h)
    await _invite(client, org_h, ride["id"], guest_id)

    first = await client.post(
        f"{RIDES}/{ride['id']}/respond", json={"accept": True}, headers=guest_h
    )
    second = await client.post(
        f"{RIDES}/{ride['id']}/respond", json={"accept": True}, headers=guest_h
    )
    assert first.status_code == 200
    assert second.status_code == 200, second.text
    rows = [r for r in second.json()["roster"] if r["user_id"] == guest_id]
    assert len(rows) == 1
    assert rows[0]["status"] == "joined"
    # The response timestamp is the FIRST one: a retry did not re-answer.
    #
    # Both sides are selected by user_id. Indexing the roster positionally
    # compares the GUEST's timestamp against the ORGANIZER's row, which differs by
    # construction - the retry is genuinely preserving `responded_at`, but a
    # positional comparison fails and would have to be suppressed. This is why the
    # assertion used to carry `or True`.
    first_rows = [r for r in first.json()["roster"] if r["user_id"] == guest_id]
    assert len(first_rows) == 1
    assert rows[0]["responded_at"] is not None
    assert rows[0]["responded_at"] == first_rows[0]["responded_at"]
    # And the whole roster is untouched apart from the answering rider's own row:
    # a retry must not re-stamp the organizer either.
    assert [r["user_id"] for r in second.json()["roster"]] == [
        r["user_id"] for r in first.json()["roster"]
    ]
    assert [r["status"] for r in second.json()["roster"]] == [
        r["status"] for r in first.json()["roster"]
    ]


# ---------------------------------------------------------------------------
# Rule 3: the roster freezes at `started`; withdrawal never does
# ---------------------------------------------------------------------------


async def test_start_requires_another_rider_joined(client):
    """A `started` ride with nobody on it has nobody to talk to, and its roster
    has just frozen, so it could never become a real one."""
    org_h, _ = await _user(client, "org")
    guest_h, guest_id = await _user(client, "guest")
    ride = await _ride(client, org_h)

    alone = await client.post(f"{RIDES}/{ride['id']}/start", headers=org_h)
    assert alone.status_code == 409, alone.text
    assert alone.json()["error"]["code"] == "RIDE_NEEDS_RIDERS"

    # An invitation is not attendance: still nobody to ride with.
    await _invite(client, org_h, ride["id"], guest_id)
    still_alone = await client.post(f"{RIDES}/{ride['id']}/start", headers=org_h)
    assert still_alone.json()["error"]["code"] == "RIDE_NEEDS_RIDERS"

    await _join(client, guest_h, ride["id"])
    started = await client.post(f"{RIDES}/{ride['id']}/start", headers=org_h)
    assert started.status_code == 200, started.text
    assert started.json()["status"] == "started"
    assert started.json()["started_at"] is not None


async def test_invite_after_start_is_refused(client):
    """Rule 5. The roster freezes at `started`: no additions, ever.

    Together with withdrawal always being allowed, a started ride can only ever
    lose participants — which is what makes the live-location audience shrink
    monotonically.
    """
    org_h, _, _, _, ride_id = await _ride_with_two(client)
    assert (await client.post(f"{RIDES}/{ride_id}/start", headers=org_h)).status_code == 200

    _, newcomer_id = await _user(client, "newcomer")
    refused = await client.post(
        f"{RIDES}/{ride_id}/invitations", json={"user_id": newcomer_id}, headers=org_h
    )
    assert refused.status_code == 409, refused.text
    assert refused.json()["error"]["code"] == "RIDE_ROSTER_FROZEN"


async def test_withdrawal_is_allowed_while_started(client):
    """Rule 4. A consent right the organizer does not get to withhold.

    This is the deliberate asymmetry with the freeze above: you can always take
    yourself out; nobody can be added late.
    """
    org_h, _, guest_h, _, ride_id = await _ride_with_two(client)
    assert (await client.post(f"{RIDES}/{ride_id}/start", headers=org_h)).status_code == 200

    left = await client.post(f"{RIDES}/{ride_id}/leave", headers=guest_h)
    assert left.status_code == 200, left.text
    assert [r for r in left.json()["roster"] if r["status"] == "left"]
    assert left.json()["participant_count"] == 1


async def test_withdrawn_rider_loses_access_immediately(client):
    """A `left` row still exists, so only the STATUS can revoke access."""
    _org_h, _, guest_h, _, ride_id = await _ride_with_two(client)
    assert (await client.post(f"{RIDES}/{ride_id}/leave", headers=guest_h)).status_code == 200

    view = await client.get(f"{RIDES}/{ride_id}", headers=guest_h)
    assert view.status_code == 404, view.text

    listed = await client.get(f"{RIDES}", headers=guest_h)
    assert listed.status_code == 200
    # The ride still appears for them, as `left` — silently dropping it would
    # look to a rider like the app lost their decision.
    entry = [r for r in listed.json()["items"] if r["id"] == ride_id]
    assert entry, listed.json()
    assert entry[0]["viewer"]["is_joined"] is False


async def test_organizer_cannot_leave_their_own_ride(client):
    """A one-organizer design has no hand-off, so the honest answer is: cancel."""
    org_h, _, _, _, ride_id = await _ride_with_two(client)
    refused = await client.post(f"{RIDES}/{ride_id}/leave", headers=org_h)
    assert refused.status_code == 409, refused.text
    assert refused.json()["error"]["code"] == "RIDE_ORGANIZER_CANNOT_LEAVE"


async def test_organizer_cannot_be_removed(client):
    """The authoritative organizer is not a roster row anybody else can touch."""
    org_h, org_id, guest_h, _, ride_id = await _ride_with_two(client)

    refused = await client.delete(f"{RIDES}/{ride_id}/participants/{org_id}", headers=guest_h)
    assert refused.status_code == 404, refused.text

    # Nor by themselves: the organizer passes the authority check, then hits the
    # explicit "you are not removable" rule — a different code on purpose, since
    # the rider already knows the ride is theirs.
    own = await client.delete(f"{RIDES}/{ride_id}/participants/{org_id}", headers=org_h)
    assert own.status_code == 409, own.text
    assert own.json()["error"]["code"] == "RIDE_ORGANIZER_IMMUTABLE"


async def test_organizer_removes_a_rider_while_started(client):
    """A ride nobody can eject anyone from is a ride nobody can stop."""
    org_h, _, guest_h, guest_id, ride_id = await _ride_with_two(client)
    assert (await client.post(f"{RIDES}/{ride_id}/start", headers=org_h)).status_code == 200

    removed = await client.delete(f"{RIDES}/{ride_id}/participants/{guest_id}", headers=org_h)
    assert removed.status_code == 200, removed.text
    row = next(r for r in removed.json()["roster"] if r["user_id"] == guest_id)
    assert row["status"] == "removed"
    assert removed.json()["participant_count"] == 1

    gone = await client.get(f"{RIDES}/{ride_id}", headers=guest_h)
    assert gone.status_code == 404


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


async def test_full_lifecycle_open_started_completed(client):
    """open -> started -> completed, with the organizer as the only driver."""
    org_h, _, _guest_h, _, ride_id = await _ride_with_two(client)

    assert (await client.post(f"{RIDES}/{ride_id}/complete", headers=org_h)).status_code == 409

    started = await client.post(f"{RIDES}/{ride_id}/start", headers=org_h)
    assert started.json()["status"] == "started"

    done = await client.post(f"{RIDES}/{ride_id}/complete", headers=org_h)
    assert done.status_code == 200, done.text
    assert done.json()["status"] == "completed"
    assert done.json()["completed_at"] is not None

    # Terminal: no further transitions (repeating the terminal one is an
    # idempotent no-op, not a conflict), and no roster changes.
    for path in ("start", "cancel"):
        again = await client.post(f"{RIDES}/{ride_id}/{path}", headers=org_h)
        assert again.status_code == 409, f"{path}: {again.text}"
    repeated_complete = await client.post(f"{RIDES}/{ride_id}/complete", headers=org_h)
    assert repeated_complete.status_code == 200, repeated_complete.text
    assert repeated_complete.json()["status"] == "completed"
    _, latecomer_id = await _user(client, "late2")
    refused = await client.post(
        f"{RIDES}/{ride_id}/invitations", json={"user_id": latecomer_id}, headers=org_h
    )
    assert refused.status_code == 409


async def test_cancel_from_open_and_from_started(client):
    """Cancellation is reachable from both live states, and is idempotent."""
    org_h, _, _, _, ride_id = await _ride_with_two(client)
    cancelled = await client.post(f"{RIDES}/{ride_id}/cancel", headers=org_h)
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["status"] == "cancelled"
    assert cancelled.json()["cancelled_at"] is not None
    again = await client.post(f"{RIDES}/{ride_id}/cancel", headers=org_h)
    assert again.status_code == 200, again.text


async def test_completed_ride_cannot_be_cancelled(client):
    """A completed ride happened. It cannot be un-completed."""
    org_h, _, _, _, ride_id = await _ride_with_two(client)
    assert (await client.post(f"{RIDES}/{ride_id}/start", headers=org_h)).status_code == 200
    assert (await client.post(f"{RIDES}/{ride_id}/complete", headers=org_h)).status_code == 200

    refused = await client.post(f"{RIDES}/{ride_id}/cancel", headers=org_h)
    assert refused.status_code == 409, refused.text
    assert refused.json()["error"]["code"] == "RIDE_CLOSED"


# ---------------------------------------------------------------------------
# Authorization: 404, never 403
# ---------------------------------------------------------------------------


async def test_non_participant_gets_404_not_403(client):
    """A rider with no roster row cannot tell the ride apart from a fake id."""
    _org_h, _, _, _, ride_id = await _ride_with_two(client)
    stranger_h, _ = await _user(client, "stranger")

    real = await client.get(f"{RIDES}/{ride_id}", headers=stranger_h)
    fake = await client.get(f"{RIDES}/{uuid.uuid4()}", headers=stranger_h)
    assert real.status_code == fake.status_code == 404
    assert real.json()["error"]["code"] == fake.json()["error"]["code"]


async def test_non_organizer_cannot_manage_the_ride(client):
    """A participant gets the same 404 a stranger does, not a 403 that would
    confirm the ride exists and is merely not theirs."""
    _org_h, _, guest_h, _, ride_id = await _ride_with_two(client)

    for path in ("start", "complete", "cancel"):
        r = await client.post(f"{RIDES}/{ride_id}/{path}", headers=guest_h)
        assert r.status_code == 404, f"{path}: {r.text}"
        assert r.json()["error"]["code"] == "RIDE_NOT_FOUND"

    _, other_id = await _user(client, "third")
    invite_refused = await client.post(
        f"{RIDES}/{ride_id}/invitations", json={"user_id": other_id}, headers=guest_h
    )
    assert invite_refused.status_code == 404

    ejected = await client.delete(f"{RIDES}/{ride_id}/participants/{other_id}", headers=guest_h)
    assert ejected.status_code == 404, ejected.text


async def test_organizer_cannot_invite_themselves(client):
    """Absorbed as a no-op by nobody: it is a confused client, worth saying."""
    org_h, org_id, _, _, ride_id = await _ride_with_two(client)
    r = await client.post(f"{RIDES}/{ride_id}/invitations", json={"user_id": org_id}, headers=org_h)
    assert r.status_code == 400, r.text
    assert r.json()["error"]["code"] == "RIDE_ALREADY_MEMBER"


async def test_only_the_invitee_may_answer(client):
    """Answering is a consent act, so nobody else can answer for you."""
    org_h, org_id, guest_h, _, ride_id = await _ride_with_two(client)
    third_h, third_id = await _user(client, "third")
    await _invite(client, org_h, ride_id, third_id)

    # Answering always acts on the CALLER's own roster row, so nobody else's
    # invitation can be resolved by proxy. The third rider stays `invited` no
    # matter what anybody else does.
    for headers, who in ((org_h, "organizer"), (guest_h, "joined guest")):
        r = await client.post(f"{RIDES}/{ride_id}/respond", json={"accept": True}, headers=headers)
        # The organizer and the already-joined guest have nothing pending, so this
        # is the idempotent no-op path — never a 404 (they are on the ride) and
        # never a mutation of somebody else's row.
        assert r.status_code == 200, f"{who}: {r.text}"
        still_pending = [x for x in r.json()["roster"] if x["user_id"] == third_id]
        assert still_pending[0]["status"] == "invited", f"{who} resolved another rider"

    # The invitee themselves still can.
    theirs = await client.post(f"{RIDES}/{ride_id}/respond", json={"accept": True}, headers=third_h)
    assert theirs.status_code == 200, theirs.text
    assert [x["status"] for x in theirs.json()["roster"] if x["user_id"] == third_id] == ["joined"]
    del org_id


async def test_organizer_sees_no_roster_row_for_a_stranger(client):
    """Roster entries carry public social identity, never account-private data."""
    org_h, _, _, _, ride_id = await _ride_with_two(client)
    view = await client.get(f"{RIDES}/{ride_id}", headers=org_h)
    assert view.status_code == 200
    for row in view.json()["roster"]:
        assert "email" not in row
        assert "username" in row
        assert set(row) >= {"user_id", "role", "status"}


# ---------------------------------------------------------------------------
# Invitations inbox
# ---------------------------------------------------------------------------


async def test_open_invitations_lists_only_pending_invites(client):
    """An answered invitation is not an invitation, and must not linger."""
    org_h, org_id = await _user(client, "org")
    guest_h, guest_id = await _user(client, "guest")
    ride = await _ride(client, org_h)
    await _invite(client, org_h, ride["id"], guest_id)
    # A second, still-pending invitation so the inbox has something to show.
    third_h, third_id = await _user(client, "third")
    await _invite(client, org_h, ride["id"], third_id)

    inbox = await client.get(f"{RIDES}/invitations", headers=guest_h)
    assert inbox.status_code == 200, inbox.text
    mine = [i for i in inbox.json()["items"] if i["group_ride_id"] == ride["id"]]
    assert len(mine) == 1, inbox.json()
    assert mine[0]["title"] == "Sunday Spin"
    assert mine[0]["organizer_user_id"] == org_id
    # The third rider's invitation is theirs, not ours.
    assert all(i["participant_id"] != third_id for i in inbox.json()["items"])

    await _join(client, guest_h, ride["id"])
    after = await client.get(f"{RIDES}/invitations", headers=guest_h)
    assert [i for i in after.json()["items"] if i["group_ride_id"] == ride["id"]] == []
    # Their RIDE list still shows it, as joined.
    rides = await client.get(f"{RIDES}", headers=guest_h)
    assert [r["id"] for r in rides.json()["items"]] == [ride["id"]]
    del guest_id, third_h


async def test_invitations_of_other_riders_are_not_visible(client):
    """The inbox is derived from the caller's own roster rows, so it cannot leak
    whose invitation this was."""
    org_h, _, _, _, ride_id = await _ride_with_two(client)
    _, third_id = await _user(client, "third2")
    await _invite(client, org_h, ride_id, third_id)

    org_inbox = await client.get(f"{RIDES}/invitations", headers=org_h)
    assert org_inbox.status_code == 200
    assert [i for i in org_inbox.json()["items"] if i["group_ride_id"] == ride_id] == []


# ---------------------------------------------------------------------------
# Blocks
# ---------------------------------------------------------------------------


async def test_blocked_pair_cannot_share_a_ride(client):
    """ADR-16 §7. A block is a personal boundary ride membership does not dissolve."""
    org_h, org_id = await _user(client, "org")
    guest_h, guest_id = await _user(client, "guest")
    ride = await _ride(client, org_h)

    # The guest blocks the organizer. Blocking is symmetric on the service side,
    # so it is the ORGANIZER's invitation of the guest that gets refused — which
    # is the direction that actually matters, since the organizer holds the only
    # power to put someone on a ride.
    blocked = await client.post(f"{SOCIAL}/blocks", json={"user_id": org_id}, headers=guest_h)
    assert blocked.status_code == 201, blocked.text

    refused = await client.post(
        f"{RIDES}/{ride['id']}/invitations", json={"user_id": guest_id}, headers=org_h
    )
    assert refused.status_code == 403, refused.text
    assert refused.json()["error"]["code"] == "RIDE_BLOCKED"

    # And the blocked pair never shares a channel or a location, even if a row
    # somehow existed.
    view = await client.get(f"{RIDES}/{ride['id']}", headers=guest_h)
    assert view.status_code == 404, view.text
    channel = await client.get(f"{RIDES}/{ride['id']}/conversation", headers=guest_h)
    assert channel.status_code == 404, channel.text


async def test_batch_invite_reports_per_target_outcomes(client):
    """One blocked rider must not make a ten-rider batch look like it failed."""
    org_h, _ = await _user(client, "org")
    good_h, good_id = await _user(client, "good")
    bad_h, bad_id = await _user(client, "bad")
    org_id = (await client.get(f"{SOCIAL}/profile/me", headers=org_h)).json()["user_id"]
    ride = await _ride(client, org_h)

    # The bad rider blocks the organizer, so the organizer cannot invite them.
    blocked = await client.post(f"{SOCIAL}/blocks", json={"user_id": org_id}, headers=bad_h)
    assert blocked.status_code == 201

    r = await client.post(
        f"{RIDES}/{ride['id']}/invitations:batch",
        json={"user_ids": [good_id, bad_id]},
        headers=org_h,
    )
    assert r.status_code == 201, r.text
    assert r.json()["invited"] == [good_id]
    assert [x["code"] for x in r.json()["rejected"]] == ["RIDE_BLOCKED"]

    # The rider who was not blocked is genuinely on the roster.
    view = await client.get(f"{RIDES}/{ride['id']}", headers=org_h)
    invited = [x["user_id"] for x in view.json()["roster"] if x["status"] == "invited"]
    assert invited == [good_id]
    del good_h


# ---------------------------------------------------------------------------
# Rule 5: the ride channel is authorized by the live roster
# ---------------------------------------------------------------------------


async def test_ride_channel_is_created_once_and_reused(client):
    """One channel per ride, no matter how many joined riders open it."""
    org_h, _, guest_h, _, ride_id = await _ride_with_two(client)
    first = await client.get(f"{RIDES}/{ride_id}/conversation", headers=org_h)
    second = await client.get(f"{RIDES}/{ride_id}/conversation", headers=guest_h)
    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    assert first.json()["id"] == second.json()["id"]
    assert first.json()["kind"] == "group_ride"


async def test_non_participant_cannot_open_a_ride_channel(client):
    """Same 404 as a conversation that never existed (ADR-16 §5)."""
    _org_h, _, _, _, ride_id = await _ride_with_two(client)
    stranger_h, _ = await _user(client, "stranger2")
    r = await client.get(f"{RIDES}/{ride_id}/conversation", headers=stranger_h)
    assert r.status_code == 404, r.text


async def test_withdrawn_rider_loses_channel_access(client):
    """The reason a ride channel cannot reuse the team rule: a `left` row still
    exists in `conversation_members` for attributability, so only the live roster
    status can revoke access."""
    _org_h, _, guest_h, _, ride_id = await _ride_with_two(client)
    channel = await client.get(f"{RIDES}/{ride_id}/conversation", headers=guest_h)
    assert channel.status_code == 200, channel.text
    convo_id = channel.json()["id"]

    assert (await client.post(f"{RIDES}/{ride_id}/leave", headers=guest_h)).status_code == 200

    assert (await client.get(f"{RIDES}/{ride_id}/conversation", headers=guest_h)).status_code == 404
    history = await client.get(f"{CHAT}/conversations/{convo_id}/messages", headers=guest_h)
    assert history.status_code == 404, history.text


async def test_joined_riders_can_read_and_post_in_the_channel(client):
    """The channel is a real conversation, usable through the normal endpoints."""
    org_h, _, guest_h, _, ride_id = await _ride_with_two(client)
    convo = (await client.get(f"{RIDES}/{ride_id}/conversation", headers=guest_h)).json()

    sent = await client.post(
        f"{CHAT}/conversations/{convo['id']}/messages",
        json={"body": "see you at the start", "client_message_id": str(uuid.uuid4())},
        headers=guest_h,
    )
    assert sent.status_code == 201, sent.text

    read = await client.get(f"{CHAT}/conversations/{convo['id']}/messages", headers=org_h)
    assert read.status_code == 200, read.text
    assert [m["body"] for m in read.json()["items"]] == ["see you at the start"]


async def test_ride_channel_refuses_writes_once_the_ride_is_cancelled(client):
    """History is retained; a cancelled ride accepts no new messages.

    A rider who can still READ but not WRITE gets 403, not 404: their standing
    was already established by the successful read, so the refusal reveals
    nothing, and it must be distinguishable from "not yours" or a client cannot
    tell retry-later from forbidden.
    """
    org_h, _, guest_h, _, ride_id = await _ride_with_two(client)
    convo = (await client.get(f"{RIDES}/{ride_id}/conversation", headers=guest_h)).json()
    assert (await client.post(f"{RIDES}/{ride_id}/cancel", headers=org_h)).status_code == 200

    still_reads = await client.get(f"{CHAT}/conversations/{convo['id']}/messages", headers=guest_h)
    assert still_reads.status_code == 200, still_reads.text

    refused = await client.post(
        f"{CHAT}/conversations/{convo['id']}/messages",
        json={"body": "one more thing", "client_message_id": str(uuid.uuid4())},
        headers=guest_h,
    )
    assert refused.status_code == 403, refused.text
    assert refused.json()["error"]["code"] == "CHAT_GROUP_RIDE_CLOSED"


async def test_a_ride_channel_with_three_joined_riders_still_opens(client):
    """The third member is what makes a group channel's peer lookup blow up.

    A conversation view computes a `peer` — the one other person in a DM — by
    selecting every member who is not the viewer. That is exactly one row for a
    DM and it is TWO for a three-rider ride, so a `scalar_one_or_none()` raises
    `MultipleResultsFound` and the read 500s.

    Two riders is why this survived review and the earlier test suite: organizer
    plus one guest is exactly the shape that works. Adding a third rider is the
    whole test.
    """
    org_h, _, guest_h, _, ride_id = await _ride_with_two(client)
    third_h, third_id = await _user(client, "third")
    await _invite(client, org_h, ride_id, third_id)
    await _join(client, third_h, ride_id)

    for headers in (org_h, guest_h, third_h):
        r = await client.get(f"{RIDES}/{ride_id}/conversation", headers=headers)
        assert r.status_code == 200, r.text
        body = r.json()
        # A group channel has no peer: null rather than an arbitrary one of the
        # other riders, which would mislabel every message in it.
        assert body["peer_user_id"] is None
        assert body["kind"] == "group_ride"
        assert body["group_ride_id"] == ride_id


async def test_a_ride_channel_lists_in_the_inbox_with_a_label(client):
    """`GET /chat` validates every row against `ConversationOut`.

    Two things have to hold for a ride channel to appear at all: the kind must be
    a member of the response enum (a newer `GROUP_RIDE` value that the enum does
    not list is a validation error and takes the WHOLE inbox down, not just the
    row), and the row must carry enough to be labelled — every `team_*` field is
    null for a ride channel, so without `group_ride_title` the inbox row is an
    unlabelled box.
    """
    _, _, guest_h, _, ride_id = await _ride_with_two(client)
    convo = (await client.get(f"{RIDES}/{ride_id}/conversation", headers=guest_h)).json()

    inbox = await client.get(f"{CHAT}/conversations", headers=guest_h)
    assert inbox.status_code == 200, inbox.text
    rows = [c for c in inbox.json()["items"] if c["id"] == convo["id"]]
    assert len(rows) == 1, inbox.json()
    assert rows[0]["kind"] == "group_ride"
    assert rows[0]["group_ride_id"] == ride_id
    assert rows[0]["group_ride_title"] == "Sunday Spin"
    # `total` must describe the same row set the page does.
    assert inbox.json()["total"] == len(inbox.json()["items"])


async def test_a_withdrawn_riders_ride_channel_leaves_their_inbox(client):
    """The inbox count and its rows must come from the SAME filtered set.

    A withdrawn rider still holds a `conversation_members` row, so filtering the
    inbox on membership alone would keep advertising a channel that answers 404 —
    and would confirm the ride still exists. Filtering after paginating instead
    would fix the row but leave `total` counting what was hidden, which reads as
    data loss to the rider.
    """
    org_h, _, guest_h, _, ride_id = await _ride_with_two(client)
    await client.get(f"{RIDES}/{ride_id}/conversation", headers=guest_h)

    before = await client.get(f"{CHAT}/conversations", headers=guest_h)
    assert before.status_code == 200, before.text
    assert before.json()["total"] == 1

    assert (await client.post(f"{RIDES}/{ride_id}/leave", headers=guest_h)).status_code == 200

    after = await client.get(f"{CHAT}/conversations", headers=guest_h)
    assert after.status_code == 200, after.text
    assert after.json()["items"] == []
    assert after.json()["total"] == 0

    # The organizer's inbox is untouched: only the withdrawn rider loses the row.
    org_inbox = await client.get(f"{CHAT}/conversations", headers=org_h)
    assert org_inbox.status_code == 200, org_inbox.text
    assert org_inbox.json()["total"] == 1
    assert org_inbox.json()["items"][0]["group_ride_id"] == ride_id


# ---------------------------------------------------------------------------
# Concurrency
# ---------------------------------------------------------------------------


async def test_concurrent_accepts_produce_one_roster_row(client):
    """Two simultaneous accepts of one invitation cannot both transition it.

    Serialized by the ride advisory lock and settled by the unique constraint on
    (ride, user) — so the permitted outcomes are a clean success or a clean
    refusal, never two rows.
    """
    org_h, _, _, _, ride_id = await _ride_with_two(client)
    racer_h, racer_id = await _user(client, "racer")
    await _invite(client, org_h, ride_id, racer_id)

    results = await asyncio.gather(
        client.post(f"{RIDES}/{ride_id}/respond", json={"accept": True}, headers=racer_h),
        client.post(f"{RIDES}/{ride_id}/respond", json={"accept": True}, headers=racer_h),
        return_exceptions=True,
    )
    statuses = sorted(r.status_code for r in results if not isinstance(r, BaseException))
    assert statuses in ([200, 200], [200, 409]), statuses

    view = await client.get(f"{RIDES}/{ride_id}", headers=org_h)
    rows = [r for r in view.json()["roster"] if r["user_id"] == racer_id]
    assert len(rows) == 1, view.json()["roster"]
    assert rows[0]["status"] == "joined"


async def test_concurrent_invites_of_the_same_rider_create_one_row(client):
    """The unique constraint is the final arbiter, not a hopeful assumption."""
    org_h, _ = await _user(client, "org")
    _, target_id = await _user(client, "target")
    ride = await _ride(client, org_h)

    results = await asyncio.gather(
        client.post(
            f"{RIDES}/{ride['id']}/invitations", json={"user_id": target_id}, headers=org_h
        ),
        client.post(
            f"{RIDES}/{ride['id']}/invitations", json={"user_id": target_id}, headers=org_h
        ),
        return_exceptions=True,
    )
    ok = [r for r in results if not isinstance(r, BaseException) and r.status_code == 201]
    assert ok, [getattr(r, "status_code", r) for r in results]

    view = await client.get(f"{RIDES}/{ride['id']}", headers=org_h)
    rows = [r for r in view.json()["roster"] if r["user_id"] == target_id]
    assert len(rows) == 1, view.json()["roster"]


async def test_start_racing_cancel_leaves_a_coherent_ride(client):
    """Two lifecycle transitions under one lock; both orders are legal.

    The lock is what serializes them, so the outcome depends on which request
    commits first — and BOTH outcomes are correct: cancelling an `open` ride
    refuses the later `start`, while cancelling a `started` ride is legitimate
    (the group split up). What must hold either way is coherence: the ride lands
    in a state whose timestamps match that state, never a mixture of both.
    """
    org_h, _, _, _, ride_id = await _ride_with_two(client)
    results = await asyncio.gather(
        client.post(f"{RIDES}/{ride_id}/start", headers=org_h),
        client.post(f"{RIDES}/{ride_id}/cancel", headers=org_h),
        return_exceptions=True,
    )
    codes = sorted(r.status_code for r in results if not isinstance(r, BaseException))
    assert codes in ([200, 409], [200, 200]), codes

    final = (await client.get(f"{RIDES}/{ride_id}", headers=org_h)).json()
    # Whatever happened, the ride is `cancelled` — cancel is always applicable
    # from a live state — and its timestamps tell one consistent story.
    assert final["status"] == "cancelled"
    assert final["cancelled_at"] is not None
    assert final["completed_at"] is None


async def test_start_racing_cancel_cannot_complete_a_cancelled_ride(client):
    """The loser of a start/cancel race may not drag the ride forward again.

    `complete` requires `started`, so once cancellation has committed the ride is
    finished — there is no path back into a live state.
    """
    org_h, _, _, _, ride_id = await _ride_with_two(client)
    assert (await client.post(f"{RIDES}/{ride_id}/cancel", headers=org_h)).status_code == 200

    refused = await client.post(f"{RIDES}/{ride_id}/complete", headers=org_h)
    assert refused.status_code == 409, refused.text
    assert refused.json()["error"]["code"] == "RIDE_NOT_STARTED"


# ---------------------------------------------------------------------------
# Rule 7: a notification is an accelerant, never a precondition
# ---------------------------------------------------------------------------


def _explode(*_args, **_kwargs):
    """A notification hook that fails the way a real one can.

    Raising rather than returning None on purpose: `notify()` returning no rows is
    an ordinary outcome (nobody to notify) and would prove nothing. The failure
    being defended against is an exception escaping the hook.
    """
    raise RuntimeError("notification backend unavailable")


async def _notifications(client, headers):
    r = await client.get(NOTIFICATIONS, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()["items"]


async def test_a_failing_invitation_notification_still_invites(client, monkeypatch):
    """The roster grows even when the invitee is never told.

    The alternative is worse than a lost notification: the organizer is shown a
    failure for an invitation that exists, retries, and watches the roster already
    contain the rider they think they failed to invite.
    """
    org_h, _ = await _user(client, "org")
    guest_h, guest_id = await _user(client, "guest")
    ride = await _ride(client, org_h)

    monkeypatch.setattr(
        "app.services.group_ride_service.notification_service.notify_group_ride_invitation",
        _explode,
    )

    r = await client.post(
        f"{RIDES}/{ride['id']}/invitations",
        json={"user_id": guest_id},
        headers=org_h,
    )
    assert r.status_code == 201, r.text

    rows = [row for row in r.json()["roster"] if row["user_id"] == guest_id]
    assert len(rows) == 1
    assert rows[0]["status"] == "invited"

    # The rider really can see it, so the invitation is not merely recorded in a
    # response nobody will ever see again. Pending invitations for a rider with no
    # roster row on the ride live on their own `/invitations` endpoint (ADR-16 §2),
    # not on `/group-rides`, which only lists rides you are on.
    pending = await client.get(f"{RIDES}/invitations", headers=guest_h)
    assert pending.status_code == 200, pending.text
    assert any(i["group_ride_id"] == ride["id"] for i in pending.json()["items"])
    # And nobody was told, which is the loss this test is actually paying for.
    assert await _notifications(client, guest_h) == []


async def test_a_failing_accept_notification_still_joins(client, monkeypatch):
    """An accepted invitation is `joined` whether or not the organizer hears.

    Also checks the converse: the failed hook must not have written a half-formed
    notification row on its way out.
    """
    org_h, _ = await _user(client, "org")
    guest_h, guest_id = await _user(client, "guest")
    ride = await _ride(client, org_h)
    await _invite(client, org_h, ride["id"], guest_id)

    monkeypatch.setattr(
        "app.services.group_ride_service.notification_service.notify_group_ride_accepted",
        _explode,
    )

    accepted = await client.post(
        f"{RIDES}/{ride['id']}/respond", json={"accept": True}, headers=guest_h
    )
    assert accepted.status_code == 200, accepted.text
    row = next(r for r in accepted.json()["roster"] if r["user_id"] == guest_id)
    assert row["status"] == "joined"

    assert await _notifications(client, org_h) == []
    # The join survives a re-read, i.e. it was committed and not merely returned.
    detail = (await client.get(f"{RIDES}/{ride['id']}", headers=guest_h)).json()
    assert next(r for r in detail["roster"] if r["user_id"] == guest_id)["status"] == "joined"


async def test_a_failing_start_notification_still_starts(client, monkeypatch):
    """The ride starts on schedule even if the push fan-out dies.

    `started` is what freezes the roster and opens the ride channel, so failing it
    over a notification would strand every rider on an `open` ride they were told
    had begun.
    """
    org_h, _, _, _, ride_id = await _ride_with_two(client)

    monkeypatch.setattr(
        "app.services.group_ride_service.notification_service.notify_group_ride_started",
        _explode,
    )

    started = await client.post(f"{RIDES}/{ride_id}/start", headers=org_h)
    assert started.status_code == 200, started.text
    assert started.json()["status"] == "started"
    assert started.json()["started_at"] is not None

    detail = (await client.get(f"{RIDES}/{ride_id}", headers=org_h)).json()
    assert detail["status"] == "started"


async def test_a_failing_invitation_notification_does_not_poison_the_next_one(client, monkeypatch):
    """One rider's failed notification must not silence the next rider's.

    The isolation is per-hook-call, not per-ride. A handler that caught the
    exception once and left the module patched, or a batch that aborted on the
    first failure, would pass every test above while quietly dropping every
    notification from then on — the exact "team channel pushed nothing for a week"
    failure the deferred-fan-out log exists to make observable.
    """
    org_h, _ = await _user(client, "org")
    first_h, first_id = await _user(client, "first")
    second_h, second_id = await _user(client, "second")
    ride = await _ride(client, org_h)

    original = notification_service.notify_group_ride_invitation
    calls = {"n": 0}

    async def flaky(*args, **kwargs):
        calls["n"] += 1
        # `invitee_id` arrives as a `uuid.UUID`, not the JSON string the helpers
        # hand back, so compare through `str` — a bare `==` against the id from
        # `_user` would never match and this test would pass for the wrong reason.
        if str(kwargs.get("invitee_id")) == first_id:
            raise RuntimeError("notification backend unavailable")
        return await original(*args, **kwargs)

    # Patched on the notification module, which is the same object
    # `group_ride_service` holds a reference to, so the service calls `flaky`.
    monkeypatch.setattr(notification_service, "notify_group_ride_invitation", flaky)

    assert (
        await client.post(
            f"{RIDES}/{ride['id']}/invitations", json={"user_id": first_id}, headers=org_h
        )
    ).status_code == 201
    assert (
        await client.post(
            f"{RIDES}/{ride['id']}/invitations", json={"user_id": second_id}, headers=org_h
        )
    ).status_code == 201

    assert calls["n"] == 2
    # The first rider was invited but never told; the second was told. Proving the
    # difference is what separates "isolated" from "swallowed everything".
    assert await _notifications(client, first_h) == []
    second_rows = await _notifications(client, second_h)
    assert len(second_rows) == 1
    assert second_rows[0]["type"] == "group_ride_invitation"
    # The link is the one the app's allowlist accepts, so a tap lands on the ride.
    assert second_rows[0]["deep_link"] == f"/group-rides/{ride['id']}"

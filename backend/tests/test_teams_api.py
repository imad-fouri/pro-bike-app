"""Phase 8.2 team API tests (ADR-13).

Real test PostgreSQL, real JWT auth, real concurrent connections for the race
tests. Nothing mocked: the team graph is storage, and storage is what is under
test.

The tests are grouped by the invariant they defend, and each group name states
the rule so a failure reads as a broken rule rather than a broken assertion.
"""

import asyncio
import json
import uuid

TEAMS = "/api/v1/teams"
SOCIAL = "/api/v1/social"
AUTH = "/api/v1/auth"


def _reg(tag):
    return {
        "email": f"team_{tag}_{uuid.uuid4().hex[:8]}@example.com",
        "password": "StrongPass123",
        "password_confirm": "StrongPass123",
        "display_name": f"Rider {tag.title()}",
    }


async def _user(client, tag="a"):
    """Register, log in, and give the rider a public social profile."""
    data = _reg(tag)
    assert (await client.post(f"{AUTH}/register", json=data)).status_code == 201
    r = await client.post(
        f"{AUTH}/login", json={"email": data["email"], "password": data["password"]}
    )
    assert r.status_code == 200
    headers = {"Authorization": f"Bearer {r.json()['access_token']}"}
    me = await client.get(f"{SOCIAL}/profile/me", headers=headers)
    assert me.status_code == 200, me.text
    # Claim a handle so member/invitation lists expose real public identity.
    await client.patch(
        f"{SOCIAL}/profile",
        json={"username": f"{tag}_rider", "display_name": f"Rider {tag.title()}"},
        headers=headers,
    )
    return headers, me.json()["user_id"]


async def _team(client, headers, name="Team X", **kwargs):
    body = {"name": name, **kwargs}
    r = await client.post(f"{TEAMS}", json=body, headers=headers)
    assert r.status_code == 201, r.text
    return r.json()


async def _add_member(client, admin_headers, team_id, user_id, role="member"):
    """Promote an existing member via the owner's role endpoint."""
    return await client.patch(
        f"{TEAMS}/{team_id}/members/{user_id}/role",
        params={"role": role},
        headers=admin_headers,
    )


async def _invite_and_accept(client, inviter_headers, team_id, invitee_headers, invitee_id):
    r = await client.post(
        f"{TEAMS}/{team_id}/invitations",
        json={"user_id": invitee_id},
        headers=inviter_headers,
    )
    assert r.status_code == 201, r.text
    inv_id = r.json()["id"]
    r2 = await client.post(f"{TEAMS}/invitations/{inv_id}/accept", headers=invitee_headers)
    assert r2.status_code == 200, r2.text
    return inv_id


# ---------------------------------------------------------------------------
# Team CRUD
# ---------------------------------------------------------------------------


async def test_create_team_makes_the_creator_owner(client):
    h, uid = await _user(client, "a")
    r = await client.post(
        f"{TEAMS}",
        json={"name": "Atlas CC", "handle": "Atlas_CC", "description": "Road crew"},
        headers=h,
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["name"] == "Atlas CC"
    # Handle canonicalized to lowercase.
    assert body["handle"] == "atlas_cc"
    assert body["owner_user_id"] == uid
    assert body["my_role"] == "owner"
    assert body["state"] == "OWNER"
    assert body["member_count"] == 1
    assert body["visibility"] == "public"
    assert "email" not in body
    assert "pending_at" not in body


async def test_create_team_rejects_blank_name(client):
    h, _ = await _user(client, "a")
    r = await client.post(f"{TEAMS}", json={"name": "   "}, headers=h)
    assert r.status_code in (422, 400)


async def test_owner_has_a_membership_row(client):
    h, uid = await _user(client, "a")
    team = await _team(client, h)
    members = await client.get(f"{TEAMS}/{team['id']}/members", headers=h)
    assert members.status_code == 200
    rows = members.json()["items"]
    assert len(rows) == 1
    assert rows[0]["user_id"] == uid
    assert rows[0]["role"] == "owner"


async def test_update_team_by_owner(client):
    h, _ = await _user(client, "a")
    team = await _team(client, h)
    r = await client.patch(
        f"{TEAMS}/{team['id']}",
        json={"description": "Now with more hills", "category": "road"},
        headers=h,
    )
    assert r.status_code == 200
    assert r.json()["description"] == "Now with more hills"
    assert r.json()["category"] == "road"


async def test_archive_team_owner_only(client):
    a, _ = await _user(client, "a")
    b, bid = await _user(client, "b")
    team = await _team(client, a)
    await _invite_and_accept(client, a, team["id"], b, bid)

    # A plain member cannot archive.
    r = await client.delete(f"{TEAMS}/{team['id']}", headers=b)
    assert r.status_code == 404

    # The owner can.
    r = await client.delete(f"{TEAMS}/{team['id']}", headers=a)
    assert r.status_code == 200
    assert r.json()["status"] == "archived"


async def test_archived_team_hidden_from_search_but_visible_to_members(client):
    a, _ = await _user(client, "a")
    c, _ = await _user(client, "c")
    team = await _team(client, a, name="Ghost Squad", handle="ghost_squad")
    await client.delete(f"{TEAMS}/{team['id']}", headers=a)

    mine = await client.get(f"{TEAMS}", headers=a)
    assert any(t["id"] == team["id"] for t in mine.json()["items"]) is False

    # A former member still resolves the team and can see it is archived.
    r = await client.get(f"{TEAMS}/{team['id']}", headers=a)
    assert r.status_code == 200
    assert r.json()["status"] == "archived"

    # A stranger gets nothing in search.
    found = await client.get(f"{TEAMS}/search", params={"q": "ghost"}, headers=c)
    assert found.status_code == 200
    assert all(t["id"] != team["id"] for t in found.json()["items"])


async def test_list_my_teams_pagination(client):
    a, _ = await _user(client, "a")
    for i in range(5):
        await _team(client, a, name=f"Squad {i}")
    r = await client.get(f"{TEAMS}", params={"page": 1, "page_size": 2}, headers=a)
    assert r.status_code == 200
    body = r.json()
    assert len(body["items"]) == 2
    assert body["total"] == 5
    assert body["page"] == 1
    assert body["page_size"] == 2


# ---------------------------------------------------------------------------
# Handles
# ---------------------------------------------------------------------------


async def test_handle_canonicalization(client):
    h, _ = await _user(client, "a")
    r = await client.post(f"{TEAMS}", json={"name": "Team", "handle": "  MiXeD_Case  "}, headers=h)
    assert r.status_code == 201
    assert r.json()["handle"] == "mixed_case"


async def test_handle_uniqueness_across_teams(client):
    a, _ = await _user(client, "a")
    b, _ = await _user(client, "b")
    await _team(client, a, name="First", handle="shared_handle")
    r = await client.post(f"{TEAMS}", json={"name": "Second", "handle": "shared_handle"}, headers=b)
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "TEAM_HANDLE_TAKEN"


async def test_handle_case_variants_collide(client):
    a, _ = await _user(client, "a")
    b, _ = await _user(client, "b")
    await _team(client, a, name="First", handle="atlas")
    r = await client.post(f"{TEAMS}", json={"name": "Second", "handle": "ATLAS"}, headers=b)
    assert r.status_code == 409


async def test_invalid_handles_rejected(client):
    h, _ = await _user(client, "a")
    for bad in ["ab", "a" * 31, "12345", "a.b.c", ".leading", "trailing_", "has space"]:
        r = await client.post(f"{TEAMS}", json={"name": "T", "handle": bad}, headers=h)
        assert r.status_code in (422, 409), f"{bad} was accepted"


async def test_handle_cannot_be_email(client):
    h, _ = await _user(client, "a")
    r = await client.post(
        f"{TEAMS}",
        json={"name": "T", "handle": "user@example.com"},
        headers=h,
    )
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# Visibility
# ---------------------------------------------------------------------------


async def test_public_team_is_searchable_and_joinable(client):
    a, _ = await _user(client, "a")
    b, bid = await _user(client, "b")
    c, _ = await _user(client, "c")
    team = await _team(client, a, name="Public Crew", handle="public_crew")

    found = await client.get(f"{TEAMS}/search", params={"q": "public"}, headers=c)
    assert any(t["id"] == team["id"] for t in found.json()["items"])

    r = await client.post(f"{TEAMS}/{team['id']}/join", headers=b)
    assert r.status_code == 200
    assert r.json()["status"] == "joined"

    members = await client.get(f"{TEAMS}/{team['id']}/members", headers=b)
    assert any(m["user_id"] == bid for m in members.json()["items"])


async def test_private_team_hidden_from_stranger_search(client):
    a, _ = await _user(client, "a")
    c, _ = await _user(client, "c")
    team = await _team(client, a, name="Secret Squad", handle="secret_squad", visibility="private")
    found = await client.get(f"{TEAMS}/search", params={"q": "secret"}, headers=c)
    assert all(t["id"] != team["id"] for t in found.json()["items"])


async def test_private_team_join_creates_request_not_membership(client):
    a, _ = await _user(client, "a")
    b, bid = await _user(client, "b")
    team = await _team(client, a, name="Secret", visibility="private")

    r = await client.post(f"{TEAMS}/{team['id']}/join", headers=b)
    assert r.status_code == 200
    assert r.json()["status"] == "requested"

    members = await client.get(f"{TEAMS}/{team['id']}/members", headers=a)
    assert all(m["user_id"] != bid for m in members.json()["items"])


async def test_private_team_is_404_for_stranger(client):
    a, _ = await _user(client, "a")
    c, _ = await _user(client, "c")
    team = await _team(client, a, name="Secret", visibility="private")
    r = await client.get(f"{TEAMS}/{team['id']}", headers=c)
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# Roles
# ---------------------------------------------------------------------------


async def test_admin_can_manage_members(client):
    a, _ = await _user(client, "a")
    b, bid = await _user(client, "b")
    c, cid = await _user(client, "c")
    team = await _team(client, a, name="Crew")
    await _invite_and_accept(client, a, team["id"], b, bid)
    r = await _add_member(client, a, team["id"], bid, "admin")
    assert r.status_code == 200
    assert r.json()["role"] == "admin"

    # Admin can invite and remove.
    r = await client.post(f"{TEAMS}/{team['id']}/invitations", json={"user_id": cid}, headers=b)
    assert r.status_code == 201
    inv = r.json()["id"]
    await client.post(f"{TEAMS}/invitations/{inv}/accept", headers=c)

    r = await client.delete(f"{TEAMS}/{team['id']}/members/{cid}", headers=b)
    assert r.status_code == 200


async def test_member_cannot_invite_or_remove(client):
    a, _ = await _user(client, "a")
    b, bid = await _user(client, "b")
    _c, cid = await _user(client, "c")
    team = await _team(client, a, name="Crew")
    await _invite_and_accept(client, a, team["id"], b, bid)

    # A plain member inviting someone → 404 (no standing to manage).
    r = await client.post(f"{TEAMS}/{team['id']}/invitations", json={"user_id": cid}, headers=b)
    assert r.status_code == 404

    # A plain member cannot remove ANY member, not even themselves. Removing
    # yourself is the "leave" endpoint, which exists precisely so the member
    # path and the manager path cannot be confused.
    r = await client.delete(f"{TEAMS}/{team['id']}/members/{bid}", headers=b)
    assert r.status_code == 404


async def test_member_cannot_remove_other_member(client):
    a, _ = await _user(client, "a")
    b, bid = await _user(client, "b")
    c, cid = await _user(client, "c")
    team = await _team(client, a, name="Crew")
    await _invite_and_accept(client, a, team["id"], b, bid)
    await _invite_and_accept(client, a, team["id"], c, cid)

    r = await client.delete(f"{TEAMS}/{team['id']}/members/{cid}", headers=b)
    assert r.status_code == 404


async def test_member_cannot_change_roles(client):
    a, _ = await _user(client, "a")
    b, bid = await _user(client, "b")
    c, cid = await _user(client, "c")
    team = await _team(client, a, name="Crew")
    await _invite_and_accept(client, a, team["id"], b, bid)
    await _invite_and_accept(client, a, team["id"], c, cid)

    r = await _add_member(client, b, team["id"], cid, "admin")
    assert r.status_code == 404


async def test_admin_cannot_change_identity_or_visibility(client):
    a, _ = await _user(client, "a")
    b, bid = await _user(client, "b")
    team = await _team(client, a, name="Crew", visibility="public")
    await _invite_and_accept(client, a, team["id"], b, bid)
    await _add_member(client, a, team["id"], bid, "admin")

    # Admin may edit the description (limited settings).
    r = await client.patch(
        f"{TEAMS}/{team['id']}", json={"description": "Edited by admin"}, headers=b
    )
    assert r.status_code == 200

    # Admin may NOT change name / handle / visibility.
    for field, value in [("name", "Hijacked"), ("handle", "hijack"), ("visibility", "private")]:
        r = await client.patch(f"{TEAMS}/{team['id']}", json={field: value}, headers=b)
        assert r.status_code == 403, f"admin changed {field}"


async def test_owner_row_is_immutable(client):
    a, aid = await _user(client, "a")
    b, bid = await _user(client, "b")
    team = await _team(client, a, name="Crew")
    await _invite_and_accept(client, a, team["id"], b, bid)

    # Cannot promote a member to owner (owner transfer out of scope).
    r = await _add_member(client, a, team["id"], bid, "admin")
    assert r.status_code == 200
    r = await _add_member(client, a, team["id"], aid, "member")
    assert r.status_code in (409, 404)  # owner cannot be demoted/removed

    # Cannot remove the owner.
    r = await client.delete(f"{TEAMS}/{team['id']}/members/{aid}", headers=a)
    assert r.status_code == 409


async def test_owner_cannot_leave(client):
    a, _aid = await _user(client, "a")
    team = await _team(client, a, name="Crew")
    r = await client.delete(f"{TEAMS}/{team['id']}/membership", headers=a)
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "TEAM_OWNER_CANNOT_LEAVE"


async def test_member_can_leave(client):
    a, _ = await _user(client, "a")
    b, bid = await _user(client, "b")
    team = await _team(client, a, name="Crew")
    await _invite_and_accept(client, a, team["id"], b, bid)
    r = await client.delete(f"{TEAMS}/{team['id']}/membership", headers=b)
    assert r.status_code == 200
    members = await client.get(f"{TEAMS}/{team['id']}/members", headers=a)
    assert all(m["user_id"] != bid for m in members.json()["items"])


# ---------------------------------------------------------------------------
# IDOR matrix
# ---------------------------------------------------------------------------


async def test_idor_stranger_cannot_mutate_team(client):
    a, _ = await _user(client, "a")
    c, _ = await _user(client, "c")
    team = await _team(client, a, name="Crew")

    # C is not a member of a public team. Every mutation → 404.
    assert (
        await client.patch(f"{TEAMS}/{team['id']}", json={"name": "X"}, headers=c)
    ).status_code == 404
    assert (await client.delete(f"{TEAMS}/{team['id']}", headers=c)).status_code == 404
    assert (await client.delete(f"{TEAMS}/{team['id']}/membership", headers=c)).status_code == 404
    assert (await client.get(f"{TEAMS}/{team['id']}/join-requests", headers=c)).status_code == 404
    assert (await client.get(f"{TEAMS}/{team['id']}/invitations", headers=c)).status_code == 404


async def test_patch_on_a_private_team_is_404_not_403(client):
    """Regression: PATCH must not confirm a private team to a non-member.

    A 403 leaks existence. A non-member must get exactly the same answer for a
    private team as for one that does not exist.
    """
    a, _ = await _user(client, "a")
    c, _ = await _user(client, "c")
    private = await _team(client, a, name="Secret", visibility="private")
    public = await _team(client, a, name="Open", visibility="public")

    r_private = await client.patch(f"{TEAMS}/{private['id']}", json={"name": "Hijacked"}, headers=c)
    r_public = await client.patch(f"{TEAMS}/{public['id']}", json={"name": "Hijacked"}, headers=c)
    assert r_private.status_code == 404
    # Identical answer for a team C could legitimately have seen: the status
    # alone must not distinguish "private" from "public but not yours".
    assert r_private.status_code == r_public.status_code == 404

    # An admin DOES get 403 for an owner-only field, because they already know
    # the team exists.
    b, bid = await _user(client, "b")
    await _invite_and_accept(client, a, public["id"], b, bid)
    await _add_member(client, a, public["id"], bid, "admin")
    r = await client.patch(f"{TEAMS}/{public['id']}", json={"name": "New"}, headers=b)
    assert r.status_code == 403


async def test_idor_member_cannot_remove_owner(client):
    a, aid = await _user(client, "a")
    b, bid = await _user(client, "b")
    team = await _team(client, a, name="Crew")
    await _invite_and_accept(client, a, team["id"], b, bid)
    # Even promoted to admin, B cannot remove A.
    await _add_member(client, a, team["id"], bid, "admin")
    r = await client.delete(f"{TEAMS}/{team['id']}/members/{aid}", headers=b)
    assert r.status_code == 409


async def test_idor_cannot_accept_another_teams_request(client):
    a, _aid = await _user(client, "a")
    b, _bid = await _user(client, "b")
    c, _ = await _user(client, "c")
    team_a = await _team(client, a, name="Team A", visibility="private")
    team_c = await _team(client, c, name="Team C", visibility="private")

    await client.post(f"{TEAMS}/{team_a['id']}/join", headers=b)
    reqs = await client.get(f"{TEAMS}/{team_a['id']}/join-requests", headers=a)
    req_id = reqs.json()["items"][0]["id"]

    # C (owner of their own team, but not of team A) cannot accept B's request.
    r = await client.post(f"{TEAMS}/{team_a['id']}/join-requests/{req_id}/accept", headers=c)
    assert r.status_code == 404

    # Nor against their own team id (mismatched request).
    r = await client.post(f"{TEAMS}/{team_c['id']}/join-requests/{req_id}/accept", headers=c)
    assert r.status_code == 404


async def test_idor_cannot_accept_another_users_invitation(client):
    a, _ = await _user(client, "a")
    _b, bid = await _user(client, "b")
    c, _cid = await _user(client, "c")
    team = await _team(client, a, name="Crew")
    r = await client.post(f"{TEAMS}/{team['id']}/invitations", json={"user_id": bid}, headers=a)
    inv_id = r.json()["id"]
    # C tries to redeem B's invitation.
    r = await client.post(f"{TEAMS}/invitations/{inv_id}/accept", headers=c)
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# Membership + join + invitation lifecycle
# ---------------------------------------------------------------------------


async def test_duplicate_membership_rejected(client):
    a, _ = await _user(client, "a")
    b, bid = await _user(client, "b")
    team = await _team(client, a, name="Crew")
    await _invite_and_accept(client, a, team["id"], b, bid)
    r = await client.post(f"{TEAMS}/{team['id']}/join", headers=b)
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "TEAM_ALREADY_MEMBER"


async def test_private_team_join_request_lifecycle(client):
    a, _aid = await _user(client, "a")
    b, bid = await _user(client, "b")
    team = await _team(client, a, name="Secret", visibility="private")

    await client.post(
        f"{TEAMS}/{team['id']}/join-requests", json={"message": "let me in"}, headers=b
    )
    reqs = await client.get(f"{TEAMS}/{team['id']}/join-requests", headers=a)
    assert reqs.status_code == 200
    items = reqs.json()["items"]
    assert len(items) == 1
    assert items[0]["user_id"] == bid
    assert items[0]["message"] == "let me in"
    assert items[0]["username"]  # public identity projection

    req_id = items[0]["id"]
    r = await client.post(f"{TEAMS}/{team['id']}/join-requests/{req_id}/accept", headers=a)
    assert r.status_code == 200
    assert r.json()["user_id"] == bid

    members = await client.get(f"{TEAMS}/{team['id']}/members", headers=a)
    assert any(m["user_id"] == bid for m in members.json()["items"])
    # Request is consumed.
    reqs = await client.get(f"{TEAMS}/{team['id']}/join-requests", headers=a)
    assert reqs.json()["items"] == []


async def test_duplicate_join_request_rejected(client):
    a, _ = await _user(client, "a")
    b, _ = await _user(client, "b")
    team = await _team(client, a, name="Secret", visibility="private")
    assert (await client.post(f"{TEAMS}/{team['id']}/join", headers=b)).status_code == 200
    r = await client.post(f"{TEAMS}/{team['id']}/join", headers=b)
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "TEAM_REQUEST_PENDING"


async def test_reject_join_request_consumes_it(client):
    a, _aid = await _user(client, "a")
    b, bid = await _user(client, "b")
    team = await _team(client, a, name="Secret", visibility="private")
    await client.post(f"{TEAMS}/{team['id']}/join", headers=b)
    reqs = await client.get(f"{TEAMS}/{team['id']}/join-requests", headers=a)
    req_id = reqs.json()["items"][0]["id"]
    r = await client.post(f"{TEAMS}/{team['id']}/join-requests/{req_id}/reject", headers=a)
    assert r.status_code == 200
    members = await client.get(f"{TEAMS}/{team['id']}/members", headers=a)
    assert all(m["user_id"] != bid for m in members.json()["items"])


async def test_cannot_request_own_team(client):
    a, _aid = await _user(client, "a")
    team = await _team(client, a, name="Crew", visibility="private")
    r = await client.post(f"{TEAMS}/{team['id']}/join", headers=a)
    assert r.status_code == 409


async def test_invitation_lifecycle(client):
    a, _ = await _user(client, "a")
    b, bid = await _user(client, "b")
    team = await _team(client, a, name="Crew")
    r = await client.post(
        f"{TEAMS}/{team['id']}/invitations",
        json={"user_id": bid, "message": "join us"},
        headers=a,
    )
    assert r.status_code == 201
    inv = r.json()
    assert inv["invited_user_id"] == bid
    assert inv["status"] == "pending"

    # Recipient sees it in their inbox.
    inbox = await client.get(f"{TEAMS}/my/invitations", headers=b)
    assert any(i["id"] == inv["id"] for i in inbox.json()["items"])

    # Accept → member.
    r = await client.post(f"{TEAMS}/invitations/{inv['id']}/accept", headers=b)
    assert r.status_code == 200
    members = await client.get(f"{TEAMS}/{team['id']}/members", headers=a)
    assert any(m["user_id"] == bid for m in members.json()["items"])


async def test_invitation_decline(client):
    a, _ = await _user(client, "a")
    b, bid = await _user(client, "b")
    team = await _team(client, a, name="Crew")
    r = await client.post(f"{TEAMS}/{team['id']}/invitations", json={"user_id": bid}, headers=a)
    inv_id = r.json()["id"]
    r = await client.post(f"{TEAMS}/invitations/{inv_id}/reject", headers=b)
    assert r.status_code == 200
    members = await client.get(f"{TEAMS}/{team['id']}/members", headers=a)
    assert all(m["user_id"] != bid for m in members.json()["items"])


async def test_invitation_revoke(client):
    a, _ = await _user(client, "a")
    b, bid = await _user(client, "b")
    team = await _team(client, a, name="Crew")
    r = await client.post(f"{TEAMS}/{team['id']}/invitations", json={"user_id": bid}, headers=a)
    inv_id = r.json()["id"]
    r = await client.delete(f"{TEAMS}/{team['id']}/invitations/{inv_id}", headers=a)
    assert r.status_code == 200
    # Recipient can no longer accept a revoked invite.
    r = await client.post(f"{TEAMS}/invitations/{inv_id}/accept", headers=b)
    assert r.status_code == 404


async def test_duplicate_invitation_rejected(client):
    a, _ = await _user(client, "a")
    _b, bid = await _user(client, "b")
    team = await _team(client, a, name="Crew")
    await client.post(f"{TEAMS}/{team['id']}/invitations", json={"user_id": bid}, headers=a)
    r = await client.post(f"{TEAMS}/{team['id']}/invitations", json={"user_id": bid}, headers=a)
    assert r.status_code == 409


async def test_reinvite_after_decline_allowed(client):
    a, _ = await _user(client, "a")
    b, bid = await _user(client, "b")
    team = await _team(client, a, name="Crew")
    r = await client.post(f"{TEAMS}/{team['id']}/invitations", json={"user_id": bid}, headers=a)
    inv_id = r.json()["id"]
    await client.post(f"{TEAMS}/invitations/{inv_id}/reject", headers=b)
    # The pair is freed, so a fresh invite works.
    r = await client.post(f"{TEAMS}/{team['id']}/invitations", json={"user_id": bid}, headers=a)
    assert r.status_code == 201


async def test_invite_collapses_pending_join_request(client):
    a, _ = await _user(client, "a")
    b, bid = await _user(client, "b")
    team = await _team(client, a, name="Crew", visibility="private")
    await client.post(f"{TEAMS}/{team['id']}/join", headers=b)
    r = await client.post(f"{TEAMS}/{team['id']}/invitations", json={"user_id": bid}, headers=a)
    assert r.status_code == 201
    # The redundant ask is gone.
    reqs = await client.get(f"{TEAMS}/{team['id']}/join-requests", headers=a)
    assert reqs.json()["items"] == []


async def test_cannot_invite_self(client):
    a, aid = await _user(client, "a")
    team = await _team(client, a, name="Crew")
    r = await client.post(f"{TEAMS}/{team['id']}/invitations", json={"user_id": aid}, headers=a)
    assert r.status_code == 422


async def test_cannot_invite_existing_member(client):
    a, _ = await _user(client, "a")
    b, bid = await _user(client, "b")
    team = await _team(client, a, name="Crew")
    await _invite_and_accept(client, a, team["id"], b, bid)
    r = await client.post(f"{TEAMS}/{team['id']}/invitations", json={"user_id": bid}, headers=a)
    assert r.status_code == 409


# ---------------------------------------------------------------------------
# Blocked users (NON-CASCADING — ADR-13 §6)
# ---------------------------------------------------------------------------


async def _friend(client, a_headers, b_headers):
    """Make A and B friends through the social API."""
    me_b = await client.get(f"{SOCIAL}/profile/me", headers=b_headers)
    bid = me_b.json()["user_id"]
    r = await client.post(f"{SOCIAL}/friend-requests", json={"user_id": bid}, headers=a_headers)
    assert r.status_code == 201
    req_id = r.json()["id"]
    r = await client.post(f"{SOCIAL}/friend-requests/{req_id}/accept", headers=b_headers)
    assert r.status_code == 200
    return bid


async def test_block_prevents_new_team_association(client):
    """A blocks B. B cannot join or request to join A's team.

    The block refuses a NEW association only; nothing is deleted (see the
    sibling tests that assert existing state survives a block).
    """
    a, _aid = await _user(client, "a")
    b, bid = await _user(client, "b")
    team = await _team(client, a, name="Crew")

    r = await client.post(f"{SOCIAL}/blocks", json={"user_id": bid}, headers=a)
    assert r.status_code == 201

    # B trying to join → refused with 404, no leak about the team.
    r = await client.post(f"{TEAMS}/{team['id']}/join", headers=b)
    assert r.status_code == 404
    members = await client.get(f"{TEAMS}/{team['id']}/members", headers=a)
    assert all(m["user_id"] != bid for m in members.json()["items"])

    # A private team behaves the same way at the request boundary.
    private = await _team(client, a, name="Secret", visibility="private")
    r = await client.post(f"{TEAMS}/{private['id']}/join", headers=b)
    assert r.status_code == 404
    reqs = await client.get(f"{TEAMS}/{private['id']}/join-requests", headers=a)
    assert reqs.json()["items"] == []


async def test_block_prevents_invite(client):
    _a, aid = await _user(client, "a")
    b, _bid = await _user(client, "b")
    team = await _team(client, b, name="B Crew")
    r = await client.post(f"{SOCIAL}/blocks", json={"user_id": aid}, headers=b)
    assert r.status_code == 201
    # A cannot be invited into B's team.
    r = await client.post(f"{TEAMS}/{team['id']}/invitations", json={"user_id": aid}, headers=b)
    assert r.status_code == 404


async def test_block_does_not_evict_existing_member(client):
    """CRITICAL INVARIANT: an existing membership survives a block.

    A block refuses NEW association actions. It deletes nothing.
    """
    a, _aid = await _user(client, "a")
    b, bid = await _user(client, "b")
    team = await _team(client, a, name="Crew")
    await _invite_and_accept(client, a, team["id"], b, bid)

    # A blocks B.
    r = await client.post(f"{SOCIAL}/blocks", json={"user_id": bid}, headers=a)
    assert r.status_code == 201

    # B is STILL a member — the block did not cascade into team_memberships.
    members = await client.get(f"{TEAMS}/{team['id']}/members", headers=a)
    assert any(m["user_id"] == bid for m in members.json()["items"])

    # And B can still leave voluntarily; the block did not lock them in.
    assert (await client.delete(f"{TEAMS}/{team['id']}/membership", headers=b)).status_code == 200


async def test_block_uses_the_existing_social_wall_and_teams_do_not_interfere(client):
    """Blocks are non-cascading **for teams**.

    Phase 8.1 already defines a block as a wall that removes the friendship
    (ADR-12 §2.3) — that behaviour is unchanged here. What Phase 8.2 adds is
    that teams are untouched by it: a block must not evict an existing member,
    and unblocking must not rebuild anything. This test pins the boundary so a
    future "make blocks delete team rows too" change cannot slip in unannounced.
    """
    a, _aid = await _user(client, "a")
    b, bid = await _user(client, "b")
    await _friend(client, a, b)
    team = await _team(client, a, name="Crew")
    await _invite_and_accept(client, a, team["id"], b, bid)

    r = await client.post(f"{SOCIAL}/blocks", json={"user_id": bid}, headers=a)
    assert r.status_code == 201

    # 8.1 wall semantics: the friendship is gone (unchanged by this phase).
    friends = await client.get(f"{SOCIAL}/friends", headers=a)
    assert not any(f["user_id"] == bid for f in friends.json()["items"])

    # 8.2 invariant: the TEAM membership is untouched by the block.
    members = await client.get(f"{TEAMS}/{team['id']}/members", headers=a)
    assert any(m["user_id"] == bid for m in members.json()["items"])

    # Unblocking restores nothing on its own: no membership is fabricated and
    # no friendship is rebuilt.
    await client.delete(f"{SOCIAL}/blocks/{bid}", headers=a)
    friends = await client.get(f"{SOCIAL}/friends", headers=a)
    assert not any(f["user_id"] == bid for f in friends.json()["items"])


async def test_friendship_survives_leaving_team(client):
    """CRITICAL INVARIANT: leaving a team does not unfriend anyone."""
    a, _aid = await _user(client, "a")
    b, bid = await _user(client, "b")
    await _friend(client, a, b)
    team = await _team(client, a, name="Crew")
    await _invite_and_accept(client, a, team["id"], b, bid)

    # B leaves the team.
    r = await client.delete(f"{TEAMS}/{team['id']}/membership", headers=b)
    assert r.status_code == 200

    # A and B are STILL friends.
    friends = await client.get(f"{SOCIAL}/friends", headers=a)
    assert any(f["user_id"] == bid for f in friends.json()["items"])


async def test_unblock_does_not_recreate_membership(client):
    """Unblocking restores nothing. A rider who genuinely left must re-join."""
    a, _aid = await _user(client, "a")
    b, bid = await _user(client, "b")
    team = await _team(client, a, name="Crew")
    await _invite_and_accept(client, a, team["id"], b, bid)

    # B leaves for real.
    await client.delete(f"{TEAMS}/{team['id']}/membership", headers=b)
    members = await client.get(f"{TEAMS}/{team['id']}/members", headers=a)
    assert all(m["user_id"] != bid for m in members.json()["items"])

    # A block/unblock cycle around that leave must not resurrect anything.
    await client.post(f"{SOCIAL}/blocks", json={"user_id": bid}, headers=a)
    await client.delete(f"{SOCIAL}/blocks/{bid}", headers=a)
    members = await client.get(f"{TEAMS}/{team['id']}/members", headers=a)
    assert all(m["user_id"] != bid for m in members.json()["items"])
    detail = await client.get(f"{TEAMS}/{team['id']}", headers=a)
    assert detail.json()["member_count"] == 1


# ---------------------------------------------------------------------------
# Concurrency (real connections, real locks)
# ---------------------------------------------------------------------------


async def test_concurrent_joins_resolve_to_one_membership(client):
    a, _ = await _user(client, "a")
    b, bid = await _user(client, "b")
    team = await _team(client, a, name="Crew")
    # B races itself twice on the public join.
    results = await asyncio.gather(
        client.post(f"{TEAMS}/{team['id']}/join", headers=b),
        client.post(f"{TEAMS}/{team['id']}/join", headers=b),
    )
    codes = sorted(r.status_code for r in results)
    assert codes in ([200, 409], [200, 200])
    members = await client.get(f"{TEAMS}/{team['id']}/members", headers=a)
    assert sum(1 for m in members.json()["items"] if m["user_id"] == bid) == 1


async def test_concurrent_accepts_of_one_request_converge(client):
    a, _aid = await _user(client, "a")
    b, bid = await _user(client, "b")
    team = await _team(client, a, name="Secret", visibility="private")
    await client.post(f"{TEAMS}/{team['id']}/join", headers=b)
    reqs = await client.get(f"{TEAMS}/{team['id']}/join-requests", headers=a)
    req_id = reqs.json()["items"][0]["id"]

    # Two managers race on the SAME request. Exactly one membership results,
    # whatever the interleaving: the loser sees 404 (already consumed) or 409.
    results = await asyncio.gather(
        client.post(f"{TEAMS}/{team['id']}/join-requests/{req_id}/accept", headers=a),
        client.post(f"{TEAMS}/{team['id']}/join-requests/{req_id}/accept", headers=a),
    )
    assert all(r.status_code in (200, 404, 409) for r in results)
    members = await client.get(f"{TEAMS}/{team['id']}/members", headers=a)
    assert sum(1 for m in members.json()["items"] if m["user_id"] == bid) == 1


async def test_member_count_is_consistent_under_concurrent_removes(client):
    a, _aid = await _user(client, "a")
    b, bid = await _user(client, "b")
    c, cid = await _user(client, "c")
    team = await _team(client, a, name="Crew")
    await _invite_and_accept(client, a, team["id"], b, bid)
    await _invite_and_accept(client, a, team["id"], c, cid)
    assert (await client.get(f"{TEAMS}/{team['id']}", headers=a)).json()["member_count"] == 3

    # B and C leave concurrently.
    await asyncio.gather(
        client.delete(f"{TEAMS}/{team['id']}/membership", headers=b),
        client.delete(f"{TEAMS}/{team['id']}/membership", headers=c),
    )
    detail = await client.get(f"{TEAMS}/{team['id']}", headers=a)
    members = await client.get(f"{TEAMS}/{team['id']}/members", headers=a)
    assert detail.json()["member_count"] == 1
    assert len(members.json()["items"]) == 1


async def test_invite_vs_join_request_race(client):
    """An offer and an ask for the same rider must not both survive.

    The invitation collapses any outstanding ask, so the end state is exactly
    one of: pending invitation, or membership — never a request row AND an
    invitation row for the same pair.
    """
    a, _ = await _user(client, "a")
    b, bid = await _user(client, "b")
    team = await _team(client, a, name="Secret", visibility="private")
    await asyncio.gather(
        client.post(f"{TEAMS}/{team['id']}/invitations", json={"user_id": bid}, headers=a),
        client.post(f"{TEAMS}/{team['id']}/join", headers=b),
    )
    members = await client.get(f"{TEAMS}/{team['id']}/members", headers=a)
    invites = await client.get(f"{TEAMS}/{team['id']}/invitations", headers=a)
    requests = await client.get(f"{TEAMS}/{team['id']}/join-requests", headers=a)
    n_invites = sum(1 for i in invites.json()["items"] if i["invited_user_id"] == bid)
    n_requests = sum(1 for r in requests.json()["items"] if r["user_id"] == bid)
    # Never an invitation and a request at the same time for one rider.
    assert not (n_invites and n_requests)
    # At most one membership row, ever.
    assert sum(1 for m in members.json()["items"] if m["user_id"] == bid) <= 1


# ---------------------------------------------------------------------------
# Search, pagination, rate limits
# ---------------------------------------------------------------------------


async def test_search_matches_name_and_handle(client):
    a, _ = await _user(client, "a")
    c, _ = await _user(client, "c")
    await _team(client, a, name="Desert Foxes", handle="desert_foxes")
    by_name = await client.get(f"{TEAMS}/search", params={"q": "desert"}, headers=c)
    assert by_name.json()["total"] >= 1
    by_handle = await client.get(f"{TEAMS}/search", params={"q": "desert_foxes"}, headers=c)
    assert by_handle.json()["total"] >= 1


async def test_search_pagination(client):
    a, _ = await _user(client, "a")
    c, _ = await _user(client, "c")
    for i in range(4):
        await _team(client, a, name=f"Rider Squad {i}")
    r = await client.get(
        f"{TEAMS}/search", params={"q": "rider", "page": 1, "page_size": 2}, headers=c
    )
    body = r.json()
    assert body["page"] == 1
    assert body["page_size"] == 2
    assert len(body["items"]) <= 2


async def test_search_rate_limited(client):
    h, _ = await _user(client, "a")
    # Fire many searches quickly; at least one must be rate-limited (429).
    codes = []
    for _ in range(65):
        r = await client.get(f"{TEAMS}/search", params={"q": "squad"}, headers=h)
        codes.append(r.status_code)
        if r.status_code == 429:
            break
    assert 429 in codes


async def test_members_pagination(client):
    a, _ = await _user(client, "a")
    team = await _team(client, a, name="Crew")
    r = await client.get(
        f"{TEAMS}/{team['id']}/members", params={"page": 1, "page_size": 1}, headers=a
    )
    body = r.json()
    assert len(body["items"]) == 1
    assert body["total"] == 1


async def test_members_ordered_owner_first(client):
    a, _ = await _user(client, "a")
    b, bid = await _user(client, "b")
    c, cid = await _user(client, "c")
    team = await _team(client, a, name="Crew")
    await _invite_and_accept(client, a, team["id"], b, bid)
    await _invite_and_accept(client, a, team["id"], c, cid)
    await _add_member(client, a, team["id"], bid, "admin")
    members = await client.get(f"{TEAMS}/{team['id']}/members", headers=a)
    roles = [m["role"] for m in members.json()["items"]]
    assert roles == ["owner", "admin", "member"]


async def test_unauthenticated_team_endpoints_401(client):
    team_id = str(uuid.uuid4())
    assert (await client.get(f"{TEAMS}")).status_code == 401
    assert (await client.get(f"{TEAMS}/{team_id}")).status_code == 401
    assert (await client.post(f"{TEAMS}", json={"name": "X"})).status_code == 401


async def test_no_private_or_location_keys_in_team_payloads(client):
    a, _aid = await _user(client, "a")
    b, bid = await _user(client, "b")
    team = await _team(client, a, name="Crew")
    await _invite_and_accept(client, a, team["id"], b, bid)

    detail = (await client.get(f"{TEAMS}/{team['id']}", headers=a)).json()
    members = (await client.get(f"{TEAMS}/{team['id']}/members", headers=a)).json()
    search = (await client.get(f"{TEAMS}/search", params={"q": "crew"}, headers=a)).json()

    banned = ["email", "password", "token", "lat", "lon", "gps", "location", "position"]
    for payload in [detail, members, search]:
        text = json.dumps(payload).lower()
        for key in banned:
            assert key not in text, f"team payload leaked {key}"


# ---------------------------------------------------------------------------
# OpenAPI contract
# ---------------------------------------------------------------------------


async def test_team_routes_in_openapi(client):
    _h, _ = await _user(client, "a")
    r = await client.get("/openapi.json")
    assert r.status_code == 200
    paths = r.json()["paths"]
    for path in [
        "/api/v1/teams",
        "/api/v1/teams/search",
        "/api/v1/teams/{team_id}",
        "/api/v1/teams/{team_id}/members",
        "/api/v1/teams/{team_id}/join",
        "/api/v1/teams/{team_id}/join-requests",
        "/api/v1/teams/{team_id}/invitations",
        "/api/v1/teams/invitations/{invitation_id}/accept",
        "/api/v1/teams/{team_id}/membership",
    ]:
        assert path in paths, f"{path} missing from OpenAPI"

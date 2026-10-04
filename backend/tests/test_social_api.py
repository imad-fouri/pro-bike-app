"""Phase 8.1 social API tests (ADR-12).

Real test PostgreSQL, real JWT auth, real concurrent connections for the
race tests. Nothing mocked except nothing: the social graph is storage, and
storage is what is under test.
"""

import asyncio
import uuid

import pytest

SOCIAL = "/api/v1/social"
AUTH = "/api/v1/auth"


def _reg(tag):
    return {
        "email": f"social_{tag}_{uuid.uuid4().hex[:8]}@example.com",
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
    return headers, me.json()["user_id"]


async def _fresh_user(client, tag="z"):
    """Register + login WITHOUT touching /social/profile/me.

    The helper `_user` above always creates the social projection, which hides
    a lazy-load bug in ensure_profile(): if the row already exists, the private
    `user_profiles` row is never read. This one leaves the projection absent so
    the first read happens on the code path that must handle it.
    """
    data = _reg(tag)
    assert (await client.post(f"{AUTH}/register", json=data)).status_code == 201
    r = await client.post(
        f"{AUTH}/login", json={"email": data["email"], "password": data["password"]}
    )
    assert r.status_code == 200
    headers = {"Authorization": f"Bearer {r.json()['access_token']}"}
    me = await client.get(f"{AUTH}/me", headers=headers)
    assert me.status_code == 200, me.text
    return headers, me.json()["user"]["id"]


async def test_viewing_a_rider_who_never_edited_their_profile(client):
    """Regression: no MissingGreenlet 500 on the lazy first projection.

    ensure_profile() seeds display_name from the private user_profiles row. If
    the target User is resolved without its profile eagerly loaded, that read
    is sync IO on an async session and the endpoint 500s. B's projection does
    not exist yet here, so this is exactly the path that broke.
    """
    a_headers, _ = await _user(client, "a")
    b_headers, b_uid = await _fresh_user(client, "b")

    r = await client.get(f"{SOCIAL}/profile/{b_uid}", headers=a_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["user_id"] == b_uid
    # Seeded from the private profile, not invented.
    assert body["display_name"] == "Rider B"
    assert body["relationship"] == "NONE"

    # A friend request also resolves the target lazily; it must not 500 either.
    r = await client.post(f"{SOCIAL}/friend-requests", json={"user_id": b_uid}, headers=a_headers)
    assert r.status_code == 201, r.text

    # And the target's own view works from the same state.
    r = await client.get(f"{SOCIAL}/profile/me", headers=b_headers)
    assert r.status_code == 200, r.text
    assert r.json()["display_name"] == "Rider B"


async def _set_username(client, headers, username):
    r = await client.patch(f"{SOCIAL}/profile", json={"username": username}, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


# ---------------------------------------------------------------------------
# Profile
# ---------------------------------------------------------------------------


async def test_profile_me_creates_lazy_projection(client):
    headers, uid = await _user(client)
    r = await client.get(f"{SOCIAL}/profile/me", headers=headers)
    assert r.status_code == 200
    body = r.json()
    assert body["user_id"] == uid
    assert body["username"] is None
    assert body["display_name"] == "Rider A"
    assert body["profile_visibility"] == "public"
    assert body["allow_friend_requests"] == "everyone"
    assert body["search_visibility"] == "discoverable"
    assert "email" not in body


async def test_update_profile_username_normalized(client):
    headers, _ = await _user(client)
    body = await _set_username(client, headers, "Imad_Fouri")
    assert body["username"] == "imad_fouri"


async def test_username_uniqueness_is_case_insensitive(client):
    a, _ = await _user(client, "a")
    b, _ = await _user(client, "b")
    await _set_username(client, a, "gravel_rider")
    r = await client.patch(f"{SOCIAL}/profile", json={"username": "Gravel_Rider"}, headers=b)
    assert r.status_code == 409, r.text
    assert r.json()["error"]["code"] == "SOCIAL_USERNAME_TAKEN"


async def test_username_can_be_released_and_reclaimed(client):
    a, _ = await _user(client, "a")
    b, _ = await _user(client, "b")
    await _set_username(client, a, "free_handle")
    r = await client.patch(f"{SOCIAL}/profile", json={"username": None}, headers=a)
    assert r.status_code == 200
    assert r.json()["username"] is None
    body = await _set_username(client, b, "free_handle")
    assert body["username"] == "free_handle"


@pytest.mark.parametrize(
    "bad,code",
    [
        ("ab", "SOCIAL_INVALID_USERNAME"),
        ("has space", "SOCIAL_INVALID_USERNAME"),
        ("user@example.com", "SOCIAL_INVALID_USERNAME"),
        ("12345", "SOCIAL_INVALID_USERNAME"),
        ("a.b.c", "SOCIAL_INVALID_USERNAME"),
        # Over the schema length: rejected by request validation, same 422.
        ("x" * 31, "VALIDATION_ERROR"),
    ],
)
async def test_invalid_usernames_rejected(client, bad, code):
    headers, _ = await _user(client)
    r = await client.patch(f"{SOCIAL}/profile", json={"username": bad}, headers=headers)
    assert r.status_code == 422, r.text
    assert r.json()["error"]["code"] == code


async def test_bio_bound_and_country_validated(client):
    headers, _ = await _user(client)
    r = await client.patch(f"{SOCIAL}/profile", json={"bio": "x" * 501}, headers=headers)
    assert r.status_code == 422, r.text
    # Over the schema bound: request validation rejects before the service.
    r = await client.patch(f"{SOCIAL}/profile", json={"country_code": "FRA"}, headers=headers)
    assert r.status_code == 422, r.text
    assert r.json()["error"]["code"] == "VALIDATION_ERROR"
    # Well-formed shape, invalid value: the service rule fires.
    r = await client.patch(f"{SOCIAL}/profile", json={"country_code": "F1"}, headers=headers)
    assert r.status_code == 422, r.text
    assert r.json()["error"]["code"] == "SOCIAL_INVALID_COUNTRY"
    r = await client.patch(
        f"{SOCIAL}/profile",
        json={"bio": "Climber.", "country_code": "ma", "city": "Ifrane"},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    assert r.json()["country_code"] == "MA"


async def test_avatar_must_be_http_url(client):
    headers, _ = await _user(client)
    r = await client.patch(
        f"{SOCIAL}/profile", json={"avatar_url": "javascript:alert(1)"}, headers=headers
    )
    assert r.status_code == 422, r.text
    assert r.json()["error"]["code"] == "SOCIAL_INVALID_AVATAR"


async def test_profile_visibility_rules(client):
    a, _ = await _user(client, "a")
    b, buid = await _user(client, "b")
    await _set_username(client, b, "private_rider")
    await client.patch(f"{SOCIAL}/profile", json={"bio": "Secret training."}, headers=b)

    # PUBLIC: full view.
    me_a = await client.get(f"{SOCIAL}/profile/me", headers=a)
    auid = me_a.json()["user_id"]
    r = await client.get(f"{SOCIAL}/profile/{auid}", headers=b)
    assert r.status_code == 200
    assert r.json()["relationship"] == "NONE"
    assert r.json()["limited"] is False

    # PRIVATE: a stranger sees bare identity only, never the bio.
    await client.patch(
        f"{SOCIAL}/profile/privacy", json={"profile_visibility": "private"}, headers=b
    )
    await _user(client, "c")  # fresh stranger exists; A views B below
    r = await client.get(f"{SOCIAL}/profile/{buid}", headers=a)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["limited"] is True
    assert body["bio"] is None
    assert body["username"] == "private_rider"
    assert "email" not in body
    # ...while B still sees their own full profile.
    r = await client.get(f"{SOCIAL}/profile/{buid}", headers=b)
    assert r.status_code == 200
    assert r.json()["relationship"] == "SELF"
    assert r.json()["bio"] == "Secret training."


async def test_friends_only_unlocks_after_accept(client):
    a, auid = await _user(client, "a")
    b, buid = await _user(client, "b")
    await client.patch(
        f"{SOCIAL}/profile/privacy", json={"profile_visibility": "friends"}, headers=b
    )
    r = await client.get(f"{SOCIAL}/profile/{buid}", headers=a)
    assert r.json()["limited"] is True
    assert r.json()["bio"] is None

    req = await client.post(f"{SOCIAL}/friend-requests", json={"user_id": buid}, headers=a)
    assert req.status_code == 201, req.text
    acc = await client.post(f"{SOCIAL}/friend-requests/{req.json()['id']}/accept", headers=b)
    assert acc.status_code == 200, acc.text
    r = await client.get(f"{SOCIAL}/profile/{buid}", headers=a)
    assert r.json()["limited"] is False
    assert r.json()["relationship"] == "FRIENDS"
    assert auid != buid


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------


async def test_search_finds_public_by_username_and_name(client):
    a, _ = await _user(client, "a")
    b, buid = await _user(client, "b")
    await _set_username(client, b, "climb_king")
    r = await client.get(f"{SOCIAL}/users/search", params={"q": "climb"}, headers=a)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] == 1
    hit = body["items"][0]
    assert hit["user_id"] == buid
    assert hit["username"] == "climb_king"
    assert hit["relationship"] == "NONE"
    assert set(hit) <= {
        "user_id",
        "username",
        "display_name",
        "bio",
        "avatar_url",
        "cycling_category",
        "country_code",
        "city",
        "relationship",
        "limited",
    }


async def test_search_excludes_hidden_private_and_blocked(client):
    a, auid = await _user(client, "a")
    b, _ = await _user(client, "b")
    c, _ = await _user(client, "c")
    d, _ = await _user(client, "d")
    await _set_username(client, b, "ghost_rider")
    await _set_username(client, c, "hidden_rider")
    await _set_username(client, d, "blocked_rider")
    await client.patch(f"{SOCIAL}/profile/privacy", json={"search_visibility": "hidden"}, headers=c)
    await client.patch(
        f"{SOCIAL}/profile/privacy", json={"profile_visibility": "private"}, headers=b
    )
    # D blocks A: neither direction may surface the pair.
    r = await client.post(f"{SOCIAL}/blocks", json={"user_id": auid}, headers=d)
    assert r.status_code == 201, r.text

    for q in ("ghost", "hidden", "blocked"):
        r = await client.get(f"{SOCIAL}/users/search", params={"q": q}, headers=a)
        assert r.status_code == 200, r.text
        assert r.json()["total"] == 0, q


async def test_search_bounds_and_pagination(client):
    a, _ = await _user(client, "a")
    for i in range(3):
        h, _ = await _user(client, f"s{i}")
        await _set_username(client, h, f"peloton_{i}")
    r = await client.get(f"{SOCIAL}/users/search", params={"q": "x"}, headers=a)
    assert r.status_code == 422, r.text
    r = await client.get(f"{SOCIAL}/users/search", params={"q": "y" * 65}, headers=a)
    assert r.status_code == 422, r.text
    r = await client.get(
        f"{SOCIAL}/users/search", params={"q": "peloton", "page": 2, "page_size": 2}, headers=a
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert (body["total"], body["page"], body["page_size"]) == (3, 2, 2)
    assert len(body["items"]) == 1


async def test_search_rate_limited(client):
    a, _ = await _user(client, "a")
    last = None
    for _ in range(61):
        last = await client.get(f"{SOCIAL}/users/search", params={"q": "rider"}, headers=a)
    assert last.status_code == 429, last.text
    assert last.json()["error"]["code"] == "RATE_LIMITED"


# ---------------------------------------------------------------------------
# Friend requests
# ---------------------------------------------------------------------------


async def test_request_lifecycle_accept(client):
    a, _ = await _user(client, "a")
    b, buid = await _user(client, "b")
    req = await client.post(f"{SOCIAL}/friend-requests", json={"user_id": buid}, headers=a)
    assert req.status_code == 201, req.text
    rid = req.json()["id"]

    out = await client.get(f"{SOCIAL}/friend-requests", params={"direction": "outgoing"}, headers=a)
    assert out.json()["total"] == 1
    assert out.json()["items"][0]["direction"] == "outgoing"
    inc = await client.get(f"{SOCIAL}/friend-requests", params={"direction": "incoming"}, headers=b)
    assert inc.json()["total"] == 1
    assert inc.json()["items"][0]["direction"] == "incoming"

    # Relationship states track the lifecycle.
    me_a = await client.get(f"{SOCIAL}/profile/me", headers=a)
    auid = me_a.json()["user_id"]
    r = await client.get(f"{SOCIAL}/profile/{buid}", headers=a)
    assert r.json()["relationship"] == "OUTGOING_PENDING"
    r = await client.get(f"{SOCIAL}/profile/{auid}", headers=b)
    assert r.json()["relationship"] == "INCOMING_PENDING"

    acc = await client.post(f"{SOCIAL}/friend-requests/{rid}/accept", headers=b)
    assert acc.status_code == 200, acc.text
    assert acc.json()["status"] == "accepted"
    # Idempotent second accept converges.
    acc2 = await client.post(f"{SOCIAL}/friend-requests/{rid}/accept", headers=b)
    assert acc2.status_code == 200, acc2.text

    friends = await client.get(f"{SOCIAL}/friends", headers=a)
    assert friends.json()["total"] == 1
    assert friends.json()["items"][0]["user_id"] == buid
    assert friends.json()["items"][0]["friends_since"]


async def test_request_reject_and_cancel(client):
    a, _ = await _user(client, "a")
    b, buid = await _user(client, "b")
    me_a = await client.get(f"{SOCIAL}/profile/me", headers=a)
    auid = me_a.json()["user_id"]

    req = await client.post(f"{SOCIAL}/friend-requests", json={"user_id": buid}, headers=a)
    rid = req.json()["id"]
    rej = await client.post(f"{SOCIAL}/friend-requests/{rid}/reject", headers=b)
    assert rej.status_code == 200, rej.text
    r = await client.get(f"{SOCIAL}/profile/{buid}", headers=a)
    assert r.json()["relationship"] == "NONE"

    req2 = await client.post(f"{SOCIAL}/friend-requests", json={"user_id": buid}, headers=a)
    assert req2.status_code == 201, req2.text
    rid2 = req2.json()["id"]
    cancel = await client.delete(f"{SOCIAL}/friend-requests/{rid2}", headers=a)
    assert cancel.status_code == 200, cancel.text
    r = await client.get(f"{SOCIAL}/profile/{buid}", headers=a)
    assert r.json()["relationship"] == "NONE"
    assert auid != buid


async def test_request_validation_rules(client):
    a, _ = await _user(client, "a")
    b, buid = await _user(client, "b")
    me_a = await client.get(f"{SOCIAL}/profile/me", headers=a)
    auid = me_a.json()["user_id"]

    r = await client.post(f"{SOCIAL}/friend-requests", json={"user_id": auid}, headers=a)
    assert r.status_code == 422, r.text
    assert r.json()["error"]["code"] == "SOCIAL_CANNOT_TARGET_SELF"

    r = await client.post(
        f"{SOCIAL}/friend-requests", json={"user_id": str(uuid.uuid4())}, headers=a
    )
    assert r.status_code == 404, r.text
    assert r.json()["error"]["code"] == "SOCIAL_USER_NOT_FOUND"

    req = await client.post(f"{SOCIAL}/friend-requests", json={"user_id": buid}, headers=a)
    assert req.status_code == 201, req.text
    dup = await client.post(f"{SOCIAL}/friend-requests", json={"user_id": buid}, headers=a)
    assert dup.status_code == 409, dup.text
    assert dup.json()["error"]["code"] == "SOCIAL_REQUEST_PENDING"

    await client.post(f"{SOCIAL}/friend-requests/{req.json()['id']}/accept", headers=b)
    again = await client.post(f"{SOCIAL}/friend-requests", json={"user_id": buid}, headers=a)
    assert again.status_code == 409, again.text
    assert again.json()["error"]["code"] == "SOCIAL_ALREADY_FRIENDS"


async def test_request_respects_privacy_and_blocks(client):
    a, auid = await _user(client, "a")
    b, buid = await _user(client, "b")
    c, cuid = await _user(client, "c")
    d, _ = await _user(client, "d")

    await client.patch(
        f"{SOCIAL}/profile/privacy", json={"allow_friend_requests": "nobody"}, headers=b
    )
    r = await client.post(f"{SOCIAL}/friend-requests", json={"user_id": buid}, headers=a)
    assert r.status_code == 403, r.text
    assert r.json()["error"]["code"] == "SOCIAL_REQUESTS_NOT_ALLOWED"

    await client.patch(
        f"{SOCIAL}/profile/privacy", json={"profile_visibility": "private"}, headers=c
    )
    r = await client.post(f"{SOCIAL}/friend-requests", json={"user_id": cuid}, headers=a)
    assert r.status_code == 403, r.text

    me_d = await client.get(f"{SOCIAL}/profile/me", headers=d)
    duid = me_d.json()["user_id"]
    await client.post(f"{SOCIAL}/blocks", json={"user_id": duid}, headers=a)
    r = await client.post(f"{SOCIAL}/friend-requests", json={"user_id": duid}, headers=a)
    assert r.status_code == 404, r.text
    r = await client.post(f"{SOCIAL}/friend-requests", json={"user_id": auid}, headers=d)
    assert r.status_code == 404, r.text


async def test_remove_friend(client):
    a, _ = await _user(client, "a")
    b, buid = await _user(client, "b")
    req = await client.post(f"{SOCIAL}/friend-requests", json={"user_id": buid}, headers=a)
    await client.post(f"{SOCIAL}/friend-requests/{req.json()['id']}/accept", headers=b)
    r = await client.delete(f"{SOCIAL}/friends/{buid}", headers=a)
    assert r.status_code == 200, r.text
    assert (await client.get(f"{SOCIAL}/friends", headers=a)).json()["total"] == 0
    assert (await client.get(f"{SOCIAL}/friends", headers=b)).json()["total"] == 0
    me_a = await client.get(f"{SOCIAL}/profile/me", headers=a)
    r = await client.get(f"{SOCIAL}/profile/{buid}", headers=a)
    assert r.json()["relationship"] == "NONE"
    assert me_a.json()["user_id"] != buid
    # Removing twice is a 404, not a silent success.
    r = await client.delete(f"{SOCIAL}/friends/{buid}", headers=a)
    assert r.status_code == 404, r.text
    assert r.json()["error"]["code"] == "SOCIAL_FRIENDSHIP_NOT_FOUND"


# ---------------------------------------------------------------------------
# Blocks
# ---------------------------------------------------------------------------


async def test_block_unblock_cycle(client):
    a, _ = await _user(client, "a")
    b, buid = await _user(client, "b")
    r = await client.post(f"{SOCIAL}/blocks", json={"user_id": buid}, headers=a)
    assert r.status_code == 201, r.text
    # Idempotent second block.
    r = await client.post(f"{SOCIAL}/blocks", json={"user_id": buid}, headers=a)
    assert r.status_code == 201, r.text
    blocks = await client.get(f"{SOCIAL}/blocks", headers=a)
    assert blocks.json()["total"] == 1
    assert blocks.json()["items"][0]["user_id"] == buid
    # Blocker sees BLOCKED; the blocked party sees BLOCKED_BY_USER, limited.
    me_a = await client.get(f"{SOCIAL}/profile/me", headers=a)
    auid = me_a.json()["user_id"]
    r = await client.get(f"{SOCIAL}/profile/{buid}", headers=a)
    assert r.json()["relationship"] == "BLOCKED"
    r = await client.get(f"{SOCIAL}/profile/{auid}", headers=b)
    assert r.json()["relationship"] == "BLOCKED_BY_USER"
    assert r.json()["limited"] is True
    assert r.json()["bio"] is None
    # Unblock restores NONE, never the friendship.
    r = await client.delete(f"{SOCIAL}/blocks/{buid}", headers=a)
    assert r.status_code == 200, r.text
    r = await client.get(f"{SOCIAL}/profile/{buid}", headers=a)
    assert r.json()["relationship"] == "NONE"
    assert (await client.get(f"{SOCIAL}/friends", headers=a)).json()["total"] == 0
    # Unblocking twice is a 404.
    r = await client.delete(f"{SOCIAL}/blocks/{buid}", headers=a)
    assert r.status_code == 404, r.text
    assert r.json()["error"]["code"] == "SOCIAL_BLOCK_NOT_FOUND"


async def test_block_destroys_friendship_and_pending(client):
    a, _ = await _user(client, "a")
    b, buid = await _user(client, "b")
    _c, cuid = await _user(client, "c")
    req = await client.post(f"{SOCIAL}/friend-requests", json={"user_id": buid}, headers=a)
    await client.post(f"{SOCIAL}/friend-requests/{req.json()['id']}/accept", headers=b)
    assert (await client.get(f"{SOCIAL}/friends", headers=a)).json()["total"] == 1
    await client.post(f"{SOCIAL}/friend-requests", json={"user_id": cuid}, headers=a)

    await client.post(f"{SOCIAL}/blocks", json={"user_id": buid}, headers=a)
    assert (await client.get(f"{SOCIAL}/friends", headers=a)).json()["total"] == 0
    # The pending request to C survives (different pair); A-B is gone.
    out = await client.get(f"{SOCIAL}/friend-requests", params={"direction": "outgoing"}, headers=a)
    assert out.json()["total"] == 1
    await client.post(f"{SOCIAL}/blocks", json={"user_id": cuid}, headers=a)
    out = await client.get(f"{SOCIAL}/friend-requests", params={"direction": "outgoing"}, headers=a)
    assert out.json()["total"] == 0


async def test_cannot_block_self(client):
    a, _ = await _user(client, "a")
    me_a = await client.get(f"{SOCIAL}/profile/me", headers=a)
    r = await client.post(f"{SOCIAL}/blocks", json={"user_id": me_a.json()["user_id"]}, headers=a)
    assert r.status_code == 422, r.text
    assert r.json()["error"]["code"] == "SOCIAL_CANNOT_TARGET_SELF"


# ---------------------------------------------------------------------------
# Authorization / IDOR
# ---------------------------------------------------------------------------


async def test_foreign_relationship_mutations_are_404(client):
    a, _ = await _user(client, "a")
    b, _ = await _user(client, "b")
    c, cuid = await _user(client, "c")
    me_b = await client.get(f"{SOCIAL}/profile/me", headers=b)
    buid = me_b.json()["user_id"]
    req = await client.post(f"{SOCIAL}/friend-requests", json={"user_id": buid}, headers=a)
    rid = req.json()["id"]

    # C is party to nothing: every mutation is 404, leaking nothing.
    assert (
        await client.post(f"{SOCIAL}/friend-requests/{rid}/accept", headers=c)
    ).status_code == 404
    assert (
        await client.post(f"{SOCIAL}/friend-requests/{rid}/reject", headers=c)
    ).status_code == 404
    assert (await client.delete(f"{SOCIAL}/friend-requests/{rid}", headers=c)).status_code == 404
    # The requester cannot accept/reject their own outgoing request either.
    assert (
        await client.post(f"{SOCIAL}/friend-requests/{rid}/accept", headers=a)
    ).status_code == 404
    assert (
        await client.post(f"{SOCIAL}/friend-requests/{rid}/reject", headers=a)
    ).status_code == 404
    # C cannot remove A-B friendship once formed, nor unblock A's block.
    await client.post(f"{SOCIAL}/friend-requests/{rid}/accept", headers=b)
    assert (await client.delete(f"{SOCIAL}/friends/{buid}", headers=c)).status_code == 404
    await client.post(f"{SOCIAL}/blocks", json={"user_id": cuid}, headers=a)
    assert (await client.delete(f"{SOCIAL}/blocks/{cuid}", headers=b)).status_code == 404
    # C cannot read A's or B's request queues or friend lists (self-only endpoints).
    assert (await client.get(f"{SOCIAL}/friend-requests", headers=c)).json()["total"] == 0
    assert (await client.get(f"{SOCIAL}/friends", headers=c)).json()["total"] == 0


async def test_profile_patch_only_touches_self(client):
    a, _ = await _user(client, "a")
    b, buid = await _user(client, "b")
    await client.patch(
        f"{SOCIAL}/profile", json={"display_name": "Mallory", "bio": "pwned"}, headers=a
    )
    r = await client.get(f"{SOCIAL}/profile/{buid}", headers=a)
    assert r.json()["display_name"] != "Mallory"
    me_b = await client.get(f"{SOCIAL}/profile/me", headers=b)
    assert me_b.json()["bio"] is None


async def test_unauthenticated_social_is_401(client):
    assert (await client.get(f"{SOCIAL}/profile/me")).status_code == 401
    assert (await client.get(f"{SOCIAL}/friends")).status_code == 401
    assert (await client.get(f"{SOCIAL}/users/search", params={"q": "ab"})).status_code == 401
    assert (
        await client.post(f"{SOCIAL}/friend-requests", json={"user_id": str(uuid.uuid4())})
    ).status_code == 401


# ---------------------------------------------------------------------------
# Concurrency
# ---------------------------------------------------------------------------


async def test_simultaneous_cross_requests_resolve_to_one_row(client):
    a, _ = await _user(client, "a")
    b, _ = await _user(client, "b")
    me_a = await client.get(f"{SOCIAL}/profile/me", headers=a)
    me_b = await client.get(f"{SOCIAL}/profile/me", headers=b)
    auid, buid = me_a.json()["user_id"], me_b.json()["user_id"]
    ra, rb = await asyncio.gather(
        client.post(f"{SOCIAL}/friend-requests", json={"user_id": buid}, headers=a),
        client.post(f"{SOCIAL}/friend-requests", json={"user_id": auid}, headers=b),
    )
    # Exactly one writer wins (201); the loser sees the pending row (409).
    assert sorted([ra.status_code, rb.status_code]) == [201, 409]
    out_a = await client.get(
        f"{SOCIAL}/friend-requests", params={"direction": "outgoing"}, headers=a
    )
    out_b = await client.get(
        f"{SOCIAL}/friend-requests", params={"direction": "outgoing"}, headers=b
    )
    inc_a = await client.get(
        f"{SOCIAL}/friend-requests", params={"direction": "incoming"}, headers=a
    )
    inc_b = await client.get(
        f"{SOCIAL}/friend-requests", params={"direction": "incoming"}, headers=b
    )
    total_pending = out_a.json()["total"] + out_b.json()["total"]
    assert total_pending == 1
    assert inc_a.json()["total"] + inc_b.json()["total"] == 1


async def test_simultaneous_accepts_converge(client):
    a, _ = await _user(client, "a")
    b, buid = await _user(client, "b")
    req = await client.post(f"{SOCIAL}/friend-requests", json={"user_id": buid}, headers=a)
    rid = req.json()["id"]
    r1, r2 = await asyncio.gather(
        client.post(f"{SOCIAL}/friend-requests/{rid}/accept", headers=b),
        client.post(f"{SOCIAL}/friend-requests/{rid}/accept", headers=b),
    )
    assert r1.status_code == 200 and r2.status_code == 200
    friends = await client.get(f"{SOCIAL}/friends", headers=a)
    assert friends.json()["total"] == 1


async def test_request_block_race_converges_to_blocked_without_relationship(client):
    # Run the race a few times on fresh pairs: every ending must be blocked
    # with no relationship rows, regardless of interleaving.
    for i in range(3):
        x, _ = await _user(client, f"rx{i}")
        y, yuid = await _user(client, f"ry{i}")
        me_x = await client.get(f"{SOCIAL}/profile/me", headers=x)
        xuid = me_x.json()["user_id"]
        await asyncio.gather(
            client.post(f"{SOCIAL}/friend-requests", json={"user_id": yuid}, headers=x),
            client.post(f"{SOCIAL}/blocks", json={"user_id": xuid}, headers=y),
            return_exceptions=True,
        )
        blocks = await client.get(f"{SOCIAL}/blocks", headers=y)
        assert any(item["user_id"] == xuid for item in blocks.json()["items"])
        out = await client.get(
            f"{SOCIAL}/friend-requests", params={"direction": "outgoing"}, headers=x
        )
        assert out.json()["total"] == 0


async def test_remove_accept_race_never_duplicates(client):
    a, _ = await _user(client, "a")
    b, buid = await _user(client, "b")
    req = await client.post(f"{SOCIAL}/friend-requests", json={"user_id": buid}, headers=a)
    rid = req.json()["id"]
    await asyncio.gather(
        client.post(f"{SOCIAL}/friend-requests/{rid}/accept", headers=b),
        client.delete(f"{SOCIAL}/friends/{buid}", headers=a),
        return_exceptions=True,
    )
    friends = await client.get(f"{SOCIAL}/friends", headers=a)
    assert friends.json()["total"] in (0, 1)


# ---------------------------------------------------------------------------
# Privacy shape + contract
# ---------------------------------------------------------------------------


FORBIDDEN_KEYS = {
    "email",
    "phone",
    "password_hash",
    "access_token",
    "refresh_token",
    "lat",
    "lon",
    "latitude",
    "longitude",
    "coordinates",
    "location",
}


def _scan(obj):
    if isinstance(obj, dict):
        for k, v in obj.items():
            assert k.lower() not in FORBIDDEN_KEYS, k
            _scan(v)
    elif isinstance(obj, list):
        for v in obj:
            _scan(v)


async def test_no_private_or_location_keys_anywhere(client):
    a, _ = await _user(client, "a")
    b, buid = await _user(client, "b")
    await _set_username(client, b, "clean_rider")
    await client.patch(
        f"{SOCIAL}/profile",
        json={"bio": "Alps climber.", "city": "Grenoble", "country_code": "FR"},
        headers=b,
    )
    req = await client.post(f"{SOCIAL}/friend-requests", json={"user_id": buid}, headers=a)
    await client.post(f"{SOCIAL}/friend-requests/{req.json()['id']}/accept", headers=b)
    endpoints = [
        ("GET", f"{SOCIAL}/profile/me", None),
        ("GET", f"{SOCIAL}/profile/{buid}", None),
        ("GET", f"{SOCIAL}/users/search?q=clean", None),
        ("GET", f"{SOCIAL}/friends", None),
        ("GET", f"{SOCIAL}/friend-requests?direction=outgoing", None),
        ("GET", f"{SOCIAL}/blocks", None),
    ]
    for method, path, body in endpoints:
        if method == "GET":
            r = await client.get(path, headers=a)
        else:
            r = await client.post(path, json=body, headers=a)
        assert r.status_code == 200, (path, r.text)
        _scan(r.json())


async def test_social_routes_in_openapi(client):
    r = await client.get("/openapi.json")
    assert r.status_code == 200
    paths = r.json()["paths"]
    for p in (
        "/api/v1/social/profile/me",
        "/api/v1/social/profile",
        "/api/v1/social/profile/privacy",
        "/api/v1/social/profile/{user_id}",
        "/api/v1/social/users/search",
        "/api/v1/social/friend-requests",
        "/api/v1/social/friend-requests/{request_id}/accept",
        "/api/v1/social/friend-requests/{request_id}/reject",
        "/api/v1/social/friend-requests/{request_id}",
        "/api/v1/social/friends",
        "/api/v1/social/friends/{user_id}",
        "/api/v1/social/blocks",
        "/api/v1/social/blocks/{user_id}",
    ):
        assert p in paths, p

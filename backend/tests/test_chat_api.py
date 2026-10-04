"""Phase 8.3 chat API tests (ADR-14).

Real PostgreSQL, real JWT auth, real concurrent connections for the race tests.
Nothing mocked: chat's whole value is its authorization and ordering guarantees,
and a mocked session would test the mocks.

Each test name states the rule it defends, so a failure reads as a broken rule
rather than a broken assertion. The four rules that get the most coverage are the
ones that are cheap to get subtly wrong and expensive to get subtly wrong in
production: 404-not-403, block scope, idempotent retry, and `seq` allocation.
"""

import asyncio
import uuid

CHAT = "/api/v1/chat"
TEAMS = "/api/v1/teams"
SOCIAL = "/api/v1/social"
AUTH = "/api/v1/auth"


def _reg(tag):
    return {
        "email": f"chat_{tag}_{uuid.uuid4().hex[:8]}@example.com",
        "password": "StrongPass123",
        "password_confirm": "StrongPass123",
        "display_name": f"Rider {tag.title()}",
    }


#: id → headers, so a test that already knows a rider's id can act as them
#: without registering a second account.
_HEADER_CACHE: dict[str, dict] = {}


async def _user(client, tag="a"):
    headers, user_id = await _new_user(client, tag)
    _HEADER_CACHE[user_id] = headers
    return headers, user_id


async def _new_user(client, tag):
    data = _reg(tag)
    assert (await client.post(f"{AUTH}/register", json=data)).status_code == 201
    r = await client.post(
        f"{AUTH}/login", json={"email": data["email"], "password": data["password"]}
    )
    assert r.status_code == 200
    headers = {"Authorization": f"Bearer {r.json()['access_token']}"}
    me = await client.get(f"{SOCIAL}/profile/me", headers=headers)
    # Claim a handle so member/invitation lists expose real public identity.
    await client.patch(
        f"{SOCIAL}/profile",
        json={"username": f"{tag}_rider", "display_name": f"Rider {tag.title()}"},
        headers=headers,
    )
    return headers, me.json()["user_id"]


async def _team(client, headers, name="Crew"):
    r = await client.post(f"{TEAMS}", json={"name": name}, headers=headers)
    assert r.status_code == 201, r.text
    return r.json()


async def _team_channel(client, headers, team_id):
    r = await client.get(f"{CHAT}/teams/{team_id}/conversation", headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


async def _send(client, headers, conversation_id, body, client_id=None):
    return await client.post(
        f"{CHAT}/conversations/{conversation_id}/messages",
        json={
            "body": body,
            "client_message_id": str(client_id or uuid.uuid4()),
        },
        headers=headers,
    )


async def _send_ok(client, headers, conversation_id, body, client_id=None):
    r = await _send(client, headers, conversation_id, body, client_id)
    assert r.status_code == 201, r.text
    return r.json()["message"]


async def _dm(client, a_headers, b_user_id):
    r = await client.post(f"{CHAT}/direct", params={"target_user_id": b_user_id}, headers=a_headers)
    assert r.status_code == 201, r.text
    return r.json()


async def _block(client, blocker_headers, blocked_id):
    r = await client.post(f"{SOCIAL}/blocks", json={"user_id": blocked_id}, headers=blocker_headers)
    assert r.status_code == 201, r.text


# ---------------------------------------------------------------------------
# Team channels
# ---------------------------------------------------------------------------


async def test_team_channel_is_created_once_and_reused(client):
    """One channel per team, no matter how many members open it."""
    h, _ = await _user(client, "a")
    team = await _team(client, h)
    first = await _team_channel(client, h, team["id"])
    second = await _team_channel(client, h, team["id"])
    assert first["id"] == second["id"]
    assert first["kind"] == "team"
    assert first["team_id"] == team["id"]


async def test_second_member_joins_the_existing_team_channel(client):
    a, _ = await _user(client, "a")
    b, bid = await _user(client, "b")
    team = await _team(client, a)
    first = await _team_channel(client, a, team["id"])
    b_inv = await client.post(f"{TEAMS}/{team['id']}/invitations", json={"user_id": bid}, headers=a)
    await client.post(f"{TEAMS}/invitations/{b_inv.json()['id']}/accept", headers=b)
    second = await _team_channel(client, b, team["id"])
    assert second["id"] == first["id"]


async def test_non_member_cannot_open_a_team_channel(client):
    """A stranger gets 404 — the same answer as a team that does not exist."""
    owner, _ = await _user(client, "a")
    stranger, _ = await _user(client, "b")
    team = await _team(client, owner)
    r = await client.get(f"{CHAT}/teams/{team['id']}/conversation", headers=stranger)
    assert r.status_code == 404
    missing = await client.get(f"{CHAT}/teams/{uuid.uuid4()}/conversation", headers=stranger)
    assert missing.status_code == r.status_code


async def test_team_channel_is_unreadable_after_removal_from_the_team(client):
    """Team standing is re-derived live, not read from the channel roster."""
    owner, _ = await _user(client, "a")
    b, bid = await _user(client, "b")
    team = await _team(client, owner)
    inv = await client.post(
        f"{TEAMS}/{team['id']}/invitations", json={"user_id": bid}, headers=owner
    )
    await client.post(f"{TEAMS}/invitations/{inv.json()['id']}/accept", headers=b)
    channel = await _team_channel(client, b, team["id"])
    await _send_ok(client, owner, channel["id"], "before removal")

    removed = await client.delete(f"{TEAMS}/{team['id']}/members/{bid}", headers=owner)
    assert removed.status_code in (200, 204)

    r = await client.get(f"{CHAT}/conversations/{channel['id']}/messages", headers=b)
    assert r.status_code == 404
    assert (await _send(client, b, channel["id"], "after removal")).status_code == 404
    assert (
        await client.get(f"{CHAT}/teams/{team['id']}/conversation", headers=b)
    ).status_code == 404


async def test_blocking_does_not_remove_team_channel_access(client):
    """A block is a personal boundary, not a team severance (ADR-14 §2)."""
    owner, aid = await _user(client, "a")
    b, bid = await _user(client, "b")
    team = await _team(client, owner)
    inv = await client.post(
        f"{TEAMS}/{team['id']}/invitations", json={"user_id": bid}, headers=owner
    )
    await client.post(f"{TEAMS}/invitations/{inv.json()['id']}/accept", headers=b)
    channel = await _team_channel(client, b, team["id"])

    await _block(client, b, aid)

    # Both sides keep posting in the team channel after the block.
    assert (await _send(client, owner, channel["id"], "team still open")).status_code == 201
    assert (await _send(client, b, channel["id"], "team still open")).status_code == 201
    r = await client.get(f"{CHAT}/conversations/{channel['id']}", headers=b)
    assert r.status_code == 200


# ---------------------------------------------------------------------------
# Direct messages
# ---------------------------------------------------------------------------


async def test_direct_conversation_is_find_or_create(client):
    a, _ = await _user(client, "a")
    _, bid = await _user(client, "b")
    first = await _dm(client, a, bid)
    second = await _dm(client, a, bid)
    assert first["id"] == second["id"]
    assert first["kind"] == "direct"
    assert first["peer_user_id"] == bid


async def test_direct_conversation_needs_no_friendship(client):
    """DM is a personal-interaction right, not a social status (ADR-14 §2)."""
    a, _ = await _user(client, "a")
    _, bid = await _user(client, "b")
    r = await client.post(f"{CHAT}/direct", params={"target_user_id": bid}, headers=a)
    assert r.status_code == 201, r.text


async def test_cannot_message_yourself(client):
    a, aid = await _user(client, "a")
    r = await client.post(f"{CHAT}/direct", params={"target_user_id": aid}, headers=a)
    assert r.status_code == 422


async def test_a_block_prevents_opening_a_dm_in_either_direction(client):
    a, aid = await _user(client, "a")
    b, bid = await _user(client, "b")
    await _block(client, a, bid)

    from_blocker = await client.post(f"{CHAT}/direct", params={"target_user_id": bid}, headers=a)
    from_blocked = await client.post(f"{CHAT}/direct", params={"target_user_id": aid}, headers=b)
    # Both are 404: a distinct code would confirm the rider exists.
    assert from_blocker.status_code == 404
    assert from_blocked.status_code == 404


async def test_a_block_refuses_reading_and_sending_but_retains_history(client, db_session_factory):
    """A block closes BOTH directions of personal messaging, and retains history.

    The name used to say "keeps history readable", which contradicted what the
    test asserts. Reading and retention are different properties: the history is
    never deleted, so unblocking restores it exactly (ADR-14 §2.1).
    """
    a, aid = await _user(client, "a")
    b, bid = await _user(client, "b")
    dm = await _dm(client, a, bid)
    await _send_ok(client, a, dm["id"], "hello before the block")

    await _block(client, b, aid)

    # Sending is refused for both directions.
    assert (await _send(client, a, dm["id"], "still here?")).status_code == 404
    assert (await _send(client, b, dm["id"], "nope")).status_code == 404
    # And neither party can read the thread while the block stands.
    assert (
        await client.get(f"{CHAT}/conversations/{dm['id']}/messages", headers=a)
    ).status_code == 404
    assert (
        await client.get(f"{CHAT}/conversations/{dm['id']}/messages", headers=b)
    ).status_code == 404

    # RETAINED means the row is still physically stored — not merely hidden by
    # a policy check. Asserted against the database so a future change that
    # hard-deletes the history on block would fail here.
    from sqlalchemy import text as sql_text

    async with db_session_factory() as db:
        stored = (
            (
                await db.execute(
                    sql_text("SELECT body FROM messages WHERE conversation_id = :cid"),
                    {"cid": uuid.UUID(dm["id"])},
                )
            )
            .scalars()
            .all()
        )
    assert stored == [
        "hello before the block"
    ], "a block must never delete message history (ADR-14 §2.4)"

    # Unblocking restores the exact thread rather than leaving a gap.
    unblocked = await client.delete(f"{SOCIAL}/blocks/{aid}", headers=b)
    assert unblocked.status_code in (200, 204)
    history = await client.get(f"{CHAT}/conversations/{dm['id']}/messages", headers=a)
    assert history.status_code == 200
    assert history.json()["items"][0]["body"] == "hello before the block"


async def test_a_team_channel_with_three_members_renders_without_500(client):
    """Regression: a team channel is not a DM, so it has no single "peer".

    `_peer_of` returns every other member, and a team channel has more than one.
    The Phase 8.3 view called it unconditionally, so a channel with three or more
    members raised MultipleResultsFound and answered 500 — invisible in Phase
    8.3 because its smoke only ever built two-member teams.
    """
    a, _ = await _user(client, "a")
    b, bid = await _user(client, "b")
    c, cid = await _user(client, "c")
    team = (await client.post(f"{TEAMS}", json={"name": "Big Crew"}, headers=a)).json()

    for user_id in (bid, cid):
        offer = await client.post(
            f"{TEAMS}/{team['id']}/invitations", json={"user_id": user_id}, headers=a
        )
        assert offer.status_code == 201, offer.text
        accept = await client.post(
            f"{TEAMS}/invitations/{offer.json()['id']}/accept",
            headers=_headers(user_id),
        )
        assert accept.status_code == 200

    members = await client.get(f"{TEAMS}/{team['id']}/members", headers=a)
    assert len(members.json()["items"]) == 3

    channel = await _team_channel(client, a, team["id"])
    await _send_ok(client, a, channel["id"], "hello everyone")

    # Every one of the three members must be able to open the channel.
    for headers in (a, b, c):
        listing = await client.get(f"{CHAT}/teams/{team['id']}/conversation", headers=headers)
        assert listing.status_code == 200, listing.text
        # A team channel has no single peer, so the peer fields stay null.
        assert listing.json()["peer_user_id"] is None
        history = await client.get(
            f"{CHAT}/conversations/{channel['id']}/messages", headers=headers
        )
        assert history.status_code == 200, history.text


def _headers(user_id: str) -> dict:
    """Headers for an already-created rider, by id.

    Only used by the multi-member tests, where the ids are known from the
    invitation flow; re-registering would create a different account.
    """
    return _HEADER_CACHE[user_id]


async def test_a_direct_conversation_still_reports_its_peer(client):
    """The DM path is untouched: a DM has exactly one peer and must name it."""
    a, _ = await _user(client, "a")
    b, bid = await _user(client, "b")
    dm = await _dm(client, a, bid)
    body = (await client.get(f"{CHAT}/conversations/{dm['id']}", headers=a)).json()
    assert body["peer_user_id"] == bid


async def test_a_removed_team_member_still_gets_404_not_500(client):
    """The 3-member bug also applied to a member removed from the team."""
    a, _ = await _user(client, "a")
    b, bid = await _user(client, "b")
    c, cid = await _user(client, "c")
    team = (await client.post(f"{TEAMS}", json={"name": "Crew"}, headers=a)).json()
    for headers, user_id in ((b, bid), (c, cid)):
        offer = await client.post(
            f"{TEAMS}/{team['id']}/invitations", json={"user_id": user_id}, headers=a
        )
        await client.post(f"{TEAMS}/invitations/{offer.json()['id']}/accept", headers=headers)
    channel = await _team_channel(client, a, team["id"])
    await _send_ok(client, a, channel["id"], "hi")
    await client.delete(f"{TEAMS}/{team['id']}/members/{bid}", headers=a)
    r = await client.get(f"{CHAT}/conversations/{channel['id']}/messages", headers=b)
    assert r.status_code == 404


async def test_stranger_cannot_read_a_private_conversation(client):
    a, _ = await _user(client, "a")
    _, bid = await _user(client, "b")
    stranger, _ = await _user(client, "c")
    dm = await _dm(client, a, bid)

    r = await client.get(f"{CHAT}/conversations/{dm['id']}/messages", headers=stranger)
    assert r.status_code == 404
    assert (
        await client.get(f"{CHAT}/conversations/{dm['id']}", headers=stranger)
    ).status_code == 404


async def test_a_missing_conversation_is_indistinguishable_from_a_forbidden_one(
    client,
):
    a, _ = await _user(client, "a")
    _, bid = await _user(client, "b")
    stranger, _ = await _user(client, "c")
    dm = await _dm(client, a, bid)
    real = await client.get(f"{CHAT}/conversations/{dm['id']}/messages", headers=stranger)
    fake = await client.get(f"{CHAT}/conversations/{uuid.uuid4()}/messages", headers=stranger)
    assert real.status_code == fake.status_code == 404
    assert real.json() == fake.json()


# ---------------------------------------------------------------------------
# Sending, ordering, idempotency
# ---------------------------------------------------------------------------


async def test_message_send_returns_201_and_a_sequence(client):
    a, _ = await _user(client, "a")
    team = await _team(client, a)
    channel = await _team_channel(client, a, team["id"])
    r = await _send(client, a, channel["id"], "first")
    assert r.status_code == 201
    body = r.json()
    assert body["duplicate"] is False
    msg = body["message"]
    assert msg["seq"] == 1
    assert msg["is_mine"] is True
    assert msg["can_edit"] is True
    assert msg["message_type"] == "text"


async def test_retrying_the_same_client_message_id_is_idempotent(client):
    """A retry after a dropped response must not create a second message."""
    a, _ = await _user(client, "a")
    team = await _team(client, a)
    channel = await _team_channel(client, a, team["id"])
    client_id = uuid.uuid4()

    first = await _send(client, a, channel["id"], "only once", client_id)
    second = await _send(client, a, channel["id"], "only once", client_id)
    assert first.status_code == 201
    assert second.status_code == 200
    assert second.json()["duplicate"] is True
    assert second.json()["message"]["id"] == first.json()["message"]["id"]
    assert second.json()["message"]["seq"] == first.json()["message"]["seq"]

    history = await client.get(f"{CHAT}/conversations/{channel['id']}/messages", headers=a)
    assert len(history.json()["items"]) == 1


async def test_the_same_client_id_from_two_different_senders_is_two_messages(client):
    """Idempotency is scoped to (conversation, sender, client id) on purpose:
    two riders on a phone-tossed client id must not collide."""
    a, _ = await _user(client, "a")
    b, bid = await _user(client, "b")
    team = await _team(client, a)
    inv = await client.post(f"{TEAMS}/{team['id']}/invitations", json={"user_id": bid}, headers=a)
    await client.post(f"{TEAMS}/invitations/{inv.json()['id']}/accept", headers=b)
    channel = await _team_channel(client, b, team["id"])

    shared = uuid.uuid4()
    first = await _send(client, a, channel["id"], "from a", shared)
    second = await _send(client, b, channel["id"], "from b", shared)
    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json()["message"]["id"] != second.json()["message"]["id"]


async def test_sequence_is_dense_and_monotonic_across_senders(client):
    a, _ = await _user(client, "a")
    b, bid = await _user(client, "b")
    team = await _team(client, a)
    inv = await client.post(f"{TEAMS}/{team['id']}/invitations", json={"user_id": bid}, headers=a)
    await client.post(f"{TEAMS}/invitations/{inv.json()['id']}/accept", headers=b)
    channel = await _team_channel(client, b, team["id"])

    for i in range(5):
        await _send_ok(client, a if i % 2 == 0 else b, channel["id"], f"m{i}")

    history = await client.get(f"{CHAT}/conversations/{channel['id']}/messages", headers=a)
    seqs = [m["seq"] for m in history.json()["items"]]
    assert seqs == [5, 4, 3, 2, 1]  # newest first
    assert sorted(seqs) == [1, 2, 3, 4, 5]  # no gaps


async def test_concurrent_sends_get_distinct_sequences(client):
    """The advisory lock serializes `seq` allocation; the unique constraint is
    the final arbiter. Either way, no two messages may share a `seq`."""
    a, _ = await _user(client, "a")
    team = await _team(client, a)
    channel = await _team_channel(client, a, team["id"])

    results = await asyncio.gather(
        *[_send(client, a, channel["id"], f"racer {i}") for i in range(6)]
    )
    assert all(r.status_code == 201 for r in results)
    seqs = [r.json()["message"]["seq"] for r in results]
    assert sorted(seqs) == list(range(1, 7))
    assert len(set(seqs)) == 6


async def test_concurrent_opens_of_one_dm_converge(client):
    a, _ = await _user(client, "a")
    _, bid = await _user(client, "b")
    results = await asyncio.gather(
        client.post(f"{CHAT}/direct", params={"target_user_id": bid}, headers=a),
        client.post(f"{CHAT}/direct", params={"target_user_id": bid}, headers=a),
    )
    assert all(r.status_code == 201 for r in results)
    assert results[0].json()["id"] == results[1].json()["id"]


async def test_blank_and_oversized_bodies_are_rejected(client):
    a, _ = await _user(client, "a")
    team = await _team(client, a)
    channel = await _team_channel(client, a, team["id"])
    assert (await _send(client, a, channel["id"], "   ")).status_code == 422
    assert (await _send(client, a, channel["id"], "x" * 4001)).status_code == 422


async def test_unknown_body_fields_are_rejected(client):
    a, _ = await _user(client, "a")
    team = await _team(client, a)
    channel = await _team_channel(client, a, team["id"])
    r = await client.post(
        f"{CHAT}/conversations/{channel['id']}/messages",
        json={"body": "hi", "client_message_id": str(uuid.uuid4()), "sender": "me"},
        headers=a,
    )
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# Cursor pagination
# ---------------------------------------------------------------------------


async def test_history_pages_by_cursor_without_gaps_or_duplicates(client):
    a, _ = await _user(client, "a")
    team = await _team(client, a)
    channel = await _team_channel(client, a, team["id"])
    for i in range(12):
        await _send_ok(client, a, channel["id"], f"m{i:02d}")

    first = await client.get(
        f"{CHAT}/conversations/{channel['id']}/messages",
        params={"limit": 5},
        headers=a,
    )
    page1 = first.json()
    assert [m["seq"] for m in page1["items"]] == [12, 11, 10, 9, 8]
    assert page1["has_more"] is True
    assert page1["next_before_seq"] == 8

    second = await client.get(
        f"{CHAT}/conversations/{channel['id']}/messages",
        params={"limit": 5, "before_seq": page1["next_before_seq"]},
        headers=a,
    )
    page2 = second.json()
    assert [m["seq"] for m in page2["items"]] == [7, 6, 5, 4, 3]

    third = await client.get(
        f"{CHAT}/conversations/{channel['id']}/messages",
        params={"limit": 5, "before_seq": page2["next_before_seq"]},
        headers=a,
    )
    page3 = third.json()
    assert [m["seq"] for m in page3["items"]] == [2, 1]
    assert page3["has_more"] is False
    assert page3["next_before_seq"] is None

    seen = [m["seq"] for p in (page1, page2, page3) for m in p["items"]]
    assert len(set(seen)) == 12  # no duplicates across pages
    assert sorted(seen) == list(range(1, 13))  # nothing skipped


async def test_a_message_sent_mid_paging_does_not_shift_the_cursor(client):
    """Offset paging would silently skip or repeat a row here; `seq` does not."""
    a, _ = await _user(client, "a")
    team = await _team(client, a)
    channel = await _team_channel(client, a, team["id"])
    for i in range(6):
        await _send_ok(client, a, channel["id"], f"m{i}")

    page1 = (
        await client.get(
            f"{CHAT}/conversations/{channel['id']}/messages",
            params={"limit": 3},
            headers=a,
        )
    ).json()
    assert [m["seq"] for m in page1["items"]] == [6, 5, 4]

    # New traffic lands at the head while the rider is still scrolling back.
    await _send_ok(client, a, channel["id"], "arrived late")

    page2 = (
        await client.get(
            f"{CHAT}/conversations/{channel['id']}/messages",
            params={"limit": 3, "before_seq": page1["next_before_seq"]},
            headers=a,
        )
    ).json()
    assert [m["seq"] for m in page2["items"]] == [3, 2, 1]


async def test_message_page_has_no_total(client):
    """Counting an append-only table on every page is the wrong trade."""
    a, _ = await _user(client, "a")
    team = await _team(client, a)
    channel = await _team_channel(client, a, team["id"])
    await _send_ok(client, a, channel["id"], "hi")
    page = await client.get(f"{CHAT}/conversations/{channel['id']}/messages", headers=a)
    assert "total" not in page.json()
    assert set(page.json()) == {"items", "has_more", "next_before_seq"}


async def test_history_limit_is_capped(client):
    a, _ = await _user(client, "a")
    team = await _team(client, a)
    channel = await _team_channel(client, a, team["id"])
    r = await client.get(
        f"{CHAT}/conversations/{channel['id']}/messages",
        params={"limit": 500},
        headers=a,
    )
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# Read state
# ---------------------------------------------------------------------------


async def test_unread_count_tracks_the_read_high_water_mark(client):
    a, _ = await _user(client, "a")
    team = await _team(client, a)
    channel = await _team_channel(client, a, team["id"])
    await _send_ok(client, a, channel["id"], "one")
    await _send_ok(client, a, channel["id"], "two")

    inbox = await client.get(f"{CHAT}/conversations", headers=a)
    assert inbox.json()["items"][0]["unread_count"] == 2

    marked = await client.post(
        f"{CHAT}/conversations/{channel['id']}/read", params={"seq": 1}, headers=a
    )
    assert marked.status_code == 200
    assert marked.json()["last_read_seq"] == 1

    inbox = await client.get(f"{CHAT}/conversations", headers=a)
    assert inbox.json()["items"][0]["unread_count"] == 1


async def test_the_read_mark_never_moves_backwards(client):
    """Two tabs must not be able to re-open already-read messages."""
    a, _ = await _user(client, "a")
    team = await _team(client, a)
    channel = await _team_channel(client, a, team["id"])
    for i in range(4):
        await _send_ok(client, a, channel["id"], f"m{i}")

    await client.post(f"{CHAT}/conversations/{channel['id']}/read", params={"seq": 4}, headers=a)
    stale = await client.post(
        f"{CHAT}/conversations/{channel['id']}/read", params={"seq": 2}, headers=a
    )
    assert stale.json()["last_read_seq"] == 4


async def test_the_read_mark_cannot_run_past_the_last_message(client):
    a, _ = await _user(client, "a")
    team = await _team(client, a)
    channel = await _team_channel(client, a, team["id"])
    await _send_ok(client, a, channel["id"], "only one")
    r = await client.post(
        f"{CHAT}/conversations/{channel['id']}/read", params={"seq": 9999}, headers=a
    )
    assert r.json()["last_read_seq"] == 1


# ---------------------------------------------------------------------------
# Edit and delete
# ---------------------------------------------------------------------------


async def test_author_can_edit_inside_the_window(client):
    a, _ = await _user(client, "a")
    team = await _team(client, a)
    channel = await _team_channel(client, a, team["id"])
    msg = await _send_ok(client, a, channel["id"], "typo")
    r = await client.patch(f"{CHAT}/messages/{msg['id']}", json={"body": "fixed"}, headers=a)
    assert r.status_code == 200
    assert r.json()["body"] == "fixed"
    assert r.json()["is_edited"] is True
    assert r.json()["edited_at"] is not None


async def test_edit_window_closes_after_fifteen_minutes(client, db_session_factory):
    """The window is server-side: a client computing it from its own clock
    would disagree with the server across device timezones. No request can
    legitimately produce this state, so the row is aged directly."""
    from sqlalchemy import text as sql_text

    a, _ = await _user(client, "a")
    team = await _team(client, a)
    channel = await _team_channel(client, a, team["id"])
    msg = await _send_ok(client, a, channel["id"], "typo")

    async with db_session_factory() as db:
        await db.execute(
            sql_text(
                "UPDATE messages SET created_at = created_at - interval '20 minutes'"
                " WHERE id = :mid"
            ),
            {"mid": uuid.UUID(msg["id"])},
        )
        await db.commit()

    r = await client.patch(f"{CHAT}/messages/{msg['id']}", json={"body": "too late"}, headers=a)
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "CHAT_EDIT_WINDOW_CLOSED"


async def test_a_non_author_cannot_edit_or_delete(client):
    a, _ = await _user(client, "a")
    b, bid = await _user(client, "b")
    team = await _team(client, a)
    inv = await client.post(f"{TEAMS}/{team['id']}/invitations", json={"user_id": bid}, headers=a)
    await client.post(f"{TEAMS}/invitations/{inv.json()['id']}/accept", headers=b)
    channel = await _team_channel(client, b, team["id"])
    msg = await _send_ok(client, a, channel["id"], "mine")

    # 404, not 403: a distinct code would confirm the message exists.
    assert (
        await client.patch(f"{CHAT}/messages/{msg['id']}", json={"body": "hijack"}, headers=b)
    ).status_code == 404
    assert (await client.delete(f"{CHAT}/messages/{msg['id']}", headers=b)).status_code == 404


async def test_delete_is_soft_and_hides_the_body(client):
    a, _ = await _user(client, "a")
    team = await _team(client, a)
    channel = await _team_channel(client, a, team["id"])
    msg = await _send_ok(client, a, channel["id"], "regrettable")

    r = await client.delete(f"{CHAT}/messages/{msg['id']}", headers=a)
    assert r.status_code == 200
    assert r.json()["body"] == "[deleted]"
    assert r.json()["is_deleted"] is True

    history = await client.get(f"{CHAT}/conversations/{channel['id']}/messages", headers=a)
    item = next(m for m in history.json()["items"] if m["id"] == msg["id"])
    assert item["body"] == "[deleted]"
    assert item["seq"] == msg["seq"]  # the row and its ordering survive


async def test_a_deleted_message_cannot_be_edited(client):
    a, _ = await _user(client, "a")
    team = await _team(client, a)
    channel = await _team_channel(client, a, team["id"])
    msg = await _send_ok(client, a, channel["id"], "regrettable")
    await client.delete(f"{CHAT}/messages/{msg['id']}", headers=a)
    r = await client.patch(
        f"{CHAT}/messages/{msg['id']}", json={"body": "back from the dead"}, headers=a
    )
    assert r.status_code == 404


async def test_delete_is_idempotent(client):
    a, _ = await _user(client, "a")
    team = await _team(client, a)
    channel = await _team_channel(client, a, team["id"])
    msg = await _send_ok(client, a, channel["id"], "twice")
    first = await client.delete(f"{CHAT}/messages/{msg['id']}", headers=a)
    second = await client.delete(f"{CHAT}/messages/{msg['id']}", headers=a)
    assert first.status_code == second.status_code == 200
    assert second.json()["body"] == "[deleted]"


# ---------------------------------------------------------------------------
# Inbox
# ---------------------------------------------------------------------------


async def test_inbox_lists_only_the_viewers_conversations(client):
    a, _ = await _user(client, "a")
    b, bid = await _user(client, "b")
    stranger, _ = await _user(client, "c")
    dm = await _dm(client, a, bid)
    await _send_ok(client, a, dm["id"], "hello")

    inbox = await client.get(f"{CHAT}/conversations", headers=stranger)
    assert inbox.json()["total"] == 0
    assert inbox.json()["items"] == []

    mine = await client.get(f"{CHAT}/conversations", headers=a)
    assert mine.json()["total"] == 1
    row = mine.json()["items"][0]
    assert row["peer_user_id"] == bid
    assert row["last_message_preview"] == "hello"


async def test_inbox_omits_a_team_channel_the_viewer_lost_standing_for(client):
    """A roster row outlives team membership; the inbox must not trust it."""
    owner, _ = await _user(client, "a")
    b, bid = await _user(client, "b")
    team = await _team(client, owner)
    inv = await client.post(
        f"{TEAMS}/{team['id']}/invitations", json={"user_id": bid}, headers=owner
    )
    await client.post(f"{TEAMS}/invitations/{inv.json()['id']}/accept", headers=b)
    await _team_channel(client, b, team["id"])
    assert (await client.get(f"{CHAT}/conversations", headers=b)).json()["total"] == 1

    await client.delete(f"{TEAMS}/{team['id']}/members/{bid}", headers=owner)
    after = await client.get(f"{CHAT}/conversations", headers=b)
    assert after.json()["items"] == []


async def test_inbox_shows_the_last_message_preview(client):
    a, _ = await _user(client, "a")
    team = await _team(client, a)
    channel = await _team_channel(client, a, team["id"])
    await _send_ok(client, a, channel["id"], "first")
    await _send_ok(client, a, channel["id"], "second")

    row = (await client.get(f"{CHAT}/conversations", headers=a)).json()["items"][0]
    assert row["last_message_preview"] == "second"
    assert row["last_message_seq"] == 2
    assert row["team_id"] == team["id"]


# ---------------------------------------------------------------------------
# Privacy
# ---------------------------------------------------------------------------


async def test_a_message_carries_no_account_private_fields(client):
    """Sender identity comes from the social profile, never from `users`."""
    a, _ = await _user(client, "a")
    team = await _team(client, a)
    channel = await _team_channel(client, a, team["id"])
    msg = await _send_ok(client, a, channel["id"], "hi")

    for field in ("email", "password_hash", "latitude", "longitude", "deleted_at"):
        assert field not in msg
    assert msg["sender_username"]
    assert msg["sender_display_name"]


async def test_unauthenticated_requests_are_refused(client):
    a, _ = await _user(client, "a")
    team = await _team(client, a)
    channel = await _team_channel(client, a, team["id"])
    assert (await client.get(f"{CHAT}/conversations")).status_code == 401
    assert (await client.get(f"{CHAT}/conversations/{channel['id']}/messages")).status_code == 401
    r = await client.post(
        f"{CHAT}/conversations/{channel['id']}/messages",
        json={"body": "hi", "client_message_id": str(uuid.uuid4())},
    )
    assert r.status_code == 401

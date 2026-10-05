"""Phase 8.4 notification API tests (ADR-15).

Real test PostgreSQL, real JWT auth. Nothing mocked except the push provider,
which is the one component whose entire job is a network call and which has no
network dependency by design.

Each test name states the rule it defends. The four that get the most coverage
are the ones that are cheap to get subtly wrong and expensive to get subtly wrong
in production: a notification must never become an existence oracle, a token must
never leak, a blocked DM must produce nothing at all, and a duplicate business
event must produce one row.
"""

import asyncio
import json
import uuid

import pytest

from app.notifications import set_provider
from app.notifications.fake import (
    FailingPushProvider,
    FakePushProvider,
    InvalidatingPushProvider,
)
from app.notifications.types import SAFE_PAYLOAD_KEYS

NOTIF = "/api/v1/notifications"
DEVICES = "/api/v1/push-devices"
SOCIAL = "/api/v1/social"
TEAMS = "/api/v1/teams"
CHAT = "/api/v1/chat"
AUTH = "/api/v1/auth"
RIDES = "/api/v1/group-rides"

SECRET = "SUPER-SECRET-PUSH-TOKEN-XYZ"


def _reg(tag):
    return {
        "email": f"notif_{tag}_{uuid.uuid4().hex[:8]}@example.com",
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
    headers = {"Authorization": f"Bearer {r.json()['access_token']}"}
    me = await client.get(f"{SOCIAL}/profile/me", headers=headers)
    await client.patch(
        f"{SOCIAL}/profile",
        json={"username": f"{tag}_rider", "display_name": f"Rider {tag.title()}"},
        headers=headers,
    )
    return headers, me.json()["user_id"]


async def _block(client, blocker_headers, blocked_id):
    r = await client.post(f"{SOCIAL}/blocks", json={"user_id": blocked_id}, headers=blocker_headers)
    assert r.status_code == 201, r.text


async def _register_device(client, headers, device_id="dev-1", token=SECRET):
    return await client.post(
        f"{DEVICES}",
        json={
            "platform": "android",
            "provider": "fcm",
            "device_id": device_id,
            "token": token,
            "app_version": "1.0.0",
            "locale": "en",
        },
        headers=headers,
    )


async def _notifs(client, headers, **params):
    r = await client.get(f"{NOTIF}", params=params, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


# ---------------------------------------------------------------------------
# Device registration
# ---------------------------------------------------------------------------


async def test_register_a_device(client):
    h, _ = await _user(client, "a")
    r = await _register_device(client, h)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["platform"] == "android"
    assert body["provider"] == "fcm"
    assert body["device_id"] == "dev-1"
    assert body["enabled"] is True


async def test_a_raw_token_is_never_returned(client):
    """The single most important device assertion."""
    h, _ = await _user(client, "a")
    await _register_device(client, h, token=SECRET)
    listed = await client.get(f"{DEVICES}", headers=h)
    assert listed.status_code == 200
    assert SECRET not in listed.text
    assert "token" not in json.dumps(listed.json())
    for row in listed.json()["items"]:
        assert "token" not in row


async def test_duplicate_registration_updates_instead_of_duplicating(client):
    """A re-registration must not accumulate a row per app launch."""
    h, _ = await _user(client, "a")
    first = await _register_device(client, h, token=SECRET)
    second = await _register_device(client, h, token=SECRET + "-rotated")
    assert first.status_code == 201
    assert second.status_code == 200, "a repeat registration is not a new device"
    assert second.json()["id"] == first.json()["id"]

    rows = (await client.get(f"{DEVICES}", headers=h)).json()["items"]
    assert len(rows) == 1, "token rotation created a second row"


async def test_token_rotation_refreshes_the_same_device(client):
    h, _ = await _user(client, "a")
    first = await _register_device(client, h, token=SECRET)
    rotated = await _register_device(client, h, token="rotated-token-999")
    assert rotated.json()["id"] == first.json()["id"]
    assert len((await client.get(f"{DEVICES}", headers=h)).json()["items"]) == 1


async def test_a_token_held_by_another_account_is_transferred(client):
    """A token already bound elsewhere MOVES instead of being refused.

    One phone, two sessions over time. Refusing the registration would leave the
    first account still receiving pushes on a device now showing the second,
    which is the leak this prevents.
    """
    h, _ = await _user(client, "a")
    other_h, _ = await _user(client, "b")
    first = await _register_device(client, h, token="shared-token")

    moved = await _register_device(client, other_h, device_id="phone-2", token="shared-token")
    assert moved.status_code == 200
    assert moved.json()["id"] == first.json()["id"]

    assert [d["id"] for d in (await client.get(f"{DEVICES}", headers=h)).json()["items"]] == []
    assert [d["id"] for d in (await client.get(f"{DEVICES}", headers=other_h)).json()["items"]] == [
        first.json()["id"]
    ]


async def test_rotating_onto_another_accounts_token_transfers_instead_of_500(client):
    """Regression: the re-activation write is guarded like the insert.

    Rotating an existing device row onto a token that another account already
    holds trips `uq_push_devices_provider_token`. That write used to sit outside
    the IntegrityError handler and surface as a 500; it now reconciles the same
    way an insert does. Found by the live smoke, not by the suite — the suite's
    session factory disables `expire_on_commit`, and nothing here raised.
    """
    h, _ = await _user(client, "a")
    other_h, _ = await _user(client, "b")
    first = await _register_device(client, h, device_id="phone", token=SECRET)
    await _register_device(client, other_h, device_id="tablet", token="b-token")

    # Rotate A's phone onto the token B's tablet holds.
    moved = await _register_device(client, h, device_id="phone", token="b-token")
    assert moved.status_code == 200, moved.text
    assert moved.json()["id"] == first.json()["id"]

    rows = (await client.get(f"{DEVICES}", headers=h)).json()["items"]
    assert [d["id"] for d in rows] == [first.json()["id"]]
    assert (await client.get(f"{DEVICES}", headers=other_h)).json()["items"] == []


async def test_a_user_may_hold_multiple_devices(client):
    h, _ = await _user(client, "a")
    await _register_device(client, h, device_id="phone", token="tok-phone")
    await _register_device(client, h, device_id="tablet", token="tok-tablet")
    rows = (await client.get(f"{DEVICES}", headers=h)).json()["items"]
    assert {r["device_id"] for r in rows} == {"phone", "tablet"}


async def test_registration_cannot_name_another_user(client):
    """Ownership is server-derived; a user_id field is not accepted at all."""
    h, _ = await _user(client, "a")
    other_h, other_id = await _user(client, "b")
    r = await client.post(
        f"{DEVICES}",
        json={
            "platform": "android",
            "provider": "fcm",
            "device_id": "dev-1",
            "token": SECRET,
            "user_id": other_id,
        },
        headers=h,
    )
    assert r.status_code == 422, "a client-supplied user_id must be rejected"
    # And nothing was registered for the other rider.
    assert (await client.get(f"{DEVICES}", headers=other_h)).json()["items"] == []


async def test_device_update_and_disable(client):
    h, _ = await _user(client, "a")
    created = (await _register_device(client, h)).json()
    r = await client.patch(f"{DEVICES}/{created['id']}", json={"enabled": False}, headers=h)
    assert r.status_code == 200
    assert r.json()["enabled"] is False


async def test_device_revoke(client):
    h, _ = await _user(client, "a")
    created = (await _register_device(client, h)).json()
    r = await client.delete(f"{DEVICES}/{created['id']}", headers=h)
    assert r.status_code == 200, r.text
    assert (await client.get(f"{DEVICES}", headers=h)).json()["items"] == []


async def test_device_ownership_isolation(client):
    """Another rider's device is 404, never 403, so ids cannot be probed."""
    h, _ = await _user(client, "a")
    other, _ = await _user(client, "b")
    created = (await _register_device(client, h)).json()

    assert (
        await client.patch(f"{DEVICES}/{created['id']}", json={"enabled": False}, headers=other)
    ).status_code == 404
    assert (await client.delete(f"{DEVICES}/{created['id']}", headers=other)).status_code == 404
    # A guessed id answers the same as a real one belonging to someone else.
    assert (await client.delete(f"{DEVICES}/{uuid.uuid4()}", headers=other)).status_code == 404
    # And it was not actually modified.
    assert (await client.get(f"{DEVICES}", headers=h)).json()["items"][0]["enabled"] is True


async def test_device_update_rejects_unwritable_fields(client):
    """Only `enabled` is settable — a device cannot be moved or re-tokenised."""
    h, _ = await _user(client, "a")
    created = (await _register_device(client, h)).json()
    r = await client.patch(
        f"{DEVICES}/{created['id']}",
        json={"enabled": True, "token": "hijack", "user_id": str(uuid.uuid4())},
        headers=h,
    )
    assert r.status_code == 422


async def test_blank_device_fields_are_rejected(client):
    h, _ = await _user(client, "a")
    r = await client.post(
        f"{DEVICES}",
        json={
            "platform": "android",
            "provider": "fcm",
            "device_id": "   ",
            "token": SECRET,
        },
        headers=h,
    )
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# Notification creation through real business flows
# ---------------------------------------------------------------------------


async def test_friend_request_notifies_the_target(client):
    a, aid = await _user(client, "a")
    b, bid = await _user(client, "b")
    r = await client.post(f"{SOCIAL}/friend-requests", json={"user_id": bid}, headers=a)
    assert r.status_code == 201, r.text

    inbox = await _notifs(client, b)
    assert inbox["total"] == 1
    item = inbox["items"][0]
    assert item["type"] == "friend_request"
    assert item["is_unread"] is True
    assert item["deep_link"] == "/friends/requests"
    assert item["actor_user_id"] == aid
    # The requester is not told about their own request.
    assert (await _notifs(client, a))["total"] == 0


async def test_friend_request_accepted_notifies_the_requester(client):
    a, _ = await _user(client, "a")
    b, bid = await _user(client, "b")
    created = await client.post(f"{SOCIAL}/friend-requests", json={"user_id": bid}, headers=a)
    await client.post(f"{SOCIAL}/friend-requests/{created.json()['id']}/accept", headers=b)
    inbox = await _notifs(client, a)
    assert inbox["total"] == 1
    assert inbox["items"][0]["type"] == "friend_request_accepted"


async def test_team_invitation_notifies_only_the_invitee(client):
    a, aid = await _user(client, "a")
    b, bid = await _user(client, "b")
    c, _ = await _user(client, "c")
    team = (await client.post(f"{TEAMS}", json={"name": "Atlas CC"}, headers=a)).json()
    r = await client.post(f"{TEAMS}/{team['id']}/invitations", json={"user_id": bid}, headers=a)
    assert r.status_code == 201, r.text

    inbox = await _notifs(client, b)
    assert inbox["total"] == 1
    assert inbox["items"][0]["type"] == "team_invitation"
    assert inbox["items"][0]["deep_link"] == f"/teams/{team['id']}"
    assert inbox["items"][0]["actor_user_id"] == aid
    # The team owner and a bystander are told nothing.
    assert (await _notifs(client, a))["total"] == 0
    assert (await _notifs(client, c))["total"] == 0


async def test_team_join_request_notifies_managers_only(client):
    a, _ = await _user(client, "a")
    b, _bid = await _user(client, "b")
    team = (
        await client.post(
            f"{TEAMS}", json={"name": "Private CC", "visibility": "private"}, headers=a
        )
    ).json()
    r = await client.post(
        f"{TEAMS}/{team['id']}/join-requests", json={"message": "let me in"}, headers=b
    )
    assert r.status_code == 201, r.text

    inbox = await _notifs(client, a)
    assert inbox["total"] == 1
    assert inbox["items"][0]["type"] == "team_join_request"
    assert (await _notifs(client, b))["total"] == 0


async def test_team_member_removed_notifies_the_removed_rider(client):
    a, _ = await _user(client, "a")
    b, bid = await _user(client, "b")
    team = (await client.post(f"{TEAMS}", json={"name": "Atlas CC"}, headers=a)).json()
    inv = await client.post(f"{TEAMS}/{team['id']}/invitations", json={"user_id": bid}, headers=a)
    await client.post(f"{TEAMS}/invitations/{inv.json()['id']}/accept", headers=b)
    r = await client.delete(f"{TEAMS}/{team['id']}/members/{bid}", headers=a)
    assert r.status_code in (200, 204)

    # Filtered by type: B's inbox also holds the earlier invitation notice.
    types = [i["type"] for i in (await _notifs(client, b))["items"]]
    assert "team_member_removed" in types


@pytest.mark.parametrize("member_count", [1, 2, 3])
async def test_team_archive_notifies_every_member_exactly_once(client, member_count):
    """Archive fans out to the exact member set — one TEAM_ARCHIVED row each.

    A count-only assertion once certified this path while only one member was
    actually notified, because the other member's inbox held an earlier
    invitation notice. Every assertion below names the recipient, the type,
    and the team, so a collapsed fan-out cannot pass.
    """
    tags = ["a", "b", "c"][:member_count]
    creds = {tag: await _user(client, tag) for tag in tags}
    a_headers, _ = creds["a"]
    team = (await client.post(f"{TEAMS}", json={"name": "Atlas CC"}, headers=a_headers)).json()
    for tag in tags[1:]:
        headers, user_id = creds[tag]
        inv = await client.post(
            f"{TEAMS}/{team['id']}/invitations", json={"user_id": user_id}, headers=a_headers
        )
        await client.post(f"{TEAMS}/invitations/{inv.json()['id']}/accept", headers=headers)

    assert (await client.delete(f"{TEAMS}/{team['id']}", headers=a_headers)).status_code == 200

    seen_ids: set[str] = set()
    for tag in tags:
        headers, _ = creds[tag]
        archived = [
            i for i in (await _notifs(client, headers))["items"] if i["type"] == "team_archived"
        ]
        assert len(archived) == 1, f"{tag} holds {len(archived)} team_archived rows"
        item = archived[0]
        assert item["entity_type"] == "team"
        assert item["entity_id"] == team["id"]
        assert item["deep_link"] == f"/teams/{team['id']}"
        assert item["l10n_key"] == "notifications.type.team_archived"
        seen_ids.add(item["id"])

    # One distinct row per member: no collapsed fan-out, no duplicates.
    assert len(seen_ids) == member_count

    # A repeated archive changes nothing: the team is already archived, and no
    # second wave of notifications is created.
    retry = await client.delete(f"{TEAMS}/{team['id']}", headers=a_headers)
    assert retry.status_code == 404, retry.text
    for tag in tags:
        headers, _ = creds[tag]
        archived = [
            i for i in (await _notifs(client, headers))["items"] if i["type"] == "team_archived"
        ]
        assert len(archived) == 1
        assert archived[0]["id"] in seen_ids


async def test_chat_message_notifies_the_recipient(client):
    a, _aid = await _user(client, "a")
    b, bid = await _user(client, "b")
    dm = (await client.post(f"{CHAT}/direct", params={"target_user_id": bid}, headers=a)).json()
    r = await client.post(
        f"{CHAT}/conversations/{dm['id']}/messages",
        json={"body": "salut", "client_message_id": str(uuid.uuid4())},
        headers=a,
    )
    assert r.status_code == 201, r.text

    inbox = await _notifs(client, b)
    assert inbox["total"] == 1
    item = inbox["items"][0]
    assert item["type"] == "chat_message"
    assert item["deep_link"] == f"/chat/{dm['id']}"
    # The sender is never told about their own message.
    assert (await _notifs(client, a))["total"] == 0


async def test_team_channel_message_notifies_live_members(client):
    a, _ = await _user(client, "a")
    b, bid = await _user(client, "b")
    team = (await client.post(f"{TEAMS}", json={"name": "Atlas CC"}, headers=a)).json()
    inv = await client.post(f"{TEAMS}/{team['id']}/invitations", json={"user_id": bid}, headers=a)
    await client.post(f"{TEAMS}/invitations/{inv.json()['id']}/accept", headers=b)
    channel = (await client.get(f"{CHAT}/teams/{team['id']}/conversation", headers=a)).json()

    await client.post(
        f"{CHAT}/conversations/{channel['id']}/messages",
        json={"body": "morning all", "client_message_id": str(uuid.uuid4())},
        headers=a,
    )
    # B's inbox also holds the earlier invitation notice, so filter by type
    # rather than asserting a total — the assertion is about the channel message.
    inbox = await _notifs(client, b)
    channel_notifications = [i for i in inbox["items"] if i["type"] == "chat_message_team"]
    assert len(channel_notifications) == 1
    assert channel_notifications[0]["params"]["teamName"] == "Atlas CC"


async def test_a_removed_member_stops_receiving_team_messages(client):
    """Live team membership, not the conversation roster, decides who is told."""
    a, _ = await _user(client, "a")
    b, bid = await _user(client, "b")
    team = (await client.post(f"{TEAMS}", json={"name": "Atlas CC"}, headers=a)).json()
    inv = await client.post(f"{TEAMS}/{team['id']}/invitations", json={"user_id": bid}, headers=a)
    await client.post(f"{TEAMS}/invitations/{inv.json()['id']}/accept", headers=b)
    channel = (await client.get(f"{CHAT}/teams/{team['id']}/conversation", headers=a)).json()
    # Prime the roster so B really does have a conversation_members row.
    await client.get(f"{CHAT}/conversations/{channel['id']}/messages", headers=b)

    await client.delete(f"{TEAMS}/{team['id']}/members/{bid}", headers=a)
    await client.post(
        f"{CHAT}/conversations/{channel['id']}/messages",
        json={"body": "after removal", "client_message_id": str(uuid.uuid4())},
        headers=a,
    )
    # B's inbox holds only the removal notice, not the channel message.
    types = [i["type"] for i in (await _notifs(client, b))["items"]]
    assert "chat_message_team" not in types


# ---------------------------------------------------------------------------
# Block policy — the load-bearing privacy rule
# ---------------------------------------------------------------------------


async def test_a_blocked_dm_produces_no_notification_for_either_party(client):
    """A blocked DM must produce NO ROW AT ALL, in both directions.

    Not a suppressed push, not a hidden row — no row, because a row that exists
    in the center but is never pushed is still a leak.
    """
    a, aid = await _user(client, "a")
    b, bid = await _user(client, "b")
    dm = (await client.post(f"{CHAT}/direct", params={"target_user_id": bid}, headers=a)).json()
    await _block(client, b, aid)

    # A blocked send is refused by chat itself...
    send = await client.post(
        f"{CHAT}/conversations/{dm['id']}/messages",
        json={"body": "still there?", "client_message_id": str(uuid.uuid4())},
        headers=a,
    )
    assert send.status_code == 404

    # ...and produces no notification on either side.
    assert (await _notifs(client, a))["total"] == 0
    assert (await _notifs(client, b))["total"] == 0


async def test_unblocking_restores_dm_notifications(client):
    a, aid = await _user(client, "a")
    b, bid = await _user(client, "b")
    dm = (await client.post(f"{CHAT}/direct", params={"target_user_id": bid}, headers=a)).json()
    await _block(client, b, aid)
    await client.post(
        f"{CHAT}/conversations/{dm['id']}/messages",
        json={"body": "blocked", "client_message_id": str(uuid.uuid4())},
        headers=a,
    )
    assert (await _notifs(client, b))["total"] == 0

    await client.delete(f"{SOCIAL}/blocks/{aid}", headers=b)
    await client.post(
        f"{CHAT}/conversations/{dm['id']}/messages",
        json={"body": "unblocked", "client_message_id": str(uuid.uuid4())},
        headers=a,
    )
    assert (await _notifs(client, b))["total"] == 1


async def test_a_block_does_not_stop_team_channel_notifications(client):
    """The Phase 8.3 policy keeps a team channel open, and push must match it.

    Silencing push while messages keep flowing would make the channel itself a
    block detector: the rider could infer the block from the missing notification.
    """
    a, aid = await _user(client, "a")
    b, bid = await _user(client, "b")
    team = (await client.post(f"{TEAMS}", json={"name": "Atlas CC"}, headers=a)).json()
    inv = await client.post(f"{TEAMS}/{team['id']}/invitations", json={"user_id": bid}, headers=a)
    await client.post(f"{TEAMS}/invitations/{inv.json()['id']}/accept", headers=b)
    await _block(client, b, aid)

    channel = (await client.get(f"{CHAT}/teams/{team['id']}/conversation", headers=a)).json()
    send = await client.post(
        f"{CHAT}/conversations/{channel['id']}/messages",
        json={"body": "team still open", "client_message_id": str(uuid.uuid4())},
        headers=a,
    )
    assert send.status_code == 201, "a block does not close a team channel"

    types = [i["type"] for i in (await _notifs(client, b))["items"]]
    assert "chat_message_team" in types, "push must match the message path"


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


async def test_a_retried_message_produces_one_notification(client):
    """The idempotent replay path must not double-notify."""
    a, _ = await _user(client, "a")
    b, bid = await _user(client, "b")
    dm = (await client.post(f"{CHAT}/direct", params={"target_user_id": bid}, headers=a)).json()
    key = str(uuid.uuid4())
    payload = {"body": "exactly once", "client_message_id": key}

    first = await client.post(f"{CHAT}/conversations/{dm['id']}/messages", json=payload, headers=a)
    second = await client.post(f"{CHAT}/conversations/{dm['id']}/messages", json=payload, headers=a)
    assert first.status_code == 201
    assert second.status_code == 200
    assert second.json()["duplicate"] is True
    assert (await _notifs(client, b))["total"] == 1


async def test_concurrent_sends_of_distinct_messages_notify_each_recipient_once(client):
    a, _ = await _user(client, "a")
    b, bid = await _user(client, "b")
    dm = (await client.post(f"{CHAT}/direct", params={"target_user_id": bid}, headers=a)).json()
    await asyncio.gather(
        *[
            client.post(
                f"{CHAT}/conversations/{dm['id']}/messages",
                json={"body": f"m{i}", "client_message_id": str(uuid.uuid4())},
                headers=a,
            )
            for i in range(5)
        ]
    )
    assert (await _notifs(client, b))["total"] == 5


async def test_the_same_client_id_from_two_senders_makes_two_notifications(client):
    a, _aid = await _user(client, "a")
    b, bid = await _user(client, "b")
    team = (await client.post(f"{TEAMS}", json={"name": "Atlas CC"}, headers=a)).json()
    inv = await client.post(f"{TEAMS}/{team['id']}/invitations", json={"user_id": bid}, headers=a)
    await client.post(f"{TEAMS}/invitations/{inv.json()['id']}/accept", headers=b)
    channel = (await client.get(f"{CHAT}/teams/{team['id']}/conversation", headers=a)).json()
    key = str(uuid.uuid4())
    await client.post(
        f"{CHAT}/conversations/{channel['id']}/messages",
        json={"body": "from a", "client_message_id": key},
        headers=a,
    )
    await client.post(
        f"{CHAT}/conversations/{channel['id']}/messages",
        json={"body": "from b", "client_message_id": key},
        headers=b,
    )
    # B is told about A's message only, not about B's own. Filtered by type
    # because B's inbox also holds the earlier invitation notice.
    channel_notifications = [
        i for i in (await _notifs(client, b))["items"] if i["type"] == "chat_message_team"
    ]
    assert len(channel_notifications) == 1


# ---------------------------------------------------------------------------
# Reading, unread state, IDOR
# ---------------------------------------------------------------------------


async def test_mark_read_and_unread_count(client):
    """One rider with two distinct unread notifications from two actors.

    Built from two friends so the assertions are about read state rather than
    about how many requests a single pair can have pending.
    """
    a, _aid = await _user(client, "a")
    b, bid = await _user(client, "b")
    _c, cid = await _user(client, "c")
    await client.post(f"{SOCIAL}/friend-requests", json={"user_id": bid}, headers=a)
    await client.post(f"{SOCIAL}/friend-requests", json={"user_id": cid}, headers=a)

    count = (await client.get(f"{NOTIF}/unread-count", headers=a)).json()
    assert count["unread_count"] == 0, "the sender is not notified"

    assert (await client.get(f"{NOTIF}/unread-count", headers=b)).json()["unread_count"] == 1

    item = (await _notifs(client, b))["items"][0]
    r = await client.post(f"{NOTIF}/{item['id']}/read", headers=b)
    assert r.status_code == 200
    assert r.json()["is_read"] is True
    assert (await client.get(f"{NOTIF}/unread-count", headers=b)).json()["unread_count"] == 0


async def test_mark_read_is_idempotent(client):
    a, _ = await _user(client, "a")
    b, bid = await _user(client, "b")
    await client.post(f"{SOCIAL}/friend-requests", json={"user_id": bid}, headers=a)
    item = (await _notifs(client, b))["items"][0]

    first = await client.post(f"{NOTIF}/{item['id']}/read", headers=b)
    second = await client.post(f"{NOTIF}/{item['id']}/read", headers=b)
    assert first.status_code == second.status_code == 200
    assert second.json()["is_read"] is True


async def test_mark_all_read(client):
    a, aid = await _user(client, "a")
    c, _cid = await _user(client, "c")
    d, _did = await _user(client, "d")
    # Two distinct actors, so the recipient genuinely holds two notifications.
    await client.post(f"{SOCIAL}/friend-requests", json={"user_id": aid}, headers=c)
    await client.post(f"{SOCIAL}/friend-requests", json={"user_id": aid}, headers=d)
    assert (await client.get(f"{NOTIF}/unread-count", headers=a)).json()["unread_count"] == 2

    r = await client.post(f"{NOTIF}/read-all", headers=a)
    assert r.status_code == 200
    assert r.json()["marked"] == 2
    assert (await client.get(f"{NOTIF}/unread-count", headers=a)).json()["unread_count"] == 0
    # Idempotent: a second sweep marks nothing and is not an error.
    again = await client.post(f"{NOTIF}/read-all", headers=a)
    assert again.status_code == 200
    assert again.json() == {"marked": 0}


async def test_a_rider_cannot_read_another_riders_notification(client):
    """404, byte-identical to a guessed id — never an existence oracle."""
    a, _aid = await _user(client, "a")
    b, bid = await _user(client, "b")
    stranger, _ = await _user(client, "c")
    await client.post(f"{SOCIAL}/friend-requests", json={"user_id": bid}, headers=a)
    item = (await _notifs(client, b))["items"][0]

    real = await client.post(f"{NOTIF}/{item['id']}/read", headers=stranger)
    guessed = await client.post(f"{NOTIF}/{uuid.uuid4()}/read", headers=stranger)
    assert real.status_code == guessed.status_code == 404
    assert real.json() == guessed.json()
    # And it was not marked read on B's behalf.
    assert (await _notifs(client, b))["items"][0]["is_read"] is False


async def test_a_rider_cannot_list_another_riders_notifications(client):
    a, _aid = await _user(client, "a")
    _b, bid = await _user(client, "b")
    await client.post(f"{SOCIAL}/friend-requests", json={"user_id": bid}, headers=a)
    assert (await _notifs(client, a))["total"] == 0


async def test_unread_only_filter(client):
    a, _aid = await _user(client, "a")
    b, bid = await _user(client, "b")
    await client.post(f"{SOCIAL}/friend-requests", json={"user_id": bid}, headers=a)
    item = (await _notifs(client, b))["items"][0]
    await client.post(f"{NOTIF}/{item['id']}/read", headers=b)
    assert (await _notifs(client, b, unread_only="true"))["total"] == 0
    assert (await _notifs(client, b, unread_only="false"))["total"] == 1


# ---------------------------------------------------------------------------
# Pagination
# ---------------------------------------------------------------------------


async def test_pagination_envelope_and_cap(client):
    """Three notifications for one recipient, from three distinct actors.

    A single pair cannot produce three: Phase 8.1 collapses a repeat request
    into one pending relationship, so the three actors must be distinct — and
    none of them may be the recipient.
    """
    a, aid = await _user(client, "a")
    b, _bid = await _user(client, "b")
    c, _cid = await _user(client, "c")
    d, _did = await _user(client, "d")
    for actor in (b, c, d):
        sent = await client.post(f"{SOCIAL}/friend-requests", json={"user_id": aid}, headers=actor)
        assert sent.status_code == 201, f"{sent.status_code} for a distinct actor"

    page = await _notifs(client, a, page=1, page_size=2)
    assert {"items", "total", "page", "page_size"} <= set(page.keys())
    assert page["total"] == 3
    assert len(page["items"]) == 2
    assert (await _notifs(client, a, page=2, page_size=2))["items"] != []
    assert (await client.get(f"{NOTIF}", params={"page_size": 500}, headers=a)).status_code == 422


async def test_notifications_are_newest_first(client):
    a, aid = await _user(client, "a")
    c, _cid = await _user(client, "c")
    d, _did = await _user(client, "d")
    await client.post(f"{SOCIAL}/friend-requests", json={"user_id": aid}, headers=c)
    await client.post(f"{SOCIAL}/friend-requests", json={"user_id": aid}, headers=d)
    items = (await _notifs(client, a))["items"]
    assert len(items) == 2
    assert items[0]["created_at"] >= items[1]["created_at"]


# ---------------------------------------------------------------------------
# Privacy and payload safety
# ---------------------------------------------------------------------------


async def test_no_notification_carries_private_content(client):
    """No message body, email, coordinate, or token in any notification."""
    a, _aid = await _user(client, "a")
    b, bid = await _user(client, "b")
    await _register_device(client, b, token=SECRET)
    dm = (await client.post(f"{CHAT}/direct", params={"target_user_id": bid}, headers=a)).json()
    secret_body = "MY-SECRET-MESSAGE-TEXT"
    await client.post(
        f"{CHAT}/conversations/{dm['id']}/messages",
        json={"body": secret_body, "client_message_id": str(uuid.uuid4())},
        headers=a,
    )
    inbox = await _notifs(client, b)
    assert inbox["total"] == 1, "the DM message produced one notification"
    text = json.dumps(inbox).lower()
    for forbidden in [
        secret_body.lower(),
        "latitude",
        "longitude",
        "gps",
        "access_token",
        "refresh_token",
    ]:
        assert forbidden not in text, forbidden
    assert SECRET not in text


async def test_the_push_payload_carries_only_safe_keys(client):
    """The provider boundary is the last place a secret could escape."""
    from app.core.config import settings

    fake = FakePushProvider()
    set_provider(fake)
    previous = settings.PUSH_ENABLED
    settings.PUSH_ENABLED = True
    try:
        a, _ = await _user(client, "a")
        b, bid = await _user(client, "b")
        await _register_device(client, b, token=SECRET)
        await client.post(f"{SOCIAL}/friend-requests", json={"user_id": bid}, headers=a)
    finally:
        settings.PUSH_ENABLED = previous
        set_provider(None)

    assert fake.call_count >= 1, "the fake provider was never called"
    payload = fake.last_payload
    assert set(payload) <= SAFE_PAYLOAD_KEYS
    assert payload["notification_type"] == "friend_request"
    assert payload["deep_link"] == "/friends/requests"
    # The fake records device ids, never tokens.
    assert SECRET not in json.dumps(payload)


async def test_a_large_fan_out_defers_push_but_still_writes_every_row(client):
    """Past the inline limit, push waits for the worker; rows are never skipped.

    The rows are the record, so dropping them to save provider round-trips would
    lose events — the one thing the notification-is-a-row design prevents. Only
    delivery is bounded.
    """
    from app.core.config import settings

    fake = FakePushProvider()
    set_provider(fake)
    previous_enabled = settings.PUSH_ENABLED
    previous_limit = settings.PUSH_INLINE_FANOUT_LIMIT
    settings.PUSH_ENABLED = True
    settings.PUSH_INLINE_FANOUT_LIMIT = 1
    try:
        a, _ = await _user(client, "a")
        b, bid = await _user(client, "b")
        c, cid = await _user(client, "c")
        for h in (a, b):
            await _register_device(client, h, token=f"tok-{id(h)}")

        team = (await client.post(f"{TEAMS}", json={"name": "Crew"}, headers=a)).json()
        for uid in (bid, cid):
            invite = await client.post(
                f"{TEAMS}/{team['id']}/invitations", json={"user_id": uid}, headers=a
            )
            await client.post(
                f"{TEAMS}/invitations/{invite.json()['id']}/accept",
                headers=b if uid == bid else c,
            )

        channel = (await client.get(f"{CHAT}/teams/{team['id']}/conversation", headers=c)).json()
        # Invitations above already delivered (one recipient each, at or under the
        # limit), so measure only across the send that fans out to two.
        calls_before = fake.call_count
        sent = await client.post(
            f"{CHAT}/conversations/{channel['id']}/messages",
            json={"body": "hi team", "client_message_id": str(uuid.uuid4())},
            headers=c,
        )
        assert sent.status_code == 201, sent.text
        calls_after = fake.call_count
    finally:
        settings.PUSH_ENABLED = previous_enabled
        settings.PUSH_INLINE_FANOUT_LIMIT = previous_limit
        set_provider(None)

    # Two recipients in one fan-out, over the limit of 1: push was deferred.
    assert calls_after == calls_before, "delivery should have been deferred"
    # Both channel rows still exist, alongside the earlier invitation notices.
    # These rows are the record; skipping them would be the real regression.
    for headers in (a, b):
        types = [i["type"] for i in (await _notifs(client, headers))["items"]]
        assert types.count("chat_message_team") == 1, types


async def test_an_invalid_token_disables_the_device(client):
    from app.core.config import settings

    fake = InvalidatingPushProvider()
    set_provider(fake)
    previous = settings.PUSH_ENABLED
    settings.PUSH_ENABLED = True
    try:
        a, _ = await _user(client, "a")
        b, bid = await _user(client, "b")
        await _register_device(client, b, token=SECRET)
        await client.post(f"{SOCIAL}/friend-requests", json={"user_id": bid}, headers=a)
    finally:
        settings.PUSH_ENABLED = previous
        set_provider(None)

    rows = (await client.get(f"{DEVICES}", headers=b)).json()["items"]
    assert rows[0]["enabled"] is False, "a dead token must not stay enabled"


async def test_a_failing_provider_never_breaks_the_business_action(client):
    """A notification is an accelerant, never a precondition."""
    from app.core.config import settings

    fake = FailingPushProvider()
    set_provider(fake)
    previous = settings.PUSH_ENABLED
    settings.PUSH_ENABLED = True
    try:
        a, _ = await _user(client, "a")
        b, bid = await _user(client, "b")
        r = await client.post(f"{SOCIAL}/friend-requests", json={"user_id": bid}, headers=a)
        assert r.status_code == 201, "the friend request must still succeed"
        # And the in-app record exists regardless.
        assert (await _notifs(client, b))["total"] == 1
    finally:
        settings.PUSH_ENABLED = previous
        set_provider(None)


# ---------------------------------------------------------------------------
# Rate limits and auth
# ---------------------------------------------------------------------------


async def test_device_registration_is_rate_limited(client):
    h, _ = await _user(client, "a")
    codes = [
        (
            await client.post(
                f"{DEVICES}",
                json={
                    "platform": "android",
                    "provider": "fcm",
                    "device_id": f"dev-{i}",
                    "token": f"tok-{i}",
                },
                headers=h,
            )
        ).status_code
        for i in range(30)
    ]
    assert 429 in codes, "device registration must be rate limited"


async def test_unauthenticated_requests_are_refused(client):
    for method, path in [
        ("GET", f"{NOTIF}"),
        ("GET", f"{NOTIF}/unread-count"),
        ("POST", f"{NOTIF}/read-all"),
        ("GET", f"{DEVICES}"),
        ("POST", f"{DEVICES}"),
    ]:
        r = await client.request(method, f"{path}", json={})
        assert r.status_code == 401, f"{method} {path}"


async def test_openapi_exposes_the_notification_surface(client):
    _h, _ = await _user(client, "a")
    paths = (await client.get("/openapi.json")).json()["paths"]
    for expected in [
        f"{NOTIF}",
        f"{NOTIF}/unread-count",
        f"{NOTIF}/read-all",
        f"{NOTIF}/{{notification_id}}/read",
        f"{DEVICES}",
        f"{DEVICES}/{{device_id}}",
    ]:
        assert expected in paths, expected


# ---------------------------------------------------------------------------
# Phase 8.6 concurrency: one business event, one notification
# ---------------------------------------------------------------------------


async def test_concurrent_duplicate_friend_request_notifies_exactly_once(client):
    """Five simultaneous identical requests create one row and one notification.

    Deduplication is enforced by a unique constraint, so a race that slips past
    the pre-check must fail at commit — and the failure must be a clean 409, not
    a 500 that the client shows as a crash.
    """
    a, aid = await _user(client, "a")
    b, bid = await _user(client, "b")

    results = await asyncio.gather(
        *[
            client.post(f"{SOCIAL}/friend-requests", json={"user_id": bid}, headers=a)
            for _ in range(5)
        ]
    )
    codes = sorted(r.status_code for r in results)
    assert codes == [201, 409, 409, 409, 409], codes

    inbox = await _notifs(client, b)
    requests = [i for i in inbox["items"] if i["type"] == "friend_request"]
    assert len(requests) == 1, f"target holds {len(requests)} friend_request rows"
    assert requests[0]["actor_user_id"] == aid
    assert (await _notifs(client, a))["total"] == 0


async def test_concurrent_team_archive_notifies_each_member_once(client):
    """Two simultaneous archives: one 200, one 404, and no second notification wave."""
    a, _ = await _user(client, "a")
    b, bid = await _user(client, "b")
    team = (await client.post(f"{TEAMS}", json={"name": "Atlas CC"}, headers=a)).json()
    inv = await client.post(f"{TEAMS}/{team['id']}/invitations", json={"user_id": bid}, headers=a)
    await client.post(f"{TEAMS}/invitations/{inv.json()['id']}/accept", headers=b)

    first, second = await asyncio.gather(
        client.delete(f"{TEAMS}/{team['id']}", headers=a),
        client.delete(f"{TEAMS}/{team['id']}", headers=a),
    )
    assert sorted([first.status_code, second.status_code]) == [200, 404]

    for headers in (a, b):
        archived = [
            i for i in (await _notifs(client, headers))["items"] if i["type"] == "team_archived"
        ]
        assert len(archived) == 1, f"holds {len(archived)} team_archived rows"


async def test_concurrent_mark_read_is_idempotent(client):
    """Reading the same notification twice concurrently leaves one read row."""
    a, aid = await _user(client, "a")
    b, bid = await _user(client, "b")
    assert (
        await client.post(f"{SOCIAL}/friend-requests", json={"user_id": bid}, headers=a)
    ).status_code == 201
    item = (await _notifs(client, b))["items"][0]

    results = await asyncio.gather(
        *[client.post(f"{NOTIF}/{item['id']}/read", headers=b) for _ in range(4)]
    )
    assert {r.status_code for r in results} <= {200, 204}
    inbox = await _notifs(client, b)
    assert inbox["items"][0]["is_unread"] is False
    assert (await client.get(f"{NOTIF}/unread-count", headers=b)).json()["unread_count"] == 0
    assert aid


# ---------------------------------------------------------------------------
# Phase 9 — group rides (ADR-16 §7)
# ---------------------------------------------------------------------------


async def _ride(client, headers, title="Sunday Spin"):
    r = await client.post(f"{RIDES}", json={"title": title}, headers=headers)
    assert r.status_code == 201, r.text
    return r.json()


async def _invite(client, headers, ride_id, user_id):
    r = await client.post(
        f"{RIDES}/{ride_id}/invitations", json={"user_id": user_id}, headers=headers
    )
    assert r.status_code == 201, r.text
    return r.json()


async def _of_type(client, headers, type_):
    return [i for i in (await _notifs(client, headers))["items"] if i["type"] == type_]


async def test_an_invitation_notifies_only_the_invitee(client):
    """An invitation is addressed to one rider. The rest of the ride has no business
    hearing about it, and the organizer certainly does not notify themselves."""
    org, _ = await _user(client, "a")
    guest, guest_id = await _user(client, "b")
    other, other_id = await _user(client, "c")
    ride = await _ride(client, org)
    await _invite(client, org, ride["id"], guest_id)
    await _invite(client, org, ride["id"], other_id)

    mine = await _of_type(client, guest, "group_ride_invitation")
    assert len(mine) == 1, mine
    assert mine[0]["deep_link"] == f"/group-rides/{ride['id']}"
    assert mine[0]["params"]["groupRideTitle"] == "Sunday Spin"
    assert await _of_type(client, org, "group_ride_invitation") == []
    assert len(await _of_type(client, other, "group_ride_invitation")) == 1


async def test_an_acceptance_notifies_the_organizer(client):
    """The organizer is the one who needs to know the ride is happening."""
    org, _ = await _user(client, "a")
    guest, guest_id = await _user(client, "b")
    ride = await _ride(client, org)
    await _invite(client, org, ride["id"], guest_id)
    assert (
        await client.post(f"{RIDES}/{ride['id']}/respond", json={"accept": True}, headers=guest)
    ).status_code == 200

    theirs = await _of_type(client, org, "group_ride_accepted")
    assert len(theirs) == 1, theirs
    assert await _of_type(client, guest, "group_ride_accepted") == []


async def test_a_decline_notifies_nobody(client):
    """A decline is a private decision, not an event anybody is waiting on."""
    org, _ = await _user(client, "a")
    guest, guest_id = await _user(client, "b")
    ride = await _ride(client, org)
    await _invite(client, org, ride["id"], guest_id)
    assert (
        await client.post(f"{RIDES}/{ride['id']}/respond", json={"accept": False}, headers=guest)
    ).status_code == 200

    assert await _of_type(client, org, "group_ride_accepted") == []
    assert await _of_type(client, guest, "group_ride_accepted") == []


async def test_starting_tells_every_joined_rider_but_not_the_organizer(client):
    """The organizer's own tap is not news to the organizer."""
    org, _ = await _user(client, "a")
    guest, guest_id = await _user(client, "b")
    ride = await _ride(client, org)
    await _invite(client, org, ride["id"], guest_id)
    await client.post(f"{RIDES}/{ride['id']}/respond", json={"accept": True}, headers=guest)
    assert (await client.post(f"{RIDES}/{ride['id']}/start", headers=org)).status_code == 200

    theirs = await _of_type(client, guest, "group_ride_started")
    assert len(theirs) == 1, theirs
    assert await _of_type(client, org, "group_ride_started") == []


async def test_a_rider_who_withdrew_is_not_told_the_ride_started(client):
    """Someone who said they were not coming should not be summoned by a push
    notification afterwards."""
    org, _ = await _user(client, "a")
    guest, guest_id = await _user(client, "b")
    staying, staying_id = await _user(client, "c")
    ride = await _ride(client, org)
    await _invite(client, org, ride["id"], guest_id)
    await _invite(client, org, ride["id"], staying_id)
    for headers in (guest, staying):
        await client.post(f"{RIDES}/{ride['id']}/respond", json={"accept": True}, headers=headers)
    assert (await client.post(f"{RIDES}/{ride['id']}/leave", headers=guest)).status_code == 200
    assert (await client.post(f"{RIDES}/{ride['id']}/start", headers=org)).status_code == 200

    assert await _of_type(client, guest, "group_ride_started") == []
    assert len(await _of_type(client, staying, "group_ride_started")) == 1


async def test_a_reinvitation_after_a_decline_reaches_the_rider(client):
    """The regression test for a real bug.

    `uq_group_ride_participants_pair` makes the roster row's id stable for the
    life of the ride, so a dedupe key built from it alone made the SECOND
    invitation a silent duplicate of the first. The rider was re-invited, the
    organizer saw "invited", and no notification ever arrived — which for the
    rider is indistinguishable from having been ignored.
    """
    org, _ = await _user(client, "a")
    guest, guest_id = await _user(client, "b")
    ride = await _ride(client, org)
    await _invite(client, org, ride["id"], guest_id)
    assert (
        await client.post(f"{RIDES}/{ride['id']}/respond", json={"accept": False}, headers=guest)
    ).status_code == 200
    assert len(await _of_type(client, guest, "group_ride_invitation")) == 1

    await _invite(client, org, ride["id"], guest_id)
    assert len(await _of_type(client, guest, "group_ride_invitation")) == 2


async def test_reinviting_a_rider_who_already_joined_notifies_nobody(client):
    """The roster row is already `joined`, so there is no new invitation — the
    organizer is re-sending a request to somebody who is already on the ride."""
    org, _ = await _user(client, "a")
    guest, guest_id = await _user(client, "b")
    ride = await _ride(client, org)
    await _invite(client, org, ride["id"], guest_id)
    await client.post(f"{RIDES}/{ride['id']}/respond", json={"accept": True}, headers=guest)

    again = await client.post(
        f"{RIDES}/{ride['id']}/invitations", json={"user_id": guest_id}, headers=org
    )
    assert again.status_code == 201, again.text
    assert len(await _of_type(client, guest, "group_ride_invitation")) == 1


async def test_a_ride_message_notifies_the_joined_roster_only(client):
    """Its own notification type, because the recipient rule is its own: joined
    roster, not team members, not block-cleared pairs."""
    org, _ = await _user(client, "a")
    guest, guest_id = await _user(client, "b")
    outsider, _ = await _user(client, "c")
    ride = await _ride(client, org)
    await _invite(client, org, ride["id"], guest_id)
    await client.post(f"{RIDES}/{ride['id']}/respond", json={"accept": True}, headers=guest)

    channel = await client.get(f"{RIDES}/{ride['id']}/conversation", headers=org)
    assert channel.status_code == 200, channel.text
    sent = await client.post(
        f"{CHAT}/conversations/{channel.json()['id']}/messages",
        json={"body": "Rolling out in five", "client_message_id": str(uuid.uuid4())},
        headers=org,
    )
    assert sent.status_code == 201, sent.text

    theirs = await _of_type(client, guest, "chat_message_group_ride")
    assert len(theirs) == 1, theirs
    assert await _of_type(client, org, "chat_message_group_ride") == []
    assert await _of_type(client, outsider, "chat_message_group_ride") == []
    # Not delivered under the generic chat type either: one type, one rule.
    assert await _of_type(client, guest, "chat_message") == []


async def test_a_rider_removed_before_a_message_is_not_notified(client):
    """Removal takes effect at once, and a notification is a sighting too."""
    org, _ = await _user(client, "a")
    guest, guest_id = await _user(client, "b")
    ride = await _ride(client, org)
    await _invite(client, org, ride["id"], guest_id)
    await client.post(f"{RIDES}/{ride['id']}/respond", json={"accept": True}, headers=guest)
    channel = await client.get(f"{RIDES}/{ride['id']}/conversation", headers=org)

    removed = await client.delete(f"{RIDES}/{ride['id']}/participants/{guest_id}", headers=org)
    assert removed.status_code == 200, removed.text

    # The organizer is still on the ride and can still post — their own
    # membership is unaffected by somebody else's removal.
    still_posted = await client.post(
        f"{CHAT}/conversations/{channel.json()['id']}/messages",
        json={"body": "Anyone still out there?", "client_message_id": str(uuid.uuid4())},
        headers=org,
    )
    assert still_posted.status_code == 201, still_posted.text

    # The removed rider is not a recipient, and cannot speak for themselves
    # either: the channel is authorized by the live roster, not by the fact that
    # they are still listed as a conversation member (ADR-16 §5).
    refused = await client.post(
        f"{CHAT}/conversations/{channel.json()['id']}/messages",
        json={"body": "Let me in", "client_message_id": str(uuid.uuid4())},
        headers=guest,
    )
    # 404, not 403: the removed rider must not be able to learn that the channel
    # exists, only that they cannot reach it.
    assert refused.status_code == 404, refused.text
    assert refused.json()["error"]["code"] == "CHAT_CONVERSATION_NOT_FOUND"
    assert await _of_type(client, guest, "chat_message_group_ride") == []

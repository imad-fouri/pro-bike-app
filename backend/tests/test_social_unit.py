"""Unit tests for Phase 8.1 pure logic: usernames, pairs, state machine.

No database, no network. Whatever the database enforces, these functions
decide first — and they are tested here without a round trip (ADR-12).
"""

import uuid

import pytest

from app.models.social import RelationshipStatus
from app.services.social_service import (
    SocialError,
    _canonical_pair,
    _like_escape,
    canonical_username,
    relationship_state,
)


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("imad_fouri", "imad_fouri"),
        ("Imad_Fouri", "imad_fouri"),
        ("  Rider_7  ", "rider_7"),
        ("cyclecoach.ma", "cyclecoach.ma"),
        ("gravel_rider", "gravel_rider"),
        ("rider_7_x", "rider_7_x"),
        ("abc", "abc"),
        ("a" * 30, "a" * 30),
    ],
)
def test_canonical_username_accepts(raw, expected):
    assert canonical_username(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "ab",
        "a" * 31,
        "has space",
        "tab\there",
        "user@example.com",
        "12345",
        "+15551234567",
        "a.b.c",
        "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.signature",
        ".leading",
        "trailing_",
        "_leading",
        "trailing.",
        "contr\\ol",
        "semi;colon",
        "dash-name",
    ],
)
def test_canonical_username_rejects(raw):
    with pytest.raises(SocialError) as exc:
        canonical_username(raw)
    assert exc.value.code == "SOCIAL_INVALID_USERNAME"
    assert exc.value.status == 422


def test_canonical_pair_orders_and_rejects_self():
    a, b = uuid.uuid4(), uuid.uuid4()
    low, high = (a, b) if a < b else (b, a)
    assert _canonical_pair(a, b) == (low, high)
    assert _canonical_pair(b, a) == (low, high)
    with pytest.raises(SocialError) as exc:
        _canonical_pair(a, a)
    assert exc.value.code == "SOCIAL_CANNOT_TARGET_SELF"


def _rel(requested_by, status=RelationshipStatus.PENDING):
    return type(
        "R",
        (),
        {"requested_by_user_id": requested_by, "status": status},
    )()


def test_relationship_state_matrix():
    me, them = uuid.uuid4(), uuid.uuid4()
    assert relationship_state(me, me, None, False, False) == "SELF"
    assert relationship_state(me, them, None, True, False) == "BLOCKED"
    assert relationship_state(me, them, None, False, True) == "BLOCKED_BY_USER"
    assert relationship_state(me, them, None, False, False) == "NONE"
    assert (
        relationship_state(me, them, _rel(me, RelationshipStatus.ACCEPTED), False, False)
        == "FRIENDS"
    )
    assert relationship_state(me, them, _rel(me), False, False) == "OUTGOING_PENDING"
    assert relationship_state(me, them, _rel(them), False, False) == "INCOMING_PENDING"
    # Blocks outrank everything: even an accepted row reads as blocked.
    assert (
        relationship_state(me, them, _rel(me, RelationshipStatus.ACCEPTED), True, False)
        == "BLOCKED"
    )


def test_like_escape_neutralizes_wildcards():
    assert _like_escape("100%_x\\y") == "100\\%\\_x\\\\y"

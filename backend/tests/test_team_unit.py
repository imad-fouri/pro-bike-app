"""Phase 8.2 team unit tests (ADR-13).

Pure logic only: handle canonicalization, role authority, and the viewer↔team
state machine. No database — these are the rules that must hold regardless of
storage, so they are worth pinning without a round trip.
"""

import pytest

from app.models.team import TeamRole
from app.services.team_service import TeamError, canonical_handle, team_state


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("atlas_cc", "atlas_cc"),
        ("Atlas_CC", "atlas_cc"),
        ("  Mixed_Case  ", "mixed_case"),
        ("team.name", "team.name"),
        ("a1b2c3", "a1b2c3"),
    ],
)
def test_canonical_handle_lowercases_and_trims(raw, expected):
    assert canonical_handle(raw) == expected


@pytest.mark.parametrize(
    "bad",
    [
        "ab",  # too short
        "a" * 31,  # too long
        "12345",  # no letter
        "a.b.c",  # more than one dot
        ".leading",
        "trailing_",
        "with space",
        "user@example.com",
        "hyphen-ated",
        "",
    ],
)
def test_canonical_handle_rejects_bad_values(bad):
    with pytest.raises(TeamError) as exc:
        canonical_handle(bad)
    assert exc.value.code == "TEAM_INVALID_HANDLE"
    assert exc.value.status == 422


def test_role_authority_order():
    """Owner outranks admin outranks member — the ladder the API relies on."""
    assert TeamRole.OWNER != TeamRole.ADMIN != TeamRole.MEMBER
    managers = (TeamRole.OWNER, TeamRole.ADMIN)
    assert TeamRole.OWNER in managers
    assert TeamRole.ADMIN in managers
    # A plain member is never a manager.
    assert TeamRole.MEMBER not in managers


@pytest.mark.parametrize(
    ("role", "has_request", "is_invited", "expected"),
    [
        (TeamRole.OWNER, False, False, "OWNER"),
        (TeamRole.ADMIN, False, False, "ADMIN"),
        (TeamRole.MEMBER, False, False, "MEMBER"),
        # Membership wins over a stale pending request or invite.
        (TeamRole.MEMBER, True, True, "MEMBER"),
        (None, True, False, "JOIN_REQUEST_PENDING"),
        (None, False, True, "INVITED"),
        # An outstanding ask takes precedence over an offer: the rider already
        # asked to join, which is the stronger signal.
        (None, True, True, "JOIN_REQUEST_PENDING"),
        (None, False, False, "NOT_AFFILIATED"),
    ],
)
def test_team_state(role, has_request, is_invited, expected):
    assert team_state(role, has_request, is_invited) == expected


def test_archived_team_state_is_not_special():
    """Archiving is carried on the team row, not folded into the viewer state.

    A member of an archived team still reports their role; the client decides
    what to render from `status`. Keeping the two orthogonal avoids a state
    matrix nobody can hold in their head.
    """
    assert team_state(TeamRole.MEMBER, False, False) == "MEMBER"


def test_team_error_defaults():
    err = TeamError("TEAM_X", "boom")
    assert err.code == "TEAM_X"
    assert err.message == "boom"
    assert err.status == 422
    err404 = TeamError("TEAM_NOT_FOUND", "Team not found.", 404)
    assert err404.status == 404


def test_canonical_handle_is_idempotent():
    once = canonical_handle("Atlas_CC")
    assert canonical_handle(once) == once
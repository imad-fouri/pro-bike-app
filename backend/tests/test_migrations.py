"""Migration determinism: upgrade -> downgrade -> upgrade on a scratch database.

Also asserts the seeded ``training_calculation_versions`` rows still match the
code registry, so a formula can never be shipped unversioned (ADR-10 §2).
"""

import os

import asyncpg
import pytest
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

from alembic import command

HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND = os.path.dirname(HERE)
BASE_URL = "postgresql+psycopg://cyclecoach:cyclecoach@localhost:5432"
DBNAME = "cyclecoach_migtest"

PHASE5 = {"routes", "route_versions", "route_points"}
PHASE6 = {
    "training_calculation_versions",
    "training_profiles",
    "ftp_records",
    "training_activities",
    "training_activity_zones",
    "training_loads",
    "workouts",
    "workout_steps",
}
PHASE81 = {"social_profiles", "friend_relationships", "user_blocks"}
PHASE82 = {"teams", "team_memberships", "team_join_requests", "team_invitations"}
PHASE83 = {"conversations", "conversation_members", "messages"}
PHASE84 = {"push_devices", "notifications"}
# Phase 9 deliberately creates NO location table: live location is ephemeral and
# lives in Redis with a TTL (ADR-16 §6). If a `group_ride_locations` table ever
# appears here, the privacy decision was quietly reversed.
PHASE89 = {"group_rides", "group_ride_participants"}
PHASE10 = {"subscriptions", "entitlements"}
# Phase 10 WS-RC — challenges. Rankings need no table: the engine aggregates
# rides on read, so this phase is purely the challenge ledger/membership set.
PHASERC = {
    "challenges",
    "challenge_memberships",
    "challenge_progress_events",
    "challenge_completions",
}
# Phase 11 WS-AC — the persisted integrity verdict lives on the existing
# ``rides`` table, so the assertion is about columns, not tables.
INTEGRITY_COLUMNS = {
    "integrity_status",
    "integrity_calculation_version",
    "integrity_rules_triggered",
    "integrity_evaluated_at",
}
BASE = {
    "users",
    "user_profiles",
    "refresh_sessions",
    "password_reset_tokens",
    "email_verification_tokens",
    "bikes",
    "rides",
    "ride_points",
}
EXPECTED = (
    BASE | PHASE5 | PHASE6 | PHASE81 | PHASE82 | PHASE83 | PHASE84 | PHASE89 | PHASE10 | PHASERC
)
ALL_PHASES = PHASE5 | PHASE6 | PHASE81 | PHASE82 | PHASE83 | PHASE84 | PHASE89 | PHASE10 | PHASERC

SENSOR_COLUMNS = {"power_w", "hr_bpm", "cadence_rpm"}

# Chat columns that carry a load-bearing invariant (ADR-14). Asserted by name
# because each one exists to make a specific failure impossible.
CHAT_COLUMNS = {
    "conversations": {"kind", "team_id", "next_seq"},
    "conversation_members": {"conversation_id", "user_id", "last_read_seq"},
    "messages": {"seq", "client_message_id", "edited_at", "deleted_at"},
}

# Notification columns that carry a load-bearing invariant (ADR-15).
NOTIFICATION_COLUMNS = {
    "push_devices": {"platform", "provider", "device_id", "token", "enabled"},
    "notifications": {"type", "l10n_key", "params", "read_at", "dedupe_key"},
}

# Group-ride columns that carry a load-bearing invariant (ADR-16).
GROUP_RIDE_COLUMNS = {
    # The immutable geometry pin is both-or-neither; the CHECK is asserted below.
    "group_rides": {
        "organizer_user_id",
        "status",
        "route_id",
        "route_version",
        "started_at",
        "completed_at",
        "cancelled_at",
    },
    # `responded_at` is what separates `invited` from every answered state.
    "group_ride_participants": {"role", "status", "invited_by_user_id", "responded_at"},
}

# WS-S columns that carry a load-bearing invariant. Commercial state and
# authorization state are separate tables; provider identifiers exist only for
# idempotent event handling, never as authorization labels.
SUBSCRIPTION_COLUMNS = {
    "subscriptions": {
        "user_id",
        "provider",
        "provider_subscription_id",
        "plan",
        "status",
        "started_at",
        "current_period_start",
        "current_period_end",
        "cancel_at_period_end",
        "last_provider_event_id",
        "last_provider_event_at",
    },
    "entitlements": {
        "user_id",
        "feature",
        "status",
        "source",
        "source_subscription_id",
        "starts_at",
        "expires_at",
    },
}


def _indexes(table: str) -> set[str]:
    eng = create_engine(f"{BASE_URL}/{DBNAME}")
    try:
        names = {i.get("name") for i in inspect(eng).get_indexes(table)}
        return {n for n in names if isinstance(n, str)}
    finally:
        eng.dispose()


def _constraints(table: str) -> set[str]:
    eng = create_engine(f"{BASE_URL}/{DBNAME}")
    try:
        insp = inspect(eng)
        names = {c["name"] for c in insp.get_unique_constraints(table)}
        names |= {c["name"] for c in insp.get_check_constraints(table)}
        return {n for n in names if n}
    finally:
        eng.dispose()


def _foreign_keys(table: str) -> dict[str, tuple[tuple[str, ...], tuple[str, ...], str]]:
    """Each FK as {name: (local columns, referenced columns, ondelete)}."""
    eng = create_engine(f"{BASE_URL}/{DBNAME}")
    try:
        return {
            fk["name"]: (
                tuple(fk["constrained_columns"]),
                tuple(fk["referred_columns"]),
                fk.get("options", {}).get("ondelete", ""),
            )
            for fk in inspect(eng).get_foreign_keys(table)
            if fk["name"]
        }
    finally:
        eng.dispose()


def _cfg() -> Config:
    cfg = Config(os.path.join(BACKEND, "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", f"{BASE_URL}/{DBNAME}")
    return cfg


def _tables() -> set[str]:
    eng = create_engine(f"{BASE_URL}/{DBNAME}")
    try:
        return set(inspect(eng).get_table_names())
    finally:
        eng.dispose()


def _columns(table: str) -> set[str]:
    eng = create_engine(f"{BASE_URL}/{DBNAME}")
    try:
        return {c["name"] for c in inspect(eng).get_columns(table)}
    finally:
        eng.dispose()


def _ride_route_columns() -> set[str]:
    return _columns("rides") & {"route_id", "route_version"}


def _seeded_versions() -> dict[str, dict]:
    eng = create_engine(f"{BASE_URL}/{DBNAME}")
    try:
        with eng.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT version, kind, title, summary, params::text "
                    "FROM training_calculation_versions"
                )
            ).all()
    finally:
        eng.dispose()
    import json

    return {
        r[0]: {
            "kind": r[1],
            "title": r[2],
            "summary": r[3],
            "params": json.loads(r[4]),
        }
        for r in rows
    }


@pytest.mark.asyncio
async def test_migration_upgrade_downgrade_upgrade():
    conn = await asyncpg.connect("postgresql://cyclecoach:cyclecoach@localhost:5432/postgres")
    try:
        await conn.execute(f'DROP DATABASE IF EXISTS "{DBNAME}"')
        await conn.execute(f'CREATE DATABASE "{DBNAME}"')
    finally:
        await conn.close()

    cfg = _cfg()
    command.upgrade(cfg, "head")
    tables = _tables()
    assert EXPECTED <= tables
    assert _ride_route_columns() == {"route_id", "route_version"}
    assert SENSOR_COLUMNS <= _columns("ride_points")
    assert PHASE81 <= tables  # 0007 social tables present
    assert PHASE82 <= tables  # 0008 team tables present
    assert PHASE83 <= tables  # 0009 chat tables present
    for table, expected in CHAT_COLUMNS.items():
        assert expected <= _columns(table), f"{table} is missing a chat column"

    # Each of these constraints exists to make one specific failure impossible;
    # losing one in a future edit is a silent regression, not a refactor.
    assert {
        "uq_messages_client_id",
        "uq_messages_conversation_seq",
        "ck_messages_seq",
    } <= _constraints("messages")
    assert {
        "uq_conversation_members_pair",
        "ck_conversation_members_last_read_seq",
    } <= _constraints("conversation_members")
    # Phase 9 widens the kind CHECK from a two-way to a three-way disjunction and
    # renames it, because it now binds a conversation to a team OR a ride. The
    # downgrade restores the 0009 name verbatim so 0009 stays byte-identical.
    assert {"ck_conversations_kind_binding", "ck_conversations_next_seq"} <= _constraints(
        "conversations"
    )
    # One channel per ride, enforced by a PARTIAL unique index (an index, not a
    # table constraint) exactly like the one-channel-per-team guarantee.
    assert "uq_conversations_group_ride_channel" in _indexes("conversations")

    # Phase 8.4 — notification foundation.
    assert PHASE84 <= tables  # 0010 notification tables present
    for table, expected in NOTIFICATION_COLUMNS.items():
        assert expected <= _columns(table), f"{table} is missing a notification column"
    assert {
        "uq_push_devices_user_device",
        "uq_push_devices_provider_token",
        "ck_push_devices_device_id",
        "ck_push_devices_token",
    } <= _constraints("push_devices")
    # The idempotency guarantee is a PARTIAL unique index, so it is an index and
    # not a table constraint. Asserting the index exists is what stops a later
    # edit replacing it with a plain unique, which would break system notices.
    assert "uq_notifications_dedupe_key" in _indexes("notifications")
    # The unread count must not scan history.
    assert "ix_notifications_recipient_unread" in _indexes("notifications")
    assert "ix_notifications_recipient_created" in _indexes("notifications")

    # Phase 9 — group rides and one authoritative roster (ADR-16).
    assert PHASE89 <= tables  # 0011 group-ride tables present
    for table, expected in GROUP_RIDE_COLUMNS.items():
        assert expected <= _columns(table), f"{table} is missing a group-ride column"
    # These three constraints each make one specific impossibility true.
    assert {
        "uq_group_ride_participants_pair",
        "ck_group_ride_participants_responded_at",
        "ck_group_ride_participants_inviter",
    } <= _constraints("group_ride_participants")
    assert {
        "ck_group_rides_route_pin",
        "ck_group_rides_route_version",
        "ck_group_rides_title_nonempty",
        "ck_group_rides_started_at",
        "ck_group_rides_completed_at",
        "ck_group_rides_cancelled_at",
    } <= _constraints("group_rides")
    # The route pin is checked as a PAIR, not as route_id alone. Asserting the
    # referenced columns is the point: a FK on route_id alone would still let a
    # ride name version 99 of a route whose only version is 1. `SET NULL` on a
    # composite FK nulls BOTH columns, which is what `ck_group_rides_route_pin`
    # requires when a route (and its versions) is deleted.
    pin = _foreign_keys("group_rides")["fk_group_rides_route_pin"]
    assert pin == (
        ("route_id", "route_version"),
        ("route_id", "version_no"),
        "SET NULL",
    )
    # The pending-invitation inbox must not scan a ride's answer history.
    assert "ix_group_ride_participants_pending_invitee" in _indexes("group_ride_participants")
    assert "ix_group_ride_participants_ride_status" in _indexes("group_ride_participants")
    # The privacy decision, asserted as a schema fact rather than a comment: a
    # location table would make GPS history permanent by accident.
    assert not any("location" in t for t in tables), "live location must stay out of the database"

    # Phase 10 WS-S — commercial subscriptions and product entitlements.
    assert PHASE10 <= tables  # 0012 subscription tables present
    for table, expected in SUBSCRIPTION_COLUMNS.items():
        assert expected <= _columns(table), f"{table} is missing a subscription column"
    # These constraints make duplicate commercial identities and duplicate live
    # grants impossible, while keeping manual grants separate from provider rows.
    assert {
        "ck_subscriptions_pro_plan",
        "ck_subscriptions_period_order",
        "ck_subscriptions_provider_subscription_id",
        "ck_subscriptions_provider_event_id",
    } <= _constraints("subscriptions")
    assert {
        "ck_entitlements_source_link",
        "ck_entitlements_window_order",
    } <= _constraints("entitlements")
    assert "uq_subscriptions_provider_external" in _indexes("subscriptions")
    assert "uq_entitlements_subscription_feature" in _indexes("entitlements")
    assert "uq_entitlements_manual_feature" in _indexes("entitlements")
    assert "ix_entitlements_user_feature_status" in _indexes("entitlements")
    entitlement_fks = set(_foreign_keys("entitlements").values())
    assert (("user_id",), ("id",), "CASCADE") in entitlement_fks
    assert (("source_subscription_id",), ("id",), "CASCADE") in entitlement_fks
    assert (("user_id",), ("id",), "CASCADE") in set(_foreign_keys("subscriptions").values())

    # Phase 10 WS-RC — challenges. The four tables make three impossibilities:
    # a rider cannot hold two memberships in one challenge, a ride cannot be
    # credited twice to one challenge, and a rider cannot be awarded twice.
    assert PHASERC <= tables  # 0013 challenge tables present
    assert "uq_challenge_memberships_pair" in _constraints("challenge_memberships")
    assert "uq_challenge_progress_challenge_ride" in _constraints("challenge_progress_events")
    assert "uq_challenge_completions_pair" in _constraints("challenge_completions")
    assert "ix_challenges_status_end" in _indexes("challenges")
    assert "ix_challenge_memberships_user" in _indexes("challenge_memberships")

    # Phase 11 WS-AC — the integrity verdict is a column family, not a table:
    # the four columns appear at head, and the backfill labels legacy completed
    # rides as accepted/v1 (provenance, never a re-evaluation).
    assert INTEGRITY_COLUMNS <= _columns("rides")

    command.downgrade(cfg, "-1")  # 0014 -> 0013
    tables = _tables()
    assert INTEGRITY_COLUMNS.isdisjoint(_columns("rides"))  # integrity columns removed
    assert PHASERC <= tables  # …while 0013 challenge tables remain untouched

    command.downgrade(cfg, "-1")  # 0013 -> 0012
    tables = _tables()
    assert PHASERC.isdisjoint(tables)  # challenge tables removed
    assert PHASE10 <= tables  # …while 0012 subscriptions remain untouched

    command.downgrade(cfg, "-1")  # 0012 -> 0011
    tables = _tables()
    assert PHASE10.isdisjoint(tables)  # subscription tables removed
    assert EXPECTED - PHASE10 - PHASERC <= tables  # …while 0011 and below remain
    assert PHASE89 <= tables  # group-ride tables untouched by the WS-S downgrade

    command.downgrade(cfg, "-1")  # 0011 -> 0010
    tables = _tables()
    assert PHASE89.isdisjoint(tables)  # group-ride tables removed
    assert EXPECTED - PHASE10 - PHASE89 - PHASERC <= tables  # …while 0010 and below remain
    assert PHASE84 <= tables  # notification tables untouched by the group-ride downgrade
    assert PHASE83 <= tables  # chat tables untouched: the enum was rebuilt, not replaced
    # The widened conversation CHECK is restored to the 0009 name and shape, so a
    # downgrade is byte-identical to the pre-Phase-9 database.
    assert "ck_conversations_kind_team" in _constraints("conversations")
    assert "ck_conversations_kind_binding" not in _constraints("conversations")
    assert "group_ride_id" not in _columns("conversations")

    command.downgrade(cfg, "-1")  # 0010 -> 0009
    tables = _tables()
    assert PHASE84.isdisjoint(tables)  # notification tables removed
    assert (
        EXPECTED - PHASE10 - PHASE84 - PHASE89 - PHASERC <= tables
    )  # …while 0009 and below remain
    assert PHASE83 <= tables  # chat tables untouched by the notification downgrade

    command.downgrade(cfg, "-1")  # 0009 -> 0008
    tables = _tables()
    assert PHASE83.isdisjoint(tables)  # chat tables removed
    assert EXPECTED - ALL_PHASES <= tables  # …while 0008 and below remain
    assert PHASE82 <= tables  # team tables untouched by the chat downgrade

    command.downgrade(cfg, "-1")  # 0008 -> 0007
    tables = _tables()
    assert PHASE82.isdisjoint(tables)  # team tables removed
    assert EXPECTED - ALL_PHASES <= tables  # …while 0007 and below
    assert PHASE81 <= tables  # social tables untouched by the team downgrade

    command.downgrade(cfg, "-1")  # 0007 -> 0006
    tables = _tables()
    assert PHASE81.isdisjoint(tables)  # social tables removed
    assert EXPECTED - ALL_PHASES <= tables  # …0006 and below

    command.downgrade(cfg, "-1")  # 0006 -> 0005
    tables = _tables()
    assert PHASE6.isdisjoint(tables)  # training tables removed
    assert EXPECTED - ALL_PHASES <= tables
    assert SENSOR_COLUMNS.isdisjoint(_columns("ride_points"))  # sensor columns dropped
    assert _ride_route_columns() == {"route_id", "route_version"}

    command.downgrade(cfg, "-1")  # 0005 -> 0004
    tables = _tables()
    assert PHASE5.isdisjoint(tables)  # route tables removed
    assert EXPECTED - ALL_PHASES <= tables
    assert _ride_route_columns() == set()  # ride->route columns dropped

    command.downgrade(cfg, "base")
    assert _tables() <= {"alembic_version"}  # full rollback is clean

    command.upgrade(cfg, "head")  # deterministic re-apply
    assert EXPECTED <= _tables()
    assert _ride_route_columns() == {"route_id", "route_version"}
    assert SENSOR_COLUMNS <= _columns("ride_points")
    assert PHASERC <= _tables()  # …and the WS-RC tables come back with their invariants
    assert INTEGRITY_COLUMNS <= _columns("rides")  # …and so do the WS-AC columns
    assert "uq_challenge_memberships_pair" in _constraints("challenge_memberships")
    assert "uq_challenge_progress_challenge_ride" in _constraints("challenge_progress_events")
    assert "uq_challenge_completions_pair" in _constraints("challenge_completions")
    for table, expected in CHAT_COLUMNS.items():
        assert expected <= _columns(table), f"{table} lost a column on re-apply"
    for table, expected in NOTIFICATION_COLUMNS.items():
        assert expected <= _columns(table), f"{table} lost a column on re-apply"
    for table, expected in GROUP_RIDE_COLUMNS.items():
        assert expected <= _columns(table), f"{table} lost a column on re-apply"
    for table, expected in SUBSCRIPTION_COLUMNS.items():
        assert expected <= _columns(table), f"{table} lost a column on re-apply"
    # Re-applying must not resurrect the widened conversation CHECK.
    assert "ck_conversations_kind_binding" in _constraints("conversations")


@pytest.mark.asyncio
async def test_seeded_calculation_versions_match_the_code_registry():
    """The migration seed is the release record; the registry is the contract.

    If this fails, either a version was added to the code without being seeded
    in migration 0006, or a released version's meaning was edited in place —
    both are bugs (ADR-10 §2).
    """
    from app.services.training_calc import CALCULATION_VERSIONS

    conn = await asyncpg.connect("postgresql://cyclecoach:cyclecoach@localhost:5432/postgres")
    try:
        await conn.execute(f'DROP DATABASE IF EXISTS "{DBNAME}"')
        await conn.execute(f'CREATE DATABASE "{DBNAME}"')
    finally:
        await conn.close()

    command.upgrade(_cfg(), "head")
    seeded = _seeded_versions()
    assert set(seeded) == set(CALCULATION_VERSIONS)
    for version, entry in CALCULATION_VERSIONS.items():
        assert seeded[version] == {
            "kind": entry["kind"],
            "title": entry["title"],
            "summary": entry["summary"],
            "params": entry["params"],
        }, f"{version} drifted between migration 0006 and the code registry"

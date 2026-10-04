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
EXPECTED = BASE | PHASE5 | PHASE6 | PHASE81 | PHASE82 | PHASE83 | PHASE84

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


def _indexes(table: str) -> set[str]:
    eng = create_engine(f"{BASE_URL}/{DBNAME}")
    try:
        return {i["name"] for i in inspect(eng).get_indexes(table) if i.get("name")}
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
    assert {"ck_conversations_kind_team", "ck_conversations_next_seq"} <= _constraints(
        "conversations"
    )

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

    command.downgrade(cfg, "-1")  # 0010 -> 0009
    tables = _tables()
    assert PHASE84.isdisjoint(tables)  # notification tables removed
    assert EXPECTED - PHASE84 <= tables  # …while 0009 and below remain
    assert PHASE83 <= tables  # chat tables untouched by the notification downgrade

    command.downgrade(cfg, "-1")  # 0009 -> 0008
    tables = _tables()
    assert PHASE83.isdisjoint(tables)  # chat tables removed
    assert EXPECTED - PHASE83 - PHASE84 <= tables  # …while 0008 and below remain
    assert PHASE82 <= tables  # team tables untouched by the chat downgrade

    command.downgrade(cfg, "-1")  # 0008 -> 0007
    tables = _tables()
    assert PHASE82.isdisjoint(tables)  # team tables removed
    assert EXPECTED - PHASE82 - PHASE83 - PHASE84 <= tables  # …while 0007 and below
    assert PHASE81 <= tables  # social tables untouched by the team downgrade

    command.downgrade(cfg, "-1")  # 0007 -> 0006
    tables = _tables()
    assert PHASE81.isdisjoint(tables)  # social tables removed
    assert EXPECTED - PHASE81 - PHASE82 - PHASE83 - PHASE84 <= tables  # …0006 and below

    command.downgrade(cfg, "-1")  # 0006 -> 0005
    tables = _tables()
    assert PHASE6.isdisjoint(tables)  # training tables removed
    assert EXPECTED - PHASE6 - PHASE81 - PHASE82 - PHASE83 - PHASE84 <= tables
    assert SENSOR_COLUMNS.isdisjoint(_columns("ride_points"))  # sensor columns dropped
    assert _ride_route_columns() == {"route_id", "route_version"}

    command.downgrade(cfg, "-1")  # 0005 -> 0004
    tables = _tables()
    assert PHASE5.isdisjoint(tables)  # route tables removed
    assert (EXPECTED - PHASE5 - PHASE6 - PHASE81 - PHASE82 - PHASE83 - PHASE84) <= tables
    assert _ride_route_columns() == set()  # ride->route columns dropped

    command.downgrade(cfg, "base")
    assert _tables() <= {"alembic_version"}  # full rollback is clean

    command.upgrade(cfg, "head")  # deterministic re-apply
    assert EXPECTED <= _tables()
    assert _ride_route_columns() == {"route_id", "route_version"}
    assert SENSOR_COLUMNS <= _columns("ride_points")
    for table, expected in CHAT_COLUMNS.items():
        assert expected <= _columns(table), f"{table} lost a column on re-apply"
    for table, expected in NOTIFICATION_COLUMNS.items():
        assert expected <= _columns(table), f"{table} lost a column on re-apply"


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

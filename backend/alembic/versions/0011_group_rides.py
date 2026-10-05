"""Phase 9 migration: group rides, one authoritative roster, ride channels (ADR-16).

Two additive tables plus a conversation column on top of `0010_notifications`.
`users`, `social_profiles`, `teams`, `team_memberships` and `messages` are not
modified, so Phase 9 cannot regress Phases 8.1-8.4. The three deliberate edits
to existing objects are all additive and all reversible below:

* `conversation_kind` gains `group_ride`.
* `notification_type` gains the three ride values named in the Phase 8.4 forward
  commitment (`group_ride_invitation`, `group_ride_accepted`,
  `group_ride_started`).
* `conversations` gains `group_ride_id` and a widened kind CHECK.

Why one roster table instead of invitations + memberships as teams have: teams
need two because an applicant and an inviter have OPPOSITE permissions. A ride
has one way in, so a second table would be a second source of truth about who is
on the ride. `group_ride_participants.status` carries all five states
(invited/joined/declined/left/removed), and only `joined` grants visibility.

`UNIQUE(group_ride_id, user_id)` covers the table's whole life rather than just
active states. That makes "am I on this ride" an index-only lookup and makes
double-participation structurally impossible; a re-invite after `declined`
transitions the existing row back to `invited`.

LIVE LOCATION HAS NO TABLE IN THIS MIGRATION, ON PURPOSE. Writing GPS fixes to
Postgres would make location history permanent by accident: every row would be a
retained fact about where a person was, in every backup, subject to every future
"just add an index" request. Location lives in Redis with a TTL instead
(ADR-16 §6), so "stop sharing" deletes the field and "nobody is sharing" leaves
nothing behind at all.

`routes` FK is `ON DELETE SET NULL` and the CHECK forces both-or-neither, so a
deleted route degrades a ride to "no route" and can never leave a dangling
version number pinning geometry that no longer exists.

Enum handling: PostgreSQL cannot DROP a value from a type, so the downgrade
recreates `conversation_kind` and `notification_type` with their original value
lists. PostgreSQL 16 permits `ALTER TYPE ... ADD VALUE` inside a transaction,
so upgrade stays one atomic step.
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0011_group_rides"
down_revision = "0010_notifications"

_status = postgresql.ENUM(
    "open", "started", "completed", "cancelled", name="group_ride_status", create_type=False
)
_role = postgresql.ENUM("organizer", "participant", name="group_ride_role", create_type=False)
_participant_status = postgresql.ENUM(
    "invited",
    "joined",
    "declined",
    "left",
    "removed",
    name="group_ride_participant_status",
    create_type=False,
)


#: Both binding CHECKs compare `kind::text` rather than `kind` directly.
#:
#: PostgreSQL refuses to USE an enum value added in the same uncommitted
#: transaction ("unsafe use of new value ... New enum values must be committed
#: before they can be used"), and Alembic runs this whole migration — ADD VALUE
#: included — in one transaction. Committing the ADD VALUE separately is not an
#: option: it would either end the migration's transaction, making the schema
#: change non-atomic, or need a second connection that cannot see the enum the
#: same transaction is still creating.
#:
#: The cast puts the comparison in text space, so `'group_ride'` is never
#: resolved against the enum type and the restriction never triggers. The
#: constraint is exactly as strong as writing the bare literal would be. This
#: works in a CHECK but NOT in an index predicate, because enum-to-text goes
#: through `enum_out`, which is STABLE rather than IMMUTABLE — see
#: uq_conversations_group_ride_channel below.
_ORIGINAL_BINDING_CHECK = (
    "(kind::text = 'team' AND team_id IS NOT NULL) OR "
    "(kind::text = 'direct' AND team_id IS NULL)"
)

#: The widened three-way disjunction: a conversation binds to exactly one of a
#: team or a ride, and never to both.
_BINDING_CHECK = (
    "(kind::text = 'team' AND team_id IS NOT NULL AND group_ride_id IS NULL) OR "
    "(kind::text = 'direct' AND team_id IS NULL AND group_ride_id IS NULL) OR "
    "(kind::text = 'group_ride' AND team_id IS NULL AND group_ride_id IS NOT NULL)"
)


def _add_value(type_name: str, value: str) -> None:
    """Add one enum value inside the migration's own transaction.

    The IF NOT EXISTS guard makes a re-run a no-op rather than an error.
    """
    op.execute(
        sa.text(
            f"DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_type t "
            f"JOIN pg_enum e ON e.enumtypid = t.oid "
            f"WHERE t.typname = '{type_name}' AND e.enumlabel = '{value}') "
            f"THEN ALTER TYPE {type_name} ADD VALUE '{value}'; END IF; END $$;"
        )
    )


def _recreate_enum(type_name: str, table: str, column: str, values: list[str]) -> None:
    """Drop one enum value by rebuilding the type without it.

    Required because PostgreSQL has no `DROP VALUE`. The column is widened to
    text, the type is dropped and recreated with the reduced value list, and the
    column is cast back. Callers must have already deleted rows that use the
    dropped value, or the cast back fails loudly rather than losing data.

    The `uq_conversations_team_channel` partial index from 0009 is dropped and
    rebuilt around this, because it is defined with the predicate `kind = 'team'`
    and PostgreSQL reparses that predicate against the column's type on every
    ALTER TYPE — which fails with "operator does not exist: character varying =
    conversation_kind" the moment the column is varchar. Dropping it first and
    recreating it after is the only ordering that works, and the guard below
    means it is restored exactly as 0009 defined it.
    """
    partial_index = (
        "uq_conversations_team_channel"
        if (table == "conversations" and column == "kind")
        else None
    )
    if partial_index:
        op.drop_index(partial_index, table_name=table)
    quoted = ", ".join(f"'{v}'" for v in values)
    op.execute(sa.text(f"ALTER TABLE {table} ALTER COLUMN {column} DROP DEFAULT"))
    # Both casts are explicit. `column::varchar` alone leaves PostgreSQL trying
    # to resolve a varchar-to-enum comparison while the type still exists, and
    # `column::type` alone assumes the column is already text.
    op.execute(
        sa.text(
            f"ALTER TABLE {table} ALTER COLUMN {column} TYPE varchar "
            f"USING {column}::text"
        )
    )
    op.execute(sa.text(f"DROP TYPE {type_name}"))
    op.execute(sa.text(f"CREATE TYPE {type_name} AS ENUM ({quoted})"))
    op.execute(
        sa.text(
            f"ALTER TABLE {table} ALTER COLUMN {column} TYPE {type_name} "
            f"USING {column}::{type_name}"
        )
    )
    if partial_index:
        op.create_index(
            partial_index,
            table,
            ["team_id"],
            unique=True,
            postgresql_where=sa.text("kind = 'team'"),
        )


def upgrade() -> None:
    _status.create(op.get_bind(), checkfirst=True)
    _role.create(op.get_bind(), checkfirst=True)
    _participant_status.create(op.get_bind(), checkfirst=True)

    _add_value("conversation_kind", "group_ride")
    _add_value("notification_type", "group_ride_invitation")
    _add_value("notification_type", "group_ride_accepted")
    _add_value("notification_type", "group_ride_started")
    # A ride-channel message has a THIRD recipient authorization basis: live
    # team members for team channels, block-policy-cleared pairs for DMs, and
    # JOINED roster rows for a ride (ADR-16 §5). Same reason CHAT_MESSAGE_TEAM
    # exists as a separate value from CHAT_MESSAGE in Phase 8.4 — one type with
    # three different recipient rules would make the privacy rule unenforceable.
    _add_value("notification_type", "chat_message_group_ride")

    # --- group_rides ----------------------------------------------------------
    op.create_table(
        "group_rides",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        # CASCADE: a deleted organizer's rides go with them, exactly as
        # `teams.owner_user_id` and `rides.user_id` already do. The single
        # authoritative organizer is stored here; the matching ORGANIZER row in
        # group_ride_participants is written in the same transaction.
        sa.Column(
            "organizer_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("title", sa.String(120), nullable=False),
        sa.Column("description", sa.String(1000)),
        sa.Column("status", _status, nullable=False, server_default="open"),
        sa.Column("starts_at", sa.DateTime(timezone=True)),
        # A free-text meeting point label. Deliberately NOT a coordinate pair:
        # a lat/lon here would be a second location contract with its own
        # privacy story, and nothing in Phase 9 needs a machine-readable start
        # point. Navigation and geocoding are explicit non-goals.
        sa.Column("meeting_point", sa.String(160)),
        # An immutable geometry pin, both-or-neither. `rides` already pins this
        # pair (ADR-09); a ride pins it so an edit to the route cannot silently
        # change what the organizer invited people to ride.
        #
        # The FK is on the PAIR, not on route_id alone, and that is the whole
        # point. `route_versions` has UNIQUE(route_id, version_no), so this FK
        # makes the pair itself referentially checked: version 7 of route A is
        # rejected unless that exact version exists. A single-column FK on
        # route_id could not express this — it would happily accept route A
        # version 99, which does not exist, and leave a ride pointing at
        # geometry that was never created.
        #
        # ON DELETE SET NULL on a composite FK nulls BOTH columns, which is what
        # `ck_group_rides_route_pin` requires — so deleting a route (which
        # cascades to its versions) degrades the ride to "no route" instead of
        # failing on the CHECK or leaving a dangling version number.
        sa.Column("route_id", postgresql.UUID(as_uuid=True)),
        sa.Column("route_version", sa.Integer),
        sa.ForeignKeyConstraint(
            ["route_id", "route_version"],
            ["route_versions.route_id", "route_versions.version_no"],
            name="fk_group_rides_route_pin",
            ondelete="SET NULL",
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("cancelled_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "(route_id IS NULL AND route_version IS NULL) OR "
            "(route_id IS NOT NULL AND route_version IS NOT NULL)",
            name="ck_group_rides_route_pin",
        ),
        sa.CheckConstraint("route_version IS NULL OR route_version >= 1", name="ck_group_rides_route_version"),
        sa.CheckConstraint("length(title) > 0", name="ck_group_rides_title_nonempty"),
        # Terminal states must carry their timestamp and non-terminal states must
        # not: a `completed` row with no completed_at is not a fact a rider can
        # rely on, and it is the kind of drift that becomes a bug report.
        sa.CheckConstraint(
            "(status = 'started' AND started_at IS NOT NULL) OR (status <> 'started')",
            name="ck_group_rides_started_at",
        ),
        sa.CheckConstraint(
            "(status = 'completed' AND completed_at IS NOT NULL) OR (status <> 'completed')",
            name="ck_group_rides_completed_at",
        ),
        sa.CheckConstraint(
            "(status = 'cancelled' AND cancelled_at IS NOT NULL) OR (status <> 'cancelled')",
            name="ck_group_rides_cancelled_at",
        ),
    )
    # "Rides I organize, newest first" - the organizer's own tab.
    op.create_index("ix_group_rides_organizer_created", "group_rides", ["organizer_user_id", "created_at"])
    # Upcoming rides across all organizers: the discovery query.
    op.create_index("ix_group_rides_status_starts", "group_rides", ["status", "starts_at"])
    op.create_index("ix_group_rides_route_id", "group_rides", ["route_id"])
    # PostgreSQL partial index on created_at DESC would need an opclass here;
    # the composite (organizer_user_id, created_at) already serves the only
    # listing that needs ordering.

    # --- group_ride_participants ---------------------------------------------
    op.create_table(
        "group_ride_participants",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        # CASCADE: a deleted ride has no roster worth keeping, and its live
        # location keys expire on their own TTL.
        sa.Column(
            "group_ride_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("group_rides.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # CASCADE, matching `team_memberships.user_id`: a deleted rider leaves
        # no roster rows. Contrast `messages.sender_user_id` (RESTRICT), because
        # a roster row is a current-state fact about a ride, not history a
        # participant authored.
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("role", _role, nullable=False, server_default="participant"),
        sa.Column("status", _participant_status, nullable=False),
        # NULL for the organizer's own row; set for every invitation.
        sa.Column(
            "invited_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
        ),
        sa.Column("message", sa.String(280)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("responded_at", sa.DateTime(timezone=True)),
        # One row per (ride, user) for the table's WHOLE life, not just for
        # active states. This is what makes double-participation impossible and
        # "am I on this ride" an index-only lookup.
        sa.UniqueConstraint("group_ride_id", "user_id", name="uq_group_ride_participants_pair"),
        # A responder's timestamp is a fact; a non-responder's absence of one is
        # too. This stops a `joined` row from being written without saying when.
        sa.CheckConstraint(
            "status = 'invited' OR responded_at IS NOT NULL",
            name="ck_group_ride_participants_responded_at",
        ),
        # The organizer is authoritative and is therefore never invitable: the
        # authoritative organizer already holds the ORGANIZER/JOINED row.
        sa.CheckConstraint(
            "invited_by_user_id IS NOT NULL OR role = 'organizer'",
            name="ck_group_ride_participants_inviter",
        ),
    )
    # "Rides I am on" for the participant's own tab.
    op.create_index("ix_group_ride_participants_user_id", "group_ride_participants", ["user_id"])
    # The roster of one ride, and the `joined` filter that authorizes every
    # visibility decision in the phase.
    op.create_index(
        "ix_group_ride_participants_ride_status",
        "group_ride_participants",
        ["group_ride_id", "status"],
    )
    # The invitation inbox: a partial scan of pending rows only, so it does not
    # degrade with a ride's answer history.
    op.create_index(
        "ix_group_ride_participants_pending_invitee",
        "group_ride_participants",
        ["user_id"],
        postgresql_where=sa.text("status = 'invited'"),
    )

    # --- conversations: one channel per ride ---------------------------------
    op.add_column(
        "conversations",
        sa.Column(
            "group_ride_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("group_rides.id", ondelete="CASCADE"),
        ),
    )
    # Widen the two-way kind CHECK to a three-way disjunction. A conversation
    # still binds to exactly one of a team or a ride, and never to both.
    op.drop_constraint("ck_conversations_kind_team", "conversations", type_="check")
    op.create_check_constraint("ck_conversations_kind_binding", "conversations", _BINDING_CHECK)
    # Exactly one channel per ride, enforced by a UNIQUE index on group_ride_id.
    #
    # The 0009 per-team guarantee is a PARTIAL index (`WHERE kind = 'team'`),
    # and mirroring that shape is impossible here for a specific reason: the
    # predicate would have to name 'group_ride', a value added earlier in this
    # same transaction, which PostgreSQL refuses to use. The `kind::text` trick
    # that works in the CHECK does not work in an index either, because
    # enum-to-text goes through `enum_out`, which is STABLE rather than IMMUTABLE
    # and so is rejected outright in an index predicate.
    #
    # A plain UNIQUE index is exactly as strong, and needs no predicate. SQL's
    # unique indexes treat NULLs as distinct, so all team and direct
    # conversations (which carry a NULL group_ride_id) coexist, while every ride
    # can have precisely one channel. The index is also the FK lookup path the
    # per-ride authorization queries need, which is why it replaces the separate
    # plain index the 0009 table needed for team_id.
    op.create_index(
        "uq_conversations_group_ride_channel",
        "conversations",
        ["group_ride_id"],
        unique=True,
    )


def downgrade() -> None:
    # Conversations bound to a ride must go before the ride, and before the
    # enum values that describe them. CASCADE from group_rides handles the
    # conversation and its members, but dropping the ride first is still what
    # keeps `_recreate_enum`'s cast-back from meeting a row that uses a value
    # about to disappear.
    op.drop_table("group_ride_participants")
    op.drop_index("uq_conversations_group_ride_channel", table_name="conversations")
    op.drop_constraint("ck_conversations_kind_binding", "conversations", type_="check")
    # Rows whose kind was 'group_ride' are removed by the group_rides cascade in
    # the caller order below, but this migration must be safe standalone, so it
    # removes any remainder explicitly before narrowing the enum.
    op.execute(sa.text("DELETE FROM conversations WHERE kind::text = 'group_ride'"))
    op.drop_column("conversations", "group_ride_id")
    op.create_check_constraint("ck_conversations_kind_team", "conversations", _ORIGINAL_BINDING_CHECK)
    op.drop_table("group_rides")

    # Phase 9 notification rows reference a ride by a plain UUID, not an FK, so
    # the group_rides cascade above does NOT remove them. They must go before
    # the enum is rebuilt or the cast-back hits an invalid label and the
    # downgrade fails on exactly the installs that used the feature.
    op.execute(
        sa.text(
            "DELETE FROM notifications WHERE type IN "
            "('group_ride_invitation', 'group_ride_accepted', 'group_ride_started', "
            "'chat_message_group_ride')"
        )
    )

    # Dropping the enum values requires rebuilding the types.
    _recreate_enum("conversation_kind", "conversations", "kind", ["team", "direct"])
    _recreate_enum(
        "notification_type",
        "notifications",
        "type",
        [
            "friend_request",
            "friend_request_accepted",
            "team_invitation",
            "team_join_request",
            "team_member_removed",
            "team_archived",
            "chat_message",
            "chat_message_team",
            "system",
        ],
    )

    _participant_status.drop(op.get_bind(), checkfirst=True)
    _role.drop(op.get_bind(), checkfirst=True)
    _status.drop(op.get_bind(), checkfirst=True)

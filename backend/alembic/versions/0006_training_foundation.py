"""Phase 6: training foundation — profiles, FTP provenance, derived activity
metrics, daily load, and deterministic workout prescriptions (ADR-10).

Additive only: existing rides and points stay valid, and `ride_points` only
gains three nullable sensor columns. Calculation versions are seeded here as an
immutable record of what each version meant when it was released; a drift test
asserts this seed still matches the code registry in
`app.services.training_calc.CALCULATION_VERSIONS`.
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0006_training_foundation"
down_revision = "0005_routes"

# create_type=False: created/dropped explicitly below so two tables sharing
# `ftp_source` (ftp_records + training_profiles + training_activities) never race
# on CREATE TYPE.
_ftp_source = postgresql.ENUM(
    "manual",
    "test_20min",
    "test_ramp",
    "imported",
    "estimated",
    name="ftp_source",
    create_type=False,
)
_workout_status = postgresql.ENUM(
    "draft", "active", "archived", name="workout_status", create_type=False
)
_workout_step_type = postgresql.ENUM(
    "warmup",
    "steady",
    "interval",
    "recovery",
    "cooldown",
    name="workout_step_type",
    create_type=False,
)

# ---------------------------------------------------------------------------
# Seed: training_calculation_versions (immutable per release, see ADR-10 §2)
# ---------------------------------------------------------------------------

_SEED_AT = "2026-09-28 00:00:00+00"

_CALC_VERSION_ROWS = [
    {
        "version": "coggan_7zone_v1",
        "kind": "zones",
        "title": "Power zones (Coggan, 7 zones)",
        "summary": "Zone boundaries as a fraction of FTP.",
        "params": '{"boundaries": [0.55, 0.75, 0.9, 1.05, 1.2, 1.5], "reference": "ftp_w"}',
    },
    {
        "version": "hremax_5zone_v1",
        "kind": "zones",
        "title": "Heart rate zones (% of HRmax, 5 zones)",
        "summary": "Zone boundaries as a fraction of maximum heart rate.",
        "params": '{"boundaries": [0.6, 0.7, 0.8, 0.9], "reference": "max_hr_bpm"}',
    },
    {
        "version": "karvonen_5zone_v1",
        "kind": "zones",
        "title": "Heart rate zones (Karoven/HRR, 5 zones)",
        "summary": "Zone boundaries as a fraction of heart rate reserve (max - resting).",
        "params": ('{"boundaries": [0.6, 0.7, 0.8, 0.9], "reference": "heart_rate_reserve_bpm"}'),
    },
    {
        "version": "np_30s_v1",
        "kind": "metric",
        "title": "Normalized power (30 s rolling)",
        "summary": "Fourth-power mean of 30 s rolling average power, time-weighted.",
        "params": ('{"window_s": 30.0, "max_sample_gap_s": 10.0, "requires_full_window": true}'),
    },
    {
        "version": "if_ftp_v1",
        "kind": "metric",
        "title": "Intensity factor",
        "summary": "normalized_power / effective_ftp.",
        "params": '{"requires": ["normalized_power", "effective_ftp"]}',
    },
    {
        "version": "tss_style_v1",
        "kind": "load",
        "title": "Power training load (TSS-style)",
        "summary": (
            "duration_hours * IF^2 * 100. CycleCoach-specific formula; not "
            "TrainingPeaks TSS and not comparable with it."
        ),
        "params": '{"formula": "power_seconds/3600 * intensity_factor^2 * 100"}',
    },
    {
        "version": "trimp_5zone_v1",
        "kind": "load",
        "title": "Heart rate training load (5-zone TRIMP-style)",
        "summary": "Minutes in zone x zone weight (1..5). Never summed with power load.",
        "params": '{"weights": [1, 2, 3, 4, 5]}',
    },
    {
        "version": "ftp_20min_095_v1",
        "kind": "ftp",
        "title": "FTP from a 20 minute test",
        "summary": "0.95 x best 20 minute average power (fully covered window).",
        "params": '{"factor": 0.95, "window_s": 1200, "min_window_s": 600}',
    },
    {
        "version": "ftp_ramp_095_v1",
        "kind": "ftp",
        "title": "FTP from a ramp test (approximation)",
        "summary": (
            "0.95 x best 10 minute average power. Explicitly an approximation for "
            "a 3x10 min ramp protocol; a 20 minute test is preferred."
        ),
        "params": ('{"factor": 0.95, "window_s": 600, "min_window_s": 480, "approximation": true}'),
    },
    {
        "version": "ewma_42_7_v1",
        "kind": "load",
        "title": "Chronic/acute load (42/7 day EWMA)",
        "summary": "Exponentially weighted load. Fitness proxy, not a medical measure.",
        "params": '{"ctl_days": 42, "atl_days": 7, "tsb": "ctl_prev - atl_prev"}',
    },
    {
        "version": "load_delta_7d_v1",
        "kind": "signal",
        "title": "Load-change recovery signal",
        "summary": "7 day load compared with the preceding 7 days; +/-20% band.",
        "params": '{"window_days": 7, "increase_ratio": 1.2, "decrease_ratio": 0.8}',
    },
    {
        "version": "activity_analysis_v1",
        "kind": "manifest",
        "title": "Activity analysis manifest",
        "summary": "Bundle of the versions used to produce one activity's metrics.",
        "params": (
            '{"power_zones": "coggan_7zone_v1", "hr_max_zones": "hremax_5zone_v1", '
            '"hrr_zones": "karvonen_5zone_v1", "normalized_power": "np_30s_v1", '
            '"intensity_factor": "if_ftp_v1", "power_load": "tss_style_v1", '
            '"hr_load": "trimp_5zone_v1"}'
        ),
    },
]


def upgrade() -> None:
    _ftp_source.create(op.get_bind(), checkfirst=True)
    _workout_status.create(op.get_bind(), checkfirst=True)
    _workout_step_type.create(op.get_bind(), checkfirst=True)

    # --- registry mirror (no FKs) ------------------------------------------
    op.create_table(
        "training_calculation_versions",
        sa.Column("version", sa.String(48), primary_key=True),
        sa.Column("kind", sa.String(24), nullable=False),
        sa.Column("title", sa.String(120), nullable=False),
        sa.Column("summary", sa.String(400), nullable=False),
        sa.Column("params", postgresql.JSONB, nullable=False),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    for row in _CALC_VERSION_ROWS:
        op.execute(
            sa.text(
                "INSERT INTO training_calculation_versions "
                "(version, kind, title, summary, params, is_active, created_at) "
                "VALUES (:version, :kind, :title, :summary, CAST(:params AS jsonb), "
                "true, CAST(:created_at AS timestamptz))"
            ).bindparams(
                version=row["version"],
                kind=row["kind"],
                title=row["title"],
                summary=row["summary"],
                params=row["params"],
                created_at=_SEED_AT,
            )
        )

    # --- ftp_records (append-only) -----------------------------------------
    op.create_table(
        "ftp_records",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("source", _ftp_source, nullable=False),
        sa.Column("value_w", sa.Numeric(6, 1), nullable=False),
        sa.Column("effective_at", sa.Date, nullable=False),
        sa.Column("evidence", postgresql.JSONB),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_ftp_records_user_id", "ftp_records", ["user_id"])
    op.create_index("ix_ftp_records_user_effective", "ftp_records", ["user_id", "effective_at"])
    op.create_index("ix_ftp_records_user_created", "ftp_records", ["user_id", "created_at"])

    # --- training_profiles (1:1) -------------------------------------------
    op.create_table(
        "training_profiles",
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("ftp_w", sa.Numeric(6, 1)),
        sa.Column("ftp_source", _ftp_source),
        sa.Column(
            "ftp_record_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("ftp_records.id", ondelete="SET NULL"),
        ),
        sa.Column("max_hr_bpm", sa.Numeric(5, 1)),
        sa.Column("resting_hr_bpm", sa.Numeric(5, 1)),
        sa.Column("hr_zone_model", sa.String(8)),
        sa.Column("timezone", sa.String(64)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )

    # --- training_activities + zones ---------------------------------------
    op.create_table(
        "training_activities",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "ride_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("rides.id", ondelete="CASCADE"),
        ),
        sa.Column("local_date", sa.Date, nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True)),
        sa.Column("elapsed_seconds", sa.Integer, nullable=False, server_default="0"),
        sa.Column("moving_seconds", sa.Integer, nullable=False, server_default="0"),
        sa.Column("distance_m", sa.Numeric(12, 2)),
        sa.Column("elevation_gain_m", sa.Numeric(10, 2)),
        sa.Column("analysis_version", sa.String(48), nullable=False),
        sa.Column("insufficient_data", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("sample_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("segment_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("has_power", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("has_heart_rate", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("has_cadence", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("analyzed_seconds", sa.Numeric(10, 1), nullable=False, server_default="0"),
        sa.Column("power_seconds", sa.Numeric(10, 1), nullable=False, server_default="0"),
        sa.Column("hr_seconds", sa.Numeric(10, 1), nullable=False, server_default="0"),
        sa.Column("np_seconds", sa.Numeric(10, 1), nullable=False, server_default="0"),
        sa.Column("average_power_w", sa.Numeric(7, 1)),
        sa.Column("max_power_w", sa.Numeric(7, 1)),
        sa.Column("normalized_power_w", sa.Numeric(7, 1)),
        sa.Column("intensity_factor", sa.Numeric(6, 4)),
        sa.Column("power_load", sa.Numeric(9, 1)),
        sa.Column("average_hr_bpm", sa.Numeric(5, 1)),
        sa.Column("max_hr_bpm", sa.Numeric(5, 1)),
        sa.Column("average_cadence_rpm", sa.Numeric(5, 1)),
        sa.Column("hr_load", sa.Numeric(9, 1)),
        sa.Column("hr_zone_model", sa.String(8)),
        sa.Column("effective_ftp_w", sa.Numeric(6, 1)),
        sa.Column("ftp_source", _ftp_source),
        sa.Column("ftp_basis", sa.String(16), nullable=False, server_default="unavailable"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_training_activities_user_id", "training_activities", ["user_id"])
    op.create_index(
        "ix_training_activities_user_date", "training_activities", ["user_id", "local_date"]
    )
    op.create_index(
        "ix_training_activities_user_started", "training_activities", ["user_id", "started_at"]
    )
    # One activity per ride; manual activities carry no ride_id.
    op.create_index(
        "uq_training_activities_user_ride",
        "training_activities",
        ["user_id", "ride_id"],
        unique=True,
        postgresql_where=sa.text("ride_id IS NOT NULL"),
    )

    op.create_table(
        "training_activity_zones",
        sa.Column(
            "activity_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("training_activities.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("kind", sa.String(8), primary_key=True),
        sa.Column("zone", sa.Integer, primary_key=True),
        sa.Column("seconds", sa.Numeric(10, 1), nullable=False, server_default="0"),
    )

    # --- training_loads (one row per rider per local date) ------------------
    op.create_table(
        "training_loads",
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("local_date", sa.Date, primary_key=True),
        sa.Column("power_load", sa.Numeric(9, 1), nullable=False, server_default="0"),
        sa.Column("hr_load", sa.Numeric(9, 1), nullable=False, server_default="0"),
        sa.Column("ctl", sa.Numeric(9, 2), nullable=False, server_default="0"),
        sa.Column("atl", sa.Numeric(9, 2), nullable=False, server_default="0"),
        sa.Column("tsb", sa.Numeric(9, 2), nullable=False, server_default="0"),
        sa.Column("load_version", sa.String(48), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )

    # --- workouts + steps ---------------------------------------------------
    op.create_table(
        "workouts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("description", sa.String(2000)),
        sa.Column("discipline", sa.String(32), nullable=False, server_default="road"),
        sa.Column("goal", sa.String(64)),
        sa.Column("status", _workout_status, nullable=False, server_default="draft"),
        sa.Column("version", sa.Integer, nullable=False, server_default="1"),
        sa.Column("target_duration_s", sa.Integer),
        sa.Column("target_load", sa.Numeric(8, 1)),
        sa.Column("intensity_note", sa.String(200)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_workouts_user_id", "workouts", ["user_id"])
    op.create_index("ix_workouts_user_status", "workouts", ["user_id", "status"])

    op.create_table(
        "workout_steps",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column(
            "workout_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workouts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("seq", sa.Integer, nullable=False),
        sa.Column("step_type", _workout_step_type, nullable=False),
        sa.Column("label", sa.String(120), nullable=False),
        sa.Column("duration_s", sa.Integer, nullable=False),
        sa.Column("repeat_count", sa.Integer, nullable=False, server_default="1"),
        sa.Column("target_zone", sa.Integer),
        sa.Column("target_power_low_w", sa.Numeric(6, 1)),
        sa.Column("target_power_high_w", sa.Numeric(6, 1)),
        sa.Column("target_hr_low_bpm", sa.Numeric(5, 1)),
        sa.Column("target_hr_high_bpm", sa.Numeric(5, 1)),
        sa.UniqueConstraint("workout_id", "seq", name="uq_workout_steps_workout_seq"),
    )
    op.create_index("ix_workout_steps_workout_id", "workout_steps", ["workout_id"])

    # --- ride_points: optional sensor samples (ADR-10 §6) ------------------
    op.add_column("ride_points", sa.Column("power_w", sa.Numeric(6, 1)))
    op.add_column("ride_points", sa.Column("hr_bpm", sa.Numeric(5, 1)))
    op.add_column("ride_points", sa.Column("cadence_rpm", sa.Numeric(5, 1)))


def downgrade() -> None:
    op.drop_column("ride_points", "cadence_rpm")
    op.drop_column("ride_points", "hr_bpm")
    op.drop_column("ride_points", "power_w")

    op.drop_index("ix_workout_steps_workout_id", table_name="workout_steps")
    op.drop_table("workout_steps")
    op.drop_index("ix_workouts_user_status", table_name="workouts")
    op.drop_index("ix_workouts_user_id", table_name="workouts")
    op.drop_table("workouts")

    op.drop_table("training_loads")

    op.drop_table("training_activity_zones")
    op.drop_index("uq_training_activities_user_ride", table_name="training_activities")
    op.drop_index("ix_training_activities_user_started", table_name="training_activities")
    op.drop_index("ix_training_activities_user_date", table_name="training_activities")
    op.drop_index("ix_training_activities_user_id", table_name="training_activities")
    op.drop_table("training_activities")

    op.drop_table("training_profiles")

    op.drop_index("ix_ftp_records_user_created", table_name="ftp_records")
    op.drop_index("ix_ftp_records_user_effective", table_name="ftp_records")
    op.drop_index("ix_ftp_records_user_id", table_name="ftp_records")
    op.drop_table("ftp_records")

    op.drop_table("training_calculation_versions")

    _workout_step_type.drop(op.get_bind(), checkfirst=True)
    _workout_status.drop(op.get_bind(), checkfirst=True)
    _ftp_source.drop(op.get_bind(), checkfirst=True)

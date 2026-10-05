"""Alembic env — models via app.models; ALEMBIC_DB_URL overrides ini."""

import os
from logging.config import fileConfig

from sqlalchemy import pool
from sqlalchemy.engine import create_engine

import app.models  # noqa: F401  (register tables in metadata)
from alembic import context
from app.db.base import Base

config = context.config
if os.environ.get("ALEMBIC_DB_URL"):
    config.set_main_option("sqlalchemy.url", os.environ["ALEMBIC_DB_URL"])
if config.config_file_name:
    try:
        fileConfig(config.config_file_name)
    except KeyError:
        pass  # Phase 1 minimal ini has no logging sections; fine.

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(url=config.get_main_option("sqlalchemy.url"), literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    url = config.get_main_option("sqlalchemy.url")
    if not url:
        raise RuntimeError(
            "sqlalchemy.url is not set; set it in alembic.ini or via ALEMBIC_DB_URL"
        )
    engine = create_engine(
        url.replace("+asyncpg", ""),
        poolclass=pool.NullPool,
    )
    with engine.connect() as connection:
        context.configure(connection=connection)
        with context.begin_transaction():
            context.run_migrations()


run_migrations_offline() if context.is_offline_mode() else run_migrations_online()

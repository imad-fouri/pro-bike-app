"""DB foundation — engine + session only. No domain tables in Phase 1."""

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings

engine = create_async_engine(
    settings.DATABASE_URL,
    pool_pre_ping=True,
    # Keep bound parameter VALUES out of SQLAlchemy's rendered statement.
    #
    # An IntegrityError renders as the statement plus `[parameters: {...}]`, and
    # this schema puts an email address and an Argon2id password hash in those
    # parameters. Any `log.exception(...)` of a database error would therefore
    # write both into the log aggregator.
    #
    # `hide_parameters` covers what SQLAlchemy itself formats. It does NOT cover
    # an explicit `str(exc)` elsewhere, so the unhandled-exception handler also
    # logs `type(exc).__name__` — two independent layers, because either one alone
    # can be undone by a future line of code.
    #
    # Debugging cost: a development traceback shows `%(name)s` placeholders rather
    # than values. That trade is deliberate and the right way round.
    hide_parameters=True,
)
SessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def get_db() -> AsyncGenerator[AsyncSession, None]:  # FastAPI dependency
    async with SessionLocal() as session:
        yield session


async def check_db() -> bool:
    from sqlalchemy import text

    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True
    except Exception:  # noqa: BLE001 - health check must never raise
        return False

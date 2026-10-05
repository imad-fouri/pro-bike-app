import os

os.environ.setdefault("ENVIRONMENT", "test")

import asyncpg
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

# Import models so metadata is complete.
import app.models  # noqa: F401  (register tables in metadata)
from app.db.base import Base
from app.db.session import get_db
from app.main import create_app

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://cyclecoach:cyclecoach@localhost:5432/cyclecoach_test",
)


def _parse(url: str) -> tuple[str, str]:
    # "postgresql+asyncpg://user:pass@host:port/db" -> (server_url, dbname)
    head, dbname = url.rsplit("/", 1)
    return head, dbname


@pytest.fixture(scope="session")
def _test_db_ready():
    import asyncio

    async def _make() -> None:
        head, dbname = _parse(TEST_DATABASE_URL)
        conn = await asyncpg.connect(head.replace("+asyncpg", "") + "/postgres")
        try:
            exists = await conn.fetchval("SELECT 1 FROM pg_database WHERE datname=$1", dbname)
            if not exists:
                await conn.execute(f'CREATE DATABASE "{dbname}"')
        finally:
            await conn.close()
        engine = create_async_engine(TEST_DATABASE_URL)
        async with engine.begin() as conn2:
            await conn2.run_sync(Base.metadata.drop_all)
            await conn2.run_sync(Base.metadata.create_all)
        await engine.dispose()

    asyncio.run(_make())
    return TEST_DATABASE_URL


@pytest.fixture
async def client(test_app, _test_db_ready):
    engine = create_async_engine(TEST_DATABASE_URL)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def _override():
        async with factory() as session:
            yield session

    test_app.dependency_overrides[get_db] = _override
    async with AsyncClient(transport=ASGITransport(app=test_app), base_url="http://test") as c:
        yield c
    test_app.dependency_overrides.clear()
    async with engine.begin() as conn:
        await conn.execute(text("TRUNCATE users CASCADE"))
    await engine.dispose()


@pytest_asyncio.fixture
async def db_session_factory(_test_db_ready):
    """A session factory that talks to the same test database as `client`.

    Used by tests that need to reach around the API to arrange state the API
    deliberately will not produce — ageing a message past its edit window, for
    instance, since no request can legitimately do that.
    """
    engine = create_async_engine(TEST_DATABASE_URL)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory
    await engine.dispose()


@pytest.fixture(autouse=True)
def _clear_rate_limits():
    from app.core import rate_limit

    rate_limit._buckets.clear()
    yield
    rate_limit._buckets.clear()


@pytest.fixture
def test_app():
    return create_app()


@pytest.fixture
def outbox():
    from app.services.email import email_service

    email_service.outbox.clear()
    return email_service.outbox

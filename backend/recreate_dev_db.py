"""Drop and recreate the local dev database, then migrate it from zero.

Used to prove the Phase 8.4 migration applies to a virgin database, not only
on top of an existing one. Connects to the `postgres` maintenance database
because a server cannot drop the database it is itself attached to.
"""

import asyncio

import asyncpg

DSN = "postgresql://cyclecoach:cyclecoach@localhost:5432"


async def main() -> None:
    conn = await asyncpg.connect(database="postgres", dsn=DSN)
    try:
        await conn.execute("DROP DATABASE IF EXISTS cyclecoach WITH (FORCE)")
        await conn.execute("CREATE DATABASE cyclecoach")
        print("recreated cyclecoach")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())

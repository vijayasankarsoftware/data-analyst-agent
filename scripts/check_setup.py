"""Phase 0 smoke test: both databases are up, and the demo source's read-only login can't write."""

import asyncio

import psycopg

from analyst.config import get_settings


async def main() -> None:
    settings = get_settings()

    async with await psycopg.AsyncConnection.connect(settings.app_db_conninfo) as conn:
        cur = await conn.execute("SELECT extversion FROM pg_extension WHERE extname = 'vector'")
        row = await cur.fetchone()
        print(f"app-db          ok (pgvector {row[0] if row else 'MISSING'})")

    async with await psycopg.AsyncConnection.connect(settings.demo_warehouse_conninfo) as conn:
        # We don't hardcode table names: discover them, like the agent will.
        cur = await conn.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public' ORDER BY table_name"
        )
        tables = [r[0] for r in await cur.fetchall()]
        print(f"demo-warehouse  ok, discovered tables: {', '.join(tables)}")

        try:
            await conn.execute("DELETE FROM orders")
        except psycopg.Error as exc:
            print(f"\nWrite blocked as expected -> {type(exc).__name__}: {exc}".rstrip())
        else:
            raise SystemExit("DANGER: the read-only role was able to write!")


if __name__ == "__main__":
    asyncio.run(main())

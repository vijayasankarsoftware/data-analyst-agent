"""Tiny forward-only migrator for the app DB.

Each `migrations/NNNN_name.sql` file runs once, in order, inside a transaction, and is
recorded in `schema_migrations`. An advisory lock stops the API and MCP server from
migrating concurrently when they boot at the same time.

(A bigger project would use Alembic; this shows the core idea in 40 lines.)
"""

import asyncio
import logging
from importlib import resources
from typing import Any

from analyst.config import get_settings
from analyst.db.pool import get_app_pool

log = logging.getLogger(__name__)
_LOCK_ID = 727_001


async def migrate() -> list[str]:
    pool = await get_app_pool()
    files = sorted(
        (f for f in resources.files("analyst.db.migrations").iterdir() if f.name.endswith(".sql")),
        key=lambda f: f.name,
    )
    applied_now: list[str] = []
    async with pool.connection() as conn:
        async with conn.transaction():
            await conn.execute("SELECT pg_advisory_xact_lock(%s)", [_LOCK_ID])
            await conn.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations ("
                " version text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())"
            )
            cur = await conn.execute("SELECT version FROM schema_migrations")
            done = {row["version"] for row in await cur.fetchall()}
            for f in files:
                version = f.name.removesuffix(".sql")
                if version in done:
                    continue
                log.info("applying migration %s", version)
                # prepare=False: multi-statement scripts can't be server-side prepared.
                await conn.execute(f.read_text(), prepare=False)  # type: ignore[arg-type]
                await conn.execute("INSERT INTO schema_migrations (version) VALUES (%s)", [version])
                applied_now.append(version)
        await _check_embedding_dim(conn)
    return applied_now


async def _check_embedding_dim(conn: Any) -> None:
    """Fail at boot (not on the first upload) if EMBEDDING_DIM and the DB columns disagree."""
    cur = await conn.execute(
        "SELECT atttypmod FROM pg_attribute WHERE attrelid = 'doc_chunks'::regclass AND attname = 'embedding'"
    )
    row = await cur.fetchone()
    expected = get_settings().embedding_dim
    if row and row["atttypmod"] != expected:
        raise RuntimeError(
            f"EMBEDDING_DIM={expected} but the database stores vector({row['atttypmod']}). "
            "Add a migration that changes the vector size, or fix EMBEDDING_DIM."
        )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print("applied:", asyncio.run(migrate()) or "nothing (up to date)")

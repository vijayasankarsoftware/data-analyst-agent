"""App-DB persistence for data sources and their schema snapshots."""

from dataclasses import dataclass
from typing import Any

from psycopg.types.json import Jsonb
from pydantic import BaseModel

from analyst.datasources.base import TableInfo
from analyst.db.pool import get_app_pool, to_vector
from analyst.security.crypto import decrypt_json, encrypt_json


class SourceRecord(BaseModel):
    id: str
    workspace_id: str
    name: str
    kind: str
    description: str
    status: str
    error: str | None = None
    warnings: list[str] = []
    table_count: int = 0


@dataclass
class SourceWithCredentials:
    record: SourceRecord
    credentials: dict[str, Any]


_SELECT = """
SELECT s.id::text, s.workspace_id::text, s.name, s.kind, s.description, s.status, s.error, s.warnings,
       (SELECT count(*) FROM schema_tables t WHERE t.source_id = s.id)::int AS table_count
FROM data_sources s
"""


def _record(row: dict[str, Any]) -> SourceRecord:
    return SourceRecord(**{k: v for k, v in row.items() if k != "credentials"})


async def create_source(workspace_id: str, name: str, kind: str, credentials: dict[str, Any], description: str) -> str:
    pool = await get_app_pool()
    async with pool.connection() as conn:
        cur = await conn.execute(
            "INSERT INTO data_sources (workspace_id, name, kind, credentials, description)"
            " VALUES (%s, %s, %s, %s, %s) RETURNING id::text",
            [workspace_id, name, kind, encrypt_json(credentials), description],
        )
        row = await cur.fetchone()
    assert row is not None
    return str(row["id"])


async def set_status(
    source_id: str, status: str, *, error: str | None = None, warnings: list[str] | None = None
) -> None:
    pool = await get_app_pool()
    async with pool.connection() as conn:
        await conn.execute(
            "UPDATE data_sources SET status = %s, error = %s,"
            " warnings = coalesce(%s, warnings),"
            " refreshed_at = CASE WHEN %s = 'ready' THEN now() ELSE refreshed_at END"
            " WHERE id = %s",
            [status, error, warnings, status, source_id],
        )


async def list_sources(workspace_id: str) -> list[SourceRecord]:
    pool = await get_app_pool()
    async with pool.connection() as conn:
        cur = await conn.execute(_SELECT + " WHERE s.workspace_id = %s ORDER BY s.created_at", [workspace_id])
        return [_record(r) for r in await cur.fetchall()]


async def get_source(workspace_id: str, source_id: str) -> SourceWithCredentials:
    """Workspace-scoped lookup: a source id from another workspace behaves as 'not found'."""
    pool = await get_app_pool()
    async with pool.connection() as conn:
        cur = await conn.execute(
            _SELECT.replace("SELECT ", "SELECT s.credentials, ", 1) + " WHERE s.workspace_id = %s AND s.id::text = %s",
            [workspace_id, source_id],
        )
        row = await cur.fetchone()
    if row is None:
        raise LookupError(f"data source {source_id} not found")
    return SourceWithCredentials(record=_record(row), credentials=decrypt_json(row["credentials"]))


async def delete_source(workspace_id: str, source_id: str) -> bool:
    pool = await get_app_pool()
    async with pool.connection() as conn:
        cur = await conn.execute(
            "DELETE FROM data_sources WHERE workspace_id = %s AND id::text = %s", [workspace_id, source_id]
        )
        return cur.rowcount > 0


async def save_schema(source_id: str, tables: list[TableInfo], embeddings: list[list[float]]) -> None:
    pool = await get_app_pool()
    async with pool.connection() as conn, conn.transaction():
        await conn.execute("DELETE FROM schema_tables WHERE source_id = %s", [source_id])
        async with conn.cursor() as cur:
            await cur.executemany(
                "INSERT INTO schema_tables (source_id, schema_name, table_name, info, pii_columns, embedding)"
                " VALUES (%s, %s, %s, %s, %s, %s::vector)",
                [
                    (source_id, t.schema_name, t.name, Jsonb(t.model_dump()), t.pii_columns, to_vector(e))
                    for t, e in zip(tables, embeddings, strict=True)
                ],
            )


async def load_tables(source_id: str, names: list[str] | None = None) -> list[TableInfo]:
    """Load the snapshot. `names` may be qualified ('public.orders') or bare ('orders')."""
    pool = await get_app_pool()
    async with pool.connection() as conn:
        cur = await conn.execute(
            "SELECT info FROM schema_tables WHERE source_id = %s ORDER BY schema_name, table_name",
            [source_id],
        )
        tables = [TableInfo.model_validate(r["info"]) for r in await cur.fetchall()]
    if names is None:
        return tables
    wanted = {n.lower() for n in names}
    return [t for t in tables if t.qualified_name.lower() in wanted or t.name.lower() in wanted]


async def search_tables(source_id: str, query_embedding: list[float], k: int) -> list[str]:
    pool = await get_app_pool()
    async with pool.connection() as conn:
        cur = await conn.execute(
            "SELECT schema_name || '.' || table_name AS q FROM schema_tables WHERE source_id = %s"
            " ORDER BY embedding <=> %s::vector LIMIT %s",
            [source_id, to_vector(query_embedding), k],
        )
        return [r["q"] for r in await cur.fetchall()]

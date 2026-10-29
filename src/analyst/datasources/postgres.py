"""PostgreSQL connector.

Safety does NOT depend on the user giving us a read-only login:
every connection opens `BEGIN READ ONLY`, sets a transaction-local statement
timeout, fetches at most max_rows+1 rows, and always rolls back.
"""

import json
import time
from typing import Any

import psycopg
from psycopg import sql as pgsql
from pydantic import BaseModel

from analyst.datasources.base import (
    ColumnInfo,
    DataSource,
    ForeignKey,
    QueryResult,
    TableInfo,
    jsonable,
)
from analyst.datasources.pii import is_pii_column, mask_value

_SYSTEM_SCHEMAS = ("pg_catalog", "information_schema")

_TABLES_SQL = """
SELECT n.nspname, c.relname, obj_description(c.oid, 'pg_class'), greatest(c.reltuples, 0)::bigint
FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE c.relkind IN ('r', 'v', 'm', 'p', 'f') AND NOT c.relispartition
  AND n.nspname NOT IN ('pg_catalog', 'information_schema') AND n.nspname NOT LIKE 'pg_toast%%'
  AND has_table_privilege(c.oid, 'SELECT')
ORDER BY 1, 2
LIMIT %s
"""

_COLUMNS_SQL = """
SELECT n.nspname, c.relname, a.attname, format_type(a.atttypid, a.atttypmod),
       NOT a.attnotnull, col_description(c.oid, a.attnum)
FROM pg_attribute a
JOIN pg_class c ON c.oid = a.attrelid JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE a.attnum > 0 AND NOT a.attisdropped AND c.relkind IN ('r', 'v', 'm', 'p', 'f')
  AND n.nspname NOT IN ('pg_catalog', 'information_schema') AND n.nspname NOT LIKE 'pg_toast%%'
ORDER BY 1, 2, a.attnum
"""

_KEYS_SQL = """
SELECT n.nspname, c.relname, con.contype, a.attname, rn.nspname, rc.relname, ra.attname
FROM pg_constraint con
JOIN pg_class c ON c.oid = con.conrelid JOIN pg_namespace n ON n.oid = c.relnamespace
CROSS JOIN LATERAL unnest(con.conkey, con.confkey) AS k(attnum, refattnum)
JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum = k.attnum
LEFT JOIN pg_class rc ON rc.oid = con.confrelid
LEFT JOIN pg_namespace rn ON rn.oid = rc.relnamespace
LEFT JOIN pg_attribute ra ON ra.attrelid = con.confrelid AND ra.attnum = k.refattnum
WHERE con.contype IN ('p', 'f') AND n.nspname NOT IN ('pg_catalog', 'information_schema')
"""

_PRIVILEGES_SQL = """
SELECT r.rolsuper,
       has_database_privilege(current_database(), 'CREATE'),
       EXISTS (SELECT 1 FROM information_schema.table_privileges
               WHERE grantee = current_user
                 AND privilege_type IN ('INSERT', 'UPDATE', 'DELETE', 'TRUNCATE')),
       current_setting('default_transaction_read_only')
FROM pg_roles r WHERE r.rolname = current_user
"""


class PostgresCredentials(BaseModel):
    dsn: str  # e.g. postgresql://analyst_ro:pw@host:5432/db


class PostgresSource(DataSource):
    kind = "postgres"
    dialect = "postgres"

    def __init__(self, credentials: dict[str, Any]) -> None:
        self._creds = PostgresCredentials.model_validate(credentials)

    async def _connect(self) -> psycopg.AsyncConnection[Any]:
        conn = await psycopg.AsyncConnection.connect(
            self._creds.dsn,
            connect_timeout=10,
            autocommit=False,
            application_name="data-analyst-agent",
        )
        # Every transaction on this connection starts as BEGIN READ ONLY.
        await conn.set_read_only(True)
        return conn

    async def test_connection(self) -> list[str]:
        warnings: list[str] = []
        async with await self._connect() as conn:
            cur = await conn.execute(_PRIVILEGES_SQL)
            row = await cur.fetchone()
            await conn.rollback()
        if row:
            superuser, can_create, can_write, ro_default = row
            if superuser:
                warnings.append("This login is a SUPERUSER. Use a dedicated read-only login.")
            if can_write:
                warnings.append("This login can modify tables. Use a read-only login.")
            if can_create:
                warnings.append("This login can create objects in the database.")
            if ro_default != "on":
                warnings.append("default_transaction_read_only is off for this login.")
        if warnings:
            warnings.append("The agent still enforces read-only sessions, but least privilege is safer.")
        return warnings

    async def introspect(self, *, max_tables: int = 500, sample_rows: int = 3) -> list[TableInfo]:
        async with await self._connect() as conn:
            await conn.execute("SELECT set_config('statement_timeout', '30000', true)")
            tables_rows = await (await conn.execute(_TABLES_SQL, [max_tables])).fetchall()
            col_rows = await (await conn.execute(_COLUMNS_SQL)).fetchall()
            key_rows = await (await conn.execute(_KEYS_SQL)).fetchall()

            tables: dict[tuple[str, str], TableInfo] = {
                (s, t): TableInfo(schema_name=s, name=t, comment=cmt, columns=[], row_estimate=est)
                for s, t, cmt, est in tables_rows
            }
            for s, t, col, typ, nullable, cmt in col_rows:
                if (s, t) in tables:
                    tables[(s, t)].columns.append(
                        ColumnInfo(
                            name=col,
                            data_type=typ,
                            nullable=nullable,
                            comment=cmt,
                            is_pii=is_pii_column(col),
                        )
                    )
            for s, t, contype, col, rs, rt, rcol in key_rows:
                info = tables.get((s, t))
                if info is None:
                    continue
                if contype == "p":
                    info.primary_key.append(col)
                elif rt is not None:
                    info.foreign_keys.append(ForeignKey(column=col, ref_table=f"{rs}.{rt}", ref_column=rcol))

            # A few sample rows teach the LLM value formats ('delivered' vs 'DELIVERED').
            # PII columns are masked so personal data never reaches the prompt.
            for info in list(tables.values())[:200] if sample_rows else []:
                query = pgsql.SQL("SELECT * FROM {}.{} LIMIT {}").format(
                    pgsql.Identifier(info.schema_name),
                    pgsql.Identifier(info.name),
                    pgsql.Literal(sample_rows),
                )
                try:
                    cur = await conn.execute(query)
                    cols = [d.name for d in cur.description or []]
                    pii = set(info.pii_columns)
                    info.sample_rows = [
                        {c: (mask_value(v) if c in pii else _short(jsonable(v))) for c, v in zip(cols, r, strict=False)}
                        for r in await cur.fetchall()
                    ]
                except psycopg.Error:
                    await conn.rollback()
                    await conn.execute("SELECT set_config('statement_timeout', '30000', true)")
            await conn.rollback()
        return list(tables.values())

    async def run_readonly(self, sql: str, *, max_rows: int, timeout_ms: int) -> QueryResult:
        start = time.perf_counter()
        async with await self._connect() as conn:
            try:
                # Transaction-local: can't leak to other sessions, and the guard blocks set_config in user SQL.
                await conn.execute("SELECT set_config('statement_timeout', %s, true)", [str(timeout_ms)])
                cur = await conn.execute(sql, prepare=False)
                columns = [d.name for d in cur.description or []]
                rows = await cur.fetchmany(max_rows + 1)
            finally:
                await conn.rollback()
        truncated = len(rows) > max_rows
        rows = rows[:max_rows]
        return QueryResult(
            columns=columns,
            rows=[[jsonable(v) for v in r] for r in rows],
            row_count=len(rows),
            truncated=truncated,
            elapsed_ms=int((time.perf_counter() - start) * 1000),
        )

    async def estimate_rows(self, sql: str) -> int | None:
        async with await self._connect() as conn:
            try:
                await conn.execute("SELECT set_config('statement_timeout', '5000', true)")
                cur = await conn.execute(f"EXPLAIN (FORMAT JSON) {sql}", prepare=False)
                row = await cur.fetchone()
            except psycopg.Error:
                return None
            finally:
                await conn.rollback()
        if not row:
            return None
        plan = row[0] if not isinstance(row[0], str) else json.loads(row[0])

        # The top node is capped by our LIMIT, so use the LARGEST intermediate estimate:
        # that's what reflects how much data the query will actually churn through.
        def biggest(node: dict[str, Any]) -> int:
            return max([int(node.get("Plan Rows", 0))] + [biggest(c) for c in node.get("Plans", [])])

        return biggest(plan[0]["Plan"])


def _short(value: Any, limit: int = 60) -> Any:
    if isinstance(value, str) and len(value) > limit:
        return value[:limit] + "…"
    return value

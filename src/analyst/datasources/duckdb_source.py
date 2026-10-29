"""DuckDB connector: query CSV / Parquet files or a .duckdb database file.

This proves the agent is pluggable: a completely different engine, same interface.

Safety model for a file-reading engine:
- files must live inside settings.local_data_dir (no '/etc/passwd' sources),
- CSV/Parquet are loaded into an in-memory DB, then external access is DISABLED
  and the configuration LOCKED, so SQL can't read other files (read_csv('/...')),
- .duckdb files are opened read_only=True.
"""

import asyncio
import re
import time
from pathlib import Path
from typing import Any

import duckdb
from pydantic import BaseModel, Field

from analyst.config import get_settings
from analyst.datasources.base import ColumnInfo, DataSource, QueryResult, TableInfo, jsonable
from analyst.datasources.pii import is_pii_column, mask_value


class DuckDBCredentials(BaseModel):
    database: str | None = None  # path to a .duckdb file (relative to LOCAL_DATA_DIR)
    files: list[str] = Field(default_factory=list)  # CSV/Parquet paths (relative to LOCAL_DATA_DIR)


def _safe_path(raw: str) -> Path:
    root = get_settings().local_data_dir.resolve()
    path = (root / raw).resolve()
    if not path.is_relative_to(root):
        raise ValueError(f"path {raw!r} is outside the allowed data directory")
    if not path.exists():
        raise ValueError(f"file {raw!r} not found in the data directory")
    return path


def _table_name(path: Path) -> str:
    return re.sub(r"\W+", "_", path.stem).strip("_").lower() or "data"


class DuckDBSource(DataSource):
    kind = "duckdb"
    dialect = "duckdb"

    def __init__(self, credentials: dict[str, Any]) -> None:
        self._creds = DuckDBCredentials.model_validate(credentials)
        if not self._creds.database and not self._creds.files:
            raise ValueError("duckdb source needs 'database' or 'files'")

    def _connect(self) -> duckdb.DuckDBPyConnection:
        if self._creds.database:
            con = duckdb.connect(
                str(_safe_path(self._creds.database)), read_only=True, config={"enable_external_access": False}
            )
        else:
            con = duckdb.connect(":memory:")
            for raw in self._creds.files:
                path = _safe_path(raw)
                reader = "read_parquet" if path.suffix.lower() == ".parquet" else "read_csv_auto"
                con.execute(f'CREATE TABLE "{_table_name(path)}" AS SELECT * FROM {reader}(?)', [str(path)])
            con.execute("SET enable_external_access = false")
        con.execute("SET lock_configuration = true")
        return con

    async def test_connection(self) -> list[str]:
        def _test() -> None:
            con = self._connect()
            con.execute("SELECT 1").fetchall()
            con.close()

        await asyncio.to_thread(_test)
        return []

    async def introspect(self, *, max_tables: int = 500, sample_rows: int = 3) -> list[TableInfo]:
        def _introspect() -> list[TableInfo]:
            con = self._connect()
            try:
                tables = con.execute(
                    "SELECT table_schema, table_name FROM information_schema.tables "
                    "WHERE table_schema NOT IN ('information_schema', 'pg_catalog') "
                    "ORDER BY 1, 2 LIMIT ?",
                    [max_tables],
                ).fetchall()
                result = []
                for schema, name in tables:
                    cols = con.execute(
                        "SELECT column_name, data_type, is_nullable = 'YES' FROM information_schema.columns "
                        "WHERE table_schema = ? AND table_name = ? ORDER BY ordinal_position",
                        [schema, name],
                    ).fetchall()
                    info = TableInfo(
                        schema_name=schema,
                        name=name,
                        columns=[
                            ColumnInfo(name=c, data_type=t, nullable=n, is_pii=is_pii_column(c)) for c, t, n in cols
                        ],
                    )
                    count = con.execute(f'SELECT count(*) FROM "{schema}"."{name}"').fetchone()
                    info.row_estimate = int(count[0]) if count else None
                    if sample_rows:
                        cur = con.execute(f'SELECT * FROM "{schema}"."{name}" LIMIT {int(sample_rows)}')
                        names = [d[0] for d in cur.description or []]
                        pii = set(info.pii_columns)
                        info.sample_rows = [
                            {c: (mask_value(v) if c in pii else jsonable(v)) for c, v in zip(names, r, strict=False)}
                            for r in cur.fetchall()
                        ]
                    result.append(info)
                return result
            finally:
                con.close()

        return await asyncio.to_thread(_introspect)

    async def run_readonly(self, sql: str, *, max_rows: int, timeout_ms: int) -> QueryResult:
        con = await asyncio.to_thread(self._connect)
        start = time.perf_counter()

        def _run() -> tuple[list[str], list[tuple[Any, ...]]]:
            cur = con.execute(sql)
            return [d[0] for d in cur.description or []], cur.fetchmany(max_rows + 1)

        try:
            # DuckDB has no statement_timeout: run in a thread and interrupt it if it's too slow.
            columns, rows = await asyncio.wait_for(asyncio.to_thread(_run), timeout=timeout_ms / 1000)
        except TimeoutError:
            con.interrupt()
            raise TimeoutError(f"query exceeded {timeout_ms} ms") from None
        finally:
            con.close()
        truncated = len(rows) > max_rows
        rows = rows[:max_rows]
        return QueryResult(
            columns=columns,
            rows=[[jsonable(v) for v in r] for r in rows],
            row_count=len(rows),
            truncated=truncated,
            elapsed_ms=int((time.perf_counter() - start) * 1000),
        )

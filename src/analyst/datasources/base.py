"""The connector interface. Every pluggable database implements `DataSource`.

Adding MySQL/Snowflake/BigQuery = one new subclass + one line in `registry.py`.
Nothing else in the system knows which kind of database it's talking to; the
SQL agent only needs `dialect` (a sqlglot dialect name) to write the right SQL.
"""

import datetime as dt
import decimal
import uuid
from abc import ABC, abstractmethod
from typing import Any, ClassVar

from pydantic import BaseModel, Field


class ColumnInfo(BaseModel):
    name: str
    data_type: str
    nullable: bool = True
    comment: str | None = None
    is_pii: bool = False


class ForeignKey(BaseModel):
    column: str
    ref_table: str  # qualified "schema.table"
    ref_column: str


class TableInfo(BaseModel):
    schema_name: str
    name: str
    comment: str | None = None
    columns: list[ColumnInfo]
    primary_key: list[str] = Field(default_factory=list)
    foreign_keys: list[ForeignKey] = Field(default_factory=list)
    row_estimate: int | None = None
    sample_rows: list[dict[str, Any]] = Field(default_factory=list)

    @property
    def qualified_name(self) -> str:
        return f"{self.schema_name}.{self.name}"

    @property
    def pii_columns(self) -> list[str]:
        return [c.name for c in self.columns if c.is_pii]

    def to_prompt(self) -> str:
        """Compact DDL-like text for the LLM, including the comments that carry business rules."""
        lines = [
            f"TABLE {self.qualified_name}"
            + (f"  -- {self.comment}" if self.comment else "")
            + (f"  (~{self.row_estimate} rows)" if self.row_estimate else "")
        ]
        for c in self.columns:
            flags = " PK" if c.name in self.primary_key else ""
            flags += " PII" if c.is_pii else ""
            note = f"  -- {c.comment}" if c.comment else ""
            lines.append(f"  {c.name} {c.data_type}{flags}{note}")
        for fk in self.foreign_keys:
            lines.append(f"  FK {fk.column} -> {fk.ref_table}.{fk.ref_column}")
        if self.sample_rows:
            lines.append(f"  sample rows: {self.sample_rows}")
        return "\n".join(lines)

    def embedding_text(self) -> str:
        cols = ", ".join(f"{c.name}" + (f" ({c.comment})" if c.comment else "") for c in self.columns)
        return f"{self.qualified_name}. {self.comment or ''} Columns: {cols}"


class QueryResult(BaseModel):
    columns: list[str]
    rows: list[list[Any]]
    row_count: int
    truncated: bool
    elapsed_ms: int


class DataSource(ABC):
    kind: ClassVar[str]
    dialect: ClassVar[str]

    @abstractmethod
    async def test_connection(self) -> list[str]:
        """Connect and return warnings (e.g. 'this login can write'). Raise on failure."""

    @abstractmethod
    async def introspect(self, *, max_tables: int = 500, sample_rows: int = 3) -> list[TableInfo]:
        """Discover tables, columns, keys, comments and a few sample rows."""

    @abstractmethod
    async def run_readonly(self, sql: str, *, max_rows: int, timeout_ms: int) -> QueryResult:
        """Execute ONE already-validated SELECT in a read-only session with a timeout."""

    async def estimate_rows(self, sql: str) -> int | None:
        """Planner's row estimate (for cost-based approval). None if unsupported."""
        return None


def jsonable(value: Any) -> Any:
    """Convert DB values into JSON-safe values for tool results and checkpoints."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, decimal.Decimal):
        return float(value)
    if isinstance(value, (dt.datetime, dt.date, dt.time)):
        return value.isoformat()
    if isinstance(value, dt.timedelta):
        return value.total_seconds()
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, (bytes, bytearray, memoryview)):
        return f"<{len(bytes(value))} bytes>"
    if isinstance(value, (list, tuple)):
        return [jsonable(v) for v in value]
    if isinstance(value, dict):
        return {str(k): jsonable(v) for k, v in value.items()}
    return str(value)

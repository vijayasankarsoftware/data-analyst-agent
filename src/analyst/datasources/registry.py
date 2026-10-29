"""Connector registry: the single place that maps a source `kind` to its implementation."""

from typing import Any

from analyst.datasources.base import DataSource
from analyst.datasources.duckdb_source import DuckDBSource
from analyst.datasources.postgres import PostgresSource

CONNECTORS: dict[str, type[DataSource]] = {
    PostgresSource.kind: PostgresSource,
    DuckDBSource.kind: DuckDBSource,
    # "mysql": MySQLSource, "snowflake": SnowflakeSource, ...  <- add new connectors here
}


def make_datasource(kind: str, credentials: dict[str, Any]) -> DataSource:
    try:
        cls = CONNECTORS[kind]
    except KeyError:
        raise ValueError(f"unsupported source kind {kind!r}; available: {sorted(CONNECTORS)}") from None
    return cls(credentials)  # type: ignore[call-arg]


def dialect_for(kind: str) -> str:
    return CONNECTORS[kind].dialect

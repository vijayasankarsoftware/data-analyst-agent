"""The SQL guard is the most security-critical pure function in the project: test it hard."""

import pytest

from analyst.datasources.sql_guard import validate_sql

ALLOWED = {"public.orders", "public.customers", "public.order_items", "public.products"}
PII = {"public.customers": {"email"}}


def check(sql: str, dialect: str = "postgres", max_rows: int = 100):  # type: ignore[no-untyped-def]
    return validate_sql(sql, dialect=dialect, allowed_tables=ALLOWED, max_rows=max_rows, pii_columns=PII)


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT count(*) FROM orders",
        "SELECT o.id FROM public.orders o JOIN customers c ON c.id = o.customer_id",
        "WITH t AS (SELECT * FROM orders) SELECT count(*) FROM t",
        "SELECT status FROM orders UNION SELECT channel FROM orders",
        "SELECT * FROM (SELECT id FROM orders) sub",
        "select count(*) from orders;",
    ],
)
def test_allows_reads(sql: str) -> None:
    r = check(sql)
    assert r.ok, r.errors


@pytest.mark.parametrize(
    "sql",
    [
        "DELETE FROM orders",
        "UPDATE products SET unit_price = 0",
        "INSERT INTO orders (id) VALUES (1)",
        "DROP TABLE customers",
        "TRUNCATE orders",
        "ALTER TABLE orders ADD COLUMN x int",
        "CREATE TABLE evil AS SELECT * FROM orders",
        "SELECT * INTO evil FROM orders",
        "WITH d AS (DELETE FROM orders RETURNING *) SELECT * FROM d",
        "SELECT * FROM orders FOR UPDATE",
        "SET statement_timeout = 0",
        "GRANT ALL ON orders TO public",
        "COPY orders TO '/tmp/x'",
        "SELECT 1; DELETE FROM orders",
        "BEGIN",
    ],
)
def test_blocks_writes_and_session_changes(sql: str) -> None:
    assert not check(sql).ok


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT pg_sleep(100)",
        "SELECT set_config('statement_timeout', '0', false)",
        "SELECT pg_read_file('/etc/passwd')",
        "SELECT * FROM dblink('host=evil', 'select 1') AS t(x int)",
        "SELECT query_to_xml('select * from secrets', true, true, '')",
        "SELECT pg_terminate_backend(123)",
        "SELECT nextval('orders_id_seq')",
        "SELECT current_setting('server_version')",
    ],
)
def test_blocks_dangerous_functions(sql: str) -> None:
    assert not check(sql).ok


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM pg_catalog.pg_user",
        "SELECT * FROM information_schema.tables",
        "SELECT * FROM secrets",
        "SELECT * FROM other_db.public.orders",
    ],
)
def test_blocks_tables_outside_allowlist(sql: str) -> None:
    assert not check(sql).ok


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM read_csv('/etc/passwd')",
        "SELECT * FROM '/etc/passwd'",
        "SELECT * FROM read_parquet('s3://bucket/x.parquet')",
    ],
)
def test_duckdb_file_access_blocked(sql: str) -> None:
    assert not check(sql, dialect="duckdb").ok


def test_parse_errors_are_reported() -> None:
    r = check("SELEC * FROM orders")
    assert not r.ok and r.errors


def test_adds_limit_when_missing() -> None:
    r = check("SELECT id FROM orders")
    assert r.ok and "LIMIT 100" in r.sql


def test_lowers_too_large_limit() -> None:
    r = check("SELECT id FROM orders LIMIT 100000")
    assert r.ok and "LIMIT 100" in r.sql and "100000" not in r.sql


def test_keeps_smaller_limit() -> None:
    r = check("SELECT id FROM orders ORDER BY id LIMIT 5")
    assert r.ok and "LIMIT 5" in r.sql


def test_reports_resolved_tables() -> None:
    r = check("SELECT * FROM orders o JOIN public.customers c ON c.id = o.customer_id")
    assert sorted(r.tables) == ["public.customers", "public.orders"]


def test_pii_detected_for_explicit_column() -> None:
    assert check("SELECT email FROM customers").pii_columns == ["public.customers.email"]


def test_pii_detected_for_star() -> None:
    assert check("SELECT * FROM customers").pii_columns == ["public.customers.email"]


def test_count_star_is_not_pii() -> None:
    assert check("SELECT count(*) FROM customers").pii_columns == []

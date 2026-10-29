"""The connector stays read-only even when a user pastes ADMIN credentials."""

import psycopg
import pytest

from analyst.config import get_settings
from analyst.datasources.postgres import PostgresSource

pytestmark = pytest.mark.integration


def admin_source() -> PostgresSource:
    return PostgresSource({"dsn": get_settings().demo_warehouse_admin_conninfo})


async def test_introspection_discovers_schema() -> None:
    tables = {t.name: t for t in await PostgresSource({"dsn": get_settings().demo_warehouse_conninfo}).introspect()}
    assert {"customers", "orders", "order_items", "products"} <= set(tables)
    orders = tables["orders"]
    assert orders.primary_key == ["id"]
    assert any(fk.ref_table == "public.customers" for fk in orders.foreign_keys)
    assert "cancelled" in (orders.comment or "")
    assert "email" in tables["customers"].pii_columns
    assert all(r["email"] == "***" for r in tables["customers"].sample_rows)


async def test_admin_credentials_produce_warnings() -> None:
    warnings = await admin_source().test_connection()
    assert any("SUPERUSER" in w or "modify" in w for w in warnings)


async def test_admin_credentials_still_cannot_write() -> None:
    with pytest.raises(psycopg.errors.ReadOnlySqlTransaction):
        await admin_source().run_readonly("DELETE FROM orders", max_rows=10, timeout_ms=5000)


async def test_timeout_is_enforced_even_for_admin() -> None:
    with pytest.raises(psycopg.errors.QueryCanceled):
        await admin_source().run_readonly("SELECT pg_sleep(5)", max_rows=10, timeout_ms=500)


async def test_rows_are_capped_and_flagged_truncated() -> None:
    result = await admin_source().run_readonly("SELECT id FROM orders", max_rows=10, timeout_ms=5000)
    assert result.row_count == 10 and result.truncated


async def test_estimate_rows_uses_largest_plan_node() -> None:
    est = await admin_source().estimate_rows("SELECT * FROM order_items LIMIT 5")
    assert est is not None and est > 1000

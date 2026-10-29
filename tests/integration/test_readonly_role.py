"""Checks the demo source is set up the way we recommend users set up theirs."""

import psycopg
import pytest

from analyst.config import get_settings

pytestmark = pytest.mark.integration


async def _ro_conn() -> psycopg.AsyncConnection:
    return await psycopg.AsyncConnection.connect(get_settings().demo_warehouse_conninfo)


async def test_can_read() -> None:
    async with await _ro_conn() as conn:
        cur = await conn.execute("SELECT count(*) FROM orders")
        assert (await cur.fetchone()) == (5000,)


@pytest.mark.parametrize(
    "sql",
    [
        "DELETE FROM orders",
        "UPDATE products SET unit_price = 0",
        "DROP TABLE customers",
        "CREATE TABLE evil (id int)",
    ],
)
async def test_writes_are_blocked(sql: str) -> None:
    async with await _ro_conn() as conn:
        with pytest.raises(psycopg.Error):
            await conn.execute(sql)


async def test_long_queries_time_out() -> None:
    async with await _ro_conn() as conn:
        with pytest.raises(psycopg.errors.QueryCanceled):
            await conn.execute("SELECT pg_sleep(10)")

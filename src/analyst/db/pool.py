"""Connection pool for the APP database (ours). Never used for user data sources."""

from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from analyst.config import get_settings

_pool: AsyncConnectionPool | None = None


async def get_app_pool() -> AsyncConnectionPool:
    """Lazily open one pool per process. Both the API and the MCP server call this."""
    global _pool
    if _pool is None:
        pool = AsyncConnectionPool(
            get_settings().app_db_conninfo,
            min_size=1,
            max_size=10,
            open=False,
            # autocommit + prepare_threshold=0 + dict_row are what LangGraph's PostgresSaver expects.
            kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row},
        )
        await pool.open(wait=True)
        _pool = pool
    return _pool


async def close_app_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


def to_vector(values: list[float]) -> str:
    """pgvector accepts a '[1,2,3]' text literal; this avoids an extra driver dependency."""
    return "[" + ",".join(f"{v:.7f}" for v in values) + "]"

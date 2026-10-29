"""Append-only audit trail. When an AI touches company data, 'who ran what, when' is mandatory."""

import logging
from typing import Any

from psycopg.types.json import Jsonb

from analyst.db.pool import get_app_pool

log = logging.getLogger(__name__)


async def audit(
    action: str,
    *,
    actor: str,
    workspace_id: str | None = None,
    thread_id: str | None = None,
    source_id: str | None = None,
    detail: dict[str, Any] | None = None,
) -> None:
    try:
        pool = await get_app_pool()
        async with pool.connection() as conn:
            await conn.execute(
                "INSERT INTO audit_log (workspace_id, thread_id, actor, action, source_id, detail)"
                " VALUES (%s, %s, %s, %s, %s, %s)",
                [workspace_id, thread_id, actor, action, source_id, Jsonb(detail or {})],
            )
    except Exception:  # auditing must never break the request, but it must be loud
        log.exception("failed to write audit log entry: %s", action)

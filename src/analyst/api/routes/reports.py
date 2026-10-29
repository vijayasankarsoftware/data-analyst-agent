"""Exported reports (created by the report agent after human approval)."""

from typing import Any

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import PlainTextResponse

from analyst.api.deps import WorkspaceDep
from analyst.db.pool import get_app_pool

router = APIRouter(prefix="/reports", tags=["reports"])


@router.get("")
async def list_reports(ws: WorkspaceDep) -> dict[str, Any]:
    pool = await get_app_pool()
    async with pool.connection() as conn:
        cur = await conn.execute(
            "SELECT id::text AS id, title, thread_id::text AS thread_id, created_at::text AS created_at"
            " FROM reports WHERE workspace_id = %s ORDER BY created_at DESC LIMIT 100",
            [ws.id],
        )
        return {"reports": await cur.fetchall()}


@router.get("/{report_id}", response_class=PlainTextResponse)
async def get_report(report_id: str, ws: WorkspaceDep, download: bool = False) -> PlainTextResponse:
    pool = await get_app_pool()
    async with pool.connection() as conn:
        cur = await conn.execute(
            "SELECT title, content_md FROM reports WHERE id::text = %s AND workspace_id = %s", [report_id, ws.id]
        )
        row = await cur.fetchone()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "report not found")
    headers = {"Content-Disposition": f'attachment; filename="report-{report_id[:8]}.md"'} if download else {}
    return PlainTextResponse(row["content_md"], media_type="text/markdown", headers=headers)

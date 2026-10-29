"""Request dependencies: API-key auth resolving to a workspace, and thread ownership."""

from typing import Annotated, Any

from fastapi import Depends, Header, HTTPException, Request, status
from pydantic import BaseModel

from analyst.db.pool import get_app_pool
from analyst.security.crypto import hash_api_key


class Workspace(BaseModel):
    id: str
    name: str


async def get_workspace(x_api_key: Annotated[str | None, Header()] = None) -> Workspace:
    if not x_api_key:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing X-API-Key header")
    pool = await get_app_pool()
    async with pool.connection() as conn:
        cur = await conn.execute(
            "SELECT w.id::text AS id, w.name FROM api_keys k JOIN workspaces w ON w.id = k.workspace_id"
            " WHERE k.key_hash = %s AND k.revoked_at IS NULL",
            [hash_api_key(x_api_key)],
        )
        row = await cur.fetchone()
    if row is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid API key")
    return Workspace(**row)


WorkspaceDep = Annotated[Workspace, Depends(get_workspace)]


async def require_thread(thread_id: str, workspace: WorkspaceDep) -> dict[str, Any]:
    """404 (not 403) for other workspaces' threads, so ids can't be probed."""
    pool = await get_app_pool()
    async with pool.connection() as conn:
        cur = await conn.execute(
            "SELECT id::text AS id, title, created_at::text AS created_at FROM threads"
            " WHERE id::text = %s AND workspace_id = %s",
            [thread_id, workspace.id],
        )
        row = await cur.fetchone()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "thread not found")
    return dict(row)


ThreadDep = Annotated[dict[str, Any], Depends(require_thread)]


def get_graph(request: Request) -> Any:
    return request.app.state.graph


GraphDep = Annotated[Any, Depends(get_graph)]

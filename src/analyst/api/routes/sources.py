"""Flow A: connect, list, refresh and delete data sources (delegated to MCP tools)."""

from typing import Any

from fastapi import APIRouter, HTTPException, status

from analyst.api.deps import WorkspaceDep
from analyst.api.schemas import SourceCreate
from analyst.datasources.registry import CONNECTORS
from analyst.tools_client import ToolError, get_tool_client

router = APIRouter(prefix="/sources", tags=["sources"])


@router.get("/kinds")
async def kinds() -> dict[str, list[str]]:
    return {"kinds": sorted(CONNECTORS)}


@router.get("")
async def list_sources(ws: WorkspaceDep) -> dict[str, Any]:
    return await get_tool_client().call("list_sources", workspace_id=ws.id)


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_source(body: SourceCreate, ws: WorkspaceDep) -> dict[str, Any]:
    try:
        return await get_tool_client().call(
            "register_source",
            workspace_id=ws.id,
            name=body.name,
            kind=body.kind,
            credentials=body.credentials,
            description=body.description,
        )
    except ToolError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from None


@router.get("/{source_id}/tables")
async def source_tables(source_id: str, ws: WorkspaceDep) -> dict[str, Any]:
    try:
        return await get_tool_client().call("describe_tables", workspace_id=ws.id, source_id=source_id)
    except ToolError as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(e)) from None


@router.post("/{source_id}/refresh")
async def refresh_source(source_id: str, ws: WorkspaceDep) -> dict[str, Any]:
    try:
        return await get_tool_client().call("refresh_source", workspace_id=ws.id, source_id=source_id)
    except ToolError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from None


@router.delete("/{source_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_source(source_id: str, ws: WorkspaceDep) -> None:
    result = await get_tool_client().call("delete_source", workspace_id=ws.id, source_id=source_id)
    if not result["deleted"]:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "source not found")

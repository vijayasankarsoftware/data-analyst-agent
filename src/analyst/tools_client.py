"""How agents and the API call MCP tools.

Two transports, one code path:
- MCP_URL set   -> Streamable HTTP to the separate MCP server process (production),
- MCP_URL unset -> in-process MCP connection to the same server object (dev/tests/evals).
Either way, calls go through the MCP protocol: schemas are validated and results are typed.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from functools import lru_cache
from typing import Any

import httpx2
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

from analyst.config import get_settings


class ToolError(Exception):
    """A tool returned an error result (guardrail rejection, DB error, not found, ...)."""


class ToolClient:
    def __init__(self, url: str | None, token: str | None) -> None:
        self.url = url
        self.token = token

    @asynccontextmanager
    async def _client(self) -> AsyncIterator[Client]:
        if self.url is None:
            from analyst.mcp_server.server import mcp

            async with Client(mcp, read_timeout_seconds=600) as client:
                yield client
            return
        headers = {"Authorization": f"Bearer {self.token}"} if self.token else {}
        async with httpx2.AsyncClient(headers=headers, timeout=httpx2.Timeout(600, connect=10)) as http:
            transport = streamable_http_client(self.url, http_client=http)
            async with Client(transport, read_timeout_seconds=600) as client:
                yield client

    async def call(self, tool: str, /, **arguments: Any) -> dict[str, Any]:
        # `tool` is positional-only (the `/`), so tools may have their own `name` argument
        # (e.g. register_source(name=...)) without clashing with this parameter.
        args = {k: v for k, v in arguments.items() if v is not None}
        async with self._client() as client:
            result = await client.call_tool(tool, args)
        if result.is_error:
            text = " ".join(getattr(block, "text", "") for block in result.content).strip()
            raise ToolError(text or f"tool {tool} failed")
        structured = result.structured_content
        return structured if isinstance(structured, dict) else {"result": structured}


@lru_cache
def get_tool_client() -> ToolClient:
    s = get_settings()
    return ToolClient(s.mcp_url, s.mcp_auth_token.get_secret_value() if s.mcp_auth_token else None)

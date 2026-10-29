"""Run the MCP server over Streamable HTTP:  python -m analyst.mcp_server

Stateless HTTP + JSON responses: every tool call is an independent request, so the
server can be scaled horizontally behind a load balancer.
"""

import asyncio
import hmac
import logging
from typing import Any

import uvicorn
from mcp.server.transport_security import TransportSecuritySettings

from analyst.config import get_settings
from analyst.db.migrate import migrate
from analyst.db.pool import close_app_pool
from analyst.mcp_server.server import mcp

Scope = dict[str, Any]


class BearerAuth:
    """Tiny ASGI middleware: reject HTTP requests without the shared bearer token."""

    def __init__(self, app: Any, token: str) -> None:
        self.app = app
        self.expected = f"Bearer {token}".encode()

    async def __call__(self, scope: Scope, receive: Any, send: Any) -> None:
        if scope["type"] == "http":
            headers = dict(scope.get("headers") or [])
            if not hmac.compare_digest(headers.get(b"authorization", b""), self.expected):
                await send(
                    {"type": "http.response.start", "status": 401, "headers": [(b"content-type", b"text/plain")]}
                )
                await send({"type": "http.response.body", "body": b"unauthorized"})
                return
        await self.app(scope, receive, send)


async def _migrate_once() -> None:
    await migrate()
    await close_app_pool()  # the pool belongs to this temporary event loop; uvicorn opens its own


def main() -> None:
    s = get_settings()
    logging.basicConfig(level=s.log_level)
    asyncio.run(_migrate_once())
    app = mcp.streamable_http_app(
        stateless_http=True,
        json_response=True,
        # DNS-rebinding protection targets browsers hitting localhost servers; this server is
        # internal-only and requires a bearer token, and in Docker the Host header is 'mcp:8001'.
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    )
    uvicorn.run(BearerAuth(app, s.require("mcp_auth_token")), host=s.mcp_host, port=s.mcp_port)


if __name__ == "__main__":
    main()

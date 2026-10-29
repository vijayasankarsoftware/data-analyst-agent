"""The MCP server: exposes the tools over the Model Context Protocol.

The same server works for our LangGraph agents AND any MCP client (Claude Desktop,
Cursor, MCP Inspector), which is the point of MCP: tools become reusable products.
"""

from mcp.server.mcpserver import MCPServer

from analyst.mcp_server.tools import ALL_TOOLS

mcp = MCPServer(
    name="data-analyst-tools",
    instructions=(
        "Tools for analysing a workspace's plugged-in databases and documents. "
        "Call workspace_overview first, use find_relevant_tables before writing SQL, "
        "validate_sql before run_sql. All SQL is read-only and guarded."
    ),
)

for _tool in ALL_TOOLS:
    mcp.tool()(_tool)

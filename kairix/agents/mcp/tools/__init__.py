"""kairix.agents.mcp.tools — agent-facing MCP tool implementations.

Each tool module exposes a pure-Python ``tool_<name>`` function that the
MCP server in ``kairix.agents.mcp.server`` wires through ``@server.tool``.
Keeping the bodies here (rather than inline in server.py) lets the unit
tests import the tool function directly without standing up FastMCP.
"""

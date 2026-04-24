# Connecting Agents to Kairix

Kairix works with any agent platform that supports MCP (Model Context Protocol). This guide shows how to connect the most common ones.

## Claude Desktop / Claude Code

Add kairix as an MCP server in your Claude Desktop configuration:

**macOS:** `~/Library/Application Support/Claude/claude_desktop_config.json`
**Linux:** `~/.config/Claude/claude_desktop_config.json`

```json
{
  "mcpServers": {
    "kairix": {
      "command": "kairix",
      "args": ["mcp", "serve"]
    }
  }
}
```

Restart Claude Desktop. Kairix tools (search, research, entity, prep) will appear automatically.

---

## OpenClaw

Register kairix as an MCP server:

```bash
openclaw mcp set mcp-kairix "kairix mcp serve"
```

Or add to your `openclaw.json` manually:

```json
{
  "mcp": {
    "servers": {
      "mcp-kairix": {
        "command": "kairix",
        "args": ["mcp", "serve"],
        "description": "Knowledge base search, research, entity lookup"
      }
    }
  }
}
```

---

## Docker (SSE transport)

When running kairix in Docker, the MCP server uses SSE (Server-Sent Events) over HTTP:

```bash
docker compose up -d
# MCP endpoint: http://localhost:8080
```

Any MCP client that supports SSE can connect to `http://localhost:8080`.

For Claude Desktop with a Docker-hosted kairix, you can use the SSE transport:

```json
{
  "mcpServers": {
    "kairix": {
      "transport": "sse",
      "url": "http://localhost:8080"
    }
  }
}
```

---

## VS Code (Copilot MCP)

If your VS Code setup supports MCP servers, add to your settings:

```json
{
  "mcp.servers": {
    "kairix": {
      "command": "kairix",
      "args": ["mcp", "serve"]
    }
  }
}
```

---

## Direct Python (no MCP server needed)

If your agent runs in the same Python process, you can call kairix tools directly without an MCP server:

```python
from kairix.mcp.server import tool_search, tool_research, tool_entity

# Simple search
result = tool_search(query="engineering standards", agent="my-agent")
for item in result["results"]:
    print(f"  {item['path']}: {item['snippet'][:100]}")

# Research (iterative, multi-turn)
research = tool_research(query="competitive positioning analysis")
print(research["synthesis"])

# Entity lookup
entity = tool_entity(name="Jordan Blake")
print(entity["summary"])
```

This is the fastest integration path — no server, no protocol overhead.

---

## Custom agent frameworks

For any framework that supports tool calling (LangChain, CrewAI, AutoGen, etc.):

1. **Option A: MCP client** — connect to `kairix mcp serve` via stdio or SSE
2. **Option B: Direct import** — import `tool_search`, `tool_research`, etc. from `kairix.mcp.server`
3. **Option C: HTTP wrapper** — run `kairix mcp serve --transport sse --port 8080` and call via HTTP

All three options expose the same 6 tools with identical parameters and return values. See [mcp-tools.md](mcp-tools.md) for the full tool reference.

---

## Troubleshooting

| Problem | Fix |
|---------|-----|
| "kairix: command not found" | Run `pip install kairix` or check your PATH |
| Tools don't appear in Claude Desktop | Restart Claude Desktop after editing config |
| SSE connection refused | Check `docker compose ps` — kairix service must be running |
| "No results" on first search | Run `kairix embed` to index your documents first |
| Slow responses | First search embeds the query (~500ms). Subsequent searches use cached index. |

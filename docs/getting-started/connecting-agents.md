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

## Docker (HTTP transport)

When running kairix in Docker, the MCP server speaks streamable HTTP — the recommended transport:

```bash
docker compose up -d
# MCP endpoint: http://localhost:8080/mcp
```

Any MCP client that supports streamable HTTP can connect to `http://localhost:8080/mcp`. Older clients that only speak SSE can use the legacy `http://localhost:8080/sse` endpoint — it is still served by default.

If host port 8080 is already taken (a reverse proxy, another service), set `KAIRIX_HOST_PORT` in your `.env` before `docker compose up -d` and use that port in the URL instead.

For Claude Desktop / Claude Code with a Docker-hosted kairix:

```json
{
  "mcpServers": {
    "kairix": {
      "url": "http://localhost:8080/mcp"
    }
  }
}
```

---

## Claude Code (project-level)

To give Claude Code access to kairix in one repo, add a `.mcp.json` file at the project root pointing at the HTTP endpoint:

```json
{
  "mcpServers": {
    "kairix": {
      "type": "http",
      "url": "http://localhost:8080/mcp"
    }
  }
}
```

Commit the file and every Claude Code session in that repo gets the kairix tools. If your kairix listens on a different port (`KAIRIX_HOST_PORT` in `.env`), use that port in the URL.

**Tell the agent what to do at session start.** Add a few lines like these to the project's `CLAUDE.md`:

```markdown
At session start, call the kairix `bootstrap` tool with your agent name
(e.g. `agent-alpha`) to load your role, recent memory, and active goals.
Search kairix before answering questions about the team's work — `search`
for retrieval, `prep` for a tiered topic summary. If your kairix lists a
memory-write tool, store durable decisions through it so the team can
find them later.
```

Each agent should use its own stable name so its memory and scopes resolve correctly across sessions. (The memory-write line only applies once your kairix release lists such a tool — check the server's tool list; `bootstrap`, `search`, and `prep` are available everywhere.)

---

## Hermes and other MCP-over-HTTP clients

Any agent platform that can consume an MCP server over streamable HTTP can use kairix — Hermes is one example. Kairix doesn't ship a Hermes config template; register kairix wherever your platform's agent config declares MCP servers, using:

- **Endpoint:** `http://<kairix-host>:8080/mcp` (streamable HTTP; the legacy `/sse` endpoint is also served)
- **Auth:** none built in — read the remote-agents note below before exposing kairix beyond localhost
- **Session start:** have each agent call the `bootstrap` tool with its own agent name (e.g. `agent-alpha`) to load role, recent memory, and goals — the same per-agent pattern as OpenClaw
- **During the session:** `search` for retrieval, `prep` for a tiered topic summary

Check your platform's own documentation for the exact config file syntax; kairix only needs the URL.

### Remote agents (agent on a different machine)

The compose file binds kairix to `127.0.0.1` by default, so only local processes can reach it. For agents on other machines:

1. Set `KAIRIX_MCP_BIND_HOST=0.0.0.0` in your `.env`, then `docker compose up -d` to recreate the container with the new binding.
2. Put an auth-enforcing reverse proxy (caddy / nginx / Authentik) in front — the MCP server has no built-in authentication. See [OPERATIONS §"Deploying behind a reverse proxy"](../operations/OPERATIONS.md#deploying-behind-a-reverse-proxy-caddy--nginx--cloudflared).

Then point remote agents at the proxy URL (e.g. `https://kairix.your-team.example.com/mcp`).

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
from kairix.agents.mcp.server import tool_search, tool_research, tool_entity

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

1. **Option A: MCP client** — connect to `kairix mcp serve` via stdio or streamable HTTP
2. **Option B: Direct import** — import `tool_search`, `tool_research`, etc. from `kairix.agents.mcp.server`
3. **Option C: HTTP wrapper** — run `kairix mcp serve --transport http --port 8080` and call `http://localhost:8080/mcp`

All three options expose the same 35 tools with identical parameters and return values. See [mcp-tools.md](../user-guide/mcp-tools.md) for the full tool reference.

---

## Troubleshooting

| Problem | Fix |
|---------|-----|
| "kairix: command not found" | Run `pipx install kairix-agentic-knowledge-mgt` (or `pip install kairix-agentic-knowledge-mgt` inside a venv) or check your PATH |
| Tools don't appear in Claude Desktop | Restart Claude Desktop after editing config |
| Connection refused on `/mcp` (or legacy `/sse`) | Check `docker compose ps` — kairix service must be running |
| "No results" on first search | Run `kairix embed` to index your documents first |
| Slow responses | First search embeds the query (~500ms). Subsequent searches use cached index. |

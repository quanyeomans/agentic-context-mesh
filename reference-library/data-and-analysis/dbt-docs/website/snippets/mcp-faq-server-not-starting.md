---
title: "Mcp Faq Server Not Starting"
source: dbt Core Documentation
source_url: https://github.com/dbt-labs/docs.getdbt.com
licence: Apache-2.0
domain: data-and-analysis
subdomain: dbt-docs
date_added: 2026-04-25
---

<Expandable alt_header="Server not starting">

**Symptoms:** The MCP server shows as disconnected or unavailable in your client.

**Diagnosis:** Check the server logs:

- **VS Code:** Open the Command Palette (`Ctrl/Cmd + Shift + P`) → `MCP: List Servers` → click the dbt server to see detailed logs.
- **Claude Desktop:** Check `~/Library/Logs/Claude` (macOS) or `%APPDATA%\Claude\logs` (Windows).
- **All clients:** Set `DBT_MCP_LOG_LEVEL=DEBUG` in your environment variables to get more verbose output.

**Common causes:**

- Missing or incorrect `DBT_PROJECT_DIR` or `DBT_PATH` — verify the paths exist and are absolute paths.
- Invalid or expired authentication tokens — generate a new token and update your config.
- Missing required environment variables for the toolset you're trying to use — see [Tool requirements](/docs/dbt-ai/setup-local-mcp#tool-requirements-at-a-glance).
</Expandable>

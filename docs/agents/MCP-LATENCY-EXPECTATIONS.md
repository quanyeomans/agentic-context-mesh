# MCP latency expectations — moved

The per-tool latency table now ships **inside the kairix package** so it is
present in every install (the wheel and the Docker image), not just a source
checkout. The `usage_guide` MCP tool routes `topic="mcp-latency"` to it; it
used to live here, but a copy under `docs/` is absent from the built image
(#466).

Single source of truth:

    kairix/agents/usage_guide/data/MCP-LATENCY-EXPECTATIONS.md

Read it from any install:

- `kairix usage-guide mcp-latency` — print the table from a shell
- the `usage_guide` MCP tool with `topic="mcp-latency"` — for agents

This stub stays so older links to `docs/agents/MCP-LATENCY-EXPECTATIONS.md`
do not 404.

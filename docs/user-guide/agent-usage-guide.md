# Agent usage guide — moved

The canonical agent usage guide now ships **inside the kairix package** so
it is present in every install (the wheel and the Docker image), not just a
source checkout. It used to live here, but a copy under `docs/` is absent
from the built image — which is why the `usage_guide` MCP tool returned
`UsageGuideNotFound` on stock production deploys (#466).

Single source of truth:

    kairix/agents/usage_guide/data/agent-usage-guide.md

That bundled file is **generated** from the capability catalogue
(`CAPABILITIES_CATALOG` in `kairix/agents/mcp/server.py`) — its loop-ordered
capability index derives from the registry so it can't drift. Do not hand-edit
it. Edit the prose template (`.../data/agent-usage-guide.md.tmpl`), then
regenerate:

    python -m kairix.agents.usage_guide.generate

Read it from any install:

- `kairix usage-guide` — print the guide from a shell
- the `usage_guide` MCP tool — for agents
- `python -c "from importlib import resources; print(resources.files('kairix.agents.usage_guide').joinpath('data/agent-usage-guide.md').read_text())"`

This stub stays so older links to `docs/user-guide/agent-usage-guide.md`
do not 404.

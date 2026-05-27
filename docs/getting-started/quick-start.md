# Quick Start

Get kairix running and searching your documents in under 30 minutes. Two install paths cover most operators:

- **Docker** — the default for shared hosts, VMs, and anywhere `docker compose up -d` is the operating shape. Three containers (kairix / worker / neo4j) with healthchecks.
- **Pip install** — the default for laptop dogfooding and single-user deployments. One Python virtualenv; kairix runs as two processes (`kairix worker run` and `kairix mcp serve`).

Pick the path that matches your environment and skip the other one.

## What you need (either path)

- **An LLM API key** — Azure OpenAI, standard OpenAI, or any OpenAI-compatible provider
- **A folder of documents** — markdown files, text files, or structured notes
- **Optional: connector secrets** — only the connectors you plan to enable need their secrets provisioned (all available in v2026.5.28+):
  - SharePoint — M365 OAuth triple (`CONNECTOR_M365_*`)
  - Slack — bot token (optional app token for live events)
  - GitHub — personal access token OR app-installation triple + webhook secret
  - Notion — integration token
  - Microsoft 365 calendar + email headers — same OAuth triple as SharePoint

---

## Path A — Docker

### A1. Get the compose file

```bash
curl -O https://raw.githubusercontent.com/three-cubes/kairix/main/docker-compose.yml
curl -O https://raw.githubusercontent.com/three-cubes/kairix/main/.env.example
```

Or clone the full repo:

```bash
git clone https://github.com/three-cubes/kairix
cd kairix
```

### A2. Set up your credentials

```bash
cp .env.example .env
```

Open `.env` and add your LLM API key:

```bash
# For Azure OpenAI:
KAIRIX_LLM_ENDPOINT=https://your-resource.openai.azure.com
KAIRIX_LLM_API_KEY=your-key-here

# Or for standard OpenAI / OpenRouter:
# KAIRIX_LLM_ENDPOINT=https://api.openai.com/v1
# KAIRIX_LLM_API_KEY=sk-your-key-here
```

### A3. Point to your documents

```bash
ln -s ~/Documents/my-notes ./documents
```

The container includes 5,800+ curated reference library documents, so you can start searching immediately and add your own documents later.

### A4. Start everything

```bash
docker compose up -d
```

This starts three services:
- **kairix** — search engine and MCP server (port 8080)
- **kairix-worker** — indexes your documents automatically every hour
- **neo4j** — knowledge graph for people/company queries

### A5. Index your documents

```bash
docker compose exec kairix kairix embed
```

For 1,000 documents (~4,000 chunks), expect ~$0.50-1.00 with text-embedding-3-large.

### A6. Verify your setup

```bash
docker compose exec kairix kairix onboard check          # human-readable
docker compose exec kairix kairix onboard check --json   # structured — exits 0 only on every-check pass, wire into your healthcheck
```

The output reports one row per check. Every failed check carries a one-line `remediation` string so you can fix forward without grepping logs. The `--json` shape `{passed, total, fully_passed, failures: [{check, detail, remediation}]}` is the canonical signal for any machine consumer.

---

## Path B — Pip install

### B1. Install the package

```bash
python3 -m venv ~/.venvs/kairix
source ~/.venvs/kairix/bin/activate
pip install "kairix[agents]"
```

The `[agents]` extra pulls in the MCP server dependencies. Most operators want this.

### B2. Set up your config + secrets

```bash
# Config file goes anywhere; set KAIRIX_CONFIG_PATH if you don't use the cwd default.
curl -o ~/.kairix/kairix.config.yaml https://raw.githubusercontent.com/three-cubes/kairix/main/kairix.config.example.yaml
mkdir -p ~/.config/kairix/secrets
```

Edit `~/.kairix/kairix.config.yaml` and set `provider:`, `paths.document_root:`, plus the `topology_v2:` block if you want connectors enabled (see the inline comments in the example file).

Put your secrets in a file kairix's resolver can read:

```bash
cat > ~/.config/kairix/secrets/kairix.env <<EOF
KAIRIX_LLM_API_KEY=your-key-here
KAIRIX_LLM_ENDPOINT=https://your-resource.openai.azure.com
EOF
chmod 600 ~/.config/kairix/secrets/kairix.env
```

Optional: if you want the agent-driven setup flow (an LLM agent writes the config on your behalf), see [agent-driven-setup.md](agent-driven-setup.md).

### B3. Start the worker + MCP server

```bash
# In one terminal — the worker indexes your documents on a schedule.
export KAIRIX_CONFIG_PATH=~/.kairix/kairix.config.yaml
export KAIRIX_SECRETS_FILE=~/.config/kairix/secrets/kairix.env
kairix worker run &

# In another terminal — the MCP server (HTTP / SSE transport).
export KAIRIX_CONFIG_PATH=~/.kairix/kairix.config.yaml
export KAIRIX_SECRETS_FILE=~/.config/kairix/secrets/kairix.env
kairix mcp serve --transport sse --port 8080
```

For long-running deployments use a systemd unit — see [`docs/operations/SHARED-HOSTS.md`](../operations/SHARED-HOSTS.md) for the unit-file pointer.

### B4. Index your documents

```bash
kairix embed
```

### B5. Verify your setup

```bash
kairix onboard check          # human-readable
kairix onboard check --json   # structured
```

Same shape as the Docker path. The JSON envelope is the canonical signal for any healthcheck or CI gate.

---

## What every operator should see (either path)

A clean install reports something like:

```
kairix deployment check
──────────────────────────────────────────────────
  ✓ kairix_on_path
  ✓ wrapper_installed
  ✓ secrets_loaded
  ✓ document_root_configured — Document root: /data/documents
  ✓ vector_search_working
  ✓ neo4j_reachable
  ✓ agent_knowledge_populated
  ✓ chunk_date_populated
  ✓ mcp_service
  ✓ query_cache_stats
  ✓ embed_cache_stats
  ✓ topology_v2_config_valid — skipped — topology_v2_config flag is OFF (default-safe)
  ✓ topology_v2_cc_pairs_registered — skipped — topology_v2_config flag is OFF (default-safe)
  ✓ sharepoint_credentials_loaded — skipped — connector_sharepoint flag is OFF (default-safe)
  ✓ maintenance_loop_ticking — skipped — maintenance_loop flag is OFF (default-safe)
──────────────────────────────────────────────────
  All 15 checks passed
```

If any check fails the output names the next command to run.

`agent_knowledge_populated` looks at `04-Agent-Knowledge/**/*.md` under your document root by default. If your vault uses a different layout, set `paths.agent_knowledge_dir` (directory name) and `paths.agent_memory_glob` (file pattern) in `kairix.config.yaml`. See [`how-to-upgrade-kairix`](../operations/runbooks/how-to-upgrade-kairix.md) for the full options.

---

## Topology v2

Topology v2 unlocks the operator-config surface (connectors / credentials / cc_pairs / collections / scope_profiles / skills) plus four shipping connectors (SharePoint, Slack, GitHub, Notion). Every part is gated by a default-off feature flag so existing deployments stay bit-for-bit identical until you flip them.

The flags split into three groups:

| Group | Flag | What it does |
|---|---|---|
| Operator surface | `topology_v2_config` | Parse + apply the `topology_v2:` block in your config |
| Operator surface | `topology_v2_runtime` | Route chunk writes through the per-cc_pair CollectionRouter |
| Connector slots | `connector_sharepoint` | Enable SharePoint (Graph drive-delta + extractor dispatch) |
| Connector slots | `connector_slack` | Enable Slack (Web API + Socket Mode live events) |
| Connector slots | `connector_github` | Enable GitHub (REST + GraphQL + webhook listener) |
| Connector slots | `connector_notion` | Enable Notion (workspace pages + database rows) |
| Per-source pilots | `topology_v2_obsidian` | One Container per Obsidian folder + delta cursor |
| Per-source pilots | `topology_v2_dex_crm` | Tenant Container for Dex CRM |
| Per-source pilots | `topology_v2_m365_email_headers` | One Container per mailbox |
| Per-source pilots | `topology_v2_m365_calendar` | One Container per calendar |
| Per-source pilots | `topology_v2_sharepoint` | One Container per drive |
| Per-source pilots | `topology_v2_slack` | One Container per channel |
| Per-source pilots | `topology_v2_github` | One Container per repository |
| Per-source pilots | `topology_v2_notion` | One Container per page tree |
| Background loop | `maintenance_loop` | Periodic orphan-vector cleanup (24h default) |

To turn the alpha on:

1. **Author the YAML.** Copy [`kairix.config.example.yaml`](https://github.com/three-cubes/kairix/blob/main/kairix.config.example.yaml) at the repo root into your config path (Docker overlay: `kairix.config.local.yaml`; pip: `~/.kairix/kairix.config.yaml`). The example shape parses cleanly and `kairix config validate` reports zero failures against it.
2. **Flip the flags.** Add only the flags for the surfaces you want active. The example config's `features:` block lists every flag with a comment beside each.
3. **Set the secrets** for each connector you enabled. See the per-connector recipe in [`how-to-upgrade-kairix`](../operations/runbooks/how-to-upgrade-kairix.md) — each connector lists the exact env-var names + where to put them for Docker and pip.
4. **Apply + verify.**
    ```bash
    kairix worker apply-config       # materialise declared cc_pairs into topology_cc_pairs
    kairix config validate           # parser + 5 cross-reference checks
    kairix features status           # confirm the flags are on
    kairix onboard check             # all 15 checks should pass
    ```

The connector-credentials onboard checks skip with `ok=True` when their flag is off — the alpha is reversible at any time by removing the flag entry from the `features:` block.

---

## Search quality check (either path)

Run the built-in benchmark against the reference library:

```bash
# Docker
docker compose exec kairix kairix benchmark list
docker compose exec kairix kairix benchmark run reflib

# Pip
kairix benchmark list
kairix benchmark run reflib
```

Sensible defaults gate the run (in `pyproject.toml` under `[tool.kairix.benchmark.gates]`): overall ≥ 0.78, temporal ≥ 0.55, entity ≥ 0.80, contextual_prep ≥ 0.60. Expected baseline:

| Metric | Expected |
|--------|----------|
| Weighted total | ≥ 0.80 |
| NDCG@10 | ≥ 0.90 |
| Hit@5 | ≥ 95% |

If scores are significantly below these, check your embedding model and LLM connection.

---

## Search a document

```bash
# Docker
docker compose exec kairix kairix search "your question here"

# Pip
kairix search "your question here"
```

Your knowledge store is running.

---

## Connecting agents

The MCP server runs on port 8080 (Docker) or whatever you passed to `kairix mcp serve --port` (pip). Any MCP-compatible agent can connect via SSE.

**Claude Desktop / Claude Code:**

```json
{
  "mcpServers": {
    "kairix": {
      "url": "http://localhost:8080/sse"
    }
  }
}
```

See [connecting-agents.md](connecting-agents.md) for OpenClaw, LangGraph, and other platforms.

If you're an LLM agent reading this and standing kairix up on a user's behalf, see [agent-driven-setup.md](agent-driven-setup.md) — the declarative path optimised for unambiguous machine instructions.

---

## What happens next

- **Documents are indexed automatically** every hour by the worker service. Operator controls: `kairix worker pause` / `resume` / `status`.
- **The MCP server exposes 12 tools** — `search`, `entity`, `prep`, `timeline`, `research`, `contradict`, `usage_guide`, `brief`, `bootstrap`, `entity_suggest`, `entity_validate`, `warm`. Each response carries a `health` envelope so agents know what's online.
- **Agents should call `kairix bootstrap <agent>` at session start** to get a one-shot orientation envelope (role, board, recent memory, active goals, health).
- **Run `kairix onboard check --json`** any time — exit 0 means every check passed; exit 1 prints structured failures with remediation strings.
- **Run `kairix benchmark run reflib`** to benchmark search quality against the bundled gold suite.

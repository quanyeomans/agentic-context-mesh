# Quick Start

Get kairix running and searching your documents in under 30 minutes. Two install paths cover most operators:

- **Docker** — the default for shared hosts, VMs, and anywhere `docker compose up -d` is the operating shape. Two containers (kairix + neo4j) with healthchecks; the kairix container runs both the api and the background worker under an internal supervisor (s6) as the `kairix` system user (uid 995).
- **Pip install** — the default for laptop setups and single-user deployments. One Python virtualenv; kairix runs as two processes (`kairix worker run` and `kairix mcp serve`).

Pick the path that matches your environment and skip the other one.

> **Not a developer? Start here.** The fastest path is the browser setup wizard — run `docker compose up -d`, then open <http://localhost:8080/setup> and follow the steps (provider, documents, first search). No YAML, no terminal beyond that one command. The full wizard walkthrough is in [Path A → A4b. Set up in your browser](#a4b-set-up-in-your-browser-optional) below.

> **Want a system-managed install with `kairix init --system` (kairix user, systemd unit, FHS paths)?** See [install.md](install.md) — the full three-track guide for system, user, Docker, macOS, and Windows.

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
KAIRIX_PROVIDER_LLM_ENDPOINT=https://your-resource.openai.azure.com
KAIRIX_PROVIDER_LLM_API_KEY=your-key-here

# Or for standard OpenAI / OpenRouter:
# KAIRIX_PROVIDER_LLM_ENDPOINT=https://api.openai.com/v1
# KAIRIX_PROVIDER_LLM_API_KEY=sk-your-key-here
```

`docker-compose.yml` loads `.env` via `env_file:` so every kairix container picks the values up. For production with a cloud secrets manager (Azure KV, AWS Secrets Manager, GCP Secret Manager, 1Password, ECS / Cloud Run / AKS), see [secrets-configuration.md](../operations/secrets-configuration.md) — same kairix, different secret source.

### A3. Point to your documents

```bash
ln -s ~/Documents/my-notes ./documents
```

Your documents stay read-only — kairix never edits them. The one place kairix writes is the agent-memory folder (`kairix remember` and the `memory_write` tool). Create it now so the container (which runs as uid 995) can write there:

```bash
mkdir -p documents/04-Agent-Knowledge
sudo chown -R 995:985 documents/04-Agent-Knowledge
```

The container includes 5,800+ curated reference library documents, so you can start searching immediately and add your own documents later.

### A4. Start everything

```bash
docker compose up -d
```

This starts two services:
- **kairix** — search engine, MCP server (port 8080), and background worker. Both processes run inside the same container under an internal supervisor (s6); the container runs as the `kairix` user (uid 995, gid 985), so files written to bind-mounted volumes are owned by `kairix:kairix` on the host.
- **neo4j** — knowledge graph for people/company queries

> **Upgrading from an earlier release where the worker ran in its own container?** No action needed — `docker compose up -d` swaps both old containers for the unified one. If your host volume directories were written by the old root-owned image, run `sudo chown -R 995:985 /path/to/host/volume` once so the new uid-995 container can read and write them.

> **Port 8080 already in use?** If you're already running caddy, nginx, or another reverse proxy on host 8080, set `KAIRIX_HOST_PORT=8090` (or any unused port) in your `.env` before `docker compose up -d`. See [OPERATIONS §"Deploying behind a reverse proxy"](../operations/OPERATIONS.md#deploying-behind-a-reverse-proxy-caddy--nginx--cloudflared).

### A4b. Set up in your browser (optional)

Once the container is up, open the in-box setup wizard:

```
http://localhost:8080/setup
```

It walks you through picking a provider, adding your key, choosing a documents folder, and a first search — the same steps as the rest of this guide, in a browser. The wizard ships ready to use out of the box; no flag to flip.

- **On the same machine** (localhost): no token needed.
- **From another machine** (you opened the port to the network): you need the operator token. On first boot the container prints a one-time link to its logs (`docker compose logs kairix | grep setup`); follow that link. See the operator-token note in `.env.example` to pin your own token instead.

Prefer a no-browser setup? Skip the wizard and run [`kairix setup`](#no-browser-setup-kairix-setup) on the command line — it drives the same steps headless.

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

### A7. Discover your agents (recommended)

If your knowledge store has per-agent subdirectories — for example `04-Agent-Knowledge/<agent>/` per agent — let kairix discover them rather than hand-authoring the config:

```bash
docker compose exec kairix kairix onboard scan \
    --memory-root /data/documents/04-Agent-Knowledge \
    --yaml > agents-block.yaml
```

Review `agents-block.yaml` — it lists each agent kairix found, the file count, and the most-recent file's date so you can see the proposal is real. Paste the `agents:` block from it into your `kairix.config.yaml`, then validate:

```bash
docker compose exec kairix kairix doctor agent --all
```

Doctor reports `ok` per agent when its configured paths exist and contain recent files, `warn` for staleness or fallback synthesis, `error` for missing paths.

Skip this step if you don't have per-agent subdirectories yet — kairix synthesises sensible defaults and you can add the `agents:` block later.

---

## Path B — Pip install

### B1. Install the package

```bash
pipx install "kairix-agentic-knowledge-mgt[agents]"
```

Or, if you prefer a virtualenv you manage yourself:

```bash
python3 -m venv ~/.venvs/kairix
source ~/.venvs/kairix/bin/activate
pip install "kairix-agentic-knowledge-mgt[agents]"
```

The `[agents]` extra pulls in the MCP server dependencies. Most operators want this.

> **Why pipx / a venv, not `pip install --user`?** Modern distros (Ubuntu 24.04, Homebrew Python, Debian 12+) mark the system Python as externally managed (PEP 668), so a bare `pip install` or `pip install --user` fails with `externally-managed-environment`. pipx creates its own isolated venv per tool and puts `kairix` on your PATH.

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
KAIRIX_PROVIDER_LLM_API_KEY=your-key-here
KAIRIX_PROVIDER_LLM_ENDPOINT=https://your-resource.openai.azure.com
EOF
chmod 600 ~/.config/kairix/secrets/kairix.env
```

Set `KAIRIX_SECRETS_FILE` to point at it (next step). For long-running deployments or cloud secrets managers, see [secrets-configuration.md](../operations/secrets-configuration.md) — covers systemd + cloud-secret-fetch sidecars for every major provider.

Optional: if you want the agent-driven setup flow (an LLM agent writes the config on your behalf), see [agent-driven-setup.md](agent-driven-setup.md).

### B3. Start the worker + MCP server

```bash
# In one terminal — the worker indexes your documents on a schedule.
export KAIRIX_CONFIG_PATH=~/.kairix/kairix.config.yaml
export KAIRIX_SECRETS_FILE=~/.config/kairix/secrets/kairix.env
kairix worker run &

# In another terminal — the MCP server (streamable HTTP at /mcp, legacy /sse).
export KAIRIX_CONFIG_PATH=~/.kairix/kairix.config.yaml
export KAIRIX_SECRETS_FILE=~/.config/kairix/secrets/kairix.env
kairix mcp serve --transport http --port 8080
```

For long-running deployments use a systemd unit — see [`docs/operations/SHARED-HOSTS.md`](../operations/SHARED-HOSTS.md) for the unit-file pointer.

Once `kairix mcp serve` is running, you can set up in your browser at `http://localhost:8080/setup` (use the port you passed to `--port`) — the same in-box wizard described in the Docker path. It's ready out of the box; same-machine access needs no token, remote access needs the operator token. Prefer the command line? See [No-browser setup](#no-browser-setup-kairix-setup) below.

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

## No-browser setup (`kairix setup`)

Prefer the command line, or running somewhere with no browser (a headless server, an SSH session, a CI job)? `kairix setup` drives the same onboarding steps on the terminal — pick a provider, add your key, choose a documents folder — and writes your config. It works whether or not the web wizard is on.

```bash
# Docker
docker compose exec -it kairix kairix setup

# Pip
kairix setup
```

For scripts and CI, add `--non-interactive` to skip every prompt and take the defaults:

```bash
kairix setup --non-interactive
```

Both write the same `kairix.config.yaml` the rest of this guide edits by hand. Run `kairix config validate` afterwards to confirm the result.

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
  ✓ topology_v2_config_valid
  ✓ topology_v2_cc_pairs_registered
  ✓ topology_v2_default_in_scope_field_present
  ✓ topology_v2_wildcard_expansion_resolved
  ✓ sharepoint_credentials_loaded — skipped — connector_sharepoint flag is OFF (default-safe)
  ✓ maintenance_loop_ticking — skipped — maintenance_loop flag is OFF (default-safe)
  ✓ extractor_libraries_importable
──────────────────────────────────────────────────
  All 18 checks passed
```

If any check fails the output names the next command to run.

`agent_knowledge_populated` looks at `04-Agent-Knowledge/**/*.md` under your document root by default. If your knowledge store uses a different layout, set `paths.agent_knowledge_dir` (directory name) and `paths.agent_memory_glob` (file pattern) in `kairix.config.yaml`. See [`how-to-upgrade-kairix`](../operations/runbooks/how-to-upgrade-kairix.md) for the full options.

---

## Topology v2

Topology v2 is the operator-config surface (connectors / credentials / cc_pairs / collections / scope_profiles / skills). The `topology_v2:` block in your config is parsed and applied on every deployment — no flag needed (the old `topology_v2_*` flags retired after the cutover completed). Individual connectors and newer behaviours stay behind default-off feature flags so existing deployments stay bit-for-bit identical until you flip them.

Run `kairix features status` to see the live registry. The current flags:

| Group | Flag | Default | What it does |
|---|---|---|---|
| Connector slots | `connector_sharepoint` | off | Enable SharePoint (Graph drive-delta + extractor dispatch) |
| Connector slots | `connector_slack` | off | Enable Slack (Web API + Socket Mode live events) |
| Connector slots | `connector_github` | off | Enable GitHub (REST + GraphQL + webhook listener) |
| Connector slots | `connector_notion` | off | Enable Notion (workspace pages + database rows) |
| Connector slots | `connector_gmail` | off | Enable Gmail (message body + envelope via REST API) |
| Connector slots | `connector_m365_email_headers` | off | Enable M365 email headers (metadata only, no body) |
| Connector slots | `connector_m365_calendar` | off | Enable M365 calendar (events → entity signals + timeline) |
| Connector slots | `connector_dex_crm` | off | Enable Dex CRM (Person/Org entity signals) |
| Search quality | `entity_summary_indexing_enabled` | off | Project entity summaries into a searchable collection |
| Search quality | `intent_confidence_gated_boosts` | off | Skip ranking boosts when intent classification is ambiguous |
| Background loop | `maintenance_loop` | off | Periodic orphan-vector cleanup (24h default) |
| Observability | `pipeline_status_emit` | off | Write per-item per-stage status rows for `kairix worker inspect` |
| Agent queue | `agent_query_queue` | off | Queue slow searches and carry results to the agent's next call |
| CLI routing | `cli_routes_through_warm_mcp` | **on** | Text-mode CLI subcommands route through a warm MCP when one is responsive |
| Onboarding | `setup_wizard_web` | **on** | Serve the in-box setup wizard at `/setup` (same-machine open is unauthenticated; remote needs the operator token) |

To turn a connector on:

1. **Author the YAML.** Copy [`kairix.config.example.yaml`](https://github.com/three-cubes/kairix/blob/main/kairix.config.example.yaml) at the repo root into your config path (Docker overlay: `kairix.config.local.yaml`; pip: `~/.kairix/kairix.config.yaml`). Run `kairix config validate` after editing to catch shape errors.
2. **Flip the flags.** Add only the flags for the surfaces you want active. The example config's `features:` block lists every flag with a comment beside each.
3. **Set the secrets** for each connector you enabled. See the per-connector recipe in [`how-to-upgrade-kairix`](../operations/runbooks/how-to-upgrade-kairix.md) — each connector lists the exact env-var names + where to put them for Docker and pip.
4. **Apply + verify.** Declared cc_pairs register on worker startup — restart the worker after editing the YAML, then validate:
    ```bash
    docker compose restart kairix    # Docker; pip: restart your `kairix worker run` process
    kairix config validate           # parser + 5 cross-reference checks
    kairix features status           # confirm the flags are on
    kairix onboard check             # all 18 checks should pass
    ```

The connector-credentials onboard checks skip with `ok=True` when their flag is off — a connector is reversible at any time by removing its flag entry from the `features:` block.

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

The MCP server runs on port 8080 (Docker) or whatever you passed to `kairix mcp serve --port` (pip). Any MCP-compatible agent can connect over streamable HTTP at `/mcp` (recommended); the legacy `/sse` endpoint is still served for older clients.

**Claude Desktop / Claude Code:**

```json
{
  "mcpServers": {
    "kairix": {
      "url": "http://localhost:8080/mcp"
    }
  }
}
```

See [connecting-agents.md](connecting-agents.md) for OpenClaw, LangGraph, and other platforms.

If you're an LLM agent reading this and standing kairix up on a user's behalf, see [agent-driven-setup.md](agent-driven-setup.md) — the declarative path optimised for unambiguous machine instructions.

---

## What happens next

- **Documents are indexed automatically** every hour by the worker service. Operator controls: `kairix worker pause` / `resume` / `status`.
- **The MCP server exposes the full set of MCP tools** (see [`docs/user-guide/mcp-tools.md`](../user-guide/mcp-tools.md) for the complete, authoritative reference) — retrieval (`search`, `prep`, `timeline`, `research`, `brief`, `bootstrap`, `facts_about`), entity (`entity`, `entity_suggest`, `entity_validate`), and operator surfaces (`onboard_check`, `worker_status`, `features_status`, `secrets_verify`, `benchmark_run`, and more). Each response carries a `health` envelope so agents know what's online.
- **Agents should call `kairix bootstrap <agent>` at session start** to get a one-shot orientation envelope (role, board, recent memory, active goals, health).
- **Run `kairix onboard check --json`** any time — exit 0 means every check passed; exit 1 prints structured failures with remediation strings.
- **Run `kairix benchmark run reflib`** to benchmark search quality against the bundled gold suite.

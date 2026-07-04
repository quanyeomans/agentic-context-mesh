# Operations Guide

Step-by-step deployment and operations guide for Kairix on a server. This document is the single source of truth for getting a new deployment running and keeping it healthy.

For benchmark methodology and current scores see [EVALUATION.md](../evaluation/EVALUATION.md).

## Operations docs index

| Topic | Where |
|-------|-------|
| Base config, secrets, install, upgrade | this document |
| Fact extractor — how it works, cost model, prompt customisation | [fact-extractor.md](fact-extractor.md) |
| `kairix eval` suite + regression-gate CI pattern | [eval-suite.md](eval-suite.md) |
| Agent-callable MCP fact tools (`ingest_chat`, `facts_about`) | [MCP-ingest-tools.md](MCP-ingest-tools.md) |
| MCP server deployment | [MCP-DEPLOYMENT.md](MCP-DEPLOYMENT.md) |
| MCP client migration (`/sse` → `/mcp`) | [MCP-CLIENT-MIGRATION.md](MCP-CLIENT-MIGRATION.md) |
| Running multiple knowledge stores on one host | [SHARED-HOSTS.md](SHARED-HOSTS.md) |
| Incident runbooks + how-to procedures | [runbooks/INDEX.md](runbooks/INDEX.md) |
| Fact-layer architecture ADR | [`../architecture/fact-layer.md`](../architecture/fact-layer.md) |

---

## Configuration vs Secrets

Not all environment variables are secrets. Configuration values belong in the operator `.env` (at `/etc/kairix/.env` for a system install) or `docker-compose.override.yml`. Secrets belong in your secret store (Key Vault) or `/run/secrets/` (tmpfs).

> **Cross-provider recipes:** the table below covers the Azure Key Vault path on a VM. For the same setup on AWS Secrets Manager / GCP Secret Manager / 1Password / ECS / Cloud Run / AKS CSI / plain Docker `.env`, see [`secrets-configuration.md`](secrets-configuration.md). The canonical naming convention + resolution order live there too.

**Configuration (operator `.env` / compose environment):**

| Variable | Purpose | Default |
|----------|---------|---------|
| `KAIRIX_EMBED_DIMS` | Embedding vector dimensions | `1536` |
| `KAIRIX_AZURE_API_VERSION` | Azure API version override | `2024-12-01-preview` |
| `KAIRIX_NEO4J_URI` | Neo4j connection URI | `bolt://localhost:7687` |
| `KAIRIX_NEO4J_USER` | Neo4j username | `neo4j` |
| `KAIRIX_DOCUMENT_ROOT` | Path to document store | `/data/documents` (Docker), `/var/lib/kairix/documents` (system install), `~/Documents` (user install) |
| `KAIRIX_DB_PATH` | SQLite database path | `~/.cache/kairix/index.sqlite` |
| `KAIRIX_KV_NAME` | Azure Key Vault name (for secret resolution) | — |
| `KAIRIX_MAINTENANCE_SKIP_NOOP_THRESHOLD` | How many quiet indexing cycles (nothing new to index) the app waits before it pauses background upkeep (entity seeding, health checks, wikilinks) | `10` |

**Secrets (Key Vault / /run/secrets/ / env var override):**

| KV name | Env var | Purpose |
|---------|---------|---------|
| `kairix-provider-llm-api-key` | `KAIRIX_PROVIDER_LLM_API_KEY` | LLM API key |
| `kairix-provider-llm-endpoint` | `KAIRIX_PROVIDER_LLM_ENDPOINT` | LLM API endpoint |
| `kairix-provider-llm-model` | `KAIRIX_PROVIDER_LLM_MODEL` | Chat model name |
| `kairix-provider-embed-api-key` | `KAIRIX_PROVIDER_EMBED_API_KEY` | Embed API key (falls back to LLM) |
| `kairix-provider-embed-endpoint` | `KAIRIX_PROVIDER_EMBED_ENDPOINT` | Embed API endpoint (falls back to LLM) |
| `kairix-provider-embed-model` | `KAIRIX_PROVIDER_EMBED_MODEL` | Embed model name |
| `kairix-embed-pool-size` | `KAIRIX_EMBED_POOL_SIZE` | int, default 20 — max concurrent HTTP connections to the embed provider |
| `kairix-embed-pool-keepalive` | `KAIRIX_EMBED_POOL_KEEPALIVE` | int, default 10 — max idle connections kept warm |
| `kairix-embed-pool-expiry-s` | `KAIRIX_EMBED_POOL_EXPIRY_S` | float, default 30.0 — idle-connection expiry (seconds) |
| `kairix-infra-neo4j-password` | `KAIRIX_NEO4J_PASSWORD` | Neo4j password |

The full canonical-name schema (`kairix-<scope>-<area>[-<instance>]-<leaf>`) plus per-connector names live in [`secrets-configuration.md`](secrets-configuration.md). Run `kairix secrets verify` to see every registered credential and whether it resolves.

Resolution order: environment variable > per-file secret (`/run/secrets/<name>`) > bundle file (`/run/secrets/kairix.env`) > Azure Key Vault (`KAIRIX_KV_NAME`).

---

## Environment Configuration

All infrastructure-specific values (Key Vault name, paths, credentials) are passed via environment variables — nothing is hardcoded in the source. The repo ships [`.env.example`](../../.env.example) with every variable documented.

**Setting up your environment file:**

```bash
# On your deployment server (system install): config lives under /etc/kairix
cp .env.example /etc/kairix/.env
chmod 600 /etc/kairix/.env
# Edit with your values (Key Vault name, document root, data dir, etc.)
nano /etc/kairix/.env
```

With Docker compose, put the same values in a `.env` next to `docker-compose.yml` — compose reads it automatically.

**For local dev/testing:**

```bash
cp .env.example .env    # .env is gitignored
# Edit with your values, then:
source .env && kairix search "test query" --agent builder
```

**For GitHub Actions:** add each variable as a repository secret (Settings → Secrets and variables → Actions). CI workflows that need provider credentials read them as `${{ secrets.KAIRIX_PROVIDER_LLM_API_KEY }}` etc.

**Key variables to set first:**

| Variable | What it is |
|---|---|
| `KAIRIX_KV_NAME` | Your Azure Key Vault name |
| `KAIRIX_DOCUMENT_ROOT` | Path to your document store (the knowledge store kairix indexes) |
| `KAIRIX_DATA_DIR` | Where logs and data files go |
| `KAIRIX_WORKSPACE_ROOT` | Agent memory log root (e.g. `/data/workspaces`) |
| `LOG_DIR` | Where deploy.sh and cron wrappers write logs |

See `.env.example` for the complete variable reference.

---

## Prerequisites

### 1. Azure Resources

You need an Azure subscription with the following resources:

**Azure OpenAI resource** (Australia East recommended for data residency)
- Deployment: `text-embedding-3-large` (1536-dim, for embedding)
- Deployment: `gpt-4o-mini` (for briefing, classification, entity extraction)

**Azure Key Vault** — set `KAIRIX_KV_NAME` env var to your vault name (e.g. `my-project-kv`)
- Used to store API credentials at runtime — credentials are never hardcoded or stored in env files

Create the following secrets in Key Vault:

| Secret name | Value |
|---|---|
| `kairix-provider-llm-endpoint` | `https://<your-resource>.cognitiveservices.azure.com/` |
| `kairix-provider-llm-api-key` | Your Azure OpenAI API key |
| `kairix-provider-embed-model` | `text-embedding-3-large` (or your deployment name) |
| `kairix-provider-llm-model` | `gpt-4o-mini` (or your deployment name) |
| `kairix-infra-neo4j-password` | Your Neo4j password |

```bash
# Create secrets (run once, from a machine with Key Vault access)
az keyvault secret set --vault-name ${KAIRIX_KV_NAME} --name kairix-provider-llm-endpoint \
  --value "https://your-resource.cognitiveservices.azure.com/"
az keyvault secret set --vault-name ${KAIRIX_KV_NAME} --name kairix-provider-llm-api-key \
  --value "your-api-key"
az keyvault secret set --vault-name ${KAIRIX_KV_NAME} --name kairix-provider-embed-model \
  --value "text-embedding-3-large"
az keyvault secret set --vault-name ${KAIRIX_KV_NAME} --name kairix-provider-llm-model \
  --value "gpt-4o-mini"
```

### 2. Azure Authentication on the VM

The VM running Kairix must be able to authenticate to Azure Key Vault. Two options:

**Option A: Azure Managed Identity (recommended for production)**
- Assign a system-assigned or user-assigned managed identity to the VM
- Grant the identity `Key Vault Secrets User` role on the Key Vault
- No credentials needed on the VM — `az keyvault secret show` works automatically

```bash
# Verify managed identity auth is working
az keyvault secret show --vault-name ${KAIRIX_KV_NAME} --name kairix-provider-llm-endpoint --query value -o tsv
```

**Option B: Service Principal**
- Create a service principal with Key Vault Secrets User access
- Set `AZURE_CLIENT_ID`, `AZURE_CLIENT_SECRET`, `AZURE_TENANT_ID` in the service env file
- Or use `az login --service-principal` in the deploy script

### 3. Kairix Index

Kairix owns its own SQLite database for full-text search (FTS5) and vector storage (usearch HNSW). No external search tool is required.

```bash
# Run the initial index build
kairix embed

# Verify the index exists
ls ~/.cache/kairix/index.sqlite

# Check index health
kairix onboard check
```

**usearch:** Installed automatically as a pip dependency (`usearch>=2.0`). No manual extension path configuration needed.

### 4. Neo4j (optional — entity graph)

Neo4j Community Edition powers entity boost, alias resolution, and multi-hop query planning. All other kairix features work without it.

Neo4j Community Edition is licensed under **GPL v3**. Kairix communicates via the Bolt protocol using the Apache 2.0 Python driver — no GPL3 code is bundled with kairix.

**Install:**

```bash
# Install script (Docker default; --apt option also available)
bash <(curl -fsSL https://raw.githubusercontent.com/three-cubes/kairix/main/scripts/install-neo4j.sh)

# Or quick Docker start (no install script):
docker run -d --name neo4j -p 7687:7687 \
  -e NEO4J_AUTH=neo4j/$(openssl rand -hex 16) \
  neo4j:5-community
```

After installing, set in the operator `.env` (`/etc/kairix/.env` for a system install, or the `.env` next to `docker-compose.yml`):
```
KAIRIX_NEO4J_URI=bolt://localhost:7687
KAIRIX_NEO4J_USER=neo4j
KAIRIX_NEO4J_PASSWORD=<your-password>
```

For managed deployments where the password is stored in Azure Key Vault as `kairix-infra-neo4j-password`, `kairix-fetch-secrets.service` populates `KAIRIX_NEO4J_PASSWORD` in `/run/secrets/kairix.env` automatically.

Verify Neo4j is reachable:
```bash
kairix onboard check
# → neo4j_reachable: ✓  Neo4j reachable — N nodes in graph
```

### 5. Infrastructure Directories

Create the required directories before first run:

```bash
# Set KAIRIX_DATA_DIR and KAIRIX_WORKSPACE_ROOT to your preferred locations
sudo mkdir -p ${KAIRIX_DATA_DIR:-/var/lib/kairix}/briefing
sudo mkdir -p ${KAIRIX_DATA_DIR:-/var/lib/kairix}/logs
sudo mkdir -p ${KAIRIX_WORKSPACE_ROOT:-/var/lib/kairix/workspaces}
sudo chown -R <service-user>:<service-user> \
  ${KAIRIX_DATA_DIR:-/var/lib/kairix} \
  ${KAIRIX_WORKSPACE_ROOT:-/var/lib/kairix/workspaces}
```

Kairix expects:
- `$KAIRIX_DOCUMENT_ROOT` — document root (kairix indexes this)
- `$KAIRIX_DATA_DIR/briefing/` — session briefings output directory
- `$KAIRIX_DATA_DIR/logs/` — optional query logs (`KAIRIX_LOG_QUERIES=1`)
- `$KAIRIX_WORKSPACE_ROOT/<agent>/memory/` — agent memory logs (required for briefing pipeline)

### 6. Kairix runtime data lives on the largest disk you have

**Do not** rely on the default Docker named volumes (`kairix-data`, `neo4j-data`) when a real connector corpus is attached. The Docker default puts them under `/var/lib/docker/volumes/` on whichever disk holds the Docker root — typically the OS disk. If that disk runs out, the entire host falls over (OOM-kill cascade, systemd misbehaviour).

**Bind-mount kairix runtime onto your largest partition.** The right path depends on your VM's disk shape — `/data` if you have an attached data disk that's the largest; `/var/lib/kairix-runtime` (or similar) on root if the OS disk is the largest one. The 2026-05-26 dogfood VM has a 256 GB OS disk and a 64 GB data disk, so kairix-runtime lives on `/`. A different VM might have the opposite shape.

**Compose override pattern (pick the right host path):**

```yaml
# docker-compose.override.yml — replace HOST_PATH with the right
# directory for YOUR disk layout. The container side stays /data/kairix.
services:
  kairix:
    volumes:
      - <HOST_PATH>:/data/kairix    # e.g. /var/lib/kairix-runtime OR /data/kairix
      - <DOCS_PATH>:/data/documents # docs tree is usually small; either disk works
  kairix:
    volumes:
      - <HOST_PATH>:/data/kairix
      - <DOCS_PATH>:/data/documents
  neo4j:
    volumes:
      - <NEO4J_PATH>:/data          # Neo4j is ~1 GB for typical graphs; small disk fine
```

Prepare the host paths once (replace `<HOST_PATH>` etc. with your chosen directories):

```bash
sudo mkdir -p <HOST_PATH> <DOCS_PATH> <NEO4J_PATH>
sudo chown -R 1000:1000 <HOST_PATH> <DOCS_PATH> <NEO4J_PATH>
```

**Pick the right disk by checking which is biggest:**

```bash
df -h /         # OS disk
df -h /data     # data disk (if attached)
# Pick the one with the most free space for HOST_PATH (the kairix runtime).
```

**What ends up where:**

| Component | Size guide | Suggested disk |
|---|---|---|
| SQLite (`index.sqlite`, `kairix.db`) | grows with index size; ~1 GB / million chunks | largest disk |
| Vector index (`vectors.usearch`) | grows with `embed_dims × chunk_count` | largest disk |
| Neo4j graph | typically ≤1 GB for a single-team corpus | small disk fine |
| Document tree | depends on your source corpus | small disk fine |
| Extractor scratch (`/data/kairix/tmp`) | bounded by per-extraction file size; cleaned on every call | small disk fine |

Fetched source data is **not** kept on disk. When kairix pulls a file from a source, it reads the bytes, extracts the text in memory, and discards the original — only small metadata (source location, content hash, fetch time) is stored, at a few kB per item. So a large corpus needs far less disk than the raw source bytes would suggest.

Temporary scratch files (used while extracting text from PDFs, Office files, etc.) go to the `/data/kairix/tmp` mount, not the small in-memory `/tmp`. They are cleaned up on every call, including on failure, so sustained ingestion does not leak scratch files.

---

## Installation

There are two supported ways to run kairix:

- **Docker compose** (recommended) — one `kairix` app container plus a `neo4j` container.
- **Host / systemd install** — `kairix init --system` lays down the file-system layout (config under `/etc/kairix/`, data under `/var/lib/kairix/`) and a systemd unit.

Either way, first-time setup is driven by a **browser setup wizard**: once the app is up, open it in a browser and it walks you through credentials, document location, and a first search. `kairix onboard check` is the health gate you run afterward to confirm everything is wired.

### Docker Compose (recommended)

Docker compose is the primary deployment method. The base stack (repo root `docker-compose.yml`) starts the `kairix` app container and Neo4j and reads a plain `.env`.

```bash
# Clone and start
git clone https://github.com/three-cubes/kairix.git
cd kairix
cp .env.example .env
# Edit .env — set KAIRIX_PROVIDER_LLM_ENDPOINT + KAIRIX_PROVIDER_LLM_API_KEY
cp kairix.config.example.yaml kairix.config.yaml
ln -s /path/to/your/documents ./documents
docker compose up -d
```

Then open the setup wizard in a browser to finish configuration. See the repo-root [`docker-compose.yml`](../../docker-compose.yml) for the full service definition; `kairix onboard check` runs inside the container on startup. VM deployments that feed secrets through a `/run/secrets/kairix.env` sidecar use the stack in [`docker/`](../../docker/README.md) instead.

#### Deploying behind a reverse proxy (caddy / nginx / Cloudflared)

If host port 8080 is already used by your reverse proxy fronting kairix, set `KAIRIX_HOST_PORT` in `.env` to an unused port:

```bash
# .env
KAIRIX_HOST_PORT=8090
```

The base compose file binds `127.0.0.1:${KAIRIX_HOST_PORT:-8080}:8080` so the env var fully controls the host port — no `docker-compose.override.yml` array-merge gymnastics required ([#331](https://github.com/three-cubes/kairix/issues/331) — the merge-not-replace behaviour caused silent port collisions on reverse-proxy deployments). Point your reverse proxy at `127.0.0.1:8090` and you're done.

If kairix container fails to start with `failed to bind host port 127.0.0.1:8080/tcp: address already in use`, that's the symptom — check `KAIRIX_HOST_PORT` per the runbook entry below.

### systemd unit (recommended for reboot-survivable VM deployments)

If you run kairix as a systemd-managed Docker stack on a long-running VM, copy the example units from `scripts/install/` and tailor them. They pin the correct dependency ordering (kairix.service → kairix-fetch-secrets.service → docker.service) so the deployment self-heals after a reboot rather than crash-looping when `/run/secrets/kairix.env` is empty (resolved in v2026.5.10, see #167).

```bash
# Create the data directory the unit declares in ReadWritePaths= before
# enabling the unit, otherwise systemd's mount namespace setup will fail
# with "Failed to set up mount namespacing: /var/lib/kairix: No such
# file or directory" (exit 226/NAMESPACE) — the unit appears failed
# even though the containers come up cleanly outside systemd. The
# ReadWritePaths= entries in the shipped unit use the `-` prefix so
# they don't strictly require this, but creating the canonical path
# explicitly is cheaper than chasing a phantom-failure unit later.
sudo install -d -m 0750 -o kairix -g kairix /var/lib/kairix

sudo install -m 0644 scripts/install/kairix.service.example /etc/systemd/system/kairix.service
sudo install -m 0644 scripts/install/kairix-fetch-secrets.service.example /etc/systemd/system/kairix-fetch-secrets.service
sudo install -m 0750 -o kairix -g kairix scripts/install/permissions-preflight.sh /etc/kairix/bin/permissions-preflight.sh
sudo systemctl daemon-reload
sudo systemctl enable --now kairix-fetch-secrets.service kairix.service
```

`permissions-preflight.sh` runs as `ExecStartPre=` and:

- Fixes `.env` ownership/mode if root + service-user mismatch (the #167 root cause).
- Fails fast if `/run/secrets/kairix.env` is missing or empty.
- Verifies that `KAIRIX_PROVIDER_LLM_API_KEY`, `KAIRIX_PROVIDER_LLM_ENDPOINT`, `KAIRIX_PROVIDER_EMBED_API_KEY`, `KAIRIX_PROVIDER_EMBED_ENDPOINT` are all populated when the service-env and secrets file are merged.

A failed preflight surfaces as an actionable journalctl line — far more useful than docker compose's "permission denied" loop.

### Health probes

Kairix exposes two health endpoints from the MCP HTTP transport:

| Endpoint | Purpose | Body shape |
|---|---|---|
| `GET /healthz` | Basic liveness — process up, started_at clock past zero. Back-compat. | `{"ready": bool, "uptime_s": int}` |
| `GET /healthz/ready` | Layered readiness — granular capability checks. Use this from your load balancer. | `{"live": bool, "ready": bool, "uptime_s": int, "checks": {"secrets_loaded": bool, "vector_search_capable": bool, "bm25_search_capable": bool, "detail": {...}}}` |

`/healthz/ready` is the actionable signal: `ready=true` means the deployment is fully operational (secrets loaded AND vector search capable). A degraded deployment that has lost vector search will report `ready=false` with `vector_search_capable=false` and a `detail` message — far better than the pre-v2026.5.10 behaviour where `/healthz` returned `ready=true` while semantic search was silently broken (#167).

### Host / systemd install (no Docker)

For hosts without Docker, install kairix directly and let it lay down its own file-system layout:

```bash
# Install the kairix package (with Neo4j + MCP support)
pip install "kairix-agentic-knowledge-mgt[neo4j,agents]"

# Lay down the system layout + systemd unit (requires root)
sudo kairix init --system

# Confirm the layout and unit are in place
kairix init verify
kairix --help
```

`kairix init --system` creates:

- `/etc/kairix/` — config (owned `root:root`); put your `.env` and `kairix.config.yaml` here.
- `/var/lib/kairix/` — state: index database, vector index, logs (owned `kairix:kairix`).
- A systemd unit so the service starts on boot.

Secrets come from the operator `.env` (`/etc/kairix/.env`) or `/run/secrets/`. Keep operator-specific values (Key Vault name, document location, agent names, private benchmark suites) in `/etc/kairix/` — **not** inside the kairix source tree. To remove the layout later, run `sudo kairix uninstall --system`.

### Upgrading

```bash
pip install --upgrade "kairix-agentic-knowledge-mgt[neo4j,agents]"
kairix onboard check   # verify after upgrade
```

For Docker compose deployments, pull the new image and recreate the stack:

```bash
docker compose pull && docker compose up -d
kairix onboard check
```

---

## First-Run Sequence

After the app is up (Docker compose or `kairix init --system`), run these in order on a fresh deployment. Each step must succeed before the next.

### Step 1: Finish setup in the browser wizard

Open the setup wizard in a browser. It walks you through:

- Provider credentials (LLM + embedding API key and endpoint).
- The document location kairix should index.
- A first scan and a first search to confirm everything works.

The wizard writes your configuration to `kairix.config.yaml` and saves secrets to the configured secret store. You don't hand-edit a config file unless you want to.

### Step 2: Confirm the deployment is healthy

```bash
kairix onboard check
```

All checks should pass. In particular look for `secrets_loaded: ✓` and a passing vector-search check. If `vec_failed: true` appears, credentials aren't loaded — re-check the provider key and endpoint you entered in the wizard.

### Step 3: Index your documents

The wizard kicks off a first scan, but you can run indexing yourself at any time:

```bash
# Index a small sample first to confirm the provider responds
kairix embed --limit 20
```

Expected output:
```
INFO  Starting embed — pending=20
INFO  Embedded batch 0 (20 chunks)
INFO  Running post-embed recall check...
INFO  Recall: 4/5 (80%)
INFO  Done — embedded=20 failed=0 duration=12s cost=$0.0005
```

If you see `SchemaVersionError` or `usearch index load failed`, see [Troubleshooting](#troubleshooting).

### Step 4: Index the full document store

```bash
nohup kairix embed >> ${KAIRIX_DATA_DIR:-/var/lib/kairix}/logs/embed.log 2>&1 &
echo "PID: $!"
```

For a typical knowledge store this takes 10–30 minutes and costs roughly $0.30–0.50 at 1536-dim, depending on size. Monitor with:
```bash
tail -f ${KAIRIX_DATA_DIR:-/var/lib/kairix}/logs/embed.log
```

Done when you see: `Done — embedded=N failed=0`

### Step 5: Verify search works

```bash
kairix search "what are our engineering standards" --agent builder --json
```

Expected: `vec_count > 0` and 3–5 results with file paths. If `vec_failed: true`, credentials aren't loaded — run `kairix onboard check`.

### Step 6: Populate the entity graph

```bash
kairix store crawl --document-root /path/to/documents
kairix curator health   # should report entity counts
```

Expected: entity count ≥ 50 for a typical knowledge store.

**Entity-graph management.** Routine maintenance commands:

- **Drop-and-rebuild from the document store** — `kairix store crawl --reset --confirm` runs `MATCH (n) DETACH DELETE n` against the live graph and immediately re-crawls. Pair `--reset` with `--confirm` interactively, or set `KAIRIX_NONINTERACTIVE=1` in scripted pipelines (cron, CI). `--dry-run` previews without writing. The summary prints `Reset: deleted N entities, M relationships before crawl` before the usual crawl counts. Full sequence in [how-to-rebuild-entity-graph](runbooks/how-to-rebuild-entity-graph.md).
- **Override-coverage report** — every `kairix store crawl` (with or without `--reset`) reads `${KAIRIX_DOCUMENT_ROOT}/04-Agent-Knowledge/_entity-overrides.md`, records which allowlist entries fired against the crawled text, and writes a sidecar to `${KAIRIX_DATA_DIR}/entity-override-coverage.json`. Curators inspect this to find dead allowlist entries (`never_matched`) without an O(N) shell loop over `kairix entity get`. Audit details in [kairix-entity-audit Step 4](runbooks/kairix-entity-audit.md#step-4--override-coverage-allowlisted-but-never-matched).

### Step 7: Test briefing

```bash
kairix brief builder
```

Output written to `$KAIRIX_DATA_DIR/briefing/builder-latest.md`. Verify it's non-empty and coherent.

### Step 8: Install agent usage guide

```bash
kairix onboard guide --document-root /path/to/documents
kairix embed --changed   # make the guide searchable
```

This installs the agent usage guide into the document store's shared knowledge base so agents can search for kairix usage instructions.

### Step 9: Register cron jobs

See [Cron Scheduling](#cron-scheduling) below.

---

## Operating the worker

The kairix worker is the background process that runs `kairix embed` on a loop and performs upkeep (entity seeding, health checks, wikilinks) between cycles. It saves its state to `${KAIRIX_DATA_DIR}/worker-state.json`.

### Pause / resume

`kairix worker pause` and `kairix worker resume` toggle a touch-file in `${KAIRIX_DATA_DIR}`. The running worker enters `PAUSED` at the next loop iteration (within 5 s) and stops task work until the flag is removed. Decoupled from the worker process — a stuck worker can still be paused, and the pause survives restarts.

```bash
kairix worker pause     # set the pause flag; worker stops at next iteration
kairix worker resume    # clear the flag; worker resumes within 5 s
```

### Status

`kairix worker status` reads the persisted `WorkerState` JSON and prints phase, embedded total, failed chunks, recall alerts, restart count, uptime. Exit code is the authoritative "worker alive AND has run" signal: `0` if state file present, `1` if missing.

```bash
kairix worker status
# phase: RUNNING     embedded: 4203    failed: 0    recall_alerts: 0    restarts: 1    uptime: 4d 7h
```

### Skip-on-idle maintenance

After `KAIRIX_MAINTENANCE_SKIP_NOOP_THRESHOLD` quiet indexing cycles in a row (default `10`, where nothing new was found to index), the worker pauses background upkeep (entity seeding, health checks, wikilinks) until an indexing cycle does real work again. This lowers CPU and disk use on shared hosts during quiet periods. Tune the value down for busier document stores, up for quieter ones; set to `0` to disable.

### Shared hosts

`docker-compose.example.yml` ships with resource caps (`cpus`, `mem_limit`) suited for a co-located host. The accompanying `SHARED-HOSTS.md` guide covers how `KAIRIX_MAINTENANCE_SKIP_NOOP_THRESHOLD` plus the caps lets kairix share a VM with openclaw and other tooling without stepping on neighbours.

---

## Cron Scheduling

Two recurring jobs are required for a production deployment.

### Secrets in cron jobs

Cron jobs must source credentials from the tmpfs secrets file populated by `kairix-fetch-secrets.service` — do not fetch secrets inline in cron entries.

```bash
# Correct pattern — source the secrets file written by kairix-fetch-secrets.service
source "${KAIRIX_SECRETS_FILE:-/run/secrets/kairix.env}"
kairix embed

# Wrong — fetches secrets inline, requires az CLI auth per-run, leaks into cron logs
export KAIRIX_PROVIDER_LLM_API_KEY=$(az keyvault secret show ...)
```

For production VM deployments, `kairix-fetch-secrets.service` writes Azure credentials to `/run/secrets/kairix.env` (tmpfs) at boot using the VM's managed identity. See [SECURITY.md](../SECURITY.md) for setup detail.

### Incremental embed (new or changed files)

Runs kairix embed incrementally — only embeds files modified since the last run. Exits quickly (embedded=0) when nothing has changed. Schedule to run frequently (e.g. hourly).

Your cron wrapper should source credentials before running:
```bash
# Example wrapper pattern
source "${KAIRIX_SECRETS_FILE:-/run/secrets/kairix.env}"
kairix embed >> ${KAIRIX_DATA_DIR:-/var/lib/kairix}/logs/embed.log 2>&1
```

See [`scripts/cron/kairix-embed.sh`](scripts/cron/kairix-embed.sh) for the reference implementation.

### Nightly entity + relationship seed

Runs the document-store crawler and relationship seeding. Uses GPT-4o-mini for relationship classification. Schedule nightly during low-usage hours.

```bash
# The two commands to run, in order:
kairix store crawl --document-root $KAIRIX_DOCUMENT_ROOT
python scripts/seed-entity-relations.py
```

See [`scripts/cron/`](scripts/cron/) for reference cron wrapper scripts.

### Verifying cron jobs are registered

```bash
crontab -l
```

### Verifying cron jobs ran successfully

```bash
# Check embed log
grep "Done —" ${KAIRIX_DATA_DIR:-/var/lib/kairix}/logs/embed.log | tail -5

# Check entity log
tail -20 ${KAIRIX_DATA_DIR:-/var/lib/kairix}/logs/entity-relation-seed.log
```

---

## Environment Variables

All credentials are fetched from Azure Key Vault at runtime. You can override any value with environment variables for testing:

| Variable | Purpose | Default |
|---|---|---|
| `KAIRIX_PROVIDER_LLM_API_KEY` | Azure OpenAI API key | From Key Vault `kairix-provider-llm-api-key` |
| `KAIRIX_PROVIDER_LLM_ENDPOINT` | Azure OpenAI endpoint URL | From Key Vault `kairix-provider-llm-endpoint` |
| `KAIRIX_PROVIDER_EMBED_MODEL` | Embedding deployment name | From Key Vault `kairix-provider-embed-model` |
| `KAIRIX_DOCUMENT_ROOT` | Path to your document store | `/data/documents` (Docker), `~/Documents` (user install) |
| `KAIRIX_DATA_DIR` | Data directory for logs | `/var/lib/kairix` |
| `KAIRIX_WORKSPACE_ROOT` | Agent memory log root | `/data/workspaces` |
| `KAIRIX_NEO4J_URI` | Neo4j Bolt URI | `bolt://localhost:7687` |
| `KAIRIX_NEO4J_USER` | Neo4j username | `neo4j` |
| `KAIRIX_LOG_QUERIES` | Set to `1` to log all search queries | Off |
| `KAIRIX_USEARCH_PATH` | Override usearch index file path | `~/.cache/kairix/vectors.usearch` |
| `KAIRIX_MAX_CONCURRENCY` | Expected number of searches running at once. Sizes the shared backend-dispatch thread pool (each search runs its BM25 + vector legs in parallel, so the pool holds `2 × concurrency` workers). | CPU-aware: `2 × cores`, bounded to `4`–`32` (pool `8`–`64`). Unknowable core count falls back to `8`. |

**Dispatch concurrency.** Each search fans its BM25 and vector legs out to a shared, process-wide thread pool so the two run in parallel instead of back to back. The pool is sized from the expected concurrent-search load: leave `KAIRIX_MAX_CONCURRENCY` unset and it auto-scales with the host core count (a bigger box uses more parallelism; a 1–2 core box stays small), or set it explicitly to pin the load to your teaming size (the number of agents firing searches at once) — an explicit value is authoritative and overrides the CPU-aware default. On an 8-core box the CPU-aware default resolves to `16` (pool of `32`), so no override is needed; set `KAIRIX_MAX_CONCURRENCY=16` only if you want to pin it. The code in `kairix/core/search/pipeline.py` (`cpu_aware_default_concurrency` / `dispatch_workers_for`) is the source of truth for the exact math.

---

## Summarise Pipeline

After embedding, kairix automatically generates L0 (abstract-level) summaries for each document and stores them in `summaries.db`. These summaries improve search quality by giving the ranking engine a concise representation of each document's content.

- **Runs automatically** after `kairix embed` completes.
- Summaries are stored in a separate SQLite database (`summaries.db` in the data directory).
- To skip summarisation (e.g. for a quick test embed), pass `--skip-summarise`:
  ```bash
  kairix embed --skip-summarise
  ```
- To run summarisation independently:
  ```bash
  kairix summarise
  ```

---

## Optional Extras

### Cross-encoder re-ranking (`[rerank]`)

For MULTI_HOP and SEMANTIC intent queries, kairix can apply a cross-encoder re-ranker after initial retrieval to improve result ordering. This requires the `rerank` extra:

```bash
pip install "kairix-agentic-knowledge-mgt[rerank]"
```

Re-ranking is applied automatically when the extra is installed. Without it, kairix falls back to the standard fusion ranking (no degradation, just no cross-encoder pass).

### Entity suggestion (`[nlp]`)

Entity suggestion uses spaCy NLP models to detect named entities in your documents. This requires the `nlp` extra:

```bash
pip install "kairix-agentic-knowledge-mgt[nlp]"
```

This is required for `kairix entity suggest` to work, including inside Docker containers. The Docker image includes the `nlp` extra by default.

---

## Running the Benchmark

Bundled suites resolve by name — no file path needed:

```bash
kairix benchmark list                # enumerate bundled suites with default collection + description
kairix benchmark run reflib          # bundled name; reads default_collection from suite metadata
kairix benchmark run --suite suites/example.yaml    # custom suite at a path
```

A pass means every gate in `[tool.kairix.benchmark.gates]` (in `pyproject.toml`) is met. Sensible defaults shipped with kairix: overall **≥ 0.78**, temporal **≥ 0.55**, entity **≥ 0.80**, contextual_prep **≥ 0.60**. Run the same suites in CI via the `Reference library benchmark gate` workflow.

For shared production VMs, run inside the **sandboxed eval container** (closes #88) so eval workloads can't starve agent traffic:

```bash
docker compose -f docker-compose.yml \
               -f docker-compose.override.yml \
               -f docker-compose.eval.yml \
               --profile eval \
    run --rm kairix-eval benchmark run --suite suites/reflib-gold.yaml
```

The Dockerfile's entrypoint auto-prepends `kairix`, so the leading `kairix` is omitted from the run command. The eval profile pins `cpus=1.0` and `mem_limit=2g` (vs production's 4 CPU / 3 GB), so a benchmark run can't pin the host. The container exits when the command finishes; `--rm` cleans up. See [docker-compose.eval.yml](../../docker-compose.eval.yml) for the full overlay.

See [EVALUATION.md](../evaluation/EVALUATION.md) for current scores, benchmark methodology, and the graded relevance scoring format.

---

## Monitoring

### What to check daily

```bash
# Embed ran and found/embedded the right number of files
grep "Done —" ${KAIRIX_DATA_DIR:-/var/lib/kairix}/logs/embed.log | tail -3

# No dimension mismatch errors (would indicate concurrent index writers)
grep -i "dimension mismatch" ${KAIRIX_DATA_DIR:-/var/lib/kairix}/logs/embed.log | tail -5

# Entity crawler ran cleanly
tail -5 ${KAIRIX_DATA_DIR:-/var/lib/kairix}/logs/entity-relation-seed.log

# Vector count is stable or growing
kairix onboard check

# Entity graph health
kairix curator health
```

### Key metrics to track

- **Vector count:** Should grow as the document store grows. Sudden drop indicates an index rebuild issue.
- **Entity count:** Grows as new entity stubs are added and the document-store crawler runs. Check with `kairix curator health`.
- **Entity graph density:** Growing node/relationship counts improve entity-aware retrieval.
- **Recall gate:** Post-embed recall check in embed log — should be ≥ 4/5. If < 4/5, run `kairix embed --force`.

### Enabling query logging

```bash
export KAIRIX_LOG_QUERIES=1
# Queries logged to ${KAIRIX_DATA_DIR:-/var/lib/kairix}/logs/queries.jsonl
# Analyse with:
python scripts/analyze_queries.py
```

---

## Troubleshooting

> For cross-cutting retrieval degradation (search wrong/empty, recall canary regressed, multiple `kairix onboard check` failures), use [`../runbooks/kairix-retrieval-health.md`](../runbooks/kairix-retrieval-health.md) — it has the full diagnosis tree and recovery paths. The per-symptom recipes below cover the narrow cases.

### `kairix: command not found`

kairix is not on PATH for the current session.

```bash
# Docker compose: run the CLI inside the app container
docker compose exec kairix kairix --help

# Host install: a system install puts kairix on PATH; confirm the install
kairix init verify
```

If you installed with `kairix init --system` and the command still isn't found, your shell's PATH may not include the install's bin directory — open a fresh login shell or run `kairix init verify` for the exact location.

### `vec_failed: true` — Vector search broken, BM25 only

Provider credentials aren't loaded for the kairix process, so it falls back to keyword (BM25) search only.

```bash
# Diagnose
kairix onboard check

# Confirm the provider key/endpoint resolve
kairix secrets verify
```

Set the LLM/embed key and endpoint in the operator `.env` (or your secret store) and re-run. If you set up via the wizard, re-open it and re-enter the provider credentials.

### `Required secret not available: kairix-provider-llm-api-key`

The embed or briefing command can't find provider credentials. The error message names the canonical secret and the env var to set (`KAIRIX_PROVIDER_LLM_API_KEY`).

```bash
# Check Key Vault auth
az account show
az keyvault secret show --vault-name ${KAIRIX_KV_NAME} --name kairix-provider-llm-api-key --query value -o tsv
```

If `az account show` fails, run `az login` or check the VM's managed identity assignment.

### `usearch index load failed`

The usearch library or index file can't be found.

```bash
# Check if usearch is available
python3 -c "import usearch; print(usearch.__version__)"

# Check if the index file exists
ls -la ~/.cache/kairix/vectors.usearch

# Override index path manually
export KAIRIX_USEARCH_PATH="/path/to/vectors.usearch"
kairix embed --limit 5
```

### `SchemaVersionError: missing columns`

The database schema has changed between versions.

```bash
# Check kairix version
kairix --version

# Run onboard check to confirm the schema is current
kairix onboard check
```

If the schema is out of date after an upgrade, re-run `kairix onboard check` — it reports the mismatch and the migration step to take.

### Vector search returns 0 results

The embed pipeline hasn't run, or the usearch vector index is empty or missing.

```bash
# Check if the usearch index file exists
ls -la ~/.cache/kairix/vectors.usearch
# Should exist and be > 0 bytes

# Check dimensions from metadata
cat ~/.cache/kairix/vectors.meta.json
# Look for "ndim": 1536

# If index is missing: run full re-embed
KAIRIX_PROVIDER_LLM_ENDPOINT="$ENDPOINT" KAIRIX_PROVIDER_LLM_API_KEY="$APIKEY" \
kairix embed --force
```

### `Dimension mismatch` errors in embed log

A dimension mismatch is now auto-detected: the old index is deleted and rebuilt with the correct dimensions on the next embed run. No manual intervention required.

### Embedding model mismatch

If another tool writes embeddings with a different model or dimension to the same database, it causes dimension mismatch errors or `vec=0` results.

**Detect:**
```bash
# Check for mixed embedding models in content_vectors
sqlite3 ~/.cache/kairix/index.sqlite \
  "SELECT model, COUNT(*) FROM content_vectors GROUP BY model;"
# If you see two models, the conflict is active
```

**Fix:**
1. Ensure no other tool writes embeddings to the kairix database — only `kairix embed` should write vectors
2. Force-rebuild Azure vectors: `kairix embed --force`
3. Verify: the query above should show only `text-embedding-3-large`

### Neo4j unavailable

kairix degrades gracefully — entity boost and multi-hop queries are disabled, but search still works.

```bash
# Check Neo4j is running
systemctl status neo4j

# Check connection settings
echo $KAIRIX_NEO4J_URI   # should be bolt://localhost:7687

# Populate entity graph after fixing
kairix store crawl --document-root $KAIRIX_DOCUMENT_ROOT
```

### Nightly entity extraction not running

```bash
# Check cron is registered
crontab -l

# Check log for last run
tail -20 ${KAIRIX_DATA_DIR:-/var/lib/kairix}/logs/entity-relation-seed.log

# Run manually to debug
kairix store crawl --document-root $KAIRIX_DOCUMENT_ROOT
python scripts/seed-entity-relations.py
```

### Briefing output is empty or incoherent

```bash
# Check memory logs exist for the agent
ls /data/workspaces/<agent>/memory/ | tail -5

# Check entity graph has content
kairix curator health

# Run briefing with debug output
KAIRIX_LOG_QUERIES=1 kairix brief <agent> --budget 5000
```

### More detailed runbooks

For deeper diagnostic procedures and less common failure modes, see [`docs/operations/runbooks/INDEX.md`](runbooks/INDEX.md).

---

## Upgrading

**Docker compose:**

```bash
docker compose pull && docker compose up -d
kairix onboard check   # verify after upgrade
```

**Host / systemd install:**

```bash
pip install --upgrade "kairix-agentic-knowledge-mgt[neo4j,agents]"
sudo systemctl restart kairix     # if running under systemd
kairix onboard check
```

`kairix onboard check` is the gate after every upgrade — it confirms the schema is current and reports any migration step still needed. For per-release upgrade notes and migration steps, see [how-to-upgrade-kairix](runbooks/how-to-upgrade-kairix.md).

---

## Data Residency

Knowledge-store content is sent to Azure OpenAI (Australia East) for:
- **Embedding:** All indexed documents sent to `text-embedding-3-large` for indexing
- **Briefing synthesis:** Memory logs + retrieved chunks sent to `gpt-4o-mini`
- **Entity extraction:** Entity stub content sent to `gpt-4o-mini` for NER
- **Relationship classification:** Relationship text sent to `gpt-4o-mini`

No document content is stored externally beyond the duration of the API request. All vectors, entity data, and briefings live in SQLite and Neo4j on your own infrastructure.

See [SECURITY.md](../SECURITY.md) for the full data handling and secret management policy.

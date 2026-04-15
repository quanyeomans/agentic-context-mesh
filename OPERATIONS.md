# Operations Guide

Step-by-step deployment and operations guide for Kairix on a server. This document is the single source of truth for getting a new deployment running and keeping it healthy.

For design rationale see [PRD.md](PRD.md). For benchmark methodology and current scores see [EVALUATION.md](EVALUATION.md).

---

## Environment Configuration

All infrastructure-specific values (vault name, paths, credentials) are passed via environment variables — nothing is hardcoded in the source. The repo ships [`env.example`](../env.example) with every variable documented.

**Setting up your environment file:**

```bash
# On your deployment VM
cp env.example /opt/kairix/service.env
chmod 600 /opt/kairix/service.env
# Edit with your values (Key Vault name, vault path, data dir, etc.)
nano /opt/kairix/service.env

# Source it in each cron job (see Cron Scheduling below)
source /opt/kairix/service.env
```

**For local dev/testing:**

```bash
cp env.example .env    # .env is gitignored
# Edit with your values, then:
source .env && kairix search "test query" --agent builder
```

**For GitHub Actions:** add each variable as a repository secret (Settings → Secrets and variables → Actions). The CI workflows that need Azure credentials read them as `${{ secrets.AZURE_OPENAI_API_KEY }}` etc.

**Key variables to set first:**

| Variable | What it is |
|---|---|
| `KAIRIX_KV_NAME` | Your Azure Key Vault name |
| `KAIRIX_VAULT_ROOT` | Path to your Obsidian vault |
| `KAIRIX_DATA_DIR` | Where logs and data files go |
| `KAIRIX_WORKSPACE_ROOT` | Agent memory log root (e.g. `/data/workspaces`) |
| `LOG_DIR` | Where deploy.sh and cron wrappers write logs |

See `env.example` for the complete variable reference.

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
| `azure-openai-endpoint` | `https://<your-resource>.cognitiveservices.azure.com/` |
| `azure-openai-api-key` | Your Azure OpenAI API key |
| `azure-openai-embedding-deployment` | `text-embedding-3-large` (or your deployment name) |
| `azure-openai-gpt4o-mini-deployment` | `gpt-4o-mini` (or your deployment name) |
| `kairix-neo4j-password` | Your Neo4j password |

```bash
# Create secrets (run once, from a machine with Key Vault access)
az keyvault secret set --vault-name ${KAIRIX_KV_NAME} --name azure-openai-endpoint \
  --value "https://your-resource.cognitiveservices.azure.com/"
az keyvault secret set --vault-name ${KAIRIX_KV_NAME} --name azure-openai-api-key \
  --value "your-api-key"
az keyvault secret set --vault-name ${KAIRIX_KV_NAME} --name azure-openai-embedding-deployment \
  --value "text-embedding-3-large"
az keyvault secret set --vault-name ${KAIRIX_KV_NAME} --name azure-openai-gpt4o-mini-deployment \
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
az keyvault secret show --vault-name ${KAIRIX_KV_NAME} --name azure-openai-endpoint --query value -o tsv
```

**Option B: Service Principal**
- Create a service principal with Key Vault Secrets User access
- Set `AZURE_CLIENT_ID`, `AZURE_CLIENT_SECRET`, `AZURE_TENANT_ID` in the service env file
- Or use `az login --service-principal` in the deploy script

### 3. QMD Installed and Running

Kairix writes into QMD's SQLite index. QMD must be installed and have indexed your vault before running `kairix embed`.

```bash
# Verify QMD is installed
qmd --version        # should report 1.1.2 or later

# Verify QMD index exists
ls ~/.cache/qmd/index.sqlite   # or the path QMD is configured to use

# Verify QMD has indexed content
qmd status
# Expected: N files, N vectors, last updated <date>
```

**sqlite-vec:** Kairix uses the sqlite-vec extension bundled with QMD. No separate installation needed — the extension is discovered automatically from QMD's `node_modules` directory. If the discovery fails at startup, set:
```bash
export SQLITE_VEC_PATH="/path/to/vec0.so"
```

### 4. Neo4j (optional — entity graph)

Neo4j Community Edition powers entity boost, alias resolution, and multi-hop query planning. All other kairix features work without it.

Neo4j Community Edition is licensed under **GPL v3**. Kairix communicates via the Bolt protocol using the Apache 2.0 Python driver — no GPL3 code is bundled with kairix.

**Install:**

```bash
# Install script (Docker default; --apt option also available)
bash <(curl -fsSL https://raw.githubusercontent.com/quanyeomans/agentic-context-mesh/main/scripts/install-neo4j.sh)

# Or quick Docker start (no install script):
docker run -d --name neo4j -p 7687:7687 \
  -e NEO4J_AUTH=neo4j/changeme \
  neo4j:5-community
```

After installing, set in `service.env` or `/opt/kairix/service.env`:
```
KAIRIX_NEO4J_URI=bolt://localhost:7687
KAIRIX_NEO4J_USER=neo4j
KAIRIX_NEO4J_PASSWORD=<your-password>
```

For managed deployments where the password is stored in Azure Key Vault as `kairix-neo4j-password`, `kairix-fetch-secrets.service` populates `KAIRIX_NEO4J_PASSWORD` in `/run/secrets/kairix.env` automatically.

Verify Neo4j is reachable:
```bash
kairix onboard check
# → neo4j_reachable: ✓  Neo4j reachable — N nodes in graph
```

### 5. Infrastructure Directories

Create the required directories before first run:

```bash
sudo mkdir -p /data/kairix/briefing
sudo mkdir -p /data/kairix/logs
sudo mkdir -p /data/workspaces
sudo chown -R <service-user>:<service-user> /data/kairix /data/workspaces
```

Kairix expects:
- `/path/to/vault/` — vault root (QMD indexes this; set your actual vault path via `KAIRIX_VAULT_ROOT`)
- `/data/kairix/briefing/` — session briefings output directory
- `/data/kairix/logs/` — optional query logs (`KAIRIX_LOG_QUERIES=1`)
- `/data/workspaces/<agent>/memory/` — agent memory logs (required for briefing pipeline)

---

## Installation

Kairix is installed as a pip package — the source repo is not required on the VM.

```bash
# One-line deploy (downloads and runs deploy-vm.sh from the public repo)
bash <(curl -fsSL https://raw.githubusercontent.com/quanyeomans/agentic-context-mesh/main/scripts/deploy-vm.sh)
```

This creates `/opt/kairix/.venv/`, installs kairix into it, installs the wrapper script, and creates the `/usr/local/bin/kairix` symlink. After this, `kairix --help` works from any shell.

**Manual install (alternative):**

```bash
# Create venv and install (core)
python3 -m venv /opt/kairix/.venv
/opt/kairix/.venv/bin/pip install kairix

# With Neo4j entity graph support (recommended for full feature set)
/opt/kairix/.venv/bin/pip install "kairix[neo4j]"

# With MCP server for agent integration
/opt/kairix/.venv/bin/pip install "kairix[agents]"

# Verify
/opt/kairix/.venv/bin/kairix --help
```

### Operator configuration

Kairix itself is the retrieval engine. Operator-specific configuration (vault paths, Azure credentials, agent names, private benchmark suites) is kept separately — **not inside the kairix source tree**.

The expected layout on the VM:
```
/opt/kairix/
  .venv/              ← kairix package installed here
  bin/
    kairix-wrapper.sh ← env loader; /usr/local/bin/kairix symlinks here
  service.env         ← operator config (KAIRIX_KV_NAME, KAIRIX_VAULT_ROOT, etc.)
  secrets/            ← optional: pre-fetched secrets for non-Docker deployments
```

For production deployments: operator config (service.env, private benchmark suites) should live in a separate private configuration repo, not in the kairix package.

### Upgrading

```bash
/opt/kairix/.venv/bin/pip install --upgrade git+https://github.com/quanyeomans/agentic-context-mesh
kairix onboard check   # verify after upgrade
```

---

## Wrapper Script and PATH Setup

**This is required for agents.** If you skip this, agents calling `kairix` will get either "command not found" or vector search failures (BM25-only fallback) because the raw Python binary has no environment loaded.

The kairix wrapper (`scripts/kairix-wrapper.sh`) loads `service.env` and `/run/secrets/kairix.env` before exec'ing the real binary. The system symlink must point to the wrapper, not the Python binary.

### Automated (recommended)

```bash
bash scripts/deploy-vm.sh
```

This installs the wrapper, creates/updates the symlink, and sets up `/etc/profile.d/kairix.sh` so every shell and agent exec context has kairix on PATH.

### Manual

```bash
# Install wrapper
sudo mkdir -p /opt/kairix/bin
sudo cp scripts/kairix-wrapper.sh /opt/kairix/bin/kairix-wrapper.sh
sudo chmod 755 /opt/kairix/bin/kairix-wrapper.sh

# Create or update the symlink (replace existing if it points to raw Python binary)
sudo ln -sf /opt/kairix/bin/kairix-wrapper.sh /usr/local/bin/kairix

# Add to PATH for all sessions
sudo bash -c 'echo "export PATH=/usr/local/bin:\$PATH" > /etc/profile.d/kairix.sh'
sudo chmod 644 /etc/profile.d/kairix.sh

# Verify the symlink points to the wrapper (not the Python binary)
ls -la /usr/local/bin/kairix
readlink /usr/local/bin/kairix
# Should show: /opt/kairix/bin/kairix-wrapper.sh
```

### Verify wrapper is working

```bash
kairix onboard check
```

All checks should pass. Specifically look for `wrapper_installed: ✓` and `secrets_loaded: ✓`. If `vec_failed: true` appears in the vector search check, the wrapper isn't loading secrets — check that `service.env` has `KAIRIX_KV_NAME` set.

---

## First-Run Sequence

Run these in order on a fresh deployment. Each step must succeed before the next.

### Step 1: Deploy wrapper and PATH

```bash
bash scripts/deploy-vm.sh
```

Or follow the manual steps in [Wrapper Script and PATH Setup](#wrapper-script-and-path-setup).

### Step 2: Populate service.env

```bash
# If service.env doesn't exist yet
cp env.example /opt/kairix/service.env
nano /opt/kairix/service.env
# Set: KAIRIX_KV_NAME, KAIRIX_VAULT_ROOT, KAIRIX_DATA_DIR, KAIRIX_WORKSPACE_ROOT
```

### Step 3: Verify Azure credentials

```bash
source /opt/kairix/service.env

ENDPOINT=$(az keyvault secret show --vault-name ${KAIRIX_KV_NAME} \
  --name azure-openai-endpoint --query value -o tsv)
APIKEY=$(az keyvault secret show --vault-name ${KAIRIX_KV_NAME} \
  --name azure-openai-api-key --query value -o tsv)

echo "Endpoint: ${ENDPOINT:0:40}..."
echo "Key: ${APIKEY:0:8}..."
```

Both must return values. If either is empty, check Azure CLI auth and Key Vault access policy.

### Step 4: Run a test embed (first 20 chunks)

```bash
AZURE_OPENAI_ENDPOINT="$ENDPOINT" \
AZURE_OPENAI_API_KEY="$APIKEY" \
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

If you see `SchemaVersionError` or `sqlite-vec extension load failed`, see [Troubleshooting](#troubleshooting).

### Step 5: Full vault embed

```bash
AZURE_OPENAI_ENDPOINT="$ENDPOINT" \
AZURE_OPENAI_API_KEY="$APIKEY" \
nohup kairix embed >> /data/kairix/logs/embed.log 2>&1 &
echo "PID: $!"
```

For a ~2,800 file vault this takes 15–20 minutes and costs ~$0.30–0.40 at 1536-dim. Monitor with:
```bash
tail -f /data/kairix/logs/embed.log
```

Done when you see: `Done — embedded=N failed=0`

### Step 6: Verify search works

```bash
kairix search "what are our engineering standards" --agent builder --json
```

Expected: `vec_count > 0` and 3–5 results with file paths. If `vec_failed: true`, the wrapper isn't loading credentials — run `kairix onboard check`.

### Step 7: Populate the entity graph

```bash
kairix vault crawl --vault-root /path/to/vault
kairix curator health   # should report entity counts
```

Expected: entity count ≥ 50 for a typical vault.

### Step 8: Test briefing

```bash
AZURE_OPENAI_ENDPOINT="$ENDPOINT" \
AZURE_OPENAI_API_KEY="$APIKEY" \
kairix brief builder
```

Output written to `/data/kairix/briefing/builder-latest.md`. Verify it's non-empty and coherent.

### Step 9: Install agent usage guide

```bash
kairix onboard guide --vault-root /path/to/vault
kairix embed --changed   # make the guide searchable
```

This installs `docs/agent-usage-guide.md` into the vault's shared knowledge base so agents can search for kairix usage instructions.

### Step 10: Register cron jobs

See [Cron Scheduling](#cron-scheduling) below.

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
export AZURE_OPENAI_API_KEY=$(az keyvault secret show ...)
```

For production VM deployments, `kairix-fetch-secrets.service` writes Azure credentials to `/run/secrets/kairix.env` (tmpfs) at boot using the VM's managed identity. See [SECURITY.md](SECURITY.md) for setup detail.

### Hourly embed (new vault files)

Runs kairix embed incrementally — only embeds files modified since the last run. Exits quickly (embedded=0) when nothing has changed.

```cron
15 * * * * /opt/kairix/cron/kairix-embed.sh >> /data/kairix/logs/embed.log 2>&1
```

`/opt/kairix/cron/kairix-embed.sh` sources `/run/secrets/kairix.env` then runs `kairix embed`. This script is deployed by `apply-kairix-config.sh` on managed deployments.

### Nightly entity + relationship seed (03:00 AEST / 17:00 UTC)

Runs vault crawler and relationship seeding. Uses GPT-4o-mini for relationship classification.

```cron
0 17 * * * /opt/kairix/cron/entity-relation-seed.sh >> /data/kairix/logs/entity-relation-seed.log 2>&1
```

The shell script (`entity-relation-seed.sh`) sources `/run/secrets/kairix.env` and runs:
1. `kairix vault crawl --vault-root $KAIRIX_VAULT_ROOT`
2. `python /opt/kairix/scripts/seed-entity-relations.py`

Add both crons with `crontab -e` as the service user.

### Verifying cron jobs are registered

```bash
crontab -l
# Should show both entries
```

### Verifying cron jobs ran successfully

```bash
# Check embed log (should show runs at :15 of each hour)
grep "Done —" /data/kairix/logs/embed.log | tail -5

# Check entity log (should show a run at 03:00 AEST)
tail -20 /data/kairix/logs/entity-relation-seed.log
```

---

## Environment Variables

All credentials are fetched from Azure Key Vault at runtime. You can override any value with environment variables for testing:

| Variable | Purpose | Default |
|---|---|---|
| `AZURE_OPENAI_API_KEY` | Azure OpenAI API key | From Key Vault `azure-openai-api-key` |
| `AZURE_OPENAI_ENDPOINT` | Azure OpenAI endpoint URL | From Key Vault `azure-openai-endpoint` |
| `AZURE_OPENAI_EMBED_DEPLOYMENT` | Embedding deployment name | From Key Vault `azure-openai-embedding-deployment` |
| `KAIRIX_VAULT_ROOT` | Path to Obsidian vault | `/path/to/vault` |
| `KAIRIX_DATA_DIR` | Data directory for logs | `/data/kairix` |
| `KAIRIX_WORKSPACE_ROOT` | Agent memory log root | `/data/workspaces` |
| `KAIRIX_NEO4J_URI` | Neo4j Bolt URI | `bolt://localhost:7687` |
| `KAIRIX_NEO4J_USER` | Neo4j username | `neo4j` |
| `KAIRIX_LOG_QUERIES` | Set to `1` to log all search queries | Off |
| `SQLITE_VEC_PATH` | Override sqlite-vec `.so` path | Auto-discovered from QMD node_modules |

---

## Running the Benchmark

```bash
kairix benchmark run --suite suites/example.yaml
```

See [EVALUATION.md](EVALUATION.md) for current scores, benchmark methodology, and the graded relevance scoring format.

---

## Monitoring

### What to check daily

```bash
# Embed ran and found/embedded the right number of files
grep "Done —" /data/kairix/logs/embed.log | tail -3

# No dimension mismatch errors (would indicate QMD cron conflict)
grep -i "dimension mismatch" /data/kairix/logs/embed.log | tail -5

# Entity crawler ran cleanly
tail -5 /data/kairix/logs/entity-relation-seed.log

# Vector count is stable or growing
qmd status

# Entity graph health
kairix curator health
```

### Key metrics to track

- **Vector count:** Should grow as vault grows. Sudden drop indicates QMD reindex issue.
- **Entity count:** Grows as new entity stubs are added and vault crawler runs. Check with `kairix curator health`.
- **Entity graph density:** Growing node/relationship counts improve entity-aware retrieval.
- **Recall gate:** Post-embed recall check in embed log — should be ≥ 4/5. If < 4/5, run `kairix embed --force`.

### Enabling query logging

```bash
export KAIRIX_LOG_QUERIES=1
# Queries logged to /data/kairix/logs/queries.jsonl
# Analyse with:
.venv/bin/python scripts/analyze_queries.py
```

---

## Troubleshooting

### `kairix: command not found`

kairix is not on PATH for the current session.

```bash
# Quick fix for current session
export PATH=/usr/local/bin:$PATH
kairix --help

# Permanent fix: run the deploy script
bash scripts/deploy-vm.sh

# Or manually check where the symlink is
ls -la /usr/local/bin/kairix
```

For agent exec contexts specifically (agents running commands via shell), ensure `/etc/profile.d/kairix.sh` exists and contains the PATH export. Non-login shells don't source `/etc/profile.d/` automatically — the cron wrapper or agent exec script must `source /etc/profile.d/kairix.sh` or set PATH explicitly.

### `vec_failed: true` — Vector search broken, BM25 only

Azure credentials aren't loaded for the kairix process.

```bash
# Diagnose
kairix onboard check

# Most common cause: symlink points to raw Python binary
ls -la /usr/local/bin/kairix
readlink /usr/local/bin/kairix
# If this shows .venv/bin/kairix, the wrapper isn't installed:
bash scripts/deploy-vm.sh

# Alternative: verify manually
which kairix
head -1 $(which kairix)
# Should show: #!/usr/bin/env bash  (not #!/path/to/python)
```

### `AZURE_OPENAI_API_KEY not set`

The embed or briefing command can't find Azure credentials.

```bash
# Check Key Vault auth
az account show
az keyvault secret show --vault-name ${KAIRIX_KV_NAME} --name azure-openai-api-key --query value -o tsv
```

If `az account show` fails, run `az login` or check the VM's managed identity assignment.

### `sqlite-vec extension load failed`

The sqlite-vec `.so` file can't be found.

```bash
# Check if QMD is installed and has sqlite-vec bundled
which qmd
find $(dirname $(which qmd))/../ -name "vec0.so" 2>/dev/null

# Override manually
export SQLITE_VEC_PATH="/path/to/vec0.so"
kairix embed --limit 5
```

### `SchemaVersionError: missing columns`

QMD has been upgraded to a version with a changed schema.

```bash
# Check QMD version
qmd --version

# Run schema compatibility tests
.venv/bin/pytest tests/ -k "schema" -v

# If tests pass, bump the version in schema.py and pyproject.toml
```

### Vector search returns 0 results

The embed pipeline hasn't run, or the vectors_vec table is empty or wrong-dimension.

```bash
# Check vector count
sqlite3 ~/.cache/qmd/index.sqlite "SELECT COUNT(*) FROM vectors_vec;"
# Should be > 0

# Check dimensions
sqlite3 ~/.cache/qmd/index.sqlite \
  "SELECT length(embedding)/4 FROM vectors_vec LIMIT 1;"
# Should be 1536

# If 0 vectors: run full re-embed
AZURE_OPENAI_ENDPOINT="$ENDPOINT" AZURE_OPENAI_API_KEY="$APIKEY" \
kairix embed --force
```

### `Dimension mismatch` errors in embed log

QMD's reindex cron (`qmd update`) ran during a long embed run and recreated `vectors_vec` with different dimensions. This is handled automatically by the retry logic in embed.py (ADR-M08), but if it's happening frequently:

```bash
# Check QMD cron schedule — if it runs during long embed windows, offset it
crontab -l | grep qmd

# Schedule embed cron to run just after QMD reindex, not during it
```

### Neo4j unavailable

kairix degrades gracefully — entity boost and multi-hop queries are disabled, but search still works.

```bash
# Check Neo4j is running
systemctl status neo4j

# Check connection settings
echo $KAIRIX_NEO4J_URI   # should be bolt://localhost:7687

# Populate entity graph after fixing
kairix vault crawl --vault-root $KAIRIX_VAULT_ROOT
```

### Nightly entity extraction not running

```bash
# Check cron is registered
crontab -l

# Check log for last run
tail -20 /data/kairix/logs/entity-relation-seed.log

# Run manually to debug
/opt/kairix/cron-scripts/entity-relation-seed.sh
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

---

## Upgrading

### Upgrading Kairix

```bash
# Upgrade to latest
/opt/kairix/.venv/bin/pip install --upgrade kairix

# Or pin to a specific version
/opt/kairix/.venv/bin/pip install "kairix==0.9.1"

# Verify
kairix onboard check
```

If the wrapper script has changed in the new version, re-run:
```bash
bash scripts/deploy-vm.sh --skip-smoke   # re-downloads and re-installs wrapper
```

### Upgrading QMD

```bash
# Check QMD changelog for schema changes first
# Then run compatibility tests from the kairix source (clone temporarily if needed)
git clone --depth=1 https://github.com/quanyeomans/agentic-context-mesh /tmp/kairix-src
cd /tmp/kairix-src
/opt/kairix/.venv/bin/pytest tests/ -k "schema" -v

# If tests pass, the upgrade is compatible
rm -rf /tmp/kairix-src
```

---

## Data Residency

Vault content is sent to Azure OpenAI (Australia East) for:
- **Embedding:** All vault documents sent to `text-embedding-3-large` for indexing
- **Briefing synthesis:** Memory logs + retrieved chunks sent to `gpt-4o-mini`
- **Entity extraction:** Entity stub content sent to `gpt-4o-mini` for NER
- **Relationship classification:** Relationship text sent to `gpt-4o-mini`

No vault content is stored externally beyond the duration of the API request. All vectors, entity data, and briefings live in SQLite and Neo4j on your own infrastructure.

See [SECURITY.md](SECURITY.md) for the full data handling and secret management policy.

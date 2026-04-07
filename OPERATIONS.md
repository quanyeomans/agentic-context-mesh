# Operations Guide

Step-by-step deployment and operations guide for Mnemosyne on an a server. This document is the single source of truth for getting a new deployment running and keeping it healthy.

For design rationale see [PRD.md](PRD.md). For benchmark methodology see [EVALUATION.md](EVALUATION.md). For QMD schema compatibility see [QMD_COMPAT.md](QMD_COMPAT.md).

---

## Environment Configuration

All infrastructure-specific values (vault name, paths, credentials) are passed via environment variables — nothing is hardcoded in the source. The repo ships [`env.example`](../env.example) with every variable documented.

**Setting up your environment file:**

```bash
# On your deployment VM
cp env.example /opt/mnemosyne/service.env
chmod 600 /opt/mnemosyne/service.env
# Edit with your values (Key Vault name, vault path, data dir, etc.)
nano /opt/mnemosyne/service.env

# Source it in each cron job (see Cron Scheduling below)
source /opt/mnemosyne/service.env
```

**For local dev/testing:**

```bash
cp env.example .env    # .env is gitignored
# Edit with your values, then:
source .env && mnemosyne search "test query" --agent builder
```

**For GitHub Actions:** add each variable as a repository secret (Settings → Secrets and variables → Actions). The CI workflows that need Azure credentials read them as `${{ secrets.AZURE_OPENAI_API_KEY }}` etc.

**Key variables to set first:**

| Variable | What it is |
|---|---|
| `MNEMOSYNE_KV_NAME` | Your Azure Key Vault name |
| `MNEMOSYNE_VAULT_ROOT` | Path to your Obsidian vault |
| `MNEMOSYNE_DATA_DIR` | Where entities.db and logs go |
| `LOG_DIR` | Where deploy.sh and cron wrappers write logs |

See `env.example` for the complete variable reference.

---

## Prerequisites

### 1. Azure Resources

You need an Azure subscription with the following resources:

**Azure OpenAI resource** (Australia East recommended for data residency)
- Deployment: `text-embedding-3-large` (1536-dim, for embedding)
- Deployment: `gpt-4o-mini` (for briefing, classification, entity extraction)

**Azure Key Vault** — set `KV_NAME` env var to your vault name (e.g. `my-project-kv`)
- Used to store API credentials at runtime — credentials are never hardcoded or stored in env files

Create the following secrets in Key Vault:

| Secret name | Value |
|---|---|
| `azure-openai-endpoint` | `https://<your-resource>.cognitiveservices.azure.com/` |
| `azure-openai-api-key` | Your Azure OpenAI API key |
| `azure-openai-embedding-deployment` | `text-embedding-3-large` (or your deployment name) |
| `azure-openai-gpt4o-mini-deployment` | `gpt-4o-mini` (or your deployment name) |

```bash
# Create secrets (run once, from a machine with Key Vault access)
az keyvault secret set --vault-name ${KV_NAME} --name azure-openai-endpoint \
  --value "https://your-resource.cognitiveservices.azure.com/"
az keyvault secret set --vault-name ${KV_NAME} --name azure-openai-api-key \
  --value "your-api-key"
az keyvault secret set --vault-name ${KV_NAME} --name azure-openai-embedding-deployment \
  --value "text-embedding-3-large"
az keyvault secret set --vault-name ${KV_NAME} --name azure-openai-gpt4o-mini-deployment \
  --value "gpt-4o-mini"
```

### 2. Azure Authentication on the VM

The VM running Mnemosyne must be able to authenticate to Azure Key Vault. Two options:

**Option A: Azure Managed Identity (recommended for production)**
- Assign a system-assigned or user-assigned managed identity to the VM
- Grant the identity `Key Vault Secrets User` role on the Key Vault
- No credentials needed on the VM — `az keyvault secret show` works automatically

```bash
# Verify managed identity auth is working
az keyvault secret show --vault-name ${KV_NAME} --name azure-openai-endpoint --query value -o tsv
```

**Option B: Service Principal**
- Create a service principal with Key Vault Secrets User access
- Set `AZURE_CLIENT_ID`, `AZURE_CLIENT_SECRET`, `AZURE_TENANT_ID` in the service env file (e.g. `/opt/<service-user>/env/service.env`)
- Or use `az login --service-principal` in the deploy script

### 3. QMD Installed and Running

Mnemosyne writes into QMD's SQLite index. QMD must be installed and have indexed your vault before running `mnemosyne embed`.

```bash
# Verify QMD is installed
qmd --version        # should report 1.1.2 or later

# Verify QMD index exists
ls ~/.cache/qmd/index.sqlite   # or the path QMD is configured to use

# Verify QMD has indexed content
qmd status
# Expected: N files, N vectors, last updated <date>
```

**sqlite-vec:** Mnemosyne uses the sqlite-vec extension bundled with QMD. No separate installation needed — the extension is discovered automatically from QMD's `node_modules` directory. If the discovery fails at startup, set:
```bash
export SQLITE_VEC_PATH="/path/to/vec0.so"
```

See [QMD_COMPAT.md](QMD_COMPAT.md) for compatible QMD versions and schema details.

### 4. Infrastructure Directories

Create the required directories on the VM before first run:

```bash
sudo mkdir -p /data/mnemosyne/briefing
sudo mkdir -p /data/mnemosyne/logs
sudo chown -R <service-user>:<service-user> /data/mnemosyne
```

Mnemosyne expects:
- `/data/obsidian-vault/` — vault root (QMD indexes this; set your actual vault path via `VAULT_ROOT` if different)
- `/data/mnemosyne/entities.db` — entity graph (created automatically on first run)
- `/data/mnemosyne/briefing/` — session briefings output directory
- `/data/mnemosyne/logs/` — optional query logs (`MNEMOSYNE_LOG_QUERIES=1`)
- `/data/workspaces/<agent>/memory/` — agent memory logs (required for briefing pipeline)

---

## Installation

```bash
# Clone to the VM
git clone https://github.com/quanyeomans/agentic-context-mesh /data/tools/qmd-azure-embed
cd /data/tools/qmd-azure-embed

# Create virtualenv and install
python3 -m venv .venv
.venv/bin/pip install -e .

# Verify
.venv/bin/mnemosyne --help
```

---

## First-Run Sequence

Run these in order on a fresh deployment. Each step must succeed before the next.

### Step 1: Verify Azure credentials

```bash
source /opt/<service-user>/env/service.env

ENDPOINT=$(az keyvault secret show --vault-name ${KV_NAME} \
  --name azure-openai-endpoint --query value -o tsv)
APIKEY=$(az keyvault secret show --vault-name ${KV_NAME} \
  --name azure-openai-api-key --query value -o tsv)

echo "Endpoint: ${ENDPOINT:0:40}..."
echo "Key: ${APIKEY:0:8}..."
```

Both must return values. If either is empty, check Azure CLI auth and Key Vault access policy.

### Step 2: Run a test embed (first 20 chunks)

```bash
cd /data/tools/qmd-azure-embed

AZURE_OPENAI_ENDPOINT="$ENDPOINT" \
AZURE_OPENAI_API_KEY="$APIKEY" \
.venv/bin/mnemosyne embed --limit 20
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

### Step 3: Full vault embed

```bash
AZURE_OPENAI_ENDPOINT="$ENDPOINT" \
AZURE_OPENAI_API_KEY="$APIKEY" \
nohup .venv/bin/mnemosyne embed >> /data/mnemosyne/logs/embed.log 2>&1 &
echo "PID: $!"
```

For a ~2,800 file vault this takes 15–20 minutes and costs ~$0.30–0.40 at 1536-dim. Monitor with:
```bash
tail -f /data/mnemosyne/logs/embed.log
```

Done when you see: `Done — embedded=N failed=0`

### Step 4: Verify search works

```bash
.venv/bin/mnemosyne search "what are our engineering standards" --agent builder
```

Expected: 3–5 results with file paths and relevance snippets. If you get 0 results, the embed didn't complete or the QMD index path is wrong.

### Step 5: Seed the entity graph

```bash
.venv/bin/mnemosyne entity extract --changed
.venv/bin/mnemosyne entity list   # should report entity count
```

Expected: entity count ≥ 100 for a typical vault. The entity extraction runs LLM calls for named entity recognition — requires Azure credentials.

### Step 6: Test briefing

```bash
AZURE_OPENAI_ENDPOINT="$ENDPOINT" \
AZURE_OPENAI_API_KEY="$APIKEY" \
.venv/bin/mnemosyne brief builder
```

Output written to `/data/mnemosyne/briefing/builder-latest.md`. Verify it's non-empty and coherent.

### Step 7: Register cron jobs

See [Cron Scheduling](#cron-scheduling) below.

---

## Cron Scheduling

Two recurring jobs are required for a production deployment.

### Hourly embed (new vault files)

Runs mnemosyne embed incrementally — only embeds files modified since the last run. Exits quickly (embedded=0) when nothing has changed.

```cron
15 * * * * source /opt/<service-user>/env/service.env \
  && export AZURE_OPENAI_ENDPOINT=$(az keyvault secret show --vault-name ${KV_NAME} --name azure-openai-endpoint --query value -o tsv 2>/dev/null) \
  && export AZURE_OPENAI_API_KEY=$(az keyvault secret show --vault-name ${KV_NAME} --name azure-openai-api-key --query value -o tsv 2>/dev/null) \
  && cd /data/tools/qmd-azure-embed \
  && .venv/bin/mnemosyne embed >> /data/mnemosyne/logs/embed.log 2>&1
```

### Nightly entity + relationship seed (03:00 AEST / 17:00 UTC)

Runs incremental entity extraction and relationship seeding. Uses GPT-4o-mini for relationship classification.

```cron
0 17 * * * /data/tc-agent-zone/cron-scripts/entity-relation-seed.sh >> /data/mnemosyne/logs/entity-relation-seed.log 2>&1
```

The shell script (`entity-relation-seed.sh`) handles Key Vault credential fetching and runs:
1. `mnemosyne entity extract --changed`
2. `python scripts/seed-entity-relations.py`

Add both crons with `crontab -e` as the `<service-user>` service user.

### Verifying cron jobs are registered

```bash
crontab -l
# Should show both entries
```

### Verifying cron jobs ran successfully

```bash
# Check embed log (should show runs at :15 of each hour)
grep "Done —" /data/mnemosyne/logs/embed.log | tail -5

# Check entity log (should show a run at 03:00 AEST)
tail -20 /data/mnemosyne/logs/entity-relation-seed.log
```

---

## Environment Variables

All credentials are fetched from Azure Key Vault at runtime. You can override any value with environment variables for testing:

| Variable | Purpose | Default |
|---|---|---|
| `AZURE_OPENAI_API_KEY` | Azure OpenAI API key | From Key Vault `azure-openai-api-key` |
| `AZURE_OPENAI_ENDPOINT` | Azure OpenAI endpoint URL | From Key Vault `azure-openai-endpoint` |
| `AZURE_OPENAI_EMBED_DEPLOYMENT` | Embedding deployment name | From Key Vault `azure-openai-embedding-deployment` |
| `VAULT_ROOT` | Path to Obsidian vault | `/data/obsidian-vault` |
| `MNEMOSYNE_LOG_QUERIES` | Set to `1` to log all search queries to `queries.jsonl` | Off |
| `SQLITE_VEC_PATH` | Override sqlite-vec `.so` path | Auto-discovered from QMD node_modules |

---

## Running the Benchmark

The v2-real-world benchmark suite measures retrieval quality across 263 real queries from agent session logs.

```bash
cd /data/tools/qmd-azure-embed

# Run R1 benchmark (results saved to vault benchmark-results/ folder)
.venv/bin/python scripts/run-benchmark-v2.py R2-description \
  --suite suites/v2-real-world.yaml

# View latest results
tail -30 /data/tc-agent-zone/logs/benchmark-R1.log
```

**Current scores (R1, 2026-04-07):**

| Category | NDCG@10 | Target |
|---|---|---|
| semantic | 0.831 | ≥ 0.80 ✅ |
| entity | 0.763 | ≥ 0.70 ✅ |
| keyword | 0.791 | ≥ 0.75 ✅ |
| multi_hop | 0.585 | ≥ 0.65 🟡 |
| procedural | 0.389 | ≥ 0.55 🔴 |
| temporal | 0.378 | ≥ 0.55 🔴 |
| **overall** | **0.7756** | **≥ 0.74 ✅** |

To rebuild gold paths after a large vault restructure:
```bash
AZURE_OPENAI_ENDPOINT="$ENDPOINT" AZURE_OPENAI_API_KEY="$APIKEY" \
PYTHONUNBUFFERED=1 .venv/bin/python -u scripts/build-eval-gold.py \
  > /data/mnemosyne/logs/build-eval-gold.log 2>&1 &
```
This takes ~80 minutes and costs ~$0.27 for 388 queries.

---

## Monitoring

### What to check daily

```bash
# Embed ran and found/embedded the right number of files
grep "Done —" /data/mnemosyne/logs/embed.log | tail -3

# No dimension mismatch errors (would indicate QMD cron conflict)
grep -i "dimension mismatch" /data/mnemosyne/logs/embed.log | tail -5

# Entity extraction ran cleanly
tail -5 /data/mnemosyne/logs/entity-relation-seed.log

# Vector count is stable or growing
qmd status
```

### Key metrics to track

- **Vector count:** Should grow as vault grows. Sudden drop indicates QMD reindex issue.
- **Entity count:** Grows as new entity stubs are written. Check with `mnemosyne entity list`.
- **Relationship count:** Grows as nightly seed runs. Check with sqlite CLI: `sqlite3 /data/mnemosyne/entities.db "SELECT COUNT(*) FROM entity_relationships;"`
- **Recall gate:** Post-embed recall check in embed log — should be ≥ 4/5. If < 4/5, run `mnemosyne embed --force`.

### Enabling query logging

```bash
export MNEMOSYNE_LOG_QUERIES=1
# Queries logged to /data/mnemosyne/logs/queries.jsonl
# Analyse with:
.venv/bin/python scripts/analyze_queries.py
```

---

## Troubleshooting

### `AZURE_OPENAI_API_KEY not set`

The embed or briefing command can't find Azure credentials.

```bash
# Check Key Vault auth
az account show
az keyvault secret show --vault-name ${KV_NAME} --name azure-openai-api-key --query value -o tsv
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
mnemosyne embed --limit 5
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
mnemosyne embed --force
```

### `Dimension mismatch` errors in embed log

QMD's reindex cron (`qmd update`) ran during a long embed run and recreated `vectors_vec` with different dimensions. This is handled automatically by the retry logic in embed.py (ADR-M08), but if it's happening frequently:

```bash
# Check QMD cron schedule — if it runs during long embed windows, offset it
crontab -l | grep qmd

# Schedule embed cron to run just after QMD reindex, not during it
```

### Nightly entity extraction not running

```bash
# Check cron is registered
crontab -l

# Check log for last run
tail -20 /data/mnemosyne/logs/entity-relation-seed.log

# Run manually to debug
/data/tc-agent-zone/cron-scripts/entity-relation-seed.sh
```

### Briefing output is empty or incoherent

```bash
# Check memory logs exist for the agent
ls /data/workspaces/<agent>/memory/ | tail -5

# Check entity graph has content
mnemosyne entity list

# Run briefing with debug output
MNEMOSYNE_LOG_QUERIES=1 mnemosyne brief <agent> --budget 5000
```

---

## Upgrading

### Upgrading Mnemosyne

```bash
cd /data/tools/qmd-azure-embed
git pull origin main
.venv/bin/pip install -e .

# Run tests to verify
.venv/bin/pytest tests/ -m "not e2e" -q
```

No schema migrations are required between minor versions. The entities.db schema is versioned and validated at startup.

### Upgrading QMD

```bash
# Check QMD changelog for schema changes first
# Then run compatibility tests
cd /data/tools/qmd-azure-embed
.venv/bin/pytest tests/ -k "schema" -v

# If tests pass, update the version references
# qmd-tested-version in pyproject.toml
# QMD_TESTED_VERSION in mnemosyne/embed/schema.py
```

See [QMD_COMPAT.md](QMD_COMPAT.md) for the full upgrade procedure.

---

## Data Residency

Vault content is sent to Azure OpenAI (Australia East) for:
- **Embedding:** All vault documents sent to `text-embedding-3-large` for indexing
- **Briefing synthesis:** Memory logs + retrieved chunks sent to `gpt-4o-mini`
- **Entity extraction:** Entity stub content sent to `gpt-4o-mini` for NER
- **Relationship classification:** Relationship text sent to `gpt-4o-mini`

No vault content is stored externally beyond the duration of the API request. All vectors, entity data, and briefings live in SQLite on your own infrastructure.

See [SECURITY.md](SECURITY.md) for the full data handling and secret management policy.

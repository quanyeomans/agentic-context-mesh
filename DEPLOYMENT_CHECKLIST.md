# Deployment Checklist

Use this checklist when deploying Mnemosyne to a new OpenClaw server environment, or after major infrastructure changes. Each item should be verified, not assumed.

See [OPERATIONS.md](OPERATIONS.md) for the full procedure with exact commands.

---

## 1. Infrastructure Prerequisites

- [ ] VM has Python 3.10+ installed (`python3 --version`)
- [ ] QMD is installed and on PATH (`qmd --version` → 1.1.x or later)
- [ ] QMD has indexed the vault at least once (`ls -lh ~/.cache/qmd/index.sqlite` → non-zero file)
- [ ] sqlite-vec is available (either bundled with QMD or at `$SQLITE_VEC_PATH`)
- [ ] Azure OpenAI resource exists with `text-embedding-3-large` deployment
- [ ] Azure OpenAI resource exists with `gpt-4o-mini` deployment
- [ ] VM managed identity is assigned `Key Vault Secrets User` on the Key Vault
- [ ] `az account show` on the VM returns the correct subscription (no interactive login needed)

## 2. Key Vault Secrets

Verify all four secrets exist and have correct values:

- [ ] `azure-openai-endpoint` → `https://<resource>.cognitiveservices.azure.com/`
- [ ] `azure-openai-api-key` → the API key (starts with a hex string, not empty)
- [ ] `azure-openai-embed-deployment` → `text-embedding-3-large` (or your deployment name)
- [ ] `azure-openai-gpt4o-mini-deployment` → `gpt-4o-mini` (or your deployment name)

```bash
az keyvault secret list --vault-name <vault-name> --query "[].name" -o tsv
```

## 3. Installation

- [ ] Repository cloned to `/data/tools/qmd-azure-embed` (or your chosen path)
- [ ] Virtualenv created: `.venv/bin/python --version` → 3.10+
- [ ] Package installed: `.venv/bin/mnemosyne --help` prints usage without error
- [ ] Infra directories exist and are writable:
  - [ ] `/data/mnemosyne/briefing/`
  - [ ] `/data/mnemosyne/logs/` (or wherever embed log goes)
  - [ ] `/data/tc-agent-zone/logs/`

## 4. Environment Variables

Test that secrets can be fetched from Key Vault:

```bash
source /opt/<service-user>/env/service.env
export AZURE_OPENAI_ENDPOINT=$(az keyvault secret show \
  --vault-name <vault-name> --name azure-openai-endpoint --query value -o tsv)
export AZURE_OPENAI_API_KEY=$(az keyvault secret show \
  --vault-name <vault-name> --name azure-openai-api-key --query value -o tsv)
echo "Endpoint: ${AZURE_OPENAI_ENDPOINT:0:40}..."
echo "Key length: ${#AZURE_OPENAI_API_KEY}"
```

- [ ] Endpoint URL printed (not empty, starts with `https://`)
- [ ] Key length > 30 characters

## 5. Schema Validation

Mnemosyne validates the QMD schema on startup. Run a quick embed dry-run:

```bash
.venv/bin/mnemosyne embed --limit 1
```

- [ ] No `SchemaVersionError` — confirms `content.doc` column present
- [ ] No `ExtensionLoadError` — confirms sqlite-vec loads correctly
- [ ] Chunk embedded and written (log line: `embedded=1 failed=0`)

## 6. First Embed Run

- [ ] Incremental embed completes: `mnemosyne embed` → `Done — embedded=N failed=0`
- [ ] No dimension mismatch errors in log
- [ ] Chunk count is non-trivial (≥ 1000 for a real vault)
- [ ] `vectors_vec` is 1536-dim: verify in sqlite3 or check embed log for `dim=1536`

## 7. Search Smoke Test

```bash
.venv/bin/mnemosyne search "test query" --agent builder
```

- [ ] Returns results (not empty)
- [ ] No errors in output

## 8. Entity Graph

```bash
.venv/bin/mnemosyne entity list | head -5
```

- [ ] Returns entity records (not empty, assuming entity stubs exist in vault)
- [ ] If empty: run `mnemosyne entity extract --changed` to populate from vault-entities collection

## 9. Cron Registration

- [ ] Hourly embed cron added to `crontab -l`:
  ```
  15 * * * * source ... && export AZURE_... && cd /data/tools/qmd-azure-embed && .venv/bin/mnemosyne embed
  ```
- [ ] Nightly entity seed cron added:
  ```
  0 17 * * * source ... && export AZURE_... && cd /data/tools/qmd-azure-embed && .venv/bin/mnemosyne entity extract --changed && .venv/bin/python scripts/seed-entity-relations.py
  ```
- [ ] `crontab -l` shows both entries with no syntax errors

## 10. Post-Deploy Verification (next day)

- [ ] Embed log shows successful run since install: `grep "Done —" /data/tc-agent-zone/logs/azure-embed.log | tail -3`
- [ ] Chunk count is increasing or stable (not zero)
- [ ] No `failed=N` with N > 0 in embed log
- [ ] Entity count stable: `mnemosyne entity list | wc -l`

---

## Common First-Deploy Failures

| Symptom | Likely cause | Fix |
|---|---|---|
| `SchemaVersionError: body` | Old code version | `git pull && pip install -e .` |
| `ExtensionLoadError` | sqlite-vec not found | Set `SQLITE_VEC_PATH`; verify QMD has vec bundled |
| `AuthenticationError` on embed | Wrong API key or endpoint | Re-fetch from Key Vault; verify deployment name |
| `Expected 768 dimensions` | QMD reindex cron ran between embed start and batch write | ADR-M08 retry handles this automatically; no action needed |
| `0 chunks embedded` on first run | QMD hasn't indexed vault | Run `qmd index --collection vault /path/to/vault` first |
| Empty search results | vectors_vec empty | Check embed completed; verify `--collection` scope in search config |

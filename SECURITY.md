# Security Policy

## Supported Versions

| Version | Supported |
|---|---|
| 0.6.x (current) | ✅ |
| 0.5.x | ✅ security fixes only |
| < 0.5.0 | ✗ |

## Reporting a Vulnerability

Open a **private** security advisory on GitHub: Settings → Security → Advisories → New draft advisory.

Do not open a public issue for security vulnerabilities. Expect an acknowledgement within 48 hours and a fix or mitigation plan within 14 days.

---

## Secret Management

Mnemosyne requires three secrets to operate:

| Secret | Environment variable | Sensitivity |
|---|---|---|
| Azure OpenAI API key | `AZURE_OPENAI_API_KEY` | HIGH — rotate on any suspected exposure |
| Azure OpenAI endpoint URL | `AZURE_OPENAI_ENDPOINT` | MEDIUM — not a credential but limits blast radius of key exposure |
| Embedding deployment name | `AZURE_OPENAI_EMBED_DEPLOYMENT` | LOW |
| GPT-4o-mini deployment name | `AZURE_OPENAI_GPT4O_MINI_DEPLOYMENT` | LOW |

**Recommended: Azure Key Vault** — fetch secrets at runtime via managed identity, never store in environment files on disk. See [OPERATIONS.md](OPERATIONS.md) for the exact `az keyvault secret show` invocations used in cron entries.

**Do not:**
- Store API keys in `.env` files committed to git
- Log secrets (Mnemosyne does not log secret values; verify custom scripts do the same)
- Pass secrets as CLI arguments (they appear in `ps` output)

If secrets are stored in environment files (e.g., `/opt/openclaw/env/openclaw.env`), restrict permissions: `chmod 600 /opt/openclaw/env/openclaw.env` owned by the service account only.

---

## API Key Rotation

Rotate `AZURE_OPENAI_API_KEY` when:
- Any team member with access leaves
- Suspected exposure (logs, debug output, screenshots)
- Quarterly as standard hygiene

Rotation steps:
1. Generate new key in Azure Portal → Azure OpenAI resource → Keys and Endpoint
2. Update the Key Vault secret: `az keyvault secret set --vault-name <vault> --name azure-openai-api-key --value "<new-key>"`
3. Verify the next scheduled embed cron picks up the new key (check log output within 1 hour)
4. Disable the old key in Azure Portal

---

## Data Residency

Vault content is sent to **Azure OpenAI (Australia East)** for:
- Embedding (all indexed chunks) — `text-embedding-3-large`
- Synthesis (briefing, classification, benchmark judging) — `gpt-4o-mini`

**No vault content is stored by Azure** beyond the API call. Azure OpenAI does not use customer data for model training by default (see Microsoft's Data Privacy terms).

All vectors, entity data, and search indexes live in SQLite on your own infrastructure:
- `~/.cache/qmd/index.sqlite` — QMD FTS + sqlite-vec vectors (1536-dim)
- `/data/mnemosyne/entities.db` — entity graph + relationships

---

## Access Control

The VM service account (`openclaw`) requires:
- Read access to the Obsidian vault directory
- Read/write to `/data/mnemosyne/` and `~/.cache/qmd/`
- `az` CLI authenticated via managed identity (no stored credentials)

The managed identity requires only:
- `Key Vault Secrets User` role on the Key Vault
- No other Azure RBAC permissions required for normal operation

SSH access to the VM should be restricted to:
- Azure Bastion or Cloudflare Access tunnel (not direct public SSH)
- Temporary NSG rules for maintenance, closed immediately after use

---

## Dependency Security

The dev toolchain runs:
- `pip-audit` — checks installed packages against OSV database (zero known CVEs with fixes policy)
- `bandit` — static analysis for HIGH/MEDIUM findings (blocks merge)
- `detect-secrets` — scans for committed secrets (run pre-commit)

To run locally:
```bash
.venv/bin/pip-audit
.venv/bin/bandit -r mnemosyne/ -c pyproject.toml
.venv/bin/detect-secrets scan --baseline .secrets.baseline
```

Dependabot is configured (`.github/dependabot.yml`) to open PRs for dependency updates weekly.

---

## Audit Logging

Mnemosyne does not produce a dedicated audit log. Operational visibility:
- Embed logs: `/data/tc-agent-zone/logs/azure-embed.log` — timestamps, chunk counts, errors
- Query logs: `~/.cache/qmd/queries.jsonl` (when `MNEMOSYNE_LOG_QUERIES=1`)
- Benchmark results: `benchmark-results/*.json` — archived per-run NDCG scores
- Azure OpenAI usage: visible in Azure Portal → Azure OpenAI → Monitoring → Requests

To monitor for unexpected API usage, set up Azure cost alerts on the OpenAI resource.

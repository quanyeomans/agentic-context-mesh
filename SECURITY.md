# Security Policy

## Supported Versions

| Version | Supported |
|---|---|
| 2026.x.x (current CalVer) | Fully supported |
| 2026.x.xaN (alpha pre-releases) | Best-effort; upgrade to stable |
| Older than current CalVer quarter | Not supported |

## Reporting a Vulnerability

Open a **private** security advisory on GitHub: Settings → Security → Advisories → New draft advisory.

Do not open a public issue for security vulnerabilities. Expect an acknowledgement within 48 hours and a fix or mitigation plan within 14 days.

---

## Secret Management

Kairix requires these secrets to operate:

| Secret | Environment variable | Sensitivity |
|---|---|---|
| Azure OpenAI API key | `AZURE_OPENAI_API_KEY` | HIGH — rotate on any suspected exposure |
| Azure OpenAI endpoint URL | `AZURE_OPENAI_ENDPOINT` | MEDIUM |
| Embedding deployment name | `AZURE_OPENAI_EMBED_DEPLOYMENT` | LOW |
| GPT-4o-mini deployment name | `AZURE_OPENAI_GPT4O_MINI_DEPLOYMENT` | LOW |
| Neo4j password | `KAIRIX_NEO4J_PASSWORD` | HIGH (if Neo4j installed) |

### Production: tmpfs via systemd oneshot unit (recommended)

For production VM deployments, secrets should never be stored in plaintext on disk. The recommended pattern uses a systemd oneshot unit (`kairix-fetch-secrets.service`) that:

1. Runs at boot, after `network-online.target`
2. Authenticates to Azure Key Vault using the VM's **system-assigned managed identity** (no stored credentials)
3. Writes secrets to `/run/secrets/kairix.env` — a tmpfs path that is never written to disk and cleared on reboot
4. Sets permissions `640 root:<service-group>` on the secrets file

The kairix wrapper script (`/opt/kairix/bin/kairix-wrapper.sh`) sources `/run/secrets/kairix.env` before executing kairix, so all processes launched via the wrapper inherit secrets without them appearing in environment files or cron entries.

**Required Azure RBAC:** the VM managed identity must have `Key Vault Secrets User` role on the Key Vault. No other Azure permissions are required.

**Cron entries** must source credentials from the secrets file, not fetch them inline:

```bash
# Correct — secrets already in tmpfs from systemd unit
source "${KAIRIX_SECRETS_FILE:-/run/secrets/kairix.env}"
kairix embed

# Wrong — secrets fetched inline, appear in cron log, require az CLI auth per-run
export AZURE_OPENAI_API_KEY=$(az keyvault secret show ...)
```

### Local development

```bash
cp env.example .env    # .env is gitignored
# Edit with your values, then:
source .env && kairix search "test query"
```

**Do not:**
- Store API keys in `.env` files committed to git
- Pass secrets as CLI arguments (they appear in `ps` output)
- Log secret values (kairix does not log them; verify custom scripts do the same)
- Store secrets in plaintext files on production VMs — use the tmpfs pattern above

---

## Multi-Cloud / Self-Hosted Secret Backends

The tmpfs pattern above uses Azure Key Vault with managed identity. For other deployment environments:

| Environment | Recommended approach |
|---|---|
| **Azure VM** | System-assigned managed identity + Key Vault Secrets User RBAC |
| **AWS EC2** | IAM instance profile + AWS Secrets Manager or Parameter Store |
| **GCP Compute** | Service account + Secret Manager |
| **Kubernetes** | External Secrets Operator → K8s secrets mounted as files |
| **Self-hosted (no cloud KV)** | Vault by HashiCorp agent sidecar; or manual secrets file at `600 root:root` |

In all cases: write secrets to a tmpfs path at boot, not to a persistent disk file. The `KAIRIX_SECRETS_FILE` environment variable can override the default `/run/secrets/kairix.env` path.

---

## Neo4j Licensing Note

Neo4j Community Edition is licensed under **GPL v3**. Kairix communicates with Neo4j exclusively via the Bolt protocol using the Apache 2.0 Python driver (`neo4j>=5.0,<6.0`). No Neo4j GPL3 code is bundled with kairix; there is no copyleft propagation.

Neo4j is an **optional** dependency (`pip install "kairix[neo4j]"`). If Neo4j is not installed, kairix operates with degraded entity boost and multi-hop query planning — all other features work normally.

---

## API Key Rotation

Rotate `AZURE_OPENAI_API_KEY` when:
- Any team member with access leaves
- Suspected exposure (logs, debug output, screenshots)
- Quarterly as standard hygiene

Rotation steps:
1. Generate new key in Azure Portal → Azure OpenAI resource → Keys and Endpoint
2. Update the Key Vault secret: `az keyvault secret set --vault-name <vault> --name azure-openai-api-key --value "<new-key>"`
3. Restart `kairix-fetch-secrets.service` to refresh `/run/secrets/kairix.env`: `sudo systemctl restart kairix-fetch-secrets.service`
4. Verify the next scheduled embed cron picks up the new key (check log output within 1 hour)
5. Disable the old key in Azure Portal

---

## Data Residency

Vault content is sent to **Azure OpenAI** for:
- Embedding (all indexed chunks) — `text-embedding-3-large`
- Synthesis (briefing, classification, benchmark judging) — `gpt-4o-mini`

No vault content is stored by Azure beyond the API call. Azure OpenAI does not use customer data for model training by default (see Microsoft's Data Privacy terms).

All vectors, entity data, and search indexes live in SQLite and Neo4j on your own infrastructure:
- `~/.cache/kairix/index.sqlite` — Kairix FTS index
- `~/.cache/kairix/vectors.usearch` — usearch HNSW vectors (1536-dim)
- Neo4j — entity graph (if installed)

---

## Access Control

The service account running kairix requires:
- Read access to the Obsidian vault directory
- Read/write to `${KAIRIX_DATA_DIR}/` and `~/.cache/kairix/`
- Read access to `/run/secrets/kairix.env` (group membership)
- `az` CLI authenticated via managed identity (no stored credentials)

SSH access to the VM should be restricted to:
- Azure Bastion or Cloudflare Access tunnel (not direct public SSH)
- Temporary firewall rules for maintenance, closed immediately after use

---

## Dependency Security

The dev toolchain runs:
- `pip-audit` — checks installed packages against OSV database (zero known CVEs with fixes policy)
- `bandit` — static analysis for HIGH/MEDIUM findings (blocks merge)
- `detect-secrets` — scans for committed secrets (run pre-commit)

To run locally:
```bash
.venv/bin/pip-audit
.venv/bin/bandit -r kairix/ -c pyproject.toml
.venv/bin/detect-secrets scan --baseline .secrets.baseline
```

Dependabot is configured (`.github/dependabot.yml`) to open PRs for dependency updates weekly.

---

## Audit Logging

Kairix does not produce a dedicated audit log. Operational visibility:
- Embed logs: `${KAIRIX_DATA_DIR}/logs/embed.log` — timestamps, chunk counts, errors
- Query logs: `~/.cache/kairix/queries.jsonl` (when `KAIRIX_LOG_QUERIES=1`)
- Benchmark results: `benchmark-results/*.json` — archived per-run NDCG scores
- Azure OpenAI usage: visible in Azure Portal → Azure OpenAI → Monitoring → Requests

To monitor for unexpected API usage, set up Azure cost alerts on the OpenAI resource.

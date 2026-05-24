# Agent-driven setup

This document is for an LLM agent that is standing up kairix on a user's behalf. Read it as a recipe: gather the inputs, write the files, run the validation, escalate the listed decisions. Optimised for unambiguous machine instructions — humans setting up kairix should follow [quick-start.md](quick-start.md) instead.

If you are reading this in-session and the user has asked you to "install kairix" or "set this up for me", start at step 1. If a check fails at any step, surface the failure's `fix:` / `next:` affordance verbatim to the user — don't paraphrase.

---

## 1. What to ask the user

Collect these inputs before you write any files. If the user has not provided one, ask once with the exact field name. Do not guess.

### Required

| Field | What | Where it goes |
|-------|------|---------------|
| `KAIRIX_LLM_API_KEY` | Anthropic / OpenAI / Azure key | secrets file (Path B: `~/.config/kairix/secrets/kairix.env`; Path A: `/run/secrets/kairix.env`) |
| `KAIRIX_LLM_ENDPOINT` | Provider endpoint URL (Azure / OpenAI-compatible / LiteLLM) | secrets file |
| `provider` | Plugin name (`azure_foundry` / `openai` / `azure_legacy` / `bedrock` / `ollama` / `litellm_proxy` / `anthropic`) | `kairix.config.yaml` top-level |
| document root path | Where the user's markdown / text corpus lives | `paths.document_root` in `kairix.config.yaml` |

### Optional (only ask if you intend to enable the feature)

| Field | What | When you need it |
|-------|------|------------------|
| `CONNECTOR_M365_TENANT_ID` | AAD tenant GUID | enabling `connector_sharepoint` |
| `CONNECTOR_M365_CLIENT_ID` | App registration client id | enabling `connector_sharepoint` |
| `CONNECTOR_M365_CLIENT_SECRET` | App registration client secret | enabling `connector_sharepoint` |
| `KAIRIX_NEO4J_PASSWORD` | Neo4j bolt password | enabling Neo4j (entity boost + multi-hop) |
| SharePoint drive id | Graph drive identifier per site | enabling `connector_sharepoint` |
| `CONNECTOR_SLACK_BOT_TOKEN` | Workspace bot token (`xoxb-...`) | enabling `connector_slack` |
| `CONNECTOR_SLACK_APP_TOKEN` | App-level token (`xapp-...`) for Socket Mode live events | enabling `connector_slack` with live events |
| Slack workspace id | `T01ABC...` from Slack admin → about | enabling `connector_slack` |
| `CONNECTOR_GITHUB_PERSONAL_ACCESS_TOKEN` OR App triple | GitHub auth — PAT is simplest; App scales to many repos | enabling `connector_github` |
| `CONNECTOR_GITHUB_WEBHOOK_SECRET` | Random 32-byte hex for webhook HMAC validation | enabling `connector_github` live events |
| `CONNECTOR_NOTION_INTEGRATION_TOKEN` | Notion internal-integration token (`secret_...`) | enabling `connector_notion` |

If the user says "use the local Neo4j" — provision Neo4j via the [`docs/operations/runbooks/how-to-install-neo4j.md`](../operations/runbooks/how-to-install-neo4j.md) recipe and use the password it sets.

For the SharePoint drive id, ask the user to run `kairix sharepoint list-sites` after the basic install and pick the drive id by name. The connector flag stays off until the operator pins a drive id — this keeps a missing id from silently selecting the wrong drive.

For Notion, after the integration token is set, the user has to share each page or database with the integration via the Notion UI before it can be indexed. The integration name appears in Notion's share dialog.

### Connector privileges — single-page reference

When the user is provisioning a connector, walk them through these grants in the source platform's admin UI. Each entry is the minimum permission set the connector needs to function — kairix is read-only by design and asks for no write capability.

**SharePoint** — Azure Portal → App registrations → your app → API Permissions → Add → Microsoft Graph → Application Permissions:
- `Sites.Read.All` (enumerate sites + drives)
- `Files.Read.All` (read file content)

Both require admin consent ("Grant admin consent for your-tenant") after they're added. Shares one app registration with the M365 email-headers + calendar connectors.

**Slack** — Slack admin → Apps → your app → OAuth & Permissions → Bot Token Scopes:
- `channels:history`, `channels:read` (public channels)
- `groups:history` (private channels the bot is in)
- `im:history`, `mpim:history` (DMs the bot is in)
- `users:read` (resolve user ids to display names)
- `files:read` (file metadata for sensitivity routing)
- `chat:write` (stable message permalinks — read path only)

For Socket Mode live events, also create an app-level token with `connections:write`. Without Socket Mode the connector polls.

After granting, the user must invite the bot to each channel they want indexed (`/invite @bot-name`). Slack doesn't let the bot read history from a channel it isn't a member of.

**GitHub** — Settings → Developer settings → GitHub Apps → your app → Permissions and events → Repository permissions:
- Contents: Read (repo trees + blob content)
- Issues: Read (issues + bodies)
- Pull requests: Read (PR metadata + diffs)
- Metadata: Read (always granted automatically)

Webhook events (Subscribe to events): `push`, `issues`, `pull_request`, `installation_repositories`.

The PAT path uses the same permissions with the equivalent scope names (`repo`, `read:org`); the App path needs explicit installation on the repos to be indexed (your app → Install → "Only select repositories").

**Notion** — Notion → Settings & members → Integrations → New integration → Capabilities:
- Read content (only — leave Update and Insert OFF, the connector is read-only)

Then for each page or database to index: open it → Share → search for the integration by name → Invite. Sharing a parent page grants access to its children.

---

## 2. The declarative path — write a kairix.config.yaml directly

You do NOT need an interactive wizard. Write `kairix.config.yaml` directly using the canonical example as your template.

### Source-of-truth template

The shape that parses cleanly through `kairix config validate` lives at the repo root: [`kairix.config.example.yaml`](../../kairix.config.example.yaml). Read it once before you write the user's config so your output matches the exact field names and nesting the parser expects.

### Minimal-viable template

For a fresh user who wants the basic setup (one provider, one corpus, no topology v2 connectors yet):

```yaml
# kairix.config.yaml — minimal viable
_schema_version: 1
provider: azure_foundry   # or: openai / azure_legacy / bedrock / ollama / litellm_proxy / anthropic

paths:
  document_root: /Users/<user>/Documents/my-knowledge-store
  db_path: ~/.cache/kairix/index.sqlite
  log_dir: ~/.cache/kairix/logs
  workspace_root: ~/.kairix/workspaces

retrieval:
  fusion_strategy: bm25_primary
  boosts:
    entity:    { enabled: true,  factor: 0.20, cap: 2.0 }
    procedural:{ enabled: true,  factor: 1.4 }
    temporal:
      date_path_boost:  { enabled: false }
      chunk_date_boost: { enabled: false }
```

Write that file to `~/.kairix/kairix.config.yaml` (or whatever path you set as `KAIRIX_CONFIG_PATH`).

### Topology v2 add-on (only if the user asked for connectors)

When the user has asked you to wire up a connector (SharePoint, Slack, GitHub, or Notion) OR multi-folder Obsidian routing, append the `topology_v2:` block from `kairix.config.example.yaml`. Each connector flag flips independently — turn on only the ones the user has asked for and has secrets for.

```yaml
features:
  topology_v2_config: true                  # required when any connector_* flag is on
  topology_v2_obsidian: true                # only if user wants per-folder Obsidian containers
  connector_sharepoint: true                # only if user provided the M365 triple + drive id
  connector_slack: true                     # only if user provided slack bot + (optional) app token
  connector_github: true                    # only if user provided PAT or app triple
  connector_notion: true                    # only if user provided the integration token
```

Leave `topology_v2_runtime` OFF on a brand-new install. Flip it only when the user has explicitly asked AND `topology_v2_config` has been on for at least one full sync cycle — `topology_v2_runtime` changes chunk write routing, and the user should validate the parsed config first (`kairix config validate` reports zero failures). The cutover protocol in `docs/architecture/feature-flag-architecture.md` §"Cutover protocol" walks through the snapshot → flip → soak → diff sequence.

---

## 3. Secrets shape

kairix resolves secrets through this chain (every step is checked in order):

1. **Process env var** — `os.environ.get(env_var_name)`. Fastest.
2. **Per-file secret** — `<secrets_dir>/<logical-name>` (one secret per file, one-line value).
3. **Bundle file** — `<secrets_dir>/kairix.env` (KEY=VALUE lines).
4. **Azure Key Vault** — when `KAIRIX_KV_NAME` is set, fall back to `az keyvault secret show`.

`<secrets_dir>` is `/run/secrets/` (Docker) or `~/.config/kairix/secrets/` (pip install).

### Logical-name → env-var-name map (the names you write in the secrets file)

| Logical name (used by connector code) | Env var (what you write) |
|--------------------------------------|--------------------------|
| `kairix-llm-api-key` | `KAIRIX_LLM_API_KEY` |
| `kairix-llm-endpoint` | `KAIRIX_LLM_ENDPOINT` |
| `kairix-llm-model` | `KAIRIX_LLM_MODEL` |
| `kairix-embed-api-key` | `KAIRIX_EMBED_API_KEY` |
| `kairix-embed-endpoint` | `KAIRIX_EMBED_ENDPOINT` |
| `kairix-neo4j-password` | `KAIRIX_NEO4J_PASSWORD` |
| `connector-m365-tenant-id` | `CONNECTOR_M365_TENANT_ID` |
| `connector-m365-client-id` | `CONNECTOR_M365_CLIENT_ID` |
| `connector-m365-client-secret` | `CONNECTOR_M365_CLIENT_SECRET` |
| `connector-slack-bot-token` | `CONNECTOR_SLACK_BOT_TOKEN` |
| `connector-slack-app-token` | `CONNECTOR_SLACK_APP_TOKEN` |
| `connector-github-personal-access-token` | `CONNECTOR_GITHUB_PERSONAL_ACCESS_TOKEN` |
| `connector-github-app-id` | `CONNECTOR_GITHUB_APP_ID` |
| `connector-github-installation-id` | `CONNECTOR_GITHUB_INSTALLATION_ID` |
| `connector-github-app-private-key` | `CONNECTOR_GITHUB_APP_PRIVATE_KEY` |
| `connector-github-webhook-secret` | `CONNECTOR_GITHUB_WEBHOOK_SECRET` |
| `connector-notion-integration-token` | `CONNECTOR_NOTION_INTEGRATION_TOKEN` |

### Docker secret file (Path A)

```
# /run/secrets/kairix.env — write via docker secret create / volume mount
KAIRIX_LLM_API_KEY=<value>
KAIRIX_LLM_ENDPOINT=<value>
# Optional — SharePoint connector:
CONNECTOR_M365_TENANT_ID=<value>
CONNECTOR_M365_CLIENT_ID=<value>
CONNECTOR_M365_CLIENT_SECRET=<value>
# Optional — Neo4j:
KAIRIX_NEO4J_PASSWORD=<value>
```

### Pip secret file (Path B)

```
# ~/.config/kairix/secrets/kairix.env — chmod 600
KAIRIX_LLM_API_KEY=<value>
KAIRIX_LLM_ENDPOINT=<value>
# (same optional rows as above)
```

Set permissions: `chmod 600 ~/.config/kairix/secrets/kairix.env`.

---

## 4. Validation pipeline

Run these commands in order. Stop at the first failure and surface the `remediation` to the user.

### 4.1 `kairix onboard check --json`

```bash
kairix onboard check --json
```

This emits a structured envelope: `{passed, total, fully_passed, failures: [{check, detail, remediation}]}`. Exit code is 0 only when `fully_passed: true`.

Three new checks landed in v2026.5.24a1:

- `topology_v2_config_valid` — when `topology_v2_config` is on, parses + cross-references the `topology_v2:` block
- `topology_v2_cc_pairs_registered` — when `topology_v2_config` is on, confirms each declared cc_pair has a row in `topology_cc_pairs` (run `kairix worker apply-config` to materialise them)
- `sharepoint_credentials_loaded` — when `connector_sharepoint` is on, confirms the three M365 secrets resolve

Every check that is gated by a feature flag reports `ok=true` with a `"skipped — <flag> flag is OFF"` detail when the flag is off — so a fresh install sees the check exists but doesn't block.

### 4.2 `kairix worker preflight --json`

```bash
kairix worker preflight --json
```

Runs the integrity audit — confirms the SQLite index is on the expected schema version, the embed pipeline can resolve its provider plugin, and the document root contains at least one indexable file.

### 4.3 `kairix features status --topology-v2`

```bash
kairix features status --topology-v2
```

Reports which flags are on, where the value came from (env / config / default), and — when topology_v2 is on — which cc_pairs are registered in `topology_cc_pairs` plus the per-actor scope resolution.

### 4.4 `kairix benchmark run --suite reflib`

```bash
kairix benchmark run reflib
```

Retrieval-quality smoke against the bundled reference library. The release gates pass when overall ≥ 0.78, entity ≥ 0.80, temporal ≥ 0.55. If any gate fails, surface the metric name + value to the user — likely the embed pipeline isn't fully primed yet (run `kairix embed` once more, then re-benchmark).

---

## 5. Common failure modes + fixes

Every failure path you might hit, with the exact next command. Mirrors the F21 affordance shape (`fix: ... next: ... run: ...`) so the user can act without re-reading the docs.

| Failure | What it means | Fix |
|---------|---------------|-----|
| `secrets_loaded` fails: "LLM credentials missing" | `KAIRIX_LLM_API_KEY` or `KAIRIX_LLM_ENDPOINT` not in env / secrets file | fix: write both keys to the secrets file at the path reported in `detail`. next: re-run `kairix onboard check secrets_loaded`. |
| `document_root_configured` fails: "directory does not exist" | The path you wrote into `paths.document_root` (or `KAIRIX_DOCUMENT_ROOT`) does not exist on disk | fix: `mkdir -p <path>` OR correct the path in `kairix.config.yaml`. next: re-run `kairix onboard check`. |
| `vector_search_working` fails: "vec_failed=True" | The provider plugin can't reach its endpoint (auth, network, or quota) | fix: `kairix probe-config` reports the specific reason; usually a stale API key. next: rotate the key, re-write the secrets file, re-run. |
| `topology_v2_config_valid` fails: "<N> cross-reference failure(s)" | A `cc_pair` references a connector / credential that wasn't declared; or a `collection.source` / `scope_profile.entry` references a missing cc_pair / collection | fix: open `kairix.config.yaml` and add the missing entries OR remove the dangling reference. next: run `kairix config validate`. |
| `topology_v2_cc_pairs_registered` fails: "<N> declared cc_pair(s) not registered" | You wrote new cc_pairs to the YAML but didn't apply them to the DB | fix: `kairix worker apply-config`. next: re-run `kairix onboard check`. |
| `sharepoint_credentials_loaded` fails: "<N> SharePoint secret(s) unresolved" | The three M365 secrets are not in env / secrets file / Key Vault | fix: write `CONNECTOR_M365_TENANT_ID` / `CONNECTOR_M365_CLIENT_ID` / `CONNECTOR_M365_CLIENT_SECRET` to the secrets file. next: re-run `kairix onboard check sharepoint_credentials_loaded`. |
| `neo4j_reachable` fails: "client unavailable" | Neo4j is not installed OR `KAIRIX_NEO4J_URI` is wrong | fix: install via `bash scripts/install-neo4j.sh` OR run a containerised neo4j; set `KAIRIX_NEO4J_URI=bolt://localhost:7687`. next: re-run `kairix onboard check`. Neo4j is OPTIONAL — entity boost degrades gracefully. |
| `mcp_service` fails: "not configured" | No MCP consumer harness has kairix registered | fix: add kairix to the user's `claude_desktop_config.json` OR `~/.openclaw/openclaw.json` (the failure detail names the exact paths). next: re-run `kairix onboard check`. |
| `kairix worker preflight` fails: "schema version mismatch" | The SQLite DB was created by an older kairix version | fix: `kairix migrate up` (the migration is automatic on next embed; this verb forces it now). next: re-run `kairix worker preflight`. |
| `kairix benchmark run reflib` fails: gate below threshold | Embed pipeline incomplete or provider mis-tuned | fix: `kairix embed --limit 100` to confirm embed works end-to-end; if green, run `kairix embed` for a full pass; then re-benchmark. next: if the gate still fails, escalate to the user — likely a model-quality issue requiring a provider swap. |

---

## 6. What to escalate to the user

Do NOT auto-decide these. Stop, surface the question, and wait for the user's answer.

- **Any secret value.** Tenant ids, client ids, secrets, API keys, Neo4j passwords. Even if the user has previously typed them — never persist a guess.
- **Permission grants.** AAD app registration consent (Sites.Read.All, Files.Read.All) is a high-trust action; the user — not you — should click through the consent screen.
- **Any spend.** `kairix embed` against a large corpus costs real money (~$0.50-1.00 per 1000 documents on text-embedding-3-large). Confirm before kicking off a full backfill.
- **Cutover flag flips on a soaked deployment.** If kairix has been running with `topology_v2_config: false` and the user asks you to flip it to true on a corpus the team is already using, capture a pre-flip baseline (`scripts/cutover/capture_baseline.py`) and tell the user to read `docs/architecture/feature-flag-architecture.md` §"Cutover protocol" before the flip.
- **Destructive verbs.** `kairix entity purge`, `kairix worker reset-cursor`, `kairix migrate down`, anything that drops data. Always confirm.
- **Production deployments.** Setup on a shared VM / production host: confirm with the user that the host is the intended target before running.

---

## 7. Cross-references

- [`quick-start.md`](quick-start.md) — the human-facing version with Docker + pip parallel steps
- [`kairix.config.example.yaml`](../../kairix.config.example.yaml) — the canonical operator-facing YAML shape, with inline comments per block
- [`docs/architecture/feature-flag-architecture.md`](../architecture/feature-flag-architecture.md) — the cutover protocol for any flag flip on a soaked corpus
- [`docs/architecture/connector-scope-topology/ADR.md`](../architecture/connector-scope-topology/ADR.md) — full spec for topology v2 (connectors / collections / scope profiles / skills)
- [`docs/operations/MCP-DEPLOYMENT.md`](../operations/MCP-DEPLOYMENT.md) — cold-start behaviour the MCP server reports during warm-up
- [`docs/user-guide/agent-usage-guide.md`](../user-guide/agent-usage-guide.md) — what `kairix onboard guide` installs into the operator's knowledge store

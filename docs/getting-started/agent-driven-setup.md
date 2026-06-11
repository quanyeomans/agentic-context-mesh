# Agent-driven setup

This document is for an LLM agent that is standing up kairix on a user's behalf. Read it as a recipe: gather the inputs, write the files, run the validation, escalate the listed decisions. Optimised for unambiguous machine instructions — humans setting up kairix should follow [quick-start.md](quick-start.md) instead.

If you are reading this in-session and the user has asked you to "install kairix" or "set this up for me", start at step 1. If a check fails at any step, surface the failure's `fix:` / `next:` affordance verbatim to the user — don't paraphrase.

---

## 1. What to ask the user

Collect these inputs before you write any files. If the user has not provided one, ask once with the exact field name. Do not guess.

### Required

| Field | What | Where it goes |
|-------|------|---------------|
| `KAIRIX_PROVIDER_LLM_API_KEY` | Anthropic / OpenAI / Azure key | secrets file (Path B: `~/.config/kairix/secrets/kairix.env`; Path A: `/run/secrets/kairix.env`) |
| `KAIRIX_PROVIDER_LLM_ENDPOINT` | Provider endpoint URL (Azure / OpenAI-compatible / LiteLLM) | secrets file |
| `provider` | Plugin name (`azure_foundry` / `openai` / `azure_legacy` / `bedrock` / `ollama` / `litellm_proxy` / `anthropic`) | `kairix.config.yaml` top-level |
| document root path | Where the user's markdown / text corpus lives | `paths.document_root` in `kairix.config.yaml` |

### Optional (only ask if you intend to enable the feature)

| Field | What | When you need it |
|-------|------|------------------|
| `CONNECTOR_M365_TENANT_ID` | AAD tenant GUID | enabling `connector_sharepoint` |
| `CONNECTOR_M365_CLIENT_ID` | App registration client id | enabling `connector_sharepoint` |
| `CONNECTOR_M365_CLIENT_SECRET` | App registration client secret | enabling `connector_sharepoint` |
| `KAIRIX_INFRA_NEO4J_PASSWORD` (or `kairix-infra-neo4j-password` in KV) | Neo4j bolt password | **required for production** — entity boost, multi-hop, alias resolution, briefing synthesis all need it |
| SharePoint drive id | Graph drive identifier per site | enabling `connector_sharepoint` |
| `CONNECTOR_SLACK_BOT_TOKEN` | Workspace bot token (`xoxb-...`) | enabling `connector_slack` |
| `CONNECTOR_SLACK_APP_TOKEN` | App-level token (`xapp-...`) for Socket Mode live events | enabling `connector_slack` with live events |
| Slack workspace id | `T01ABC...` from Slack admin → about | enabling `connector_slack` |
| `CONNECTOR_GITHUB_PERSONAL_ACCESS_TOKEN` OR App triple | GitHub auth — PAT is simplest; App scales to many repos | enabling `connector_github` |
| `CONNECTOR_GITHUB_WEBHOOK_SECRET` | Random 32-byte hex for webhook HMAC validation | enabling `connector_github` live events |
| `CONNECTOR_NOTION_TOKEN` | Notion internal-integration token (`secret_...`) | enabling `connector_notion` |

Neo4j is part of every production deployment — the bundled `docker-compose.yml` ships a Neo4j sidecar by default. Provision its password (`kairix-infra-neo4j-password` in KV; `KAIRIX_INFRA_NEO4J_PASSWORD` in the bundle env file) before standing kairix up. For non-Docker installs, run `bash scripts/install-neo4j.sh` from the repo and use the password it sets.

For the SharePoint drive id, look it up via the Microsoft Graph API (the same app registration's credentials work): `GET https://graph.microsoft.com/v1.0/sites?search=<site-name>` to find the site id, then `GET https://graph.microsoft.com/v1.0/sites/<site-id>/drives` to list its drives — the `id` field of the drive you want is the value for the config. A guided in-CLI discovery flow ("pick your site from a list") is planned — see [`docs/architecture/guided-configuration.md`](../architecture/guided-configuration.md). The connector flag stays off until the operator pins a drive id — this keeps a missing id from silently selecting the wrong drive.

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

kairix uses **logical secret names** (lowercase-hyphenated). The name is the same string whatever the resolution path — `connector-slack-bot-token` is the key in Azure Key Vault, the filename for a per-file secret, and the lookup key the connector calls. Two paths are supported for production: **Azure Key Vault** (the dogfood / shared-infra pattern) and **bundled env file** (the simple Docker / pip-on-VM pattern). They can coexist; KV wins when both resolve.

### Resolution order (every step is checked in order)

1. **Process env var** — `os.environ.get(env_var_name)`. The env-var name is the upper-snake-case version of the logical name (e.g. `CONNECTOR_SLACK_BOT_TOKEN` for logical `connector-slack-bot-token`).
2. **Per-file secret** — `<secrets_dir>/<logical-name>` (one file per secret, the filename is the logical name verbatim, contents is the value on one line).
3. **Bundle file** — `<secrets_dir>/kairix.env` (KEY=VALUE lines, where KEY is the env-var name from the table below).
4. **Azure Key Vault** — when `KAIRIX_KV_NAME` is set, fall back to `az keyvault secret show --vault-name $KAIRIX_KV_NAME --name <logical-name>`. **The KV secret name is the logical name verbatim** — `connector-slack-bot-token`, not `CONNECTOR_SLACK_BOT_TOKEN`.

`<secrets_dir>` is `/run/secrets/` (Docker) or `~/.config/kairix/secrets/` (pip install).

### Path A — Azure Key Vault (production / dogfood)

Set one env var on the kairix runtime (container or systemd unit):

```
KAIRIX_KV_NAME=<your-keyvault-short-name>     # e.g. kairix-prod-kv (without https://...vault.azure.net)
```

Then create secrets in that vault. **Use the logical name verbatim as the KV secret name** — no uppercasing, no underscore-conversion, exactly what kairix passes to `az keyvault secret show --name`.

#### Runtime identity requirements

The runtime needs **`Get` permission on secrets** in your KV. Either:

- **Access policy mode (legacy):** Add the runtime principal (managed identity or service principal) with `Get` on Secret Permissions. KV → Access policies → Add Access Policy → Get only → select principal.
- **RBAC mode (preferred):** Grant the runtime principal the **Key Vault Secrets User** role on the vault scope. KV → Access control (IAM) → Add role assignment → Key Vault Secrets User → select principal.

The runtime container must also have the `az` CLI installed and be logged in (managed identity does this automatically; service-principal-on-VM uses `az login --identity` or `az login --service-principal`).

#### Complete list of KV secret names

Every secret kairix may resolve. **The name in this column is the literal value to pass to `az keyvault secret create --name`** (or to type into the portal's "Secret name" field).

| KV secret name (= logical name) | Required for | Notes |
|---|---|---|
| `kairix-provider-llm-api-key` | LLM calls (briefing, classification, fact extraction) | Always required |
| `kairix-provider-llm-endpoint` | Same | Required for Azure / OpenAI-compatible / LiteLLM providers |
| `kairix-provider-llm-model` | Same | Optional override; default model varies per provider |
| `kairix-provider-embed-api-key` | Embedding calls | Falls back to `kairix-provider-llm-api-key` if absent |
| `kairix-provider-embed-endpoint` | Same | Falls back to `kairix-provider-llm-endpoint` if absent |
| `kairix-provider-embed-model` | Same | Optional override |
| `kairix-infra-neo4j-password` | Neo4j connection (entity boost, multi-hop, alias resolution) | **Required for production** — see §"Neo4j is required" below |
| `connector-m365-tenant-id` | SharePoint / m365-email-headers / m365-calendar connectors | Shared across all three M365 connectors |
| `connector-m365-client-id` | Same | One app registration covers all three |
| `connector-m365-client-secret` | Same | Required when any M365 connector flag is on |
| `connector-slack-bot-token` | Slack connector | Required when `connector_slack` is on; `xoxb-...` |
| `connector-slack-app-token` | Slack Socket Mode | Optional; `xapp-...`; without it the connector polls instead of streaming |
| `connector-slack-client-id` | Slack OAuth flow | Optional; only for user-initiated install flows |
| `connector-slack-client-secret` | Same | Optional; same caveat |
| `connector-github-personal-access-token` | GitHub PAT path | Provide **either** this **or** the App triple below |
| `connector-github-app-id` | GitHub App path | Provide all three or use the PAT above |
| `connector-github-installation-id` | Same | |
| `connector-github-app-private-key` | Same | PEM contents on one line |
| `connector-github-webhook-secret` | GitHub webhook listener | Required if you want the live event listener; random 32-byte hex |
| `connector-notion-token` | Notion connector | Required when `connector_notion` is on; `secret_...` |

#### Verifying the KV path resolves

After the secrets are in KV and the runtime has Get permission:

```bash
# Inside the kairix container or on the VM where kairix runs:
az keyvault secret show --vault-name $KAIRIX_KV_NAME --name kairix-provider-llm-api-key --query value -o tsv
# Should print the value. If it errors, the runtime identity is missing Get permission.

kairix onboard check
# secrets_loaded must report ok=true with the resolution path it used.
```

### Path B — bundled env file (simple Docker / pip-on-laptop)

Skip this if you're on Path A — the KV path is enough on its own.

Write the bundle file as `KEY=VALUE` lines:

```
# /run/secrets/kairix.env (Docker) OR ~/.config/kairix/secrets/kairix.env (pip)
KAIRIX_PROVIDER_LLM_API_KEY=<value>
KAIRIX_PROVIDER_LLM_ENDPOINT=<value>
KAIRIX_INFRA_NEO4J_PASSWORD=<value>    # production-required (see §"Neo4j is required")
```

All connector secrets are in the env-var map (`CONNECTOR_M365_*`, `CONNECTOR_SLACK_*`, `CONNECTOR_GITHUB_*`, `CONNECTOR_NOTION_TOKEN`) — they resolve from the bundle file like the core secrets do.

If you'd rather drop a single secret as a file (e.g. for Docker secrets mounted at `/run/secrets/`), the filename is the **logical name verbatim** — no uppercasing:

```
echo -n 'xoxb-your-real-token' > /run/secrets/connector-slack-bot-token
echo -n 'secret_your-real-token' > /run/secrets/connector-notion-token
chmod 600 /run/secrets/connector-*
```

Set permissions on the bundle: `chmod 600 ~/.config/kairix/secrets/kairix.env`.

### Neo4j is required

Kairix loads without Neo4j, but every production deployment should run it — without Neo4j, entity boost, multi-hop search, alias resolution, and briefing synthesis all degrade significantly on entity-heavy corpora (which most operator knowledge stores are). The bundled `docker-compose.yml` ships a Neo4j sidecar by default for this reason.

Provision `kairix-infra-neo4j-password` in KV (or `KAIRIX_INFRA_NEO4J_PASSWORD` in the bundle file). The Neo4j connection URL is set via `KAIRIX_NEO4J_URI` (defaults to `bolt://neo4j:7687` for the bundled compose layout).

If you genuinely want a Neo4j-less deployment, expect: search still returns hits, but entity-named queries drop ~5–10 NDCG points, multi-hop queries return single-hop results only, and the briefing pipeline can't resolve entities across documents. Confirm with the user that this trade-off is intentional before skipping Neo4j.

---

## 4. Validation pipeline

Run these commands in order. Stop at the first failure and surface the `remediation` to the user.

### 4.1 `kairix onboard check --json`

```bash
kairix onboard check --json
```

This emits a structured envelope: `{passed, total, fully_passed, failures: [{check, detail, remediation}]}`. Exit code is 0 only when `fully_passed: true`.

Checks worth knowing about for connector setups:

- `topology_v2_config_valid` — parses + cross-references the `topology_v2:` block
- `topology_v2_cc_pairs_registered` — confirms each declared cc_pair has a row in `topology_cc_pairs` (declared cc_pairs register on worker startup — restart the worker after editing the YAML)
- `sharepoint_credentials_loaded` — when `connector_sharepoint` is on, confirms the three M365 secrets resolve

Checks gated by a feature flag (`sharepoint_credentials_loaded`, `maintenance_loop_ticking`) report `ok=true` with a `"skipped — <flag> flag is OFF"` detail when the flag is off — so a fresh install sees the check exists but doesn't block.

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
| `secrets_loaded` fails: "LLM credentials missing" | The LLM key + endpoint are not in env / secrets file | fix: write both keys (see §3) to the secrets file at the path reported in `detail`. next: run `kairix secrets verify`, then re-run `kairix onboard check`. |
| `document_root_configured` fails: "directory does not exist" | The path you wrote into `paths.document_root` (or `KAIRIX_DOCUMENT_ROOT`) does not exist on disk | fix: `mkdir -p <path>` OR correct the path in `kairix.config.yaml`. next: re-run `kairix onboard check`. |
| `vector_search_working` fails: "vec_failed=True" | The provider plugin can't reach its endpoint (auth, network, or quota) | fix: `kairix probe-config` reports the specific reason; usually a stale API key. next: rotate the key, re-write the secrets file, re-run. |
| `topology_v2_config_valid` fails: "<N> cross-reference failure(s)" | A `cc_pair` references a connector / credential that wasn't declared; or a `collection.source` / `scope_profile.entry` references a missing cc_pair / collection | fix: open `kairix.config.yaml` and add the missing entries OR remove the dangling reference. next: run `kairix config validate`. |
| `topology_v2_cc_pairs_registered` fails: "<N> declared cc_pair(s) not registered" | You wrote new cc_pairs to the YAML but the worker hasn't re-applied them | fix: restart the worker (`docker compose restart kairix` or restart your `kairix worker run` process) — declared cc_pairs register on startup. next: re-run `kairix onboard check`. |
| `sharepoint_credentials_loaded` fails: "<N> SharePoint secret(s) unresolved" | The three M365 secrets are not in env / secrets file / Key Vault | fix: write `CONNECTOR_M365_TENANT_ID` / `CONNECTOR_M365_CLIENT_ID` / `CONNECTOR_M365_CLIENT_SECRET` to the secrets file. next: re-run `kairix onboard check sharepoint_credentials_loaded`. |
| `neo4j_reachable` fails: "client unavailable" | Neo4j is not installed OR `KAIRIX_NEO4J_URI` is wrong | fix: install via `bash scripts/install-neo4j.sh` OR run the bundled compose (Neo4j is a sidecar in `docker-compose.yml`); set `KAIRIX_NEO4J_URI=bolt://localhost:7687` (pip) or `bolt://neo4j:7687` (compose). next: re-run `kairix onboard check`. Neo4j is required for production — entity boost, multi-hop, and briefing all rely on it. |
| `mcp_service` fails: "not configured" | No MCP consumer harness has kairix registered | fix: add kairix to the user's `claude_desktop_config.json` OR `~/.openclaw/openclaw.json` (the failure detail names the exact paths). next: re-run `kairix onboard check`. |
| `kairix worker preflight` fails: "schema version mismatch" | The SQLite DB was created by an older kairix version | fix: restart the worker — schema migrations apply automatically at boot (`kairix.core.db.schema`). next: re-run `kairix worker preflight`. |
| `kairix benchmark run reflib` fails: gate below threshold | Embed pipeline incomplete or provider mis-tuned | fix: `kairix embed --limit 100` to confirm embed works end-to-end; if green, run `kairix embed` for a full pass; then re-benchmark. next: if the gate still fails, escalate to the user — likely a model-quality issue requiring a provider swap. |

---

## 6. What to escalate to the user

Do NOT auto-decide these. Stop, surface the question, and wait for the user's answer.

- **Any secret value.** Tenant ids, client ids, secrets, API keys, Neo4j passwords. Even if the user has previously typed them — never persist a guess.
- **Permission grants.** AAD app registration consent (Sites.Read.All, Files.Read.All) is a high-trust action; the user — not you — should click through the consent screen.
- **Any spend.** `kairix embed` against a large corpus costs real money (~$0.50-1.00 per 1000 documents on text-embedding-3-large). Confirm before kicking off a full backfill.
- **Cutover flag flips on a soaked deployment.** If kairix has been running with `topology_v2_config: false` and the user asks you to flip it to true on a corpus the team is already using, capture a pre-flip baseline (`scripts/cutover/capture_baseline.py`) and tell the user to read `docs/architecture/feature-flag-architecture.md` §"Cutover protocol" before the flip.
- **Destructive verbs.** `kairix entity purge`, `kairix cc-pair delete`, `kairix uninstall --no-keep-data`, anything that drops data. Always confirm.
- **Production deployments.** Setup on a shared VM / production host: confirm with the user that the host is the intended target before running.

---

## 7. Cross-references

- [`quick-start.md`](quick-start.md) — the human-facing version with Docker + pip parallel steps
- [`kairix.config.example.yaml`](../../kairix.config.example.yaml) — the canonical operator-facing YAML shape, with inline comments per block
- [`docs/architecture/feature-flag-architecture.md`](../architecture/feature-flag-architecture.md) — the cutover protocol for any flag flip on a soaked corpus
- [`docs/architecture/connector-scope-topology/ADR.md`](../architecture/connector-scope-topology/ADR.md) — full spec for topology v2 (connectors / collections / scope profiles / skills)
- [`docs/operations/MCP-DEPLOYMENT.md`](../operations/MCP-DEPLOYMENT.md) — cold-start behaviour the MCP server reports during warm-up
- [`docs/user-guide/agent-usage-guide.md`](../user-guide/agent-usage-guide.md) — what `kairix onboard guide` installs into the operator's knowledge store

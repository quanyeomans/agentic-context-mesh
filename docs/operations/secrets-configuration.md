# Secrets Configuration

How to give kairix its credentials — the LLM API key, embed-provider key, Neo4j password, and per-connector secrets. Covers both install paths (Docker and pip) and the realistic secret-source options (env vars, .env file, Azure Key Vault, AWS Secrets Manager, GCP Secret Manager, 1Password CLI, plain Docker secrets).

If you're standing up a brand-new deployment and just want it working, jump to [Local-dev shortcut](#local-dev-shortcut) or [Docker on a VM with Azure Key Vault](#docker-on-a-vm-with-azure-key-vault). If you're an agent reading this on a user's behalf, see [agent-driven-setup.md](../getting-started/agent-driven-setup.md) for the declarative path.

## Mental model

Kairix needs values like `KAIRIX_PROVIDER_LLM_API_KEY` to be present in the process environment of every kairix command and service. How those values *get there* is your choice — there's no kairix-specific magic. The recommended pattern depends on where you're running:

| Where you run kairix | Where the secrets live | How they reach the process |
|---|---|---|
| Local dev / laptop | `.env` file or your shell rc | `docker compose` auto-loads `.env`; pip install reads `$KAIRIX_SECRETS_FILE` |
| Shared VM / server | Cloud secrets manager (KV / Secrets Manager / Secret Manager) | A small sidecar (systemd one-shot, init container, or built-in cloud feature) writes a file to `/run/secrets/kairix.env`; docker-compose mounts it |
| Managed container platform (ECS / Cloud Run / AKS) | Native secret references in the task definition | The platform injects them as env vars; no extra plumbing |
| Locally via Claude Desktop / Cursor | Your shell env, your platform credential helper, or a dotenv loader you call before launching | The MCP client launches `kairix mcp serve` as a subprocess and inherits your environment |

The values are the same in every case. What changes is where the file (or the platform) reads them from.

## Resolution order

When a kairix process starts, it resolves each secret using this order (first hit wins):

1. **Process environment** — `KAIRIX_PROVIDER_LLM_API_KEY=…` already set in env
2. **Per-file secret** — `/run/secrets/<canonical-name>` exists and is readable
3. **Bundle file** — `KEY=VALUE` lines in the file pointed to by `$KAIRIX_SECRETS_FILE` (default `/run/secrets/kairix.env`)
4. **Azure Key Vault CLI fallback** — `az keyvault secret show --vault-name $KAIRIX_KV_NAME --name <name>` when `KAIRIX_KV_NAME` is set

Most deployments use one or two of these — usually `(1)` for local dev or `(3)` for production. The CLI fallback `(4)` is meant as a safety net, not the hot path; pulling on every read adds 200-500ms of az latency per secret. If you're using KV, use a sidecar to materialise the values into `(2)` or `(3)` at boot.

## Canonical secret names

Every kairix secret has a canonical name following one schema, regardless of which secrets manager you use:

```
kairix-<scope>-<area>[-<instance>]-<leaf>
```

Examples:

| Canonical name | What it is |
|---|---|
| `kairix-provider-llm-api-key` | LLM API key |
| `kairix-provider-llm-endpoint` | LLM endpoint URL |
| `kairix-provider-embed-api-key` | Separate embed provider key (falls back to LLM) |
| `kairix-infra-neo4j-password` | Neo4j password |
| `kairix-connector-m365-tenant-id` | M365 / SharePoint tenant id |
| `kairix-connector-m365-client-secret` | M365 client secret |
| `kairix-connector-github-app-id` | GitHub App id (App mode) |
| `kairix-connector-github-app-private-key` | GitHub App PEM private key (multi-line — see note below) |
| `kairix-connector-github-installation-id` | GitHub App installation id |
| `kairix-connector-github-pat` | GitHub personal access token (PAT-mode alternative) |
| `kairix-connector-apple-caldav-app-password` | Apple CalDAV app password |
| `kairix-connector-obsidian-tcv-encryption-password` | Per-vault Obsidian encryption password (`tcv` is an example instance id) |
| `kairix-connector-linear-api-key` | Linear API key (workspace or personal key) |

The env var form is the canonical name uppercased with `-` → `_`: `kairix-provider-llm-api-key` → `KAIRIX_PROVIDER_LLM_API_KEY`. One rule, one function — see `kairix.secrets.naming.canonical_env_var()`.

> **Multi-line values (PEM keys):** the bundle file is one `KEY=VALUE` pair per line. When you store a multi-line value through `kairix secrets set`, `kairix connect`, or the setup wizard, kairix encodes it onto one quoted line (`KEY="-----BEGIN ...\n..."`) and decodes it back to the original bytes when reading. The line stays greppable and every other tool that parses the file keeps working. KV-backed sources (Azure KV, per-file secret mounts) hold the real multi-line value directly — no encoding needed there.

> **Legacy aliases retired:** older env var names (`KAIRIX_LLM_API_KEY`, `CONNECTOR_M365_TENANT_ID`, `APPLE_CALDAV_USERNAME`, etc.) are no longer recognised. Rotate to the canonical `KAIRIX_<SCOPE>_<AREA>[_<INSTANCE>]_<LEAF>` form (see the canonical-name table above). Run `kairix secrets verify` after rotating to confirm every credential resolves.

> **Multi-instance connectors:** when you run multiple instances of the same connector (multiple Obsidian vaults, multiple Slack workspaces, multiple GitHub PATs), the `<instance>` slot disambiguates them. The instance ids come from your `kairix.config.yaml` connector blocks — kairix doesn't enumerate them on its own.

## Install path × secret source matrix

Pick one row and follow it. The recipes below it cover the detail.

| Install | Local dev | Single-machine prod | VM with cloud secrets manager | Managed container platform |
|---|---|---|---|---|
| **Docker** | `.env` file ([recipe](#docker-with-env-file)) | `.env` file with restricted perms ([recipe](#docker-with-env-file)) | sidecar → `/run/secrets/kairix.env` ([Azure KV recipe](#docker-on-a-vm-with-azure-key-vault), [AWS recipe](#docker-on-a-vm-with-aws-secrets-manager), [GCP recipe](#docker-on-a-vm-with-gcp-secret-manager)) | Platform-native env injection ([ECS](#ecs--fargate), [Cloud Run](#cloud-run), [AKS](#aks-with-csi-driver)) |
| **Pip** | dotenv via `$KAIRIX_SECRETS_FILE` ([recipe](#pip-with-secrets-file)) | systemd unit + `$KAIRIX_SECRETS_FILE` ([recipe](#pip-with-systemd)) | systemd one-shot fetches into the secrets file ([recipe](#pip-with-systemd-fetch-from-cloud)) | not the typical shape — use Docker on these platforms |

## Local-dev shortcut

Get a kairix instance running on your laptop in 60 seconds with hardcoded local secrets. No cloud, no KV, nothing to provision.

```bash
cp .env.example .env
# Edit .env — set KAIRIX_PROVIDER_LLM_ENDPOINT + KAIRIX_PROVIDER_LLM_API_KEY to your provider
docker compose up -d
```

Or with pip:

```bash
mkdir -p ~/.config/kairix/secrets
cat > ~/.config/kairix/secrets/kairix.env <<'EOF'
KAIRIX_PROVIDER_LLM_ENDPOINT=https://your-resource.openai.azure.com
KAIRIX_PROVIDER_LLM_API_KEY=your-key-here
EOF
chmod 600 ~/.config/kairix/secrets/kairix.env
export KAIRIX_SECRETS_FILE=~/.config/kairix/secrets/kairix.env
kairix worker run &
kairix mcp serve --transport http --port 8080
```

That's it. The same shape scales up to production — only the *source* of the file changes.

## Recipes

### Docker with .env file

For local dev or single-machine prod where managing a secrets manager is overkill.

```bash
cp .env.example .env
chmod 600 .env   # restrict perms — the file holds plaintext credentials
# Edit .env with your values
docker compose up -d
```

The `env_file: - .env` block in `docker-compose.yml` loads every line into each service's environment. No further wiring needed.

For prod hygiene: store the `.env` file outside the repo (e.g. `/etc/kairix/.env`), reference it from a `docker-compose.override.yml`, and back it up to your password manager out-of-band.

### Docker on a VM with Azure Key Vault

The recommended production shape for Azure deployments. The VM's managed identity has `Key Vault Secrets User` on the KV; a systemd one-shot fetches every `kairix-*` secret and writes a bundle file; docker-compose mounts it as `/run/secrets/kairix.env`; kairix reads it on startup.

```bash
# 1. Install the etc-default file the systemd unit sources for KAIRIX_KV_NAME.
#    The unit reads /etc/default/kairix-fetch-secrets via EnvironmentFile=-,
#    so this file is the canonical place for the vault name on a VM — no
#    shell-rc / docker-compose .env juggling required.
sudo install -m 0644 scripts/deploy/etc-default-kairix-fetch-secrets \
    /etc/default/kairix-fetch-secrets
sudo $EDITOR /etc/default/kairix-fetch-secrets   # replace KAIRIX_KV_NAME=... with your vault

# 2. Install the fetch-secrets systemd unit
sudo cp scripts/deploy/kairix-fetch-secrets.service /etc/systemd/system/
sudo cp scripts/deploy/fetch-secrets.sh /etc/kairix/bin/
sudo chmod +x /etc/kairix/bin/fetch-secrets.sh
sudo systemctl enable --now kairix-fetch-secrets.service

# 3. Verify
ls -la /run/secrets/kairix.env   # should be 0640, root:kairix
sudo systemctl status kairix-fetch-secrets.service

# 4. Start kairix — docker-compose mounts /run/secrets into the containers
docker compose -f /etc/kairix/docker-compose.yml up -d
```

`KAIRIX_KV_NAME` lives in `/etc/default/kairix-fetch-secrets` so the systemd
unit can source it on every `systemctl restart` without depending on the
operator's interactive shell. Previously the unit could fail on restart
after a reboot because the vault name was only exported in the operator's
`.bashrc` — the etc-default file removes that footgun.

The fetch-secrets script reads `az keyvault secret list --query "[?starts_with(name,'kairix-')]"` and writes each one to both `/run/secrets/<name>` (per-file) and `/run/secrets/kairix.env` (bundle). No per-secret list to maintain — adding `kairix-connector-newthing-api-key` to your KV makes it available on the next service restart, with no code change.

> **Group/gid contract (load-bearing).** The secrets files are mode `0640` — group-readable only — and are group-owned by the **container's runtime group**, `kairix` (gid **985** in the published image). The kairix container runs as `uid 995 / gid 985` and reads the bundle via group-read, so the host's `kairix` group **must** be gid 985 to match the image. Grouping the files to a *different* group (e.g. the operator's `openclaw` group) means the container cannot read its own secrets and crash-loops on the next recreate — this caused the 2026-06-28 outage, where `openclaw` had drifted to gid 1001 while the container stayed gid 985. Override the group with `KAIRIX_SECRETS_GROUP` (default `kairix`) only if you point it at another group the container is in. The `openclaw` operator user is a member of the `kairix` group, so it retains read access for debugging. `fetch-secrets.sh` warns in its output if the chosen group's gid doesn't match `KAIRIX_CONTAINER_GID` (default 985).

To rotate a secret: run `az keyvault secret set` with `--vault-name "$KAIRIX_KV_NAME"`, `--name "<canonical-name>"`, and the new value, then `sudo systemctl restart kairix-fetch-secrets && docker compose restart kairix`.

### Docker on a VM with AWS Secrets Manager

Same shape as Azure but with AWS CLI on the fetch side. The VM's instance profile has `secretsmanager:GetSecretValue` + `secretsmanager:ListSecrets` for secrets matching `kairix-*`.

```bash
# Install fetch-secrets-aws.sh (community-maintained alternative)
cat > /etc/kairix/bin/fetch-secrets-aws.sh <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
OUT_FILE="/run/secrets/kairix.env"
mkdir -p /run/secrets
: > "$OUT_FILE"
aws secretsmanager list-secrets --filters "Key=name,Values=kairix-" \
  --query 'SecretList[].Name' --output text | tr '\t' '\n' | while read -r name; do
    value=$(aws secretsmanager get-secret-value --secret-id "$name" --query SecretString --output text)
    env_var="KAIRIX_$(echo "${name#kairix-}" | tr 'a-z-' 'A-Z_')"
    echo "${env_var}=${value}" >> "$OUT_FILE"
done
chmod 640 "$OUT_FILE"
EOF
chmod +x /etc/kairix/bin/fetch-secrets-aws.sh

# Wire it as a systemd one-shot, then proceed as Azure path
```

### Docker on a VM with GCP Secret Manager

```bash
# Same shape with gcloud
cat > /etc/kairix/bin/fetch-secrets-gcp.sh <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
OUT_FILE="/run/secrets/kairix.env"
mkdir -p /run/secrets
: > "$OUT_FILE"
gcloud secrets list --filter='name~^kairix-' --format='value(name)' | while read -r name; do
    short_name="${name##*/}"
    value=$(gcloud secrets versions access latest --secret="$short_name")
    env_var="KAIRIX_$(echo "${short_name#kairix-}" | tr 'a-z-' 'A-Z_')"
    echo "${env_var}=${value}" >> "$OUT_FILE"
done
chmod 640 "$OUT_FILE"
EOF
```

### Docker with 1Password CLI

For dogfooding or small-team deployments where 1Password is already your password manager:

```bash
# Reference a vault item by path; 1Password CLI injects the values into env
op run --env-file=/etc/kairix/.env.template -- docker compose up -d
```

Where `.env.template` contains `op://kairix-vault/llm-api-key/credential` references that 1Password resolves at process start. See [1Password docs on secret references](https://developer.1password.com/docs/cli/secret-references).

### ECS / Fargate

In the task definition, reference Secrets Manager directly. No fetch script.

```json
{
  "containerDefinitions": [{
    "name": "kairix",
    "image": "ghcr.io/three-cubes/kairix:latest",
    "secrets": [
      {"name": "KAIRIX_PROVIDER_LLM_API_KEY", "valueFrom": "arn:aws:secretsmanager:...:secret:kairix-provider-llm-api-key"},
      {"name": "KAIRIX_INFRA_NEO4J_PASSWORD", "valueFrom": "arn:aws:secretsmanager:...:secret:kairix-infra-neo4j-password"}
    ]
  }]
}
```

ECS injects each secret as an env var before kairix starts. The kairix loader's canonical-name resolution (step 1 in the resolution order) picks them up directly.

### Cloud Run

Mount Secret Manager secrets as env vars in the service:

```bash
gcloud run services update kairix \
  --update-secrets=KAIRIX_PROVIDER_LLM_API_KEY=kairix-provider-llm-api-key:latest,KAIRIX_INFRA_NEO4J_PASSWORD=kairix-infra-neo4j-password:latest
```

### AKS with CSI driver

Use the Azure Key Vault Provider for Secrets Store CSI Driver to project each secret as a file under `/run/secrets/`. Kairix's resolution order picks them up at step (2) without any glue code.

```yaml
# SecretProviderClass — projects every kairix-* secret as a file under /run/secrets/
apiVersion: secrets-store.csi.x-k8s.io/v1
kind: SecretProviderClass
metadata:
  name: kairix-secrets
spec:
  provider: azure
  parameters:
    keyvaultName: your-kv-name
    objects: |
      array:
        - |
          objectName: kairix-provider-llm-api-key
          objectType: secret
        - |
          objectName: kairix-infra-neo4j-password
          objectType: secret
```

### Pip with secrets file

```bash
mkdir -p ~/.config/kairix/secrets
cat > ~/.config/kairix/secrets/kairix.env <<'EOF'
KAIRIX_PROVIDER_LLM_ENDPOINT=https://your-resource.openai.azure.com
KAIRIX_PROVIDER_LLM_API_KEY=your-key-here
EOF
chmod 600 ~/.config/kairix/secrets/kairix.env
export KAIRIX_SECRETS_FILE=~/.config/kairix/secrets/kairix.env
```

Add the export to your shell rc so every shell sees it, or wire it into the systemd unit (next recipe).

### Pip with systemd

For long-running pip deployments — kairix as a system service that survives reboot.

```ini
# /etc/systemd/system/kairix-mcp.service
[Unit]
Description=Kairix MCP server
After=network-online.target
Wants=network-online.target

[Service]
Type=exec
User=kairix
Group=kairix
EnvironmentFile=/etc/kairix/kairix.env
Environment=KAIRIX_CONFIG_PATH=/etc/kairix/kairix.config.yaml
ExecStart=/usr/local/bin/kairix mcp serve --transport http --host 127.0.0.1 --port 8080
Restart=on-failure
RestartSec=5s

[Install]
WantedBy=multi-user.target
```

Where `/etc/kairix/kairix.env` is the secrets file (mode `0640`, owner `root:kairix`).

### Pip with systemd fetch-from-cloud

Combine [Docker-on-a-VM-with-KV](#docker-on-a-vm-with-azure-key-vault) and [Pip-with-systemd](#pip-with-systemd) — a systemd one-shot fetches from your cloud secrets manager into `/etc/kairix/kairix.env`, then the kairix-mcp service starts after it via `Wants=` + `After=`.

## Local MCP server (Claude Desktop / Cursor / Aider)

When an MCP-compatible client (Claude Desktop, Claude Code, Cursor, Aider) launches kairix as a subprocess, kairix inherits the client's process environment. If you've set `KAIRIX_PROVIDER_LLM_API_KEY` in your shell rc or via a credential helper, you're done.

If the client is launched from a GUI (Claude Desktop on macOS, for example) and doesn't pick up your shell rc, the easiest fix is a wrapper script that loads the secrets file before exec-ing kairix:

```bash
cat > ~/.local/bin/kairix-mcp <<'EOF'
#!/usr/bin/env bash
set -a
source ~/.config/kairix/secrets/kairix.env
set +a
exec ~/.venvs/kairix/bin/kairix mcp serve --transport stdio "$@"
EOF
chmod +x ~/.local/bin/kairix-mcp
```

Then point the MCP client at the wrapper:

```json
{
  "mcpServers": {
    "kairix": {
      "command": "/Users/<you>/.local/bin/kairix-mcp"
    }
  }
}
```

For `--transport http` deployments (a long-running kairix on your machine that multiple clients connect to), use the systemd recipe above — secrets load once at service start, clients just point at `http://localhost:8080/mcp`.

## Verifying your configuration

Run the deployment check — it reports per-subsystem status with a one-line remediation for any failure:

```bash
# Docker
docker compose exec kairix kairix onboard check
docker compose exec kairix kairix onboard check --json   # for healthchecks / CI

# Pip
kairix onboard check
```

A failed `secrets_loaded` row tells you which env var is missing and where to put it. The `--json` envelope is exit-code 0 iff every check passed.

To see what canonical names kairix is expecting (vs what's actually present), run:

```bash
kairix secrets verify   # walks every loaded connector + provider, reports each required secret
```

## Rotating a secret

The mechanics depend on your source:

| Source | Rotate |
|---|---|
| `.env` file | Edit the file; `docker compose restart kairix` |
| Azure KV (with fetch-secrets sidecar) | `az keyvault secret set …`; `sudo systemctl restart kairix-fetch-secrets && docker compose restart kairix` |
| AWS Secrets Manager | `aws secretsmanager update-secret …`; restart kairix services |
| GCP Secret Manager | `gcloud secrets versions add …`; restart kairix services |
| ECS / Cloud Run (native) | Update the secret; the platform redeploys the task automatically |
| AKS with CSI | Update the secret; the CSI driver refreshes the projected file; restart pods |
| 1Password CLI | Update the item in 1Password; restart kairix (re-resolves on next launch) |

After every rotation, run `kairix onboard check --json` to verify the new value loaded cleanly.

## Capturing OAuth2 tokens with `kairix connect`

For Google connectors (Gmail / Drive / Calendar) — and the Slack / GitHub App connectors landing alongside — `kairix connect` automates the OAuth2 capture so you do not have to copy-paste tokens into your KV.

### Google (Gmail / Drive / Calendar)

One-time GCP setup (do this once per kairix install; the same OAuth client works for all three Google connectors):

1. **GCP project + APIs.** In the [GCP console](https://console.cloud.google.com/), create or pick a project. Under *APIs & Services → Library*, enable the APIs you need: **Gmail API**, **Google Drive API**, **Google Calendar API**.
2. **Consent screen — set to Production.** Under *APIs & Services → OAuth consent screen*, choose **External** user type and configure the app name + support email. Then click **Publish App** to move the consent screen from Testing to Production.

   This step is unavoidable and load-bearing. In Testing mode, Google silently expires refresh tokens after 7 days; your connector will stop working a week after capture and the failure is difficult to diagnose. Production mode keeps refresh tokens valid until the operator explicitly revokes them.
3. **Create the OAuth client.** Under *APIs & Services → Credentials → Create credentials → OAuth client ID*, pick application type **Desktop app**. Name it whatever you like. After creation, click **Download JSON** — you'll get a `client_secret_<long-id>.json` file.
4. **Run `kairix connect`.** Pass the downloaded JSON to the connect command:

   ```bash
   kairix connect google-gmail --client-secret-path ~/Downloads/client_secret.json
   kairix connect google-drive --client-secret-path ~/Downloads/client_secret.json
   kairix connect google-calendar --client-secret-path ~/Downloads/client_secret.json
   ```

   For each subcommand, your default browser opens to Google's consent screen, you approve the scopes, and `kairix connect` captures the resulting tokens into `$KAIRIX_SECRETS_FILE` (default `~/.config/kairix/secrets/kairix.env`).

### Store backends

`kairix connect` writes to whatever backend you choose:

| Backend | Flag | Where the tokens land |
|---|---|---|
| File (default) | `--store=file` | `$KAIRIX_SECRETS_FILE` (default `~/.config/kairix/secrets/kairix.env`) |
| Azure Key Vault | `--store=azure-kv` (reads `$KAIRIX_KV_NAME`) | `https://<vault>.vault.azure.net/` |
| Azure Key Vault (named) | `--store=azure-kv:<vault-name>` | `https://<vault-name>.vault.azure.net/` |
| Azure Key Vault (full URL) | `--store=azure-kv:https://<vault>.vault.usgovcloudapi.net/` | the exact URL (sovereign clouds) |
| Stdout TSV | `--store=stdout` | `<CANONICAL_ENV_VAR>\t<value>` lines piped wherever you like |

For Azure Key Vault, the identity running `kairix connect` needs the **Key Vault Secrets Officer** role on the vault (the read-only `Key Vault Secrets User` is not enough — writes need Officer). See [ADR-032 §"Operator setup for `--store=azure-kv`"](../architecture/ADR-032-oauth2-connect-flow.md) for the full identity-options matrix (managed identity / service principal / `az login`).

### Headless VMs

`kairix connect` opens a browser locally; on a headless VM it will fail fast with a clear hint to run from your local workstation instead. A `--no-browser` mode that prints the URL for you to paste into a remote browser is planned for a future release.

## Adding a new secret

The canonical-name convention is load-bearing — there's no central registry to update. To add a new secret:

1. **Pick the canonical name** following `kairix-<scope>-<area>[-<instance>]-<leaf>`. For example, a new "Linear" connector's API key would be `kairix-connector-linear-api-key`.
2. **Provision it** in your secrets source under the canonical name (`az keyvault secret set --name kairix-connector-linear-api-key …`).
3. **Restart** the fetch-secrets sidecar (if you're using one) or restart kairix directly. The convention-driven fetch script picks up every `kairix-*` prefix automatically.
4. **In code**, the connector calls `secrets.require(scope="connector", area="linear", instance=None, leaf="api-key")` — and that's it. No manifest, no glue.

For connectors that don't yet exist in kairix, the canonical name is still useful documentation: `kairix secrets verify` inspects what's loaded vs what's needed, so adding a new secret to your KV pre-deploy is reflected in `verify`'s "missing" report after the connector ships.

## Reference

- [Canonical naming convention spec (ADR-031)](../architecture/ADR-031-canonical-credential-naming.md) — the schema, the derivation rules, and the dual-write migration plan
- [OPERATIONS.md §Configuration vs Secrets](OPERATIONS.md#configuration-vs-secrets) — the broader operations doc
- [MCP-DEPLOYMENT.md](MCP-DEPLOYMENT.md) — MCP server deployment (covers transports, healthchecks, gateway routing)
- [Quick start](../getting-started/quick-start.md) — fastest path from zero to a running kairix
- [Agent setup](../agents/AGENT-SETUP.md) — what agents need to know when kairix's secrets layer degrades

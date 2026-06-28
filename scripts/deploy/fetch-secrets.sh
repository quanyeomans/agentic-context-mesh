#!/usr/bin/env bash
#
# Fetches kairix secrets from Azure Key Vault and writes them to
# /run/secrets/ for docker-compose to mount into the kairix containers.
#
# Convention-driven (ADR-031): instead of a hardcoded per-secret map,
# this script lists every secret in KV whose name starts with `kairix-`
# and derives the env var name deterministically:
#
#     kairix-<scope>-<area>[-<instance>]-<leaf>  (KV name)
#       → KAIRIX_<SCOPE>_<AREA>[_<INSTANCE>]_<LEAF>  (env var, the loader's canonical form)
#
# Adding a new secret means just creating it in KV with the right
# prefix — no script edit, no service restart on the host.
#
# For backwards compatibility with code paths that still read legacy
# env vars directly (the historic `kairix.secrets.get_secret(...)`
# resolver), this script also exports the legacy aliases from
# LEGACY_KV_TO_LEGACY_ENV below — see kairix/secrets/_legacy_aliases.py
# for the canonical source. The legacy block can be deleted once every
# connector + provider has been refactored to call `SecretsLoader`
# directly (tracked as the Wave-2 follow-up to ADR-031).
#
# Write targets:
#   /run/secrets/<canonical-kv-name>   per-file secret (mode 0640)
#   /run/secrets/kairix.env            KEY=VALUE bundle (mode 0640)
#
# Authentication:
#   Uses the VM's managed identity. The identity needs `Key Vault
#   Secrets User` role on the vault named in $KAIRIX_KV_NAME.
#
# Invocation:
#   Run as root via a systemd one-shot before docker-compose starts.
#   See scripts/deploy/kairix-fetch-secrets.service.
#
# Operator overrides:
#   - $KAIRIX_KV_NAME  — the Key Vault to read from (required)
#   - $KAIRIX_SECRETS_DIR  — where to write (default /run/secrets)
#   - $KAIRIX_SECRETS_GROUP — group that owns the secrets files (default
#                             `kairix`, the container's runtime group). MUST be
#                             a group the kairix container is in (gid 985 in the
#                             published image) or the container cannot read its
#                             own /run/secrets/kairix.env. See the gid guard
#                             below.
#   - $KAIRIX_CONTAINER_GID — the gid the kairix container reads as (default
#                             985, the published image's `kairix` group); used
#                             only by the mismatch guard.
#   - $KAIRIX_LEGACY_EXPORT=0  — skip the legacy env-var aliases
#                                (turn on once Wave-2 lands)

set -euo pipefail

VAULT_NAME="${KAIRIX_KV_NAME:-}"
OUT_DIR="${KAIRIX_SECRETS_DIR:-/run/secrets}"
OUT_FILE="${OUT_DIR}/kairix.env"
LEGACY_EXPORT="${KAIRIX_LEGACY_EXPORT:-1}"
# The secrets files are group-readable (mode 0640). Group them to the
# CONTAINER's runtime group — `kairix` (gid 985 in the published image) — NOT
# the operator's `openclaw` group. The kairix container runs as uid 995 / gid
# 985 and reads the bundle via group-read; grouping the files to a group the
# container is not in makes it unable to read its own secrets and crash-loop on
# the next recreate (the 2026-06-28 incident: `openclaw` had drifted to gid
# 1001 while the container stayed gid 985, so a fetch-secrets re-run produced
# files the container could no longer read). The `openclaw` operator user is a
# member of the `kairix` group, so it keeps read access for debugging.
SECRETS_GROUP="${KAIRIX_SECRETS_GROUP:-kairix}"

if [[ -z "$VAULT_NAME" ]]; then
    echo "fetch-secrets: ERROR — KAIRIX_KV_NAME is not set" >&2
    echo "fix: set KAIRIX_KV_NAME=<your-vault-name> in /etc/default/kairix-fetch-secrets" >&2
    exit 1
fi

# Guard: the kairix container reads these files as gid $CONTAINER_GID (985 in
# the published image). If the chosen secrets group resolves to a different
# gid, the container will NOT be able to read /run/secrets/kairix.env and will
# crash-loop on the next recreate. Warn loudly so a host group/gid mismatch
# surfaces in the journal (and the deploy log) instead of as a silent outage.
CONTAINER_GID="${KAIRIX_CONTAINER_GID:-985}"
secrets_group_gid="$(getent group "$SECRETS_GROUP" | cut -d: -f3 || true)"
if [[ -n "$secrets_group_gid" && "$secrets_group_gid" != "$CONTAINER_GID" ]]; then
    echo "fetch-secrets: WARN — secrets group '${SECRETS_GROUP}' is gid ${secrets_group_gid} but the kairix container reads as gid ${CONTAINER_GID}; the container may be unable to read ${OUT_FILE}. Align the host '${SECRETS_GROUP}' group to gid ${CONTAINER_GID}, or set KAIRIX_SECRETS_GROUP to a group the container is in." >&2
fi

mkdir -p "$OUT_DIR"
chown "root:${SECRETS_GROUP}" "$OUT_DIR"
chmod 751 "$OUT_DIR"

: > "$OUT_FILE"
chmod 640 "$OUT_FILE"
chown "root:${SECRETS_GROUP}" "$OUT_FILE"

# Derive the canonical env-var name from a canonical KV name.
# Rule: strip `kairix-` prefix, replace `-` with `_`, uppercase, prefix `KAIRIX_`.
# Matches kairix.secrets.naming.canonical_env_var() exactly.
canonical_env_for() {
    local kv_name="$1"
    local stripped="${kv_name#kairix-}"
    echo "KAIRIX_$(echo "$stripped" | tr 'a-z-' 'A-Z_')"
}

# Map of canonical KV name -> legacy env var(s) to also export.
# Mirrors kairix.secrets._legacy_aliases.LEGACY_ALIASES. Keep in sync
# until the Wave-2 connector refactor lands and historic env reads are
# gone, then delete this table + the loop that uses it.
#
# Format: one line per canonical name, "canonical-kv-name LEGACY_ENV_VAR[ ANOTHER_LEGACY]"
# Hash-prefix lines are comments; blank lines ignored.
LEGACY_KV_TO_LEGACY_ENV=$(cat <<'EOF'
# infra
kairix-infra-neo4j-password KAIRIX_NEO4J_PASSWORD
kairix-infra-neo4j-uri KAIRIX_NEO4J_URI
kairix-infra-neo4j-user KAIRIX_NEO4J_USER
# providers
kairix-provider-llm-api-key KAIRIX_LLM_API_KEY
kairix-provider-llm-endpoint KAIRIX_LLM_ENDPOINT
kairix-provider-llm-model KAIRIX_LLM_MODEL
kairix-provider-embed-api-key KAIRIX_EMBED_API_KEY
kairix-provider-embed-endpoint KAIRIX_EMBED_ENDPOINT
kairix-provider-embed-model KAIRIX_EMBED_MODEL
kairix-provider-embed-dims KAIRIX_EMBED_DIMS
# connectors — M365 / SharePoint / Calendar / Email headers
kairix-connector-m365-tenant-id CONNECTOR_M365_TENANT_ID KAIRIX_M365_TENANT_ID
kairix-connector-m365-client-id CONNECTOR_M365_CLIENT_ID KAIRIX_M365_CLIENT_ID
kairix-connector-m365-client-secret CONNECTOR_M365_CLIENT_SECRET KAIRIX_M365_CLIENT_SECRET
# connectors — Slack
kairix-connector-slack-bot-token CONNECTOR_SLACK_BOT_TOKEN
kairix-connector-slack-app-token CONNECTOR_SLACK_APP_TOKEN
kairix-connector-slack-client-id CONNECTOR_SLACK_CLIENT_ID
kairix-connector-slack-client-secret CONNECTOR_SLACK_CLIENT_SECRET
# connectors — GitHub
kairix-connector-github-pat CONNECTOR_GITHUB_PERSONAL_ACCESS_TOKEN
kairix-connector-github-app-id CONNECTOR_GITHUB_APP_ID
kairix-connector-github-installation-id CONNECTOR_GITHUB_INSTALLATION_ID
kairix-connector-github-app-private-key CONNECTOR_GITHUB_APP_PRIVATE_KEY
kairix-connector-github-webhook-secret CONNECTOR_GITHUB_WEBHOOK_SECRET
# connectors — Notion
kairix-connector-notion-token CONNECTOR_NOTION_TOKEN
# connectors — Google
kairix-connector-google-drive-access-token CONNECTOR_GOOGLE_DRIVE_ACCESS_TOKEN
kairix-connector-gmail-client-id CONNECTOR_GMAIL_CLIENT_ID
kairix-connector-gmail-client-secret CONNECTOR_GMAIL_CLIENT_SECRET
kairix-connector-gmail-refresh-token CONNECTOR_GMAIL_REFRESH_TOKEN
kairix-connector-gmail-access-token CONNECTOR_GMAIL_ACCESS_TOKEN
# connectors — Apple CalDAV
kairix-connector-apple-caldav-username CONNECTOR_APPLE_CALDAV_USERNAME
kairix-connector-apple-caldav-app-password CONNECTOR_APPLE_CALDAV_PASSWORD
# connectors — Dex
kairix-connector-dex-api-key CONNECTOR_DEX_API_KEY
EOF
)

# Look up legacy aliases for a canonical name. Echoes nothing on miss.
legacy_aliases_for() {
    local kv_name="$1"
    echo "$LEGACY_KV_TO_LEGACY_ENV" | awk -v kv="$kv_name" '
        /^#/ || /^$/ { next }
        $1 == kv { for (i=2; i<=NF; i++) print $i }
    '
}

write_per_file() {
    local name="$1"
    local value="$2"
    local target="${OUT_DIR}/${name}"
    printf %s "$value" > "$target"
    chmod 640 "$target"
    chown "root:${SECRETS_GROUP}" "$target"
}

write_to_bundle() {
    local var_name="$1"
    local value="$2"
    echo "${var_name}=${value}" >> "$OUT_FILE"
}

# Discover every kairix-* secret in the vault.
canonical_count=0
legacy_count=0

# Read kairix-* secret names into an array — one name per line from az tsv output.
canonical_names=()
while IFS= read -r line; do
    [[ -n "$line" ]] && canonical_names+=("$line")
done < <(az keyvault secret list \
    --vault-name "$VAULT_NAME" \
    --query "[?starts_with(name,'kairix-')].name" \
    -o tsv 2>/dev/null)

if [[ ${#canonical_names[@]} -eq 0 ]]; then
    echo "fetch-secrets: WARN — no secrets starting with 'kairix-' found in $VAULT_NAME" >&2
    echo "fix: provision secrets with 'az keyvault secret set --vault-name $VAULT_NAME --name kairix-<scope>-<area>-<leaf> ...'" >&2
fi

for canonical_name in "${canonical_names[@]}"; do
    value=$(az keyvault secret show \
        --vault-name "$VAULT_NAME" \
        --name "$canonical_name" \
        --query value -o tsv 2>/dev/null || true)

    if [[ -z "$value" ]]; then
        echo "fetch-secrets: WARN — ${canonical_name} is empty or unreadable" >&2
        continue
    fi

    # Per-file secret (always — supports docker secrets + AKS CSI shape).
    write_per_file "$canonical_name" "$value"

    # Canonical env var into bundle (the loader's primary path).
    canonical_env=$(canonical_env_for "$canonical_name")
    write_to_bundle "$canonical_env" "$value"
    canonical_count=$((canonical_count + 1))

    # Legacy env-var aliases for backwards compatibility.
    if [[ "$LEGACY_EXPORT" == "1" ]]; then
        while IFS= read -r legacy_env; do
            [[ -z "$legacy_env" ]] && continue
            write_to_bundle "$legacy_env" "$value"
            legacy_count=$((legacy_count + 1))
        done < <(legacy_aliases_for "$canonical_name")
    fi
done

# Webhook file-only secrets — read by the alpha-deploy webhook process
# (kairix-webhook group), not by the kairix containers. Kept on the
# convention-driven path because their names already follow the
# canonical schema; they don't go through the bundle file because the
# webhook uses WEBHOOK_SECRET_PATH / KAIRIX_GITHUB_PAT_PATH file paths.
WEBHOOK_SECRETS=( kairix-alpha-deploy-webhook-secret kairix-alpha-deploy-webhook-pat )
WEBHOOK_GROUP="${KAIRIX_WEBHOOK_GROUP:-kairix-webhook}"
for secret_name in "${WEBHOOK_SECRETS[@]}"; do
    value=$(az keyvault secret show \
        --vault-name "$VAULT_NAME" \
        --name "$secret_name" \
        --query value -o tsv 2>/dev/null || true)
    if [[ -z "$value" ]]; then
        echo "fetch-secrets: WARN — ${secret_name} empty or missing in KV" >&2
        continue
    fi
    target="${OUT_DIR}/${secret_name}"
    printf %s "$value" > "$target"
    chmod 640 "$target"
    chown "root:${WEBHOOK_GROUP}" "$target"
done

echo "fetch-secrets: wrote ${canonical_count} canonical secret(s) + ${legacy_count} legacy alias(es) to ${OUT_FILE} ($(stat -c '%U:%G %a' "$OUT_FILE" 2>/dev/null || echo 'perms?'))"

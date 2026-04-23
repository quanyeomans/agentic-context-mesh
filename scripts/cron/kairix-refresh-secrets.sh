#!/usr/bin/env bash
# kairix-refresh-secrets.sh — periodic secret refresh from Azure Key Vault
#
# Fetches latest secrets from Azure KV and writes to the kairix secrets file.
# Designed to be run via cron (e.g. every 6 hours) on bare-VM deployments
# where there is no vault-agent sidecar to keep secrets fresh.
#
# USAGE:
#   0 */6 * * * /opt/kairix/cron/kairix-refresh-secrets.sh >> /opt/kairix/logs/secrets-refresh.log 2>&1
#
# REQUIREMENTS:
#   - Azure CLI (az) installed and authenticated
#   - KAIRIX_KV_NAME set in /opt/kairix/service.env

set -euo pipefail

KAIRIX_OPT="${KAIRIX_OPT:-/opt/kairix}"
SECRETS_FILE="${KAIRIX_SECRETS_FILE:-${KAIRIX_OPT}/secrets.env}"

log() { echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] $*"; }

# Source service.env for KAIRIX_KV_NAME
if [[ -f "${KAIRIX_OPT}/service.env" ]]; then
    # shellcheck source=/dev/null
    source "${KAIRIX_OPT}/service.env"
fi

KV_NAME="${KAIRIX_KV_NAME:-}"
if [[ -z "$KV_NAME" ]]; then
    log "ERROR: KAIRIX_KV_NAME not set — cannot refresh secrets"
    exit 1
fi

if ! command -v az >/dev/null 2>&1; then
    log "ERROR: Azure CLI (az) not found — cannot refresh secrets"
    exit 1
fi

fetch_secret() {
    az keyvault secret show --vault-name "$KV_NAME" --name "$1" --query value -o tsv 2>/dev/null
}

SECRETS_TMP="${SECRETS_FILE}.tmp"

{
    echo "# Refreshed from Azure KV '${KV_NAME}' on $(date -u +%Y-%m-%dT%H:%M:%SZ)"
    for kv_env in \
        "azure-openai-api-key:AZURE_OPENAI_API_KEY" \
        "azure-openai-endpoint:AZURE_OPENAI_ENDPOINT" \
        "kairix-neo4j-password:KAIRIX_NEO4J_PASSWORD"; do
        kv_name="${kv_env%%:*}"
        env_var="${kv_env#*:}"
        value=$(fetch_secret "$kv_name")
        if [[ -n "$value" ]]; then
            echo "${env_var}=${value}"
        else
            log "WARNING: ${kv_name} not found in vault — skipping"
        fi
    done
} > "$SECRETS_TMP"

# Atomic write — prevents partial reads by kairix wrapper
mv "$SECRETS_TMP" "$SECRETS_FILE"
chmod 600 "$SECRETS_FILE"

log "Secrets refreshed successfully → ${SECRETS_FILE}"

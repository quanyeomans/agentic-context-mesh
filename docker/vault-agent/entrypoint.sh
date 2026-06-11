#!/usr/bin/env bash
# kairix-vault-agent — fetches Azure KV secrets to shared tmpfs, refreshes every 8h
set +x  # NEVER enable tracing — secret values will leak to stderr
set -euo pipefail

KV_NAME="${KAIRIX_KV_NAME:?KAIRIX_KV_NAME must be set}"
SECRETS_DIR="${KAIRIX_SECRETS_DIR:-/run/secrets}"
REFRESH="${REFRESH_INTERVAL_SECONDS:-28800}"

fetch_and_write() {
    local tmpfile kv_name_local
    kv_name_local="$KV_NAME"
    tmpfile=$(mktemp "${SECRETS_DIR}/.secrets.XXXXXX")
    chmod 600 "$tmpfile"
    _fetch() { local secret_name="$1"; az keyvault secret show --vault-name "$kv_name_local" --name "$secret_name" --query value -o tsv 2>/dev/null || echo ""; }
    # Resolve a secret by its canonical KV name (kairix-<scope>-<area>-<leaf>,
    # see docs/operations/secrets-configuration.md), falling back to the
    # pre-canonical short name so existing vaults keep working during the
    # transition window (#479).
    _fetch_canonical() {
        local value
        value=$(_fetch "$1")
        if [[ -z "$value" ]]; then
            value=$(_fetch "$2")
        fi
        echo "$value"
    }
    local llm_api_key llm_endpoint llm_model
    local embed_api_key embed_endpoint embed_model neo4j_password
    llm_api_key=$(_fetch_canonical kairix-provider-llm-api-key kairix-llm-api-key)
    llm_endpoint=$(_fetch_canonical kairix-provider-llm-endpoint kairix-llm-endpoint)
    llm_model=$(_fetch_canonical kairix-provider-llm-model kairix-llm-model)
    embed_api_key=$(_fetch_canonical kairix-provider-embed-api-key kairix-embed-api-key)
    embed_endpoint=$(_fetch_canonical kairix-provider-embed-endpoint kairix-embed-endpoint)
    embed_model=$(_fetch_canonical kairix-provider-embed-model kairix-embed-model)
    neo4j_password=$(_fetch_canonical kairix-infra-neo4j-password kairix-neo4j-password)
    {
        # Canonical env-var names (docs/operations/secrets-configuration.md).
        echo "KAIRIX_PROVIDER_LLM_API_KEY=${llm_api_key}"
        echo "KAIRIX_PROVIDER_LLM_ENDPOINT=${llm_endpoint}"
        echo "KAIRIX_PROVIDER_LLM_MODEL=${llm_model}"
        echo "KAIRIX_PROVIDER_EMBED_API_KEY=${embed_api_key}"
        echo "KAIRIX_PROVIDER_EMBED_ENDPOINT=${embed_endpoint}"
        echo "KAIRIX_PROVIDER_EMBED_MODEL=${embed_model}"
        echo "KAIRIX_INFRA_NEO4J_PASSWORD=${neo4j_password}"
        # legacy aliases — remove with #369. KAIRIX_NEO4J_PASSWORD is NOT
        # only an alias yet: the graph layer (kairix.secrets.neo4j_password)
        # and docker-compose interpolation still read it today.
        echo "KAIRIX_LLM_API_KEY=${llm_api_key}"
        echo "KAIRIX_LLM_ENDPOINT=${llm_endpoint}"
        echo "KAIRIX_LLM_MODEL=${llm_model}"
        echo "KAIRIX_EMBED_API_KEY=${embed_api_key}"
        echo "KAIRIX_EMBED_ENDPOINT=${embed_endpoint}"
        echo "KAIRIX_EMBED_MODEL=${embed_model}"
        echo "KAIRIX_NEO4J_PASSWORD=${neo4j_password}"
    } >> "$tmpfile"
    mv -f "$tmpfile" "${SECRETS_DIR}/kairix.env"
    chmod 600 "${SECRETS_DIR}/kairix.env"
    echo "[vault-agent] Secrets written at $(date -u +%Y-%m-%dT%H:%M:%SZ)"
}

mkdir -p "$SECRETS_DIR"
fetch_and_write
while true; do
    sleep "$REFRESH"
    echo "[vault-agent] Refreshing secrets..."
    fetch_and_write
done

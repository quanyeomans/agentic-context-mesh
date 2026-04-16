#!/usr/bin/env bash
# kairix-vault-agent — fetches Azure KV secrets to shared tmpfs, refreshes every 8h
set -euo pipefail

KV_NAME="${KAIRIX_KV_NAME:?KAIRIX_KV_NAME must be set}"
SECRETS_DIR="${KAIRIX_SECRETS_DIR:-/run/secrets}"
REFRESH="${REFRESH_INTERVAL_SECONDS:-28800}"

fetch_and_write() {
    local tmpfile
    tmpfile=$(mktemp "${SECRETS_DIR}/.secrets.XXXXXX")
    chmod 600 "$tmpfile"
    _fetch() { az keyvault secret show --vault-name "$KV_NAME" --name "$1" --query value -o tsv 2>/dev/null || echo ""; }
    {
        printf 'AZURE_OPENAI_API_KEY=%s\n'             "$(_fetch azure-openai-api-key)"
        printf 'AZURE_OPENAI_ENDPOINT=%s\n'            "$(_fetch azure-openai-endpoint)"
        printf 'AZURE_OPENAI_EMBED_DEPLOYMENT=%s\n'    "$(_fetch azure-openai-embedding-deployment)"
        printf 'AZURE_OPENAI_GPT4O_MINI_DEPLOYMENT=%s\n' "$(_fetch azure-openai-gpt4o-mini-deployment)"
        printf 'KAIRIX_NEO4J_PASSWORD=%s\n'            "$(_fetch kairix-neo4j-password)"
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

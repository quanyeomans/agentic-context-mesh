#!/usr/bin/env bash
# deploy.sh — fetch secrets from Key Vault and run qmd-azure-embed
# Usage: ./scripts/deploy.sh [--force] [--limit N]
#
# Requires: az CLI authenticated, KV_NAME env var set to your Azure Key Vault name
# Logs to: /data/tc-agent-zone/logs/azure-embed.log

set -euo pipefail

KV_NAME="${KV_NAME:?Error: KV_NAME must be set (your Azure Key Vault name)}"
LOG=/data/tc-agent-zone/logs/azure-embed.log
mkdir -p "$(dirname "$LOG")"
timestamp() { date -u +%Y-%m-%dT%H:%M:%SZ; }

echo "[$(timestamp)] deploy.sh: fetching secrets from $KV_NAME" | tee -a "$LOG"

export AZURE_OPENAI_API_KEY=$(az keyvault secret show \
  --vault-name "$KV_NAME" --name azure-openai-api-key --query value -o tsv)

export AZURE_OPENAI_ENDPOINT=$(az keyvault secret show \
  --vault-name "$KV_NAME" --name azure-openai-endpoint --query value -o tsv)

export AZURE_OPENAI_EMBED_DEPLOYMENT=$(az keyvault secret show \
  --vault-name "$KV_NAME" --name azure-openai-embedding-deployment --query value -o tsv 2>/dev/null \
  || echo "text-embedding-3-large")

echo "[$(timestamp)] deploy.sh: deployment=$AZURE_OPENAI_EMBED_DEPLOYMENT" | tee -a "$LOG"

# Run embed — pass through any extra args (--force, --limit, etc.)
qmd-azure-embed embed "$@"
EXIT=$?

echo "[$(timestamp)] deploy.sh: done (exit=$EXIT)" | tee -a "$LOG"
exit $EXIT

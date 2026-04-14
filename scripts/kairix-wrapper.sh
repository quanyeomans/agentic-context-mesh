#!/usr/bin/env bash
# kairix-wrapper.sh — runtime environment loader for the kairix CLI
#
# DEPLOY: install this at /opt/kairix/bin/kairix-wrapper.sh
# and create a system-wide symlink:
#   sudo ln -sf /opt/kairix/bin/kairix-wrapper.sh /usr/local/bin/kairix
#
# WHY THIS EXISTS:
# The pip-installed kairix binary has no operator environment loaded.
# Agents calling `kairix search ...` from their exec context get:
#   - No AZURE_OPENAI_API_KEY → vector search fails (vec_failed=True, BM25 only)
#   - No KAIRIX_VAULT_ROOT / KAIRIX_KV_NAME → wrong paths, failed KV lookups
#   - No SQLITE_VEC_PATH → sqlite-vec extension not found
#
# This wrapper loads environment in priority order:
#   1. Service env (/opt/kairix/service.env) — paths, KV name, non-secret config
#   2. Pre-fetched secrets (/run/secrets/kairix.env) — Docker vault-agent sidecar
#   3. Existing environment — always wins (never overwritten)
# Then exec's the pip-installed kairix binary with the loaded environment.
#
# INSTALL DIR:
# kairix is installed as a pip package into /opt/kairix/.venv — not cloned from source.
# Set KAIRIX_VENV to override (e.g. if installed into a different venv).
#
# CONFIGURATION:
#   KAIRIX_VENV         — virtualenv containing the kairix binary (default: /opt/kairix/.venv)
#   KAIRIX_SERVICE_ENV  — service env file (default: /opt/kairix/service.env)
#   KAIRIX_SECRETS_FILE — pre-fetched secrets file (default: /run/secrets/kairix.env)

set -euo pipefail

KAIRIX_VENV="${KAIRIX_VENV:-/opt/kairix/.venv}"
SERVICE_ENV="${KAIRIX_SERVICE_ENV:-/opt/kairix/service.env}"
REAL_BIN="${KAIRIX_VENV}/bin/kairix"

# Step 1: Load service env (paths, KV name — non-secret config)
# MUST happen before SECRETS_FILE is resolved so that KAIRIX_SECRETS_FILE
# set in service.env takes effect (e.g. pointing to /opt/kairix/secrets.env
# on non-Docker deployments instead of the Docker-sidecar default).
if [[ -f "$SERVICE_ENV" ]]; then
    set -a
    # shellcheck source=/dev/null
    source "$SERVICE_ENV"
    set +a
fi

# Resolve secrets file AFTER service.env so operator can override via KAIRIX_SECRETS_FILE
SECRETS_FILE="${KAIRIX_SECRETS_FILE:-/run/secrets/kairix.env}"

# Step 2: Load pre-fetched secrets (vault-agent tmpfs or deploy-fetched)
# Same priority semantics as kairix.secrets.load_secrets(): existing env never overwritten.
if [[ -f "$SECRETS_FILE" ]]; then
    while IFS= read -r line || [[ -n "$line" ]]; do
        [[ -z "$line" || "$line" == \#* ]] && continue
        [[ "$line" != *=* ]] && continue
        key="${line%%=*}"
        value="${line#*=}"
        if [[ -z "${!key+x}" ]]; then
            export "$key"="$value"
        fi
    done < "$SECRETS_FILE"
fi

# Step 3: Verify binary exists
if [[ ! -x "$REAL_BIN" ]]; then
    echo "kairix-wrapper: binary not found at $REAL_BIN" >&2
    echo "  Install kairix: pip install git+https://github.com/quanyeomans/agentic-context-mesh" >&2
    echo "  Or set KAIRIX_VENV to the virtualenv that contains kairix." >&2
    exit 1
fi

exec "$REAL_BIN" "$@"

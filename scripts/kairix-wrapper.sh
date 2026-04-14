#!/usr/bin/env bash
# kairix-wrapper.sh — runtime environment loader for the kairix CLI
#
# DEPLOY: this script is installed at /opt/kairix/bin/kairix-wrapper.sh
# and the system-wide symlink /opt/openclaw/bin/kairix (or /usr/local/bin/kairix)
# points HERE, not to the raw Python binary.
#
# WHY THIS EXISTS:
# The raw Python binary at .venv/bin/kairix has no environment loaded. Agents
# calling `kairix search ...` from their exec context get:
#   - No AZURE_OPENAI_API_KEY → vector search fails (vec_failed=True, BM25 only)
#   - No SQLITE_VEC_PATH → sqlite-vec extension not found
#   - No KAIRIX_VAULT_ROOT / KAIRIX_DATA_DIR → wrong paths
#
# This wrapper loads environment in priority order:
#   1. Service env (/opt/kairix/service.env) — paths and non-secret config
#   2. Pre-fetched secrets (/run/secrets/kairix.env) — Docker vault-agent sidecar
#   3. Existing environment — always wins (never overwritten)
# Then execs the real binary with the loaded environment.
#
# USAGE: called transparently via the kairix symlink. No changes to callers needed.
#
# CONFIGURATION:
#   KAIRIX_INSTALL_DIR  — override install directory (default: /data/tools/qmd-azure-embed)
#   KAIRIX_SERVICE_ENV  — override service env path (default: /opt/kairix/service.env)
#   KAIRIX_SECRETS_FILE — override secrets file path (default: /run/secrets/kairix.env)

set -euo pipefail

KAIRIX_INSTALL_DIR="${KAIRIX_INSTALL_DIR:-/data/tools/qmd-azure-embed}"
SERVICE_ENV="${KAIRIX_SERVICE_ENV:-/opt/kairix/service.env}"
SECRETS_FILE="${KAIRIX_SECRETS_FILE:-/run/secrets/kairix.env}"
REAL_BIN="${KAIRIX_INSTALL_DIR}/.venv/bin/kairix"

# Step 1: Load service env (paths, KV name — non-secret config)
if [[ -f "$SERVICE_ENV" ]]; then
    # set -a exports every assignment; set +a stops it
    set -a
    # shellcheck source=/dev/null
    source "$SERVICE_ENV"
    set +a
fi

# Step 2: Load pre-fetched secrets if available (vault-agent tmpfs or deploy-fetched)
# load_secrets() semantics: existing env vars are NEVER overwritten
if [[ -f "$SECRETS_FILE" ]]; then
    while IFS= read -r line || [[ -n "$line" ]]; do
        # Skip blank lines and comments
        [[ -z "$line" || "$line" == \#* ]] && continue
        # Require KEY=VALUE format
        [[ "$line" != *=* ]] && continue
        key="${line%%=*}"
        value="${line#*=}"
        # Only set if not already in environment
        if [[ -z "${!key+x}" ]]; then
            export "$key"="$value"
        fi
    done < "$SECRETS_FILE"
fi

# Step 3: Verify real binary exists
if [[ ! -x "$REAL_BIN" ]]; then
    echo "kairix-wrapper: binary not found at $REAL_BIN" >&2
    echo "  Set KAIRIX_INSTALL_DIR to the kairix install directory." >&2
    echo "  Expected: \$KAIRIX_INSTALL_DIR/.venv/bin/kairix" >&2
    exit 1
fi

# Step 4: Exec — replace wrapper process with real binary (clean process table)
exec "$REAL_BIN" "$@"

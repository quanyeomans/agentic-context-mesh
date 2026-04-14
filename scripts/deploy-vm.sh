#!/usr/bin/env bash
# deploy-vm.sh — idempotent VM deployment script for kairix
#
# Installs kairix as a pip package (no source tree required) and sets up the
# operator environment. Run as the service user on the target VM.
#
# INSTALL MODEL:
#   kairix is installed via pip from the public GitHub repo into /opt/kairix/.venv
#   Operator configuration (service.env, private suites) lives separately in
#   /opt/kairix/ — not inside the kairix source repo.
#
# USAGE:
#   bash <(curl -fsSL https://raw.githubusercontent.com/quanyeomans/agentic-context-mesh/main/scripts/deploy-vm.sh)
#
#   Or if you have the script locally:
#   bash scripts/deploy-vm.sh [--version TAG] [--no-path-setup] [--skip-smoke]
#
# FLAGS:
#   --version TAG     Install specific git tag or branch (default: main)
#   --no-path-setup   Skip /etc/profile.d/ setup (if you manage PATH yourself)
#   --skip-smoke      Skip smoke test at end

set -euo pipefail

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

KAIRIX_OPT="${KAIRIX_OPT:-/opt/kairix}"
KAIRIX_VENV="${KAIRIX_VENV:-/opt/kairix/.venv}"
KAIRIX_SYMLINK="${KAIRIX_SYMLINK:-/usr/local/bin/kairix}"
KAIRIX_REPO="${KAIRIX_REPO:-https://github.com/quanyeomans/agentic-context-mesh}"
KAIRIX_VERSION="${KAIRIX_VERSION:-main}"

NO_PATH_SETUP=0
SKIP_SMOKE=0

for arg in "$@"; do
    case "$arg" in
        --version=*) KAIRIX_VERSION="${arg#--version=}" ;;
        --no-path-setup) NO_PATH_SETUP=1 ;;
        --skip-smoke)    SKIP_SMOKE=1 ;;
        *) echo "Unknown flag: $arg" >&2; exit 1 ;;
    esac
done

WRAPPER_DEST="${KAIRIX_OPT}/bin/kairix-wrapper.sh"
WRAPPER_SRC_URL="${KAIRIX_REPO}/raw/${KAIRIX_VERSION}/scripts/kairix-wrapper.sh"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

log()  { echo "[deploy] $*"; }
warn() { echo "[deploy] WARNING: $*" >&2; }
fail() { echo "[deploy] ERROR: $*" >&2; exit 1; }

require_cmd() { command -v "$1" >/dev/null 2>&1 || fail "Required command not found: $1"; }

# ---------------------------------------------------------------------------
# Step 1: Install kairix package
# ---------------------------------------------------------------------------

log "kairix deployment — version: ${KAIRIX_VERSION}"
log "Venv: ${KAIRIX_VENV}"

require_cmd python3

if [[ ! -d "$KAIRIX_VENV" ]]; then
    log "Step 1: creating virtualenv at ${KAIRIX_VENV}"
    mkdir -p "$(dirname "$KAIRIX_VENV")"
    python3 -m venv "$KAIRIX_VENV"
else
    log "Step 1: virtualenv exists at ${KAIRIX_VENV}"
fi

log "  Installing kairix from ${KAIRIX_REPO}@${KAIRIX_VERSION}"
"${KAIRIX_VENV}/bin/pip" install -q --upgrade \
    "git+${KAIRIX_REPO}@${KAIRIX_VERSION}"

INSTALLED_VERSION=$("${KAIRIX_VENV}/bin/kairix" --version 2>/dev/null || echo "unknown")
log "  Installed: ${INSTALLED_VERSION}"

# ---------------------------------------------------------------------------
# Step 2: Create /opt/kairix/ structure
# ---------------------------------------------------------------------------

log "Step 2: creating directory structure"
mkdir -p "${KAIRIX_OPT}/bin"
mkdir -p "${KAIRIX_OPT}/secrets"   # optional: pre-fetched secrets (non-Docker)
mkdir -p "${KAIRIX_OPT}/logs"

# Scaffold service.env if it doesn't exist
if [[ ! -f "${KAIRIX_OPT}/service.env" ]]; then
    log "  Creating service.env template at ${KAIRIX_OPT}/service.env"
    cat > "${KAIRIX_OPT}/service.env" << 'ENVTEMPLATE'
# kairix operator configuration — fill in your values
# This file is sourced by the kairix wrapper before every command.
# Never commit this file to version control.

# ── Azure Key Vault ───────────────────────────────────────────────────────────
# Key Vault name — kairix fetches Azure OpenAI secrets from here at runtime.
KAIRIX_KV_NAME=

# ── Vault paths ───────────────────────────────────────────────────────────────
KAIRIX_VAULT_ROOT=/data/obsidian-vault
KAIRIX_DATA_DIR=/data/kairix
KAIRIX_WORKSPACE_ROOT=/data/workspaces

# ── Neo4j ─────────────────────────────────────────────────────────────────────
KAIRIX_NEO4J_URI=bolt://localhost:7687
KAIRIX_NEO4J_USER=neo4j
# KAIRIX_NEO4J_PASSWORD=   # Set here or via vault-agent sidecar

# ── Optional ──────────────────────────────────────────────────────────────────
# SQLITE_VEC_PATH=/path/to/vec0.so   # Only needed if auto-discovery fails
# KAIRIX_LOG_QUERIES=1               # Log all queries to queries.jsonl
ENVTEMPLATE
    chmod 600 "${KAIRIX_OPT}/service.env"
    warn "service.env created at ${KAIRIX_OPT}/service.env — fill in your values before running kairix."
else
    log "  service.env already exists — skipping"
fi

# ---------------------------------------------------------------------------
# Step 3: Install wrapper script
# ---------------------------------------------------------------------------

log "Step 3: installing wrapper script"

if command -v curl >/dev/null 2>&1; then
    curl -fsSL "$WRAPPER_SRC_URL" -o "$WRAPPER_DEST"
elif command -v wget >/dev/null 2>&1; then
    wget -qO "$WRAPPER_DEST" "$WRAPPER_SRC_URL"
else
    fail "curl or wget required to download the wrapper script"
fi

chmod 755 "$WRAPPER_DEST"
log "  Wrapper installed at ${WRAPPER_DEST}"

# Stamp the venv path into a companion env so wrapper can find the binary
{
    echo "# Written by deploy-vm.sh on $(date -u +%Y-%m-%dT%H:%M:%SZ)"
    echo "KAIRIX_VENV=${KAIRIX_VENV}"
} > "${KAIRIX_OPT}/bin/kairix-install.env"

# ---------------------------------------------------------------------------
# Step 4: Create symlink
# ---------------------------------------------------------------------------

log "Step 4: creating symlink ${KAIRIX_SYMLINK} → ${WRAPPER_DEST}"

SYMLINK_DIR="$(dirname "$KAIRIX_SYMLINK")"
if [[ ! -d "$SYMLINK_DIR" ]]; then
    warn "Symlink target directory does not exist: ${SYMLINK_DIR}"
    warn "Run: sudo mkdir -p ${SYMLINK_DIR} && sudo ln -sf ${WRAPPER_DEST} ${KAIRIX_SYMLINK}"
else
    if [[ -w "$SYMLINK_DIR" ]]; then
        ln -sf "$WRAPPER_DEST" "$KAIRIX_SYMLINK"
        log "  Symlink created"
    else
        warn "${SYMLINK_DIR} is not writable. Run with sudo or set KAIRIX_SYMLINK to a writable location."
        warn "  sudo ln -sf ${WRAPPER_DEST} ${KAIRIX_SYMLINK}"
    fi
fi

# ---------------------------------------------------------------------------
# Step 5: Add to PATH
# ---------------------------------------------------------------------------

if [[ $NO_PATH_SETUP -eq 0 ]]; then
    log "Step 5: PATH setup"
    SYMLINK_DIR="$(dirname "$KAIRIX_SYMLINK")"
    PROFILE_D="/etc/profile.d/kairix.sh"

    if [[ -f "$PROFILE_D" ]] && grep -q "$SYMLINK_DIR" "$PROFILE_D" 2>/dev/null; then
        log "  PATH already configured in ${PROFILE_D}"
    elif [[ -w "/etc/profile.d" ]]; then
        printf 'export PATH="%s:$PATH"\n' "$SYMLINK_DIR" > "$PROFILE_D"
        chmod 644 "$PROFILE_D"
        log "  PATH configured in ${PROFILE_D}"
    else
        warn "Cannot write to /etc/profile.d/ — run with sudo, or add manually:"
        warn "  echo 'export PATH=\"${SYMLINK_DIR}:\$PATH\"' | sudo tee /etc/profile.d/kairix.sh"
    fi
else
    log "Step 5: skipping PATH setup (--no-path-setup)"
fi

# ---------------------------------------------------------------------------
# Step 6: Smoke test
# ---------------------------------------------------------------------------

if [[ $SKIP_SMOKE -eq 0 ]]; then
    log "Step 6: smoke test"

    KAIRIX_SERVICE_ENV="${KAIRIX_OPT}/service.env" \
    KAIRIX_VENV="${KAIRIX_VENV}" \
    "$WRAPPER_DEST" onboard check 2>&1 | tail -20 || warn "Some checks failed — run: kairix onboard check"
else
    log "Step 6: skipping smoke test (--skip-smoke)"
fi

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  kairix deployment complete"
echo ""
echo "  Binary:   ${KAIRIX_VENV}/bin/kairix"
echo "  Wrapper:  ${WRAPPER_DEST}"
echo "  Symlink:  ${KAIRIX_SYMLINK}"
echo "  Config:   ${KAIRIX_OPT}/service.env"
echo ""
echo "  Next steps:"
echo "  1. Fill in ${KAIRIX_OPT}/service.env (KAIRIX_KV_NAME, vault paths)"
echo "  2. kairix onboard check        ← verify full deployment"
echo "  3. kairix embed --limit 20     ← test embed"
echo "  4. kairix embed                ← full vault embed"
echo ""
echo "  See OPERATIONS.md for cron setup and monitoring."
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

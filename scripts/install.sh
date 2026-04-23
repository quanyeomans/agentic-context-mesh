#!/usr/bin/env bash
# install.sh — idempotent kairix installer
#
# Installs kairix as a pip package (no source tree required) and sets up the
# operator environment. Works on any Linux host: bare metal, VM, or WSL.
# Run as the service user that will own the kairix installation.
#
# INSTALL MODEL:
#   kairix is installed via pip from the public GitHub repo into /opt/kairix/.venv
#   Operator configuration (service.env, private suites) lives separately in
#   /opt/kairix/ — not inside the kairix source repo.
#
# USAGE:
#   bash <(curl -fsSL https://raw.githubusercontent.com/quanyeomans/agentic-context-mesh/main/scripts/install.sh)
#
#   Or if you have the script locally:
#   bash scripts/install.sh [--version TAG] [--no-path-setup] [--skip-smoke] [--fetch-secrets]
#
# FLAGS:
#   --version TAG       Install specific git tag or branch (default: installed version)
#   --no-path-setup     Skip /etc/profile.d/ setup (if you manage PATH yourself)
#   --skip-smoke        Skip smoke test at end
#   --fetch-secrets     Fetch Azure KV secrets into /opt/kairix/secrets.env

set -euo pipefail

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

KAIRIX_OPT="${KAIRIX_OPT:-/opt/kairix}"
KAIRIX_VENV="${KAIRIX_VENV:-/opt/kairix/.venv}"
KAIRIX_SYMLINK="${KAIRIX_SYMLINK:-/usr/local/bin/kairix}"
KAIRIX_REPO="${KAIRIX_REPO:-https://github.com/quanyeomans/agentic-context-mesh}"
KAIRIX_VERSION="${KAIRIX_VERSION:-}"

NO_PATH_SETUP=0
SKIP_SMOKE=0
FETCH_SECRETS=0

for arg in "$@"; do
    case "$arg" in
        --version=*)     KAIRIX_VERSION="${arg#--version=}" ;;
        --no-path-setup) NO_PATH_SETUP=1 ;;
        --skip-smoke)    SKIP_SMOKE=1 ;;
        --fetch-secrets) FETCH_SECRETS=1 ;;
        *) echo "Unknown flag: $arg" >&2; exit 1 ;;
    esac
done

# Default version: detect from installed package, fall back to main
if [[ -z "$KAIRIX_VERSION" ]]; then
    if [[ -d "$KAIRIX_VENV" ]] && "${KAIRIX_VENV}/bin/pip" show kairix >/dev/null 2>&1; then
        KAIRIX_VERSION=$("${KAIRIX_VENV}/bin/pip" show kairix 2>/dev/null | grep '^Version:' | awk '{print $2}')
        log "  Auto-detected installed version: ${KAIRIX_VERSION}"
    fi
    KAIRIX_VERSION="${KAIRIX_VERSION:-main}"
fi

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
# KAIRIX_DB_PATH=                    # Override kairix DB path (default: ~/.cache/kairix/index.sqlite)
# KAIRIX_LOG_QUERIES=1               # Log all queries to queries.jsonl
ENVTEMPLATE
    chmod 600 "${KAIRIX_OPT}/service.env"
    warn "service.env created at ${KAIRIX_OPT}/service.env — fill in your values before running kairix."
else
    log "  service.env already exists — skipping"
fi

# ---------------------------------------------------------------------------
# Step 2.5: Fetch Azure KV secrets (if requested)
# ---------------------------------------------------------------------------

if [[ $FETCH_SECRETS -eq 1 ]]; then
    log "Step 2.5: fetching Azure KV secrets"
    SECRETS_DEST="${KAIRIX_OPT}/secrets.env"

    # Source service.env to get KAIRIX_KV_NAME
    KV_NAME=""
    if [[ -f "${KAIRIX_OPT}/service.env" ]]; then
        KV_NAME=$(grep -E '^KAIRIX_KV_NAME=' "${KAIRIX_OPT}/service.env" 2>/dev/null | cut -d= -f2 | tr -d '[:space:]')
    fi

    if [[ -z "$KV_NAME" ]]; then
        warn "KAIRIX_KV_NAME not set in service.env — cannot fetch secrets"
    elif ! command -v az >/dev/null 2>&1; then
        warn "Azure CLI (az) not installed — cannot fetch secrets"
    else
        _fetch_kv_secret() {
            az keyvault secret show --vault-name "$KV_NAME" --name "$1" --query value -o tsv 2>/dev/null
        }

        SECRETS_TMP="${SECRETS_DEST}.tmp"
        {
            echo "# Fetched from Azure Key Vault '${KV_NAME}' on $(date -u +%Y-%m-%dT%H:%M:%SZ)"
            for kv_env in \
                "azure-openai-api-key:AZURE_OPENAI_API_KEY" \
                "azure-openai-endpoint:AZURE_OPENAI_ENDPOINT" \
                "kairix-neo4j-password:KAIRIX_NEO4J_PASSWORD"; do
                kv_name="${kv_env%%:*}"
                env_var="${kv_env#*:}"
                value=$(_fetch_kv_secret "$kv_name")
                if [[ -n "$value" ]]; then
                    echo "${env_var}=${value}"
                    log "  ✓ ${env_var}"
                else
                    warn "  ✗ ${kv_name} not found in vault"
                fi
            done
        } > "$SECRETS_TMP"

        mv "$SECRETS_TMP" "$SECRETS_DEST"
        chmod 600 "$SECRETS_DEST"
        log "  Secrets written to ${SECRETS_DEST} (mode 600)"
    fi
else
    log "Step 2.5: skipping secret fetch (use --fetch-secrets to enable)"
fi

# Harden permissions on any existing secrets files
for secrets_path in "${KAIRIX_OPT}/secrets.env" "/run/secrets/kairix.env"; do
    if [[ -f "$secrets_path" ]]; then
        current_perms=$(stat -c %a "$secrets_path" 2>/dev/null || stat -f %Lp "$secrets_path" 2>/dev/null)
        if [[ -n "$current_perms" ]] && [[ "$current_perms" != "600" ]] && [[ "$current_perms" != "640" ]]; then
            chmod 600 "$secrets_path"
            log "  Hardened permissions on ${secrets_path}: ${current_perms} → 600"
        fi
    fi
done

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
    echo "# Written by install.sh on $(date -u +%Y-%m-%dT%H:%M:%SZ)"
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
# Step 6: Install and enable kairix-mcp.service (systemd)
# ---------------------------------------------------------------------------

log "Step 6: installing kairix-mcp.service"

SYSTEMD_UNIT_DIR="/etc/systemd/system"
MCP_UNIT_DEST="${SYSTEMD_UNIT_DIR}/kairix-mcp.service"
MCP_UNIT_SRC_URL="${KAIRIX_REPO}/raw/${KAIRIX_VERSION}/scripts/kairix-mcp.service"

if command -v systemctl >/dev/null 2>&1 && [[ -d "$SYSTEMD_UNIT_DIR" ]]; then
    # Download the unit file
    if command -v curl >/dev/null 2>&1; then
        curl -fsSL "$MCP_UNIT_SRC_URL" -o /tmp/kairix-mcp.service.tmp
    elif command -v wget >/dev/null 2>&1; then
        wget -qO /tmp/kairix-mcp.service.tmp "$MCP_UNIT_SRC_URL"
    else
        warn "curl or wget required to download the MCP service unit file"
    fi

    if [[ -f /tmp/kairix-mcp.service.tmp ]]; then
        if [[ -w "$SYSTEMD_UNIT_DIR" ]]; then
            cp /tmp/kairix-mcp.service.tmp "$MCP_UNIT_DEST"
            chmod 644 "$MCP_UNIT_DEST"
            systemctl daemon-reload
            systemctl enable kairix-mcp.service
            systemctl restart kairix-mcp.service
            sleep 2
            if systemctl is-active --quiet kairix-mcp.service; then
                log "  kairix-mcp.service enabled and running"
            else
                warn "kairix-mcp.service did not start cleanly — check: journalctl -u kairix-mcp.service -n 20"
            fi
            rm -f /tmp/kairix-mcp.service.tmp

            # Register in OpenClaw tool manifest (if openclaw is present)
            REGISTER_SCRIPT="${KAIRIX_OPT}/cron/kairix-mcp-register.sh"
            if [[ ! -f "$REGISTER_SCRIPT" ]]; then
                REGISTER_SCRIPT_URL="${KAIRIX_REPO}/raw/${KAIRIX_VERSION}/scripts/cron/kairix-mcp-register.sh"
                mkdir -p "$(dirname "$REGISTER_SCRIPT")"
                curl -fsSL "$REGISTER_SCRIPT_URL" -o "$REGISTER_SCRIPT" 2>/dev/null \
                    || wget -qO "$REGISTER_SCRIPT" "$REGISTER_SCRIPT_URL" 2>/dev/null \
                    || true
                [[ -f "$REGISTER_SCRIPT" ]] && chmod 755 "$REGISTER_SCRIPT"
            fi
            if [[ -f "$REGISTER_SCRIPT" ]]; then
                bash "$REGISTER_SCRIPT" || warn "OpenClaw registration failed — may not be installed yet"
            fi
        else
            warn "${SYSTEMD_UNIT_DIR} is not writable — cannot install kairix-mcp.service"
            warn "  Run with sudo, or manually:"
            warn "    sudo cp /tmp/kairix-mcp.service.tmp /etc/systemd/system/kairix-mcp.service"
            warn "    sudo systemctl daemon-reload && sudo systemctl enable --now kairix-mcp.service"
        fi
    else
        warn "Failed to download kairix-mcp.service unit file"
    fi
else
    log "  systemd not available — skipping MCP service installation"
    log "  Start manually: kairix mcp serve --transport sse --port 7443 &"
fi

# ---------------------------------------------------------------------------
# Step 7: Smoke test
# ---------------------------------------------------------------------------

if [[ $SKIP_SMOKE -eq 0 ]]; then
    log "Step 7: smoke test"

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
echo "  3. kairix scan                 ← index vault into kairix DB"
echo "  4. kairix embed --limit 20     ← test embed (Azure OpenAI vectors)"
echo "  5. kairix embed                ← full vault embed"
echo ""
echo "  Secrets: re-run with --fetch-secrets to pull from Azure KV"
echo "  See docs/operations.md for cron setup and monitoring."
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

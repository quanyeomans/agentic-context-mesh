#!/usr/bin/env bash
# deploy-vm.sh — idempotent VM deployment script for kairix
#
# Run as the service user (e.g. openclaw) on the target VM.
# Safe to re-run: creates only what's missing, updates what's stale.
#
# WHAT THIS DOES:
#   1. Creates /opt/kairix/ directory structure
#   2. Installs kairix-wrapper.sh to /opt/kairix/bin/
#   3. Creates/updates the kairix symlink at KAIRIX_SYMLINK_PATH
#   4. Adds kairix to PATH via /etc/profile.d/kairix.sh (requires sudo)
#   5. Updates the git repo and pip-installs
#   6. Runs smoke tests (search + vec > 0 check)
#
# USAGE:
#   cd /data/tools/qmd-azure-embed
#   bash scripts/deploy-vm.sh [--no-pull] [--no-path-setup] [--skip-smoke]
#
# FLAGS:
#   --no-pull       Skip git pull (deploy current working tree as-is)
#   --no-path-setup Skip /etc/profile.d/ setup (if you manage PATH yourself)
#   --skip-smoke    Skip smoke test at end

set -euo pipefail

# ---------------------------------------------------------------------------
# Configuration — override via env vars before calling this script
# ---------------------------------------------------------------------------

INSTALL_DIR="${KAIRIX_INSTALL_DIR:-/data/tools/qmd-azure-embed}"
KAIRIX_OPT="${KAIRIX_OPT:-/opt/kairix}"
KAIRIX_SYMLINK_PATH="${KAIRIX_SYMLINK_PATH:-/opt/openclaw/bin/kairix}"
SERVICE_ENV="${KAIRIX_SERVICE_ENV:-/opt/kairix/service.env}"

NO_PULL=0
NO_PATH_SETUP=0
SKIP_SMOKE=0

for arg in "$@"; do
    case "$arg" in
        --no-pull)       NO_PULL=1 ;;
        --no-path-setup) NO_PATH_SETUP=1 ;;
        --skip-smoke)    SKIP_SMOKE=1 ;;
        *) echo "Unknown flag: $arg" >&2; exit 1 ;;
    esac
done

REAL_BIN="${INSTALL_DIR}/.venv/bin/kairix"
WRAPPER_SRC="${INSTALL_DIR}/scripts/kairix-wrapper.sh"
WRAPPER_DEST="${KAIRIX_OPT}/bin/kairix-wrapper.sh"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

log()  { echo "[deploy] $*"; }
warn() { echo "[deploy] WARNING: $*" >&2; }
fail() { echo "[deploy] ERROR: $*" >&2; exit 1; }

require_cmd() {
    command -v "$1" >/dev/null 2>&1 || fail "Required command not found: $1"
}

# ---------------------------------------------------------------------------
# Pre-flight
# ---------------------------------------------------------------------------

log "Starting kairix VM deployment"
log "Install dir: $INSTALL_DIR"
log "Opt dir:     $KAIRIX_OPT"
log "Symlink:     $KAIRIX_SYMLINK_PATH"

require_cmd python3
require_cmd git

[[ -d "$INSTALL_DIR" ]] || fail "Install directory not found: $INSTALL_DIR. Clone the repo first."
[[ -f "$INSTALL_DIR/pyproject.toml" ]] || fail "pyproject.toml not found — is $INSTALL_DIR the kairix repo?"

# ---------------------------------------------------------------------------
# Step 1: Update repo (unless --no-pull)
# ---------------------------------------------------------------------------

if [[ $NO_PULL -eq 0 ]]; then
    log "Step 1: git pull"
    git -C "$INSTALL_DIR" pull origin main
else
    log "Step 1: skipping git pull (--no-pull)"
fi

# ---------------------------------------------------------------------------
# Step 2: Install Python dependencies
# ---------------------------------------------------------------------------

log "Step 2: pip install"

VENV="${INSTALL_DIR}/.venv"
if [[ ! -d "$VENV" ]]; then
    log "  Creating virtualenv at $VENV"
    python3 -m venv "$VENV"
fi

"$VENV/bin/pip" install -q -e "$INSTALL_DIR"
log "  pip install complete"

# ---------------------------------------------------------------------------
# Step 3: Create /opt/kairix/ structure
# ---------------------------------------------------------------------------

log "Step 3: creating /opt/kairix/ directories"
mkdir -p "${KAIRIX_OPT}/bin"
mkdir -p "${KAIRIX_OPT}/secrets"  # only used in non-Docker (fallback fetch)

# ---------------------------------------------------------------------------
# Step 4: Install wrapper script
# ---------------------------------------------------------------------------

log "Step 4: installing wrapper script"

[[ -f "$WRAPPER_SRC" ]] || fail "Wrapper script not found: $WRAPPER_SRC"
cp "$WRAPPER_SRC" "$WRAPPER_DEST"
chmod 755 "$WRAPPER_DEST"

# Stamp the install dir into the wrapper's env so it doesn't rely on default
# The wrapper reads KAIRIX_INSTALL_DIR at runtime; writing a companion env file
# ensures it works even if the variable isn't in the caller's environment.
{
    echo "# Written by deploy-vm.sh on $(date -u +%Y-%m-%dT%H:%M:%SZ)"
    echo "KAIRIX_INSTALL_DIR=${INSTALL_DIR}"
    echo "KAIRIX_SERVICE_ENV=${SERVICE_ENV}"
} > "${KAIRIX_OPT}/bin/kairix-install.env"

log "  Wrapper installed at $WRAPPER_DEST"

# ---------------------------------------------------------------------------
# Step 5: Create/update symlink
# ---------------------------------------------------------------------------

log "Step 5: creating symlink $KAIRIX_SYMLINK_PATH → $WRAPPER_DEST"

SYMLINK_DIR="$(dirname "$KAIRIX_SYMLINK_PATH")"
if [[ ! -d "$SYMLINK_DIR" ]]; then
    warn "Symlink target directory does not exist: $SYMLINK_DIR"
    warn "Create it with: sudo mkdir -p $SYMLINK_DIR"
    warn "Then re-run this script."
else
    # ln -sf: create or overwrite symlink
    ln -sf "$WRAPPER_DEST" "$KAIRIX_SYMLINK_PATH"
    log "  Symlink created: $KAIRIX_SYMLINK_PATH → $WRAPPER_DEST"
fi

# Verify the symlink is now the wrapper, not the raw Python binary
if [[ -L "$KAIRIX_SYMLINK_PATH" ]]; then
    target="$(readlink "$KAIRIX_SYMLINK_PATH")"
    if [[ "$target" == *".venv/bin/kairix" ]]; then
        warn "Symlink still points to raw Python binary: $target"
        warn "This means the symlink update failed. Check permissions on $SYMLINK_DIR."
    else
        log "  Symlink verified: points to wrapper"
    fi
fi

# ---------------------------------------------------------------------------
# Step 6: Add kairix symlink dir to PATH for all sessions
# ---------------------------------------------------------------------------

if [[ $NO_PATH_SETUP -eq 0 ]]; then
    log "Step 6: setting up PATH"
    PROFILE_D="/etc/profile.d/kairix.sh"

    SYMLINK_DIR_ESCAPED="${SYMLINK_DIR//\//\\/}"
    if [[ -f "$PROFILE_D" ]] && grep -q "$SYMLINK_DIR" "$PROFILE_D" 2>/dev/null; then
        log "  PATH already configured in $PROFILE_D"
    else
        if [[ -w "/etc/profile.d" ]]; then
            cat > "$PROFILE_D" << PROFILE
# kairix — added by deploy-vm.sh
# Puts the kairix wrapper on PATH for all login shells and agent exec contexts.
export PATH="${SYMLINK_DIR}:\$PATH"
PROFILE
            chmod 644 "$PROFILE_D"
            log "  PATH configured in $PROFILE_D"
            log "  New shells will have kairix on PATH automatically."
            log "  Current shell: run 'export PATH=${SYMLINK_DIR}:\$PATH' or start a new session."
        else
            warn "Cannot write to /etc/profile.d/ — need sudo."
            warn "Run manually: sudo bash -c 'echo \"export PATH=${SYMLINK_DIR}:\\\$PATH\" > /etc/profile.d/kairix.sh'"
        fi
    fi
else
    log "Step 6: skipping PATH setup (--no-path-setup)"
fi

# ---------------------------------------------------------------------------
# Step 7: Run tests
# ---------------------------------------------------------------------------

log "Step 7: running unit tests"
if "$VENV/bin/pytest" "$INSTALL_DIR/tests/" -q --tb=short -m "not integration and not e2e" 2>&1 | tail -5; then
    log "  Tests passed"
else
    warn "Unit tests failed — check output above before proceeding"
fi

# ---------------------------------------------------------------------------
# Step 8: Smoke test (requires service.env to be populated)
# ---------------------------------------------------------------------------

if [[ $SKIP_SMOKE -eq 0 ]]; then
    log "Step 8: running smoke tests"

    if [[ ! -f "$SERVICE_ENV" ]]; then
        warn "Service env not found at $SERVICE_ENV — skipping smoke test."
        warn "Create it from env.example and re-run, or use --skip-smoke."
    else
        # shellcheck source=/dev/null
        set -a; source "$SERVICE_ENV"; set +a

        # Quick onboard check
        if "$KAIRIX_SYMLINK_PATH" onboard check --json 2>/dev/null | python3 -c "
import sys, json
data = json.load(sys.stdin)
failed = [c for c in data.get('checks', []) if not c['ok']]
if failed:
    print('CHECKS FAILED:')
    for f in failed: print(f'  ✗ {f[\"name\"]}: {f[\"detail\"]}')
    sys.exit(1)
else:
    print('All checks passed')
"; then
            log "  Smoke tests passed"
        else
            warn "Some deployment checks failed — see 'kairix onboard check' for details"
        fi
    fi
else
    log "Step 8: skipping smoke test (--skip-smoke)"
fi

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  kairix deployment complete"
echo ""
echo "  Binary:   $REAL_BIN"
echo "  Wrapper:  $WRAPPER_DEST"
echo "  Symlink:  $KAIRIX_SYMLINK_PATH"
echo ""
echo "  Next steps:"
if [[ ! -f "$SERVICE_ENV" ]]; then
    echo "  1. cp ${INSTALL_DIR}/env.example ${SERVICE_ENV}"
    echo "     nano ${SERVICE_ENV}   ← fill in KAIRIX_KV_NAME, vault paths"
fi
echo "  2. kairix onboard check        ← verify full deployment"
echo "  3. kairix search 'test' --agent builder --json"
echo "     Verify: vec_count > 0 in output"
echo ""
echo "  See OPERATIONS.md for cron setup and monitoring."
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

#!/usr/bin/env bash
# qmd-reindex.sh — QMD index update (every 6h) + weekly cleanup (Sundays)
#
# Pass --cleanup to run the weekly cleanup sequence (prune + update).
# Default (no args): incremental update only.
#
# Sequence:
#   1. High-churn detection — if vault changed ≥200 files, drops embed chunk limit
#   2. qmd update — rebuild BM25/FTS index (also runs GGUF embed → float[768])
#   3. kairix embed — rebuild Azure vectors (float[1536]), overriding GGUF
#      qmd update resets vectors_vec to float[768]; kairix restores float[1536].
#   4. Gold suite staleness guard — warns if >10% of curated gold paths missing
#
# Exit codes:
#   0 — success (indexed or already up to date)
#   1 — qmd command failed
#   2 — internal error (qmd binary missing, etc.)
#
# Logs: ${KAIRIX_DATA_DIR:-/var/lib/kairix}/logs/qmd-reindex.log
# Status: ${KAIRIX_DATA_DIR:-/var/lib/kairix}/logs/qmd-reindex-status.json

set -euo pipefail

# ── Environment ───────────────────────────────────────────────────────────────
[ -f /opt/kairix/service.env ] && set -a && source /opt/kairix/service.env && set +a

LOG_FILE="${LOG_FILE:-${KAIRIX_DATA_DIR:-/var/lib/kairix}/logs/qmd-reindex.log}"
STATUS_FILE="${STATUS_FILE:-${KAIRIX_DATA_DIR:-/var/lib/kairix}/logs/qmd-reindex-status.json}"
QMD_BIN="${QMD_BIN:-/usr/local/bin/qmd}"
KAIRIX="${KAIRIX:-/usr/local/bin/kairix}"
EMBED_LOG="${EMBED_LOG:-${KAIRIX_DATA_DIR:-/var/lib/kairix}/logs/azure-embed.log}"
MAX_EMBED_CHUNKS="${MAX_EMBED_CHUNKS:-500}"
HIGH_CHURN_THRESHOLD="${HIGH_CHURN_THRESHOLD:-200}"   # vault files changed → force full embed
GOLD_SUITE="${GOLD_SUITE:-suites/example.yaml}"
GOLD_STALE_WARN_PCT="${GOLD_STALE_WARN_PCT:-10}"       # % of gold paths missing → warn
QMD_DB="${QMD_DB:-${QMD_CACHE_DIR:-${HOME}/.cache/qmd}/index.sqlite}"
VAULT_ROOT="${VAULT_ROOT:-${KAIRIX_VAULT_ROOT:-}}"
DRY_RUN="${DRY_RUN:-false}"
CLEANUP_MODE=false

if [[ "${1:-}" == "--cleanup" ]]; then
  CLEANUP_MODE=true
fi

# ── Logging ───────────────────────────────────────────────────────────────────
timestamp() { date -u '+%Y-%m-%dT%H:%M:%SZ'; }
log()         { printf '[%s] [%s] %s\n' "$(timestamp)" "$1" "$2" | tee -a "$LOG_FILE"; }
log_info()    { log "INFO " "$1"; }
log_warn()    { log "WARN " "$1"; }
log_error()   { log "ERROR" "$1"; }
log_success() { log "OK   " "$1"; }

write_status() {
  local status="$1" new="${2:-0}" updated="${3:-0}" removed="${4:-0}" message="${5:-}"
  printf '{"status":"%s","new":%s,"updated":%s,"removed":%s,"message":"%s","ts":"%s"}\n' \
    "$status" "$new" "$updated" "$removed" "$message" "$(timestamp)" \
    > "$STATUS_FILE" 2>/dev/null || true
}

# ── Preflight ─────────────────────────────────────────────────────────────────
log_info "qmd-reindex: starting (cleanup=${CLEANUP_MODE})"

if [[ ! -x "$QMD_BIN" ]]; then
  log_error "QMD binary not found or not executable: $QMD_BIN"
  write_status "error" 0 0 0 "QMD binary missing"
  exit 2
fi

# Prepend tools bin to PATH (preserves any test overrides already set)
_qmd_dir=$(dirname "$QMD_BIN")
export PATH="${_qmd_dir}:${PATH}"

if [[ "$DRY_RUN" == "true" ]]; then
  log_info "DRY RUN mode — would run: qmd $([ "$CLEANUP_MODE" == "true" ] && echo 'cleanup + update' || echo 'update') + kairix embed"
  write_status "dry-run" 0 0 0 "Dry run completed"
  log_success "qmd-reindex: dry run complete"
  exit 0
fi

# ── Cleanup mode (weekly) ─────────────────────────────────────────────────────
if [[ "$CLEANUP_MODE" == "true" ]]; then
  log_info "Running weekly cleanup..."

  # Before stats
  BEFORE=$(qmd status 2>&1 | grep -E 'Total|Vectors|Size' | tr '\n' ' ' || true)
  log_info "Before: ${BEFORE}"

  # Cleanup
  qmd cleanup 2>&1 | while IFS= read -r line; do log_info "cleanup: $line"; done || {
    log_error "qmd cleanup failed"
    write_status "error" 0 0 0 "qmd cleanup failed"
    exit 1
  }
fi

# ── Kairix integration conflict detection ─────────────────────────────────────
# Integration tools (e.g. OpenClaw) may update cron scripts or reset symlinks.
# Check on every run so regressions are caught within 6h of any platform update.

# Check 1: 'qmd embed' in any active (uncommented) cron script.
# If present, it resets vectors_vec to float[768] every run — breaking kairix.

# Scan probable integration cron directories for conflicting 'qmd embed' calls.
# KAIRIX_INTEGRATION_CRON_DIRS can be overridden in service.env for non-standard installs.
KAIRIX_INTEGRATION_CRON_DIRS="${KAIRIX_INTEGRATION_CRON_DIRS:-\
  /opt/openclaw/cron \
  /usr/local/openclaw/cron \
  /opt/homebrew/opt/openclaw/cron \
  /usr/local/share/openclaw/cron \
  ${HOME}/.openclaw/cron}"

GGUF_OFFENDERS=""
for _dir in $KAIRIX_INTEGRATION_CRON_DIRS; do
  [[ ! -d "$_dir" ]] && continue
  _found=$(grep -rl 'qmd embed' "$_dir" 2>/dev/null | while IFS= read -r f; do
    grep -v '^\s*#' "$f" 2>/dev/null | grep -qP "(?<!['\"])qmd\s+embed" && echo "$f" || true
  done || true)
  [[ -n "$_found" ]] && GGUF_OFFENDERS="${GGUF_OFFENDERS}${_found}"$'\n'
done
GGUF_OFFENDERS="${GGUF_OFFENDERS%$'\n'}"

if [[ -n "$GGUF_OFFENDERS" ]]; then
  log_warn "CONFLICT: uncommented 'qmd embed' found in: $(echo "$GGUF_OFFENDERS" | tr '\n' ' ')"
  log_warn "This resets vectors_vec to float[768] on every run, breaking kairix vector search."
  log_warn "Fix: remove the 'qmd embed' call from the listed script(s)."
  log_warn "See: docs/runbooks/how-to-onboard-new-installation.md (Step 3)"
fi

# Check 2: Integration symlink integrity — warn if any kairix symlink points to wrong target.
# If your integration tool adds a second kairix symlink, add the check here.
# Example for a tool that installs at /opt/<tool>/bin/kairix:
#   _TOOL_LINK="/opt/<tool>/bin/kairix"
#   _ACTUAL=$(readlink "$_TOOL_LINK" 2>/dev/null || echo "missing")
#   if [[ "$_ACTUAL" != "/opt/kairix/bin/kairix-wrapper.sh" ]]; then
#     log_warn "Integration symlink $_TOOL_LINK → '$_ACTUAL' (expected: /opt/kairix/bin/kairix-wrapper.sh)"
#   fi

# ── High-churn detection ──────────────────────────────────────────────────────
# If the vault snapshot changed many files (reorganization, not just edits),
# remove the embed chunk limit so all new/changed vectors are refreshed.
VAULT_CHURN=0
if [[ -n "$VAULT_ROOT" ]] && git -C "$VAULT_ROOT" rev-parse --git-dir &>/dev/null; then
  VAULT_CHURN=$(git -C "$VAULT_ROOT" diff --stat HEAD~1 HEAD 2>/dev/null \
    | tail -1 | grep -oP '^\s*\K\d+(?= file)' || echo 0)
fi
if [[ "$VAULT_CHURN" -ge "$HIGH_CHURN_THRESHOLD" ]]; then
  log_warn "High vault churn detected (${VAULT_CHURN} files changed) — forcing full embed (removing chunk limit)"
  MAX_EMBED_CHUNKS=""
fi

# ── Index update ──────────────────────────────────────────────────────────────
log_info "Running qmd update..."

UPDATE_OUTPUT=$(qmd update 2>&1 || echo "QMD_UPDATE_FAILED")
echo "$UPDATE_OUTPUT" | while IFS= read -r line; do log_info "update: $line"; done

if echo "$UPDATE_OUTPUT" | grep -q "QMD_UPDATE_FAILED"; then
  log_error "qmd update failed"
  write_status "error" 0 0 0 "qmd update failed"
  exit 1
fi

# Parse stats from output
NEW=$(echo "$UPDATE_OUTPUT"     | grep -oP '\d+(?= new)'     | tail -1 || echo "0")
UPDATED=$(echo "$UPDATE_OUTPUT" | grep -oP '\d+(?= updated)' | tail -1 || echo "0")
REMOVED=$(echo "$UPDATE_OUTPUT" | grep -oP '\d+(?= removed)' | tail -1 || echo "0")

# After stats (cleanup mode only)
if [[ "$CLEANUP_MODE" == "true" ]]; then
  AFTER=$(qmd status 2>&1 | grep -E 'Total|Vectors|Size' | tr '\n' ' ' || true)
  log_info "After:  ${AFTER}"
fi

MSG="${NEW} new, ${UPDATED} updated, ${REMOVED} removed"
write_status "ok" "${NEW:-0}" "${UPDATED:-0}" "${REMOVED:-0}" "$MSG"
log_success "qmd-reindex: qmd update complete — ${MSG}"

# ── Azure vector refresh ───────────────────────────────────────────────────────
# qmd update runs the GGUF embedder (float[768]), overwriting Azure vectors.
# Re-run kairix embed to restore float[1536] schema and Azure embeddings.
# Credentials come from /run/secrets/kairix.env (populated at boot by kairix-fetch-secrets.service).
log_info "Restoring Azure vectors after qmd update (cost guard: max ${MAX_EMBED_CHUNKS} chunks)..."

KAIRIX_SECRETS_FILE="${KAIRIX_SECRETS_FILE:-/run/secrets/kairix.env}"
if [ ! -f "$KAIRIX_SECRETS_FILE" ]; then
  log_warn "Secrets file not found: $KAIRIX_SECRETS_FILE — skipping vector refresh (will retry on next run)"
  log_warn "To restore secrets: sudo systemctl start kairix-fetch-secrets.service"
else
  # shellcheck disable=SC1090
  set -a && source "$KAIRIX_SECRETS_FILE" && set +a
  EMBED_LIMIT_FLAG=""
  [[ -n "$MAX_EMBED_CHUNKS" ]] && EMBED_LIMIT_FLAG="--limit $MAX_EMBED_CHUNKS"
  # shellcheck disable=SC2086
  EMBED_OUTPUT=$("$KAIRIX" embed $EMBED_LIMIT_FLAG 2>&1 | tee -a "$EMBED_LOG") || true
  EMBED_COUNT=$(echo "$EMBED_OUTPUT" | grep -oP 'embedded=\K[0-9]+' | tail -1 || echo 0)
  EMBED_FAILED=$(echo "$EMBED_OUTPUT" | grep -oP 'failed=\K[0-9]+' | tail -1 || echo 0)
  log_success "Azure vector refresh complete — embedded=${EMBED_COUNT} failed=${EMBED_FAILED}"
fi

# ── Gold suite staleness guard ────────────────────────────────────────────────
# Check what % of curated gold paths no longer exist in the QMD index.
# Warns if > GOLD_STALE_WARN_PCT% are missing — indicates vault reorganization
# has broken the benchmark suite and it needs rebuilding.
if [[ -f "$GOLD_SUITE" && -f "$QMD_DB" ]] && command -v python3 &>/dev/null; then
  STALE_RESULT=$(python3 - <<'PYEOF'
import sqlite3, yaml, sys, os

suite_path = os.environ.get('GOLD_SUITE', '')
db_path = os.environ.get('QMD_DB', '')
warn_pct = int(os.environ.get('GOLD_STALE_WARN_PCT', '10'))

try:
    with open(suite_path) as f:
        suite = yaml.safe_load(f)
    db = sqlite3.connect(db_path)
    indexed = {row[0].lower() for row in db.execute("SELECT path FROM documents WHERE active=1")}
    db.close()

    total, missing = 0, 0
    for case in suite.get('cases', []):
        for gp in case.get('gold_paths', []):
            p = gp['path'].lower()
            total += 1
            if p not in indexed:
                missing += 1

    if total == 0:
        print("SKIP:no gold paths found")
        sys.exit(0)
    pct = int(missing * 100 / total)
    if pct >= warn_pct:
        print(f"WARN:{missing}/{total} gold paths missing ({pct}%) — suite may need rebuild")
    else:
        print(f"OK:{missing}/{total} gold paths missing ({pct}%)")
except Exception as e:
    print(f"SKIP:{e}")
PYEOF
  )
  GOLD_STATUS="${STALE_RESULT%%:*}"
  GOLD_MSG="${STALE_RESULT#*:}"
  if [[ "$GOLD_STATUS" == "WARN" ]]; then
    log_warn "Gold suite staleness: ${GOLD_MSG}"
  elif [[ "$GOLD_STATUS" == "OK" ]]; then
    log_info "Gold suite staleness: ${GOLD_MSG}"
  fi
fi

log_success "qmd-reindex: complete"
exit 0

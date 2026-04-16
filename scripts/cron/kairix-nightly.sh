#!/usr/bin/env bash
# kairix-nightly.sh — Nightly kairix maintenance sequence
#
# Deployed to: /opt/kairix/cron/kairix-nightly.sh
# Schedule: 02:00–03:00 local daily (adjust for your timezone)
#
# Sequence:
#   0. Azure embed — incremental (cost guard: max 500 chunks)
#   1. Recall-check quality gate (≥4/5 = silent; <4/5 = alert)
#   2. Wikilinks inject --changed
#   3. Log summary
#
# Credentials: sourced from /run/secrets/kairix.env (populated by kairix-fetch-secrets.service)
# Behaviour: silent if green (NO_REPLY), prints alert summary if action is required.

set -euo pipefail

# Load non-secret config
[ -f /opt/kairix/service.env ] && set -a && source /opt/kairix/service.env && set +a

# Load secrets from tmpfs
KAIRIX_SECRETS_FILE="${KAIRIX_SECRETS_FILE:-/run/secrets/kairix.env}"
if [ -f "$KAIRIX_SECRETS_FILE" ]; then
    set -a && source "$KAIRIX_SECRETS_FILE" && set +a
else
    echo "WARNING: secrets file not found at $KAIRIX_SECRETS_FILE" >&2
fi

KAIRIX="${KAIRIX:-/usr/local/bin/kairix}"
LOG_DIR="${LOG_DIR:-${KAIRIX_DATA_DIR:-/var/lib/kairix}/logs}"
NIGHTLY_LOG="${LOG_DIR}/nightly-maintenance.jsonl"
EMBED_LOG="${LOG_DIR}/azure-embed.log"
MAX_EMBED_CHUNKS=500

mkdir -p "$LOG_DIR"

START=$(date -u +%Y-%m-%dT%H:%M:%SZ)
EMBED_COUNT=0
EMBED_FAILED=0
RECALL_RESULT="skipped/5"
RECALL_PASS=false
LINKS_COUNT=0
ALERT=false
ALERT_REASON=""

# ---------------------------------------------------------------------------
# Step 0a: Kairix integration health checks
# Detects failure modes that integration tool version updates can reintroduce:
#   - GGUF/QMD embedding conflict (qmd embed resetting vectors to 768-dim)
#   - Mixed embedding models in content_vectors (symptom of active conflict)
# Runs nightly as a safety net; qmd-reindex.sh checks every 6h as well.
# ---------------------------------------------------------------------------
QMD_DB_PATH="${QMD_DB_PATH:-${HOME}/.cache/qmd/index.sqlite}"

# Check: mixed embedding models — means GGUF has been writing to vectors_vec
if [[ -f "$QMD_DB_PATH" ]]; then
    MODEL_COUNT=$(sqlite3 "$QMD_DB_PATH" \
      'SELECT COUNT(DISTINCT model) FROM content_vectors;' 2>/dev/null || echo 0)
    if [[ "$MODEL_COUNT" -gt 1 ]]; then
        MIXED_MODELS=$(sqlite3 "$QMD_DB_PATH" \
          'SELECT model || ":" || COUNT(*) FROM content_vectors GROUP BY model;' \
          2>/dev/null | tr '\n' ' ' || echo "unknown")
        ALERT=true
        ALERT_REASON="${ALERT_REASON:+$ALERT_REASON; }Mixed embedding models detected ($MIXED_MODELS) — GGUF conflict likely active. Fix: check for uncommented 'qmd embed' in integration cron directories, then run: kairix embed --force"
    fi
fi

# Check: uncommented 'qmd embed' in any active integration cron script
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
    ALERT=true
    ALERT_REASON="${ALERT_REASON:+$ALERT_REASON; }GGUF conflict: uncommented 'qmd embed' in $(echo "$GGUF_OFFENDERS" | tr '\n' ' ')— remove this call (resets vectors_vec to float[768])"
fi

# ---------------------------------------------------------------------------
# Step 0: Azure embed (incremental, cost-guarded)
# ---------------------------------------------------------------------------
if [[ -z "${AZURE_OPENAI_API_KEY:-}" || -z "${AZURE_OPENAI_ENDPOINT:-}" ]]; then
    ALERT=true
    ALERT_REASON="Azure OpenAI credentials not found in secrets file — embed and recall-check skipped"
else
    EMBED_OUTPUT=$("$KAIRIX" embed --limit "$MAX_EMBED_CHUNKS" 2>&1 | tee -a "$EMBED_LOG") || true
    EMBED_COUNT=$(echo "$EMBED_OUTPUT" | grep -oP 'embedded=\K[0-9]+' | tail -1 || echo 0)
    EMBED_FAILED=$(echo "$EMBED_OUTPUT" | grep -oP 'failed=\K[0-9]+' | tail -1 || echo 0)
    RECALL_RESULT=$(echo "$EMBED_OUTPUT" | grep -oP 'Recall: \K[0-9]+/[0-9]+' | tail -1 || echo "0/5")
    RECALL_SCORE=$(echo "$RECALL_RESULT" | cut -d/ -f1)
    RECALL_TOTAL=$(echo "$RECALL_RESULT" | cut -d/ -f2)
    if [[ "$RECALL_SCORE" -ge 4 ]]; then
        RECALL_PASS=true
    else
        ALERT=true
        ALERT_REASON="${ALERT_REASON:+$ALERT_REASON; }Recall gate FAILED: $RECALL_RESULT (threshold: 4/${RECALL_TOTAL})"
    fi
    if [[ "$EMBED_FAILED" -gt 0 ]]; then
        ALERT=true
        ALERT_REASON="${ALERT_REASON:+$ALERT_REASON; }Embed had $EMBED_FAILED failures — check $EMBED_LOG"
    fi
fi

# ---------------------------------------------------------------------------
# Step 2: Wikilinks inject --changed
# ---------------------------------------------------------------------------
LINKS_OUTPUT=$("$KAIRIX" wikilinks inject --changed 2>&1 || true)
LINKS_COUNT=$(echo "$LINKS_OUTPUT" | grep -oP 'injected=\K[0-9]+' | tail -1 || echo 0)

# ---------------------------------------------------------------------------
# Step 3: Log summary
# ---------------------------------------------------------------------------
END=$(date -u +%Y-%m-%dT%H:%M:%SZ)
LOG_ENTRY="{\"timestamp\":\"$END\",\"start\":\"$START\",\"embed\":{\"count\":$EMBED_COUNT,\"failed\":$EMBED_FAILED},\"recall\":{\"score\":\"$RECALL_RESULT\",\"pass\":$RECALL_PASS},\"wikilinks\":{\"injected\":$LINKS_COUNT},\"alert\":$ALERT,\"alert_reason\":\"$ALERT_REASON\"}"
echo "$LOG_ENTRY" >> "$NIGHTLY_LOG"

# ---------------------------------------------------------------------------
# Output: silent if green, alert if red
# ---------------------------------------------------------------------------
if [[ "$ALERT" == "true" ]]; then
    cat << REPLY
Nightly kairix maintenance requires attention.

Issue: $ALERT_REASON

Stats: embed=$EMBED_COUNT links=$LINKS_COUNT recall=$RECALL_RESULT

Check $EMBED_LOG and $NIGHTLY_LOG for details.
REPLY
else
    echo "NO_REPLY"
fi

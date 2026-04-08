#!/usr/bin/env bash
# run-regression.sh — Full regression pipeline for entity/benchmark changes
#
# Usage:
#   bash scripts/run-regression.sh [RUN_LABEL]
#   bash scripts/run-regression.sh entity-cleanup-R3
#
# Runs 5 stages in sequence. Each stage halts the pipeline on failure.
# Designed for post-entity-change validation (vault cleanup, DB prune, re-seed).
#
# Stages:
#   R-0: Pre-flight validation (entity count, embed index, DB sanity)
#   R-1: Reindex + embed (qmd update + mnemosyne embed --changed)
#   R-2: Entity mention update (mnemosyne entity extract --changed)
#   R-3: Gold path validation (benchmark-path-audit.py)
#   R-4: Gold rebuild (build-eval-gold.py --curated --skip-mined)
#   R-5: Benchmark (run-benchmark-v2.py)
#
# Environment variables:
#   MNEMOSYNE_VAULT_ROOT   — path to vault root (required)
#   MNEMOSYNE_ENTITIES_DB  — path to entities.db
#   MNEMOSYNE_RESULTS_DIR  — path for benchmark JSON output
#   QMD_DB_PATH            — path to QMD index.sqlite
#   MNEMOSYNE_REPO         — path to mnemosyne repo (default: /opt/mnemosyne)
#
set -euo pipefail

RUN_LABEL="${1:-regression-$(date +%Y%m%d)}"
VAULT_ROOT="${MNEMOSYNE_VAULT_ROOT:?MNEMOSYNE_VAULT_ROOT must be set}"
REPO="${MNEMOSYNE_REPO:-/opt/mnemosyne}"
VENV="$REPO/.venv/bin"
LOG_DIR="${MNEMOSYNE_LOG_DIR:-/tmp/mnemosyne-logs}"
LOG="$LOG_DIR/regression-${RUN_LABEL}.log"
ENTITY_DIR="$VAULT_ROOT/${MNEMOSYNE_ENTITY_SUBDIR:-agent-knowledge/entities}"
CURATED_YAML="${MNEMOSYNE_CURATED_YAML:-$REPO/suites/curated.yaml}"

mkdir -p "$LOG_DIR"

# Tee all output to the log file
exec > >(tee -a "$LOG") 2>&1

echo "========================================================"
echo "Regression pipeline: $RUN_LABEL"
echo "Started: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "Vault: $VAULT_ROOT"
echo "Repo:  $REPO"
echo "Log:   $LOG"
echo "========================================================"

# ── Stage R-0: Pre-flight ─────────────────────────────────────────────────────
echo ""
echo "=== Stage R-0: Pre-flight ==="

entity_count=$(find "$ENTITY_DIR" -name "*.md" 2>/dev/null | wc -l | tr -d ' ')
if [ "$entity_count" -lt 5 ]; then
  echo "ERROR: Fewer than 5 entity stubs in $ENTITY_DIR (found: $entity_count)" >&2
  exit 1
fi
echo "✓ Entity stubs: $entity_count"

entities_active=$(sqlite3 "${MNEMOSYNE_ENTITIES_DB:-/path/to/entities.db}" \
  "SELECT count(*) FROM entities WHERE status='active'" 2>/dev/null || echo "0")
if [ "$entities_active" -lt 1 ]; then
  echo "ERROR: No active entities in entities.db" >&2
  exit 1
fi
echo "✓ Active entities: $entities_active"

chunk_count=$(sqlite3 "${QMD_DB_PATH:-$HOME/.cache/qmd/index.sqlite}" \
  "SELECT count(*) FROM chunks" 2>/dev/null || echo "0")
if [ "$chunk_count" -lt 10000 ]; then
  echo "ERROR: Embed index has only $chunk_count chunks (expected >= 10000)" >&2
  exit 1
fi
echo "✓ Embed chunks: $chunk_count"

# ── Stage R-1: Reindex + embed ────────────────────────────────────────────────
echo ""
echo "=== Stage R-1: Reindex + embed ==="
qmd update 2>&1 | tail -5
"$VENV/mnemosyne" embed --changed 2>&1 | tail -10

new_chunk_count=$(sqlite3 "${QMD_DB_PATH:-$HOME/.cache/qmd/index.sqlite}" \
  "SELECT count(*) FROM chunks" 2>/dev/null || echo "0")
if [ "$new_chunk_count" -lt "$chunk_count" ]; then
  echo "WARN: Chunk count decreased ($chunk_count → $new_chunk_count). Check embed log."
fi
echo "✓ Embed complete. Chunks: $chunk_count → $new_chunk_count"

# ── Stage R-2: Entity mention update ─────────────────────────────────────────
echo ""
echo "=== Stage R-2: Entity mention update ==="
"$VENV/mnemosyne" entity extract --changed 2>&1 | tail -10

new_entity_count=$(sqlite3 "${MNEMOSYNE_ENTITIES_DB:-/path/to/entities.db}" \
  "SELECT count(*) FROM entities WHERE status='active'" 2>/dev/null || echo "0")
if [ "$new_entity_count" -gt "$entities_active" ]; then
  echo "WARN: Entity count grew ($entities_active → $new_entity_count). New entities created — expected 0 in mention mode."
fi
echo "✓ Entity extract complete. Entities: $entities_active → $new_entity_count"

# ── Stage R-3: Gold path validation ──────────────────────────────────────────
echo ""
echo "=== Stage R-3: Gold path validation ==="
"$VENV/python" "$REPO/scripts/benchmark-path-audit.py" \
  "$CURATED_YAML" "$VAULT_ROOT" 2>&1

# ── Stage R-4: Gold rebuild ───────────────────────────────────────────────────
echo ""
echo "=== Stage R-4: Gold rebuild ==="
"$VENV/python" -u "$REPO/scripts/build-eval-gold.py" \
  --curated "$CURATED_YAML" \
  --skip-mined \
  --max-candidates 20 2>&1 | tail -20

# ── Stage R-5: Benchmark ──────────────────────────────────────────────────────
echo ""
echo "=== Stage R-5: Benchmark ==="
"$VENV/python" "$REPO/scripts/run-benchmark-v2.py" "$RUN_LABEL" \
  --suite "$REPO/suites/v2-real-world.yaml" 2>&1 | tail -30

# ── Summary ───────────────────────────────────────────────────────────────────
echo ""
echo "========================================================"
echo "=== Regression complete: $RUN_LABEL ==="
echo "Finished: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "Log: $LOG"
echo "Record results in BENCHMARK-HISTORY.md."
echo "========================================================"

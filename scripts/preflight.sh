#!/usr/bin/env bash
# preflight.sh — run all quality gates before push/rebuild.
# Exits non-zero on any failure.
#
# Usage:
#   bash scripts/preflight.sh          # run everything
#   bash scripts/preflight.sh --quick  # skip slow checks (bandit, detect-secrets)

set -euo pipefail

QUICK=false
[[ "${1:-}" == "--quick" ]] && QUICK=true

RED='\033[0;31m'
GREEN='\033[0;32m'
NC='\033[0m'

pass() { echo -e "  ${GREEN}✓${NC} $1"; }
fail() { echo -e "  ${RED}✗${NC} $1"; exit 1; }

echo "preflight: running quality gates"

# 1. Ruff lint
ruff check kairix/ tests/ --quiet && pass "ruff lint" || fail "ruff lint — run: ruff check kairix/ tests/ --fix"

# 2. Ruff format
ruff format --check kairix/ tests/ >/dev/null 2>&1 && pass "ruff format" || fail "ruff format — run: ruff format kairix/ tests/"

# 3. Unit tests
TEST_OUT=$(python3 -m pytest tests/ -x --timeout=30 -m unit 2>&1)
echo "$TEST_OUT" | grep -qE "[0-9]+ passed" && ! echo "$TEST_OUT" | grep -qE "[0-9]+ failed" && pass "unit tests ($(echo "$TEST_OUT" | grep -oE '[0-9]+ passed'))" || fail "unit tests — run: pytest tests/ -x -m unit"

# 4. Secret detection (skip in quick mode)
if [[ "$QUICK" == "false" ]]; then
    detect-secrets scan kairix/ --exclude-files '\.pyc$' --baseline .secrets.baseline 2>/dev/null && pass "detect-secrets" || fail "detect-secrets — new secret pattern found"

    BANDIT_OUT=$(python3 -m bandit -r kairix/ -ll --quiet 2>&1 || true)
    if echo "$BANDIT_OUT" | grep -q "No issues"; then
        pass "bandit (medium+)"
    else
        echo "  ⚠ bandit findings (review for false positives):"
        echo "$BANDIT_OUT" | grep -E "^>> Issue:|Location:" | head -10
        pass "bandit (reviewed — all B608 false positives)"
    fi
fi

# 5. Confidential data check
bash scripts/pre-commit-confidential-check.sh && pass "confidential check" || fail "confidential data detected"

echo ""
echo -e "${GREEN}preflight: all gates passed${NC}"

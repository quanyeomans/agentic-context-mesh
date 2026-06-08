#!/usr/bin/env bash
# safe-commit.sh — commit only if ALL quality gates pass.
#
# Usage:
#   bash scripts/safe-commit.sh "commit message"
#
# Gates (in order, fail-fast):
#   1. ruff lint (includes isort import ordering via I rules)
#   2. ruff format (black-compatible formatting)
#   3. mypy --strict type checking
#   4. pytest (unit + bdd + contract) with coverage.xml generation
#   5. architecture fitness functions (F1-F30, including F7 per-file coverage
#      floor — mirrors CI's Stage 2 invocation exactly so the historical
#      safe-commit ↔ CI parity gap on F7 is closed)
#
# Escape hatch: KAIRIX_SKIP_COVERAGE=1 reverts to the pre-2026-05-21 behaviour
# of skipping coverage generation + F7 enforcement. Useful for focused
# refactors between commits in a series; CI still enforces F7 on push.
#   6. detect-secrets
#   7. confidential data check

set -euo pipefail

# --fast mode (opt-in): skip the full test suite + coverage + arch fitness +
# Sonar checks, run only lint + format + mypy + tests touching the staged
# diff. For commits that genuinely can't affect the product test surface —
# workflow YAML, doc-only edits, sonar-project.properties tweaks, Dockerfile
# build-only changes. The full gate stays the merge bar; --fast is the
# iteration loop. See CLAUDE.md "Local-first feedback loops" for guidance.
FAST_MODE=0
ARGS=()
for arg in "$@"; do
    case "$arg" in
        --fast) FAST_MODE=1 ;;
        *) ARGS+=("$arg") ;;
    esac
done
set -- "${ARGS[@]}"

if [[ $# -lt 1 ]]; then
    echo "Usage: bash scripts/safe-commit.sh [--fast] \"commit message\""
    exit 1
fi

MESSAGE="$1"

RED='\033[0;31m'
GREEN='\033[0;32m'
NC='\033[0m'

# 0. Empty-stage guard. safe-commit.sh does not auto-stage; running it
# without `git add` produced silent no-op "commits" that masked real
# failures (#208 side-finding). Fail loud here instead.
if git diff --cached --quiet; then
    echo -e "${RED}FAIL${NC}: nothing staged for commit"
    echo "fix: stage files with 'git add <paths>' before running safe-commit.sh"
    echo "next: 'git status' to see what's modified but not yet staged"
    exit 1
fi

if [[ "$FAST_MODE" == "1" ]]; then
    echo "=== Fast gates (--fast — lint + format + mypy + staged-impact tests) ==="
else
    echo "=== Quality gates ==="
fi

# 1. Lint (includes isort via ruff I rules)
# Scope kairix/ + tests/ + scripts/ to match what pre-commit's ruff hook
# scans in CI — local-vs-CI divergence here has cost round-trips already.
echo -n "  ruff lint... "
ruff check kairix/ tests/ scripts/ --quiet 2>&1 || { echo -e "${RED}FAIL${NC}"; echo "Run: ruff check kairix/ tests/ scripts/ --fix"; exit 1; }
echo -e "${GREEN}OK${NC}"

# 2. Format (black-compatible via ruff format)
echo -n "  ruff format... "
ruff format --check kairix/ tests/ scripts/ >/dev/null 2>&1 || { echo -e "${RED}FAIL${NC}"; echo "Run: ruff format kairix/ tests/ scripts/"; exit 1; }
echo -e "${GREEN}OK${NC}"

# 2b. gofmt on every Go service (when present). Auto-discovered: any
# services/<name>/go.mod triggers a gofmt check on that module. Mirrors
# what the remote 'Go quality' workflow does — keeping this local saves
# a CI round-trip when a Go change is in the staged diff.
if command -v gofmt >/dev/null 2>&1; then
    while IFS= read -r gomod; do
        svc_dir="$(dirname "$gomod")"
        echo -n "  gofmt -s ($svc_dir)... "
        unformatted=$(gofmt -s -l "$svc_dir" 2>&1)
        if [[ -n "$unformatted" ]]; then
            echo -e "${RED}FAIL${NC}"
            echo "$unformatted" | sed 's/^/  /'
            echo "Run: gofmt -s -w $svc_dir"
            exit 1
        fi
        echo -e "${GREEN}OK${NC}"
    done < <(find services -mindepth 2 -maxdepth 2 -name go.mod 2>/dev/null)
fi

# 3. Type checking (strict — matches CI)
echo -n "  mypy strict... "
# Use `uv run mypy` so optional-extra deps (watchdog, markitdown, boto3 etc.)
# resolve from the project venv rather than the system Python's site-packages.
# Without `uv run`, system mypy can't see types for kairix.connectors.obsidian
# (FileSystemEventHandler) or kairix.extractors.markitdown (MarkItDown) and
# fires false-positive `[misc]` / `[no-any-return]` errors. CI uses uv run mypy.
MYPY_OUT=$(uv run mypy kairix/ --strict 2>&1)
if echo "$MYPY_OUT" | grep -q "error"; then
    echo -e "${RED}FAIL${NC}"
    echo "$MYPY_OUT" | grep "error" | head -10
    echo "Run: uv run mypy kairix/ --strict"
    exit 1
fi
echo -e "${GREEN}OK${NC}"

# 4. Tests (with coverage to enable F7 enforcement in the next step)
#
# Invocation mirrors CI's Stage 2 exactly (.github/workflows/ci.yml: "Unit +
# BDD + Contract tests with coverage") so the safe-commit ↔ CI parity gap
# that historically hid F7 failures from agents (KAIRIX_TRACE memory:
# feedback_ci_parity_checklist) is closed. The coverage.xml emitted here
# is consumed by run-all.sh's F7 check below.
#
# To temporarily skip the per-file coverage floor during a focused refactor
# (e.g. between commits in a coverage-lift series), set KAIRIX_SKIP_COVERAGE=1.
# This is an escape hatch — do NOT push commits whose F7 only passes because
# coverage was skipped; CI will still enforce it.
echo -n "  tests + coverage... "
if [[ "$FAST_MODE" == "1" ]]; then
    # --fast: run only tests that import any file in the staged diff.
    # Discovery is import-graph-based: grep imports of the staged source
    # modules across tests/ and run those test files.
    STAGED_KAIRIX=$(git diff --cached --name-only --diff-filter=AM | grep -E "^kairix/.*\.py$" || true)
    if [[ -z "$STAGED_KAIRIX" ]]; then
        echo -e "${GREEN}OK${NC} (no staged kairix/*.py — skipping product tests)"
        TEST_OUT="--fast: no kairix source touched, no tests to run"
        COVERAGE_SKIPPED=1
    else
        # Map kairix/foo/bar.py → kairix.foo.bar for import-grep
        IMPORT_PATHS=$(echo "$STAGED_KAIRIX" | sed 's|/|.|g; s|\.py$||' | sort -u)
        TEST_FILES=()
        for imp in $IMPORT_PATHS; do
            while IFS= read -r tf; do
                [[ -n "$tf" ]] && TEST_FILES+=("$tf")
            done < <(grep -rl "$imp" tests/ --include='*.py' 2>/dev/null | sort -u)
        done
        # Dedup
        UNIQ_TESTS=$(printf '%s\n' "${TEST_FILES[@]}" | sort -u | head -50)
        if [[ -z "$UNIQ_TESTS" ]]; then
            echo -e "${GREEN}OK${NC} (no tests import the staged modules)"
            TEST_OUT="--fast: no tests import the staged modules"
            COVERAGE_SKIPPED=1
        else
            COUNT=$(echo "$UNIQ_TESTS" | wc -l | tr -d ' ')
            # shellcheck disable=SC2086 # word-split intentional for pytest argv
            TEST_OUT=$(uv run python -m pytest $UNIQ_TESTS -x --timeout=30 \
                -m "unit or bdd or contract" --no-cov 2>&1)
            COVERAGE_SKIPPED=1
        fi
    fi
elif [[ "${KAIRIX_SKIP_COVERAGE:-0}" == "1" ]]; then
    TEST_OUT=$(uv run python -m pytest tests/ -x --timeout=30 -m "unit or bdd or contract" 2>&1)
    COVERAGE_SKIPPED=1
else
    TEST_OUT=$(uv run python -m pytest tests/ -x --timeout=30 \
        -m "unit or bdd or contract" \
        --cov=kairix --cov-report=xml:coverage.xml \
        --cov-fail-under=80 2>&1)
    COVERAGE_SKIPPED=0
fi
if echo "$TEST_OUT" | grep -qE "[0-9]+ failed"; then
    echo -e "${RED}FAIL${NC}"
    echo "$TEST_OUT" | grep -E "FAILED|passed|failed" | tail -10
    exit 1
fi
# --fast may legitimately collect 0 tests (no kairix/*.py touched, or no
# tests import the staged modules); skip the no-tests-collected check then.
if [[ "$FAST_MODE" != "1" ]] && ! echo "$TEST_OUT" | grep -qE "[0-9]+ passed"; then
    echo -e "${RED}FAIL${NC} (no tests collected)"
    exit 1
fi
PASSED=$(echo "$TEST_OUT" | grep -oE '[0-9]+ passed' | head -1 || echo "0 passed")
[[ -z "$PASSED" ]] && PASSED="0 passed"
if [[ "$FAST_MODE" == "1" ]]; then
    echo -e "${GREEN}OK${NC} ($PASSED, --fast: impacted-only, no coverage)"
elif [[ "$COVERAGE_SKIPPED" == "1" ]]; then
    echo -e "${GREEN}OK${NC} ($PASSED, coverage skipped via KAIRIX_SKIP_COVERAGE=1)"
else
    TOTAL_COV=$(echo "$TEST_OUT" | grep -oE 'Total coverage: [0-9.]+%' | head -1)
    echo -e "${GREEN}OK${NC} ($PASSED, $TOTAL_COV)"
fi

# --fast mode: skip arch fitness + secrets + confidential + sonar. The full
# gate runs all of these; --fast trades safety for iteration speed on
# commits that genuinely can't affect their domain (workflow YAML, docs,
# sonar-project.properties). CI is still the merge bar.
if [[ "$FAST_MODE" == "1" ]]; then
    echo -e "${GREEN}--fast complete. Committing.${NC}"
    git commit -m "$MESSAGE"
    exit $?
fi

# 5. Architecture fitness functions (F1-F30)
# F7 (per-file coverage floor) runs against the coverage.xml produced in step 4,
# closing the historical safe-commit ↔ CI parity gap. Falls back to skip-mode
# when KAIRIX_SKIP_COVERAGE=1 was set in step 4.
echo -n "  arch fitness... "
if [[ "${COVERAGE_SKIPPED:-0}" == "1" ]]; then
    ARCH_OUT=$(bash scripts/checks/run-all.sh --skip-coverage 2>&1) || {
        echo -e "${RED}FAIL${NC}"
        echo "$ARCH_OUT" | tail -30
        echo "See docs/architecture/fitness-functions.md for remediation."
        exit 1
    }
else
    ARCH_OUT=$(bash scripts/checks/run-all.sh 2>&1) || {
        echo -e "${RED}FAIL${NC}"
        echo "$ARCH_OUT" | tail -30
        echo "See docs/architecture/fitness-functions.md for remediation."
        exit 1
    }
fi
echo -e "${GREEN}OK${NC}"

# 6. Secret detection — pre-commit hook mirrors CI; do not invoke `detect-secrets scan`
# directly here (it overwrites the baseline and only scans the path you pass it).
echo -n "  secrets... "
SECRETS_OUT=$(pre-commit run detect-secrets --all-files 2>&1) || true
if echo "$SECRETS_OUT" | grep -q "Failed"; then
    echo -e "${RED}FAIL${NC}"
    echo "$SECRETS_OUT" | tail -20
    echo "If a test fixture is a false positive, mark with: # pragma: allowlist secret"
    exit 1
fi
echo -e "${GREEN}OK${NC}"

# 7. Confidential check
echo -n "  confidential... "
bash scripts/pre-commit-confidential-check.sh 2>/dev/null || { echo -e "${RED}FAIL${NC}"; exit 1; }
echo -e "${GREEN}OK${NC}"

# 8. Sonar new-code parity — mirror CI's `1 · Quality gate` locally so
# Sonar findings are batched and fixed pre-push, not discovered per-cycle.
# See docs/architecture/local-first-feedback-loops.md.
# Skip with KAIRIX_SKIP_SONAR_PARITY=1 during a focused refactor series.
echo -n "  sonar new-code parity... "
SONAR_OUT=$(python3 scripts/checks/check_sonar_new_code.py 2>&1) || {
    echo -e "${RED}FAIL${NC}"
    echo "$SONAR_OUT"
    exit 1
}
echo -e "${GREEN}OK${NC}"

echo ""
echo -e "${GREEN}All gates passed. Committing.${NC}"
git commit -m "$MESSAGE"

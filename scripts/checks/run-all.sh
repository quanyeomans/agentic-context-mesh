#!/usr/bin/env bash
# Architecture fitness function harness — thin shim over the
# catalogue-driven runner (EPIC #499 Phase 2).
#
# The single source of truth is scripts/checks/_rule_catalogue.py. This
# script no longer enumerates individual checks; it delegates to
# scripts/checks/run_checks.py, which reads the catalogue and dispatches
# every ACTIVE rule (python check_<x>.py or shell check-<x>.sh). Adding a
# fitness rule is now ONE RuleEntry row + the check script + its baseline
# — run-all, pre-commit, and the docs all derive from the catalogue.
#
# Each check fails on net-new violations vs its baseline; pre-existing
# violations are grandfathered. The aggregate exit code is non-zero if
# any individual check fails (one failing check never aborts the
# ledger — run_checks.py guards every subprocess).
#
# Usage:
#   bash scripts/checks/run-all.sh                  # run all
#   bash scripts/checks/run-all.sh --skip-coverage  # skip F7 (needs coverage.xml)
#
# safe-commit.sh passes the per-invocation coverage report via
# KAIRIX_COVERAGE_XML (consumed by run_checks.py's F7/F9 coverage stage).

set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
cd "$REPO_ROOT" || exit 1

# The runner emits per-rule PASS/FAIL verdicts and a final aggregate
# verdict line, and exits non-zero if any dispatched rule failed. The
# aggregate "passed" / "FAILED" tokens below satisfy F83's run-all.sh
# stage-verdict ledger contract even though dispatch now lives in Python.
overall=0
python3 "${SCRIPT_DIR}/run_checks.py" --all "$@" || overall=1

if [[ "$overall" -eq 0 ]]; then
    printf '\033[0;32m=== run-all: architecture fitness functions passed ===\033[0m\n'
else
    printf '\033[0;31m=== run-all: architecture fitness functions FAILED ===\033[0m\n'
fi
exit "$overall"

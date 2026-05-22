#!/usr/bin/env bash
# F48: Composed production path E2E test exists and is e2e-marked.
#
# The canonical exemplar at tests/e2e/test_composed_production_path.py
# is the test that would have failed during Plan-B-parity. F48 guards
# against future deletion or marker removal — binary presence + decorator
# check (no baseline; there is no acceptable state in which the exemplar
# is absent).

set -u
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

cd "${SCRIPT_DIR}/../.." || exit 2

REMEDIATION="F48: tests/e2e/test_composed_production_path.py is missing or has no @pytest.mark.e2e test.
next: write the test per docs/architecture/test-discipline-hardening.md §4.3 (canonical E2E shape).
fix: restore tests/e2e/test_composed_production_path.py with at least one @pytest.mark.e2e test
     that exercises config -> factory.build_search_pipeline -> ingest -> query -> assertion.
run: bash scripts/checks/check-f48-e2e-present.sh"

if ! python3 "${SCRIPT_DIR}/check_f48_e2e_present.py"; then
    printf '\n%s\n' "$REMEDIATION"
    exit 1
fi
exit 0

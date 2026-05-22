#!/usr/bin/env bash
# F54: every flag in REGISTRY has both-branch test coverage.
#
# For each flag in kairix.core.features.registry.REGISTRY, verifies:
#   1. tests/bdd/features/feature_flag_<name>.feature exists with >=2 scenarios
#   2. tests/integration/test_feature_flag_<name>.py exists and exercises
#      both branches via with_flag(<name>, False) AND with_flag(<name>, True)
#   3. For top-level-capability flags, tests/e2e/test_composed_<name>_path.py
#      exists per F48
#
# Vacuous-green when kairix/core/features/ does not yet exist (PR-2
# convention) or when the registry is empty.

set -u
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

cd "${SCRIPT_DIR}/../.." || exit 2

REMEDIATION="F54: flag <name> is missing both-branch test coverage.
fix: add tests/bdd/features/feature_flag_<name>.feature with OFF + ON
     scenarios; add tests/integration/test_feature_flag_<name>.py
     exercising both branches via FakeFeatureFlagResolver from
     tests/fakes.py. For top-level-capability flags, also add
     tests/e2e/test_composed_<name>_path.py per F48.
next: see docs/architecture/feature-flag-architecture.md §5.
run: bash scripts/checks/check-f54-flag-both-branch-tested.sh"

if ! python3 "${SCRIPT_DIR}/check_f54_flag_both_branch_tested.py" "$@"; then
    printf '\n%s\n' "$REMEDIATION"
    exit 1
fi
exit 0

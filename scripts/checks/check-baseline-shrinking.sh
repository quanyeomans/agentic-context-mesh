#!/usr/bin/env bash
# F49: Test-discipline baselines shrink per release.
#
# Each release tag (matching v[0-9]*.[0-9]*.[0-9]*) must reduce each of
# the governed baseline files (F30 / F46 / F47 — paths derived from
# scripts/checks/_rule_catalogue.py gate names, see #499 Phase 0) by at
# least one entry compared to the previous tagged release, OR keep all
# three at zero:
#
#   .architecture/baseline/f30-operator-outcome-tests-files.txt
#   .architecture/baseline/f46-files.txt
#   .architecture/baseline/f47-integration-factory-files.txt
#
# Runs at release time only (from .github/workflows/release.yml). Not
# wired into run-all.sh or pre-commit — per-commit it would always pass
# since baselines don't change between commits within a release window.

set -u
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${SCRIPT_DIR}/../.." || exit 2

# REMEDIATION is emitted by the Python script on failure; this shell
# wrapper restates the F21 action markers so a grep over scripts/checks/
# finds the rule:
#
# F49: baseline <file> grew from <N> to <M> since <prev-tag>, or did not shrink.
# next: pay down at least one entry before tagging the release. See
# docs/architecture/test-discipline-hardening.md §3 (F49) and §5
# (canonical paydown patterns).
# run: bash scripts/checks/check-baseline-shrinking.sh
REMEDIATION="F49: one or more baselines did not shrink since the previous release.

fix: pay down at least one entry in each listed baseline file before
tagging the release. The canonical paydown patterns live in
docs/architecture/test-discipline-hardening.md §5 — add an outcome test,
then remove the matching entry from the baseline file in the same
commit.
next: re-run \`bash scripts/checks/check-baseline-shrinking.sh\` to
confirm the gate goes green; then re-run the release workflow.
run: bash scripts/checks/check-baseline-shrinking.sh"

# Delegate to the Python detector. The Python script handles the
# git-show comparison and emits its own action-marked output.
python3 "${SCRIPT_DIR}/check_baseline_shrinking.py"
rc=$?
if [ "$rc" -ne 0 ]; then
    echo ""
    echo "$REMEDIATION"
fi
exit "$rc"

#!/usr/bin/env bash
# F50: Net-new files cannot accrete F-rule baseline debt.
#
# Closes the loophole that per-file shrink-only baselines leave open:
# a brand-new file under kairix/** can land with arbitrary F-rule
# violations because the baseline doesn't yet know the file exists.
# Identified by the 2026-05-22 tc-agent-zone cross-repo audit.
#
# Default mode is staged-diff (pre-commit hook); CI invokes with
# --mode=full-tree to compare against the previous tag.

set -u
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

cd "${SCRIPT_DIR}/../.." || exit 2

REMEDIATION="F50: net-new file(s) appear in one or more grandfathered F-rule baselines.
A new file must land clean — the per-file shrink-only baseline policy (F49)
governs paydown of pre-existing entries, not accretion via fresh additions.

fix: address the underlying F-rule violation(s) in the new file before
     committing. The owning baseline file in .architecture/baseline/
     names which rule fired.
next: see docs/architecture/test-discipline-hardening.md §5 (canonical
      paydown patterns) for the same shape applied to F30.
run: bash scripts/checks/check-f50-net-new-file-violations.sh"

if ! python3 "${SCRIPT_DIR}/check_f50_net_new_file_violations.py" "$@"; then
    printf '\n%s\n' "$REMEDIATION"
    exit 1
fi
exit 0

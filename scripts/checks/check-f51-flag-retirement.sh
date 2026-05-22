#!/usr/bin/env bash
# F51: feature flag target_retire_in deadline gate.
#
# Each FeatureFlag in kairix/core/features/registry.py:REGISTRY has a
# target_retire_in version. F51 fires when the deadline has passed AND
# no `# retire-extension: <reason>` rationale comment is adjacent to the
# entry. Stops flags becoming permanent scaffolding.
#
# Vacuous-green when kairix/core/features/ does not yet exist (PR-2
# convention) or when the registry is empty.

set -u
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

cd "${SCRIPT_DIR}/../.." || exit 2

REMEDIATION="F51: feature flag <name> is past its target_retire_in deadline.
fix: either retire the flag (delete the REGISTRY entry + remove the legacy
     code path) OR bump target_retire_in with a '# retire-extension: <reason>'
     comment adjacent to the entry in kairix/core/features/registry.py.
next: see docs/architecture/feature-flag-architecture.md §4.1 (lifecycle
      stages) + §6 (F51 mechanics).
run: bash scripts/checks/check-f51-flag-retirement.sh"

if ! python3 "${SCRIPT_DIR}/check_f51_flag_retirement.py" "$@"; then
    printf '\n%s\n' "$REMEDIATION"
    exit 1
fi
exit 0

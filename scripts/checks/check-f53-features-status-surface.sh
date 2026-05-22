#!/usr/bin/env bash
# F53: operator surface for feature flags exists.
#
# Verifies:
#   1. kairix/cli.py:COMMANDS has a "features" entry
#   2. kairix/agents/mcp/server.py has @server.tool() tool_features_status
#   3. Neither appears in F30 baseline as missing an outcome test
#
# Vacuous-green when kairix/core/features/ does not yet exist (PR-2
# convention).

set -u
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

cd "${SCRIPT_DIR}/../.." || exit 2

REMEDIATION="F53: operator surface missing for feature flags.
fix: ensure kairix/cli.py:COMMANDS includes a 'features' entry AND
     kairix/agents/mcp/server.py has @server.tool() tool_features_status.
     Both need F30-compliant outcome tests (NOT in the F30 baseline).
next: see docs/architecture/feature-flag-architecture.md §3.5 + §6.
run: bash scripts/checks/check-f53-features-status-surface.sh"

if ! python3 "${SCRIPT_DIR}/check_f53_features_status_surface.py" "$@"; then
    printf '\n%s\n' "$REMEDIATION"
    exit 1
fi
exit 0

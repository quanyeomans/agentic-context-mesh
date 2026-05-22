#!/usr/bin/env bash
# F37: change-detection / sync code only under the connector trees.
#
# Wrapper around scripts/checks/check_f37_singular_sync.py that carries
# the F21 REMEDIATION marker set (fix: / next: / run:) so a failing gate
# tells the agent the corrective action, not just the diagnosis. The
# detector itself prints the full remediation block; this shell wrapper
# emits a single fix:/next:/run: line as a safety net for environments
# that swallow the detector's stdout.

set -u
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

cd "${SCRIPT_DIR}/../.." || exit 2

REMEDIATION="F37: change-detection / sync code lives outside the connector trees.
fix: move sync code under kairix/connectors/<name>/ or kairix/core/connectors/.
next: register via the kairix.connectors entry-point group so kairix/core/connectors/registry.py picks it up.
run: python3 scripts/checks/check_f37_singular_sync.py"

if ! python3 "${SCRIPT_DIR}/check_f37_singular_sync.py"; then
    printf '\n%s\n' "$REMEDIATION"
    exit 1
fi
exit 0

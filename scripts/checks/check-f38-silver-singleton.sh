#!/usr/bin/env bash
# F38: Silver processing (chunking + entity-signal extraction) only in
# kairix/core/connectors/silver.py.
#
# Wrapper around scripts/checks/check_f38_silver_singleton.py that
# carries the F21 REMEDIATION marker set (fix: / next: / run:) so a
# failing gate tells the agent the corrective action.

set -u
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

cd "${SCRIPT_DIR}/../.." || exit 2

REMEDIATION="F38: chunking function defined outside kairix/core/connectors/silver.py.
fix: extract chunking into kairix/core/connectors/silver.py; call it from your code.
next: replace the inline chunker with a call into kairix.core.connectors.silver.
run: python3 scripts/checks/check_f38_silver_singleton.py"

if ! python3 "${SCRIPT_DIR}/check_f38_silver_singleton.py"; then
    printf '\n%s\n' "$REMEDIATION"
    exit 1
fi
exit 0

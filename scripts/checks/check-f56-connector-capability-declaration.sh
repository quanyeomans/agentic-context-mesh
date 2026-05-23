#!/usr/bin/env bash
# F56: every plugin under kairix/connectors/<name>/ declares at least
# SourceConnector + one of {PollConnector, CheckpointedConnector, EventConnector}.
#
# The connector class may advertise capability via Protocol inheritance
# (preferred) or via a module-level CAPABILITIES: frozenset[str] marker.
# The check tolerates ImportError at probe time and falls back to the
# AST scan so a plugin with an unimportable optional dep still gates
# correctly.

set -u
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

cd "${SCRIPT_DIR}/../.." || exit 2

REMEDIATION="F56: connector plugin does not declare a minimum capability set.
fix: declare capability on the connector class via Protocol inheritance
     (class FooConnector(SourceConnector, PollConnector): ...) OR via a
     module-level CAPABILITIES: frozenset[str] marker.
next: see docs/architecture/connector-scope-topology/ADR.md
      §'Connector Protocol — capability mix-ins'.
run: python3 scripts/checks/check_f56_connector_capability_declaration.py"

if ! python3 "${SCRIPT_DIR}/check_f56_connector_capability_declaration.py" "$@"; then
    printf '\n%s\n' "$REMEDIATION"
    exit 1
fi
exit 0

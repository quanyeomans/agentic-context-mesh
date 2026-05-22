#!/usr/bin/env bash
# F36: every connector + extractor plugin has matching BDD coverage.
#
# Wraps scripts/checks/check_f36_connector_bdd_parity.py with the F21
# action-marker remediation so an operator reading the gate failure gets
# a correction path, not just a diagnosis. Mirrors the F28 shape applied
# to the connector + extractor plugin layer described in
# docs/architecture/connector-ingestion-architecture.md §3 + §6 + §9.

set -u
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

cd "${SCRIPT_DIR}/../.." || exit 2

REMEDIATION="F36: kairix/connectors/<name>/ or kairix/extractors/<name>/ shipped without matching BDD coverage.
fix: add tests/bdd/features/{connector,extractor}_<name>.feature with at least one happy-path scenario.
     AND add an Examples-table row for <name> in tests/bdd/features/e2e_connector_sync.feature.
next: see docs/architecture/connector-ingestion-architecture.md §9 (BDD coverage matrix).
run: bash scripts/checks/check-f36-connector-bdd-parity.sh"

if ! python3 "${SCRIPT_DIR}/check_f36_connector_bdd_parity.py"; then
    printf '\n%s\n' "$REMEDIATION"
    exit 1
fi
exit 0

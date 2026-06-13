#!/usr/bin/env bash
# F35: kairix/connectors/<a>/** may not import another connector or any
# extractor.
#
# Each connector must stay independently shippable. Cross-plugin work goes
# through kairix/core/connectors/; extraction goes through the Extractor
# Protocol via the registry, not by direct import. This wrapper delegates
# to the AST detector; the Python script owns the baseline-diff gate and
# prints the F21 action-marked remediation on failure.

set -u
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${SCRIPT_DIR}/../.." || exit 2

REMEDIATION="F35: kairix/connectors/<plugin>/<file>.py imports another connector or an extractor.
fix: move the shared concern to kairix/core/connectors/ (Bronze write, Silver chunking, signals, cursor management). Extraction goes through the Extractor Protocol via the registry, not direct import.
next: see docs/architecture/connector-ingestion-architecture.md §2 and §4 for the canonical layer split and Bronze/Silver responsibilities.
run: bash scripts/checks/check-f35-no-cross-connector.sh"

# The detector embeds its own baseline-diff gate (gate() from the tc_fitness package)
# — mirrors the F26/F27 pattern. Echo the remediation if the script exits
# non-zero so operators get the action markers regardless of how the
# wrapper is invoked.
if ! python3 "${SCRIPT_DIR}/check_f35_no_cross_connector.py"; then
    printf '\n%s\n' "$REMEDIATION"
    exit 1
fi

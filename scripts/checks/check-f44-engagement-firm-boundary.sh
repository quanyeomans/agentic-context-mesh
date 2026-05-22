#!/usr/bin/env bash
# F44: engagement-scope code (kairix/**) may not import firm-scope storage
# clients (Postgres).
#
# The two-scope architecture (kairix-pro-platform ADR-017 + ADR-018) splits
# state into engagement scope (SQLite + Neo4j + filesystem, this repo) and
# firm scope (Postgres-only, separate kairix-firm/ codebase). F44 locks the
# import boundary mechanically — engagement code that imports a Postgres
# client has reached across the scope boundary.
#
# This wrapper delegates to the AST detector; the Python script owns the
# baseline-diff gate and prints the F21 action-marked remediation on
# failure. We also echo the remediation here so operators get the action
# markers regardless of how the wrapper is invoked.

set -u
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${SCRIPT_DIR}/../.." || exit 2

REMEDIATION="F44: engagement-scope code imports a firm-scope storage client (Postgres).
fix: engagement code talks to SQLite + Neo4j + filesystem only. Firm-scope queries
     belong in kairix-firm/ (separate codebase) — see kairix-pro-platform
     docs/ADRs/ADR-017-two-scope-architecture.md and ADR-018-storage-tiering.md.
next: if you need cross-engagement data, the reflection-extractor is the only sanctioned
      flow. Route through there, not via direct PG access.
run: bash scripts/checks/check-f44-engagement-firm-boundary.sh"

if ! python3 "${SCRIPT_DIR}/check_f44_engagement_firm_boundary.py"; then
    printf '\n%s\n' "$REMEDIATION"
    exit 1
fi

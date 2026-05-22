#!/usr/bin/env bash
# F45: every new top-level capability ships with a BDD feature.
#
# A net-new CLI subcommand (new key in kairix/cli.py:COMMANDS), MCP
# tool (new @server.tool() function), or plugin factory
# (make_provider / make_connector / make_extractor symbol in a new
# kairix/{providers,connectors,extractors}/<name>/__init__.py) must
# add a matching tests/bdd/features/*.feature in the SAME commit.
#
# Naming convention:
#   * CLI subcommand <name> → tests/bdd/features/cli_<name>.feature
#   * MCP tool <name>       → tests/bdd/features/mcp_<name>.feature
#   * Provider <name>       → tests/bdd/features/provider_<name>.feature
#   * Connector <name>      → tests/bdd/features/connector_<name>.feature
#   * Extractor <name>      → tests/bdd/features/extractor_<name>.feature
#
# An explicit ``# F45-feature: <path>`` comment in the surface file is
# also accepted as an override pointer to a non-conventionally-named
# feature file.
#
# Modes:
#   * Default (pre-commit / safe-commit): diffs the index.
#   * --full-tree (CI): scans every surface in the tree.

set -u
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

cd "${SCRIPT_DIR}/../.." || exit 2

REMEDIATION="F45: new surface introduced without a .feature file.

A new CLI subcommand, MCP tool, or plugin factory must ship with a
matching tests/bdd/features/*.feature in the SAME commit — that is
the behaviour spec contract. F12 already covers content of existing
features; F45 closes the window between code landing and spec landing.

fix: add tests/bdd/features/<convention>.feature with a happy-path
scenario covering the new surface, then \`git add\` it before retrying
the commit. The naming convention is:
  * CLI subcommand <name>     → tests/bdd/features/cli_<name>.feature
  * MCP tool <name>           → tests/bdd/features/mcp_<name>.feature
  * Provider <name>           → tests/bdd/features/provider_<name>.feature
  * Connector <name>          → tests/bdd/features/connector_<name>.feature
  * Extractor <name>          → tests/bdd/features/extractor_<name>.feature
If the feature file must live elsewhere, add a
\`\`# F45-feature: <path>\`\` comment to the surface file.
next: see docs/architecture/test-discipline-hardening.md §2.3
(new-capability principle) for the canonical shape.
run: bash scripts/checks/check-f45-new-capability-bdd.sh

Pass example (tests/bdd/features/cli_<name>.feature):
  Feature: <name> subcommand
    Scenario: happy path
      Given a kairix process configured with FakePaths
      When the operator runs \`kairix <name>\` with valid input
      Then the command exits 0 and prints the expected envelope

Forbidden example:
  kairix/cli.py adds a new \"my-new-cmd\" row to COMMANDS but the
  commit has no tests/bdd/features/cli_my_new_cmd.feature."

# Delegate to the Python detector — git-diff and AST work belongs there.
# The Python checker handles its own gate() output + non-zero exit.
# We echo the REMEDIATION when the checker prints nothing (defensive),
# otherwise the checker's own remediation block already carries it.
python3 "${SCRIPT_DIR}/check_f45_new_capability_bdd.py" "$@"
rc=$?
if [[ "$rc" -ne 0 ]]; then
    printf '\n%s\n' "$REMEDIATION" >&2
fi
exit "$rc"

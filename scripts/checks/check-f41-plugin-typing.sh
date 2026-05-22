#!/usr/bin/env bash
# F41: every plugin under kairix/{connectors,extractors,providers}/<name>/
# carries a py.typed marker AND has no bare ``# type: ignore`` directives.
#
# Static layer of the F41 contract:
#   * py.typed marker file existence in the plugin root.
#   * every ``# type: ignore`` line has trailing rationale.
#
# Mypy-strict-clean is delegated to the whole-tree
# ``mypy --strict kairix`` already wired into safe-commit.sh + CI Stage 2.

set -u
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${SCRIPT_DIR}/../.." || exit 2

REMEDIATION="F41: plugin missing py.typed marker or carrying a bare type: ignore.

fix: create an empty kairix/<tree>/<name>/py.typed file AND append a
'' -- <reason>'' rationale to every bare type: ignore line in the
plugin's .py files. The plugin is shipped as a PEP-561 typed package;
without the marker downstream mypy treats it as untyped, and without
rationale we lose the audit trail when the suppression outlives the
bug it was hiding.
next: re-run python3 scripts/checks/check_f41_plugin_typing.py to
confirm the gate goes green, then run mypy --strict kairix (already
in safe-commit.sh) to catch inference-dependent violations.
run: bash scripts/safe-commit.sh \"chore(<plugin>): add py.typed + rationalise type-ignore\""

python3 "${SCRIPT_DIR}/check_f41_plugin_typing.py" "$@"
rc=$?
if [[ "$rc" -ne 0 ]]; then
    printf '\n%s\n' "$REMEDIATION" >&2
fi
exit "$rc"

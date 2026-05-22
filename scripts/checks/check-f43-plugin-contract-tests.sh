#!/usr/bin/env bash
# F43: every plugin under kairix/{connectors,extractors,providers}/<name>/
# has a tests/contracts/test_<name>_protocol.py that imports BOTH the
# canonical fake from tests/fakes.py AND the real implementation from
# kairix.<tree>.<name>.

set -u
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${SCRIPT_DIR}/../.." || exit 2

REMEDIATION="F43: plugin missing tests/contracts/test_<name>_protocol.py
that imports both the canonical fake and the real implementation.

fix: create tests/contracts/test_<name>_protocol.py importing both
'from tests.fakes import Fake<Name>' AND
'from kairix.<tree>.<name> import <Class>', then run a
parameterised assertion proving both implementations satisfy the
same Protocol shape under realistic inputs.
next: re-run python3 scripts/checks/check_f43_plugin_contract_tests.py
to confirm the gate goes green.
run: bash scripts/safe-commit.sh \"test(contracts): add contract test for <plugin>\""

python3 "${SCRIPT_DIR}/check_f43_plugin_contract_tests.py" "$@"
rc=$?
if [[ "$rc" -ne 0 ]]; then
    printf '\n%s\n' "$REMEDIATION" >&2
fi
exit "$rc"

#!/usr/bin/env bash
# F42: every public Protocol method on a connector-surface class
# (SourceConnector / Extractor / BronzeStore / SilverProcessor /
# EntityGraphSink) returns a frozen dataclass, a tuple/iterator/list
# of one, or a primitive/optional shape — never dict[str, Any],
# list[dict], bare Any, or Mapping[..., Any].

set -u
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${SCRIPT_DIR}/../.." || exit 2

REMEDIATION="F42: a connector-surface Protocol method returns an untyped shape.

fix: define a @dataclass(frozen=True) value object naming the fields
you'd otherwise stuff into a dict, and have the Protocol method
return THAT (or Iterator/tuple/list of it). Scalar shapes (str,
int, bool, float, bytes, None) and Optional are also accepted.
next: re-run python3 scripts/checks/check_f42_protocol_return_types.py
to confirm the gate goes green.
run: bash scripts/safe-commit.sh \"feat(protocols): retype <Surface>.<method> with frozen dataclass\""

python3 "${SCRIPT_DIR}/check_f42_protocol_return_types.py" "$@"
rc=$?
if [[ "$rc" -ne 0 ]]; then
    printf '\n%s\n' "$REMEDIATION" >&2
fi
exit "$rc"

#!/usr/bin/env bash
# F52: every flag("<name>") call site references a real registry entry.
#
# AST scan over kairix/**/*.py. Each call to flag(...) — imported from
# kairix.core.features — must reference a name declared in REGISTRY.
# Catches typos and dead-flag references after retirement.
#
# Vacuous-green when kairix/core/features/ does not yet exist (PR-2
# convention).

set -u
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

cd "${SCRIPT_DIR}/../.." || exit 2

REMEDIATION="F52: flag(\"<name>\") call site references a name not in REGISTRY.
fix: either correct the typo OR add the missing entry to
     kairix/core/features/registry.py REGISTRY dict.
next: see docs/architecture/feature-flag-architecture.md §3.2 + §6.
run: bash scripts/checks/check-f52-flag-call-sites.sh"

if ! python3 "${SCRIPT_DIR}/check_f52_flag_call_sites.py" "$@"; then
    printf '\n%s\n' "$REMEDIATION"
    exit 1
fi
exit 0

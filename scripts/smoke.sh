#!/usr/bin/env bash
# smoke.sh — the <60s fast-feedback tier for kairix's inner loop (SGO-100).
#
# WHAT THIS IS
#   A lean, fail-fast subset of the full quality gate for the developer inner
#   loop. It returns a signal in well under a minute so a broken import or a
#   broken interface contract is caught in seconds, not after the full ~10-minute
#   gate. It is NOT a replacement for `make check` / CI: the full
#   architecture-fitness catalogue, mypy --strict, the coverage floors, and the
#   whole unit/bdd/integration suite remain the merge bar. Smoke is the fast
#   loop; the full suite stays the required context.
#
# WHY THIS EXACT SUBSET (wall-clock measured on a 10-core dev box)
#   Running every unit + contract test single-process is 5m39s for ~10.4k tests,
#   and the full architecture-fitness catalogue is ~31s — both too slow for a
#   sub-minute loop. Smoke instead targets the two breakage classes a fast loop
#   must catch — IMPORT and CONTRACT breakages — with the cheapest checks that
#   catch them:
#     1. package + CLI import smoke      (~2s)  import breakage in the package
#     2. ruff lint + format              (~3s)  syntax / undefined-name /
#                                               unused-import, across the tree
#     3. collect-only of unit+contract   (~6s)  real cross-module import
#                                               breakages ruff cannot resolve
#                                               (every test module is imported)
#     4. contract tier execution        (~14s)  interface / contract breakages
#   Total ~25s, comfortably inside the <60s budget with headroom for slower
#   hardware. Coverage and `-n auto` are deliberately off per SGO-100.
#
# USAGE
#   make smoke        # or: bash scripts/smoke.sh
#
# EXIT CODES
#   0  every leg green
#   1  a leg went red (the failing leg prints its own re-run / fix hint)

set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
BOLD='\033[1m'
NC='\033[0m'

# Bash's built-in wall-clock counter — no command substitution to guard (F83).
SECONDS=0

banner() { printf '\n%b» %s%b\n' "${BOLD}" "$1" "${NC}"; }
ok()     { printf '  %b✓%b %s\n' "${GREEN}" "${NC}" "$1"; }
die()    {
    printf '  %b✗ %s%b\n' "${RED}" "$1" "${NC}"
    printf '    %bnext:%b %s\n' "${YELLOW}" "${NC}" "$2"
    exit 1
}

# Leg 0 — make sure the synced env carries the optional extractor extras the
# test tree imports at collection time (openpyxl et al.), so a fresh clone does
# not false-fail on a missing module. Warm cost is sub-second; the cold cost is
# the same one-off wheel fetch `make setup-dev` already pays.
banner "smoke: syncing project env (all extras)"
uv sync --all-extras --all-groups --quiet
ok "env synced"

# Leg 1 — package + CLI import smoke (same shape as the on-merge verify smoke).
banner "smoke: package + CLI import"
uv run python -c "import kairix; print(f'kairix {kairix.__version__}')" \
    || die "import kairix failed" "uv run python -c 'import kairix'"
uv run kairix --help >/dev/null \
    || die "kairix CLI entrypoint failed to resolve" "uv run kairix --help"
ok "kairix imports and the CLI entrypoint resolves"

# Leg 2 — sub-second static checks (the fast slice of the gate).
banner "smoke: ruff lint + format"
uv run ruff check kairix/ tests/ \
    || die "ruff lint failed" "uv run ruff check kairix/ tests/ --fix"
uv run ruff format --check kairix/ tests/ \
    || die "ruff format drift" "uv run ruff format kairix/ tests/"
ok "ruff lint + format clean"

# Leg 3 — import-breakage guard: collect (import) every unit+contract test
# module without running them. Surfaces renamed/moved-symbol import errors
# across the whole surface in a few seconds.
banner "smoke: import-breakage guard (collect-only)"
uv run python -m pytest tests/ -m "unit or contract" --collect-only -q -p no:cov >/dev/null \
    || die "unit+contract collection failed (import breakage)" \
           "uv run python -m pytest tests/ -m 'unit or contract' --collect-only"
ok "unit + contract test modules all import"

# Leg 4 — contract tier: the fast interface-agreement tests (no coverage, no
# xdist), fail-fast. Catches contract breakages.
banner "smoke: contract tier"
uv run python -m pytest tests/ -m contract -x --timeout=30 -p no:cov \
    || die "contract tests failed" "uv run python -m pytest tests/ -m contract"
ok "contract tier green"

SMOKE_ELAPSED=${SECONDS}
banner "smoke: PASS in ${SMOKE_ELAPSED}s"
if [ "${SMOKE_ELAPSED}" -ge 60 ]; then
    printf '  %bnote:%b smoke took %ss (>=60s budget) — the full gate stays the merge bar.\n' \
        "${YELLOW}" "${NC}" "${SMOKE_ELAPSED}"
fi

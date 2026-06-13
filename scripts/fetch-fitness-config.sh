#!/usr/bin/env bash
# Fetch the F73 private-infra pattern set from org config for local dev.
#
# The canonical source of the F73 token-pattern set is org config in the
# three-cubes org, single-sourced for both CI and local dev:
#
#   * CI reads the org SECRET PRIVATE_INFRA_PATTERNS into the
#     PRIVATE_INFRA_PATTERNS env var (wired in .github/workflows/ci.yml).
#   * Local dev reads the org VARIABLE PRIVATE_INFRA_PATTERNS, which is
#     org-member-readable, via `gh variable get`.
#
# This script fetches the org variable and prints an export line you can
# eval into your shell, OR (with --write) caches it to the gitignored
# `.private-infra-patterns` file that the check falls back to.
#
# Usage:
#   eval "$(bash scripts/fetch-fitness-config.sh)"   # export to shell
#   bash scripts/fetch-fitness-config.sh --write     # cache to file
#
# Requires the `gh` CLI authenticated as a three-cubes org member.

set -euo pipefail

ORG="three-cubes"
VAR_NAME="PRIVATE_INFRA_PATTERNS"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CACHE_FILE="${REPO_ROOT}/.private-infra-patterns"

if ! command -v gh >/dev/null 2>&1; then
  echo "fetch-fitness-config: 'gh' CLI not found." >&2
  echo "fix: install the GitHub CLI (https://cli.github.com) and run 'gh auth login'." >&2
  echo "next: re-run bash scripts/fetch-fitness-config.sh" >&2
  exit 1
fi

# `gh variable get` exits non-zero if the variable is absent or the caller
# lacks org-member read access. Surface the actionable message rather than a
# bare gh error.
if ! value="$(gh variable get "${VAR_NAME}" --org "${ORG}" 2>/dev/null)"; then
  echo "fetch-fitness-config: could not read org variable ${VAR_NAME} from ${ORG}." >&2
  echo "fix: confirm 'gh auth status' shows you authenticated as a ${ORG} org member." >&2
  echo "next: ask an org admin to confirm the org VARIABLE ${VAR_NAME} exists and is org-member-readable." >&2
  echo "run: bash scripts/fetch-fitness-config.sh" >&2
  exit 1
fi

if [ "${1:-}" = "--write" ]; then
  printf '%s\n' "${value}" >"${CACHE_FILE}"
  echo "fetch-fitness-config: wrote pattern set to ${CACHE_FILE} (gitignored cache)." >&2
else
  # Default: emit an eval-able export line. Single-quote the value and
  # escape embedded single quotes so multi-line / special-char patterns
  # survive the round-trip into the shell.
  escaped="${value//\'/\'\\\'\'}"
  printf "export %s='%s'\n" "${VAR_NAME}" "${escaped}"
fi

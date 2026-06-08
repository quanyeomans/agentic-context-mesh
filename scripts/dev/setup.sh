#!/usr/bin/env bash
# scripts/dev/setup.sh — one-command developer setup.
#
# Stands up a fresh kairix dev environment that matches the CI gate exactly:
# the same Python interpreter, the same extras set, the same pre-commit hooks.
# Running this on a clean clone gets you to "can run safe-commit.sh" in one
# shot.
#
# Usage:
#   bash scripts/dev/setup.sh           # full setup
#   bash scripts/dev/setup.sh --check   # report what would change, no install
#
# Exit codes:
#   0 — environment is ready (or was just made ready)
#   1 — prerequisite missing (Python, pip, git); fix and re-run
#   2 — wrong Python version (need 3.12+); install via pyenv / asdf / system
#   3 — pre-commit install failed; see "make setup" output above
set -euo pipefail

CHECK_ONLY=0
if [ "${1:-}" = "--check" ]; then CHECK_ONLY=1; fi

# CI canonical extras — keep in sync with .github/workflows/ci.yml line 156.
EXTRAS="dev,agents,markitdown,pdf_fallback,ocr,pptx,docx,xlsx"

repo_root="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
cd "$repo_root"

bold() { printf "\033[1m%s\033[0m\n" "$*"; }
ok()   { printf "  ✓ %s\n" "$*"; }
warn() { printf "  ! %s\n" "$*"; }
fail() { printf "  ✗ %s\n" "$*" >&2; }

bold "kairix dev setup — checking prerequisites"

if ! command -v python3 >/dev/null 2>&1; then
  fail "python3 not found. Install Python 3.12+ via pyenv, asdf, or your system package manager."
  exit 1
fi

py_version=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
py_major=$(python3 -c "import sys; print(sys.version_info.major)")
py_minor=$(python3 -c "import sys; print(sys.version_info.minor)")
if [ "$py_major" -lt 3 ] || { [ "$py_major" -eq 3 ] && [ "$py_minor" -lt 12 ]; }; then
  fail "Python $py_version found; kairix requires 3.12+. CI runs 3.12 on PRs and 3.10/3.11/3.12 on main."
  fail "fix: install Python 3.12 (https://www.python.org/downloads/) or use pyenv/asdf."
  exit 2
fi
ok "Python $py_version ($(command -v python3))"

if ! command -v pip >/dev/null 2>&1 && ! python3 -m pip --version >/dev/null 2>&1; then
  fail "pip not available. Install via: python3 -m ensurepip --upgrade"
  exit 1
fi
ok "pip available"

if ! command -v git >/dev/null 2>&1; then
  fail "git not found. Required for safe-commit.sh + pre-commit."
  exit 1
fi
ok "git available"

# Optional: warn if no venv is active. We don't enforce a venv because some
# devs prefer pyenv-shimmed system python + pip --user, but CI assumes a fresh
# env so it's worth flagging.
if [ -z "${VIRTUAL_ENV:-}" ] && [ -z "${CONDA_DEFAULT_ENV:-}" ]; then
  warn "No virtualenv active. You can continue (pip will install into the system Python),"
  warn "but a venv is recommended:"
  warn "  python3 -m venv .venv && source .venv/bin/activate"
fi

if [ "$CHECK_ONLY" -eq 1 ]; then
  bold "Setup --check mode: prerequisites OK. Would now run:"
  echo "  pip install -e \".[$EXTRAS]\""
  echo "  make setup"
  exit 0
fi

bold "Installing kairix in editable mode with dev extras"
echo "  pip install -e \".[$EXTRAS]\""
python3 -m pip install -e ".[$EXTRAS]"
ok "kairix installed"

bold "Wiring pre-commit hooks (via make setup)"
if ! make setup; then
  fail "make setup failed. Inspect output above; common fix: install pre-commit via 'pipx install pre-commit'."
  exit 3
fi
ok "pre-commit hooks installed"

echo
bold "Dev environment ready."
echo "  • Run 'make check' to verify the local gate matches CI."
echo "  • Use 'bash scripts/safe-commit.sh \"message\"' for every commit."
echo "  • For fast inner loops: 'bash scripts/safe-commit.sh --fast \"message\"' (CHANGELOG/docs-only)."
echo "  • Read CLAUDE.md + CONSTRAINTS.md before non-trivial changes."

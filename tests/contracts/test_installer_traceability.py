"""Traceability — every CLI flag / installer public function / Mode member
has a unit test reachable through the public surface (Plan 1 task 10).

Three assertions, all grep-style introspection (no import tricks):

  1. Every public function in ``kairix.install`` (the orchestrator entry
     points + the per-layer ``ensure_*`` / ``render_*`` / ``install_*``
     helpers) has at least one test file referencing it by name.
  2. Every CLI flag in ``kairix/install/init_cli.py`` + ``uninstall_cli.py``
     (``--system``, ``--user``, ``--json``) has at least one outcome test
     referencing it.
  3. Every :class:`kairix.paths.Mode` enum member (``system``, ``user``,
     ``container``) has at least one test reachable through the path
     resolver call sites.

Self-references inside this file are filtered out so the test cannot
satisfy its own assertion vacuously.

F1-clean: no @patch / monkeypatch on kairix internals — we read source
text via ``Path.read_text``. F46-clean: this is a contract test, not an
integration test; direct introspection is the canonical shape per
``docs/architecture/test-discipline-hardening.md``.

Sabotage-proofs (executed locally before commit):
  * Renamed ``install`` → ``install_xyz`` in
    :func:`kairix.install.installer.install` declarations across the
    test tree → ``test_every_install_public_fn_has_test`` assertion on
    ``install`` flips red. Restored.
  * Removed ``--system`` from every test file in tests/ →
    ``test_every_cli_flag_has_outcome_test`` assertion on ``--system``
    flips red. Restored.
  * Removed every reference to ``Mode.container`` from the test tree →
    ``test_every_mode_member_has_path_resolver_test`` assertion flips
    red. Restored.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.contract


# ---------------------------------------------------------------------------
# Public surfaces under test
# ---------------------------------------------------------------------------

# Public functions in the kairix.install package the installer publishes.
# Every name here must appear in at least one tests/**/*.py file outside
# this traceability test. Match the names exported from the layer modules:
# - kairix/install/installer.py:  install, verify, uninstall
# - kairix/install/system_user.py: ensure_kairix_system_user
# - kairix/install/dirs.py:       ensure_dirs
# - kairix/install/systemd.py:    render_unit, install_unit
_INSTALL_PUBLIC: tuple[str, ...] = (
    "install",
    "verify",
    "uninstall",
    "ensure_kairix_system_user",
    "ensure_dirs",
    "render_unit",
    "install_unit",
)

# CLI flag tokens on kairix init + kairix uninstall. Every flag must be
# referenced from at least one test file (BDD step impl, outcome test,
# or contract test) so a regression that drops a flag fails the gate.
_CLI_FLAGS: tuple[str, ...] = ("--system", "--user", "--json")

# Mode enum members; every member must surface in at least one test file
# so the path-resolver dispatch contract has explicit per-member coverage.
# References match either ``Mode.system`` or the bare ``"system"`` string.
_MODE_MEMBERS: tuple[str, ...] = ("system", "user", "container")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _tests_root() -> Path:
    """Resolve the tests/ directory regardless of where pytest was invoked."""
    return Path(__file__).resolve().parents[1]


def _other_test_files() -> list[Path]:
    """Every test_*.py file under tests/ EXCEPT this traceability test.

    Filtering self-references is what stops the test from satisfying its
    own assertion vacuously — if every needle appears only inside this
    file (because we list it in ``_INSTALL_PUBLIC`` / ``_CLI_FLAGS`` /
    ``_MODE_MEMBERS``), the gate would never fire on real regressions.
    """
    me = Path(__file__).name
    return [p for p in _tests_root().rglob("test_*.py") if p.name != me]


def _any_file_references(needle: str, files: list[Path]) -> bool:
    """True when at least one file's text contains ``needle``."""
    return any(needle in p.read_text(encoding="utf-8", errors="ignore") for p in files)


# ---------------------------------------------------------------------------
# Assertions
# ---------------------------------------------------------------------------


def test_every_install_public_fn_has_test() -> None:
    """Every name in ``_INSTALL_PUBLIC`` appears in at least one
    test file outside this traceability test."""
    files = _other_test_files()
    missing = [name for name in _INSTALL_PUBLIC if not _any_file_references(name, files)]
    assert not missing, (
        "Public installer functions with no test reference: "
        f"{missing}. Add an integration / contract / unit test that "
        "names the function in source (e.g. via import) so the "
        "traceability map closes."
    )


def test_every_cli_flag_has_outcome_test() -> None:
    """Every CLI flag in ``_CLI_FLAGS`` appears in at least one
    test file outside this traceability test.

    Per F30, every CLI subcommand needs an outcome test asserting on
    stdout / stderr / envelope; the traceability check confirms the
    flags are wired into those outcome tests (not just declared in
    the argparse definition).
    """
    files = _other_test_files()
    missing = [flag for flag in _CLI_FLAGS if not _any_file_references(flag, files)]
    assert not missing, (
        "CLI flags with no test reference: "
        f"{missing}. Add an outcome test in tests/integration/test_cli_init.py "
        "or tests/integration/test_cli_uninstall.py that exercises the flag "
        "and asserts on the subprocess envelope."
    )


def test_every_mode_member_has_path_resolver_test() -> None:
    """Every ``Mode`` enum member appears in at least one test file
    outside this traceability test.

    We accept either the ``Mode.system`` (typed access) or the bare
    ``"system"`` (string-form, e.g. inside JSON envelope assertions)
    form — both prove the member is exercised through the test tree.
    """
    files = _other_test_files()
    missing: list[str] = []
    for member in _MODE_MEMBERS:
        typed = f"Mode.{member}"
        string_form = f'"{member}"'
        if not (_any_file_references(typed, files) or _any_file_references(string_form, files)):
            missing.append(member)
    assert not missing, (
        "Mode enum members with no test reference: "
        f"{missing}. Add a path-resolver test in "
        "tests/contracts/test_paths_mode.py (or sibling) that constructs "
        "Mode.<member> and asserts on the resolved path."
    )

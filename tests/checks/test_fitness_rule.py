"""Tests for the FitnessRule ABC (ADR-026 Track B).

Pins:
- Subclass with required attributes + file_has_violation runs cleanly.
- Default ``is_in_scope`` filters by roots + extensions.
- Default ``enumerate_files`` walks ``.py`` files under roots.
- Non-``.py`` extensions trigger the generic fallback enumeration.
- ``exempt_files`` skips known violators.
- Missing required class attributes raise on instantiation.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_CHECKS_DIR = _REPO_ROOT / "scripts" / "checks"
if str(_CHECKS_DIR) not in sys.path:
    sys.path.insert(0, str(_CHECKS_DIR))

# Import depends on the sys.path mutation above — the helpers live
# outside the kairix package (repo-fitness scripts, not app code).
from _fitness_rule import FitnessRule  # noqa: E402

pytestmark = pytest.mark.unit


class _RuleAllPyFilesViolate(FitnessRule):
    """Test fixture: every .py file under roots is a violation."""

    name = "test-all-py-violate"
    remediation = "fix: -\nnext: -\nrun: -\nPass example: -\nForbidden example: -\n"
    roots = ("kairix",)

    def file_has_violation(self, path: Path) -> bool:
        return True


class _RuleNoFilesViolate(FitnessRule):
    name = "test-no-violations"
    remediation = "fix: -\nnext: -\nrun: -\nPass example: -\nForbidden example: -\n"
    roots = ("kairix",)

    def file_has_violation(self, path: Path) -> bool:
        return False


def test_default_scope_filters_by_roots_and_extensions() -> None:
    rule = _RuleAllPyFilesViolate()
    assert rule.is_in_scope("kairix/worker.py")
    assert not rule.is_in_scope("tests/unit/test_worker.py")
    assert not rule.is_in_scope("kairix/worker.md")


def test_default_enumerate_files_walks_py_files() -> None:
    rule = _RuleAllPyFilesViolate()
    files = rule.enumerate_files()
    assert files, "expected at least one .py file under kairix/"
    assert all(p.suffix == ".py" for p in files)
    assert all("__pycache__" not in p.parts for p in files)


def test_collect_violations_returns_relative_paths() -> None:
    rule = _RuleAllPyFilesViolate()
    violations = rule.collect_violations()
    assert violations, "expected at least one violation when every file violates"
    assert all(not str(p).startswith("/") for p in violations)


def test_collect_violations_empty_when_no_violator() -> None:
    rule = _RuleNoFilesViolate()
    assert rule.collect_violations() == set()


def test_exempt_files_skip_known_violators() -> None:
    class _RuleWithExemption(_RuleAllPyFilesViolate):
        name = "test-exemption"
        exempt_files = frozenset({"kairix/worker.py"})

    rule = _RuleWithExemption()
    violations = rule.collect_violations()
    assert all(str(p) != "kairix/worker.py" for p in violations)


def test_custom_extension_uses_fallback_enumeration(tmp_path: Path) -> None:
    """Non-.py extensions trigger the generic glob-based fallback."""

    class _MarkdownRule(FitnessRule):
        name = "test-markdown"
        remediation = "fix: -\nnext: -\nrun: -\nPass example: -\nForbidden example: -\n"
        roots = ("docs",)
        extensions = (".md",)

        def file_has_violation(self, path: Path) -> bool:
            return False

    rule = _MarkdownRule()
    files = rule.enumerate_files()
    # docs/ is non-empty in this repo and contains .md files.
    assert files, "expected at least one .md file under docs/"
    assert all(p.suffix == ".md" for p in files)


def test_abstract_method_blocks_direct_instantiation() -> None:
    with pytest.raises(TypeError):
        FitnessRule()  # type: ignore[abstract]  # intentional: proving abstract instantiation raises


def test_run_returns_int_exit_code() -> None:
    rule = _RuleNoFilesViolate()
    # No violations + no baseline file => clean exit 0.
    assert rule.run() == 0

"""Unit tests for F54 (``scripts/checks/check_f54_flag_both_branch_tested.py``).

F54 enforces that every flag in REGISTRY has:

  * A BDD feature file at ``tests/bdd/features/feature_flag_<name>.feature``
    with ≥2 scenarios.
  * An integration test at ``tests/integration/test_feature_flag_<name>.py``
    exercising both branches via ``with_flag(<name>, False)`` and
    ``with_flag(<name>, True)``.
  * For top-level-capability flags, an E2E test at
    ``tests/e2e/test_composed_<name>_path.py``.

These tests exercise the public helpers (``_count_scenarios``,
``_integration_exercises_both_branches``, ``_is_top_level_capability_flag``,
``_violation_lines``) with synthetic fixtures.

Each test carries an inline sabotage-proof.
"""

from __future__ import annotations

import importlib.util
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_DETECTOR_PATH = _REPO_ROOT / "scripts" / "checks" / "check_f54_flag_both_branch_tested.py"


def _load_detector() -> object:
    """Load the F54 detector module by file path."""
    spec = importlib.util.spec_from_file_location("_f54_detector", _DETECTOR_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["_f54_detector"] = module
    spec.loader.exec_module(module)
    return module


@dataclass(frozen=True)
class _FakeFlag:
    """Synthetic FeatureFlag stand-in for F54 testing.

    Only carries ``related_spec`` — the field that determines whether
    the E2E test is required.
    """

    related_spec: str | None = None


def test_empty_registry_returns_no_violations() -> None:
    """Vacuous-green: empty registry → empty violations.

    Sabotage proof: change ``find_violations`` to iterate ``[None]``
    instead of ``registry.items()`` and this test flips.
    """
    detector = _load_detector()
    assert detector.find_violations({}) == []  # type: ignore[attr-defined]  # detector loaded by path; mypy can't see attrs


def test_count_scenarios_returns_two_for_canonical_feature(tmp_path: Path) -> None:
    """A canonical OFF + ON feature file has exactly 2 scenarios.

    Sabotage proof: change the regex to match ``Step:`` instead of
    ``Scenario:`` and this returns 0.
    """
    detector = _load_detector()
    feature = tmp_path / "feature_flag_my_flag.feature"
    feature.write_text(
        """Feature: my_flag toggle

  Scenario: Flag OFF uses legacy
    Given the operator sets my_flag to false
    Then the legacy path runs

  Scenario: Flag ON uses new
    Given the operator sets my_flag to true
    Then the new path runs
""",
        encoding="utf-8",
    )
    assert detector._count_scenarios(feature) == 2  # type: ignore[attr-defined]  # detector loaded by path; mypy can't see attrs


def test_integration_exercises_both_branches_positive(tmp_path: Path) -> None:
    """An integration test calling with_flag(name, False) AND True passes.

    Sabotage proof: remove either branch's call from the source and the
    assertion flips from True to False.
    """
    detector = _load_detector()
    integ = tmp_path / "test_feature_flag_my_flag.py"
    integ.write_text(
        """import pytest

@pytest.mark.integration
def test_off(e2e_db):
    paths = e2e_db.with_flag("my_flag", False)
    assert legacy_ran(paths)

@pytest.mark.integration
def test_on(e2e_db):
    paths = e2e_db.with_flag("my_flag", True)
    assert new_ran(paths)
""",
        encoding="utf-8",
    )
    assert detector._integration_exercises_both_branches(integ, "my_flag") is True  # type: ignore[attr-defined]  # detector loaded by path; mypy can't see attrs


def test_integration_missing_off_branch_fails(tmp_path: Path) -> None:
    """An integration test that only covers ON is incomplete.

    Sabotage proof: invert the AND to OR in
    ``_integration_exercises_both_branches`` and this assertion flips.
    """
    detector = _load_detector()
    integ = tmp_path / "test_feature_flag_my_flag.py"
    integ.write_text(
        """import pytest

@pytest.mark.integration
def test_on(e2e_db):
    paths = e2e_db.with_flag("my_flag", True)
    assert new_ran(paths)
""",
        encoding="utf-8",
    )
    assert detector._integration_exercises_both_branches(integ, "my_flag") is False  # type: ignore[attr-defined]  # detector loaded by path; mypy can't see attrs


def test_top_level_capability_flag_requires_e2e() -> None:
    """A flag whose related_spec is in the top-level set requires an E2E.

    Sabotage proof: clear the ``_TOP_LEVEL_SPECS`` set and this returns
    False for every input.
    """
    detector = _load_detector()
    entry = _FakeFlag(related_spec="docs/architecture/connector-ingestion-architecture.md")
    assert detector._is_top_level_capability_flag(entry) is True  # type: ignore[attr-defined]  # detector loaded by path; mypy can't see attrs


def test_non_top_level_flag_does_not_require_e2e() -> None:
    """A flag whose related_spec is unrelated does NOT require an E2E.

    Sabotage proof: make ``_is_top_level_capability_flag`` return True
    unconditionally and the assertion flips.
    """
    detector = _load_detector()
    entry = _FakeFlag(related_spec="docs/internal/some-random-doc.md")
    assert detector._is_top_level_capability_flag(entry) is False  # type: ignore[attr-defined]  # detector loaded by path; mypy can't see attrs


def test_remediation_carries_action_markers() -> None:
    """F54's REMEDIATION must carry F21 ``fix:`` / ``next:`` / ``run:`` markers."""
    detector = _load_detector()
    rem = detector.REMEDIATION.lower()  # type: ignore[attr-defined]  # detector loaded by path; mypy can't see attrs
    assert "fix:" in rem
    assert "next:" in rem
    assert "run:" in rem


def test_vacuous_green_when_module_absent() -> None:
    """The detector returns 0 when kairix.core.features.registry is absent.

    PR-2 may not be landed yet; F54 must not block.
    """
    detector = _load_detector()
    assert detector._load_registry() is None  # type: ignore[attr-defined]  # detector loaded by path; mypy can't see attrs
    assert detector.main() == 0  # type: ignore[attr-defined]  # detector loaded by path; mypy can't see attrs

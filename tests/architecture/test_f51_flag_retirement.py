"""Unit tests for F51 (``scripts/checks/check_f51_flag_retirement.py``).

F51 enforces that every ``FeatureFlag`` in the registry has a
``target_retire_in`` within 6 months of the current ``setuptools-scm``
version. Past that deadline, the gate fires unless a
``# retire-extension: <reason>`` comment is adjacent to the entry.

These tests exercise the public ``find_violations`` entry point with
synthetic registries + synthetic registry source, so the rule is
provable without depending on PR-2 having landed.

Each test carries an inline sabotage-proof — mutate the production code
and re-run; the assertion flips.
"""

from __future__ import annotations

import importlib.util
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_DETECTOR_PATH = _REPO_ROOT / "scripts" / "checks" / "check_f51_flag_retirement.py"


def _load_detector() -> object:
    """Load the F51 detector module by file path."""
    spec = importlib.util.spec_from_file_location("_f51_detector", _DETECTOR_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["_f51_detector"] = module
    spec.loader.exec_module(module)
    return module


@dataclass(frozen=True)
class _FakeFlag:
    """Synthetic FeatureFlag stand-in for testing F51 logic.

    Carries only the field F51 inspects (``target_retire_in``); the real
    dataclass has more shape but F51 only reads this one attribute.
    """

    target_retire_in: str


def test_empty_registry_returns_no_violations() -> None:
    """Vacuous-green: an empty registry produces zero violations.

    Sabotage proof: change ``find_violations`` to iterate ``[None]``
    instead of ``registry.items()`` and this test surfaces the regression.
    """
    detector = _load_detector()
    result = detector.find_violations({}, "", "v2026.5.22")  # type: ignore[attr-defined]  # detector loaded by path; mypy can't see attrs
    assert result == []


def test_flag_past_deadline_without_extension_flagged() -> None:
    """A flag with a deadline >6 months out from current version fires.

    Current version: ``v2026.5.22``. Deadline window: 6 months → ``v2026.11.22``.
    A flag with ``target_retire_in="v2027.5.22"`` (a year out) violates.

    Sabotage proof: mutate ``_add_six_months`` to add 12 months and this
    assertion goes from list-of-one to empty.
    """
    detector = _load_detector()
    registry = {"long_lived_flag": _FakeFlag(target_retire_in="v2027.5.22")}
    # Source text contains no extension comment.
    src = """REGISTRY = {
    "long_lived_flag": FeatureFlag(
        target_retire_in="v2027.5.22",
    ),
}
"""
    result = detector.find_violations(registry, src, "v2026.5.22")  # type: ignore[attr-defined]  # detector loaded by path; mypy can't see attrs
    assert result == ["kairix/core/features/registry.py:flag=long_lived_flag"]


def test_retire_extension_comment_exempts_flag() -> None:
    """A ``# retire-extension: <reason>`` comment above the entry exempts it.

    Same flag, same deadline, but the registry source carries the
    rationale comment immediately above the entry. F51 must not fire.

    Sabotage proof: change ``_find_extension_comment_lines`` to return
    ``set()`` unconditionally and this assertion goes from empty list to
    a violation entry.
    """
    detector = _load_detector()
    registry = {"long_lived_flag": _FakeFlag(target_retire_in="v2027.5.22")}
    src = """REGISTRY = {
    # retire-extension: blocked on Wave 5 dispatch; bumped 2026-05-22
    "long_lived_flag": FeatureFlag(
        target_retire_in="v2027.5.22",
    ),
}
"""
    result = detector.find_violations(registry, src, "v2026.5.22")  # type: ignore[attr-defined]  # detector loaded by path; mypy can't see attrs
    assert result == []


def test_flag_within_deadline_not_flagged() -> None:
    """A flag whose deadline is inside the 6-month window is healthy.

    Sabotage proof: invert the comparison in ``find_violations`` (use
    ``<`` instead of ``>``) and this assertion flips.
    """
    detector = _load_detector()
    registry = {"short_flag": _FakeFlag(target_retire_in="v2026.7.22")}  # 2 months out
    result = detector.find_violations(registry, "", "v2026.5.22")  # type: ignore[attr-defined]  # detector loaded by path; mypy can't see attrs
    assert result == []


def test_remediation_carries_action_markers() -> None:
    """F51's REMEDIATION must carry F21 ``fix:`` / ``next:`` / ``run:`` markers."""
    detector = _load_detector()
    rem = detector.REMEDIATION.lower()  # type: ignore[attr-defined]  # detector loaded by path; mypy can't see attrs
    assert "fix:" in rem
    assert "next:" in rem
    assert "run:" in rem


def test_vacuous_green_when_module_absent() -> None:
    """When ``kairix.core.features.registry`` cannot be imported, the
    gate stays green (vacuous). PR-2 may not be landed yet.

    Verified via the real-repo invocation: ``main()`` returns 0 when
    the module is absent (the current state in this worktree).
    """
    detector = _load_detector()
    # _load_registry returns None when the module is absent.
    assert detector._load_registry() is None  # type: ignore[attr-defined]  # detector loaded by path; mypy can't see attrs
    assert detector.main() == 0  # type: ignore[attr-defined]  # detector loaded by path; mypy can't see attrs

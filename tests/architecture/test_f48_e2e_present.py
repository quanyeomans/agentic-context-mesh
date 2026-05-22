"""Unit tests for F48 (``scripts/checks/check_f48_e2e_present.py``).

F48 enforces a binary presence + decorator contract on the canonical
E2E exemplar at ``tests/e2e/test_composed_production_path.py``. These
tests prove:

  1. The exemplar exists and carries ``@pytest.mark.e2e`` (real-repo gate
     is green today).
  2. The detector fires when the exemplar is absent (sabotage proof via
     a tmpdir-rooted detector invocation).
  3. The remediation text carries the F21 ``fix:`` / ``next:`` / ``run:``
     action markers so an agent reading the failure gets the next step.
"""

from __future__ import annotations

import ast
import importlib.util
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_DETECTOR_PATH = _REPO_ROOT / "scripts" / "checks" / "check_f48_e2e_present.py"
_EXEMPLAR_PATH = _REPO_ROOT / "tests" / "e2e" / "test_composed_production_path.py"


def _load_detector() -> object:
    """Load the F48 detector module by file path."""
    spec = importlib.util.spec_from_file_location("_f48_detector", _DETECTOR_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["_f48_detector"] = module
    spec.loader.exec_module(module)
    return module


def test_exemplar_exists_and_has_e2e_marker() -> None:
    """The canonical exemplar exists and AST-parses to at least one
    ``@pytest.mark.e2e``-decorated function — F48's positive contract.

    Asserting directly on the file (not via the detector) so a failure
    here pinpoints the exemplar itself rather than the detector wiring.
    """
    assert _EXEMPLAR_PATH.exists(), (
        f"F48 exemplar missing at {_EXEMPLAR_PATH.relative_to(_REPO_ROOT)} — "
        "see docs/architecture/test-discipline-hardening.md §4.3 for the canonical shape."
    )
    tree = ast.parse(_EXEMPLAR_PATH.read_text())
    has_e2e = False
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        for dec in node.decorator_list:
            # `@pytest.mark.e2e` — Attribute(Attribute(Name('pytest'), 'mark'), 'e2e')
            if (
                isinstance(dec, ast.Attribute)
                and dec.attr == "e2e"
                and isinstance(dec.value, ast.Attribute)
                and dec.value.attr == "mark"
                and isinstance(dec.value.value, ast.Name)
                and dec.value.value.id == "pytest"
            ):
                has_e2e = True
                break
        if has_e2e:
            break
    assert has_e2e, (
        f"F48 exemplar at {_EXEMPLAR_PATH.relative_to(_REPO_ROOT)} has no "
        "@pytest.mark.e2e decorator on any test function."
    )


def test_real_repo_gate_is_green() -> None:
    """The F48 detector against the live repo returns 0 (gate is green)."""
    detector = _load_detector()
    assert detector.main() == 0  # type: ignore[attr-defined]  # detector loaded by path; mypy can't see attrs


def test_detector_flags_absence(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """When the exemplar is absent, the detector returns non-zero.

    Re-roots the detector's ``REPO_ROOT`` at a fresh ``tmp_path`` with no
    ``tests/e2e/test_composed_production_path.py``, then asserts
    ``main()`` returns 1. This is the sabotage-proof: the production
    detector must fire when the binary contract is broken.
    """
    detector = _load_detector()
    # Re-root detector at an empty tmp_path — the exemplar won't exist there.
    monkeypatch.setattr(detector, "REPO_ROOT", tmp_path)  # type: ignore[arg-type]  # detector loaded by path; mypy can't see attrs
    assert detector.main() == 1  # type: ignore[attr-defined]  # detector loaded by path; mypy can't see attrs


def test_remediation_carries_action_markers() -> None:
    """F48's REMEDIATION must satisfy F21 — the failure output carries
    inline ``fix:`` / ``next:`` / ``run:`` action markers so the agent
    reading the failure gets the correction step, not just the diagnosis.
    """
    detector = _load_detector()
    rem = detector.REMEDIATION.lower()  # type: ignore[attr-defined]  # detector loaded by path; mypy can't see attrs
    assert "fix:" in rem, "F48 remediation should carry a 'fix:' action marker"
    assert "next:" in rem, "F48 remediation should carry a 'next:' action marker"
    assert "run:" in rem, "F48 remediation should carry a 'run:' action marker"

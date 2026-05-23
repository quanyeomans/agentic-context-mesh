"""Unit tests for F58 (``scripts/checks/check_f58_hierarchy_parent_before_child.py``).

F58 enforces: when a class named ``HierarchyConnector`` exists in
``kairix/**/*.py``, at least one test under ``tests/contracts/`` must
have a function name matching ``test_*hierarchy*parent_before_child*``
AND reference ``HierarchyConnector`` in its source.

Drives ``collect_violations(repo_root)`` against synthetic trees under
``tmp_path``. Each scenario carries an inline executed sabotage-proof.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_DETECTOR_PATH = _REPO_ROOT / "scripts" / "checks" / "check_f58_hierarchy_parent_before_child.py"


def _load_detector() -> object:
    """Load the F58 detector module by file path."""
    spec = importlib.util.spec_from_file_location("_f58_detector", _DETECTOR_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["_f58_detector"] = module
    spec.loader.exec_module(module)
    return module


def _write(path: Path, body: str = "") -> None:
    """Create ``path`` with parent dirs and write ``body``."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


def test_real_repo_gate_is_green() -> None:
    """The real F58 detector on the full repo emits no violations.

    Pre-Wave-E no HierarchyConnector class exists in production code,
    so F58 is vacuous-green. Sabotage proof: synthesize a temporary
    HierarchyConnector class under ``kairix/`` and re-run — the gate
    fires because no satisfying contract test exists.
    """
    detector = _load_detector()
    assert detector.main() == 0  # type: ignore[attr-defined]  # detector loaded by path; mypy can't see attrs


def test_vacuous_when_no_kairix_dir(tmp_path: Path) -> None:
    """Fresh tree with no ``kairix/`` directory: gate stays green."""
    detector = _load_detector()
    assert detector.collect_violations(tmp_path) == set()  # type: ignore[attr-defined]  # detector loaded by path; mypy can't see attrs


def test_no_production_protocol_means_no_violation(tmp_path: Path) -> None:
    """When production code does not declare ``HierarchyConnector`` the
    gate is green — even without a contract test."""
    detector = _load_detector()
    _write(tmp_path / "kairix" / "core" / "protocols.py", "class SomeOther: pass\n")
    violations = detector.collect_violations(tmp_path)  # type: ignore[attr-defined]  # detector loaded by path; mypy can't see attrs
    assert violations == set()


def test_protocol_without_contract_test_is_flagged(tmp_path: Path) -> None:
    """A ``class HierarchyConnector`` in production code without a
    matching ``test_*hierarchy*parent_before_child*`` contract test fires.

    Sabotage-proof: add the contract test → clean.
    """
    detector = _load_detector()
    _write(
        tmp_path / "kairix" / "core" / "protocols.py",
        "from typing import Protocol\nclass HierarchyConnector(Protocol):\n    def iter_containers(self): ...\n",
    )
    violations = detector.collect_violations(tmp_path)  # type: ignore[attr-defined]  # detector loaded by path; mypy can't see attrs
    assert violations  # non-empty

    # Sabotage executed: add the contract test → clean.
    _write(
        tmp_path / "tests" / "contracts" / "test_hierarchy_emission.py",
        "from kairix.core.protocols import HierarchyConnector\n"
        "\n"
        "def test_hierarchy_parent_before_child_invariant():\n"
        "    pass\n",
    )
    violations = detector.collect_violations(tmp_path)  # type: ignore[attr-defined]  # detector loaded by path; mypy can't see attrs
    assert violations == set()


def test_contract_test_must_reference_hierarchy_connector(tmp_path: Path) -> None:
    """A test whose name matches the pattern but does NOT reference the
    ``HierarchyConnector`` symbol anywhere in its source doesn't satisfy F58.
    """
    detector = _load_detector()
    _write(
        tmp_path / "kairix" / "core" / "protocols.py",
        "class HierarchyConnector: pass\n",
    )
    # Note: deliberately no reference to the canonical class name anywhere
    # in this test module's source — F58 must reject.
    _write(
        tmp_path / "tests" / "contracts" / "test_hierarchy_emission.py",
        "def test_hierarchy_parent_before_child_invariant():\n    pass\n",
    )
    violations = detector.collect_violations(tmp_path)  # type: ignore[attr-defined]  # detector loaded by path; mypy can't see attrs
    assert violations  # still non-empty


def test_contract_test_name_must_match_pattern(tmp_path: Path) -> None:
    """A test that references ``HierarchyConnector`` but has the wrong
    function-name shape doesn't satisfy F58 either.
    """
    detector = _load_detector()
    _write(
        tmp_path / "kairix" / "core" / "protocols.py",
        "class HierarchyConnector: pass\n",
    )
    _write(
        tmp_path / "tests" / "contracts" / "test_hierarchy_emission.py",
        "from kairix.core.protocols import HierarchyConnector\n\ndef test_hierarchy_basic():\n    pass\n",
    )
    violations = detector.collect_violations(tmp_path)  # type: ignore[attr-defined]  # detector loaded by path; mypy can't see attrs
    assert violations  # still non-empty


def test_satisfying_test_outside_contracts_doesnt_count(tmp_path: Path) -> None:
    """A satisfying test must live under ``tests/contracts/``. The same
    test under ``tests/unit/`` doesn't satisfy F58.
    """
    detector = _load_detector()
    _write(
        tmp_path / "kairix" / "core" / "protocols.py",
        "class HierarchyConnector: pass\n",
    )
    _write(
        tmp_path / "tests" / "unit" / "test_hierarchy_emission.py",
        "from kairix.core.protocols import HierarchyConnector\n"
        "\n"
        "def test_hierarchy_parent_before_child_invariant():\n"
        "    pass\n",
    )
    violations = detector.collect_violations(tmp_path)  # type: ignore[attr-defined]  # detector loaded by path; mypy can't see attrs
    assert violations  # tests/contracts/ is required


def test_remediation_carries_action_markers() -> None:
    """F58's REMEDIATION must satisfy F21."""
    detector = _load_detector()
    rem = detector.REMEDIATION.lower()  # type: ignore[attr-defined]  # detector loaded by path; mypy can't see attrs
    assert "fix:" in rem
    assert "next:" in rem
    assert "run:" in rem

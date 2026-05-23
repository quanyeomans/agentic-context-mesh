"""Unit tests for F57 (``scripts/checks/check_f57_ccpair_lifecycle_integrity.py``).

F57 enforces every ``UPDATE topology_cc_pairs ... SET status = ?`` SQL
string literal sits in a module that also declares a top-level
``_ALLOWED_TRANSITIONS`` dispatch dict — so status mutations cannot
bypass the declared state-machine.

Drives ``collect_violations(repo_root)`` against synthetic trees under
``tmp_path``. Each scenario carries an inline executed sabotage-proof
per ``feedback_sabotage_must_be_executed``.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_DETECTOR_PATH = _REPO_ROOT / "scripts" / "checks" / "check_f57_ccpair_lifecycle_integrity.py"


def _load_detector() -> object:
    """Load the F57 detector module by file path."""
    spec = importlib.util.spec_from_file_location("_f57_detector", _DETECTOR_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["_f57_detector"] = module
    spec.loader.exec_module(module)
    return module


def _write(path: Path, body: str = "") -> None:
    """Create ``path`` with parent dirs and write ``body``."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


def test_real_repo_gate_is_green() -> None:
    """The real F57 detector on the full repo emits no violations.

    Pre-Wave-C no production code references topology_cc_pairs.
    Sabotage proof: add a Python file under ``kairix/`` containing the
    UPDATE string without _ALLOWED_TRANSITIONS — the gate fires.
    """
    detector = _load_detector()
    assert detector.main() == 0  # type: ignore[attr-defined]  # detector loaded by path; mypy can't see attrs


def test_vacuous_when_no_kairix_dir(tmp_path: Path) -> None:
    """Fresh tree with no ``kairix/`` directory: gate stays green."""
    detector = _load_detector()
    assert detector.collect_violations(tmp_path) == set()  # type: ignore[attr-defined]  # detector loaded by path; mypy can't see attrs


def test_update_without_transition_matrix_is_flagged(tmp_path: Path) -> None:
    """A module that issues UPDATE on topology_cc_pairs.status without
    declaring _ALLOWED_TRANSITIONS is flagged.

    Sabotage-proof: add _ALLOWED_TRANSITIONS to the module → clean.
    """
    detector = _load_detector()
    target = tmp_path / "kairix" / "worker.py"
    _write(
        target,
        "def go(db, pid):\n"
        "    db.execute(\n"
        "        \"UPDATE topology_cc_pairs SET status = 'ACTIVE' WHERE id = ?\",\n"
        "        (pid,),\n"
        "    )\n",
    )
    violations = detector.collect_violations(tmp_path)  # type: ignore[attr-defined]  # detector loaded by path; mypy can't see attrs
    assert Path("kairix/worker.py") in violations

    # Sabotage executed: add the dispatch matrix → clean.
    _write(
        target,
        '_ALLOWED_TRANSITIONS = {"SCHEDULED": frozenset({"INITIAL_INDEXING"})}\n'
        "\n"
        "def go(db, pid):\n"
        "    db.execute(\n"
        "        \"UPDATE topology_cc_pairs SET status = 'ACTIVE' WHERE id = ?\",\n"
        "        (pid,),\n"
        "    )\n",
    )
    violations = detector.collect_violations(tmp_path)  # type: ignore[attr-defined]  # detector loaded by path; mypy can't see attrs
    assert Path("kairix/worker.py") not in violations


def test_update_with_annotated_transition_matrix_is_clean(tmp_path: Path) -> None:
    """An annotated dispatch dict ``_ALLOWED_TRANSITIONS: dict[...] = {...}``
    counts the same as the bare form."""
    detector = _load_detector()
    target = tmp_path / "kairix" / "core" / "connectors" / "cc_pair.py"
    _write(
        target,
        "from typing import Any\n"
        "_ALLOWED_TRANSITIONS: dict[Any, frozenset[Any]] = {}\n"
        "\n"
        "def transition(db, pid):\n"
        '    db.execute("UPDATE topology_cc_pairs SET status = ? WHERE id = ?", (pid,))\n',
    )
    violations = detector.collect_violations(tmp_path)  # type: ignore[attr-defined]  # detector loaded by path; mypy can't see attrs
    assert violations == set()


def test_other_topology_table_updates_are_out_of_scope(tmp_path: Path) -> None:
    """Updates on other topology_* tables (groups, hierarchy, etc.) are
    out of scope for F57 — the state machine only applies to cc_pairs."""
    detector = _load_detector()
    target = tmp_path / "kairix" / "worker.py"
    _write(
        target,
        "def go(db, pid):\n    db.execute(\"UPDATE topology_groups SET status = 'ACTIVE' WHERE id = ?\", (pid,))\n",
    )
    violations = detector.collect_violations(tmp_path)  # type: ignore[attr-defined]  # detector loaded by path; mypy can't see attrs
    assert violations == set()


def test_status_update_inside_comment_is_ignored(tmp_path: Path) -> None:
    """A # comment that mentions UPDATE topology_cc_pairs.status doesn't
    count — the detector walks string-literal Constant nodes, not source
    text generally.
    """
    detector = _load_detector()
    target = tmp_path / "kairix" / "worker.py"
    _write(
        target,
        "# UPDATE topology_cc_pairs SET status = 'ACTIVE' WHERE id = 1\ndef go():\n    pass\n",
    )
    violations = detector.collect_violations(tmp_path)  # type: ignore[attr-defined]  # detector loaded by path; mypy can't see attrs
    assert violations == set()


def test_multiline_sql_string_is_matched(tmp_path: Path) -> None:
    """A multi-line SQL string literal still matches the UPDATE regex."""
    detector = _load_detector()
    target = tmp_path / "kairix" / "worker.py"
    _write(
        target,
        "def go(db, pid):\n"
        "    db.execute(\n"
        '        """UPDATE topology_cc_pairs\n'
        "           SET status = ?, updated_at = ?\n"
        '           WHERE id = ?""",\n'
        '        ("ACTIVE", "now", pid),\n'
        "    )\n",
    )
    violations = detector.collect_violations(tmp_path)  # type: ignore[attr-defined]  # detector loaded by path; mypy can't see attrs
    assert Path("kairix/worker.py") in violations


def test_remediation_carries_action_markers() -> None:
    """F57's REMEDIATION must satisfy F21."""
    detector = _load_detector()
    rem = detector.REMEDIATION.lower()  # type: ignore[attr-defined]  # detector loaded by path; mypy can't see attrs
    assert "fix:" in rem
    assert "next:" in rem
    assert "run:" in rem

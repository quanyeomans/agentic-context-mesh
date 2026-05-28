"""Unit tests for F44 (``scripts/checks/check_f44_engagement_firm_boundary.py``).

F44 forbids engagement-scope code (``kairix/**``) from importing firm-scope
Postgres clients. The two-scope architecture keeps cross-engagement state
in a separate Postgres-only codebase; engagement code that imports
``psycopg`` / ``psycopg2`` / ``asyncpg`` / ``pg8000`` / ``aiopg`` has
reached across the scope boundary.

Each test has an inline sabotage-proof: introduce a violation, confirm
the detector flags it; remove the violation, confirm the detector clears.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_DETECTOR_PATH = _REPO_ROOT / "scripts" / "checks" / "check_f44_engagement_firm_boundary.py"


def _load_detector():
    """Load the F44 detector module by file path."""
    spec = importlib.util.spec_from_file_location("_f44_detector", _DETECTOR_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["_f44_detector"] = module
    spec.loader.exec_module(module)
    return module


def _write(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


def test_real_repo_gate_is_vacuous_green() -> None:
    """The real F44 detector run against the full repo emits no
    violations — no engagement-scope code currently imports a firm-scope
    Postgres client. Locks the preventive guarantee.
    """
    detector = _load_detector()
    assert detector.collect_violations() == set()
    assert detector.main() == 0


def test_import_psycopg_in_kairix_is_flagged(tmp_path: Path) -> None:
    """``import psycopg`` from anywhere under ``kairix/`` fires F44.

    Sabotage-proof inline: rewrite the import as ``import sqlite3``;
    the flag clears (SQLite is engagement-scope and welcome).
    """
    detector = _load_detector()
    target = tmp_path / "kairix" / "foo.py"
    _write(target, "import psycopg\n")
    violations = detector.collect_violations(tmp_path)
    assert Path("kairix/foo.py") in violations

    # Sabotage: switch to the engagement-scope storage client.
    _write(target, "import sqlite3\n")
    assert detector.collect_violations(tmp_path) == set()


def test_sqlite3_import_is_not_flagged(tmp_path: Path) -> None:
    """``sqlite3`` is the engagement-scope storage client — explicitly
    allowed. The whole point of F44 is to gate Postgres, not SQLite.
    """
    detector = _load_detector()
    target = tmp_path / "kairix" / "core" / "store.py"
    _write(target, "import sqlite3\nfrom neo4j import GraphDatabase\n")
    assert detector.collect_violations(tmp_path) == set()


def test_from_psycopg2_import_connect_is_flagged(tmp_path: Path) -> None:
    """``from psycopg2 import connect`` fires F44 — the
    ``ImportFrom`` form is detected just like the ``Import`` form.

    Sabotage-proof inline: rewrite as ``from sqlite3 import connect``;
    the flag clears.
    """
    detector = _load_detector()
    target = tmp_path / "kairix" / "foo.py"
    _write(target, "from psycopg2 import connect\n")
    violations = detector.collect_violations(tmp_path)
    assert Path("kairix/foo.py") in violations

    # Sabotage: SQLite is engagement-scope.
    _write(target, "from sqlite3 import connect\n")
    assert detector.collect_violations(tmp_path) == set()


def test_psycopg2_submodule_import_is_flagged(tmp_path: Path) -> None:
    """``from psycopg2.extras import RealDictCursor`` fires F44 — the
    detector matches on dotted prefix, so submodule imports of a
    denylisted package don't slip through.
    """
    detector = _load_detector()
    target = tmp_path / "kairix" / "foo.py"
    _write(target, "from psycopg2.extras import RealDictCursor\n")
    violations = detector.collect_violations(tmp_path)
    assert Path("kairix/foo.py") in violations


def test_asyncpg_import_is_flagged(tmp_path: Path) -> None:
    """``asyncpg`` is on the denylist alongside ``psycopg`` /
    ``psycopg2``.
    """
    detector = _load_detector()
    target = tmp_path / "kairix" / "core" / "async_store.py"
    _write(target, "import asyncpg\n")
    violations = detector.collect_violations(tmp_path)
    assert Path("kairix/core/async_store.py") in violations


def test_pg8000_and_aiopg_are_flagged(tmp_path: Path) -> None:
    """The remaining denylist entries (``pg8000``, ``aiopg``) fire F44.

    Sabotage-proof inline: rewrite each as a plain stdlib import; the
    flag clears.
    """
    detector = _load_detector()
    target_a = tmp_path / "kairix" / "a.py"
    target_b = tmp_path / "kairix" / "b.py"
    _write(target_a, "import pg8000\n")
    _write(target_b, "from aiopg import create_pool\n")
    violations = detector.collect_violations(tmp_path)
    assert Path("kairix/a.py") in violations
    assert Path("kairix/b.py") in violations

    # Sabotage: replace with engagement-scope choices.
    _write(target_a, "import json\n")
    _write(target_b, "import sqlite3\n")
    assert detector.collect_violations(tmp_path) == set()


def test_postgres_client_outside_kairix_is_not_flagged(tmp_path: Path) -> None:
    """F44 only scans ``kairix/``. A file at ``kairix-firm/foo.py``
    (separate codebase, conceptually) is out of scope — the detector
    does not even walk it.
    """
    detector = _load_detector()
    target = tmp_path / "kairix-firm" / "foo.py"
    _write(target, "import psycopg\n")
    assert detector.collect_violations(tmp_path) == set()


def test_missing_kairix_directory_passes(tmp_path: Path) -> None:
    """Fresh checkout where ``kairix/`` doesn't exist yet — F44 is a
    no-op until engagement code lands.
    """
    detector = _load_detector()
    assert detector.collect_violations(tmp_path) == set()


def test_remediation_carries_action_markers() -> None:
    """F44's REMEDIATION must satisfy F21 (every check_*.{py,sh}
    failure-output string carries at least one of ``fix:`` / ``next:`` /
    ``run:``)."""
    detector = _load_detector()
    rem = detector.REMEDIATION.lower()
    assert "fix:" in rem
    assert "next:" in rem
    assert "run:" in rem

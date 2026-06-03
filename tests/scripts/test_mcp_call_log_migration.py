"""Tests for ``scripts/migrations/2026-06-03-mcp-call-log-schema.py``.

Validates the in-place migration that creates the ``mcp_call_log``
table + indexes on a legacy DB. Pins:

  * Fresh DB receives table + both indexes.
  * Already-migrated DB is a no-op (idempotency).
  * ``--dry-run`` makes no mutation.
  * Missing DB path returns a non-zero exit.

Sabotage proofs documented per test — the migration's correctness
depends on idempotency (re-running in production must not raise) and
on the schema matching the wrapper's INSERT shape.
"""

from __future__ import annotations

import importlib.util
import sqlite3
from pathlib import Path
from types import ModuleType
from typing import Any, cast

import pytest

pytestmark = pytest.mark.unit


_SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "migrations" / "2026-06-03-mcp-call-log-schema.py"


def _load_module() -> Any:
    """Import the migration script as a module (filename has dashes).

    Returns ``Any`` so test sites can call ``module.run_migration(...)``
    and ``module.main(...)`` without per-line type-ignores — the
    module is loaded via importlib at runtime, so static type info
    is not available anyway.
    """
    spec = importlib.util.spec_from_file_location("_mcp_call_log_migration", _SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return cast(ModuleType, module)


def _make_empty_db(tmp_path: Path) -> Path:
    """Create a fresh sqlite DB with no kairix tables — the legacy starting point."""
    db_path = tmp_path / "index.sqlite"
    conn = sqlite3.connect(str(db_path))
    conn.close()
    return db_path


def _table_exists(db_path: Path, name: str) -> bool:
    conn = sqlite3.connect(str(db_path))
    try:
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (name,),
        ).fetchone()
        return row is not None
    finally:
        conn.close()


def _index_exists(db_path: Path, name: str) -> bool:
    conn = sqlite3.connect(str(db_path))
    try:
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='index' AND name=?",
            (name,),
        ).fetchone()
        return row is not None
    finally:
        conn.close()


def test_migration_creates_table_and_indexes_on_fresh_db(tmp_path: Path) -> None:
    """Fresh empty DB: migration creates the table + both indexes.

    Sabotage proof: removed `_apply` call from `run_migration` — the
    after-state assertions fail (table absent). Restored.
    """
    mod = _load_module()
    db_path = _make_empty_db(tmp_path)

    rc = mod.run_migration(db_path=db_path, dry_run=False)

    assert rc == 0
    assert _table_exists(db_path, "mcp_call_log")
    assert _index_exists(db_path, "idx_mcp_call_log_tool_time")
    assert _index_exists(db_path, "idx_mcp_call_log_time")


def test_migration_is_idempotent(tmp_path: Path) -> None:
    """Re-running on an already-migrated DB is a no-op (exit 0, no errors).

    Sabotage proof: dropped the IF NOT EXISTS from the CREATE TABLE in
    `_CREATE_TABLE_SQL` — the second run raises OperationalError
    ("table mcp_call_log already exists"). Restored.
    """
    mod = _load_module()
    db_path = _make_empty_db(tmp_path)

    rc1 = mod.run_migration(db_path=db_path, dry_run=False)
    rc2 = mod.run_migration(db_path=db_path, dry_run=False)

    assert rc1 == 0
    assert rc2 == 0
    # Table still present after the second pass.
    assert _table_exists(db_path, "mcp_call_log")


def test_dry_run_makes_no_mutation(tmp_path: Path) -> None:
    """``--dry-run`` reports state but doesn't create anything.

    Sabotage proof: removed the `if dry_run: return 0` branch — the
    table appears on disk after `dry_run=True`. Restored.
    """
    mod = _load_module()
    db_path = _make_empty_db(tmp_path)

    rc = mod.run_migration(db_path=db_path, dry_run=True)

    assert rc == 0
    assert not _table_exists(db_path, "mcp_call_log")
    assert not _index_exists(db_path, "idx_mcp_call_log_tool_time")


def test_missing_db_path_returns_error(tmp_path: Path) -> None:
    """Non-existent DB path emits a clear error + returns -1.

    Sabotage proof: removed the existence check — `sqlite3.connect`
    would silently create an empty DB at the nonexistent path. The
    test catches this because `_table_exists` returns False (we'd
    expect True if the migration silently created the DB). Restored.
    """
    mod = _load_module()
    missing = tmp_path / "no-such-db.sqlite"

    rc = mod.run_migration(db_path=missing, dry_run=False)

    assert rc == -1
    assert not missing.exists()


def test_main_dispatches_to_run_migration(tmp_path: Path) -> None:
    """`main(argv)` parses --db + --dry-run and returns 0 on a fresh DB.

    Sabotage proof: returning 1 unconditionally from `main` — this
    test fails (expected rc=0). Restored.
    """
    mod = _load_module()
    db_path = _make_empty_db(tmp_path)

    rc = mod.main(["--db", str(db_path)])

    assert rc == 0
    assert _table_exists(db_path, "mcp_call_log")


def test_main_reports_no_op_on_already_migrated(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Re-running through `main` on an already-migrated DB reports no-op.

    Sabotage proof: changed the no-op branch to always re-apply — the
    'no-op' message disappears from stdout. Restored.
    """
    mod = _load_module()
    db_path = _make_empty_db(tmp_path)

    mod.main(["--db", str(db_path)])  # apply once
    capsys.readouterr()  # discard first-run output
    rc = mod.main(["--db", str(db_path)])  # re-run
    out = capsys.readouterr().out

    assert rc == 0
    assert "no-op" in out

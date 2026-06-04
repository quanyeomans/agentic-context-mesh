"""GH #409 — pin the ``path_canonical`` migration's idempotency + scale shape.

The production VM (alpha9) carries 1.1M ``documents`` rows on the
pre-#409 schema. The migration script
``scripts/migrations/2026-06-04-documents-path-canonical.py`` must:

  - Add the ``path_canonical`` virtual generated column.
  - Create ``idx_documents_path_canonical`` on it (the index is what
    makes the rewritten enrich SQL a probe instead of a full scan).
  - Be idempotent (re-runnable without error or duplicate work).

These tests build a legacy-shaped DB (no ``path_canonical``, no
index), run the migration, then assert post-state via
``PRAGMA table_xinfo`` + ``sqlite_master`` + ``EXPLAIN QUERY PLAN``.

No monkeypatch, no @patch — the migration's public entry point
``run_migration(db_path=..., dry_run=False)`` is called directly.
F1/F2/F4-clean.
"""

from __future__ import annotations

import importlib.util
import sqlite3
from pathlib import Path
from types import ModuleType

import pytest

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Module loader — the migration filename starts with a digit (date-style),
# which can't be imported via a regular ``import`` statement. The loader
# below is the canonical way to address a date-prefixed migration module
# from a test without renaming the script.
# ---------------------------------------------------------------------------


def _load_migration() -> ModuleType:
    """Load the migration script as a module via importlib."""
    here = Path(__file__).resolve().parents[2]
    migration_path = here / "scripts" / "migrations" / "2026-06-04-documents-path-canonical.py"
    spec = importlib.util.spec_from_file_location("path_canonical_migration", migration_path)
    assert spec is not None
    assert spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# DB shape helpers
# ---------------------------------------------------------------------------


def _legacy_db(tmp_path: Path, *, rows: int = 25) -> Path:
    """Build a tmp DB with a pre-#409 ``documents`` shape and N seeded rows."""
    db_path = tmp_path / "legacy.sqlite"
    db = sqlite3.connect(str(db_path))
    db.execute(
        "CREATE TABLE documents ("
        "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
        "  collection TEXT NOT NULL,"
        "  path TEXT NOT NULL,"
        "  hash TEXT NOT NULL,"
        "  active INTEGER DEFAULT 1,"
        "  UNIQUE(collection, path)"
        ")"
    )
    for i in range(rows):
        db.execute(
            "INSERT INTO documents (collection, path, hash) VALUES (?, ?, ?)",
            ("notes", f"/abs/p{i}.md", f"h{i}"),
        )
    db.commit()
    db.close()
    return db_path


def _column_exists(db_path: Path, table: str, column: str) -> bool:
    """Use ``table_xinfo`` so generated columns are visible."""
    db = sqlite3.connect(str(db_path))
    try:
        # safe: hardcoded table identifier.
        return any(row[1] == column for row in db.execute(f"PRAGMA table_xinfo({table})"))
    finally:
        db.close()


def _index_exists(db_path: Path, name: str) -> bool:
    """Return True iff an index named ``name`` is registered."""
    db = sqlite3.connect(str(db_path))
    try:
        row = db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='index' AND name=?",
            (name,),
        ).fetchone()
    finally:
        db.close()
    return row is not None


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_migration_creates_column_and_backfills_from_path(tmp_path: Path) -> None:
    """Post-migration, every row has ``path_canonical = path``.

    Sabotage proof: if the migration used a typo on the generated
    expression (e.g. ``GENERATED ALWAYS AS ('') VIRTUAL``), the post-
    migration ``path_canonical`` values would all be the empty string
    and the equality-with-``path`` assertion below would fail.
    """
    db_path = _legacy_db(tmp_path, rows=10)
    mig = _load_migration()

    rc = mig.run_migration(db_path=db_path, dry_run=False)
    assert rc == 0

    assert _column_exists(db_path, "documents", "path_canonical")

    # Backfill check: ``path_canonical`` must equal ``path`` on every row.
    # The VIRTUAL generated expression computes this at read time, so
    # the check also exercises that the generator is wired correctly.
    db = sqlite3.connect(str(db_path))
    try:
        mismatched = db.execute("SELECT COUNT(*) FROM documents WHERE path_canonical IS NOT path").fetchone()[0]
    finally:
        db.close()
    assert mismatched == 0, f"{mismatched} rows have path_canonical != path"


def test_migration_creates_index_on_path_canonical(tmp_path: Path) -> None:
    """The migration registers ``idx_documents_path_canonical`` in sqlite_master,
    and the planner picks it for an ``IN`` lookup on ``path_canonical``.

    Sabotage proof: if the ``CREATE INDEX`` step were dropped from
    ``_apply``, the ``_index_exists`` assertion would fail. If the
    index existed but the column it targets were wrong, the planner
    would fall back to ``SCAN documents`` and the plan-text assertion
    would fail.
    """
    db_path = _legacy_db(tmp_path, rows=10)
    mig = _load_migration()

    rc = mig.run_migration(db_path=db_path, dry_run=False)
    assert rc == 0

    assert _index_exists(db_path, "idx_documents_path_canonical")

    # The planner must pick the new index for an exact-match probe.
    db = sqlite3.connect(str(db_path))
    try:
        plan = db.execute(
            "EXPLAIN QUERY PLAN SELECT path FROM documents WHERE path_canonical IN (?)",
            ("/abs/p3.md",),
        ).fetchall()
    finally:
        db.close()
    plan_text = " | ".join(row[3] for row in plan)
    assert "idx_documents_path_canonical" in plan_text, f"planner did not pick the new index; plan: {plan_text}"


def test_migration_is_idempotent_on_second_run(tmp_path: Path) -> None:
    """Running the migration twice is a no-op on the second invocation.

    The script must detect the existing ``path_canonical`` column via
    ``PRAGMA table_xinfo`` (regular ``PRAGMA table_info`` omits
    generated columns and would cause a ``duplicate column name``
    error on re-run).

    Sabotage proof: if the script used ``table_info`` instead of
    ``table_xinfo`` for the existence check, the second
    ``run_migration`` call would raise ``sqlite3.OperationalError:
    duplicate column name: path_canonical`` and ``rc == 0`` below
    would fail.
    """
    db_path = _legacy_db(tmp_path, rows=5)
    mig = _load_migration()

    rc1 = mig.run_migration(db_path=db_path, dry_run=False)
    rc2 = mig.run_migration(db_path=db_path, dry_run=False)
    rc3 = mig.run_migration(db_path=db_path, dry_run=False)

    assert rc1 == 0
    assert rc2 == 0
    assert rc3 == 0
    # State unchanged across re-runs.
    assert _column_exists(db_path, "documents", "path_canonical")
    assert _index_exists(db_path, "idx_documents_path_canonical")


def test_dry_run_does_not_mutate(tmp_path: Path) -> None:
    """``--dry-run`` reports the missing state but does not apply changes.

    Sabotage proof: if ``--dry-run`` accidentally called ``_apply``,
    the post-dry-run check would see the column present and the
    assertion ``not _column_exists`` would fail.
    """
    db_path = _legacy_db(tmp_path, rows=5)
    mig = _load_migration()

    assert not _column_exists(db_path, "documents", "path_canonical")
    rc = mig.run_migration(db_path=db_path, dry_run=True)
    assert rc == 0
    # Still missing after dry-run.
    assert not _column_exists(db_path, "documents", "path_canonical")
    assert not _index_exists(db_path, "idx_documents_path_canonical")


def test_migration_reports_minus_one_when_db_path_missing(tmp_path: Path) -> None:
    """A non-existent ``--db`` path surfaces as an operator-actionable error
    (rc == -1) rather than crashing or silently no-op'ing.

    Sabotage proof: if the precondition check were removed, the
    sqlite3 connect call would create an empty DB file and the
    migration would succeed against a brand-new empty schema —
    the operator would then think the production DB was migrated
    when in fact a new empty file was just spawned.
    """
    missing = tmp_path / "does-not-exist.sqlite"
    mig = _load_migration()
    assert not missing.exists()

    rc = mig.run_migration(db_path=missing, dry_run=False)

    assert rc == -1
    # And the script did not create a stub file.
    assert not missing.exists()

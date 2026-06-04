"""Unit tests for ``kairix.platform.warm.sqlite_stats.ensure_sqlite_stats``.

Covers the two behaviour branches the brief mandates:

1. **Bootstrap** — fresh DB with N > 0 documents and no
   ``sqlite_stat1`` rows runs ANALYZE and reports the canonical detail.
2. **Idempotent skip** — DB with stats already populated short-circuits
   and reports ``elapsed_ms=0`` so containers don't pay 100+ seconds on
   every restart.

Test discipline:
  * F1 / F2 — every test uses an open ``sqlite3.Connection`` on a tmp
    file plus :func:`FakePaths` from ``tests/fakes``. No monkey-patching
    of kairix internals; no env-var manipulation.
  * F8 — module-level ``pytestmark = pytest.mark.unit``.
  * F47 — paths are constructed via ``FakePaths(...)``, the canonical
    composition seam.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from kairix.core.db.schema import create_schema
from kairix.platform.warm.sqlite_stats import (
    DETAIL_ANALYZE_COMPLETE,
    DETAIL_SKIPPED_STATS_PRESENT,
    STEP_NAME,
    ensure_sqlite_stats,
)
from tests.fakes import FakePaths

pytestmark = pytest.mark.unit


# Reference-library document hash; not tied to any real content.
_DOC_HASH = "agent-alpha-doc-0001"
_NOW = "2026-06-04T00:00:00Z"


def _open_fresh_db(db_path: Path) -> sqlite3.Connection:
    """Open a new SQLite connection on ``db_path`` with kairix schema applied."""
    db = sqlite3.connect(str(db_path))
    create_schema(db, dims=4)
    return db


def _seed_documents_row(db: sqlite3.Connection) -> None:
    """Insert one ``documents`` row so ANALYZE has something to look at.

    The minimal column set is what ``create_schema`` declares NOT NULL —
    add columns here defensively if the schema grows new mandatory
    columns. Without a row the bootstrap short-circuits as 'no data to
    analyze yet'.
    """
    db.execute(
        "INSERT INTO documents (collection, path, hash, source_name, source_uri, "
        "source_modified_at, source_page, sensitivity, created_at, modified_at, active) "
        "VALUES ('default', 'doc.md', ?, NULL, NULL, NULL, NULL, 'public', ?, ?, 1)",
        (_DOC_HASH, _NOW, _NOW),
    )
    db.commit()


def test_ensure_sqlite_stats_runs_analyze_when_missing(tmp_path: Path) -> None:
    """Fresh DB with N>0 documents and no sqlite_stat1 rows runs ANALYZE.

    Sabotage-proof (executed): replaced the ``db.execute("ANALYZE")`` line
    with ``pass`` and the assertion on the post-call stat row count
    failed (0 stat rows after the call). Restored the ANALYZE line to
    make it pass.
    """
    db_path = tmp_path / "kairix.sqlite"
    db = _open_fresh_db(db_path)
    _seed_documents_row(db)
    try:
        paths = FakePaths(db_path=db_path, document_root=tmp_path / "vault")

        result = ensure_sqlite_stats(db, paths)

        assert result.name == STEP_NAME
        assert result.ok is True
        assert result.detail == DETAIL_ANALYZE_COMPLETE, (
            f"expected detail={DETAIL_ANALYZE_COMPLETE!r}; got {result.detail!r}"
        )
        # ANALYZE measurable runtime — assert ≥ 0 (very fast on tmp DB)
        # rather than > 0 to avoid flake on extremely fast machines.
        assert result.elapsed_ms >= 0.0

        # sqlite_stat1 table now exists AND has at least one stat row.
        # Without ANALYZE having actually run, the table wouldn't be
        # there (sqlite_stat1 is created lazily by ANALYZE).
        row = db.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='sqlite_stat1'").fetchone()
        assert row is not None and int(row[0]) == 1, "sqlite_stat1 table should exist after ANALYZE"
        stat_rows = db.execute("SELECT COUNT(*) FROM sqlite_stat1").fetchone()
        # Schema has multiple indexes — at least one should produce a stat
        # row. Asserting >= 1 keeps the test schema-independent (more
        # indexes = more stat rows, but the floor of 1 always holds when
        # ANALYZE ran successfully against a populated documents table).
        assert stat_rows is not None and int(stat_rows[0]) >= 1, (
            f"ANALYZE should populate at least one stat row; got {stat_rows}"
        )
    finally:
        db.close()


def test_ensure_sqlite_stats_skips_when_already_populated(tmp_path: Path) -> None:
    """DB with sqlite_stat1 already populated short-circuits with elapsed_ms=0.

    Sabotage-proof (executed): removed the early-return guard in
    :func:`ensure_sqlite_stats` so it always ran ANALYZE — the
    ``elapsed_ms == 0.0`` assertion fired because ANALYZE re-ran and
    spent measurable time. Restored the guard to make it pass.
    """
    db_path = tmp_path / "kairix.sqlite"
    db = _open_fresh_db(db_path)
    _seed_documents_row(db)
    try:
        # Pre-populate sqlite_stat1 by running ANALYZE once outside the
        # function under test — this is the "stats already present"
        # baseline.
        db.execute("ANALYZE")
        db.commit()

        paths = FakePaths(db_path=db_path, document_root=tmp_path / "vault")
        result = ensure_sqlite_stats(db, paths)

        assert result.name == STEP_NAME
        assert result.ok is True
        assert result.detail == DETAIL_SKIPPED_STATS_PRESENT, (
            f"expected detail={DETAIL_SKIPPED_STATS_PRESENT!r}; got {result.detail!r}"
        )
        assert result.elapsed_ms == 0.0, f"skipped path should report elapsed_ms=0; got {result.elapsed_ms}"
    finally:
        db.close()


def test_ensure_sqlite_stats_skips_when_documents_empty(tmp_path: Path) -> None:
    """DB with schema applied but zero documents rows defers ANALYZE.

    Running ANALYZE on an empty DB produces zero-row stats the planner
    ignores anyway; deferring until the first ingest lands is the right
    contract. Asserting on this branch keeps the warm step from billing
    operators for wasted I/O on a brand-new install before any ingest.
    """
    db_path = tmp_path / "kairix.sqlite"
    db = _open_fresh_db(db_path)
    # NB: no _seed_documents_row call — documents table is empty.
    try:
        paths = FakePaths(db_path=db_path, document_root=tmp_path / "vault")
        result = ensure_sqlite_stats(db, paths)

        assert result.name == STEP_NAME
        assert result.ok is True
        assert result.detail == DETAIL_SKIPPED_STATS_PRESENT
        assert result.elapsed_ms == 0.0
    finally:
        db.close()


def test_warm_step_result_is_frozen() -> None:
    """F42 compliance — :class:`WarmStepResult` is a frozen dataclass.

    A frozen contract means callers can pass the result across threads /
    write it into a registry without worrying about a downstream
    mutation. Sabotage-proof: drop the ``frozen=True`` on the dataclass
    and this assertion fires.
    """
    from kairix.platform.warm.sqlite_stats import WarmStepResult

    result = WarmStepResult(name="x", ok=True, elapsed_ms=0.0, detail="d")
    with pytest.raises((AttributeError, Exception)) as exc_info:
        result.ok = False  # type: ignore[misc]  # frozen-dataclass assignment is the test target
    # FrozenInstanceError on cpython; the exact class name varies between
    # dataclass versions, so assert on the family.
    assert "frozen" in str(exc_info.value).lower() or isinstance(exc_info.value, AttributeError)

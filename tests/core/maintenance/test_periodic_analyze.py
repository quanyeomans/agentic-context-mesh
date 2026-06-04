"""Unit tests for the periodic ANALYZE refresh step (issue #376).

The decision rule under test:

  * Never analyzed before -> run.
  * Last analyze > 24h ago -> run.
  * Doc count grew by > 10% since last analyze -> run.
  * Otherwise -> skip.

Test discipline:
  * F1 / F2 — every test passes an explicit clock and an open in-memory
    SQLite connection. No monkey-patching, no ``setenv``.
  * F8 — module-level ``pytestmark = pytest.mark.unit``.
  * F42 — :class:`PeriodicAnalyzeResult` is exercised through its
    frozen-dataclass surface.
"""

from __future__ import annotations

import json
import sqlite3

import pytest

from kairix.core.db.schema import create_schema
from kairix.core.maintenance.periodic_analyze import (
    DEFAULT_GROWTH_THRESHOLD,
    DEFAULT_STALE_SECONDS,
    META_KEY,
    REASON_FRESH,
    REASON_GROWTH,
    REASON_NEVER_ANALYZED,
    REASON_STALE,
    read_last_analyze,
    run_periodic_analyze,
    should_run_analyze,
    write_last_analyze,
)

pytestmark = pytest.mark.unit


_NOW = "2026-06-04T00:00:00Z"


def _fresh_db() -> sqlite3.Connection:
    db = sqlite3.connect(":memory:")
    create_schema(db, dims=4)
    return db


def _seed_n_documents(db: sqlite3.Connection, n: int, *, prefix: str = "agent-alpha") -> None:
    """Insert ``n`` documents rows so periodic_analyze has something to measure."""
    rows = [
        (
            "default",
            f"doc-{i:06d}.md",
            f"{prefix}-{i:06d}",
            None,
            None,
            None,
            None,
            "public",
            _NOW,
            _NOW,
            1,
        )
        for i in range(n)
    ]
    db.executemany(
        "INSERT INTO documents (collection, path, hash, source_name, source_uri, "
        "source_modified_at, source_page, sensitivity, created_at, modified_at, active) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        rows,
    )
    db.commit()


# ---------------------------------------------------------------------------
# Pure decision function — ``should_run_analyze``
# ---------------------------------------------------------------------------


def test_should_run_when_never_analyzed() -> None:
    """No prior analyze -> run with REASON_NEVER_ANALYZED."""
    ran, reason = should_run_analyze(
        now=1000.0,
        last_ts=None,
        last_doc_count=0,
        current_doc_count=100,
    )
    assert ran is True
    assert reason == REASON_NEVER_ANALYZED


def test_should_run_when_stale() -> None:
    """Last analyze > stale_seconds ago -> run with REASON_STALE."""
    now = 100_000.0
    last_ts = now - DEFAULT_STALE_SECONDS - 1.0
    ran, reason = should_run_analyze(
        now=now,
        last_ts=last_ts,
        last_doc_count=100,
        current_doc_count=101,
    )
    assert ran is True
    assert reason == REASON_STALE


def test_should_run_when_growth_exceeds_threshold() -> None:
    """Doc growth > threshold ratio -> run with REASON_GROWTH."""
    now = 100_000.0
    # Within stale window but doc count went 100 -> 200 (100% growth).
    last_ts = now - 60.0
    ran, reason = should_run_analyze(
        now=now,
        last_ts=last_ts,
        last_doc_count=100,
        current_doc_count=200,
    )
    assert ran is True
    assert reason == REASON_GROWTH


def test_should_skip_when_fresh_and_low_growth() -> None:
    """Fresh stats + < 10% growth -> skip with REASON_FRESH.

    Sabotage proof (executed): hardcoded ``return True, REASON_STALE``
    at the top of :func:`should_run_analyze` and this test failed
    (asserted ran is False; got True). Restored to make it pass.
    """
    now = 100_000.0
    last_ts = now - 60.0  # well within stale window
    ran, reason = should_run_analyze(
        now=now,
        last_ts=last_ts,
        last_doc_count=100,
        current_doc_count=105,  # 5% growth — below the 10% threshold
    )
    assert ran is False
    assert reason == REASON_FRESH


def test_should_run_at_threshold_exact_boundary() -> None:
    """Growth ratio at exactly threshold -> skip (strict > comparison).

    The threshold is 10% — exactly 10% growth should NOT trigger; only
    > 10% does. This pins the boundary semantics.
    """
    now = 100_000.0
    last_ts = now - 60.0
    ran, _ = should_run_analyze(
        now=now,
        last_ts=last_ts,
        last_doc_count=100,
        current_doc_count=110,  # exactly 10% growth
        growth_threshold=DEFAULT_GROWTH_THRESHOLD,
    )
    assert ran is False, "growth at exact threshold should not trigger"


# ---------------------------------------------------------------------------
# I/O wrapper — ``run_periodic_analyze`` + bookkeeping
# ---------------------------------------------------------------------------


def test_periodic_analyze_runs_when_never_analyzed() -> None:
    """Fresh DB with documents and no kairix_meta last_analyze row -> ANALYZE fires.

    Sabotage proof: see the dedicated stale/growth/fresh tests below;
    each pin one decision branch so the regression of any branch is
    caught.
    """
    db = _fresh_db()
    try:
        _seed_n_documents(db, n=50)

        result = run_periodic_analyze(db, clock=lambda: 100_000.0)

        assert result.ran is True
        assert result.reason == REASON_NEVER_ANALYZED
        assert result.doc_count_at_analyze == 50
        assert result.previous_doc_count == 0

        # Bookkeeping landed in kairix_meta.
        row = db.execute("SELECT value FROM kairix_meta WHERE key = ?", (META_KEY,)).fetchone()
        assert row is not None, "kairix_meta should carry the last_analyze snapshot"
        payload = json.loads(row[0])
        assert payload["doc_count"] == 50
        assert payload["ts"] == 100_000.0
    finally:
        db.close()


def test_periodic_analyze_runs_when_stale() -> None:
    """Last analyze > 24h ago triggers a re-run.

    Sabotage proof (executed): commented out the ``REASON_STALE`` branch
    in :func:`should_run_analyze` and this test failed (ran=False). Restored
    to make it pass.
    """
    db = _fresh_db()
    try:
        _seed_n_documents(db, n=100)
        # Seed kairix_meta with a snapshot from > 24h ago.
        now = 1_000_000.0
        old_ts = now - DEFAULT_STALE_SECONDS - 1.0
        write_last_analyze(db, ts=old_ts, doc_count=100)

        result = run_periodic_analyze(db, clock=lambda: now)

        assert result.ran is True
        assert result.reason == REASON_STALE
        assert result.previous_doc_count == 100
    finally:
        db.close()


def test_periodic_analyze_runs_when_doc_growth_exceeds_10pct() -> None:
    """Doc count grew > 10% since last analyze -> re-run.

    Sabotage proof (executed): hardcoded the growth branch to skip
    (``return False, REASON_FRESH``) and this test failed because the
    seeded 100 -> 120 doc growth (20%) should fire ANALYZE. Restored
    the branch to pass.
    """
    db = _fresh_db()
    try:
        _seed_n_documents(db, n=120)
        # Last analyze 1 minute ago with 100 docs -> within stale window
        # but documents has grown to 120 (20% growth).
        now = 1_000_000.0
        write_last_analyze(db, ts=now - 60.0, doc_count=100)

        result = run_periodic_analyze(db, clock=lambda: now)

        assert result.ran is True
        assert result.reason == REASON_GROWTH
        assert result.previous_doc_count == 100
        assert result.doc_count_at_analyze == 120
    finally:
        db.close()


def test_periodic_analyze_skips_when_fresh() -> None:
    """Recent ts + < 10% growth -> ANALYZE skipped, no I/O.

    Sabotage proof (executed): replaced the early-return on the fresh
    branch with an unconditional ``db.execute("ANALYZE")`` and this test
    failed (ran=True; expected False). Restored to make it pass.
    """
    db = _fresh_db()
    try:
        _seed_n_documents(db, n=105)
        # Last analyze 1 minute ago with 100 docs; now 105 (5% growth)
        # -> below the 10% threshold AND within the stale window.
        now = 1_000_000.0
        write_last_analyze(db, ts=now - 60.0, doc_count=100)

        result = run_periodic_analyze(db, clock=lambda: now)

        assert result.ran is False
        assert result.reason == REASON_FRESH
        assert result.doc_count_at_analyze == 0
        assert result.previous_doc_count == 100
        assert result.elapsed_ms == 0.0
    finally:
        db.close()


def test_periodic_analyze_writes_new_snapshot_after_running() -> None:
    """After a successful run, kairix_meta carries the new (ts, doc_count).

    The next tick's decision must read the new snapshot — without this,
    the scheduler would re-run ANALYZE every tick.
    """
    db = _fresh_db()
    try:
        _seed_n_documents(db, n=10)
        # First run — never analyzed.
        run_periodic_analyze(db, clock=lambda: 500.0)
        # Bookkeeping check.
        snapshot = read_last_analyze(db)
        assert snapshot == (500.0, 10), f"expected (500.0, 10); got {snapshot!r}"

        # Second run — same tick conditions; should now skip.
        result_2 = run_periodic_analyze(db, clock=lambda: 500.1)
        assert result_2.ran is False
        assert result_2.reason == REASON_FRESH
    finally:
        db.close()


def test_read_last_analyze_returns_none_on_legacy_db() -> None:
    """A DB with no kairix_meta row for last_analyze returns None — never crashes."""
    db = _fresh_db()
    try:
        assert read_last_analyze(db) is None
    finally:
        db.close()


def test_read_last_analyze_returns_none_on_malformed_payload() -> None:
    """Corrupt JSON in kairix_meta surfaces as None, not a crash.

    Defensive: a fat-fingered operator UPDATE on kairix_meta shouldn't
    take down the maintenance loop.
    """
    db = _fresh_db()
    try:
        db.execute(
            "INSERT OR REPLACE INTO kairix_meta (key, value) VALUES (?, ?)",
            (META_KEY, "{not valid json"),
        )
        db.commit()
        assert read_last_analyze(db) is None
    finally:
        db.close()

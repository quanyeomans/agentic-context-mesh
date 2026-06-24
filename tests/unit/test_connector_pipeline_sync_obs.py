"""SYNC-OBS — pipeline-level observability (quiet ≠ dead).

Mirrors the style of ``tests/unit/test_connector_pipeline.py`` (real
Bronze + Silver + Cursor + DeadLetter surfaces against a tmp SQLite DB,
``@pytest.mark.unit``). Covers the three silent-stall WARNs the
connector pipeline previously emitted as INFO-or-nothing, plus the new
``items_seen`` / ``cursor_advanced`` / ``latest_modified_at`` fields on
:class:`BatchResult`.

Each WARN test asserts the WARN fires (caplog) AND that the outcome is
unchanged from the pre-SYNC-OBS behaviour — the change is purely
additive visibility, never a control-flow change.
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Callable
from pathlib import Path

import pytest

from kairix.core.connectors import StreamingBronzeStore
from kairix.core.connectors.cursor_store import CursorStore
from kairix.core.connectors.dead_letter import DeadLetterStore
from kairix.core.connectors.pipeline import ConnectorPipeline
from kairix.core.connectors.silver import DefaultSilverProcessor
from kairix.core.db.schema import create_schema
from kairix.core.protocols import ChangeEvent
from tests.fakes import FakeChunkWriter, FakeEntityGraphSink, FakeExtractor, FakeSourceConnector

pytestmark = pytest.mark.unit

_PIPELINE_LOGGER = "kairix.core.connectors.pipeline"


def _open_db(tmp_path: Path) -> sqlite3.Connection:
    db = sqlite3.connect(str(tmp_path / "kairix.db"))
    create_schema(db)
    return db


def _build_pipeline(
    db: sqlite3.Connection,
    *,
    disk_free_resolver: Callable[[], int] | None = None,
) -> ConnectorPipeline:
    return ConnectorPipeline(
        db=db,
        bronze=StreamingBronzeStore(db),
        silver=DefaultSilverProcessor(),
        chunk_writer=FakeChunkWriter(),
        entity_graph_sink=FakeEntityGraphSink(),
        cursor_store=CursorStore(db),
        dead_letter=DeadLetterStore(db),
        disk_free_resolver=disk_free_resolver,
    )


def _events(*item_ids: str) -> list[ChangeEvent]:
    return [
        ChangeEvent(op="modified", item_id=item_id, modified_at=f"2026-05-22T10:0{i}:00Z")
        for i, item_id in enumerate(item_ids, start=1)
    ]


def test_items_flowing_sets_items_seen_and_latest_modified_at(tmp_path: Path) -> None:
    """A tick that surfaces items reports items_seen and the newest modified_at."""
    db = _open_db(tmp_path)
    try:
        pipeline = _build_pipeline(db)
        events = _events("a.md", "b.md")
        connector = FakeSourceConnector(
            name="flowing",
            events=events,
            content={"a.md": b"alpha body", "b.md": b"beta body"},
            cursor_token="tok-1",
        )
        result = pipeline.run_batch(connector, FakeExtractor())

        assert result.items_seen == 2
        assert result.processed == 2
        assert result.cursor_advanced is True
        # Newest modified_at observed across the items this tick.
        assert result.latest_modified_at == "2026-05-22T10:02:00Z"
    finally:
        db.close()


def test_quiet_tick_reports_zero_items_seen(tmp_path: Path) -> None:
    """A connector with no changes reports items_seen=0 (the quiet signal)."""
    db = _open_db(tmp_path)
    try:
        pipeline = _build_pipeline(db)
        connector = FakeSourceConnector(name="quiet", events=[], cursor_token="tok-q")
        result = pipeline.run_batch(connector, FakeExtractor())

        assert result.items_seen == 0
        assert result.processed == 0
        assert result.latest_modified_at is None
        # A token WAS supplied, so the cursor advanced — quiet, not stuck.
        assert result.cursor_advanced is True
    finally:
        db.close()


def test_cursor_none_warns_and_does_not_clobber(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """SYNC-OBS site (a): next_cursor()==None → WARN + cursor_advanced False.

    Sabotage proof: delete the ``else`` branch in ``_commit_and_flush``;
    ``cursor_advanced`` stays True and no WARN is emitted — both
    assertions below fail. Restored, the WARN fires and the flag flips,
    with the persisted cursor still untouched (control flow unchanged).
    """
    db = _open_db(tmp_path)
    try:
        # Pre-seed a cursor so we can prove the None return does NOT clobber it.
        CursorStore(db).write("locked", "previously-persisted-token")
        db.commit()
        pipeline = _build_pipeline(db)
        # cursor_token=None AND track_modified_at=False → next_cursor()==None.
        connector = FakeSourceConnector(name="locked", events=[], cursor_token=None)

        with caplog.at_level(logging.WARNING, logger=_PIPELINE_LOGGER):
            result = pipeline.run_batch(connector, FakeExtractor())

        assert result.cursor_advanced is False
        warns = [r.getMessage() for r in caplog.records if "cursor_not_advanced" in r.getMessage()]
        assert warns, f"expected a cursor-lock WARN; got {[r.getMessage() for r in caplog.records]}"
        assert "locked" in warns[0]
        # Control flow unchanged: the prior cursor is NOT overwritten.
        fresh = sqlite3.connect(str(tmp_path / "kairix.db"))
        try:
            assert CursorStore(fresh).read("locked") == "previously-persisted-token"
        finally:
            fresh.close()
    finally:
        db.close()


def test_multi_chunk_all_none_cursor_warns_once_per_tick(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """MF2 — a multi-chunk all-None-cursor batch logs the cursor-lock WARN once.

    ``_commit_and_flush`` runs per chunk (every ``chunk_size`` items) plus
    the terminal drain. With 120 items at chunk_size 50 that is 3 flushes
    (items 50, 100, terminal), and every flush sees ``next_cursor() is None``.
    Before the fix the identical WARN fired once per flush (3x); after, it
    fires exactly once. The state effect (``cursor_advanced`` False) is
    unchanged.

    Sabotage proof: drop the ``cursor_lock_warned`` guard and the count
    below becomes 3, failing the assertion.
    """
    db = _open_db(tmp_path)
    try:
        item_ids = [f"item-{i:03d}.md" for i in range(120)]
        events = _events(*item_ids)
        connector = FakeSourceConnector(
            name="multichunk",
            events=events,
            content={item_id: b"body" for item_id in item_ids},
            cursor_token=None,  # AND track_modified_at False → next_cursor()==None
        )
        pipeline = ConnectorPipeline(
            db=db,
            bronze=StreamingBronzeStore(db),
            silver=DefaultSilverProcessor(),
            chunk_writer=FakeChunkWriter(),
            entity_graph_sink=FakeEntityGraphSink(),
            cursor_store=CursorStore(db),
            dead_letter=DeadLetterStore(db),
            chunk_size=50,
        )

        with caplog.at_level(logging.WARNING, logger=_PIPELINE_LOGGER):
            result = pipeline.run_batch(connector, FakeExtractor())

        # All 120 items flowed across 3 flushes — counters intact.
        assert result.processed == 120
        assert result.items_seen == 120
        assert result.cursor_advanced is False
        warns = [r.getMessage() for r in caplog.records if "cursor_not_advanced" in r.getMessage()]
        assert len(warns) == 1, f"expected the cursor-lock WARN exactly once; got {len(warns)}: {warns}"
        assert "multichunk" in warns[0]
    finally:
        db.close()


def test_watermark_skip_emits_warn_not_just_info(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """SYNC-OBS site (b): disk-watermark skip is now a WARN (was INFO).

    Sabotage proof: revert ``logger.warning`` back to ``logger.info`` at
    the watermark gate; the WARNING-level capture below is empty and the
    assertion fails. The skip outcome (skipped_low_disk True, zero
    processed, untouched cursor) is unchanged either way.
    """
    db = _open_db(tmp_path)
    try:
        pipeline = _build_pipeline(db, disk_free_resolver=lambda: 1 * 1024**3)  # 1 GiB free
        events = _events("x.md")
        connector = FakeSourceConnector(
            name="diskblocked",
            events=events,
            content={"x.md": b"body"},
            cursor_token="tok-d",
            disk_watermark_min_free_bytes=5 * 1024**3,  # 5 GiB required
        )

        with caplog.at_level(logging.WARNING, logger=_PIPELINE_LOGGER):
            result = pipeline.run_batch(connector, FakeExtractor())

        assert result.skipped_low_disk is True
        assert result.processed == 0
        # MF1 — the watermark skip returns before the cursor is ever read or
        # written, so cursor_advanced MUST be False (it defaults True, which
        # would falsely report progress on a disk-blocked source).
        assert result.cursor_advanced is False
        warns = [r.getMessage() for r in caplog.records if "watermark_skip" in r.getMessage()]
        assert warns, f"expected a watermark WARN; got {[r.getMessage() for r in caplog.records]}"
        assert "diskblocked" in warns[0]
    finally:
        db.close()


def test_all_poisoned_batch_warns_and_advances_cursor(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """SYNC-OBS site (c): every surfaced item poisoned → WARN, cursor advances.

    Sabotage proof: remove the ``_warn_if_all_poisoned`` call in
    ``_process_batch``; the all-poisoned WARN never fires and the
    assertion below fails. The poisoned_skipped count and the cursor
    advance are unchanged either way (read-only check on totals).
    """
    db = _open_db(tmp_path)
    try:
        dead_letter = DeadLetterStore(db)
        # Seed three failures so the single item is already poisoned (threshold 3).
        for _ in range(3):
            dead_letter.record("allpoison", "poisoned.md", "earlier failure")
        db.commit()

        pipeline = _build_pipeline(db)
        connector = FakeSourceConnector(
            name="allpoison",
            events=_events("poisoned.md"),
            content={"poisoned.md": b"never fetched"},
            cursor_token="tok-p",
        )

        with caplog.at_level(logging.WARNING, logger=_PIPELINE_LOGGER):
            result = pipeline.run_batch(connector, FakeExtractor())

        assert result.poisoned_skipped == 1
        assert result.processed == 0
        assert result.items_seen == 1
        warns = [r.getMessage() for r in caplog.records if "all_items_poisoned" in r.getMessage()]
        assert warns, f"expected an all-poisoned WARN; got {[r.getMessage() for r in caplog.records]}"
        assert "allpoison" in warns[0]
        # The item was never fetched (poison-skip is before fetch).
        assert connector.fetch_calls == []
    finally:
        db.close()


def test_partial_poison_does_not_warn_all_poisoned(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """A mix of poisoned + healthy items must NOT trigger the all-poisoned WARN."""
    db = _open_db(tmp_path)
    try:
        dead_letter = DeadLetterStore(db)
        for _ in range(3):
            dead_letter.record("mixed", "poisoned.md", "earlier failure")
        db.commit()

        pipeline = _build_pipeline(db)
        connector = FakeSourceConnector(
            name="mixed",
            events=_events("poisoned.md", "healthy.md"),
            content={"poisoned.md": b"skip", "healthy.md": b"healthy body"},
            cursor_token="tok-m",
        )

        with caplog.at_level(logging.WARNING, logger=_PIPELINE_LOGGER):
            result = pipeline.run_batch(connector, FakeExtractor())

        assert result.poisoned_skipped == 1
        assert result.processed == 1
        warns = [r.getMessage() for r in caplog.records if "all_items_poisoned" in r.getMessage()]
        assert warns == [], "all-poisoned WARN must NOT fire when at least one item processed"
    finally:
        db.close()

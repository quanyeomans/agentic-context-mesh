"""IM-2 integration tests for :class:`ConnectorPipeline`.

Drives the per-batch orchestrator against real SQLite + real
:class:`FilesystemBronzeStore` + real :class:`DefaultSilverProcessor` +
real :class:`CursorStore` + real :class:`DeadLetterStore`. The
:class:`SourceConnector` / :class:`Extractor` /
:class:`EntityGraphSink` / :class:`ChunkWriter` boundaries are filled
by canonical fakes from :mod:`tests.fakes` — those four sit at the
external network / index boundary of the pipeline.

Per spec §4: list_changes → fetch → bronze → silver → documents writer
→ entity-graph sink → cursor advance are ONE SQLite transaction. The
tests in this file pin:

1. **Happy path** — three ChangeEvents flow end-to-end; chunks reach
   the writer; entity signals reach the sink; cursor advances.
2. **Per-item failure isolation** — fetch raises on item 2; items 1+3
   succeed; item 2 lands in dead_letter; cursor advances.
3. **Poison threshold** — item 2 fails three times; on the fourth
   attempt the pipeline skips it (cursor advances past it).
4. **Batch-level rollback** — Silver raises; the whole batch rolls
   back; cursor unchanged; no chunks written.

Sabotage proof (executed by the agent, recorded here for the reader):

  In ``kairix/core/connectors/pipeline.py`` ``_process_batch``, comment
  out ``self._db.commit()``. Re-run ``test_happy_path_advances_cursor``:
  the cursor read at the end of the batch should be the latest event's
  ``modified_at``, but with the commit removed a fresh connection sees
  the cursor as ``None`` and the assertion ``stored_cursor ==
  latest_modified_at`` fails. Restore the commit; test passes again.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from kairix.core.connectors.bronze import FilesystemBronzeStore
from kairix.core.connectors.cursor_store import CursorStore
from kairix.core.connectors.dead_letter import DeadLetterStore
from kairix.core.connectors.pipeline import BatchResult, ConnectorPipeline
from kairix.core.connectors.silver import DefaultSilverProcessor
from kairix.core.db.schema import create_schema
from kairix.core.protocols import ChangeEvent
from tests.fakes import FakeChunkWriter, FakeEntityGraphSink, FakeExtractor, FakeSourceConnector

pytestmark = pytest.mark.integration


def _open_db(tmp_path: Path) -> sqlite3.Connection:
    """Open a fresh SQLite connection against the kairix schema."""
    db_path = tmp_path / "connector_pipeline.sqlite"
    db = sqlite3.connect(str(db_path))
    create_schema(db)
    return db


def _build_pipeline(
    db: sqlite3.Connection,
    bronze_root: Path,
    *,
    dead_letter_threshold: int = 3,
) -> tuple[ConnectorPipeline, FakeChunkWriter, FakeEntityGraphSink]:
    """Construct the pipeline with real stores + capture-only fakes for the writer/sink."""
    bronze = FilesystemBronzeStore(db, bronze_root)
    silver = DefaultSilverProcessor()
    cursor_store = CursorStore(db)
    dead_letter = DeadLetterStore(db)
    chunk_writer = FakeChunkWriter()
    entity_graph_sink = FakeEntityGraphSink()
    pipeline = ConnectorPipeline(
        db=db,
        bronze=bronze,
        silver=silver,
        chunk_writer=chunk_writer,
        entity_graph_sink=entity_graph_sink,
        cursor_store=cursor_store,
        dead_letter=dead_letter,
        dead_letter_threshold=dead_letter_threshold,
    )
    return pipeline, chunk_writer, entity_graph_sink


def _three_events() -> list[ChangeEvent]:
    """Three modify events with monotonically increasing modified_at."""
    return [
        ChangeEvent(op="modified", item_id=f"note-{i}.md", modified_at=f"2026-05-22T10:0{i}:00Z") for i in (1, 2, 3)
    ]


def _three_event_content() -> dict[str, bytes]:
    """Content for the three events — a markdown body large enough to chunk."""
    body = (
        "Jane Smith met Acme Corp on Tuesday.\n\n"
        + ("This is a body paragraph carrying enough text to fill a chunk. " * 20)
    ).encode("utf-8")
    return {f"note-{i}.md": body for i in (1, 2, 3)}


def test_happy_path_advances_cursor(tmp_path: Path) -> None:
    """Three events flow end-to-end; chunks + signals reach their sinks; cursor advances."""
    db = _open_db(tmp_path)
    try:
        pipeline, chunk_writer, entity_graph_sink = _build_pipeline(db, tmp_path / "bronze")
        connector = FakeSourceConnector(
            name="fake-source",
            events=_three_events(),
            content=_three_event_content(),
        )
        extractor = FakeExtractor()

        result = pipeline.run_batch(connector, extractor)

        assert isinstance(result, BatchResult)
        assert result.processed == 3
        assert result.dead_lettered == 0
        assert result.poisoned_skipped == 0

        # Every item reached the writer + sink.
        assert len(chunk_writer.writes) == 3
        assert len(entity_graph_sink.staged) == 3

        # Entity signals were extracted — "Jane Smith" + "Acme Corp" from
        # the body show up at least once across the batch.
        all_signals = [s for batch in entity_graph_sink.staged for s in batch]
        signal_values = {s.value for s in all_signals}
        assert "Jane Smith" in signal_values
        assert "Acme Corp" in signal_values
        # Sensitivity travelled through.
        for s in all_signals:
            assert s.sensitivity == "internal"

        # F39 guard — every emitted chunk carries the three metadata fields.
        all_chunks = [c for batch in chunk_writer.writes for c in batch]
        assert all_chunks  # at least one chunk emitted
        for chunk in all_chunks:
            assert chunk.source_uri.startswith("fake-source://item/")
            assert chunk.source_modified_at.startswith("2026-05-22T10:")
            assert chunk.sensitivity == "internal"
            assert chunk.source_name == "fake-source"

        # Bronze blobs are on disk + bronze_records rows exist.
        bronze_rows = db.execute("SELECT COUNT(*) FROM bronze_records").fetchone()[0]
        assert bronze_rows == 3

        # Cursor advanced to the latest event's modified_at.
        fresh = sqlite3.connect(str(tmp_path / "connector_pipeline.sqlite"))
        try:
            stored_cursor = CursorStore(fresh).read("fake-source")
            assert stored_cursor == "2026-05-22T10:03:00Z"
        finally:
            fresh.close()
    finally:
        db.close()


def test_per_item_failure_isolation_dead_letters(tmp_path: Path) -> None:
    """Fetch raises on item 2 → items 1+3 still succeed; item 2 lands in dead_letter."""
    db = _open_db(tmp_path)
    try:
        pipeline, chunk_writer, entity_graph_sink = _build_pipeline(db, tmp_path / "bronze")
        connector = FakeSourceConnector(
            name="fake-source",
            events=_three_events(),
            content=_three_event_content(),
            fail_on_fetch={"note-2.md"},
        )
        extractor = FakeExtractor()

        result = pipeline.run_batch(connector, extractor)

        assert result.processed == 2
        assert result.dead_lettered == 1
        assert result.poisoned_skipped == 0

        # Sibling items wrote chunks; failed item did not.
        assert len(chunk_writer.writes) == 2
        assert len(entity_graph_sink.staged) == 2

        # Item 2 sits in dead_letter at failure_count=1.
        row = db.execute(
            "SELECT failure_count FROM connector_deadletter WHERE source_name = ? AND item_id = ?",
            ("fake-source", "note-2.md"),
        ).fetchone()
        assert row is not None
        assert row[0] == 1

        # Cursor still advanced — sibling items succeeded.
        stored_cursor = CursorStore(db).read("fake-source")
        assert stored_cursor == "2026-05-22T10:03:00Z"
    finally:
        db.close()


def test_persistent_failure_becomes_poisoned_and_skips(tmp_path: Path) -> None:
    """On the third retry the item exceeds the threshold and is skipped past."""
    db = _open_db(tmp_path)
    try:
        pipeline, _writer, _sink = _build_pipeline(db, tmp_path / "bronze")
        events = _three_events()
        content = _three_event_content()

        # Three batches: item 2 fails every time. After three failures it
        # crosses the threshold; the fourth attempt skips it.
        for attempt in (1, 2, 3):
            connector = FakeSourceConnector(
                name="fake-source",
                events=events,
                content=content,
                fail_on_fetch={"note-2.md"},
            )
            result = pipeline.run_batch(connector, FakeExtractor())
            assert result.dead_lettered == 1, f"attempt {attempt}: expected one dead-letter"
            assert result.poisoned_skipped == 0, f"attempt {attempt}: poison threshold not yet crossed"

        # On the 4th attempt the item is poisoned — is_poisoned returns True
        # and the pipeline skips it before the fetch call.
        assert DeadLetterStore(db).is_poisoned("fake-source", "note-2.md", threshold=3) is True

        connector = FakeSourceConnector(
            name="fake-source",
            events=events,
            content=content,
            fail_on_fetch={"note-2.md"},
        )
        result = pipeline.run_batch(connector, FakeExtractor())
        assert result.poisoned_skipped == 1
        # The pipeline did NOT call fetch on the poisoned item.
        assert "note-2.md" not in connector.fetch_calls

        # Sibling items still processed.
        assert result.processed == 2

        # Cursor advanced past the poisoned item — the worker doesn't spin
        # on the broken record forever.
        stored_cursor = CursorStore(db).read("fake-source")
        assert stored_cursor == "2026-05-22T10:03:00Z"
    finally:
        db.close()


def test_silver_failure_rolls_back_entire_batch(tmp_path: Path) -> None:
    """Silver raises → transaction rolls back → cursor unchanged → no chunks written."""

    class _ExplodingSilver:
        """A SilverProcessor that always raises — proves batch-level rollback."""

        def process(self, *_args: object, **_kwargs: object) -> object:
            raise RuntimeError("silver: simulated batch-level failure")

    db = _open_db(tmp_path)
    try:
        bronze = FilesystemBronzeStore(db, tmp_path / "bronze")
        cursor_store = CursorStore(db)
        dead_letter = DeadLetterStore(db)
        chunk_writer = FakeChunkWriter()
        entity_graph_sink = FakeEntityGraphSink()
        pipeline = ConnectorPipeline(
            db=db,
            bronze=bronze,
            silver=_ExplodingSilver(),  # type: ignore[arg-type]  # F3-rationale: synthetic Protocol-compliant stand-in for negative-path test.
            chunk_writer=chunk_writer,
            entity_graph_sink=entity_graph_sink,
            cursor_store=cursor_store,
            dead_letter=dead_letter,
        )
        # Seed a pre-existing cursor so we can prove it doesn't advance.
        cursor_store.write("fake-source", "PRE-EXISTING-CURSOR")
        db.commit()

        connector = FakeSourceConnector(
            name="fake-source",
            events=_three_events(),
            content=_three_event_content(),
        )

        with pytest.raises(RuntimeError, match="silver: simulated batch-level failure"):
            pipeline.run_batch(connector, FakeExtractor())

        # The exception triggered rollback — the cursor STILL reads the
        # pre-existing value, the bronze_records table is empty, the
        # writer + sink received nothing.
        fresh = sqlite3.connect(str(tmp_path / "connector_pipeline.sqlite"))
        try:
            assert CursorStore(fresh).read("fake-source") == "PRE-EXISTING-CURSOR"
            row = fresh.execute("SELECT COUNT(*) FROM bronze_records").fetchone()
            assert row[0] == 0
        finally:
            fresh.close()
        assert chunk_writer.writes == []
        assert entity_graph_sink.staged == []
    finally:
        db.close()

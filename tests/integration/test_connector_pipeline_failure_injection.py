"""Integration tests for ConnectorPipeline failure injection (test-resilience plan Wave 1).

Targets failure-mode Class D from docs/architecture/test-resilience-plan.md
§2: pipeline mid-batch failures + recovery. The pre-existing
``test_connector_pipeline_long_batch_durability.py`` covers Silver
failing mid-batch (#321 regression lock); these tests fill the gaps
for fetch failures, extractor failures, chunk_writer failures, and
list_changes iteration failures.

F47-clean: all pipelines built via ``kairix.core.factory.build_connector_pipeline``.
F1-clean: canonical fakes from ``tests/fakes.py``; scripted-failure
classes here are F1-allowed because they implement a Protocol from
scratch rather than monkeypatching kairix internals.

Each test sabotage-proves by mutating the relevant pipeline branch
(recorded inline) so the assertion has teeth.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from kairix.core.db.schema import create_schema
from kairix.core.factory import build_connector_pipeline
from kairix.core.protocols import ChangeEvent, ExtractedDocument, MimeType
from tests.fakes import FakeChunkWriter, FakeEntityGraphSink, FakeExtractor, FakeSourceConnector

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_pipeline(
    db: sqlite3.Connection,
    bronze_root: Path,
    *,
    silver: Any = None,
    chunk_writer: Any = None,
    entity_graph_sink: Any = None,
) -> Any:
    """F47-compliant factory construction with optional overrides."""
    return build_connector_pipeline(
        db=db,
        collection="default",
        silver=silver,
        chunk_writer=chunk_writer if chunk_writer is not None else FakeChunkWriter(),
        entity_graph_sink=entity_graph_sink if entity_graph_sink is not None else FakeEntityGraphSink(),
    )


def _count_bronze_records(db: sqlite3.Connection, source_name: str) -> int:
    return int(db.execute("SELECT count(*) FROM bronze_records WHERE source_name = ?", (source_name,)).fetchone()[0])


def _count_dead_letter(db: sqlite3.Connection, source_name: str) -> int:
    return int(
        db.execute("SELECT count(*) FROM connector_deadletter WHERE source_name = ?", (source_name,)).fetchone()[0]
    )


# ---------------------------------------------------------------------------
# Scripted-failure components — F1-clean Protocol impls (not monkeypatches)
# ---------------------------------------------------------------------------


@dataclass
class _RaisingExtractor:
    """Extractor that raises on a configurable subset of mime/item combos.

    Implements the Extractor Protocol from scratch — F1-clean test seam.
    """

    name: str = "raising-extractor"
    version: str = "1.0.0"
    fail_on_call_n: int = 0
    calls: int = 0

    def can_extract(self, mime: MimeType, magic_bytes: bytes) -> bool:
        return True

    def extract(self, raw: bytes, mime: MimeType) -> ExtractedDocument:
        self.calls += 1
        if self.calls == self.fail_on_call_n:
            raise RuntimeError(f"extractor-failing-on-call: simulated failure at call {self.fail_on_call_n}")
        from kairix.core.protocols import DocMetadata

        return ExtractedDocument(
            markdown=f"# Extracted call {self.calls}\n\nbody",
            pages=(),
            images=(),
            metadata=DocMetadata(title=None, author=None, created_date=None, language=None, page_count=None),
            confidence=0.5,
        )

    def quality_ok(self, doc: ExtractedDocument) -> bool:
        return True


@dataclass
class _RaisingChunkWriter:
    """ChunkWriter that raises on the N-th upsert call."""

    fail_on_call_n: int = 0
    calls: int = 0

    def upsert(self, chunks: Any) -> int:
        self.calls += 1
        if self.calls == self.fail_on_call_n:
            raise RuntimeError(f"writer-failing-on-call: simulated failure at call {self.fail_on_call_n}")
        return 0


class _StopAfterNListChangesConnector(FakeSourceConnector):
    """Source connector whose ``list_changes`` raises after yielding N events.

    Tests the path where the connector itself blows up mid-stream — the
    pipeline batch loop should surface the exception rather than silently
    truncate.
    """

    def __init__(self, *, events: list[ChangeEvent], content: dict[str, bytes], raise_after_n: int) -> None:
        super().__init__(name="raising-list-changes", events=events, content=content)
        self._raise_after_n = raise_after_n

    def list_changes(self, cursor: Any = None) -> Any:
        emitted = 0
        for event in self._events:  # type: ignore[attr-defined]  # FakeSourceConnector stores events as _events
            if emitted >= self._raise_after_n:
                raise RuntimeError(f"list_changes-failing-after-{self._raise_after_n}")
            yield event
            emitted += 1


# ---------------------------------------------------------------------------
# Test 1 — Fetch failure on one item: siblings still process
# ---------------------------------------------------------------------------


def test_fetch_failure_on_single_item_does_not_abort_batch(tmp_path: Path) -> None:
    """A connector that raises on fetch for item-005 (but not its siblings)
    must dead-letter item-005 AND still process items 0-4 and 6-9.

    Pre-existing ``test_failing_connector_logged_and_loop_continues`` covers
    failure at the connector-resolution boundary. This test covers failure
    mid-fetch, which is a different code path inside ``_process_item``.

    Sabotage proof: remove the try/except around ``connector.fetch(item_id)``
    in ``pipeline.py:_process_item`` (the fetch try-block at lines ~294-298);
    the test fails because the entire batch aborts on item-005 instead of
    dead-lettering and continuing.
    """
    db = sqlite3.connect(":memory:")
    create_schema(db)
    bronze_root = tmp_path / "bronze"

    events = [
        ChangeEvent(op="created", item_id=f"item-{i:03d}", modified_at=f"2026-01-01T00:00:{i:02d}Z") for i in range(10)
    ]
    contents = {f"item-{i:03d}": f"payload-{i}".encode() for i in range(10)}
    source = FakeSourceConnector(
        name="fetch-failure-source",
        events=events,
        content=contents,
        fail_on_fetch={"item-005"},  # one bad apple
    )
    extractor = FakeExtractor()
    pipeline = _build_pipeline(db, bronze_root)
    result = pipeline.run_batch(source, extractor)

    # 9 items processed (0-4 + 6-9), 1 dead-lettered (item-005)
    assert result.processed == 9, f"expected 9 processed, got {result.processed}; result={result}"
    assert result.dead_lettered == 1, f"expected 1 dead-lettered, got {result.dead_lettered}; result={result}"
    assert _count_dead_letter(db, "fetch-failure-source") == 1

    # Verify the failing item is the one in dead_letter
    row = db.execute(
        "SELECT item_id, last_error FROM connector_deadletter WHERE source_name = ?",
        ("fetch-failure-source",),
    ).fetchone()
    assert row[0] == "item-005"
    assert "fetch" in row[1].lower()

    db.close()


# ---------------------------------------------------------------------------
# Test 2 — Extract failure on one item: siblings still process
# ---------------------------------------------------------------------------


def test_extract_failure_on_single_item_does_not_abort_batch(tmp_path: Path) -> None:
    """Extractor.extract raises on the 5th call; items 1-4 + 6-10 should
    still process; item-005 dead-letters.

    Sabotage proof: remove the try/except around ``extractor.extract(raw, mime)``
    inside ``_process_item``; the test fails with RuntimeError escaping out
    of run_batch.
    """
    db = sqlite3.connect(":memory:")
    create_schema(db)
    bronze_root = tmp_path / "bronze"

    events = [
        ChangeEvent(op="created", item_id=f"item-{i:03d}", modified_at=f"2026-01-01T00:00:{i:02d}Z") for i in range(10)
    ]
    contents = {f"item-{i:03d}": f"payload-{i}".encode() for i in range(10)}
    source = FakeSourceConnector(name="extract-failure-source", events=events, content=contents)
    extractor = _RaisingExtractor(fail_on_call_n=5)
    pipeline = _build_pipeline(db, bronze_root)
    result = pipeline.run_batch(source, extractor)

    assert result.processed == 9, f"expected 9 processed, got {result.processed}; result={result}"
    assert result.dead_lettered == 1, f"expected 1 dead-lettered, got {result.dead_lettered}; result={result}"
    assert _count_dead_letter(db, "extract-failure-source") == 1

    row = db.execute(
        "SELECT last_error FROM connector_deadletter WHERE source_name = ?",
        ("extract-failure-source",),
    ).fetchone()
    assert "extract" in row[0].lower()

    db.close()


# ---------------------------------------------------------------------------
# Test 3 — chunk_writer failure mid-chunk: that chunk rolls back
# ---------------------------------------------------------------------------


def test_writer_failure_mid_chunk_rolls_back_only_that_chunk(tmp_path: Path) -> None:
    """chunk_writer.upsert raises on the 51st call (item-050, first of
    chunk-2 with chunk_size=50). Items 0-49 should remain committed
    (chunk-1 boundary); items 50-99 should NOT be committed; the
    RuntimeError surfaces to the caller.

    Sabotage proof: in ``pipeline.py:_process_batch``, change the per-chunk
    commit to a single end-of-batch commit (pre-#321 shape) — the test
    fails because items 0-49 also roll back when chunk-2 raises.
    """
    db_path = tmp_path / "writer_failure.sqlite"
    db = sqlite3.connect(str(db_path))
    create_schema(db)
    bronze_root = tmp_path / "bronze"

    events = [
        ChangeEvent(op="created", item_id=f"item-{i:03d}", modified_at=f"2026-01-01T00:00:{i:02d}Z") for i in range(100)
    ]
    contents = {f"item-{i:03d}": f"payload-{i}".encode() for i in range(100)}
    source = FakeSourceConnector(name="writer-failure-source", events=events, content=contents)
    extractor = FakeExtractor()
    writer = _RaisingChunkWriter(fail_on_call_n=51)

    pipeline = _build_pipeline(db, bronze_root, chunk_writer=writer)
    with pytest.raises(RuntimeError, match="writer-failing-on-call"):
        pipeline.run_batch(source, extractor)

    # Chunk-1 (items 0..49) committed; chunk-2 (items 50..99) rolled back.
    # Per #321 fix, chunk_size=50 means we expect 50 bronze_records to survive.
    bronze_count = _count_bronze_records(db, "writer-failure-source")
    assert bronze_count == 50, (
        f"#321 chunked-commit contract: writer failure on item 50 should "
        f"leave items 0-49 committed (50 rows); got {bronze_count}. "
        f"If <50, the chunked commit isn't holding; if >50, the pipeline "
        f"continued past the writer failure."
    )

    db.close()


# ---------------------------------------------------------------------------
# Test 4 — connector.list_changes raises mid-stream: surfaces to caller
# ---------------------------------------------------------------------------


def test_list_changes_raises_mid_stream_surfaces_to_caller(tmp_path: Path) -> None:
    """A connector whose list_changes generator raises after yielding 3
    items must surface the exception to the caller. The pipeline must
    NOT silently swallow the failure (which would look like a clean
    batch with 3 items processed).

    Sabotage proof: wrap the ``for change in connector.list_changes(...)``
    loop in a try/except that absorbs the exception — the test fails
    because no exception propagates. Restored, RuntimeError surfaces.
    """
    db = sqlite3.connect(":memory:")
    create_schema(db)
    bronze_root = tmp_path / "bronze"

    events = [
        ChangeEvent(op="created", item_id=f"item-{i:03d}", modified_at=f"2026-01-01T00:00:{i:02d}Z") for i in range(10)
    ]
    contents = {f"item-{i:03d}": f"payload-{i}".encode() for i in range(10)}
    source = _StopAfterNListChangesConnector(events=events, content=contents, raise_after_n=3)
    extractor = FakeExtractor()
    pipeline = _build_pipeline(db, bronze_root)

    with pytest.raises(RuntimeError, match="list_changes-failing-after-3"):
        pipeline.run_batch(source, extractor)

    # The 3 items that were successfully yielded + processed BEFORE the
    # raise should be on-disk; partial-batch durability is governed by
    # the chunked-commit contract (#321). With chunk_size=50 and only
    # 3 items completed before raise, those 3 may or may not be committed
    # depending on whether they hit a chunk boundary. Don't assert exact
    # count — assert behaviour: the exception surfaced cleanly.
    db.close()


# ---------------------------------------------------------------------------
# Test 5 — fetch + extract failure interaction: both routes dead-letter
# ---------------------------------------------------------------------------


def test_mixed_fetch_and_extract_failures_route_independently(tmp_path: Path) -> None:
    """Items 1+3 fail at fetch; items 5+7 fail at extract; items 0+2+4+6+8+9
    succeed. All four failing items dead-letter; six items process.

    Sabotage proof: collapse the two try/except blocks in ``_process_item``
    into one (the fetch-error message becomes the extract-error message);
    the test fails because dead_letter rows for items 5+7 carry "fetch:"
    instead of "extract:".
    """
    db = sqlite3.connect(":memory:")
    create_schema(db)
    bronze_root = tmp_path / "bronze"

    events = [
        ChangeEvent(op="created", item_id=f"item-{i:03d}", modified_at=f"2026-01-01T00:00:{i:02d}Z") for i in range(10)
    ]
    contents = {f"item-{i:03d}": f"payload-{i}".encode() for i in range(10)}
    source = FakeSourceConnector(
        name="mixed-failure-source",
        events=events,
        content=contents,
        fail_on_fetch={"item-001", "item-003"},
    )

    # Extractor must raise on the 3rd and 5th SUCCESSFUL calls (items 005 and 007).
    # Items 0, 2, 4 succeed (calls 1-3 — extract raises on call 3 = item-004 NOT
    # item-005). Need to compute carefully:
    # - fetch order: 0, 1(fail), 2, 3(fail), 4, 5, 6, 7, 8, 9
    # - extract calls happen only for fetched items: 0, 2, 4, 5, 6, 7, 8, 9
    # - call 1=item-000, 2=item-002, 3=item-004, 4=item-005, 5=item-006, 6=item-007, 7=item-008, 8=item-009
    # - To fail at item-005 (call 4) and item-007 (call 6), use the _MultiPointRaisingExtractor below
    class _MultiPointRaising:
        name = "multi-raising"
        version = "1.0.0"

        def __init__(self) -> None:
            self.calls = 0

        def can_extract(self, mime: MimeType, magic_bytes: bytes) -> bool:
            return True

        def extract(self, raw: bytes, mime: MimeType) -> ExtractedDocument:
            self.calls += 1
            if self.calls in (4, 6):  # item-005 and item-007 per the fetch sequence
                raise RuntimeError(f"extract-multi-fail-call-{self.calls}")
            from kairix.core.protocols import DocMetadata

            return ExtractedDocument(
                markdown="# ok",
                pages=(),
                images=(),
                metadata=DocMetadata(title=None, author=None, created_date=None, language=None, page_count=None),
                confidence=0.5,
            )

        def quality_ok(self, doc: ExtractedDocument) -> bool:
            return True

    extractor = _MultiPointRaising()
    pipeline = _build_pipeline(db, bronze_root)
    result = pipeline.run_batch(source, extractor)

    # 6 successful items + 4 dead-lettered (2 fetch + 2 extract)
    assert result.processed == 6, f"expected 6 processed, got {result.processed}; result={result}"
    assert result.dead_lettered == 4, f"expected 4 dead-lettered, got {result.dead_lettered}; result={result}"

    rows = db.execute(
        "SELECT item_id, last_error FROM connector_deadletter WHERE source_name = ? ORDER BY item_id",
        ("mixed-failure-source",),
    ).fetchall()
    by_item = {item_id: err for item_id, err in rows}
    assert "fetch" in by_item["item-001"].lower()
    assert "fetch" in by_item["item-003"].lower()
    assert "extract" in by_item["item-005"].lower()
    assert "extract" in by_item["item-007"].lower()

    db.close()

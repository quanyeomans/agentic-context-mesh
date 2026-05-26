"""ADR-018 Wave 1 characterization test — long-batch durability (#321).

The current ``ConnectorPipeline._process_batch`` runs the entire batch
inside ONE SQLite transaction (line 185 — single ``self._db.commit()``
at the end). When any item's Silver / writer / sink raises, the batch
rolls back at line 154, but every blob already fsynced to disk by
``FilesystemBronzeStore.write`` stays put. On a 6000-item SharePoint
backfill, a single Silver failure mid-stream produces thousands of
orphans on disk while ``bronze_records`` stays empty.

This test FAILS today (against ``FilesystemBronzeStore`` + the current
``ConnectorPipeline``). It will PASS after ADR-018 Wave 1 lands
``DltBronzeStore`` with bounded load packages — at most one chunk's
worth of orphans can survive a Silver failure.

Why we land the failing test FIRST (per the user's TDD instruction):

  Build tests; establish green; refactor dlt; fix code and/or test
  equivalence to get to green.

The xfail marker carries the issue reference so the test is a live
regression-lock — when Wave 1 ships, the marker is removed and the
test must pass cleanly. If the test starts passing before the fix
(e.g. someone accidentally lands a chunking change), pytest reports
XPASSED and we know we have unattributed coverage.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest

from kairix.core.db.schema import create_schema
from kairix.core.factory import build_connector_pipeline
from kairix.core.protocols import ChangeEvent, SilverOutput
from tests.fakes import FakeChunkWriter, FakeEntityGraphSink, FakeExtractor, FakeSourceConnector

pytestmark = pytest.mark.integration


class _SilverFailingOnNthCall:
    """SilverProcessor stand-in that raises on the N-th process() call.

    Drives the bug condition deterministically: the first N-1 items
    write their bronze blobs (and pre-commit bronze_records rows that
    will roll back), then the N-th raises and the batch rolls back.
    """

    def __init__(self, *, fail_on_call: int) -> None:
        self._fail_on_call = fail_on_call
        self.calls = 0

    def process(
        self,
        raw: Any,
        extracted: Any,
        source_uri: str,
        source_modified_at: str,
        sensitivity: Any,
    ) -> SilverOutput:
        del raw, extracted, source_uri, source_modified_at, sensitivity
        self.calls += 1
        if self.calls == self._fail_on_call:
            raise RuntimeError(f"silver-failing-on-call: simulated failure at item {self._fail_on_call}")
        return SilverOutput(chunks=(), entity_signals=())


def _build_pipeline(
    db: sqlite3.Connection,
    bronze_root: Path,
    silver: _SilverFailingOnNthCall,
) -> Any:
    """F47-compliant: build through ``kairix.core.factory.build_connector_pipeline``
    with the failing-silver override + capture-only chunk_writer / sink so
    the pipeline composition matches production wiring."""
    return build_connector_pipeline(
        db=db,
        bronze_root=bronze_root,
        collection="default",
        silver=silver,
        chunk_writer=FakeChunkWriter(),
        entity_graph_sink=FakeEntityGraphSink(),
    )


def _count_on_disk_blobs(bronze_root: Path, source_name: str) -> int:
    source_dir = bronze_root / source_name
    if not source_dir.is_dir():
        return 0
    return sum(1 for prefix in source_dir.iterdir() if prefix.is_dir() for blob in prefix.iterdir() if blob.is_file())


def _count_bronze_records(db: sqlite3.Connection, source_name: str) -> int:
    row = db.execute(
        "SELECT count(*) FROM bronze_records WHERE source_name = ?",
        (source_name,),
    ).fetchone()
    return int(row[0]) if row else 0


def test_silver_failure_mid_batch_leaves_orphans_bounded_by_chunk_size(tmp_path: Path) -> None:
    """#321 fix: a 100-item batch with Silver failing on item 50 should
    leave AT MOST chunk_size orphans on disk (those in the failing
    uncommitted chunk). Earlier chunks committed and survive the
    failure; their cursor advance survives too.

    Sabotage proof: revert ConnectorPipeline._process_batch to its
    pre-#321 single-commit shape and this test fails with 50 orphans
    (the companion test below documents that exact pre-fix shape).
    """
    db_path = tmp_path / "connector_pipeline.sqlite"
    db = sqlite3.connect(str(db_path))
    create_schema(db)
    bronze_root = tmp_path / "bronze"

    # 100 events with distinct payloads so each fetch writes a unique blob.
    events = [
        ChangeEvent(op="created", item_id=f"item-{i:03d}", modified_at=f"2026-01-01T00:00:{i:02d}Z") for i in range(100)
    ]
    contents = {f"item-{i:03d}": f"payload-{i}".encode() for i in range(100)}
    source = FakeSourceConnector(name="long-batch-source", events=events, content=contents)
    extractor = FakeExtractor()
    # fail_on_call=51 → Silver raises on the 51st call (item index 50,
    # the first item of chunk-2 when chunk_size=50). Chunk-1 has
    # already committed; only chunk-2 (the failing single item) rolls
    # back. This is the post-#321 fix's exact contract.
    silver = _SilverFailingOnNthCall(fail_on_call=51)

    pipeline = _build_pipeline(db, bronze_root, silver)

    with pytest.raises(RuntimeError, match="silver-failing-on-call"):
        pipeline.run_batch(source, extractor)

    on_disk = _count_on_disk_blobs(bronze_root, "long-batch-source")
    registered = _count_bronze_records(db, "long-batch-source")
    orphans = on_disk - registered

    # The intended bound: ADR-018 Wave 1 ships chunking with package size
    # 50. A failure on item 50 should leave at most 49 orphans (the
    # current uncommitted chunk). Earlier chunks were already committed.
    expected_chunk_size = 50
    assert orphans < expected_chunk_size, (
        f"#321 — silver failure mid-batch left {orphans} orphans on disk; "
        f"expected fewer than {expected_chunk_size} with bounded load packages. "
        f"on_disk={on_disk} bronze_records={registered}"
    )

    db.close()


def test_silver_failure_post_fix_committed_chunks_survive(tmp_path: Path) -> None:
    """#321 fix companion: prove the EARLIER chunks survive the failure
    of a later chunk. With chunk_size=50 and Silver failing on item 50,
    the first chunk (items 0..49) commits and stays put; the second
    chunk (item 50 only, where Silver raises) rolls back.

    Pre-fix behaviour (regression-locked): all 50 fetched blobs are
    orphans and zero bronze_records survive. Sabotage: revert the
    pipeline.py _process_batch change to its single-commit shape and
    this test fails with bronze_records=0.
    """
    db_path = tmp_path / "connector_pipeline.sqlite"
    db = sqlite3.connect(str(db_path))
    create_schema(db)
    bronze_root = tmp_path / "bronze"

    events = [
        ChangeEvent(op="created", item_id=f"item-{i:03d}", modified_at=f"2026-01-01T00:00:{i:02d}Z") for i in range(100)
    ]
    contents = {f"item-{i:03d}": f"payload-{i}".encode() for i in range(100)}
    source = FakeSourceConnector(name="long-batch-source-bug", events=events, content=contents)
    extractor = FakeExtractor()
    # fail_on_call=51 → Silver raises on the 51st call (item index 50,
    # the first item of chunk-2 when chunk_size=50). Chunk-1 has
    # already committed; only chunk-2 (the failing single item) rolls
    # back. This is the post-#321 fix's exact contract.
    silver = _SilverFailingOnNthCall(fail_on_call=51)

    pipeline = _build_pipeline(db, bronze_root, silver)

    with pytest.raises(RuntimeError, match="silver-failing-on-call"):
        pipeline.run_batch(source, extractor)

    on_disk = _count_on_disk_blobs(bronze_root, "long-batch-source-bug")
    registered = _count_bronze_records(db, "long-batch-source-bug")

    # Post-#321: items 0..49 (the first chunk) committed before the
    # second chunk started. Item 50 (the only item in chunk-2) had its
    # bronze blob fsynced then Silver raised → chunk-2 rolls back and
    # leaves 1 orphan. on_disk = 51 (50 committed + 1 orphan);
    # bronze_records = 50.
    assert registered == 50, (
        f"expected 50 bronze_records from the committed first chunk; got {registered}. "
        f"If this assertion fails with 0, the chunking fix has regressed (see #321)."
    )
    orphans = on_disk - registered
    assert orphans <= 1, f"expected at most 1 orphan (the failing item from chunk-2); got {orphans}"

    db.close()

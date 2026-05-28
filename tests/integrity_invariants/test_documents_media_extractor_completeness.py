"""Invariant: every successfully-extracted content row has a documents_media row.

Why
---
ADR-024 §"Defects" — GH #336: the ``documents_media`` table shipped in
Wave 1 with rich extractor-version + per-document-status columns, but
no code INSERTed into it. Production accumulated ~1M chunks across 4
years with zero documents_media rows; per-extractor analytics + F40
re-extract triage were structurally impossible. F70 catches the
missing-writer shape *statically*; this invariant catches the failure
mode at runtime — even with the writer wired in, every successful
content row must have a paired documents_media row with a non-null
``extractor_name``.

The mechanical contract: after the connector pipeline lands N
successfully-extracted items,

    |distinct content_hash in bronze_records that produced content|
        ==
    |distinct hash in documents_media WHERE extractor_name IS NOT NULL
        AND extraction_status = 'ok'|

The ``extractor_name`` non-null clause is what catches the GH #336
shape — a documents_media row with NULL extractor identity is as bad
as no row at all for per-extractor analytics.

Sabotage proof (executed during authoring)
------------------------------------------
Mutation: in ``kairix/core/connectors/silver.py:DefaultSilverProcessor.process``,
comment out the ``self._documents_media_writer.write(...)`` line (or
wrap it in ``if False:``). Re-run this test:

    AssertionError: documents_media_extractor_completeness violated:
      10 successfully-extracted content rows but only 0 have a
      documents_media row with extractor_name set. Missing: 10 —
      per-extractor analytics blank, F40 re-extract triage broken.

Restoration: revert. The mismatch surfaces because the LEFT JOIN over
documents_media returns 10 NULLs.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from kairix.core.db.schema import create_schema
from kairix.core.factory import build_connector_pipeline
from kairix.core.protocols import ChangeEvent
from tests.fakes import FakeChunkWriter, FakeEntityGraphSink, FakeExtractor, FakeSourceConnector

pytestmark = pytest.mark.invariant


def _open_db(tmp_path: Path, name: str = "media_completeness.sqlite") -> sqlite3.Connection:
    db = sqlite3.connect(str(tmp_path / name))
    create_schema(db, dims=4)
    return db


def _run_batch(
    db: sqlite3.Connection,
    *,
    source_name: str,
    n: int,
) -> int:
    """Drive N unique items through the composed pipeline; return processed count.

    Uses ``factory.build_connector_pipeline`` with the default Silver
    processor (which is the one wired with SqliteDocumentsMediaWriter
    per GH #336 / ADR-024 Bundle B). Passing ``chunk_writer=FakeChunkWriter()``
    keeps the documents/content writes out of scope — the invariant
    targets the silver-side documents_media write, not the chunk-writer
    side. We still assert against the real ``bronze_records`` +
    ``documents_media`` tables.
    """
    events: list[ChangeEvent] = []
    content: dict[str, bytes] = {}
    for i in range(n):
        item_id = f"media-doc-{i:05d}.md"
        events.append(ChangeEvent(op="modified", item_id=item_id, modified_at=f"2026-05-28T13:00:{i % 60:02d}Z"))
        content[item_id] = (
            f"successfully-extracted body for {item_id} — unique per item to produce distinct content_hash values."
        ).encode()
    pipeline = build_connector_pipeline(
        db=db,
        collection="media-completeness-invariant",
        chunk_writer=FakeChunkWriter(),
        entity_graph_sink=FakeEntityGraphSink(),
    )
    connector = FakeSourceConnector(
        name=source_name,
        events=events,
        content=content,
        cursor_token=f"{source_name}-cursor-1",
        per_tick_max_items=max(n, 1),
    )
    result = pipeline.run_batch(connector, FakeExtractor())
    return result.processed


def _count_successful_extractions(db: sqlite3.Connection, source_name: str) -> int:
    """Distinct content_hash values from bronze rows that successfully extracted.

    "Successfully extracted" == bronze.content_hash is non-null AND
    appears in the canonical post-extraction surface (here, the
    documents_media table when extraction_status='ok'). We anchor the
    count to bronze so the assertion catches the "extracted but
    documents_media write skipped" failure mode — bronze counts each
    successful fetch + extract, documents_media counts each successful
    silver pass.
    """
    row = db.execute(
        "SELECT COUNT(DISTINCT content_hash) FROM bronze_records WHERE source_name = ? AND content_hash IS NOT NULL",
        (source_name,),
    ).fetchone()
    return int(row[0]) if row else 0


def _count_documents_media_with_extractor(db: sqlite3.Connection) -> int:
    """Distinct hash values in documents_media with non-null extractor_name AND status=ok.

    The extractor_name non-null clause catches the GH #336 shape where
    a row exists but the extractor identity wasn't threaded through.
    """
    row = db.execute(
        "SELECT COUNT(DISTINCT hash) FROM documents_media WHERE extractor_name IS NOT NULL AND extraction_status = 'ok'"
    ).fetchone()
    return int(row[0]) if row else 0


def _assert_completeness(db: sqlite3.Connection, source_name: str) -> None:
    """Assert the bronze-vs-documents_media count agreement for successful extractions."""
    successful = _count_successful_extractions(db, source_name)
    media_with_extractor = _count_documents_media_with_extractor(db)
    assert successful > 0, (
        "documents_media_extractor_completeness fixture-setup invariant: "
        "expected at least one successfully-extracted bronze row; got zero. "
        "Verify FakeExtractor produced non-empty markdown for the seeded items."
    )
    missing = successful - media_with_extractor
    assert missing == 0, (
        f"documents_media_extractor_completeness violated: {successful} "
        f"successfully-extracted content rows but only {media_with_extractor} "
        f"have a documents_media row with extractor_name set. Missing={missing} "
        f"— per-extractor analytics blank, F40 re-extract triage broken. "
        f"See ADR-024 §F70 / GH #336 — this is the 4-year "
        f"~1M-chunks-zero-media-rows shape."
    )


def test_invariant_holds_at_fixture_scale(tmp_path: Path) -> None:
    """N=15 successful extracts: every one lands a documents_media row with extractor identity."""
    db = _open_db(tmp_path)
    try:
        processed = _run_batch(db, source_name="media-completeness-fixture", n=15)
        assert processed == 15, (
            f"fixture self-check: expected 15 processed items, got {processed} — "
            f"pipeline dead-lettered when it should have processed"
        )
        _assert_completeness(db, "media-completeness-fixture")
    finally:
        db.close()


@pytest.mark.soak
def test_invariant_holds_at_soak_scale(tmp_path: Path) -> None:
    """N=10**4 successful extracts: documents_media writer never skips at scale.

    The pipeline commits per ``chunk_size`` (default 50) so 10**4 items
    cross ~200 commit boundaries. A regression where the silver write
    survives the bronze write but not the documents_media write (a
    half-rolled-back commit) surfaces here as missing rows.
    """
    db = _open_db(tmp_path, name="media_completeness_soak.sqlite")
    try:
        n = 10_000
        processed = _run_batch(db, source_name="media-completeness-soak", n=n)
        assert processed == n, (
            f"soak self-check: expected {n} processed items, got {processed} — pipeline dead-lettered at scale"
        )
        _assert_completeness(db, "media-completeness-soak")
    finally:
        db.close()

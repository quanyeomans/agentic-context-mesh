"""End-to-end composed production path for chunk-expansion (PLA-268, F48 sibling).

Exercises the full composed path that makes ``expand`` meaningful:

  real connector pipeline (``factory.build_connector_pipeline``)
    → real silver chunking of a multi-paragraph document
    → real ``_SqliteChunkWriter`` persisting ``<source_uri>#<seq>`` rows
    → real ``SQLiteDocumentRepository.get_by_path`` backbone
    → ``run_expand`` returning the neighbour window

This is the contract a per-surface unit test can't prove: that the chunk
KEY the writer enumerates (``<source_uri>#<seq>``) is the exact key
``run_expand`` reads back. If the chunk-path format ever drifts, this test
fails instead of expand silently returning nothing.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from kairix.core.db.repository import SQLiteDocumentRepository
from kairix.core.db.schema import create_schema
from kairix.core.factory import build_connector_pipeline
from kairix.core.protocols import ChangeEvent
from kairix.use_cases.expand import ExpandDeps, run_expand
from tests.fakes import FakeExtractor, FakeSourceConnector

pytestmark = pytest.mark.e2e

_COLLECTION = "obsidian"
_ITEM = "long-note.md"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _multi_paragraph_markdown() -> bytes:
    """A document large enough that silver chunking splits it into >=3 chunks."""
    # Each paragraph is ~900 chars; the ~1000-char chunk target flushes one
    # paragraph per chunk (see kairix.core.connectors.silver._chunk_markdown).
    paragraphs = [
        "Section one discusses the architecture. " * 23,
        "Section two covers the retrieval pipeline. " * 21,
        "Section three explains the expansion tool. " * 21,
    ]
    return ("\n\n".join(p.strip() for p in paragraphs)).encode("utf-8")


def test_connector_ingest_then_expand_returns_neighbours(tmp_path: Path) -> None:
    db_path = tmp_path / "index.sqlite"
    db = sqlite3.connect(str(db_path), timeout=10.0)
    try:
        create_schema(db)
        connector = FakeSourceConnector(
            name=_COLLECTION,
            events=[ChangeEvent(op="created", item_id=_ITEM, modified_at=_now())],
            content={_ITEM: _multi_paragraph_markdown()},
        )
        pipeline = build_connector_pipeline(db=db, collection=_COLLECTION)
        result = pipeline.run_batch(connector, FakeExtractor())
        db.commit()
        assert result.processed == 1, f"expected 1 item processed; got {result}"

        # Discover the source_uri + chunk seqs the writer actually produced —
        # the composed path owns the key format, the test must not hardcode it.
        rows = db.execute(
            "SELECT source_uri, path FROM documents WHERE collection = ? AND active = 1 ORDER BY path",
            (_COLLECTION,),
        ).fetchall()
    finally:
        db.close()

    source_uri = rows[0][0]
    assert source_uri, f"connector wrote no source_uri; rows={rows!r}"
    assert len(rows) >= 3, f"expected a multi-chunk document; got {len(rows)} chunks: {rows!r}"

    # Real backbone: SQLiteDocumentRepository.get_by_path against the on-disk index.
    repo = SQLiteDocumentRepository(db_path)
    out = run_expand(source_uri, 1, token_budget=10_000, deps=ExpandDeps(get_chunk=repo.get_by_path))

    assert out.error == "", f"expand errored: {out.error!r}"
    seqs = [c.seq for c in out.chunks]
    # The matched chunk (1) plus at least its two neighbours (0 and 2), ordered.
    assert {0, 1, 2}.issubset(set(seqs)), f"expected the neighbour window; got {seqs!r}"
    assert seqs == sorted(seqs)
    matched = [c.seq for c in out.chunks if c.is_match]
    assert matched == [1]
    # Every returned row resolves to the same canonical source_uri breadcrumb.
    assert all(c.source_ref().source_uri == source_uri for c in out.chunks)

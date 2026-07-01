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
from kairix.core.factory import (
    build_connector_pipeline,
    build_search_pipeline,
    reset_search_pipeline_cache,
)
from kairix.core.protocols import ChangeEvent
from kairix.core.search.config import RetrievalConfig
from kairix.use_cases.expand import ExpandDeps, run_expand
from tests.fakes import (
    FakeExtractor,
    FakePaths,
    FakeProvider,
    FakeProviderRegistry,
    FakeSourceConnector,
)

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


def _populate_fts(db: sqlite3.Connection) -> None:
    """Populate ``documents_fts`` from the ingested rows so BM25 can retrieve.

    Mirrors the production FTS-build step the composed search path expects.
    """
    db.execute("DELETE FROM documents_fts")
    db.execute(
        """
        INSERT INTO documents_fts (rowid, filepath, title, doc)
        SELECT d.id, d.path, d.title, c.doc
        FROM documents d
        JOIN content c ON c.hash = d.hash
        WHERE d.active = 1
        """
    )


def test_search_then_expand_by_source_uri_round_trip(tmp_path: Path) -> None:
    """Composed search → expand round-trip through ``factory.build_*`` (PLA-297).

    Ingests a multi-chunk document, runs the REAL composed search pipeline
    (``factory.build_search_pipeline``) to get a hit, then hands the hit's
    ``source_uri`` — WITH NO seq, exactly as a document / section-level (L2)
    hit arrives — to ``run_expand``. The source_uri-only path resolves the
    document's chunks and returns the ordered neighbour window, so the L2
    handoff never dead-ends.

    Sabotage-proof: forcing the anchor selection in
    ``kairix.use_cases.expand._expand_by_source_uri`` to ``seqs[-1]`` (the
    last chunk instead of the first) flips the match marker off chunk 0 and
    truncates the window, failing the ``matched == [0]`` + ordered-window
    asserts below.
    """
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
        _populate_fts(db)
        db.commit()
        source_uri = db.execute(
            "SELECT source_uri FROM documents WHERE collection = ? AND active = 1 ORDER BY path LIMIT 1",
            (_COLLECTION,),
        ).fetchone()[0]
    finally:
        db.close()
    assert source_uri, "connector wrote no source_uri"

    paths = FakePaths(
        document_root=tmp_path / "vault",
        db_path=db_path,
        log_dir=tmp_path / "logs",
        workspace_root=tmp_path / "workspaces",
    )
    reset_search_pipeline_cache()
    registry = FakeProviderRegistry({"fake": FakeProvider(name="fake", vector=[0.1] * 1536, dim=1536)})
    search = build_search_pipeline(config=RetrievalConfig(provider="fake"), registry=registry, paths=paths)

    hits = search.search(query="retrieval pipeline", budget=3000)
    assert hits.results, f"composed search returned nothing: error={hits.error!r}"
    # The hit's canonical breadcrumb is the DOCUMENT source_uri — an L2 hit
    # carries it with seq=None. Take it straight from the composed result.
    hit_source_uri = str(getattr(getattr(hits.results[0], "result", None), "source_uri", "") or "")
    assert hit_source_uri == source_uri, f"hit breadcrumb {hit_source_uri!r} != ingested {source_uri!r}"

    # The L2 handoff: expand by source_uri ALONE (no seq) — must not dead-end.
    repo = SQLiteDocumentRepository(db_path)
    out = run_expand(
        hit_source_uri,
        None,
        token_budget=10_000,
        deps=ExpandDeps(get_chunk=repo.get_by_path, list_chunk_seqs=repo.list_chunk_seqs),
    )
    assert out.error == "", f"source_uri-only expand errored: {out.error!r}"
    assert out.no_finer_chunks is False, "a chunked document must not report doc-level-only"
    seqs = [c.seq for c in out.chunks]
    assert seqs == sorted(seqs), f"window not ordered: {seqs!r}"
    assert seqs[0] == 0, f"expected the window anchored on the first chunk; got {seqs!r}"
    matched = [c.seq for c in out.chunks if c.is_match]
    assert matched == [0], f"expected chunk 0 anchored as the match; got {matched!r}"

"""Contract: the ADR-028 Wave F.4 re-chunk sweep.

Exercises the Silver pipeline + chunk writer + sweep against an in-memory
SQLite schema: a document whose recorded chunker version is behind the
registry is re-chunked from its persisted ``silver_source`` markdown; a
converged document is left alone; paged formats and docs without a source
row are skipped; empty re-chunks and per-doc failures are counted; the
per-tick cursor advances and wraps.
"""

from __future__ import annotations

import sqlite3

import pytest

from kairix.core.connectors.chunker_registry import PPTX_MIME, build_default_registry
from kairix.core.connectors.collection_router import legacy_chunk_writer
from kairix.core.connectors.rechunk_sweep import (
    expected_chunker_version,
    run_rechunk_sweep,
    scan_candidates,
)
from kairix.core.connectors.silver import (
    DefaultSilverProcessor,
    SqliteDocumentsMediaWriter,
    SqliteSilverSourceWriter,
)
from kairix.core.db.schema import create_schema
from kairix.core.embed.schema import get_pending_chunks
from kairix.core.protocols import BronzeRef, DocMetadata, ExtractedDocument

pytestmark = pytest.mark.contract

_TS = "2026-06-25T00:00:00Z"
_MD = "# Heading\n\nFirst paragraph body text.\n\nSecond paragraph body text."


def _db() -> sqlite3.Connection:
    db = sqlite3.connect(":memory:")
    create_schema(db)
    return db


def _ingest(db, *, content_hash, source_uri, registry, kind="obsidian", mime="text/markdown", markdown=_MD) -> None:
    silver = DefaultSilverProcessor(
        documents_media_writer=SqliteDocumentsMediaWriter(db),
        silver_source_writer=SqliteSilverSourceWriter(db),
        chunker_registry=registry,
    )
    raw = BronzeRef(
        source_name=kind,
        item_id=f"item-{content_hash}",
        raw_path=None,
        mime=mime,
        fetched_at=_TS,
        content_hash=content_hash,
    )
    extracted = ExtractedDocument(
        markdown=markdown,
        pages=(),
        images=(),
        metadata=DocMetadata(title="T", author=None, created_date=None, language=None, page_count=None),
        confidence=1.0,
    )
    out = silver.process(
        raw=raw, extracted=extracted, source_uri=source_uri, source_modified_at=_TS, sensitivity="internal"
    )
    legacy_chunk_writer(db, collection="default").upsert(out.chunks)
    db.commit()


def _make_stale(db, content_hash, version="stale-v0") -> None:
    db.execute("UPDATE documents_media SET chunker_version = ? WHERE hash = ?", (version, content_hash))
    db.commit()


def _media_version(db, content_hash):
    return db.execute("SELECT chunker_version FROM documents_media WHERE hash = ?", (content_hash,)).fetchone()[0]


def _chunk_count(db, source_uri) -> int:
    return db.execute("SELECT COUNT(*) FROM documents WHERE source_uri = ?", (source_uri,)).fetchone()[0]


def test_stale_doc_is_rechunked_and_version_converges() -> None:
    db = _db()
    registry = build_default_registry()
    _ingest(db, content_hash="rawA", source_uri="obsidian://note-a", registry=registry)
    expected = expected_chunker_version(registry, kind="obsidian", mime="text/markdown")
    _make_stale(db, "rawA")

    result = run_rechunk_sweep(db, cap=100, registry=registry)

    assert result.stale == 1
    assert result.rechunked == 1
    assert result.failed == 0
    assert _media_version(db, "rawA") == expected, "documents_media must converge to the registry version"
    assert _chunk_count(db, "obsidian://note-a") >= 1, "the doc keeps its chunks after re-chunk"


def test_converged_doc_is_left_alone() -> None:
    db = _db()
    registry = build_default_registry()
    _ingest(db, content_hash="rawB", source_uri="obsidian://note-b", registry=registry)
    # ingested WITH the registry => already at the expected version, not stale.

    result = run_rechunk_sweep(db, cap=100, registry=registry)

    assert result.scanned == 1
    assert result.stale == 0
    assert result.rechunked == 0


def test_rechunked_chunks_are_discoverable_for_embedding() -> None:
    db = _db()
    registry = build_default_registry()
    _ingest(db, content_hash="rawC", source_uri="obsidian://note-c", registry=registry)
    _make_stale(db, "rawC")
    run_rechunk_sweep(db, cap=100, registry=registry)

    discovered = {row["hash"] for row in get_pending_chunks(db)}
    doc_hashes = {r[0] for r in db.execute("SELECT hash FROM documents WHERE source_uri = ?", ("obsidian://note-c",))}
    assert doc_hashes, "re-chunk must leave documents rows"
    assert doc_hashes <= discovered, "every re-chunked chunk is un-embedded (the embed worker picks it up)"


def _seed_paged(db, *, content_hash, source_uri, mime, version) -> None:
    db.execute(
        "INSERT INTO documents_media (hash, path, format, extraction_status, chunker_version) "
        "VALUES (?, ?, ?, 'ok', ?)",
        (content_hash, f"item-{content_hash}", mime, version),
    )
    db.execute(
        "INSERT INTO silver_source (hash, source_uri, markdown, created_at) VALUES (?, ?, ?, ?)",
        (content_hash, source_uri, "slide one\n\nslide two", _TS),
    )
    db.execute(
        "INSERT INTO documents (collection, path, hash, source_name, source_uri, source_modified_at, "
        "source_page, sensitivity, created_at, modified_at, active) "
        "VALUES ('default', ?, ?, 'sharepoint', ?, ?, NULL, 'internal', ?, ?, 1)",
        (f"{source_uri}#0", f"chunk-{content_hash}", source_uri, _TS, _TS, _TS),
    )
    db.commit()


def test_paged_doc_is_skipped_not_rechunked() -> None:
    db = _db()
    registry = build_default_registry()
    _seed_paged(db, content_hash="rawP", source_uri="sharepoint://deck", mime=PPTX_MIME, version="stale-v0")

    result = run_rechunk_sweep(db, cap=100, registry=registry)

    assert result.skipped_paged == 1
    assert result.rechunked == 0
    assert _media_version(db, "rawP") == "stale-v0", "a skipped paged doc is left unchanged"


def test_doc_without_silver_source_is_not_scanned() -> None:
    db = _db()
    registry = build_default_registry()
    db.execute(
        "INSERT INTO documents_media (hash, path, format, extraction_status, chunker_version) "
        "VALUES ('rawX', 'item-x', 'text/markdown', 'ok', 'stale-v0')",
    )
    db.commit()

    stale, scanned, _ = scan_candidates(db, registry, cap=100, cursor="")

    assert scanned == 0, "documents_media rows without a silver_source row are invisible to the sweep"
    assert stale == []


def _seed_direct(db, *, content_hash, source_uri, mime, markdown, version) -> None:
    db.execute(
        "INSERT INTO documents_media (hash, path, format, extraction_status, chunker_version) "
        "VALUES (?, ?, ?, 'ok', ?)",
        (content_hash, f"item-{content_hash}", mime, version),
    )
    db.execute(
        "INSERT INTO silver_source (hash, source_uri, markdown, created_at) VALUES (?, ?, ?, ?)",
        (content_hash, source_uri, markdown, _TS),
    )
    db.execute(
        "INSERT INTO documents (collection, path, hash, source_name, source_uri, source_modified_at, "
        "source_page, sensitivity, created_at, modified_at, active) "
        "VALUES ('default', ?, ?, 'obsidian', ?, ?, NULL, 'internal', ?, ?, 1)",
        (f"{source_uri}#0", f"chunk-{content_hash}", source_uri, _TS, _TS, _TS),
    )
    db.commit()


def test_empty_rechunk_is_counted_as_skipped() -> None:
    db = _db()
    registry = build_default_registry()
    # Whitespace-only markdown is truthy (so silver_source was written) but
    # re-chunks to zero chunks — the doc is left deleted and counted skipped.
    _seed_direct(
        db,
        content_hash="rawE",
        source_uri="obsidian://empty",
        mime="text/markdown",
        markdown="   \n\n  ",
        version="stale-v0",
    )

    result = run_rechunk_sweep(db, cap=100, registry=registry)

    assert result.stale == 1
    assert result.rechunked == 0
    assert result.skipped_empty == 1
    assert _chunk_count(db, "obsidian://empty") == 0, "an empty re-chunk leaves no chunk rows"


def test_per_doc_failure_is_isolated_and_counted() -> None:
    db = _db()
    registry = build_default_registry()
    # A healthy stale doc that re-chunks cleanly...
    _ingest(db, content_hash="rawOK", source_uri="obsidian://ok", registry=registry)
    _make_stale(db, "rawOK")
    # ...and a poisoned one whose re-chunk raises: drop documents_fts so the
    # chunk writer's FTS insert fails for the bad doc's source_uri. (Both share
    # the table, but the healthy doc is processed in its own committed txn first
    # by hash order — rawBad < rawOK lexically, so rawBad fails, rawOK is fine.)
    _seed_direct(
        db,
        content_hash="rawBad",
        source_uri="obsidian://bad",
        mime="text/markdown",
        markdown=_MD,
        version="stale-v0",
    )
    db.execute("DROP TABLE documents_fts")
    db.commit()

    result = run_rechunk_sweep(db, cap=100, registry=registry)

    assert result.failed >= 1, "a per-doc re-chunk failure is isolated + counted, not propagated"


def test_cursor_advances_then_wraps_to_head() -> None:
    db = _db()
    registry = build_default_registry()
    for h in ("raw1", "raw2", "raw3"):
        _ingest(db, content_hash=h, source_uri=f"obsidian://{h}", registry=registry)

    _stale, scanned, cursor = scan_candidates(db, registry, cap=2, cursor="")
    assert scanned == 2
    assert cursor == "raw2", "cursor advances to the last scanned hash"

    _stale2, scanned2, cursor2 = scan_candidates(db, registry, cap=2, cursor=cursor)
    assert scanned2 == 1
    assert cursor2 == "", "a partial final page wraps the cursor back to the table head"

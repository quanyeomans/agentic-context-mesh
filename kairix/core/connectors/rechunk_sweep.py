"""ADR-028 Wave F.4 — the re-chunk sweep.

Re-chunks already-ingested connector documents whose recorded
``documents_media.chunker_version`` no longer matches the version the
current chunker registry would produce for their ``(kind, mime)``. This
lets a chunker improvement propagate across the existing corpus WITHOUT
re-fetching from the remote connector: the source markdown persisted at
ingest (``silver_source``, ADR-028 Wave F.4) is the re-chunk input.

Mechanics, per stale document:

1. Delete the document's existing chunk rows via
   ``ChunkWriter.delete_by_source_uri`` (clears ``documents`` + ``documents_fts``;
   the orphaned ``content_vectors`` are reclaimed by the existing bounded
   orphan-prune maintenance tick, so the sweep writes no bespoke deletes).
2. Reconstruct the ``ExtractedDocument`` from ``silver_source.markdown`` and
   re-run :class:`DefaultSilverProcessor.process` with the current registry.
3. ``upsert`` the new chunks — un-embedded. The independent embed worker
   embeds them on its own cycle (no inline embed -> no #352 OOM risk).

Boundedness (F66): each tick scans at most ``cap`` documents starting from a
persisted ``kairix_meta`` cursor, so the sweep walks the whole corpus over
successive ticks rather than re-scanning the table head every tick. Paged
formats (PPTX/XLSX/DOCX) are skipped — their chunkers need ``extracted.pages``,
which the worker ingest path does not persist; they are deferred to the
operator re-fetch path.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

from kairix.core.connectors.chunker_registry import (
    DOCX_MIME,
    LEGACY_XLS_MIME,
    PPTX_MIME,
    XLSX_MIME,
    ChunkerRegistry,
    build_default_registry,
)
from kairix.core.protocols import BronzeRef, DocMetadata, ExtractedDocument, Sensitivity

if TYPE_CHECKING:
    from kairix.core.connectors.silver import DefaultSilverProcessor

# Paged formats need ``extracted.pages`` (not persisted by the worker ingest
# path) to re-chunk faithfully; the sweep skips them. Deferred to the
# operator re-fetch path (kairix curator rechunk).
PAGED_MIMES = frozenset({PPTX_MIME, XLSX_MIME, LEGACY_XLS_MIME, DOCX_MIME})

_CURSOR_KEY = "rechunk_sweep_cursor"
_EXTRACTION_OK = "ok"

# Bounded per-tick scan starting after the persisted cursor (F66 + watermark).
_DISCOVERY_SQL = """
SELECT dm.hash, dm.format, dm.path, dm.chunker_version,
       dm.title, dm.author, dm.created_date, dm.language, dm.page_count,
       dm.extractor_name, dm.extractor_version,
       ss.source_uri, ss.markdown
FROM documents_media dm
JOIN silver_source ss ON ss.hash = dm.hash
WHERE dm.extraction_status = ?
  AND ss.source_uri IS NOT NULL
  AND dm.hash > ?
ORDER BY dm.hash
LIMIT ?
"""

# Per-document chunk context (same across a doc's chunks). LIMIT 1 — F63-bounded.
_DOC_CONTEXT_SQL = """
SELECT collection, source_name, source_modified_at, sensitivity
FROM documents
WHERE source_uri = ? AND active = 1
LIMIT 1
"""


@dataclass(frozen=True)
class StaleDoc:
    """A document whose recorded chunker version is behind the registry."""

    content_hash: str
    source_uri: str
    item_id: str
    kind: str
    mime: str
    markdown: str
    collection: str
    source_modified_at: str
    sensitivity: Sensitivity
    title: str | None
    author: str | None
    created_date: str | None
    language: str | None
    page_count: int | None
    extractor_name: str | None
    extractor_version: str | None
    current_version: str | None
    expected_version: str


@dataclass(frozen=True)
class RechunkSweepResult:
    """Outcome of one re-chunk sweep tick."""

    scanned: int
    stale: int
    rechunked: int
    skipped_paged: int
    skipped_empty: int
    failed: int
    cursor_advanced_to: str


def expected_chunker_version(registry: ChunkerRegistry, *, kind: str, mime: str) -> str:
    """The version the current registry would stamp for ``(kind, mime)``.

    Unregistered pairs resolve to the paragraph fallback (version ``"1"``),
    so "expected" is always defined.
    """
    return registry.dispatch(kind=kind, mime=mime, section_kind="text").version


def _read_cursor(db: sqlite3.Connection) -> str:
    row = db.execute("SELECT value FROM kairix_meta WHERE key = ?", (_CURSOR_KEY,)).fetchone()
    return row[0] if row and row[0] is not None else ""


def _write_cursor(db: sqlite3.Connection, value: str) -> None:
    db.execute(
        "INSERT OR REPLACE INTO kairix_meta (key, value) VALUES (?, ?)",
        (_CURSOR_KEY, value),
    )


def _doc_context(db: sqlite3.Connection, source_uri: str) -> tuple[str, str, str, str] | None:
    row = db.execute(_DOC_CONTEXT_SQL, (source_uri,)).fetchone()
    return tuple(row) if row is not None else None


def scan_candidates(
    db: sqlite3.Connection,
    registry: ChunkerRegistry,
    *,
    cap: int,
    cursor: str,
) -> tuple[list[StaleDoc], int, str]:
    """Scan up to ``cap`` documents after ``cursor``; return stale ones.

    Returns ``(stale, scanned, next_cursor)``. ``next_cursor`` advances to the
    last scanned hash, or resets to ``""`` (wrap to table head) when fewer than
    ``cap`` rows remain — so the sweep cycles the whole corpus.
    """
    # F63-bounded: _DISCOVERY_SQL carries ``LIMIT ?`` (= cap); the per-tick cap
    # bounds the scan and the cursor walks the table head over successive ticks.
    rows = db.execute(_DISCOVERY_SQL, (_EXTRACTION_OK, cursor, cap)).fetchall()
    stale: list[StaleDoc] = []
    for row in rows:
        doc = _candidate_to_stale(db, registry, row)
        if doc is not None:
            stale.append(doc)
    next_cursor = "" if len(rows) < cap else rows[-1][0]
    return stale, len(rows), next_cursor


def _candidate_to_stale(
    db: sqlite3.Connection,
    registry: ChunkerRegistry,
    row: tuple[Any, ...],
) -> StaleDoc | None:
    """Resolve one discovery row to a :class:`StaleDoc`, or None if converged."""
    (
        content_hash,
        mime,
        item_id,
        current_version,
        title,
        author,
        created_date,
        language,
        page_count,
        extractor_name,
        extractor_version,
        source_uri,
        markdown,
    ) = row
    ctx = _doc_context(db, source_uri)
    if ctx is None:  # no live chunks for this source_uri (already deleted)
        return None
    collection, kind, source_modified_at, sensitivity = ctx
    expected = expected_chunker_version(registry, kind=kind, mime=mime)
    if current_version == expected:  # already on the current version
        return None
    return StaleDoc(
        content_hash=content_hash,
        source_uri=source_uri,
        item_id=item_id,
        kind=kind,
        mime=mime,
        markdown=markdown,
        collection=collection,
        source_modified_at=source_modified_at or "",
        sensitivity=cast(Sensitivity, sensitivity or "internal"),
        title=title,
        author=author,
        created_date=created_date,
        language=language,
        page_count=page_count,
        extractor_name=extractor_name,
        extractor_version=extractor_version,
        current_version=current_version,
        expected_version=expected,
    )


def _build_silver(db: sqlite3.Connection, registry: ChunkerRegistry) -> DefaultSilverProcessor:
    from kairix.core.connectors.silver import (
        DefaultSilverProcessor,
        SqliteDocumentsMediaWriter,
        SqliteSilverSourceWriter,
    )

    return DefaultSilverProcessor(
        documents_media_writer=SqliteDocumentsMediaWriter(db),
        silver_source_writer=SqliteSilverSourceWriter(db),
        chunker_registry=registry,
    )


def rechunk_doc(db: sqlite3.Connection, registry: ChunkerRegistry, doc: StaleDoc) -> bool:
    """Delete + re-chunk one document from its persisted source markdown.

    Returns True when new chunks were written, False when re-chunking produced
    no chunks (the doc is left deleted — caller counts it as ``skipped_empty``).
    Does NOT commit; the caller owns the per-document transaction boundary.
    """
    from kairix.core.connectors.collection_router import legacy_chunk_writer

    silver = _build_silver(db, registry)
    chunk_writer = legacy_chunk_writer(db, collection=doc.collection)
    chunk_writer.delete_by_source_uri(doc.source_uri)

    raw = BronzeRef(
        source_name=doc.kind,
        item_id=doc.item_id,
        raw_path=None,
        mime=doc.mime,
        fetched_at=doc.source_modified_at,
        content_hash=doc.content_hash,
    )
    extracted = ExtractedDocument(
        markdown=doc.markdown,
        pages=(),
        images=(),
        metadata=DocMetadata(
            title=doc.title,
            author=doc.author,
            created_date=doc.created_date,
            language=doc.language,
            page_count=doc.page_count,
        ),
        confidence=1.0,
    )
    out = silver.process(
        raw=raw,
        extracted=extracted,
        source_uri=doc.source_uri,
        source_modified_at=doc.source_modified_at,
        sensitivity=doc.sensitivity,
        extractor_name=doc.extractor_name,
        extractor_version=doc.extractor_version,
        extraction_status=_EXTRACTION_OK,
    )
    if not out.chunks:
        return False
    chunk_writer.upsert(out.chunks)
    return True


def run_rechunk_sweep(
    db: sqlite3.Connection,
    *,
    cap: int,
    registry: ChunkerRegistry | None = None,
) -> RechunkSweepResult:
    """Run one bounded re-chunk sweep tick.

    Scans up to ``cap`` documents after the persisted cursor, re-chunks the
    stale non-paged ones (each in its own committed transaction so a single
    failure can't lose prior progress), advances the cursor, and returns the
    per-tick outcome. The embed worker embeds the freshly-written chunks on its
    own cycle.
    """
    registry = registry or build_default_registry()
    cursor = _read_cursor(db)
    stale, scanned, next_cursor = scan_candidates(db, registry, cap=cap, cursor=cursor)

    rechunked = skipped_paged = skipped_empty = failed = 0
    for doc in stale:
        if doc.mime in PAGED_MIMES:
            skipped_paged += 1
            continue
        try:
            if rechunk_doc(db, registry, doc):
                rechunked += 1
            else:
                skipped_empty += 1
            db.commit()
        except (sqlite3.Error, ValueError, KeyError):
            db.rollback()
            failed += 1

    _write_cursor(db, next_cursor)
    db.commit()
    return RechunkSweepResult(
        scanned=scanned,
        stale=len(stale),
        rechunked=rechunked,
        skipped_paged=skipped_paged,
        skipped_empty=skipped_empty,
        failed=failed,
        cursor_advanced_to=next_cursor,
    )

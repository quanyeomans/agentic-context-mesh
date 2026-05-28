"""Silver processing - the SINGULAR chunking + entity-signal extraction surface.

F38 makes this file the one and only home for chunking and entity-
signal extraction in the connector framework. No per-connector
chunker. No per-extractor chunker. The orchestrator
(:mod:`kairix.core.connectors.pipeline`) hands every Bronze record plus
its :class:`~kairix.core.protocols.ExtractedDocument` to
:meth:`SilverProcessor.process`; Silver returns a
:class:`~kairix.core.protocols.SilverOutput` carrying
``(chunks, entity_signals)`` - a tuple of frozen dataclasses per F42.

Plain Python, no LLM (per KFEAT-005). LLM-driven work (fact
extraction in :mod:`kairix.corpus.ingest`, Curator enrichment) stays on
existing surfaces. The connector path and the conversational corpus
path are disjoint.

Entity-signal extraction (v1, IM-2): a paragraph-boundary-aware regex
heuristic. We scan for two-or-three-word Capitalised tokens
(``Jane Smith`` / ``Acme Corp`` / ``First Middle Last``) and tag them as
``person`` candidates; longer sequences ending in an org suffix
(``Corp`` / ``Inc`` / ``Ltd`` / ``LLC``) are tagged ``org``. Real NER
(spaCy / GLiNER) is a Wave 3+ concern; the regex is sufficient to
populate the ``entity_signals`` staging table so the Curator coupling
boundary has a stream to consume.

GH #336 (ADR-024 Bundle B) — Silver also writes a per-document row to
the ``documents_media`` table when a :class:`DocumentsMediaWriter` is
wired in. The writer captures extractor identity, page count,
extraction status (``ok`` / ``failed`` / ``unsupported``), and
ADR-021-merged envelope metadata so re-extract triage + per-extractor
analytics work in production. The writer is optional at construction
time so existing tests + non-pipeline callers continue to work.
"""

from __future__ import annotations

import hashlib
import re
import sqlite3
from collections.abc import Callable, Sequence
from datetime import datetime, timezone
from typing import Any

from kairix.core.protocols import (
    BronzeRef,
    Chunk,
    EntitySignal,
    ExtractedDocument,
    Page,
    Sensitivity,
    SilverOutput,
    SourceMetadata,
)

# Allowed values for ``documents_media.extraction_status``. ``ok`` and
# ``failed`` mirror the legacy schema default; ``unsupported`` is new
# (per ADR-024 Bundle B / GH #336) — set by the pipeline when no
# extractor in the escalation chain claimed the format via
# ``can_extract`` or when ``quality_ok`` was False across the chain.
_EXTRACTION_STATUS_OK = "ok"
_EXTRACTION_STATUS_FAILED = "failed"
_EXTRACTION_STATUS_UNSUPPORTED = "unsupported"

_ALLOWED_EXTRACTION_STATUSES: frozenset[str] = frozenset(
    {_EXTRACTION_STATUS_OK, _EXTRACTION_STATUS_FAILED, _EXTRACTION_STATUS_UNSUPPORTED}
)

# Target chunk size at paragraph boundaries (characters). Smaller than
# the embed-time chunker (which sees overlap + token semantics); Silver
# only needs to keep chunks search-shaped. Smarter chunking — semantic
# sectioning, heading-aware splitting — is a Wave 3+ concern.
_TARGET_CHUNK_CHARS = 1000

# Capitalised-word entity heuristic. Matches two- or three-token sequences
# where each token is Capitalised (``\b[A-Z][a-z]+``). The trailing token
# may be an org suffix (``Corp`` / ``Inc`` / ``Ltd`` / ``LLC`` / ``GmbH``)
# in which case the match is classified as an org rather than a person.
_ENTITY_RE = re.compile(r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,2})\b")
_ORG_SUFFIXES: frozenset[str] = frozenset({"Corp", "Inc", "Ltd", "LLC", "GmbH", "Plc", "Company", "Group"})


def _chunk_markdown(markdown: str) -> tuple[str, ...]:
    """Split ``markdown`` on paragraph boundaries, ~``_TARGET_CHUNK_CHARS`` each.

    Paragraphs are blocks separated by a blank line. We greedily glue
    paragraphs into the current chunk until adding the next one would
    push it past ``_TARGET_CHUNK_CHARS``; then we flush and start a new
    chunk. Empty input yields an empty tuple.

    Paragraphs longer than ``_TARGET_CHUNK_CHARS`` are pre-split at
    sentence boundaries (then word, then char) BEFORE the greedy-glue
    loop runs — otherwise a single oversized paragraph would land as
    one chunk regardless of the target. ``tests/unit/test_silver.py``
    pins the pathological case.
    """
    text = markdown.strip()
    if not text:
        return ()
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    if not paragraphs:
        return ()

    # Expand any oversized paragraph into sub-paragraphs that fit the
    # target before the greedy-glue loop runs. Each sub-paragraph
    # carries the same paragraph-boundary semantics so the glue loop
    # treats them as siblings.
    expanded: list[str] = []
    for para in paragraphs:
        if len(para) <= _TARGET_CHUNK_CHARS:
            expanded.append(para)
        else:
            expanded.extend(_split_long_paragraph(para, _TARGET_CHUNK_CHARS))

    chunks: list[str] = []
    current = ""
    for para in expanded:
        if not current:
            current = para
            continue
        if len(current) + len(para) + 2 <= _TARGET_CHUNK_CHARS:
            current = current + "\n\n" + para
        else:
            chunks.append(current)
            current = para
    if current:
        chunks.append(current)
    return tuple(chunks)


def _split_long_paragraph(paragraph: str, target: int) -> list[str]:
    """Split ``paragraph`` (longer than ``target``) at sentence boundaries.

    Greedy glue: combine sentences into the current chunk until adding
    the next would push past ``target``, then flush. A sentence that
    is itself longer than ``target`` falls through to word-boundary
    splitting via :func:`_split_long_sentence`.
    """
    # Sentence-boundary regex: split AFTER terminal punctuation followed
    # by whitespace. Lookbehind keeps the punctuation with the sentence.
    sentences = re.split(r"(?<=[.!?])\s+", paragraph)
    return _greedy_glue(sentences, target, separator=" ", split_oversize=_split_long_sentence)


def _split_long_sentence(sentence: str, target: int) -> list[str]:
    """Split ``sentence`` (longer than ``target``) at word boundaries.

    A single word longer than ``target`` (e.g. a long URL) falls
    through to hard char-boundary chunks of exactly ``target``.
    """
    return _greedy_glue(sentence.split(), target, separator=" ", split_oversize=_split_long_word)


def _split_long_word(word: str, target: int) -> list[str]:
    """Hard char-boundary fallback for a single token longer than ``target``."""
    return [word[i : i + target] for i in range(0, len(word), target)]


def _greedy_glue(
    pieces: list[str],
    target: int,
    *,
    separator: str,
    split_oversize: Callable[[str, int], list[str]],
) -> list[str]:
    """Greedy-glue ``pieces`` with ``separator`` up to ``target`` chars.

    If a single piece exceeds ``target``, delegate to ``split_oversize``
    to break it down further. Output chunks are all ``<= target`` (or
    ``<= target`` after the recursive split for the oversize case).
    """
    chunks: list[str] = []
    current = ""
    sep_len = len(separator)
    for piece in pieces:
        if len(piece) > target:
            if current:
                chunks.append(current)
                current = ""
            chunks.extend(split_oversize(piece, target))
            continue
        if not current:
            current = piece
            continue
        if len(current) + len(piece) + sep_len <= target:
            current = current + separator + piece
        else:
            chunks.append(current)
            current = piece
    if current:
        chunks.append(current)
    return chunks


def _chunk_pages(pages: tuple[Page, ...]) -> tuple[tuple[str, int], ...]:
    """Chunk per-page text and attribute each chunk to a page number.

    Each page's text is chunked independently via :func:`_chunk_markdown`
    so chunks never straddle page boundaries — the trivial majority
    attribution rule (a chunk belongs to the page containing the
    majority of its content) collapses to "the page that produced the
    chunk" because we cut at page boundaries before paragraph chunking.

    Returns a tuple of ``(chunk_text, page_number)`` pairs in page-order.
    Pages with empty text produce no chunks. Empty input yields an empty
    tuple — callers should fall through to markdown-only chunking when
    ``pages`` is empty.
    """
    out: list[tuple[str, int]] = []
    for page in pages:
        for chunk_text in _chunk_markdown(page.text):
            out.append((chunk_text, page.page_number))
    return tuple(out)


def _extract_entity_signals(
    text: str,
    *,
    source_uri: str,
    source_modified_at: str,
    sensitivity: Sensitivity,
) -> tuple[EntitySignal, ...]:
    """Capitalised-word entity heuristic over ``text``.

    Returns one :class:`EntitySignal` per distinct match (de-duplicated
    within the call). ``person`` for two-or-three-word Capitalised
    sequences; ``org`` when the trailing token is in ``_ORG_SUFFIXES``.
    Confidence is fixed at ``0.5`` — the heuristic is deliberately
    coarse; real NER (Wave 3+) replaces this with a calibrated score.
    """
    seen: set[tuple[str, str]] = set()
    signals: list[EntitySignal] = []
    for match in _ENTITY_RE.finditer(text):
        value = match.group(1)
        last_token = value.rsplit(" ", maxsplit=1)[-1]
        kind: str = "org" if last_token in _ORG_SUFFIXES else "person"
        key = (kind, value)
        if key in seen:
            continue
        seen.add(key)
        signals.append(
            EntitySignal(
                kind=kind,  # type: ignore[arg-type]  # F3-rationale: kind is Literal["person","org","relationship"]; the heuristic only emits the first two — narrowing handled by the explicit branch above.
                value=value,
                source_uri=source_uri,
                modified_at=source_modified_at,
                confidence=0.5,
                sensitivity=sensitivity,
            )
        )
    return tuple(signals)


def _utc_now_iso() -> str:
    """UTC-now ISO-8601 string with the trailing ``Z`` operators expect.

    Mirrors :func:`kairix.connectors.obsidian.connector._iso_z` /
    :func:`kairix.core.curator.drain._utc_now_iso` — keeping it local
    rather than introducing a shared ``kairix.core.timeutils`` module
    in this commit to minimise blast radius. F70 / GH #336 lands the
    writer; a future commit can DRY the helper.
    """
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class SqliteDocumentsMediaWriter:
    """Production :class:`~kairix.core.connectors.silver.DocumentsMediaWriter`.

    GH #336 (ADR-024 Bundle B) — writes one ``documents_media`` row per
    processed document, keyed by the raw-bytes ``content_hash``. The
    table records extractor identity + per-document status so:

      * F40 re-extract triage can identify documents needing
        re-processing when an extractor version bumps
      * per-extractor analytics (success rate, failure rate per
        format) are observable
      * the canonical ``bronze_records -> documents_media -> content``
        join for \"is this document fully processed?\" returns rows

    The writer does NOT commit — the caller's per-batch transaction
    owns the commit so the documents_media row, chunks, cursor advance,
    and bronze row commit together or roll back together (mirrors
    :class:`~kairix.core.connectors.pipeline.ChunkWriter`).
    """

    def __init__(self, db: sqlite3.Connection) -> None:
        self._db = db

    def write(
        self,
        *,
        content_hash: str,
        path: str,
        mime: str,
        size_bytes: int | None,
        page_count: int | None,
        title: str | None,
        author: str | None,
        created_date: str | None,
        language: str | None,
        extraction_status: str,
        extractor_name: str | None,
        extractor_version: str | None,
        chunker_version: str | None,
    ) -> None:
        """INSERT or REPLACE one ``documents_media`` row for the document.

        ``content_hash`` is the PRIMARY KEY; the same document re-ingested
        (same bytes) updates the existing row so a later re-extract on
        an extractor version bump cleanly replaces the prior status +
        extractor identity rather than accumulating duplicates.
        """
        if extraction_status not in _ALLOWED_EXTRACTION_STATUSES:
            raise ValueError(
                f"extraction_status must be one of {sorted(_ALLOWED_EXTRACTION_STATUSES)!r}; "
                f"got {extraction_status!r}. "
                "fix: pass 'ok' / 'failed' / 'unsupported' from the orchestrator. "
                "run: bash scripts/safe-commit.sh"
            )
        self._db.execute(
            "INSERT OR REPLACE INTO documents_media ("
            "hash, path, format, size_bytes, page_count, title, author, created_date, "
            "language, extraction_status, extraction_timestamp, extractor_name, "
            "extractor_version, chunker_version"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                content_hash,
                path,
                mime,
                size_bytes,
                page_count,
                title,
                author,
                created_date,
                language,
                extraction_status,
                _utc_now_iso(),
                extractor_name,
                extractor_version,
                chunker_version,
            ),
        )


class SqliteDocumentPagesWriter:
    """Production writer for the ``document_pages`` table (GH #338).

    Mirrors :class:`SqliteDocumentsMediaWriter` (ADR-024 Bundle B) for
    the per-page row pattern. Page-bearing extractors (PDF / PPTX /
    DOCX) populate :attr:`ExtractedDocument.pages` with one
    :class:`~kairix.core.protocols.Page` per page / slide / sheet; this
    writer persists each Page as a row keyed by ``(hash, page_number)``
    so downstream retrieval (MM-3 citation paths, per-page snippet
    surfaces, page-bounded re-extract triage) can attribute back to a
    specific page.

    ``image_descriptions`` is intentionally left ``NULL`` at the F70
    paydown — the schema column is forward-armed for a future vision
    extractor that will populate per-image alt-text; today's
    page-bearing extractors emit text only.

    The writer does NOT commit — the caller's per-batch transaction
    owns the commit so all per-document rows (documents_media,
    document_pages, content, content_vectors) commit together or
    roll back together.
    """

    def __init__(self, db: sqlite3.Connection) -> None:
        self._db = db

    def write_pages(
        self,
        *,
        content_hash: str,
        pages: Sequence[Any],
    ) -> int:
        """INSERT or REPLACE one ``document_pages`` row per Page.

        Returns the count of rows written. Caller-supplied ``pages``
        is expected to be a sequence of objects with attributes
        ``page_number: int``, ``text: str``, ``has_images: bool`` —
        the :class:`~kairix.core.protocols.Page` shape. An empty
        sequence is a no-op (non-paged extractors don't write).

        Re-ingesting the same document (same ``content_hash``) updates
        the existing rows via ``INSERT OR REPLACE`` so re-extracts
        cleanly overwrite rather than accumulate duplicates. The
        ``has_images`` boolean is serialised as 0/1 to match the
        schema's INTEGER DEFAULT 0.
        """
        if not pages:
            return 0
        written = 0
        for page in pages:
            self._db.execute(
                "INSERT OR REPLACE INTO document_pages ("
                "hash, page_number, extracted_text, has_images, image_descriptions"
                ") VALUES (?, ?, ?, ?, NULL)",
                (
                    content_hash,
                    int(page.page_number),
                    page.text,
                    1 if page.has_images else 0,
                ),
            )
            written += 1
        return written


class DefaultSilverProcessor:
    """Production :class:`~kairix.core.protocols.SilverProcessor` implementation.

    SINGULAR Silver surface per F38 - chunking + entity-signal
    extraction live ONLY here in production code. Per-connector
    chunkers are a regression and pre-commit blocks them.

    Constructs :class:`~kairix.core.protocols.Chunk` value objects
    carrying ``source_uri``, ``source_modified_at``, and ``sensitivity``
    per F39. ``source_page`` is ``None`` for non-paged formats; paged
    extractors (PDF / PPTX / XLSX) populate ``extracted.pages`` and the
    chunker emits one or more chunks per page, each tagged with the
    page number so retrieval (MM-3) can cite back to a specific page.

    GH #336 (ADR-024 Bundle B) — when a ``documents_media_writer`` is
    wired in (via ``__init__``), Silver writes one ``documents_media``
    row per processed document so per-extractor analytics + F40
    re-extract triage work. ``None`` keeps the legacy behaviour for
    tests / callers that don't need the row.

    GH #338 (F70 paydown) — when a ``document_pages_writer`` is wired
    in, Silver also writes one ``document_pages`` row per page for
    page-bearing extractors. ``None`` keeps the legacy behaviour.
    """

    def __init__(
        self,
        documents_media_writer: SqliteDocumentsMediaWriter | None = None,
        document_pages_writer: SqliteDocumentPagesWriter | None = None,
    ) -> None:
        self._documents_media_writer = documents_media_writer
        self._document_pages_writer = document_pages_writer

    def process(
        self,
        raw: BronzeRef,
        extracted: ExtractedDocument,
        source_uri: str,
        source_modified_at: str,
        sensitivity: Sensitivity,
        connector_metadata: SourceMetadata | None = None,
        extractor_metadata: SourceMetadata | None = None,
        extractor_name: str | None = None,
        extractor_version: str | None = None,
        extraction_status: str = _EXTRACTION_STATUS_OK,
    ) -> SilverOutput:
        """Split ``extracted.markdown`` into chunks; emit entity signals.

        Every chunk carries ``source_uri`` + ``source_modified_at`` +
        ``sensitivity`` per F39. ADR-021 (Wave E.5) adds the merged
        :class:`SourceMetadata` payload — author / author_email / tags /
        properties — derived from ``connector_metadata`` and
        ``extractor_metadata`` with connector > extractor > defaults
        priority. When both metadata arguments are ``None`` the chunks
        carry the legacy single-source shape (no author, empty tags,
        empty metadata mapping).

        When ``extracted.pages`` is non-empty (PDF / PPTX / XLSX
        extractions), chunks are produced per-page and each chunk carries
        its source page number. Chunking cuts at page boundaries before
        applying the paragraph-aware chunker so a chunk never straddles
        two pages — the majority-attribution rule degenerates to "the
        page that produced the chunk" by construction.

        When ``extracted.pages`` is empty (passthrough markdown, flat
        extract), chunks are produced from ``extracted.markdown`` and
        every chunk's ``source_page`` is ``None``.
        """
        merged = _merge_metadata(connector_metadata, extractor_metadata)
        chunk_modified_at = merged.modified_at or source_modified_at
        if extracted.pages:
            page_chunks = _chunk_pages(extracted.pages)
            chunks = tuple(
                Chunk(
                    text=chunk_text,
                    content_hash=hashlib.sha256(chunk_text.encode("utf-8")).hexdigest(),
                    source_name=raw.source_name,
                    source_uri=source_uri,
                    source_modified_at=chunk_modified_at,
                    source_page=page_number,
                    sensitivity=sensitivity,
                    author=merged.author,
                    author_email=merged.author_email,
                    tags=merged.tags,
                    metadata=merged.properties,
                )
                for chunk_text, page_number in page_chunks
            )
        else:
            chunk_texts = _chunk_markdown(extracted.markdown)
            chunks = tuple(
                Chunk(
                    text=chunk_text,
                    content_hash=hashlib.sha256(chunk_text.encode("utf-8")).hexdigest(),
                    source_name=raw.source_name,
                    source_uri=source_uri,
                    source_modified_at=chunk_modified_at,
                    source_page=None,
                    sensitivity=sensitivity,
                    author=merged.author,
                    author_email=merged.author_email,
                    tags=merged.tags,
                    metadata=merged.properties,
                )
                for chunk_text in chunk_texts
            )
        signals = _extract_entity_signals(
            extracted.markdown,
            source_uri=source_uri,
            source_modified_at=chunk_modified_at,
            sensitivity=sensitivity,
        )
        if merged.author:
            signals = (
                EntitySignal(
                    kind="person",
                    value=merged.author,
                    source_uri=source_uri,
                    modified_at=chunk_modified_at,
                    confidence=0.95,
                    sensitivity=sensitivity,
                ),
                *signals,
            )
        # GH #336 (ADR-024 Bundle B) — write the per-document
        # documents_media row when a writer is wired in. The hash key
        # is the raw-bytes content hash from BronzeRef (Phase 2 of
        # streaming-bronze populates this on every write); a chunk-level
        # text hash would multiply rows per document. When BronzeRef
        # has no content_hash (legacy rows pre-Phase 2), skip the write
        # rather than synthesise a misleading key.
        self._maybe_write_documents_media(
            raw=raw,
            extracted=extracted,
            merged=merged,
            extractor_name=extractor_name,
            extractor_version=extractor_version,
            extraction_status=extraction_status,
            chunker_version=_first_chunker_version(chunks),
        )
        return SilverOutput(chunks=chunks, entity_signals=signals)

    def _maybe_write_documents_media(
        self,
        *,
        raw: BronzeRef,
        extracted: ExtractedDocument,
        merged: SourceMetadata,
        extractor_name: str | None,
        extractor_version: str | None,
        extraction_status: str,
        chunker_version: str | None,
    ) -> None:
        """Write the per-document documents_media row when a writer is wired in.

        Silent no-op when the writer is None (legacy / fake-paths-only
        tests). Silent no-op when ``raw.content_hash`` is None (legacy
        bronze rows pre Phase 2 of streaming-bronze; the writer cannot
        synthesise a meaningful key without bytes). The orchestrator
        relies on Phase 2's at-write population so the silent-skip
        window is bounded to legacy rows that pre-date the migration.
        """
        if self._documents_media_writer is None:
            return
        if raw.content_hash is None:
            return
        page_count = len(extracted.pages) if extracted.pages else None
        size_bytes = len(extracted.markdown.encode("utf-8")) if extracted.markdown else None
        # Title preference: merged metadata (connector envelope wins
        # per ADR-021) -> extractor DocMetadata -> None.
        title = extracted.metadata.title if extracted.metadata else None
        # Author / created_date: merged metadata first; fall back to
        # the DocMetadata body extraction.
        author = merged.author or (extracted.metadata.author if extracted.metadata else None)
        created_date = merged.created_at or (extracted.metadata.created_date if extracted.metadata else None)
        language = extracted.metadata.language if extracted.metadata else None
        self._documents_media_writer.write(
            content_hash=raw.content_hash,
            path=str(raw.item_id),
            mime=str(raw.mime),
            size_bytes=size_bytes,
            page_count=page_count,
            title=title,
            author=author,
            created_date=created_date,
            language=language,
            extraction_status=extraction_status,
            extractor_name=extractor_name,
            extractor_version=extractor_version,
            chunker_version=chunker_version,
        )
        # GH #338 — per-page rows for retrieval citation paths. Silent
        # no-op when no writer wired or extractor produced no pages
        # (non-paged formats like markdown).
        if self._document_pages_writer is not None and extracted.pages:
            self._document_pages_writer.write_pages(
                content_hash=raw.content_hash,
                pages=extracted.pages,
            )

    def write_extraction_outcome(
        self,
        *,
        raw: BronzeRef,
        _source_modified_at: str,
        extractor_name: str | None,
        extractor_version: str | None,
        extraction_status: str,
    ) -> None:
        """Write a documents_media row WITHOUT running silver chunking.

        GH #336 — the orchestrator calls this on the ``failed`` /
        ``unsupported`` paths where extraction either raised or every
        chain member declined ``can_extract``. The row captures the
        outcome so dashboards + re-extract triage observe the failed
        documents, not just the successful ones. Silent no-op when no
        writer is wired in OR when the BronzeRef has no content_hash
        (same constraint as the happy path).

        ``_source_modified_at`` is accepted for caller-side symmetry
        with :meth:`process` but is not stored on the row — the
        documents_media schema records ``extraction_timestamp`` (the
        wall-clock at write time), not the source's modify time.
        """
        if self._documents_media_writer is None:
            return
        if raw.content_hash is None:
            return
        self._documents_media_writer.write(
            content_hash=raw.content_hash,
            path=str(raw.item_id),
            mime=str(raw.mime),
            size_bytes=None,
            page_count=None,
            title=None,
            author=None,
            created_date=None,
            language=None,
            extraction_status=extraction_status,
            extractor_name=extractor_name,
            extractor_version=extractor_version,
            chunker_version=None,
        )


def _first_chunker_version(chunks: tuple[Chunk, ...]) -> str | None:
    """Return the first non-None ``chunker_version`` across ``chunks``.

    Today Silver constructs Chunks without ``chunker_version=`` (the
    Wave C / F55 thread-through is partial — see CLAUDE.md F55 note).
    The helper returns None for now; once the chunker registry is
    plumbed in, every emitter sets ``chunker_version=self.version``
    and this helper will surface that value to documents_media.
    """
    for chunk in chunks:
        if chunk.chunker_version is not None:
            return chunk.chunker_version
    return None


def _merge_metadata(
    connector_metadata: SourceMetadata | None,
    extractor_metadata: SourceMetadata | None,
) -> SourceMetadata:
    """Merge connector + extractor metadata with connector-wins precedence.

    ADR-021 §"Silver merge logic": connector envelope > extractor body >
    defaults. Tags are unioned (deduplicated, sorted for determinism).
    Properties merge with connector entries overwriting extractor ones
    on key collision. ``None`` inputs collapse to the empty
    :class:`SourceMetadata`; the helper always returns a populated
    instance so the caller can deconstruct without ``is None`` checks.
    """
    connector = connector_metadata or SourceMetadata()
    extractor = extractor_metadata or SourceMetadata()
    merged_tags = tuple(sorted({*connector.tags, *extractor.tags}))
    merged_props: dict[str, str] = {**extractor.properties, **connector.properties}
    return SourceMetadata(
        modified_at=connector.modified_at or extractor.modified_at,
        created_at=connector.created_at or extractor.created_at,
        author=connector.author or extractor.author,
        author_email=connector.author_email or extractor.author_email,
        tags=merged_tags,
        properties=merged_props,
    )

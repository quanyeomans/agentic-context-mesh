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
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable

from kairix.core.protocols import (
    BronzeRef,
    Chunk,
    EntitySignal,
    ExtractedDocument,
    Page,
    Sensitivity,
    SilverOutput,
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

    Bug B fix (v2026.5.26a1 dogfood): paragraphs longer than
    ``_TARGET_CHUNK_CHARS`` are pre-split at sentence boundaries (then
    word, then char) BEFORE the greedy-glue loop runs. Previously a
    32,595-char paragraph from a SharePoint backfill landed as one
    chunk regardless of the 1000-char target — sabotage-proof in
    ``tests/unit/test_silver.py``.
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
    """

    def process(
        self,
        raw: BronzeRef,
        extracted: ExtractedDocument,
        source_uri: str,
        source_modified_at: str,
        sensitivity: Sensitivity,
    ) -> SilverOutput:
        """Split ``extracted.markdown`` into chunks; emit entity signals.

        Every chunk carries ``source_uri`` + ``source_modified_at`` +
        ``sensitivity`` per F39.

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
        if extracted.pages:
            page_chunks = _chunk_pages(extracted.pages)
            chunks = tuple(
                Chunk(
                    text=chunk_text,
                    content_hash=hashlib.sha256(chunk_text.encode("utf-8")).hexdigest(),
                    source_name=raw.source_name,
                    source_uri=source_uri,
                    source_modified_at=source_modified_at,
                    source_page=page_number,
                    sensitivity=sensitivity,
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
                    source_modified_at=source_modified_at,
                    source_page=None,
                    sensitivity=sensitivity,
                )
                for chunk_text in chunk_texts
            )
        signals = _extract_entity_signals(
            extracted.markdown,
            source_uri=source_uri,
            source_modified_at=source_modified_at,
            sensitivity=sensitivity,
        )
        return SilverOutput(chunks=chunks, entity_signals=signals)

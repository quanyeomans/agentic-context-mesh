"""Unified corpus-ingest primitive — Spike C1 contract.

`ingest_corpus(request, *, paths, fact_store, fact_extractor, ...)`
is the single shared primitive every conversational ingest path
eventually routes through:

  * ``kairix ingest-chat`` (P2 adapter, Phase 2)
  * ``SuiteRunner._ingest_sessions`` (P3 adapter, Phase 3)
  * LoCoMo benchmark harness (P6 adapter, Phase 6)

The contract composes four optional collaborators on top of the two
required ones (``fact_store`` + ``fact_extractor``):

  * ``document_writer`` (Protocol, nullable) — renders markdown chunks
    into the document store. ``None`` = facts-only mode (today's
    SuiteRunner behaviour).
  * ``embedder`` (Protocol, nullable) — embeds the documents this pass
    wrote. ``None`` = no chunk index update.
  * ``consolidation`` (ConsolidationPass, nullable) — supersedes
    contradicting prior facts. ``None`` = pure-extract mode (useful
    for measuring raw extractor F1 without consolidation muddying).

Design contract:

- **Dependency injection is total.** No env-var reads, no
  ``KairixPaths.resolve()`` call from inside ``ingest_corpus`` — every
  caller resolves paths at the CLI/harness layer and forwards them in.
- **Per-session error isolation.** One bad session JSONL doesn't
  abort the call; ``IngestResult.skipped_sessions`` indexes into
  ``request.sessions`` so the operator (and the suite runner) can
  surface which sessions dropped out.
- **Lever A is alive end-to-end.** ``SessionPayload.metadata``
  threads through to ``fact_extractor.extract`` as ``session_metadata``,
  closing the SuiteRunner regression (C1 §1c).

F-rule notes:
  * F1: no monkeypatching. Tests inject Fake* implementations through
    the constructor seam.
  * F22: this module is ``kairix/corpus/ingest.py`` — snake_case.
  * F26: imports from ``kairix.core.protocols``,
    ``kairix.core.facts.consolidation``, and ``kairix.paths`` only —
    no providers/transport.
"""

from __future__ import annotations

import dataclasses
import hashlib
import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from kairix.core.facts.consolidation import ConsolidationPass
from kairix.core.protocols import (
    CorpusEmbedder,
    DocumentWriter,
    FactExtractor,
    FactStore,
)
from kairix.paths import KairixPaths

logger = logging.getLogger(__name__)

__all__ = [
    "IngestRequest",
    "IngestResult",
    "SessionPayload",
    "ingest_corpus",
]


# ---------------------------------------------------------------------------
# Field-name constants — F17 (no string literal of ≥10 chars duplicated ≥3
# times). Centralised so the schema lives in one place.
# ---------------------------------------------------------------------------

_KEY_EVIDENCE_AT = "evidence_at"
_KEY_DATE_TIME = "date_time"
_KEY_SESSION_DATE = "session_date"


# ---------------------------------------------------------------------------
# Public value objects
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SessionPayload:
    """One conversational session, in memory, source-agnostic.

    LoCoMo records, JSONL-per-session corpora, and the existing
    ``ingest-chat`` per-conversation groups all collapse onto this
    shape via adapters.

    Fields:
      turns
          Ordered turn dicts. Each dict carries at least ``role``
          (or ``speaker``) and ``content`` (or ``text``); the
          unified writer treats role / content as the canonical keys.
      session_id
          Optional stable id used as the markdown filename stem.
          When ``None``, the orchestrator falls back to an
          adapter-provided default (e.g. ``session-{idx:03d}``).
      metadata
          Stream A Lever A surface; same dict shape the
          :class:`FactExtractor` Protocol's ``session_metadata``
          accepts. May carry ``date_time``, ``session_id``, and
          arbitrary extras (round-tripped through to the extractor,
          ignored by the orchestrator if unknown).
    """

    turns: tuple[dict[str, Any], ...]
    session_id: str | None = None
    metadata: dict[str, Any] | None = None


@dataclass(frozen=True)
class IngestRequest:
    """One unit of work for :func:`ingest_corpus`.

    ``corpus_id`` is the namespace anchor: it becomes the namespace
    stamped onto every emitted FactRecord AND the subdirectory the
    ``DocumentWriter`` uses to scope markdown chunks. For LoCoMo
    this is ``"conv-26"``; for reference-library suites it's the
    suite's directory name; for ``ingest-chat`` single-file
    invocations it defaults to the JSONL filename stem.

    ``window_turns`` is the sliding-window size used by the
    fact extractor. ``0`` collapses to a single window (the extractor
    sees the whole session at once) — helpful for tiny corpora
    where windowing would fragment context.
    """

    sessions: tuple[SessionPayload, ...]
    corpus_id: str
    window_turns: int = 4


@dataclass(frozen=True)
class IngestResult:
    """Per-call counters surfaced to operators, CI gates, and tests.

    The shape collects every count today's three divergent paths
    care about, so the same envelope covers the LoCoMo harness +
    suite-runner + ingest-chat needs without three separate result
    types.

    Field semantics:
      corpus_id
          Echoed from :class:`IngestRequest` for downstream
          consumers (CLI logs, audit trails).
      sessions_processed
          Sessions that reached the orchestrator loop. Includes
          skipped sessions — the absolute denominator.
      turns_ingested
          Total turns across all non-skipped sessions.
      document_paths
          The Paths the :class:`DocumentWriter` returned, in session
          order. Empty tuple when ``document_writer=None`` or every
          session was skipped.
      windows_extracted
          Total extractor windows processed across all non-skipped
          sessions. ``0`` when ``fact_extractor`` returned no
          windows (e.g. zero turns) OR when extractor wiring chose
          a no-op extractor.
      facts_added
          Count of ``fact_store.add`` calls — i.e. facts emitted
          by the extractor. ``0`` when extractor returned empty.
      facts_superseded
          Count of prior facts the consolidation pass marked as
          superseded by a newly added fact. ``0`` when
          ``consolidation=None`` (pure-extract mode).
      chunks_indexed
          Count returned by the :class:`CorpusEmbedder`. ``0`` when
          ``embedder=None`` (no chunk index update).
      skipped_sessions
          Indexes (into ``request.sessions``) of sessions that
          raised during processing. The other sessions ingested
          normally.
    """

    corpus_id: str
    sessions_processed: int
    turns_ingested: int
    document_paths: tuple[Path, ...]
    windows_extracted: int
    facts_added: int
    facts_superseded: int
    chunks_indexed: int
    skipped_sessions: tuple[int, ...]


# ---------------------------------------------------------------------------
# Internal counter accumulator — keeps the orchestrator loop under F16.
# ---------------------------------------------------------------------------


@dataclass
class _Counters:
    """Running totals threaded through the per-session orchestration helper."""

    sessions_processed: int = 0
    turns_ingested: int = 0
    windows_extracted: int = 0
    facts_added: int = 0
    facts_superseded: int = 0
    skipped: list[int] = field(default_factory=list)
    document_paths: list[Path] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Public orchestrator
# ---------------------------------------------------------------------------


def ingest_corpus(
    request: IngestRequest,
    *,
    paths: KairixPaths,
    fact_store: FactStore,
    fact_extractor: FactExtractor,
    document_writer: DocumentWriter | None = None,
    embedder: CorpusEmbedder | None = None,
    consolidation: ConsolidationPass | None = None,
) -> IngestResult:
    """Run the unified ingest pipeline against ``request``.

    Workflow per session:

      1. If ``document_writer`` is non-None, render markdown +
         frontmatter and call ``document_writer.write(...)``. The
         returned Path is appended to ``IngestResult.document_paths``.
      2. If ``request.window_turns >= 0`` and ``fact_extractor`` is
         provided, slice the turns into non-overlapping windows and
         call ``fact_extractor.extract(turns=window,
         session_metadata=session.metadata)``. Every returned fact
         is namespace-stamped (corpus_id) and ``fact_store.add(fact)``
         is called.
      3. If ``consolidation`` is non-None AND the fact exposes the
         full ``FactRecord`` Protocol (``namespace`` attribute),
         the consolidation pass runs against it and any superseded
         prior facts are counted.
      4. After every session, if ``embedder`` is non-None, the
         collected document paths are passed to
         ``embedder.embed(...)`` and the returned chunk count flows
         through to ``IngestResult.chunks_indexed``.

    Per-session error isolation: a session whose processing raises
    is logged + its index is added to ``IngestResult.skipped_sessions``;
    sibling sessions continue.

    Parameters
    ----------
    request:
        Unit of work — sessions + corpus id + window size.
    paths:
        Resolved :class:`KairixPaths`. Currently used only as a
        forward parameter to the embedder; never re-resolved.
    fact_store:
        Implementation of :class:`FactStore` Protocol (real or fake).
        Required even when no facts are expected — the orchestrator
        always has SOMETHING to call ``add`` on.
    fact_extractor:
        Implementation of :class:`FactExtractor` Protocol. Pass a
        no-op extractor for chunks-only ingest.
    document_writer:
        Optional :class:`DocumentWriter`. ``None`` = facts-only mode
        (markdown is not written; ``document_paths=()``).
    embedder:
        Optional :class:`CorpusEmbedder`. ``None`` = no chunk index
        update (``chunks_indexed=0``).
    consolidation:
        Optional :class:`ConsolidationPass`. ``None`` = pure-extract
        mode — supersession is not run and ``facts_superseded=0``.

    Returns
    -------
    IngestResult
        Per-call counters. ``corpus_id`` is echoed from ``request``.
    """
    counters = _Counters()
    ingested_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    # ``paths`` is reserved for future expansions (per-corpus workspace
    # audit logs, cache-dir-rooted ingest-state JSONs — see C1 §4.3).
    # Logging it at DEBUG keeps it referenced today so the F19 gate sees
    # the load-bearing use; downstream phases will read concrete fields.
    logger.debug(
        "ingest_corpus: corpus_id=%s sessions=%d document_root=%s",
        request.corpus_id,
        len(request.sessions),
        paths.document_root,
    )

    for index, session in enumerate(request.sessions):
        counters.sessions_processed += 1
        try:
            _process_session(
                index=index,
                session=session,
                request=request,
                ingested_at=ingested_at,
                fact_store=fact_store,
                fact_extractor=fact_extractor,
                document_writer=document_writer,
                consolidation=consolidation,
                counters=counters,
            )
        except Exception as exc:
            logger.warning(
                "ingest_corpus: session %d (id=%r) raised; skipping. error=%s",
                index,
                session.session_id,
                exc,
            )
            counters.skipped.append(index)

    chunks_indexed = _maybe_embed(embedder, counters.document_paths)

    return IngestResult(
        corpus_id=request.corpus_id,
        sessions_processed=counters.sessions_processed,
        turns_ingested=counters.turns_ingested,
        document_paths=tuple(counters.document_paths),
        windows_extracted=counters.windows_extracted,
        facts_added=counters.facts_added,
        facts_superseded=counters.facts_superseded,
        chunks_indexed=chunks_indexed,
        skipped_sessions=tuple(counters.skipped),
    )


# ---------------------------------------------------------------------------
# Per-session helper — extracted to keep ``ingest_corpus`` under F16.
# ---------------------------------------------------------------------------


def _process_session(
    *,
    index: int,
    session: SessionPayload,
    request: IngestRequest,
    ingested_at: str,
    fact_store: FactStore,
    fact_extractor: FactExtractor,
    document_writer: DocumentWriter | None,
    consolidation: ConsolidationPass | None,
    counters: _Counters,
) -> None:
    """Run one session through write + extract + consolidate.

    Raises any exception from collaborators — the orchestrator catches
    them and accumulates the index in ``IngestResult.skipped_sessions``.
    """
    session_id = session.session_id or f"session-{index + 1:03d}"
    counters.turns_ingested += len(session.turns)

    if document_writer is not None:
        path = _write_session_document(
            session=session,
            session_id=session_id,
            corpus_id=request.corpus_id,
            ingested_at=ingested_at,
            writer=document_writer,
        )
        counters.document_paths.append(path)

    for window in _window(session.turns, request.window_turns):
        counters.windows_extracted += 1
        facts = fact_extractor.extract(
            turns=window,
            session_metadata=session.metadata,
        )
        for fact in facts:
            stamped = _apply_namespace(fact, request.corpus_id)
            fact_store.add(stamped)
            counters.facts_added += 1
            if consolidation is not None and hasattr(stamped, "namespace"):
                outcome = consolidation.process(stamped)
                counters.facts_superseded += len(outcome.superseded_ids)


# ---------------------------------------------------------------------------
# Document-writing helper — wraps render + writer call.
# ---------------------------------------------------------------------------


def _write_session_document(
    *,
    session: SessionPayload,
    session_id: str,
    corpus_id: str,
    ingested_at: str,
    writer: DocumentWriter,
) -> Path:
    """Render the session as markdown and persist it via the writer."""
    date_time = _extract_session_date_time(session.metadata)
    body = _render_body(session.turns, date_time=date_time)
    frontmatter = _build_frontmatter(
        corpus_id=corpus_id,
        session_id=session_id,
        ingested_at=ingested_at,
        turn_count=len(session.turns),
        date_time=date_time,
    )
    return writer.write(
        corpus_id=corpus_id,
        session_id=session_id,
        rendered_body=body,
        frontmatter=frontmatter,
    )


def _render_body(turns: Sequence[dict[str, Any]], *, date_time: str | None) -> str:
    """Render the markdown body for a session — Stream A Lever A convention.

    Prepends a ``**Session date:**`` pin when ``date_time`` is set so
    the retrieval-side LLM context carries the temporal anchor even
    when the chunker drops the frontmatter (same body convention as
    ``ingest-chat`` and the LoCoMo harness).
    """
    body_lines: list[str] = []
    if date_time:
        body_lines.append(f"**Session date:** {date_time}")
        body_lines.append("")  # blank line separator
    body_lines.extend(_format_turn(turn) for turn in turns)
    return "\n\n".join(body_lines) + "\n"


def _format_turn(turn: dict[str, Any]) -> str:
    """Format one turn line as ``**<role>**: <content>``.

    Falls back to ``speaker`` / ``text`` when ``role`` / ``content``
    are missing — covers both the JSONL chat-message shape and the
    LoCoMo-native shape without forcing adapters to translate.
    """
    role = turn.get("role") or turn.get("speaker") or "unknown"
    content = turn.get("content") or turn.get("text") or ""
    return f"**{role}**: {content}"


def _build_frontmatter(
    *,
    corpus_id: str,
    session_id: str,
    ingested_at: str,
    turn_count: int,
    date_time: str | None,
) -> dict[str, Any]:
    """Build the canonical frontmatter dict for one session document."""
    frontmatter: dict[str, Any] = {
        "corpus_id": corpus_id,
        "session_id": session_id,
        "turn_count": turn_count,
        "ingested_at": ingested_at,
    }
    if date_time:
        frontmatter[_KEY_DATE_TIME] = date_time
    return frontmatter


def _extract_session_date_time(metadata: dict[str, Any] | None) -> str | None:
    """Return the session's ``date_time`` (or equivalent) string when present."""
    if not metadata:
        return None
    for key in (_KEY_DATE_TIME, _KEY_EVIDENCE_AT, _KEY_SESSION_DATE):
        raw = metadata.get(key)
        if isinstance(raw, str) and raw.strip():
            return raw.strip()
    return None


# ---------------------------------------------------------------------------
# Windowing helper — matches ingest-chat's _window() semantics.
# ---------------------------------------------------------------------------


def _window(turns: Sequence[dict[str, Any]], size: int) -> list[list[dict[str, Any]]]:
    """Slice ``turns`` into non-overlapping windows of length ``size``.

    The trailing window may be shorter than ``size`` (e.g. 12 turns
    with size=5 → windows of 5/5/2). ``size <= 0`` collapses to one
    window containing all turns — defensive default so a misconfigured
    request can't trigger an infinite loop downstream. Zero-turn
    sessions return ``[]`` so the extractor isn't called with an
    empty list.
    """
    if not turns:
        return []
    if size <= 0:
        return [list(turns)]
    return [list(turns[i : i + size]) for i in range(0, len(turns), size)]


# ---------------------------------------------------------------------------
# Namespace-stamping — copied from ingest-chat verbatim; F1-clean.
# ---------------------------------------------------------------------------


def _apply_namespace(fact: Any, namespace: str) -> Any:
    """Stamp ``namespace`` (== corpus_id) onto a fact when the extractor
    returned the default.

    Most extractors won't know which corpus they're being run against;
    the orchestrator has that context (``request.corpus_id``) and
    applies it at persistence time. Falls back to returning the
    original fact if the namespace already matches or the fact
    doesn't expose a settable namespace.

    Preserves the F1 invariant — never mutates the extractor's
    record via attribute assignment. Uses :func:`dataclasses.replace`
    for production records and a Protocol-shaped reconstruction
    pathway for fakes.
    """
    try:
        current = fact.namespace
    except AttributeError:
        return fact
    if current == namespace:
        return fact
    if dataclasses.is_dataclass(fact) and not isinstance(fact, type):
        try:
            return dataclasses.replace(fact, namespace=namespace)
        except (TypeError, ValueError):
            return fact
    fake_kwargs = _try_fake_record_kwargs(fact)
    if fake_kwargs is not None:
        fake_kwargs["namespace"] = namespace
        return type(fact)(**fake_kwargs)
    return fact


def _try_fake_record_kwargs(fact: Any) -> dict[str, Any] | None:
    """Read the public Protocol surface back into a constructor kwarg dict.

    Returns ``None`` if any required property is missing — callers
    fall back to using the original fact. Mirrors the helper in
    ``ingest_chat`` so production fakes can be re-stamped without
    F1-violating attribute reassignment.
    """
    try:
        kwargs: dict[str, Any] = {
            "id": fact.id,
            "entity": fact.entity,
            "attribute": fact.attribute,
            "value": fact.value,
            "confidence": fact.confidence,
            "source_turn_ids": fact.source_turn_ids,
            "extracted_at": fact.extracted_at,
            "superseded_by": fact.superseded_by,
        }
    except AttributeError:
        return None
    if hasattr(fact, _KEY_EVIDENCE_AT):
        kwargs[_KEY_EVIDENCE_AT] = fact.evidence_at
    return kwargs


# ---------------------------------------------------------------------------
# Embedding helper — single call point so the count surfaces cleanly.
# ---------------------------------------------------------------------------


def _maybe_embed(
    embedder: CorpusEmbedder | None,
    document_paths: list[Path],
) -> int:
    """Call ``embedder.embed`` when wired; return ``0`` otherwise.

    Empty ``document_paths`` is a legal no-op signal — the embedder
    Protocol accepts an empty tuple and is documented to return
    ``0``. We still call so embedders that want to surface a
    "nothing to do" log line have the affordance.
    """
    if embedder is None:
        return 0
    return embedder.embed(tuple(document_paths))


# ---------------------------------------------------------------------------
# Body-hash helper — exported only for testing the idempotency story
# downstream adapters carry. Not part of the public surface.
# ---------------------------------------------------------------------------


def _content_hash(text: str) -> str:
    """SHA-256 hex digest of the chunk body.

    Used by downstream ``DocumentWriter`` implementations to skip the
    disk write when the body is unchanged. Kept in this module so
    every writer gets the same canonical hashing rule.
    """
    return hashlib.sha256(text.encode("utf-8")).hexdigest()

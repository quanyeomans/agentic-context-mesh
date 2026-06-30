"""Timeline use case — date-aware retrieval shared by CLI and MCP.

Closes #163 (CLI/MCP timeline divergence) and lays the Phase-1 template
for #168 (CLI/MCP feature parity). The use case:

  1. Resolves a time window — either explicit ``since``/``until`` from
     the caller, or extracted from the query when both are None.
  2. Rewrites the query temporally (so vector/BM25 search sees expanded
     date phrases like "April 2026").
  3. **Primary backend:** queries the structured temporal-chunks index
     for board-card / memory-section hits in the window.
  4. **Fall-through:** if the temporal-chunks backend returns nothing
     (or no time window was detectable), runs the search pipeline on
     the rewritten query so callers always get *some* signal.

CLI and MCP both call ``run_timeline``; their adapters translate argv /
JSON in and the ``TimelineResult`` dataclass out. Adapters never own
business logic — see ``docs/architecture/cli-mcp-feature-parity.md``.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from kairix.core.protocols import SourceRef
from kairix.core.search.scope import Scope

logger = logging.getLogger(__name__)

# Envelope key shared by every TimelineHit emitted to / consumed from
# the canonical JSON envelope (CLI ``--json`` + MCP ``tool_timeline``,
# #412 + #421). Extracted to a module-level constant per F17 — the
# literal appears on the writer side (``timeline_output_to_envelope``),
# the reader side (``TimelineResult.from_envelope``), and the
# ``_chunk_to_hit`` projector.
_HIT_KEY_CHUNK_TYPE = "chunk_type"

# PLA-274 breadcrumb keys — read on the envelope writer, the envelope
# reader (``from_envelope``), and both hit projectors; F17 extracts the
# ≥10-char keys to one edit site.
_HIT_KEY_SOURCE_URI = "source_uri"
_HIT_KEY_COLLECTION = "collection"
_HIT_KEY_SOURCE_PAGE = "source_page"


# ---------------------------------------------------------------------------
# Production defaults — lazy-import wrappers so the use case stays
# import-light (no temporal.index / core.factory loads on module load) and
# unit tests that inject deps never touch this wiring.
# ---------------------------------------------------------------------------


def _default_extract_window(query: str, reference_date: date | None) -> tuple[date | None, date | None]:
    from kairix.core.temporal.rewriter import extract_time_window

    return extract_time_window(query=query, reference_date=reference_date)


def _default_rewrite_query(query: str, reference_date: date | None) -> str:
    from kairix.core.temporal.rewriter import rewrite_temporal_query

    rewritten = rewrite_temporal_query(query=query, reference_date=reference_date)
    return rewritten if rewritten is not None else query


def _default_query_chunks(
    topic: str,
    start: date | None,
    end: date | None,
    chunk_types: list[str] | None,
    limit: int,
) -> list[Any]:
    from kairix.core.temporal.index import query_temporal_chunks

    return query_temporal_chunks(
        topic=topic,
        start=start,
        end=end,
        chunk_types=chunk_types,
        limit=limit,
    )


def _default_search(
    query: str,
    budget: int,
    agent: str | None,
    scope: Scope,
) -> Any:
    from kairix.core.factory import build_search_pipeline

    pipeline = build_search_pipeline()
    return pipeline.search(query=query, budget=budget, agent=agent, scope=scope)


@dataclass(frozen=True)
class TimelineHit:
    """A single timeline hit — uniform shape across both backends.

    The temporal-chunks backend populates ``date`` and ``chunk_type``;
    the search-pipeline fallback leaves them empty. Both populate
    ``path``, ``title``, ``snippet``, ``score``.

    PLA-274 — ``collection`` + ``source_page`` were dropped pre-fix (the
    timeline envelope omitted them entirely, so an agent couldn't tell
    which collection a hit came from or cite a specific page). They are
    now carried, alongside the canonical ``source_uri`` breadcrumb +
    within-document ``locator``, via :meth:`source_ref`.
    """

    path: str
    title: str
    snippet: str
    score: float
    date: str = ""
    chunk_type: str = ""
    collection: str = ""
    source_page: int | None = None
    source_uri: str = ""
    locator: str | None = None

    def source_ref(self) -> SourceRef:
        """Return the shared :class:`SourceRef` breadcrumb for this hit (F97)."""
        return SourceRef.of(
            path=self.path,
            source_uri=self.source_uri,
            title=self.title or None,
            collection=self.collection or None,
            source_page=self.source_page,
            locator=self.locator,
        )


@dataclass(frozen=True)
class TimelineResult:
    """Outcome of one ``run_timeline`` invocation.

    Attributes:
        original_query: The caller's query, unchanged.
        rewritten_query: Query after temporal rewriting (== original
            when no temporal expression was found).
        is_temporal: True when a time window was extracted (or supplied).
        fell_back: True when the search-pipeline fallback produced
            ``results`` (because temporal-chunks returned empty).
        time_window: ``{"start": "YYYY-MM-DD", "end": "YYYY-MM-DD"}``;
            empty strings when a bound is open. ``{}`` when no window
            was detectable.
        results: Up to ``limit`` ``TimelineHit``s, best-first.
        error: Empty string on success; structured ``"<Class>: <msg>"``
            on failure (mirrors the wrap_tool_errors envelope).
    """

    original_query: str
    rewritten_query: str
    is_temporal: bool
    fell_back: bool
    time_window: dict[str, str]
    results: list[TimelineHit] = field(default_factory=list)
    error: str = ""

    @classmethod
    def from_envelope(cls, envelope: dict[str, Any]) -> TimelineResult:
        """Rebuild a ``TimelineResult`` from the dict ``timeline_output_to_envelope`` emits.

        The seam for warm-MCP text-mode routing (#421 PR 2.7). The CLI
        dispatcher receives a JSON envelope from the MCP worker; this
        adapter projects it back to the dataclass shape that
        ``format_header`` / ``format_results`` already consume, so the
        in-process and warm paths render byte-identical text.

        Field defaults mirror the dataclass defaults so an envelope
        missing optional keys (e.g. produced by an older worker) still
        rebuilds into a renderable result.
        """
        raw_results = envelope.get("results", []) or []
        rebuilt_hits: list[TimelineHit] = []
        for hit in raw_results:
            if not isinstance(hit, dict):
                continue
            raw_page = hit.get(_HIT_KEY_SOURCE_PAGE)
            raw_locator = hit.get("locator")
            rebuilt_hits.append(
                TimelineHit(
                    path=str(hit.get("path", "")),
                    title=str(hit.get("title", "")),
                    snippet=str(hit.get("snippet", "")),
                    score=float(hit.get("score", 0.0)),
                    date=str(hit.get("date", "")),
                    chunk_type=str(hit.get(_HIT_KEY_CHUNK_TYPE, "")),
                    collection=str(hit.get(_HIT_KEY_COLLECTION, "") or ""),
                    source_page=int(raw_page) if isinstance(raw_page, int) else None,
                    source_uri=str(hit.get(_HIT_KEY_SOURCE_URI, "") or ""),
                    locator=str(raw_locator) if raw_locator else None,
                )
            )
        raw_window = envelope.get("time_window", {}) or {}
        # The envelope round-trip is dict[str,str] keyed; coerce defensively
        # so a None or non-str value doesn't poison the renderer.
        time_window: dict[str, str] = (
            {str(k): str(v) for k, v in raw_window.items()} if isinstance(raw_window, dict) else {}
        )
        return cls(
            original_query=str(envelope.get("original_query", "")),
            rewritten_query=str(envelope.get("rewritten_query", "")),
            is_temporal=bool(envelope.get("is_temporal", False)),
            fell_back=bool(envelope.get("fell_back", False)),
            time_window=time_window,
            results=rebuilt_hits,
            error=str(envelope.get("error", "")),
        )


def timeline_output_to_envelope(result: TimelineResult) -> dict[str, Any]:
    """Project ``TimelineResult`` onto the canonical JSON envelope.

    Single source of truth for CLI ``--json`` and MCP ``tool_timeline``
    output shape (#412 — pre-fix the CLI dropped ``results`` and emitted
    only ``results_count``; the MCP envelope inlined its own dict literal).
    Both surfaces now call this helper so the envelope can't drift.
    """
    return {
        "original_query": result.original_query,
        "rewritten_query": result.rewritten_query,
        "is_temporal": result.is_temporal,
        "fell_back": result.fell_back,
        "time_window": result.time_window,
        "results": [
            {
                "path": h.path,
                "title": h.title,
                "snippet": h.snippet,
                "score": h.score,
                "date": h.date,
                _HIT_KEY_CHUNK_TYPE: h.chunk_type,
                # PLA-274 — collection + per-page citation were dropped pre-fix;
                # carried now alongside the canonical source_uri breadcrumb +
                # within-document locator (keys mirror SourceRef).
                _HIT_KEY_COLLECTION: h.collection,
                _HIT_KEY_SOURCE_PAGE: h.source_page,
                _HIT_KEY_SOURCE_URI: h.source_ref().source_uri,
                "locator": h.source_ref().locator,
            }
            for h in result.results
        ],
        "error": result.error,
    }


@dataclass(frozen=True)
class TimelineDeps:
    """Injectable dependencies for ``run_timeline``.

    Production callers leave every field unset and the dataclass'
    ``default_factory`` wires the real implementations. Tests construct
    a ``TimelineDeps(...)`` with light-weight stand-ins to drive the
    orchestration end-to-end without touching the real document store,
    search pipeline, or query rewriter.

    All callable fields use ``field(default_factory=lambda: _default_X)``
    rather than ``Callable[...] | None = None`` (per CLAUDE.md F6
    guidance: avoid the ``Optional[Callable] + post-init`` pattern) so
    mypy sees the production callable directly and ``run_timeline``
    invokes ``d.x_fn(...)`` without a None-fallback ladder.
    """

    extract_window_fn: Callable[[str, date | None], tuple[date | None, date | None]] = field(
        default_factory=lambda: _default_extract_window
    )
    rewrite_query_fn: Callable[[str, date | None], str] = field(default_factory=lambda: _default_rewrite_query)
    query_chunks_fn: Callable[..., list[Any]] = field(default_factory=lambda: _default_query_chunks)
    search_fn: Callable[..., Any] = field(default_factory=lambda: _default_search)


def _format_window(start: date | None, end: date | None) -> dict[str, str]:
    if start is None and end is None:
        return {}
    return {
        "start": start.isoformat() if start else "",
        "end": end.isoformat() if end else "",
    }


def _chunk_to_hit(chunk: Any) -> TimelineHit:
    """Project a TemporalChunk into the uniform TimelineHit shape."""
    text = getattr(chunk, "text", "") or ""
    chunk_date = getattr(chunk, "date", None)
    metadata = getattr(chunk, "metadata", {}) or {}
    title = metadata.get("section_heading") or metadata.get("card_id") or metadata.get("title") or ""
    # PLA-274 — surface collection + per-page citation + canonical breadcrumb
    # from the temporal-chunk metadata when available; SourceRef.of falls
    # source_uri back to the source_path so the pointer stays resolvable.
    raw_page = metadata.get(_HIT_KEY_SOURCE_PAGE)
    return TimelineHit(
        path=str(getattr(chunk, "source_path", "")),
        title=str(title),
        snippet=text[:300],
        score=float(metadata.get("score", 0.0)),
        date=chunk_date.isoformat() if chunk_date else "",
        chunk_type=str(getattr(chunk, _HIT_KEY_CHUNK_TYPE, "")),
        collection=str(metadata.get(_HIT_KEY_COLLECTION, "") or ""),
        source_page=int(raw_page) if isinstance(raw_page, int) else None,
        source_uri=str(metadata.get(_HIT_KEY_SOURCE_URI, "") or ""),
    )


def _search_to_hits(search_result: Any, limit: int) -> list[TimelineHit]:
    """Project a SearchResult's BudgetedResult list into TimelineHits."""
    out: list[TimelineHit] = []
    for budgeted in getattr(search_result, "results", [])[:limit]:
        inner = getattr(budgeted, "result", None)
        if inner is None:
            continue
        snippet = getattr(budgeted, "content", "") or getattr(inner, "snippet", "")
        raw_page = getattr(inner, _HIT_KEY_SOURCE_PAGE, None)
        out.append(
            TimelineHit(
                path=str(getattr(inner, "path", "")),
                title=str(getattr(inner, "title", "")),
                snippet=snippet[:300],
                score=float(getattr(inner, "boosted_score", getattr(inner, "score", 0.0))),
                # PLA-274 — carry collection + per-page citation + breadcrumb
                # off the fused result so the timeline fallback path is at
                # parity with the search surface.
                collection=str(getattr(inner, _HIT_KEY_COLLECTION, "") or ""),
                source_page=int(raw_page) if isinstance(raw_page, int) else None,
                source_uri=str(getattr(inner, _HIT_KEY_SOURCE_URI, "") or ""),
            )
        )
    return out


def run_timeline(
    query: str,
    *,
    anchor_date: date | None = None,
    agent: str | None = None,
    scope: Scope = Scope.SHARED_AGENT,
    since: date | None = None,
    until: date | None = None,
    chunk_types: list[str] | None = None,
    limit: int = 10,
    deps: TimelineDeps | None = None,
) -> TimelineResult:
    """Run the timeline use case and return a structured result.

    Never raises — failures populate ``TimelineResult.error`` and return
    an otherwise-empty result. Callers (CLI/MCP) surface the error verbatim.

    Args:
        query: User's natural-language query (may contain temporal
            expressions like "last week", "April 2026").
        anchor_date: Reference date for relative expressions. None →
            today, evaluated by the rewriter.
        agent: Agent name for collection scoping (search fallback only).
        scope: Multi-agent scope (search fallback only).
        since: Explicit lower bound; overrides query-extracted start.
        until: Explicit upper bound; overrides query-extracted end.
        chunk_types: Filter for the temporal-chunks backend
            (e.g. ``["board_card"]``); None → both types.
        limit: Maximum number of hits.
        deps: Injectable dependencies; production callers leave None.
    """
    d = deps or TimelineDeps()
    try:
        return _run_timeline_inner(
            query,
            anchor_date,
            agent,
            scope,
            since,
            until,
            chunk_types,
            limit,
            d,
        )
    except Exception as exc:
        logger.warning("run_timeline failed: %s", exc, exc_info=True)
        return TimelineResult(
            original_query=query,
            rewritten_query=query,
            is_temporal=False,
            fell_back=True,
            time_window={},
            results=[],
            error=f"{type(exc).__name__}: {exc}",
        )


def _resolve_window(
    extract: Callable[..., tuple[date | None, date | None]],
    query: str,
    anchor_date: date | None,
    since: date | None,
    until: date | None,
) -> tuple[date | None, date | None]:
    """Pick the explicit (since,until) when present, else extract from query.

    Returns ``(None, None)`` on any extract failure — including the
    ``extract()`` returning a non-tuple, which would otherwise raise at
    the caller's ``start, end = _resolve_window(...)`` unpacking. The
    unpack happens INSIDE this try so the failure is caught here, not
    propagated. Extracted from ``run_timeline`` to flatten one of its
    four nested try/except branches out of the parent — F16 demanded
    the split.
    """
    if since is not None or until is not None:
        return since, until
    try:
        start, end = extract(query, anchor_date)
        return start, end
    except Exception:
        logger.debug("extract_window failed", exc_info=True)
        return None, None


def _rewrite_temporal_query(
    rewrite: Callable[[str, date | None], str],
    query: str,
    anchor_date: date | None,
) -> str:
    """Rewrite a temporal query, falling back to the original on failure.

    Extracted from ``run_timeline`` for the same F16 reason as
    ``_resolve_window``.
    """
    try:
        return rewrite(query, anchor_date)
    except Exception:
        logger.debug("rewrite_query failed", exc_info=True)
        return query


def _query_temporal_chunks(
    query_chunks: Callable[..., list[Any]],
    rewritten: str,
    start: date | None,
    end: date | None,
    chunk_types: list[str] | None,
    limit: int,
) -> list[TimelineHit]:
    """Run the temporal-chunks backend; return [] on failure (logged).

    Extracted from ``run_timeline`` for the same F16 reason as
    ``_resolve_window``.
    """
    try:
        chunks = query_chunks(rewritten, start, end, chunk_types, limit)
        return [_chunk_to_hit(c) for c in chunks]
    except Exception:
        logger.warning("temporal-chunks query failed", exc_info=True)
        return []


def _search_fallback(
    search: Callable[..., Any],
    rewritten: str,
    agent: str | None,
    scope: Scope,
    limit: int,
) -> list[TimelineHit]:
    """Run the search-pipeline fallback; return [] on failure (logged).

    Extracted from ``run_timeline`` for the same F16 reason as
    ``_resolve_window``.
    """
    try:
        sr = search(rewritten, 3000, agent, scope)
        return _search_to_hits(sr, limit)
    except Exception:
        logger.warning("search fallback failed", exc_info=True)
        return []


def _run_timeline_inner(
    query: str,
    anchor_date: date | None,
    agent: str | None,
    scope: Scope,
    since: date | None,
    until: date | None,
    chunk_types: list[str] | None,
    limit: int,
    d: TimelineDeps,
) -> TimelineResult:
    """The non-failure-handling body of ``run_timeline``.

    Extracted so ``run_timeline`` can be a thin try/except wrapper — F16
    flagged the inlined version at score 33, twice the ceiling. Any
    uncaught exception in this helper is caught by the caller and
    transformed into a populated ``TimelineResult.error``.
    """
    start, end = _resolve_window(d.extract_window_fn, query, anchor_date, since, until)
    time_window = _format_window(start, end)
    is_temporal = bool(time_window)

    rewritten = _rewrite_temporal_query(d.rewrite_query_fn, query, anchor_date) if is_temporal else query

    chunk_hits: list[TimelineHit] = []
    if is_temporal:
        chunk_hits = _query_temporal_chunks(
            d.query_chunks_fn,
            rewritten,
            start,
            end,
            chunk_types,
            limit,
        )

    if chunk_hits:
        return TimelineResult(
            original_query=query,
            rewritten_query=rewritten,
            is_temporal=True,
            fell_back=False,
            time_window=time_window,
            results=chunk_hits,
        )

    search_hits = _search_fallback(d.search_fn, rewritten, agent, scope, limit)
    return TimelineResult(
        original_query=query,
        rewritten_query=rewritten,
        is_temporal=is_temporal,
        fell_back=True,
        time_window=time_window,
        results=search_hits,
    )

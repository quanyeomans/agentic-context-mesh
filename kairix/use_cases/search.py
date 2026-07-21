"""Search use case — hybrid BM25+vector retrieval shared by CLI and MCP.

Phase 2 of the CLI/MCP feature parity initiative (#168). Both surfaces
were calling ``SearchPipeline.search`` directly with their own
adapters around it; the result was drift in:

  - parameters (CLI exposed ``--limit``; MCP did not)
  - output fields (CLI emitted ``bm25_count``/``vec_count``/``vec_failed``;
    MCP omitted them)
  - per-result fields (CLI included ``title``/``tier``; MCP omitted both)
  - entity-graph augmentation (MCP-only; CLI users couldn't see entity cards)

This use case absorbs every divergence into one ``run_search`` callable
returning a ``SearchOutput`` dataclass. Adapters serialise from it and
own no business logic.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from kairix.core.health import (
    HealthDeps,
    KairixHealth,
    cached_probe_health,
    health_to_envelope,
    search_next_action,
)
from kairix.core.protocols import SourceRef
from kairix.core.search.intent import QueryIntent
from kairix.core.search.scope import Scope
from kairix.text import estimate_tokens

logger = logging.getLogger(__name__)


# Envelope key constants — F17: the keys appear in three sites
# (``search_output_to_envelope`` writer, ``SearchOutput.from_envelope``
# / ``SearchHit.from_envelope`` reader, ``_search_output_from_pipeline``
# pipeline-shape ``getattr``) and a string-literal triplet trips the
# duplicate-string rule. The constants make the coupling between
# writer + reader + pipeline-mapper explicit and renaming a single
# edit site.
_KEY_BM25_COUNT = "bm25_count"
_KEY_VEC_COUNT = "vec_count"
_KEY_FUSED_COUNT = "fused_count"
_KEY_VEC_FAILED = "vec_failed"
_KEY_TOTAL_TOKENS = "total_tokens"
_KEY_LATENCY_MS = "latency_ms"
_KEY_COLLECTION = "collection"
_KEY_SOURCE_PAGE = "source_page"
# PLA-274 — breadcrumb envelope keys; read by the writer
# (``search_output_to_envelope``) AND the reader (``SearchHit.from_envelope``).
_KEY_SOURCE_URI = "source_uri"
_KEY_LOCATOR = "locator"
# PLA-270 — chunk-sequence envelope key; same writer↔reader coupling.
_KEY_SEQ = "seq"


def _default_search(
    query: str,
    budget: int,
    scope: Scope,
    agent: str | None,
    collections: list[str] | None = None,
    intent: QueryIntent | None = None,
    max_tier: str = "L2",
) -> Any:
    """Lazy-load the production search pipeline.

    Kept inside the use-case module (rather than a shim) so the file
    owns its own production wiring — eliminates the ``_search_defaults``
    indirection layer that the F7 coverage baseline had to grandfather.

    ``collections``: when set by CLI/MCP callers, the value is passed to
    ``pipeline.search`` so retrieval is restricted to the listed collection
    set. Default ``None`` preserves scope-based resolution.

    ``intent``: when ``run_search`` has already classified the query (to
    size the token budget) it threads the result through so the pipeline
    reuses it instead of classifying the same query a second time
    (PLA-273 warm-path dedup). ``None`` preserves the pipeline's internal
    classification.
    """
    from kairix.core.factory import build_search_pipeline
    from kairix.core.search.config_loader import load_config

    pipeline = build_search_pipeline(config=load_config())
    return pipeline.search(
        query=query,
        budget=budget,
        scope=scope,
        agent=agent,
        collections=collections,
        intent=intent,
        max_tier=max_tier,
    )


def _default_entity_card(name: str) -> dict[str, Any] | None:
    from kairix.agents.mcp.server import _fetch_entity_card

    return _fetch_entity_card(name)


def _default_classify(query: str) -> QueryIntent:
    from kairix.core.search.intent import classify

    return classify(query)


@dataclass(frozen=True)
class SearchHit:
    """A single hit — uniform shape for every result the use case emits.

    The ``source`` and ``entity`` fields are populated only when an
    entity-graph card is prepended at the top of the results (intent
    is ENTITY and a card is found). For ordinary search rows they
    remain empty.

    ``source_page`` is the MM-3 per-page citation. ``None`` for non-paged
    documents (passthrough markdown, vault notes); an integer for PDF /
    PPTX / XLSX chunks so the agent can quote the specific page back
    to the operator.
    """

    path: str
    title: str
    snippet: str
    score: float
    tier: str = ""
    tokens: int = 0
    collection: str = ""
    source: str = ""
    entity: dict[str, str] = field(default_factory=dict)
    source_page: int | None = None
    # PLA-274 — the canonical resolvable breadcrumb (``documents.source_uri``)
    # threaded from the fused result. ``""`` for passthrough vault rows;
    # ``source_ref()`` falls that back to ``path`` so the pointer always
    # resolves. ``locator`` is the within-document anchor for non-paged docs.
    source_uri: str = ""
    locator: str | None = None
    # PLA-270 — the chunk sequence index (0-based) parsed from the chunk
    # path at fusion. ``None`` for non-chunked rows (passthrough vault
    # notes, entity cards). Together with ``source_uri`` this is the clean,
    # typed expansion key the chunk-expansion tool (PLA-268) consumes —
    # an agent no longer string-parses ``#N`` off ``path``.
    seq: int | None = None

    def source_ref(self) -> SourceRef:
        """Return the shared :class:`SourceRef` breadcrumb for this hit (F97).

        The canonical, resolvable pointer an agent cites or re-opens. Built
        through ``SourceRef.of`` so the source_uri→path fallback and the
        non-paged locator derivation apply uniformly across every surface.
        """
        return SourceRef.of(
            path=self.path,
            source_uri=self.source_uri,
            title=self.title or None,
            collection=self.collection or None,
            source_page=self.source_page,
            locator=self.locator,
        )

    @classmethod
    def from_envelope(cls, hit: dict[str, Any]) -> SearchHit:
        """Rebuild a ``SearchHit`` from the per-hit dict ``search_output_to_envelope`` emits.

        Mirrors the projection in ``search_output_to_envelope``: the
        envelope per-hit dict carries every structural field straight
        through (``path`` / ``title`` / ``snippet`` / ``score`` / ``tier``
        / ``tokens`` / ``collection`` / ``source_page``) and includes
        ``source`` + ``entity`` ONLY when ``source`` was non-empty in the
        original (entity-card hits). The inverse defaults the omitted
        keys back to their dataclass-default zero values so flat search
        rows rebuild identically.
        """
        raw_page = hit.get(_KEY_SOURCE_PAGE)
        entity_raw = hit.get("entity") or {}
        # Coerce the entity dict's values to str to match the
        # ``dict[str, str]`` field annotation — the envelope round-trip
        # through JSON preserves types, but downstream consumers should
        # not assume the dict was untouched.
        entity = {str(k): str(v) for k, v in entity_raw.items()}
        raw_locator = hit.get(_KEY_LOCATOR)
        raw_seq = hit.get(_KEY_SEQ)
        return cls(
            path=str(hit.get("path", "")),
            title=str(hit.get("title", "")),
            snippet=str(hit.get("snippet", "")),
            score=float(hit.get("score", 0.0)),
            tier=str(hit.get("tier", "")),
            tokens=int(hit.get("tokens", 0) or 0),
            collection=str(hit.get(_KEY_COLLECTION, "")),
            source=str(hit.get("source", "")),
            entity=entity,
            source_page=int(raw_page) if isinstance(raw_page, int) else None,
            source_uri=str(hit.get(_KEY_SOURCE_URI, "") or ""),
            locator=str(raw_locator) if raw_locator else None,
            seq=int(raw_seq) if isinstance(raw_seq, int) else None,
        )


@dataclass(frozen=True)
class SearchOutput:
    """Outcome of one ``run_search`` invocation.

    Attributes:
        query: The caller's query, unchanged.
        intent: Classifier-assigned ``QueryIntent`` value as a string
            (``"semantic"``, ``"entity"``, ``"keyword"``, …).
        results: Up to ``limit`` ``SearchHit``s, best-first. When the
            intent is ENTITY and the graph has a matching card, that
            card appears first with ``source="entity_graph"``.
        bm25_count / vec_count / fused_count: Per-stage diagnostics
            from the underlying pipeline (zero when the stage didn't
            run).
        vec_failed: True when the vector backend errored mid-pipeline.
        total_tokens: Sum of token estimates across returned hits.
        latency_ms: Wall-clock time of the use case.
        error: Empty on success; structured ``"<Class>: <msg>"`` on a
            top-level failure.
    """

    query: str
    intent: str
    results: list[SearchHit] = field(default_factory=list)
    bm25_count: int = 0
    vec_count: int = 0
    fused_count: int = 0
    vec_failed: bool = False
    total_tokens: int = 0
    latency_ms: float = 0.0
    health: KairixHealth = field(default_factory=KairixHealth)
    error: str = ""

    @classmethod
    def from_envelope(cls, envelope: dict[str, Any]) -> SearchOutput:
        """Rebuild a ``SearchOutput`` from the dict ``search_output_to_envelope`` emits.

        The seam for warm-MCP text-mode routing (#421 PR 2.2). The CLI
        dispatcher receives a JSON envelope from the MCP worker; this
        adapter projects it back to the dataclass shape ``format_text``
        already consumes, so the in-process and warm paths render
        byte-identical text.

        ``health`` is NOT round-tripped from its envelope dict here.
        ``format_text`` reads only ``query`` / ``intent`` / ``results``
        / count diagnostics / ``error``; reconstructing a
        ``KairixHealth`` from its envelope dict would require an
        inverse of ``health_to_envelope`` that doesn't exist today.
        The dataclass default ``KairixHealth()`` is acceptable because
        the CLI's rendering path never reads it. Any future caller
        that needs the round-tripped health snapshot should add an
        explicit ``envelope_to_health`` and wire it here.
        """
        raw_results = envelope.get("results") or []
        results = [SearchHit.from_envelope(h) for h in raw_results if isinstance(h, dict)]
        return cls(
            query=str(envelope.get("query", "")),
            intent=str(envelope.get("intent", "")),
            results=results,
            bm25_count=int(envelope.get(_KEY_BM25_COUNT, 0) or 0),
            vec_count=int(envelope.get(_KEY_VEC_COUNT, 0) or 0),
            fused_count=int(envelope.get(_KEY_FUSED_COUNT, 0) or 0),
            vec_failed=bool(envelope.get(_KEY_VEC_FAILED, False)),
            total_tokens=int(envelope.get(_KEY_TOTAL_TOKENS, 0) or 0),
            latency_ms=float(envelope.get(_KEY_LATENCY_MS, 0.0) or 0.0),
            error=str(envelope.get("error", "")),
        )


@dataclass(frozen=True)
class SearchDeps:
    """Injectable dependencies for ``run_search``.

    Non-Optional fields wired to production defaults via ``default_factory``
    — eliminates the ``Optional[Callable]`` mypy regression class flagged
    in #204. Tests construct ``SearchDeps(search_fn=fake, ...)`` with
    explicit overrides; ``SearchDeps()`` with no kwargs resolves to the
    module-level ``_default_*`` callables.
    """

    search_fn: Callable[..., Any] = field(default_factory=lambda: _default_search)
    entity_card_fn: Callable[[str], dict[str, Any] | None] = field(default_factory=lambda: _default_entity_card)
    classify_fn: Callable[[str], QueryIntent] = field(default_factory=lambda: _default_classify)
    health_deps: HealthDeps = field(default_factory=HealthDeps)


_RESEARCH_WORDS_DEFAULT_BUDGET = 5000
_LOOKUP_BUDGET = 1500
_DEFAULT_BUDGET = 3000


def _infer_budget(
    query: str,
    explicit_budget: int,
    intent: QueryIntent | None,
) -> int:
    """Adjust the budget based on query intent.

    Quick lookups (entity / keyword) get the small budget; the caller's
    explicit non-default value overrides everything.

    ``intent`` is the single classification ``run_search`` already
    performed for this request (``None`` when the classifier failed) —
    reused here instead of re-classifying so the query is classified once
    per request, not twice (PLA-273 warm-path dedup).
    """
    if explicit_budget != _DEFAULT_BUDGET:
        return explicit_budget
    if intent in (QueryIntent.ENTITY, QueryIntent.KEYWORD):
        return _LOOKUP_BUDGET
    import re

    if re.search(r"\b(research|compare|analyse|analyze|comprehensive|detailed)\b", query, re.IGNORECASE):
        return _RESEARCH_WORDS_DEFAULT_BUDGET
    return _DEFAULT_BUDGET


def _classify_for_request(query: str, classify: Callable[[str], QueryIntent]) -> QueryIntent | None:
    """Classify ``query`` once per request; ``None`` on classifier failure.

    The single classification site for ``run_search``: its result sizes
    the token budget AND threads into the pipeline so the pipeline reuses
    it rather than re-classifying the same query (PLA-273). Failure is
    isolated to ``None`` so the budget falls back to its heuristic and the
    pipeline classifies internally.
    """
    try:
        return classify(query)
    except Exception:
        logger.debug("intent classification failed; using heuristic budget", exc_info=True)
        return None


_ENTITY_PREFIX_RE = None


def _extract_entity_name(query: str) -> str:
    global _ENTITY_PREFIX_RE
    if _ENTITY_PREFIX_RE is None:
        import re

        _ENTITY_PREFIX_RE = re.compile(
            r"^(what\s+is|who\s+is|tell\s+me\s+about|what\s+do\s+we\s+know\s+about)\s+",
            re.IGNORECASE,
        )
    return _ENTITY_PREFIX_RE.sub("", query).strip().rstrip("?!. ")


def _budgeted_to_hit(b: Any) -> SearchHit:
    """Project a BudgetedResult onto the uniform SearchHit shape."""
    inner = getattr(b, "result", None)
    if inner is None:
        return SearchHit(path="", title="", snippet="", score=0.0)
    snippet_src = getattr(b, "content", "") or getattr(inner, "snippet", "") or ""
    raw_page = getattr(inner, "source_page", None)
    raw_seq = getattr(inner, "seq", None)
    return SearchHit(
        path=str(getattr(inner, "path", "")),
        title=str(getattr(inner, "title", "") or ""),
        snippet=snippet_src[:500],
        score=float(getattr(inner, "boosted_score", getattr(inner, "score", 0.0))),
        tier=str(getattr(b, "tier", "")),
        tokens=int(getattr(b, "token_estimate", 0)),
        collection=str(getattr(inner, "collection", "") or ""),
        source_page=int(raw_page) if isinstance(raw_page, int) else None,
        # PLA-274 — carry the canonical breadcrumb off the fused result.
        source_uri=str(getattr(inner, "source_uri", "") or ""),
        # PLA-270 — carry the typed chunk seq off the fused result.
        seq=int(raw_seq) if isinstance(raw_seq, int) else None,
    )


def search_output_to_envelope(out: SearchOutput) -> dict[str, Any]:
    """Project a ``SearchOutput`` to the JSON envelope MCP callers receive.

    Both the MCP adapter (``tool_search``) and BDD step files use this
    helper so the envelope shape has one definition. Agents call
    ``tool_search`` and read this dict; the dict's contents are the
    use case's data, projected.
    """
    return {
        "query": out.query,
        "intent": out.intent,
        "results": [
            {
                "path": h.path,
                "title": h.title,
                "snippet": h.snippet,
                "score": h.score,
                "tier": h.tier,
                "tokens": h.tokens,
                _KEY_COLLECTION: h.collection,
                # MM-3 — per-page citation surfaced to MCP / CLI callers.
                # ``None`` for non-paged documents.
                _KEY_SOURCE_PAGE: h.source_page,
                # PLA-274 — the canonical resolvable breadcrumb. ``path`` stays
                # for display; ``source_uri`` is what an agent cites/re-opens.
                # ``locator`` is the within-document anchor for non-paged docs.
                # These keys mirror ``SourceRef`` so the per-hit dict round-trips
                # through ``SourceRef.from_envelope`` losslessly.
                _KEY_SOURCE_URI: h.source_ref().source_uri,
                _KEY_LOCATOR: h.source_ref().locator,
                # PLA-270 — the typed chunk-sequence expansion key. ``None``
                # for non-chunked rows; PLA-268's chunk-expansion tool reads
                # ``seq`` + ``source_uri`` rather than re-parsing ``#N``.
                _KEY_SEQ: h.seq,
                **({"source": h.source, "entity": h.entity} if h.source else {}),
                # ADR-036 §Q7 — entity-summary chunks from the projector
                # carry source_uri='entity://<QID>'; the MCP renderer + any
                # downstream agent can gate on this flag to render a
                # Wikidata badge or treat the row as external context.
                **({"entity_summary": True} if h.path.startswith("entity://") else {}),
            }
            for h in out.results
        ],
        _KEY_BM25_COUNT: out.bm25_count,
        _KEY_VEC_COUNT: out.vec_count,
        _KEY_FUSED_COUNT: out.fused_count,
        _KEY_VEC_FAILED: out.vec_failed,
        _KEY_TOTAL_TOKENS: out.total_tokens,
        _KEY_LATENCY_MS: out.latency_ms,
        "health": dict(health_to_envelope(out.health)),
        "error": out.error,
    }


def _intent_value(sr: Any) -> str:
    """Coerce ``sr.intent`` (possibly an Enum or string) into a plain value string."""
    intent = getattr(sr, "intent", None)
    value = getattr(intent, "value", None)
    if value is not None:
        return str(value)
    return str(intent or "")


def _fetch_entity_card_safe(entity_card: Callable[[str], Any], name: str) -> Any:
    """Call the entity-card lookup; swallow exceptions and return None."""
    try:
        return entity_card(name)
    except Exception:
        logger.debug("entity card lookup failed", exc_info=True)
        return None


def _entity_card_hit(card: dict[str, Any]) -> SearchHit:
    """Build the entity-graph SearchHit prepended to ENTITY-intent results."""
    summary = card.get("summary", "")
    return SearchHit(
        path=card.get("vault_path", ""),
        title=card.get("name", ""),
        snippet=summary,
        score=1.0,
        tokens=estimate_tokens(summary),
        source="entity_graph",
        entity={
            "id": card.get("id", ""),
            "name": card.get("name", ""),
            "type": card.get("type", ""),
        },
    )


def _maybe_prepend_entity_card(
    hits: list[SearchHit],
    query: str,
    intent_value: str,
    entity_card: Callable[[str], Any],
    include_entity_card: bool,
) -> None:
    """When the query is ENTITY-intent and a card exists, prepend it to ``hits``."""
    if not include_entity_card or intent_value != QueryIntent.ENTITY.value:
        return
    name = _extract_entity_name(query)
    if not name:
        return
    card = _fetch_entity_card_safe(entity_card, name)
    if card is None:
        return
    hits.insert(0, _entity_card_hit(card))


def _search_output_from_pipeline(
    sr: Any,
    query: str,
    hits: list[SearchHit],
    health: KairixHealth,
    elapsed_ms: float,
) -> SearchOutput:
    """Map the raw ``SearchPipeline`` result onto a ``SearchOutput`` envelope."""
    return SearchOutput(
        query=getattr(sr, "query", query),
        intent=_intent_value(sr),
        results=hits,
        bm25_count=int(getattr(sr, _KEY_BM25_COUNT, 0) or 0),
        vec_count=int(getattr(sr, _KEY_VEC_COUNT, 0) or 0),
        fused_count=int(getattr(sr, _KEY_FUSED_COUNT, 0) or 0),
        vec_failed=bool(getattr(sr, _KEY_VEC_FAILED, False)),
        total_tokens=int(getattr(sr, _KEY_TOTAL_TOKENS, 0) or 0),
        latency_ms=float(getattr(sr, _KEY_LATENCY_MS, elapsed_ms) or elapsed_ms),
        health=health,
        error=str(getattr(sr, "error", "") or ""),
    )


class SearchSeamSignatureError(TypeError):
    """A ``search_fn`` whose signature drifts from the seam contract.

    PLA-281: ``run_search`` wraps the pipeline call in a broad ``except`` that
    turns any failure into an empty ``SearchOutput.error``. That is right for a
    genuine runtime failure, but wrong for a fake/real ``search_fn`` whose
    signature no longer matches the seam — a programmer error that should fail
    loudly, not silently return empty results (the exact swallow that let a
    DI-seam change go green locally and red in CI Stage 3). This distinct type
    is re-raised past that broad ``except`` so the mismatch surfaces.
    """


def _invoke_search_fn(search_fn: Callable[..., Any], /, **kwargs: Any) -> Any:
    """Call the ``search_fn`` DI seam, surfacing a signature mismatch loudly.

    A binding ``TypeError`` (wrong/missing/extra kwarg) is raised by the call
    machinery *before* ``search_fn``'s body runs, so its traceback stops at
    this frame (``tb_next is None``). We convert that into
    ``SearchSeamSignatureError`` so ``run_search``'s broad ``except`` can't
    swallow a programmer error into empty results. A ``TypeError`` raised
    *inside* ``search_fn`` descends past this frame (``tb_next`` is set) and is
    re-raised unchanged — a genuine runtime failure the broad ``except`` still
    handles gracefully.
    """
    try:
        return search_fn(**kwargs)
    except TypeError as exc:
        tb = exc.__traceback__
        if tb is not None and tb.tb_next is None:
            raise SearchSeamSignatureError(str(exc)) from exc
        raise


def run_search(
    query: str,
    *,
    agent: str | None = None,
    scope: Scope = Scope.SHARED_AGENT,
    collections: list[str] | None = None,
    budget: int = _DEFAULT_BUDGET,
    limit: int = 10,
    include_entity_card: bool = True,
    max_tier: str = "L2",
    deps: SearchDeps | None = None,
) -> SearchOutput:
    """Run hybrid search and return a structured result.

    Never raises — failures populate ``SearchOutput.error`` and return
    an otherwise-empty result.

    Args:
        query: User's natural-language query.
        agent: Agent name for collection scoping; None = no scoping.
        scope: Multi-agent scope.
        collections: Explicit collection allow-list. ``None`` preserves
            resolver/default scope behaviour; ``[]`` means an intentionally
            empty resolved scope.
        budget: Token budget. Default 3000 triggers intent-based auto-scaling
            (1500 for entity/keyword, 5000 for research-style queries);
            any explicit non-default value is used unchanged.
        limit: Maximum number of hits returned.
        include_entity_card: When True (the default) and the query is
            classified as ENTITY, prepend a graph-card hit. CLI callers
            who only want flat results pass False.
        max_tier: PLA-270 tiered-context ceiling — the richest representation
            to return per hit: ``"L0"`` abstracts (cheapest), ``"L1"``
            overviews, or ``"L2"`` full snippets (the default). Lets an agent
            request the cheapest sufficient context. Takes effect when the
            pipeline has tier summaries wired; otherwise every hit is L2.
        deps: Injectable dependencies; production callers leave None.
    """
    if deps is None:  # pragma: no cover — production lazy default; tests pass deps=SearchDeps(...)
        deps = SearchDeps()
    # TTL-cached on the warm path (the canonical kairix.core.health cache, also
    # used by run_brief/run_prep) so repeated/concurrent searches don't each
    # re-stat the filesystem + respawn the 4 probe threads per request (PLA-273).
    health = _tool_health(cached_probe_health(deps.health_deps), tool=_search_next_action)

    started = time.monotonic()
    try:
        # Classify ONCE per request, then reuse for budget sizing AND the
        # pipeline so the query isn't classified twice (PLA-273).
        intent = _classify_for_request(query, deps.classify_fn)
        effective_budget = _infer_budget(query, budget, intent)
        search_kwargs: dict[str, Any] = {
            "query": query,
            "agent": agent,
            "scope": scope,
            "budget": effective_budget,
            "intent": intent,
            "max_tier": max_tier,
        }
        if collections is not None:
            search_kwargs["collections"] = collections
        sr = _invoke_search_fn(
            deps.search_fn,
            **search_kwargs,
        )
        intent_value = _intent_value(sr)
        hits: list[SearchHit] = [_budgeted_to_hit(b) for b in getattr(sr, "results", [])[:limit]]
        _maybe_prepend_entity_card(hits, query, intent_value, deps.entity_card_fn, include_entity_card)
        elapsed_ms = (time.monotonic() - started) * 1000
        return _search_output_from_pipeline(sr, query, hits, health, elapsed_ms)
    except SearchSeamSignatureError:
        # PLA-281: a DI-seam signature mismatch is a programmer error — fail
        # loudly instead of letting the broad except below return empty results.
        raise
    except Exception as exc:
        logger.warning("run_search failed: %s", exc, exc_info=True)
        return SearchOutput(
            query=query,
            intent="",
            results=[],
            health=health,
            error=f"{type(exc).__name__}: {exc}",
        )


# ---------------------------------------------------------------------------
# Queue-aware search (ADR-029 G.1) — folded from the MCP adapter (PLA-322).
#
# The queue-aware behaviour used to be a SECOND implementation living in
# ``kairix/agents/mcp/tools/retrieval.py`` (``tool_search_queue_aware``) that
# re-wrapped the ``tool_search`` adapter. It now lives ONCE here in the use
# case layer as :func:`run_search_queue_aware`; the MCP adapter selects it via
# the ``tool_search(queue_aware=True)`` parameter (and a thin backward-compat
# ``tool_search_queue_aware`` shim). Both CLI and MCP reach the SAME
# :func:`run_search` — queue-awareness is a use-case-layer composition over it,
# not a parallel search path. This closes the residual retrieval-domain
# duplication after the W1b adapter split.
# ---------------------------------------------------------------------------


def _default_flag_reader(name: str) -> bool:
    """Production feature-flag reader — defers the import to call time."""
    from kairix.core.features import flag

    return flag(name)


def _default_queue_db() -> Any:
    """Default queue-db factory — None until the production conn is wired through."""
    return None


def _default_queue_search(
    query: str,
    agent: str | None = None,
    scope: Scope = Scope.SHARED_AGENT,
    budget: int = _DEFAULT_BUDGET,
    limit: int = 10,
    *,
    deps: SearchDeps | None = None,
) -> dict[str, Any]:
    """Production search delegate for the queue path — run the use case, serialise.

    The fold's key move (PLA-322): the default queue delegate calls
    :func:`run_search` + :func:`search_output_to_envelope` directly, so the
    queue-aware path no longer routes through the MCP adapter layer
    (``tool_search``). The envelope dict it returns is exactly what carry-along
    augments and the dispatch decorator records as ``result_json``.
    """
    return search_output_to_envelope(run_search(query, agent=agent, scope=scope, budget=budget, limit=limit, deps=deps))


@dataclass(frozen=True)
class QueueAwareSearchDeps:
    """Injectable dependencies for :func:`run_search_queue_aware`.

    F6-clean: every field has a ``default_factory`` so production callers
    construct ``QueueAwareSearchDeps()`` and get the real boundary calls; tests
    construct ``QueueAwareSearchDeps(flag_reader=lambda _: True, ...)`` and pass
    it as a single argument. Matches ``SearchDeps``'s discipline.

    Fields:
      * ``flag_reader`` — feature-flag lookup; default :func:`_default_flag_reader`
        (calls :func:`kairix.core.features.flag`).
      * ``search_fn`` — the envelope-returning search delegate; default
        :func:`_default_queue_search`, which runs :func:`run_search` and
        serialises it. Tests pass a stub so the dispatch/queue surface is the
        property under test, not the search pipeline.
      * ``queue_db_factory`` — returns the SQLite connection used for
        carry-along reads. Default returns ``None`` so production callers opt in
        by passing a connection-returning callable; tests pass a
        ``tmp_path``-backed factory.
    """

    flag_reader: Callable[[str], bool] = field(default_factory=lambda: _default_flag_reader)
    search_fn: Callable[..., dict[str, Any]] = field(default_factory=lambda: _default_queue_search)
    queue_db_factory: Callable[[], Any] = field(default_factory=lambda: _default_queue_db)


def run_search_queue_aware(
    query: str,
    *,
    agent: str | None = None,
    scope: Scope = Scope.SHARED_AGENT,
    budget: int = _DEFAULT_BUDGET,
    limit: int = 10,
    agent_id: str | None = None,
    deps: SearchDeps | None = None,
    queue_deps: QueueAwareSearchDeps | None = None,
) -> dict[str, Any] | str:
    """Queue-aware search (ADR-029 G.1) — the single implementation (PLA-322).

    When the ``agent_query_queue`` feature flag is OFF (default), delegates
    straight to the search delegate — the response shape is byte-identical to
    the plain :func:`run_search` envelope. When ON, the call routes through
    :func:`kairix.core.queue.dispatch.dispatch_or_queue` and any completed
    ``pending_queries`` rows for ``agent_id`` are carried back as a prefix
    string keyed under ``"carry_along"`` in the response envelope.

    Args mirror :func:`run_search` plus two seams:

    * ``agent_id`` — the canonical agent identifier from MCP session headers;
      the dedup key for the queue. Falls back to ``"unknown-agent"`` when None.
    * ``queue_deps`` — :class:`QueueAwareSearchDeps` holding the flag-reader /
      search-delegate / queue-db factory. Production callers leave None and the
      dataclass's ``default_factory`` shape wires the real boundary calls; tests
      pass an instance with stubs to drive both branches.

    Returns the same dict shape as the plain search envelope when the queue path
    is OFF or the handler returns within budget. When the queue path is ON and
    the budget is exceeded, returns the plain string
    ``"Processing your request (id: q_<hash>)..."`` — NOT an error envelope — so
    the agent interprets it as "accepted, continue".
    """
    from kairix.core.queue import carry_along
    from kairix.core.queue.dispatch import dispatch_or_queue

    resolved_deps = queue_deps if queue_deps is not None else QueueAwareSearchDeps()
    delegate = resolved_deps.search_fn
    reader = resolved_deps.flag_reader

    if not reader("agent_query_queue"):
        return delegate(query=query, agent=agent, scope=scope, budget=budget, limit=limit, deps=deps)

    resolved_agent_id = agent_id or "unknown-agent"

    @dispatch_or_queue(tool_name="tool_search")
    def _handler(
        query: str,
        agent: str | None,
        scope: Scope,
        budget: int,
        limit: int,
        *,
        agent_id: str,
        deps: Any,
    ) -> dict[str, Any]:
        # `agent_id` is part of the dispatch_or_queue contract — the decorator
        # reads it via kwargs.get('agent_id') on the wrapper layer to build the
        # dedup hash + the pending_queries row owner; log it here so the
        # parameter has a real consumer (F19 — every named parameter load-bearing).
        logger.debug("tool_search dispatched for agent_id=%r", agent_id)
        return delegate(query=query, agent=agent, scope=scope, budget=budget, limit=limit, deps=deps)

    result: Any = _handler(
        query,
        agent,
        scope,
        budget,
        limit,
        agent_id=resolved_agent_id,
        deps=deps,
    )

    # Plain-text queued path — pass through unchanged so the agent reads it as
    # "accepted, continue".
    if isinstance(result, str):
        return result

    # Non-plain-text: the delegate returned the envelope dict. Narrow to a typed
    # dict so the return isn't a bare Any (mypy --strict no-any-return).
    envelope: dict[str, Any] = result
    prefix = carry_along.carry_along_prefix_safe(resolved_agent_id, resolved_deps.queue_db_factory())
    if prefix:
        return {**envelope, "carry_along": prefix}
    return envelope


def _search_next_action(health: KairixHealth) -> str:
    """Wrapper so ``_tool_health`` keeps a stable callable shape."""
    return search_next_action(health)


def _tool_health(base: KairixHealth, *, tool: Callable[[KairixHealth], str]) -> KairixHealth:
    """Replace the shared ``next_action`` with the tool-specific directive.

    Returns the snapshot unchanged when ``tool`` returns the empty string
    (fully healthy or the tool is happy with the shared directive).
    Reasons remain whatever the probe set them to; only the directive
    changes so the agent reads "what to do **for this tool**" right now.
    """
    directive = tool(base)
    if not directive:
        return base
    return KairixHealth(
        vector_search=base.vector_search,
        bm25=base.bm25,
        chat=base.chat,
        secrets_loaded=base.secrets_loaded,
        degraded_reason=base.degraded_reason,
        next_action=directive,
    )

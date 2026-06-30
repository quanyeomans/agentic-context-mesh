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


def _default_search(
    query: str,
    budget: int,
    scope: Scope,
    agent: str | None,
    collections: list[str] | None = None,
    intent: QueryIntent | None = None,
) -> Any:
    """Lazy-load the production search pipeline.

    Kept inside the use-case module (rather than a shim) so the file
    owns its own production wiring — eliminates the ``_search_defaults``
    indirection layer that the F7 coverage baseline had to grandfather.

    ``collections``: when set (via the CLI's ``--collection`` flag, which
    threads through ``SearchDeps._search_with_collection``), the value
    is passed to ``pipeline.search`` so retrieval is restricted to the
    listed collection set. Default ``None`` preserves scope-based
    resolution.

    ``intent``: when ``run_search`` has already classified the query (to
    size the token budget) it threads the result through so the pipeline
    reuses it instead of classifying the same query a second time
    (PLA-273 warm-path dedup). ``None`` preserves the pipeline's internal
    classification.
    """
    from kairix.core.factory import build_search_pipeline
    from kairix.core.search.config_loader import load_config

    pipeline = build_search_pipeline(config=load_config())
    return pipeline.search(query=query, budget=budget, scope=scope, agent=agent, collections=collections, intent=intent)


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
    return SearchHit(
        path=str(getattr(inner, "path", "")),
        title=str(getattr(inner, "title", "") or ""),
        snippet=snippet_src[:500],
        score=float(getattr(inner, "boosted_score", getattr(inner, "score", 0.0))),
        tier=str(getattr(b, "tier", "")),
        tokens=int(getattr(b, "token_estimate", 0)),
        collection=str(getattr(inner, "collection", "") or ""),
        source_page=int(raw_page) if isinstance(raw_page, int) else None,
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


def run_search(
    query: str,
    *,
    agent: str | None = None,
    scope: Scope = Scope.SHARED_AGENT,
    budget: int = _DEFAULT_BUDGET,
    limit: int = 10,
    include_entity_card: bool = True,
    deps: SearchDeps | None = None,
) -> SearchOutput:
    """Run hybrid search and return a structured result.

    Never raises — failures populate ``SearchOutput.error`` and return
    an otherwise-empty result.

    Args:
        query: User's natural-language query.
        agent: Agent name for collection scoping; None = no scoping.
        scope: Multi-agent scope.
        budget: Token budget. Default 3000 triggers intent-based auto-scaling
            (1500 for entity/keyword, 5000 for research-style queries);
            any explicit non-default value is used unchanged.
        limit: Maximum number of hits returned.
        include_entity_card: When True (the default) and the query is
            classified as ENTITY, prepend a graph-card hit. CLI callers
            who only want flat results pass False.
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
        sr = deps.search_fn(query=query, agent=agent, scope=scope, budget=effective_budget, intent=intent)
        intent_value = _intent_value(sr)
        hits: list[SearchHit] = [_budgeted_to_hit(b) for b in getattr(sr, "results", [])[:limit]]
        _maybe_prepend_entity_card(hits, query, intent_value, deps.entity_card_fn, include_entity_card)
        elapsed_ms = (time.monotonic() - started) * 1000
        return _search_output_from_pipeline(sr, query, hits, health, elapsed_ms)
    except Exception as exc:
        logger.warning("run_search failed: %s", exc, exc_info=True)
        return SearchOutput(
            query=query,
            intent="",
            results=[],
            health=health,
            error=f"{type(exc).__name__}: {exc}",
        )


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

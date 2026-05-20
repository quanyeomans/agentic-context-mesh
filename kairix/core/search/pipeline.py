"""SearchPipeline — the orchestrator that composes protocols into a search pipeline.

Replaces the procedural search() function in hybrid.py with a composed object.
Each stage is a protocol implementation injected at construction time, so tests
can swap any component with a fake — no monkey-patching needed.

Pipeline stages:
  1. Classify query intent
  2. Fuse BM25 + vector results
  3. Apply boost chain
  4. Apply token budget
  5. Log search event
"""

from __future__ import annotations

import datetime
import hashlib
import logging
import time
from dataclasses import dataclass, field
from typing import Any

from kairix.core.protocols import (
    BoostStrategy,
    CollectionResolver,
    FactStore,
    FusionStrategy,
    GraphRepository,
    SearchLogger,
)
from kairix.core.search.backends import BM25SearchBackend, VectorSearchBackend
from kairix.core.search.budget import apply_budget
from kairix.core.search.config import RetrievalConfig
from kairix.core.search.intent import QueryIntent
from kairix.core.search.query_cache import QueryResultCache, make_cache_key
from kairix.core.search.rrf import FusedResult
from kairix.core.search.scope import Scope

logger = logging.getLogger(__name__)


@dataclass
class SearchResult:
    """Full result from the search pipeline."""

    query: str
    intent: QueryIntent
    results: list = field(default_factory=list)

    # Diagnostic info
    bm25_count: int = 0
    vec_count: int = 0
    fact_count: int = 0
    fused_count: int = 0
    # Per-stage wall-clock latency (ms) — populated by SearchPipeline.search.
    # Keys: classify, resolve, dispatch (bm25+vec parallel), fuse, enrich,
    # boost, budget. Sums approximately to total latency_ms minus a few ms
    # of bookkeeping. Used by ``kairix probe search --json`` to surface
    # which pipeline stage dominates the wall-clock cost (#282).
    stage_latency_ms: dict[str, float] = field(default_factory=dict)
    collections: list[str] = field(default_factory=list)
    tiers_used: list[str] = field(default_factory=list)
    total_tokens: int = 0
    latency_ms: float = 0.0
    vec_failed: bool = False
    fallback_used: bool = False
    error: str = ""


@dataclass
class SearchPipeline:
    """Composes protocol implementations into a complete search pipeline.

    Constructed once at startup via build_search_pipeline() factory (or directly
    in tests with fakes). Each field is a protocol implementation — swap any
    one to change behaviour without touching orchestration logic.
    """

    classifier: object  # IntentClassifier
    bm25: BM25SearchBackend
    vector: VectorSearchBackend
    graph: GraphRepository
    fusion: FusionStrategy
    boosts: list[BoostStrategy] = field(default_factory=list)
    logger: SearchLogger | None = None
    resolver: CollectionResolver | None = None
    config: RetrievalConfig = field(default_factory=RetrievalConfig.defaults)
    # In-process query-result cache (#281). When None, no caching is
    # applied — preserves existing test behaviour where pipelines are
    # constructed directly with fakes. Factories that want caching
    # inject a shared QueryResultCache instance per process.
    query_cache: QueryResultCache | None = None
    # Plan B-parity Capability #5 — optional fact retriever federated
    # alongside BM25 + vector. When ``None``, the federation stage is a
    # no-op and the pipeline degenerates to today's chunk-only behaviour
    # (regression-pinned). When wired, attribute-fact queries weight
    # fact hits ~2x their chunk counterparts in the fusion stage.
    fact_retriever: FactStore | None = None
    # Top-k feed into the fact retriever. Lossier than chunks, so the
    # default is roomy enough to give the intent-weighted fusion stage
    # genuine candidates to surface.
    top_k_facts: int = 15

    def search(
        self,
        query: str,
        budget: int = 3000,
        scope: Scope = Scope.SHARED_AGENT,
        agent: str | None = None,
        collections: list[str] | None = None,
        namespace: str | None = None,
    ) -> SearchResult:
        """Execute the full search pipeline using composed components.

        ``namespace`` is the engagement-scoped recall key threaded through
        to the optional ``fact_retriever`` (Capability #5). When the
        pipeline has no fact retriever wired, ``namespace`` is ignored.

        Never raises — returns SearchResult with empty results on any failure.
        """
        # 0. Query-cache fast path (#281). When a cache is wired and the
        # key hits, return the cached SearchResult immediately — sidesteps
        # the entire pipeline including the dominant Azure embed HTTP cost.
        cache_key: tuple[Any, ...] | None = None
        if self.query_cache is not None:
            cache_key = make_cache_key(query, scope, agent, collections)
            cached = self.query_cache.get(cache_key)
            if cached is not None:
                return cached

        t_start = time.monotonic()
        stages: dict[str, float] = {}

        def _stage(name: str, start: float) -> None:
            """Record one stage's wall-clock duration into the stages dict."""
            stages[name] = round((time.monotonic() - start) * 1000.0, 2)

        # 1. Classify intent
        t = time.monotonic()
        intent = self._classify(query)
        _stage("classify", t)

        # 2. Entity intent requires graph
        if intent == QueryIntent.ENTITY and not self.graph.available:
            return SearchResult(query=query, intent=intent, error=_ENTITY_GRAPH_UNAVAILABLE, stage_latency_ms=stages)

        # 3. Resolve collections via the injected CollectionResolver
        t = time.monotonic()
        collections, resolve_error = self._resolve_collections(collections, agent, scope)
        _stage("resolve", t)
        if resolve_error is not None:
            return SearchResult(query=query, intent=intent, error=resolve_error, stage_latency_ms=stages)

        # 4. Dispatch BM25 + vector search — split into bm25/vector inside the helper
        t = time.monotonic()
        bm25_results, vec_results, vec_failed = self._dispatch_backends(query, collections, stages)
        _stage("dispatch", t)

        # 4b. Federation — dispatch the optional fact retriever (Cap #5).
        # Degrades to empty list when no retriever is wired or it raises;
        # never poisons the chunk-only pipeline result.
        t = time.monotonic()
        fact_hits = self._dispatch_facts(query, namespace)
        _stage("facts", t)

        # 5. Fuse — intent-weighted when the fact layer is wired (Cap #5).
        t = time.monotonic()
        fused = self._fuse_with_intent(bm25_results, vec_results, fact_hits, intent)
        _stage("fuse", t)

        # 5b. Enrich each fused result with chunk_date metadata so the boost
        # chain (specifically ChunkDateBoost) can score by recency. Source
        # of truth is DocumentRepository.get_chunk_dates, exposed here via
        # the BM25 backend — boost adapters never reach into the repo.
        t = time.monotonic()
        self._enrich_chunk_dates(fused)
        _stage("enrich", t)

        # 6. Boost chain
        t = time.monotonic()
        fused = self._apply_boosts(fused, query, intent)
        _stage("boost", t)

        # 7. Budget
        t = time.monotonic()
        budgeted = apply_budget(fused, budget=budget)
        _stage("budget", t)
        total_tokens = sum(getattr(r, "token_estimate", 0) for r in budgeted)
        tiers_used = sorted({getattr(r, "tier", "L2") for r in budgeted})

        latency_ms = (time.monotonic() - t_start) * 1000.0

        result = SearchResult(
            query=query,
            intent=intent,
            results=budgeted,
            bm25_count=len(bm25_results),
            vec_count=len(vec_results),
            fact_count=len(fact_hits),
            fused_count=len(fused),
            collections=collections or [],
            tiers_used=tiers_used,
            total_tokens=total_tokens,
            latency_ms=latency_ms,
            vec_failed=vec_failed,
            fallback_used=not bm25_results and bool(vec_results),
            stage_latency_ms=stages,
        )

        # 8. Log
        self._log_search(query, intent, agent, scope, collections, result)

        # 9. Cache write (#281) — only cache successful results. Caching
        # errors would mask transient outages from subsequent retries
        # and stick a degraded answer in front of every same-key caller
        # for the next 5 minutes.
        if cache_key is not None and self.query_cache is not None and not result.error:
            self.query_cache.put(cache_key, result)

        return result

    def _classify(self, query: str) -> QueryIntent:
        """Classify intent; fall back to SEMANTIC on any classifier failure."""
        try:
            return self.classifier.classify(query)  # type: ignore[union-attr] — classifier may be None when graph backend unavailable; guarded by try
        except Exception as e:
            _logger.warning("pipeline: classify failed — %s", e)
            return QueryIntent.SEMANTIC

    def _resolve_collections(
        self,
        collections: list[str] | None,
        agent: str | None,
        scope: Scope,
    ) -> tuple[list[str] | None, str | None]:
        """Resolve collections via the injected resolver when not pre-supplied."""
        if collections is not None or self.resolver is None:
            return collections, None
        try:
            return self.resolver.resolve(agent, scope), None
        except NotImplementedError as e:
            # Operator misconfiguration (scope=all-agents without registry).
            return None, str(e)

    def _dispatch_backends(
        self,
        query: str,
        collections: list[str] | None,
        stages: dict[str, float] | None = None,
    ) -> tuple[list[dict], list[dict], bool]:
        """Run BM25 + vector search; isolate each failure so one can't break the other.

        When ``stages`` is supplied, records ``bm25`` and ``vector`` wall-clock
        deltas into it so the caller can decompose the ``dispatch`` stage in
        SearchResult.stage_latency_ms (#282 follow-up: probe data showed the
        dispatch stage owns 92% of total wall-clock; this split says how much
        is BM25 vs embed+vector).
        """
        cfg = self.config
        bm25_results: list[dict] = []
        t = time.monotonic()
        try:
            bm25_results = self.bm25.search(query, collections=collections, limit=cfg.bm25_limit)
        except Exception as e:
            _logger.warning("pipeline: BM25 search failed — %s", e)
        if stages is not None:
            stages["bm25"] = round((time.monotonic() - t) * 1000.0, 2)

        t = time.monotonic()
        vec_results, vec_failed = self._dispatch_vector(query, collections, stages=stages)
        if stages is not None:
            stages["vector"] = round((time.monotonic() - t) * 1000.0, 2)
        return bm25_results, vec_results, vec_failed

    def _dispatch_vector(
        self,
        query: str,
        collections: list[str] | None,
        stages: dict[str, float] | None = None,
    ) -> tuple[list[dict], bool]:
        """Vector backend dispatch with skip-flag and failure-vs-empty distinction.

        ``vec_failed`` reflects backend failure only — operators consume
        this field to triage real outages. Empty-and-failed conflation
        produced false-positive alerts.

        When ``stages`` is supplied, records the ``embed_http`` and
        ``vector_ann`` split via the VectorSearchBackend timing hook so
        probe data can attribute slow tail queries to Azure HTTP tail vs
        local ANN cost (#282 follow-up). ``vector`` (the sum) stays the
        outer-wall total recorded in ``_dispatch_backends``.
        """
        if self.config.skip_vector:
            return [], False
        try:
            return (
                self.vector.search(
                    query,
                    collections=collections,
                    limit=self.config.vec_limit,
                    timings=stages,
                ),
                False,
            )
        except Exception as e:
            _logger.warning("pipeline: vector search failed — %s", e)
            return [], True

    def _dispatch_facts(self, query: str, namespace: str | None) -> list[Any]:
        """Dispatch the optional fact retriever; degrade gracefully on failure.

        Returns the FactHit list verbatim — the conversion to fused
        ``FusedResult`` shape happens inside ``_fuse_with_intent`` so
        fact_count diagnostics still see the raw hit list.

        Plan B-parity Capability #5. When ``fact_retriever`` is None the
        federation stage is a no-op; when it raises, the chunk-only
        pipeline is returned as a graceful degradation (logged at WARN
        so operators can triage without poisoning the response).
        """
        if self.fact_retriever is None:
            return []
        try:
            return list(self.fact_retriever.search(query, top_k=self.top_k_facts, namespace=namespace))
        except Exception as e:
            _logger.warning("pipeline: fact retriever raised — facts excluded from fusion: %s", e)
            return []

    def _fuse(self, bm25_results: list[dict], vec_results: list[dict]) -> list:
        """Fuse BM25 + vector results; on fusion failure return empty list."""
        try:
            return self.fusion.fuse(bm25_results, vec_results)
        except Exception as e:
            _logger.warning("pipeline: fusion failed — %s — falling back to empty fused list", e)
            return []

    def _fuse_with_intent(
        self,
        bm25_results: list[dict],
        vec_results: list[dict],
        fact_hits: list[Any],
        intent: QueryIntent,
    ) -> list:
        """Intent-weighted fusion across BM25 + vector + fact hits.

        Three-way fusion (Plan B-parity Capability #5):

        - ATTRIBUTE_FACT → facts dominate (0.6 / 0.3 / 0.1)
        - MULTI_HOP     → chunks dominate (0.7 / 0.2 / 0.1)
        - default       → balanced (today's chunk-only fusion is preserved
                          when ``fact_hits`` is empty).

        When ``fact_hits`` is empty (no retriever wired, or it returned
        nothing, or it raised), the fact_weight branch contributes zero
        and the pipeline degenerates EXACTLY to ``_fuse`` (regression
        contract — pinned in test_pipeline_without_fact_retriever_unchanged).
        """
        chunk_fused = self._fuse(bm25_results, vec_results)
        if not fact_hits:
            return chunk_fused

        fact_w, chunk_w, _graph_w = _fusion_weights_for_intent(intent)
        # Normalise each layer's scores to [0, 1] before applying the
        # per-intent weights. Without this, fact-store overlap scores
        # (raw range 0..1) overwhelm RRF scores (raw range 0..~0.033)
        # regardless of weighting — the weighting contract only holds
        # when both layers are on the same scale. Normalisation is
        # max-relative so a single-row layer still scores 1.0 x weight.
        max_chunk = max((_read_rrf_score(r) for r in chunk_fused), default=0.0) or 1.0
        for fused_result in chunk_fused:
            current = _read_rrf_score(fused_result)
            _write_rrf_score(fused_result, (current / max_chunk) * chunk_w)

        max_fact = max((float(h.score) for h in fact_hits), default=0.0) or 1.0
        fact_fused = [_fused_from_fact_hit(hit, fact_w, denom=max_fact) for hit in fact_hits]

        # Merge by weighted-and-normalised score.
        combined: list[Any] = list(chunk_fused) + list(fact_fused)
        combined.sort(key=_read_rrf_score, reverse=True)
        return combined

    def _enrich_chunk_dates(self, fused: list) -> None:
        """Fill in ``chunk_date`` on each fused result so date-aware boosts see it."""
        if not fused:
            return
        try:
            paths = [getattr(r, "path", "") for r in fused]
            chunk_dates = self.bm25.get_chunk_dates(paths)
            for r in fused:
                cd = chunk_dates.get(getattr(r, "path", ""))
                if cd and not getattr(r, "chunk_date", ""):
                    r.chunk_date = cd
        except Exception as e:
            _logger.warning("pipeline: chunk_date enrichment failed — %s", e)

    def _apply_boosts(self, fused: list, query: str, intent: QueryIntent) -> list:
        """Apply each boost in order; per-boost failures are logged and skipped."""
        context = {
            "intent": intent,
            "query": query,
            "graph": self.graph,
            "query_date": _extract_query_date(query),
        }
        for boost in self.boosts:
            try:
                fused = boost.boost(fused, query, context)
            except Exception as e:
                _logger.warning("pipeline: boost %s failed — %s", type(boost).__name__, e)
        return fused

    def _log_search(
        self,
        query: str,
        intent: QueryIntent,
        agent: str | None,
        scope: Scope,
        collections: list[str] | None,
        result: SearchResult,
    ) -> None:
        """Emit a search log entry; never raises."""
        if not self.logger:
            return
        query_hash = hashlib.sha256(query.encode()).hexdigest()[:12]
        try:
            self.logger.log_search(
                {
                    "query_hash": query_hash,
                    "intent": intent.value,
                    "agent": agent,
                    # scope is a Scope enum (str subclass); .value for stable serialisation
                    "scope": scope.value if hasattr(scope, "value") else str(scope),
                    "collections_searched": collections or [],
                    "bm25_count": result.bm25_count,
                    "vec_count": result.vec_count,
                    "fused_count": result.fused_count,
                    "total_tokens": result.total_tokens,
                    "latency_ms": round(result.latency_ms, 1),
                    "vec_failed": result.vec_failed,
                    "fallback_used": result.fallback_used,
                    "ts": int(time.time()),
                }
            )
        except Exception as e:
            _logger.warning("pipeline: log_search failed — %s", e)


_ENTITY_GRAPH_UNAVAILABLE = (
    "Entity queries require Neo4j but the graph is unavailable. "
    "Check KAIRIX_NEO4J_URI, KAIRIX_NEO4J_USER, KAIRIX_NEO4J_PASSWORD "
    "and run `kairix onboard check` for diagnostics."
)


_logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Intent-weighted fusion helpers (Plan B-parity Capability #5).
#
# Tuple shape: (fact_weight, chunk_weight, graph_weight).
# graph_weight is reserved for the future entity-graph contribution
# inside the federation stage; today it's threaded through unused so the
# weighting matrix can grow without changing call sites.
# ---------------------------------------------------------------------------

_FUSION_WEIGHTS: dict[QueryIntent, tuple[float, float, float]] = {
    QueryIntent.ATTRIBUTE_FACT: (0.6, 0.3, 0.1),
    QueryIntent.MULTI_HOP: (0.2, 0.7, 0.1),
}
_FUSION_WEIGHTS_DEFAULT = (0.33, 0.33, 0.34)


def _fusion_weights_for_intent(intent: QueryIntent) -> tuple[float, float, float]:
    """Return the (fact, chunk, graph) weights for ``intent``.

    Anything not pinned in ``_FUSION_WEIGHTS`` falls through to the
    balanced default — SEMANTIC, KEYWORD, TEMPORAL, ENTITY, PROCEDURAL.
    """
    return _FUSION_WEIGHTS.get(intent, _FUSION_WEIGHTS_DEFAULT)


def _fused_from_fact_hit(hit: Any, fact_weight: float, *, denom: float = 1.0) -> FusedResult:
    """Adapt a ``FactHit`` to ``FusedResult`` so the boost/budget stages consume it.

    Each fact's ``record.value`` becomes the snippet body and the
    synthesised path namespaces it under ``facts://<id>`` so downstream
    consumers (logger, MCP renderer) can tell fact rows apart from chunk
    rows without sniffing field shapes.

    ``denom`` normalises the raw FactHit score against the strongest
    hit in this query's batch — same scale as the normalised chunk
    layer, so per-intent weighting is meaningful.
    """
    record = hit.record
    score = (float(hit.score) / denom) * fact_weight if denom else 0.0
    snippet = f"{record.entity} {record.attribute}: {record.value}"
    return FusedResult(
        path=f"facts://{record.id}",
        collection="facts",
        title=f"{record.entity} — {record.attribute}",
        snippet=snippet,
        rrf_score=score,
        in_bm25=False,
        in_vec=False,
    )


def _read_rrf_score(row: Any) -> float:
    """Read ``rrf_score`` from a row that may be a dataclass or a dict."""
    if isinstance(row, dict):
        return float(row.get("rrf_score", row.get("score", 0.0)) or 0.0)
    return float(getattr(row, "rrf_score", 0.0) or 0.0)


def _write_rrf_score(row: Any, value: float) -> None:
    """Set ``rrf_score`` to ``value``; mutate in place.

    Supports both ``FusedResult`` dataclass rows (production fusion
    strategies) and plain ``dict`` rows (``FakeFusion`` in unit tests).
    """
    if isinstance(row, dict):
        row["rrf_score"] = float(value)
        return
    try:
        row.rrf_score = float(value)
    except (AttributeError, TypeError):
        # Row is read-only; degrade silently. Fact rows (synthesised as
        # FusedResult) always accept the write; this branch covers
        # immutable rows in third-party fusion strategies.
        pass


def _extract_query_date(query: str) -> datetime.date | None:
    """Best-effort extraction of an explicit calendar date from the query.

    Returns a ``datetime.date`` for the first ISO ``YYYY-MM-DD`` substring
    in the query, or ``None`` if none is present (or if parsing fails).
    Used by the boost chain to drive ``ChunkDateBoost`` recency scoring.
    Never raises.
    """
    import re

    m = re.search(r"\b(\d{4})-(\d{2})-(\d{2})\b", query)
    if not m:
        return None
    try:
        return datetime.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except (ValueError, TypeError):
        return None

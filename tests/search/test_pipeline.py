"""
Tests for kairix.core.search.pipeline.SearchPipeline.

Tests compose the pipeline from fakes — no @patch, no monkey-patching.
Each test constructs a SearchPipeline with the exact fakes it needs.
"""

import pytest

from kairix.core.search.backends import BM25SearchBackend, VectorSearchBackend
from kairix.core.search.config import RetrievalConfig
from kairix.core.search.fusion import RRFFusion
from kairix.core.search.intent import QueryIntent
from kairix.core.search.pipeline import SearchPipeline, SearchResult
from tests.fakes import (
    FakeClassifier,
    FakeDocumentRepository,
    FakeEmbeddingService,
    FakeFactRecord,
    FakeFactStore,
    FakeFusion,
    FakeGraphRepository,
    FakeSearchLogger,
    FakeVectorRepository,
)

# ---------------------------------------------------------------------------
# Helper: build a test pipeline with sensible defaults
# ---------------------------------------------------------------------------


def _test_pipeline(**overrides) -> SearchPipeline:
    """Build a SearchPipeline with fake defaults. Override any component."""
    defaults = {
        "classifier": FakeClassifier(),
        "bm25": BM25SearchBackend(FakeDocumentRepository()),
        "vector": VectorSearchBackend(FakeEmbeddingService(), FakeVectorRepository()),
        "graph": FakeGraphRepository(available=True),
        "fusion": FakeFusion(),
        "boosts": [],
        "logger": FakeSearchLogger(),
        "config": RetrievalConfig.defaults(),
    }
    defaults.update(overrides)
    return SearchPipeline(**defaults)


# ---------------------------------------------------------------------------
# Basic pipeline tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_pipeline_returns_search_result():
    """SearchPipeline.search() returns a SearchResult."""
    pipeline = _test_pipeline()
    result = pipeline.search("test query")
    assert isinstance(result, SearchResult)


@pytest.mark.unit
def test_pipeline_classifies_intent():
    """Pipeline uses the classifier to determine intent."""
    pipeline = _test_pipeline(classifier=FakeClassifier(intent=QueryIntent.PROCEDURAL))
    result = pipeline.search("how to deploy")
    assert result.intent == QueryIntent.PROCEDURAL


@pytest.mark.unit
def test_pipeline_returns_bm25_results():
    """Pipeline returns BM25 results when documents match."""
    docs = [
        {
            "path": "deploy.md",
            "title": "Deploy Guide",
            "content": "how to deploy the app",
            "collection": "notes",
        },
    ]
    pipeline = _test_pipeline(
        bm25=BM25SearchBackend(FakeDocumentRepository(documents=docs)),
    )
    result = pipeline.search("deploy")
    assert result.bm25_count == 1


@pytest.mark.unit
def test_pipeline_returns_vector_results():
    """Pipeline returns vector results when vector repo has matches."""
    vec_results = [{"path": "semantic.md", "distance": 0.1, "collection": "c"}]
    pipeline = _test_pipeline(
        vector=VectorSearchBackend(FakeEmbeddingService(), FakeVectorRepository(results=vec_results)),
    )
    result = pipeline.search("semantic query")
    assert result.vec_count == 1


@pytest.mark.unit
def test_pipeline_fuses_both_sources():
    """Pipeline fuses BM25 and vector results."""
    docs = [
        {
            "path": "a.md",
            "title": "A",
            "content": "architecture patterns",
            "collection": "c",
        }
    ]
    vec_results = [{"path": "b.md", "distance": 0.1, "collection": "c"}]
    pipeline = _test_pipeline(
        bm25=BM25SearchBackend(FakeDocumentRepository(documents=docs)),
        vector=VectorSearchBackend(FakeEmbeddingService(), FakeVectorRepository(results=vec_results)),
    )
    result = pipeline.search("architecture")
    # FakeFusion concatenates: 1 from BM25 + 1 from vector = 2 fused
    assert result.fused_count == 2


@pytest.mark.unit
def test_pipeline_applies_boosts():
    """Pipeline applies each boost in the chain."""
    boost_calls = []

    class TrackingBoost:
        def boost(self, results, query, context):
            boost_calls.append(query)
            return results

    pipeline = _test_pipeline(boosts=[TrackingBoost(), TrackingBoost()])
    pipeline.search("test query")
    assert len(boost_calls) == 2
    assert all(q == "test query" for q in boost_calls)


@pytest.mark.unit
def test_pipeline_logs_search_event():
    """Pipeline logs a search event via SearchLogger."""
    fake_logger = FakeSearchLogger()
    pipeline = _test_pipeline(logger=fake_logger)
    pipeline.search("test query")
    assert len(fake_logger.events) == 1
    assert "query_hash" in fake_logger.events[0]
    assert "intent" in fake_logger.events[0]


@pytest.mark.unit
def test_pipeline_records_latency():
    """Pipeline records latency in the SearchResult."""
    pipeline = _test_pipeline()
    result = pipeline.search("latency test")
    assert result.latency_ms >= 0.0


# ---------------------------------------------------------------------------
# Entity intent — Neo4j required
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_pipeline_entity_intent_errors_when_graph_unavailable():
    """ENTITY intent returns error when graph is unavailable."""
    pipeline = _test_pipeline(
        classifier=FakeClassifier(intent=QueryIntent.ENTITY),
        graph=FakeGraphRepository(available=False),
    )
    result = pipeline.search("tell me about Acme Corp")
    assert result.intent == QueryIntent.ENTITY
    assert result.error != ""
    assert "Neo4j" in result.error
    assert result.results == []


@pytest.mark.unit
def test_pipeline_entity_intent_proceeds_when_graph_available():
    """ENTITY intent proceeds when graph is available."""
    pipeline = _test_pipeline(
        classifier=FakeClassifier(intent=QueryIntent.ENTITY),
        graph=FakeGraphRepository(available=True),
    )
    result = pipeline.search("tell me about Acme Corp")
    assert result.intent == QueryIntent.ENTITY
    assert result.error == ""


# ---------------------------------------------------------------------------
# Skip vector
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_pipeline_skip_vector_returns_bm25_only():
    """skip_vector=True in config means no vector search is run."""
    embed_calls = []

    class TrackingEmbedding:
        def embed(self, text):
            embed_calls.append(text)
            return [0.01] * 10

        def embed_batch(self, texts):
            return [[0.01] * 10 for _ in texts]

    cfg = RetrievalConfig(skip_vector=True)
    docs = [{"path": "a.md", "title": "A", "content": "match", "collection": "c"}]
    pipeline = _test_pipeline(
        bm25=BM25SearchBackend(FakeDocumentRepository(documents=docs)),
        vector=VectorSearchBackend(TrackingEmbedding(), FakeVectorRepository()),
        config=cfg,
    )
    result = pipeline.search("match")
    assert result.vec_count == 0
    assert result.vec_failed is False
    # embed should NOT have been called
    assert len(embed_calls) == 0


# ---------------------------------------------------------------------------
# Vector failure
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_pipeline_vec_failure_marks_vec_failed():
    """A genuine vector backend failure (raised exception) sets vec_failed=True.

    An empty result list is NOT a failure — it's a successful no-match —
    and must NOT trigger vec_failed (operator alerts would otherwise fire on
    every obscure query). See test_pipeline_contracts.py for the full
    distinction.
    """

    class _RaisingVectorRepo:
        def search(self, *_args, **_kwargs):
            raise RuntimeError("vector index corrupt")

        def search_with_filter(self, *_args, **_kwargs):
            raise RuntimeError("vector index corrupt")

    docs = [{"path": "a.md", "title": "A", "content": "match", "collection": "c"}]
    pipeline = _test_pipeline(
        bm25=BM25SearchBackend(FakeDocumentRepository(documents=docs)),
        vector=VectorSearchBackend(FakeEmbeddingService(), _RaisingVectorRepo()),
    )
    result = pipeline.search("match")
    assert result.vec_failed is True
    assert result.bm25_count == 1


# ---------------------------------------------------------------------------
# No logger
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_pipeline_works_without_logger():
    """Pipeline works when logger is None."""
    pipeline = _test_pipeline(logger=None)
    result = pipeline.search("test")
    assert isinstance(result, SearchResult)


# ---------------------------------------------------------------------------
# Boost failure resilience
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_pipeline_continues_when_boost_fails():
    """Pipeline continues when a boost raises an exception."""

    class FailingBoost:
        def boost(self, results, query, context):
            raise RuntimeError("boost failed")

    pipeline = _test_pipeline(boosts=[FailingBoost()])
    result = pipeline.search("test")
    assert isinstance(result, SearchResult)
    assert result.error == ""


# ---------------------------------------------------------------------------
# Collections pass-through
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_pipeline_passes_collections():
    """Pipeline passes collection filter to backends."""
    docs = [
        {"path": "a.md", "title": "A", "content": "match", "collection": "notes"},
        {"path": "b.md", "title": "B", "content": "match", "collection": "archive"},
    ]
    pipeline = _test_pipeline(
        bm25=BM25SearchBackend(FakeDocumentRepository(documents=docs)),
    )
    result = pipeline.search("match", collections=["notes"])
    assert result.bm25_count == 1
    assert result.collections == ["notes"]


# ---------------------------------------------------------------------------
# Stage timing — embed_http + vector_ann split (#282 follow-up)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_pipeline_records_embed_http_and_vector_ann_split():
    """``_dispatch_vector`` writes ``embed_http`` + ``vector_ann`` into stages.

    The ``vector`` stage owns 95%+ of every query's wall-clock on production
    workloads. Splitting it into the Azure embed HTTP call vs the local
    usearch ANN cost is the root-cause-analysis instrument the operator
    needs to tell tail-latency causes apart (#282 follow-up).

    Sabotage-proof: drop the ``timings=stages`` forwarding in
    ``_dispatch_vector`` and these new stage keys never appear in
    SearchResult.stage_latency_ms. The existing ``vector`` stage stays —
    so the sabotage only breaks the split, not the parent total.
    """
    vec_results = [{"path": "v.md", "distance": 0.1, "collection": "c"}]
    pipeline = _test_pipeline(
        vector=VectorSearchBackend(FakeEmbeddingService(), FakeVectorRepository(results=vec_results)),
    )
    result = pipeline.search("semantic query")
    assert "embed_http" in result.stage_latency_ms
    assert "vector_ann" in result.stage_latency_ms
    # Existing ``vector`` total stays — readers that consume it don't break.
    assert "vector" in result.stage_latency_ms


@pytest.mark.unit
def test_pipeline_embed_http_plus_vector_ann_approximates_vector_total():
    """The split adds up to (approximately) the parent ``vector`` total.

    Sabotage-proof: time the embed call but skip wrapping vector_ann
    with its own time.monotonic delta (return without setting it) and
    the sum below would diverge from ``vector`` by more than 2ms — the
    aggregator-induced gap would show as "missing time" in probe data.
    """
    vec_results = [{"path": "v.md", "distance": 0.1, "collection": "c"}]
    pipeline = _test_pipeline(
        vector=VectorSearchBackend(FakeEmbeddingService(), FakeVectorRepository(results=vec_results)),
    )
    result = pipeline.search("semantic query")
    split_sum = result.stage_latency_ms["embed_http"] + result.stage_latency_ms["vector_ann"]
    vector_total = result.stage_latency_ms["vector"]
    # 2ms tolerance — wraps measurement noise (rounding + outer-wrap overhead).
    assert abs(split_sum - vector_total) <= 2.0, (
        f"embed_http({result.stage_latency_ms['embed_http']}) + "
        f"vector_ann({result.stage_latency_ms['vector_ann']}) = {split_sum}; "
        f"expected ~{vector_total} (vector total)"
    )


@pytest.mark.unit
def test_pipeline_skip_vector_writes_no_embed_or_ann_stages():
    """When ``skip_vector=True`` the embed/ANN stages don't show up.

    No vector backend call → no embed_http, no vector_ann. The parent
    ``vector`` stage still appears (as a near-zero outer measurement)
    because ``_dispatch_backends`` always brackets the ``_dispatch_vector``
    call.

    Sabotage-proof: forget to skip the timing population in the
    skip-vector branch (i.e. write zeros into stages unconditionally) and
    the absence below flips to presence — operators reading "embed_http=0"
    would misread it as "embed is fast" when in fact embed never ran.
    """
    cfg = RetrievalConfig(skip_vector=True)
    pipeline = _test_pipeline(config=cfg)
    result = pipeline.search("semantic query")
    assert "embed_http" not in result.stage_latency_ms
    assert "vector_ann" not in result.stage_latency_ms
    assert "vector" in result.stage_latency_ms


@pytest.mark.unit
def test_vector_backend_search_writes_timings_when_dict_provided():
    """``VectorSearchBackend.search`` writes embed_http + vector_ann into ``timings``.

    This is the documented seam the pipeline uses to split the vector
    stage. Without this hook, the pipeline can't decompose ``vector``
    without reaching into the backend's private attributes.

    Sabotage-proof: drop the ``timings["embed_http"]`` / ``["vector_ann"]``
    assignments and the dict stays empty — the pipeline split silently
    breaks.
    """
    backend = VectorSearchBackend(FakeEmbeddingService(), FakeVectorRepository(results=[]))
    timings: dict[str, float] = {}
    backend.search("query text", timings=timings)
    assert "embed_http" in timings
    assert "vector_ann" in timings
    assert timings["embed_http"] >= 0.0
    assert timings["vector_ann"] >= 0.0


# ---------------------------------------------------------------------------
# Federation — Plan B-parity Capability #5
#
# Pipeline gains an optional fact_retriever. When None, today's
# chunk-only behaviour is preserved bit-for-bit; when wired, attribute-fact
# queries weight fact hits ~2x their chunk counterparts in fusion.
# ---------------------------------------------------------------------------


def _fact(
    fid: str,
    entity: str,
    attribute: str,
    value: str,
    *,
    namespace: str = "shared",
    superseded_by: str | None = None,
) -> FakeFactRecord:
    """Build a FakeFactRecord for federation tests."""
    return FakeFactRecord(
        id=fid,
        entity=entity,
        attribute=attribute,
        value=value,
        namespace=namespace,
        superseded_by=superseded_by,
    )


@pytest.mark.unit
def test_pipeline_without_fact_retriever_unchanged():
    """No fact_retriever wired → identical behaviour vs. today (regression gate).

    The federation stage must be a no-op when fact_retriever is None.
    fact_count stays 0; fused_count is unchanged from a pre-Cap5 build.

    Sabotage-proof: hard-code ``self.fact_retriever = FakeFactStore(...)``
    inside ``__post_init__`` and this assertion flips (fact_count > 0)
    even with the default constructor — vault-only deployments would
    silently start seeing fact contamination in chunk-only queries.
    """
    docs = [{"path": "a.md", "title": "A", "content": "match", "collection": "c"}]
    pipeline = _test_pipeline(
        bm25=BM25SearchBackend(FakeDocumentRepository(documents=docs)),
    )
    assert pipeline.fact_retriever is None
    result = pipeline.search("match")
    assert result.fact_count == 0
    # Chunk fusion still produced the BM25 row.
    assert result.bm25_count == 1


@pytest.mark.unit
def test_pipeline_dispatches_fact_retriever_when_wired():
    """A wired fact_retriever is consulted; fact_count surfaces the hit count.

    Sabotage-proof: drop the ``_dispatch_facts`` call in ``search`` and
    fact_count stays 0 even though the retriever has matching records.
    """
    store = FakeFactStore()
    store.add(_fact("f1", "acme", "address", "1 Pier Lane Sydney"))
    pipeline = _test_pipeline(fact_retriever=store)
    result = pipeline.search("acme address")
    assert result.fact_count == 1


@pytest.mark.unit
def test_pipeline_attribute_fact_intent_makes_facts_dominate():
    """ATTRIBUTE_FACT intent → fact hits dominate the fused top-K.

    Uses ``RRFFusion`` so chunk rows arrive at the federation stage as
    ``FusedResult`` dataclasses — matches the production fusion shape
    so the downstream budget stage actually emits content.

    Sabotage-proof: swap the ``(0.6, 0.3, 0.1)`` entry in
    ``_FUSION_WEIGHTS`` to ``(0.1, 0.6, 0.3)`` and the chunk row
    overtakes the fact row in fused order — pinning fact-dominates is
    the live intent contract.
    """
    docs = [{"path": "a.md", "title": "A", "content": "acme address", "collection": "c"}]
    store = FakeFactStore()
    store.add(_fact("f1", "acme", "address", "1 Pier Lane Sydney"))
    pipeline = _test_pipeline(
        classifier=FakeClassifier(intent=QueryIntent.ATTRIBUTE_FACT),
        bm25=BM25SearchBackend(FakeDocumentRepository(documents=docs)),
        fusion=RRFFusion(),
        fact_retriever=store,
    )
    result = pipeline.search("acme address")
    assert result.fact_count == 1
    # The top fused result is the fact (path prefix ``facts://``).
    assert result.results, "expected fused results"
    top = result.results[0]
    top_path = top.result.path if hasattr(top, "result") else getattr(top, "path", "")
    assert top_path.startswith("facts://")


@pytest.mark.unit
def test_pipeline_multi_hop_intent_chunk_dominates_fused_top():
    """MULTI_HOP intent → chunks lead the fused top-K (post-normalisation).

    The federation layer normalises chunk RRF scores and fact-store
    overlap scores to the same [0, 1] scale before applying the
    per-intent weights. Under MULTI_HOP (chunk_w=0.7, fact_w=0.2) the
    chunk side wins on weighted score; without normalisation raw fact
    overlap scores (1.0) overwhelmed raw RRF scores (~0.033) regardless
    of weight.

    Sabotage-proof: swap MULTI_HOP weights to (fact=0.7, chunk=0.2) and
    the fact leapfrogs the chunk under the same normalised scale —
    pinned by the top-1 assertion below.
    """
    docs = [{"path": "a.md", "title": "A", "content": "acme address billing", "collection": "c"}]
    vec_results = [{"path": "a.md", "distance": 0.1, "collection": "c"}]
    store = FakeFactStore()
    store.add(_fact("f1", "acme", "address", "1 Pier Lane Sydney"))
    pipeline = _test_pipeline(
        classifier=FakeClassifier(intent=QueryIntent.MULTI_HOP),
        bm25=BM25SearchBackend(FakeDocumentRepository(documents=docs)),
        vector=VectorSearchBackend(FakeEmbeddingService(), FakeVectorRepository(results=vec_results)),
        fusion=RRFFusion(),
        fact_retriever=store,
    )
    result = pipeline.search("acme address")
    assert result.fact_count == 1
    assert result.results, "expected fused results"
    top = result.results[0]
    top_path = top.result.path if hasattr(top, "result") else getattr(top, "path", "")
    assert not top_path.startswith("facts://"), f"Expected chunk to lead MULTI_HOP top-K, got {top_path}"


@pytest.mark.unit
def test_pipeline_threads_namespace_to_fact_retriever():
    """``namespace`` kwarg threads through to FactStore.search.

    The retriever filters by namespace, so when the caller scopes a
    query to ``engagement-alpha`` only that namespace's facts surface
    even if other namespaces hold matching records.

    Sabotage-proof: hard-code ``namespace=None`` in ``_dispatch_facts``
    and this test sees BOTH facts come back (the alpha + the bravo row),
    breaking the engagement-scoped recall guarantee.
    """
    store = FakeFactStore()
    store.add(_fact("a1", "acme", "address", "Sydney HQ", namespace="engagement-alpha"))
    store.add(_fact("b1", "acme", "address", "Melbourne HQ", namespace="engagement-bravo"))
    pipeline = _test_pipeline(fact_retriever=store)
    result = pipeline.search("acme address", namespace="engagement-alpha")
    assert result.fact_count == 1


@pytest.mark.unit
def test_pipeline_excludes_superseded_facts():
    """Superseded facts MUST NOT appear in the fused result.

    Federation must not undo FakeFactStore/SQLiteFactStore's superseded
    filter. The fact layer's whole consolidation contract relies on
    superseded rows staying invisible to default search.

    Sabotage-proof: add ``include_superseded=True`` (or remove the
    filter inside FakeFactStore.search) and the test flips to seeing
    the superseded row — papering over the consolidation guarantee.
    """
    store = FakeFactStore()
    store.add(_fact("old", "acme", "address", "Sydney"))
    store.add(_fact("new", "acme", "address", "Melbourne"))
    store.supersede(old_id="old", new_id="new")
    pipeline = _test_pipeline(fact_retriever=store)
    result = pipeline.search("acme address")
    # Only the live record surfaces — superseded record is invisible.
    assert result.fact_count == 1


class _RaisingFactStore:
    """FactStore that always raises — pins graceful degradation."""

    def add(self, fact):
        raise RuntimeError("not used in test")

    def search(self, query, *, top_k=10, namespace=None):
        raise RuntimeError("fact retriever offline")

    def find_conflicts(self, *, entity, attribute, namespace=None):
        raise RuntimeError("not used in test")

    def supersede(self, *, old_id, new_id):
        raise RuntimeError("not used in test")


@pytest.mark.unit
def test_pipeline_degrades_when_fact_retriever_raises():
    """Fact retriever raising → pipeline returns chunk-only results.

    Cap #5 contract: a broken fact layer must NOT poison the chunk
    pipeline. The query still completes; fact_count is 0; chunks flow
    through unaffected.

    Sabotage-proof: drop the ``try/except`` in ``_dispatch_facts`` and
    the search raises (or returns SearchResult(error=...)). The
    assertion below pins the graceful-degradation contract.
    """
    docs = [{"path": "a.md", "title": "A", "content": "match", "collection": "c"}]
    pipeline = _test_pipeline(
        bm25=BM25SearchBackend(FakeDocumentRepository(documents=docs)),
        fact_retriever=_RaisingFactStore(),
    )
    result = pipeline.search("match")
    assert result.fact_count == 0
    assert result.bm25_count == 1
    assert result.error == ""


@pytest.mark.unit
def test_pipeline_passes_top_k_facts_to_retriever():
    """The configured ``top_k_facts`` flows through to FactStore.search.

    Lossier records → roomier candidate pool. Wiring this knob through
    the retriever surface keeps the federation tuneable per deployment.

    Sabotage-proof: drop the ``top_k=self.top_k_facts`` kwarg in
    ``_dispatch_facts`` (so FactStore uses its default top_k=10) and
    the test below sees fact_count clamped at 10 instead of all 12.
    """
    store = FakeFactStore()
    # 12 facts whose values overlap with the query so all match.
    for i in range(12):
        store.add(_fact(f"f{i}", f"entity-{i}", "field", "match-token"))
    pipeline = _test_pipeline(fact_retriever=store)
    pipeline.top_k_facts = 12
    result = pipeline.search("match-token")
    assert result.fact_count == 12


class _EmptyFactStore:
    """FactStore that satisfies the Protocol but always returns no hits.

    Used by the dominance-sabotage test to prove that under
    ATTRIBUTE_FACT the dispatched fact path is what surfaces facts —
    with this store wired, the federation has nothing to push into
    fusion, so chunks must win on rank-fused score alone.
    """

    def add(self, fact):
        """Intentionally empty — sabotage store ignores writes."""

    def search(self, query, *, top_k=10, namespace=None):
        return []

    def find_conflicts(self, *, entity, attribute, namespace=None):
        return []

    def supersede(self, *, old_id, new_id):
        """Intentionally empty — sabotage store has no records to supersede."""


@pytest.mark.unit
def test_pipeline_attribute_fact_dominance_requires_non_empty_fact_hits():
    """When the FactStore returns no hits, ATTRIBUTE_FACT cannot dominate.

    Drives the public ``search`` surface (no private helper imports).
    Wires an empty-by-design FactStore; under ATTRIBUTE_FACT the
    pipeline still classifies + dispatches, but with zero hits the
    fusion stage degenerates to the chunk-only ordering (today's
    pre-Cap5 behaviour). This pins that fact dominance is gated by
    actual retrieval, not just intent.

    Sabotage-proof: hard-code ``return chunk_fused`` to skip the
    ``if not fact_hits`` early-return in ``_fuse_with_intent`` and
    inject a phantom fact into the merge regardless of dispatch
    output. The assertion below flips (top_path starts with facts://).
    """
    docs = [{"path": "a.md", "title": "A", "content": "acme address", "collection": "c"}]
    pipeline = _test_pipeline(
        classifier=FakeClassifier(intent=QueryIntent.ATTRIBUTE_FACT),
        bm25=BM25SearchBackend(FakeDocumentRepository(documents=docs)),
        fusion=RRFFusion(),
        fact_retriever=_EmptyFactStore(),
    )
    result = pipeline.search("acme address")
    assert result.fact_count == 0
    assert result.results, "expected fused results"
    top = result.results[0]
    top_path = top.result.path if hasattr(top, "result") else getattr(top, "path", "")
    assert not top_path.startswith("facts://")


# ---------------------------------------------------------------------------
# Reranker wiring (Issue 2 — closes the dead-code gap where
# kairix.core.search.rerank shipped + tested but was never called from
# the pipeline).
# ---------------------------------------------------------------------------


def _seq_reranker_call_log() -> list[tuple[str, int]]:
    """Build a (query, n_results) call log + a closure that records into it."""
    return []


def _make_recording_reranker(log: list[tuple[str, int]]):
    """Return a reranker callable that records each invocation in ``log``.

    The reranker re-orders the input by reversing it — so a test can confirm
    that (a) the reranker was actually called and (b) the pipeline's final
    result ordering reflects the rerank output, not the boost output.
    """

    def _record(query: str, fused: list) -> list:
        log.append((query, len(fused)))
        return list(reversed(fused))

    return _record


@pytest.mark.unit
def test_pipeline_rerank_skipped_when_reranker_is_none():
    """Default pipeline (reranker=None) skips the rerank stage entirely.

    Sabotage-proof: removing the ``if self.reranker is None`` guard from
    _maybe_rerank would cause a TypeError on the None call; this test
    confirms the guard exists.
    """
    pipeline = _test_pipeline()  # reranker defaults to None
    result = pipeline.search("any query")
    assert isinstance(result, SearchResult)
    # rerank stage still records 0ms latency — visible in stage_latency_ms
    assert "rerank" in result.stage_latency_ms


@pytest.mark.unit
def test_pipeline_rerank_skipped_when_intent_not_in_rerank_intents():
    """When config.rerank_intents=() (force-disabled), the reranker is not
    invoked even when wired.

    Locks the gating contract: just wiring a reranker shouldn't unilaterally
    enable it; config controls per-intent activation.
    """
    log: list[tuple[str, int]] = []
    config = RetrievalConfig(rerank_intents=())  # explicitly disable for all intents
    pipeline = _test_pipeline(
        reranker=_make_recording_reranker(log),
        config=config,
    )
    pipeline.search("any query")
    assert log == [], f"reranker should not have been called; got {log}"


@pytest.mark.unit
def test_pipeline_rerank_invoked_when_intent_matches_config_rerank_intents():
    """When config.rerank_intents includes the resolved intent, the reranker
    runs. Sabotage-proof: removing the rerank call from _maybe_rerank
    would leave log empty.
    """
    log: list[tuple[str, int]] = []
    config = RetrievalConfig(rerank_intents=("semantic",))
    pipeline = _test_pipeline(
        classifier=FakeClassifier(intent=QueryIntent.SEMANTIC),
        reranker=_make_recording_reranker(log),
        config=config,
    )
    pipeline.search("conceptual query")
    assert len(log) == 1, f"reranker should have been called exactly once; got {log}"
    recorded_query, _ = log[0]
    assert recorded_query == "conceptual query"


@pytest.mark.unit
def test_pipeline_rerank_invoked_when_force_enabled_regardless_of_intent():
    """config.rerank.enabled=True is the operator's force-enable knob.
    Should run rerank for every intent, regardless of rerank_intents."""
    from kairix.core.search.config import RerankConfig

    log: list[tuple[str, int]] = []
    config = RetrievalConfig(
        rerank=RerankConfig(enabled=True),
        rerank_intents=(),  # empty intent list — would normally skip
    )
    pipeline = _test_pipeline(
        classifier=FakeClassifier(intent=QueryIntent.KEYWORD),  # not in rerank_intents
        reranker=_make_recording_reranker(log),
        config=config,
    )
    pipeline.search("any query")
    assert len(log) == 1, f"force-enable should override rerank_intents gating; got {log}"


@pytest.mark.unit
def test_pipeline_rerank_exception_returns_pre_rerank_order_unchanged():
    """When the reranker raises, the pipeline must not propagate — the
    boost-order result returns unchanged. Locks the failure-isolation
    contract documented in _maybe_rerank's docstring."""

    def _exploding_reranker(query: str, fused: list) -> list:
        raise RuntimeError("rerank inference failed")

    config = RetrievalConfig(rerank_intents=("semantic",))
    pipeline = _test_pipeline(
        classifier=FakeClassifier(intent=QueryIntent.SEMANTIC),
        reranker=_exploding_reranker,
        config=config,
    )
    # Must not raise
    result = pipeline.search("anything")
    assert isinstance(result, SearchResult)


# ---------------------------------------------------------------------------
# Intent-confidence-gated boosts (Issue #456)
# ---------------------------------------------------------------------------


class _RecordingBoost:
    """Boost strategy that records whether it was invoked + reads the
    confidence value from the context dict for assertion."""

    def __init__(self) -> None:
        self.calls: list[tuple[QueryIntent, float | None]] = []

    def boost(self, results: list, query: str, context: dict) -> list:
        self.calls.append((context.get("intent"), context.get("intent_confidence")))
        return results


@pytest.mark.unit
def test_pipeline_threads_intent_confidence_into_boost_context():
    """Sabotage-proof: removing 'intent_confidence' from _apply_boosts'
    context dict makes this test fail with KeyError on the recording
    boost's read."""
    recorder = _RecordingBoost()
    pipeline = _test_pipeline(
        classifier=FakeClassifier(intent=QueryIntent.SEMANTIC, confidence=0.7),
        boosts=[recorder],
    )
    pipeline.search("test query")
    assert len(recorder.calls) == 1
    intent, confidence = recorder.calls[0]
    assert intent == QueryIntent.SEMANTIC
    assert confidence == 0.7


@pytest.mark.unit
def test_pipeline_legacy_fake_without_classify_with_confidence_defaults_to_1():
    """A classifier surface that only implements classify() (pre-#456
    fake) still works — pipeline defaults its confidence to 1.0 so
    legacy callers get unconditional boost firing."""

    class _LegacyClassifier:
        def classify(self, query: str) -> QueryIntent:
            return QueryIntent.SEMANTIC

    recorder = _RecordingBoost()
    pipeline = _test_pipeline(
        classifier=_LegacyClassifier(),
        boosts=[recorder],
    )
    pipeline.search("test query")
    assert len(recorder.calls) == 1
    _, confidence = recorder.calls[0]
    assert confidence == 1.0, (
        f"legacy classifier without classify_with_confidence should yield "
        f"confidence=1.0 (legacy unconditional fire); got {confidence}"
    )


@pytest.mark.unit
def test_intent_confidence_passes_skips_when_flag_on_and_confidence_low():
    """When the flag is ON and intent_confidence < min_confidence, the
    helper returns False — the boost will be skipped.

    Drives the gate via the flag_reader DI seam (F1/F2-clean — no
    monkey-patching of the resolver module).

    Sabotage-proof: removing `if not flag_reader(): return True` from
    intent_confidence_passes makes this test FAIL (function returns True
    on the legacy fall-through path) — confirming the flag-gate is
    load-bearing.
    """
    from kairix.core.search.boosts import intent_confidence_passes

    flag_on = lambda: True  # noqa: E731 — concise test fake
    context = {"intent": QueryIntent.TEMPORAL, "intent_confidence": 0.3}
    assert (
        intent_confidence_passes(
            context,
            QueryIntent.TEMPORAL,
            min_confidence=0.5,
            flag_reader=flag_on,
        )
        is False
    )


@pytest.mark.unit
def test_intent_confidence_passes_fires_when_flag_off_regardless_of_confidence():
    """Backwards-compat: with the flag OFF, intent_confidence is ignored
    entirely — the helper returns True on intent match alone (pre-#456
    binary behaviour preserved)."""
    from kairix.core.search.boosts import intent_confidence_passes

    flag_off = lambda: False  # noqa: E731
    # Confidence is impossibly low; min is impossibly high; flag OFF
    # → boost still fires because confidence is ignored.
    context = {"intent": QueryIntent.TEMPORAL, "intent_confidence": 0.0}
    assert (
        intent_confidence_passes(
            context,
            QueryIntent.TEMPORAL,
            min_confidence=0.99,
            flag_reader=flag_off,
        )
        is True
    )


@pytest.mark.unit
def test_intent_confidence_passes_fires_when_flag_on_and_confidence_high():
    """The user-facing happy path from #456: flag ON, confident
    classification, intent matches → boost fires."""
    from kairix.core.search.boosts import intent_confidence_passes

    flag_on = lambda: True  # noqa: E731
    context = {"intent": QueryIntent.TEMPORAL, "intent_confidence": 0.85}
    assert (
        intent_confidence_passes(
            context,
            QueryIntent.TEMPORAL,
            min_confidence=0.5,
            flag_reader=flag_on,
        )
        is True
    )


@pytest.mark.unit
def test_intent_confidence_passes_skips_when_intent_mismatch_regardless_of_flag():
    """Intent mismatch always short-circuits to False before the flag
    check — pre-#456 semantics for the legacy gate are preserved
    byte-for-byte."""
    from kairix.core.search.boosts import intent_confidence_passes

    for flag in (True, False):
        context = {"intent": QueryIntent.SEMANTIC, "intent_confidence": 1.0}
        # Expected TEMPORAL but context says SEMANTIC → False
        assert (
            intent_confidence_passes(
                context,
                QueryIntent.TEMPORAL,
                min_confidence=0.5,
                flag_reader=lambda f=flag: f,
            )
            is False
        )


@pytest.mark.unit
def test_intent_confidence_passes_legacy_default_confidence_is_one():
    """When the context doesn't carry intent_confidence (legacy callers
    that haven't been updated post-#456), the helper defaults to 1.0 so
    boosts fire normally (unconditional-fire backwards-compat)."""
    from kairix.core.search.boosts import intent_confidence_passes

    flag_on = lambda: True  # noqa: E731
    # No intent_confidence key in context; default should kick in
    context = {"intent": QueryIntent.TEMPORAL}
    assert (
        intent_confidence_passes(
            context,
            QueryIntent.TEMPORAL,
            min_confidence=0.5,
            flag_reader=flag_on,
        )
        is True
    )


# ---------------------------------------------------------------------------
# SourceTierBoost (Issue #432) — source-tier-aware ranking
# ---------------------------------------------------------------------------


def _fused(path: str, collection: str, rrf_score: float = 0.5):
    """Build a minimal FusedResult-shaped object for boost tests."""
    from kairix.core.search.rrf import FusedResult

    return FusedResult(
        path=path,
        collection=collection,
        title=f"T-{path}",
        snippet="s",
        rrf_score=rrf_score,
        boosted_score=rrf_score,
    )


@pytest.mark.unit
def test_source_tier_boost_disabled_is_noop():
    """When SourceTierBoostConfig.enabled is False (the default), the
    boost returns results with boosted_score unchanged — preserves
    pre-#432 ranking byte-for-byte.

    Sabotage-prove by removing the `if not self._config.enabled:` guard
    in SourceTierBoost.boost(): this test would then mutate scores
    even when the boost is disabled."""
    from kairix.core.search.boosts import SourceTierBoost
    from kairix.core.search.config import SourceTierBoostConfig

    boost = SourceTierBoost(
        tier_map={"vault-canon": "canonical"},
        config=SourceTierBoostConfig(enabled=False),
    )
    results = [_fused("/a.md", "vault-canon", rrf_score=0.5)]
    out = boost.boost(results, "q", {})
    assert out[0].boosted_score == 0.5


@pytest.mark.unit
def test_source_tier_boost_applies_canonical_multiplier():
    """A result whose collection maps to 'canonical' gets x3.0 boost."""
    from kairix.core.search.boosts import SourceTierBoost
    from kairix.core.search.config import SourceTierBoostConfig

    boost = SourceTierBoost(
        tier_map={"vault-canon": "canonical"},
        config=SourceTierBoostConfig(enabled=True),
    )
    results = [_fused("/ethos.md", "vault-canon", rrf_score=0.5)]
    out = boost.boost(results, "q", {})
    # canonical multiplier = 3.0
    assert out[0].boosted_score == 1.5


@pytest.mark.unit
def test_source_tier_boost_applies_reference_multiplier():
    """A result whose collection maps to 'reference' gets x0.6 boost."""
    from kairix.core.search.boosts import SourceTierBoost
    from kairix.core.search.config import SourceTierBoostConfig

    boost = SourceTierBoost(
        tier_map={"sharepoint": "reference"},
        config=SourceTierBoostConfig(enabled=True),
    )
    results = [_fused("/random.md", "sharepoint", rrf_score=1.0)]
    out = boost.boost(results, "q", {})
    # reference multiplier = 0.6
    assert out[0].boosted_score == pytest.approx(0.6)


@pytest.mark.unit
def test_source_tier_boost_archived_outranked_by_canonical():
    """The core user-facing claim of #432: a 'canonical' result outranks
    an 'archived' result with the same RRF score after the boost.

    Sabotage-prove: removing the `r.boosted_score *= multiplier` line
    leaves scores untouched and the boost is a no-op.
    """
    from kairix.core.search.boosts import SourceTierBoost
    from kairix.core.search.config import SourceTierBoostConfig

    boost = SourceTierBoost(
        tier_map={"vault-canon": "canonical", "vault-archive": "archived"},
        config=SourceTierBoostConfig(enabled=True),
    )
    canon = _fused("/ethos.md", "vault-canon", rrf_score=0.5)
    archive = _fused("/old.md", "vault-archive", rrf_score=0.5)
    results = [canon, archive]
    out = boost.boost(results, "q", {})
    # canonical x3.0 = 1.5; archived x0.2 = 0.1
    canon_out = next(r for r in out if r.path == "/ethos.md")
    archive_out = next(r for r in out if r.path == "/old.md")
    assert canon_out.boosted_score > archive_out.boosted_score


@pytest.mark.unit
def test_source_tier_boost_unconfigured_collection_falls_back_to_default_tier():
    """A result whose collection has no tier mapping falls back to the
    default tier (vault_active, x1.0) — preserves pre-#432 ranking
    byte-for-byte for any collection the operator hasn't classified."""
    from kairix.core.search.boosts import SourceTierBoost
    from kairix.core.search.config import SourceTierBoostConfig

    boost = SourceTierBoost(
        tier_map={"vault-canon": "canonical"},  # no mapping for 'other'
        config=SourceTierBoostConfig(enabled=True),
    )
    results = [_fused("/x.md", "other", rrf_score=0.5)]
    out = boost.boost(results, "q", {})
    # default_tier=vault_active multiplier = 1.0 → score unchanged
    assert out[0].boosted_score == 0.5


@pytest.mark.unit
def test_source_tier_boost_unknown_tier_name_falls_back_safely():
    """A tier_map entry with an unknown tier name (typo / future tier
    value) falls back to default_tier — does not crash + does not
    silently zero the score."""
    from kairix.core.search.boosts import SourceTierBoost
    from kairix.core.search.config import SourceTierBoostConfig

    boost = SourceTierBoost(
        tier_map={"weird": "blueprint"},  # not a SourceTier value
        config=SourceTierBoostConfig(enabled=True),
    )
    results = [_fused("/x.md", "weird", rrf_score=0.5)]
    out = boost.boost(results, "q", {})
    # Falls back to default_tier=vault_active (x1.0)
    assert out[0].boosted_score == 0.5


# ---------------------------------------------------------------------------
# Undated-chunk penalty for temporal queries (Issue #430)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_chunk_date_boost_no_undated_penalty_when_disabled():
    """Default (undated_chunk_penalty_enabled=False) preserves pre-#430
    behaviour — undated chunks pass through unchanged."""
    import datetime

    from kairix.core.search.config import TemporalBoostConfig
    from kairix.core.search.rrf import chunk_date_boost

    cfg = TemporalBoostConfig(
        chunk_date_boost_enabled=True,
        undated_chunk_penalty_enabled=False,
    )
    dated = _fused("/d.md", "c", rrf_score=0.5)
    dated.chunk_date = "2026-06-01"
    undated = _fused("/u.md", "c", rrf_score=0.5)
    out = chunk_date_boost([dated, undated], datetime.date(2026, 6, 1), config=cfg)
    # Find undated in output — it should still have boosted_score=0.5
    out_by_path = {r.path: r for r in out}
    assert out_by_path["/u.md"].boosted_score == 0.5


@pytest.mark.unit
def test_chunk_date_boost_undated_penalty_demotes_undated_chunks():
    """When undated_chunk_penalty_enabled=True and the query has a date,
    undated chunks get multiplied by the penalty (default 0.1).

    Sabotage-prove: removing the penalty loop in _chunk_date_boost_impl
    fails this test — undated.boosted_score stays at 0.5 rather than 0.05."""
    import datetime

    from kairix.core.search.config import TemporalBoostConfig
    from kairix.core.search.rrf import chunk_date_boost

    cfg = TemporalBoostConfig(
        chunk_date_boost_enabled=True,
        undated_chunk_penalty_enabled=True,
        undated_chunk_penalty=0.1,
    )
    dated = _fused("/d.md", "c", rrf_score=0.5)
    dated.chunk_date = "2026-06-01"
    undated = _fused("/u.md", "c", rrf_score=0.5)
    out = chunk_date_boost([dated, undated], datetime.date(2026, 6, 1), config=cfg)
    out_by_path = {r.path: r for r in out}
    # undated should be ~0.05 (0.5 * 0.1)
    assert out_by_path["/u.md"].boosted_score == pytest.approx(0.05)


@pytest.mark.unit
def test_chunk_date_boost_dated_outranks_undated_after_penalty():
    """The user-facing claim of #430: a dated chunk ranks above an
    undated chunk after the penalty applies — closes the reproduction
    where SharePoint reference fragments topped May/June agent-memory."""
    import datetime

    from kairix.core.search.config import TemporalBoostConfig
    from kairix.core.search.rrf import chunk_date_boost

    cfg = TemporalBoostConfig(
        chunk_date_boost_enabled=True,
        undated_chunk_penalty_enabled=True,
    )
    dated = _fused("/agent-memory.md", "vault-canon", rrf_score=0.5)
    dated.chunk_date = "2026-06-01"
    undated = _fused("/sharepoint-svg.md", "sharepoint", rrf_score=0.5)
    out = chunk_date_boost([dated, undated], datetime.date(2026, 6, 1), config=cfg)
    assert out[0].path == "/agent-memory.md"
    assert out[1].path == "/sharepoint-svg.md"


@pytest.mark.unit
def test_chunk_date_boost_no_penalty_when_every_chunk_is_undated():
    """Defensive: when EVERY candidate is undated, the penalty must not
    fire (it would penalise every result equally, producing nothing
    useful). The boost is a no-op in this degenerate case."""
    import datetime

    from kairix.core.search.config import TemporalBoostConfig
    from kairix.core.search.rrf import chunk_date_boost

    cfg = TemporalBoostConfig(
        chunk_date_boost_enabled=True,
        undated_chunk_penalty_enabled=True,
    )
    r1 = _fused("/a.md", "c", rrf_score=0.5)
    r2 = _fused("/b.md", "c", rrf_score=0.7)
    out = chunk_date_boost([r1, r2], datetime.date(2026, 6, 1), config=cfg)
    # Scores unchanged; original order by rrf_score preserved
    by_path = {r.path: r.boosted_score for r in out}
    assert by_path["/a.md"] == 0.5
    assert by_path["/b.md"] == 0.7


# ---------------------------------------------------------------------------
# ContentQualityBoost (Issue #458) — enrichment-derived content authority
# ---------------------------------------------------------------------------


def _fused_with_body(
    path: str,
    snippet: str,
    chunk_date: str = "",
    rrf_score: float = 0.5,
):
    """Build a FusedResult with a real snippet body for content-quality
    signal tests."""
    from kairix.core.search.rrf import FusedResult

    return FusedResult(
        path=path,
        collection="c",
        title=f"T-{path}",
        snippet=snippet,
        rrf_score=rrf_score,
        boosted_score=rrf_score,
        chunk_date=chunk_date,
    )


@pytest.mark.unit
def test_content_quality_boost_disabled_is_noop():
    """When ``ContentQualityBoostConfig.enabled`` is False (default),
    boost returns results with ``boosted_score`` unchanged — preserves
    pre-#458 ranking byte-for-byte.

    Sabotage-prove by removing the ``if not self._config.enabled:``
    guard: this test would then mutate scores even when disabled.
    """
    from kairix.core.search.boosts import ContentQualityBoost
    from kairix.core.search.config import ContentQualityBoostConfig

    boost = ContentQualityBoost(config=ContentQualityBoostConfig(enabled=False))
    results = [_fused_with_body("/a.md", "x" * 800, rrf_score=0.5)]
    out = boost.boost(results, "q", {})
    assert out[0].boosted_score == 0.5


@pytest.mark.unit
def test_content_quality_length_signal_substantive_beats_stub():
    """The core claim: a long substantive snippet outranks a stub when
    both have the same RRF score.

    Sabotage-prove: replacing ``length_signal(len(snippet), ...)`` with
    a constant ``1.0`` in ``ContentQualityBoost.boost`` collapses the
    distinction and the assertion would fail.
    """
    from kairix.core.search.boosts import ContentQualityBoost
    from kairix.core.search.config import ContentQualityBoostConfig

    boost = ContentQualityBoost(config=ContentQualityBoostConfig(enabled=True))
    stub = _fused_with_body("/stub.md", "tiny", rrf_score=0.5)
    body = _fused_with_body("/body.md", "x" * 1500, rrf_score=0.5)
    out = boost.boost([stub, body], "q", {})
    by_path = {r.path: r.boosted_score for r in out}
    assert by_path["/body.md"] > by_path["/stub.md"]


@pytest.mark.unit
def test_content_quality_length_signal_bounded():
    """``length_signal`` stays in
    ``[length_stub_floor, length_substantive_ceiling]`` for all inputs —
    no signal can zero a score or unbounded-multiply it.
    """
    from kairix.core.search.boosts import length_signal
    from kairix.core.search.config import ContentQualityBoostConfig

    cfg = ContentQualityBoostConfig()
    for n in (0, 1, 50, 200, 300, 600, 1500, 100_000):
        s = length_signal(n, cfg)
        assert cfg.length_stub_floor <= s <= cfg.length_substantive_ceiling


@pytest.mark.unit
def test_content_quality_structure_signal_counts_headings():
    """Markdown headings drive the structure signal upward; a snippet
    with several ``##`` lines outranks an equivalent-length stream of
    prose.

    Sabotage-prove: hard-coding ``structure_m = 1.0`` in
    ``ContentQualityBoost.boost`` removes the gap and the assertion fails.
    """
    from kairix.core.search.boosts import ContentQualityBoost
    from kairix.core.search.config import ContentQualityBoostConfig

    cfg = ContentQualityBoostConfig(enabled=True)
    boost = ContentQualityBoost(config=cfg)
    prose = "word " * 200
    structured = (
        "# Title\n\n## Section 1\n\nbody\n\n## Section 2\n\nbody\n\n## Section 3\n\nbody\n\n### Subsection\n\nbody\n"
    )
    # Pad structured to similar length so we isolate the heading signal
    structured_padded = structured + ("word " * 200)
    a = _fused_with_body("/prose.md", prose, rrf_score=0.5)
    b = _fused_with_body("/structured.md", structured_padded, rrf_score=0.5)
    out = boost.boost([a, b], "q", {})
    by_path = {r.path: r.boosted_score for r in out}
    assert by_path["/structured.md"] > by_path["/prose.md"]


@pytest.mark.unit
def test_content_quality_structure_signal_zero_headings_is_neutral():
    """A snippet with no headings produces ``structure_signal == 1.0``
    (neutral). Locks the contract that operators who write prose-only
    notes aren't penalised — they just don't get the boost."""
    from kairix.core.search.boosts import structure_signal
    from kairix.core.search.config import ContentQualityBoostConfig

    assert structure_signal(0, ContentQualityBoostConfig()) == 1.0


@pytest.mark.unit
def test_content_quality_structure_signal_bounded():
    """``structure_signal`` stays in ``[1.0, structure_ceiling]`` for all
    inputs — heavily structured docs cap out instead of runaway-boosting."""
    from kairix.core.search.boosts import structure_signal
    from kairix.core.search.config import ContentQualityBoostConfig

    cfg = ContentQualityBoostConfig()
    for n in (0, 1, 3, 5, 8, 100, 10_000):
        s = structure_signal(n, cfg)
        assert 1.0 <= s <= cfg.structure_ceiling


@pytest.mark.unit
def test_content_quality_recency_signal_neutral_when_chunk_date_missing():
    """Empty ``chunk_date`` → ``recency_neutral`` (1.0) — we don't
    penalise just for missing the metadata. That's intent-gated
    #430's job."""
    from kairix.core.search.boosts import recency_signal
    from kairix.core.search.config import ContentQualityBoostConfig

    cfg = ContentQualityBoostConfig()
    assert recency_signal("", cfg) == cfg.recency_neutral
    assert recency_signal("not-a-date", cfg) == cfg.recency_neutral


@pytest.mark.unit
def test_content_quality_recency_signal_bounded():
    """``recency_signal`` stays in ``[recency_floor, recency_neutral]``
    for all inputs — no negative scores and no over-boosting."""
    from kairix.core.search.boosts import recency_signal
    from kairix.core.search.config import ContentQualityBoostConfig

    cfg = ContentQualityBoostConfig()
    for d in ("2026-06-01", "2024-01-01", "2020-01-01", "1900-01-01"):
        s = recency_signal(d, cfg)
        assert cfg.recency_floor <= s <= cfg.recency_neutral


@pytest.mark.unit
def test_content_quality_recency_signal_recent_beats_old():
    """A recent ``chunk_date`` produces a higher signal than a very old
    one — drives stale content down the ranking even when length and
    structure are equal."""
    from kairix.core.search.boosts import recency_signal
    from kairix.core.search.config import ContentQualityBoostConfig

    cfg = ContentQualityBoostConfig()
    recent = recency_signal("2026-06-01", cfg)
    old = recency_signal("2010-01-01", cfg)
    assert recent > old


@pytest.mark.unit
def test_content_quality_signals_compose_multiplicatively():
    """The combined multiplier is the product of the three signals —
    locks the contract that no single signal dominates (a great length
    score can't bury a terrible recency score)."""
    from kairix.core.search.boosts import (
        ContentQualityBoost,
        length_signal,
        recency_signal,
        structure_signal,
    )
    from kairix.core.search.config import ContentQualityBoostConfig

    cfg = ContentQualityBoostConfig(enabled=True)
    boost = ContentQualityBoost(config=cfg)

    snippet = "# H1\n\n" + ("word " * 200)
    expected_heading_count = 1  # exactly one ``#``-starting line
    r = _fused_with_body("/a.md", snippet, chunk_date="2026-06-01", rrf_score=0.5)
    out = boost.boost([r], "q", {})

    expected = (
        0.5
        * length_signal(len(snippet), cfg)
        * structure_signal(expected_heading_count, cfg)
        * recency_signal("2026-06-01", cfg)
    )
    assert out[0].boosted_score == pytest.approx(expected)


@pytest.mark.unit
def test_content_quality_boost_failure_isolated_per_result(caplog):
    """A single broken result (e.g. missing ``boosted_score``) doesn't
    abort the whole list; the rest still get boosted. Locks the contract
    that an odd row never crashes the search path."""
    import logging

    from kairix.core.search.boosts import ContentQualityBoost
    from kairix.core.search.config import ContentQualityBoostConfig

    class _BrokenResult:
        path = "/broken.md"
        snippet = "x" * 600
        chunk_date = "2026-06-01"

        # boosted_score raises on access
        @property
        def boosted_score(self):
            raise RuntimeError("simulated broken result")

    boost = ContentQualityBoost(config=ContentQualityBoostConfig(enabled=True))
    good = _fused_with_body("/good.md", "x" * 600, rrf_score=0.5)
    with caplog.at_level(logging.WARNING):
        out = boost.boost([_BrokenResult(), good], "q", {})
    # good result still got boosted (it had a substantial snippet)
    assert out[1].boosted_score != 0.5
    assert any("ContentQualityBoost" in r.getMessage() for r in caplog.records)

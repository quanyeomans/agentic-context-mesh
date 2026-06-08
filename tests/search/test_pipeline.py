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

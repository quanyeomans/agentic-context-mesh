"""Integration tests: BM25SearchBackend and VectorSearchBackend wired into SearchPipeline.

These tests verify the full backend → fake-repo → SearchPipeline → SearchResult flow
with no @patch / no monkeypatching. Each fake satisfies the relevant Protocol from
``kairix.core.protocols``:

  - ``FakeDocumentRepository`` -> ``DocumentRepository`` (used by ``BM25SearchBackend``)
  - ``FakeEmbeddingService``   -> ``EmbeddingService``   (used by ``VectorSearchBackend``)
  - ``FakeVectorRepository``   -> ``VectorRepository``   (used by ``VectorSearchBackend``)

Contract tests (in tests/contracts/test_protocols.py) cover the adapter → fake-repo
delegation in isolation. These integration tests exercise the broader composition:
backends embedded in a real ``SearchPipeline`` (with real ``RRFFusion`` and real
``apply_budget``) returning a populated ``SearchResult``, including collection scoping,
fallback behaviour, and end-to-end fusion. ``SearchResult.results`` is a list of
``BudgetedResult`` (each wrapping a ``FusedResult``).
"""

from __future__ import annotations

import pytest

from kairix.core.factory import (
    QUERY_CACHE_DISABLED,
    RERANK_DISABLED,
    FactoryDeps,
    build_search_pipeline,
)
from kairix.core.protocols import (
    DocumentRepository,
    EmbeddingService,
    VectorRepository,
)
from kairix.core.search.backends import BM25SearchBackend, VectorSearchBackend
from kairix.core.search.budget import BudgetedResult
from kairix.core.search.config import RetrievalConfig
from kairix.core.search.fusion import RRFFusion
from kairix.core.search.intent import QueryIntent
from kairix.core.search.pipeline import SearchResult
from tests.fakes import (
    FakeClassifier,
    FakeCollectionResolver,
    FakeDocumentRepository,
    FakeEmbeddingService,
    FakeGraphRepository,
    FakePaths,
    FakeSearchLogger,
    FakeVectorRepository,
)

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Test data builders
# ---------------------------------------------------------------------------


def _bm25_doc(
    *,
    path: str,
    title: str,
    content: str,
    collection: str,
) -> dict:
    """Build a document compatible with FakeDocumentRepository.search_fts AND
    with the rrf() fusion contract (which reads ``file`` / ``title`` /
    ``snippet`` / ``collection``)."""
    return {
        "path": path,
        "file": path,  # rrf reads "file"
        "title": title,
        "content": content,
        "snippet": content[:120],  # rrf reads "snippet"
        "collection": collection,
    }


def _vec_hit(
    *,
    path: str,
    distance: float,
    collection: str,
    title: str = "Vector hit",
    snippet: str = "Snippet from vector search.",
) -> dict:
    """Build a vector-search result row in the shape rrf() consumes."""
    return {
        "path": path,
        "distance": distance,
        "collection": collection,
        "title": title,
        "snippet": snippet,
    }


def _result_paths(result: SearchResult) -> list[str]:
    """Extract paths from a SearchResult.results list of BudgetedResult."""
    paths: list[str] = []
    for r in result.results:
        # Each entry is a BudgetedResult(result=FusedResult(path=..., ...), ...).
        assert isinstance(r, BudgetedResult), f"unexpected result type {type(r)!r}"
        paths.append(r.result.path)
    return paths


def _build_pipeline(
    *,
    doc_repo: FakeDocumentRepository,
    embed: FakeEmbeddingService,
    vec_repo: FakeVectorRepository,
    intent: QueryIntent = QueryIntent.SEMANTIC,
    logger: FakeSearchLogger | None = None,
    graph_available: bool = False,
    reranker_override: object = RERANK_DISABLED,
):
    """Compose a SearchPipeline through the factory with protocol-compliant fakes.

    F47-clean: the factory wraps ``doc_repo`` in ``BM25SearchBackend`` and
    ``(embed, vec_repo)`` in ``VectorSearchBackend`` exactly as production
    does — the test wires the fakes at the same boundary the
    SQLite/Azure/usearch implementations sit on.

    Rerank seam: ``reranker_override`` defaults to ``RERANK_DISABLED`` so
    these backend-composition tests never pull the production
    cross-encoder closure — the ~5s ``sentence-transformers``/torch import
    that every SEMANTIC-intent search would otherwise trigger via
    ``pipeline._maybe_rerank`` (``RetrievalConfig.defaults().rerank_intents``
    includes ``"semantic"``). None of these tests assert the reranked
    order, so disabling rerank is behaviour-preserving for their
    assertions. A test that wants to prove the seam is exercised passes a
    tracking no-op closure instead.
    """
    return build_search_pipeline(
        config=RetrievalConfig.defaults(),
        paths=FakePaths(),
        deps=FactoryDeps(
            classifier_override=FakeClassifier(intent=intent),
            doc_repo_override=doc_repo,
            embed_service_override=embed,
            vec_repo_override=vec_repo,
            graph_override=FakeGraphRepository(available=graph_available),
            fusion_override=RRFFusion(k=60),
            boosts_override=[],
            logger_override=logger,
            resolver_override=FakeCollectionResolver(),
            query_cache_override=QUERY_CACHE_DISABLED,
            reranker_override=reranker_override,
        ),
    )


# ---------------------------------------------------------------------------
# BM25SearchBackend wired into a real SearchPipeline
# ---------------------------------------------------------------------------


class TestBM25BackendInPipeline:
    """BM25SearchBackend (wrapping FakeDocumentRepository) drives BM25 leg of pipeline."""

    @pytest.mark.integration
    def test_bm25_backend_satisfies_protocol(self) -> None:
        """FakeDocumentRepository under BM25SearchBackend satisfies DocumentRepository."""
        repo = FakeDocumentRepository()
        assert isinstance(repo, DocumentRepository)
        backend = BM25SearchBackend(repo)
        assert hasattr(backend, "search")

    @pytest.mark.integration
    def test_bm25_backend_returns_pipeline_results(self) -> None:
        """A query reaching the BM25 backend produces results in SearchResult."""
        docs = [
            _bm25_doc(
                path="vault/architecture.md",
                title="Architecture",
                content="kairix architecture patterns and protocols",
                collection="shared",
            ),
            _bm25_doc(
                path="vault/runbook.md",
                title="Runbook",
                content="operational runbook for restart",
                collection="shared",
            ),
            _bm25_doc(
                path="vault/unrelated.md",
                title="Unrelated",
                content="cooking recipes",
                collection="shared",
            ),
        ]
        repo = FakeDocumentRepository(documents=docs)
        pipeline = _build_pipeline(
            doc_repo=repo,
            embed=FakeEmbeddingService(),
            vec_repo=FakeVectorRepository(),
            intent=QueryIntent.KEYWORD,
            logger=FakeSearchLogger(),
        )

        result = pipeline.search("architecture")

        assert isinstance(result, SearchResult)
        assert result.bm25_count == 1, f"BM25 should match exactly one doc; got {result.bm25_count}"
        # The matched doc must propagate all the way through fusion + budget.
        assert _result_paths(result) == ["vault/architecture.md"]

    @pytest.mark.integration
    def test_bm25_backend_respects_collection_filter(self) -> None:
        """The pipeline forwards the resolved collections list to BM25SearchBackend."""
        docs = [
            _bm25_doc(path="a.md", title="A", content="match", collection="alpha"),
            _bm25_doc(path="b.md", title="B", content="match", collection="beta"),
        ]
        pipeline = _build_pipeline(
            doc_repo=FakeDocumentRepository(documents=docs),
            embed=FakeEmbeddingService(),
            vec_repo=FakeVectorRepository(),
            intent=QueryIntent.KEYWORD,
        )

        result = pipeline.search("match", collections=["alpha"])

        assert result.bm25_count == 1
        # Only the alpha-collection doc must surface — proves collection arg
        # actually flowed BM25SearchBackend → FakeDocumentRepository.
        assert _result_paths(result) == ["a.md"]


# ---------------------------------------------------------------------------
# VectorSearchBackend wired into a real SearchPipeline
# ---------------------------------------------------------------------------


class TestVectorBackendInPipeline:
    """VectorSearchBackend (wrapping FakeEmbedding + FakeVectorRepository) drives vector leg."""

    @pytest.mark.integration
    def test_vector_backend_components_satisfy_protocols(self) -> None:
        """Composition pieces satisfy EmbeddingService and VectorRepository."""
        emb = FakeEmbeddingService()
        repo = FakeVectorRepository()
        assert isinstance(emb, EmbeddingService)
        assert isinstance(repo, VectorRepository)
        backend = VectorSearchBackend(emb, repo)
        assert hasattr(backend, "search")

    @pytest.mark.integration
    def test_vector_backend_returns_pipeline_results(self) -> None:
        """Vector backend results flow through the pipeline to SearchResult."""
        vec_results = [
            _vec_hit(path="sem-a.md", distance=0.1, collection="shared"),
            _vec_hit(path="sem-b.md", distance=0.2, collection="shared"),
        ]
        pipeline = _build_pipeline(
            doc_repo=FakeDocumentRepository(),
            embed=FakeEmbeddingService(),
            vec_repo=FakeVectorRepository(results=vec_results),
            intent=QueryIntent.SEMANTIC,
        )

        result = pipeline.search("semantic search query")

        assert result.vec_count == 2
        # Both vector hits surface (different paths, no BM25 hits to dedupe with).
        assert sorted(_result_paths(result)) == ["sem-a.md", "sem-b.md"]
        # vec_failed should be False — vector returned non-empty
        assert result.vec_failed is False

    @pytest.mark.integration
    def test_vector_backend_empty_embedding_short_circuits(self) -> None:
        """An EmbeddingService that returns [] yields vec_count=0 and no exception.

        Uses ``FakeEmbeddingService(vector=[])`` from tests/fakes.py — the
        canonical way to exercise the empty-embedding branch of
        ``VectorSearchBackend.search``.
        """
        empty_embedding = FakeEmbeddingService(vector=[])
        # Sanity: protocol-compliant.
        assert isinstance(empty_embedding, EmbeddingService)
        assert empty_embedding.embed("anything") == []

        pipeline = _build_pipeline(
            doc_repo=FakeDocumentRepository(),
            embed=empty_embedding,
            vec_repo=FakeVectorRepository(results=[_vec_hit(path="never.md", distance=0.1, collection="c")]),
            intent=QueryIntent.SEMANTIC,
        )

        result = pipeline.search("query")

        assert result.vec_count == 0
        assert result.vec_failed is True
        # The "never.md" vector hit must NOT appear — embedding bailed.
        assert "never.md" not in _result_paths(result)
        assert _result_paths(result) == []

    @pytest.mark.integration
    def test_vector_backend_respects_collection_filter(self) -> None:
        """Pipeline forwards collections through VectorSearchBackend.search."""
        vec_results = [
            _vec_hit(path="a.md", distance=0.1, collection="alpha"),
            _vec_hit(path="b.md", distance=0.2, collection="beta"),
        ]
        pipeline = _build_pipeline(
            doc_repo=FakeDocumentRepository(),
            embed=FakeEmbeddingService(),
            vec_repo=FakeVectorRepository(results=vec_results),
            intent=QueryIntent.SEMANTIC,
        )

        result = pipeline.search("query", collections=["beta"])

        assert result.vec_count == 1
        assert _result_paths(result) == ["b.md"]

    @pytest.mark.integration
    def test_injected_reranker_seam_is_invoked_on_semantic_intent(self) -> None:
        """The factory's ``reranker_override`` seam reaches the pipeline rerank stage.

        Proves the seam is wired end-to-end: a SEMANTIC-intent search with
        ``RetrievalConfig.defaults()`` (whose ``rerank_intents`` includes
        ``"semantic"``) passes through ``pipeline._maybe_rerank``, which
        invokes the *injected* closure rather than the production
        cross-encoder. This is what lets the SEMANTIC tests skip the ~5s
        torch import: the seam is the live code path, not a bypass.
        """
        calls: list[tuple[str, int]] = []

        def tracking_noop_reranker(query: str, fused: list) -> list:
            calls.append((query, len(fused)))
            return fused

        vec_results = [_vec_hit(path="sem.md", distance=0.1, collection="shared")]
        pipeline = _build_pipeline(
            doc_repo=FakeDocumentRepository(),
            embed=FakeEmbeddingService(),
            vec_repo=FakeVectorRepository(results=vec_results),
            intent=QueryIntent.SEMANTIC,
            reranker_override=tracking_noop_reranker,
        )

        result = pipeline.search("semantic query")

        # The injected reranker was called exactly once for the SEMANTIC
        # query, and the result still surfaces (no-op returns input order).
        assert len(calls) == 1
        assert calls[0][0] == "semantic query"
        assert _result_paths(result) == ["sem.md"]

    @pytest.mark.integration
    def test_disabled_reranker_seam_skips_rerank_stage(self) -> None:
        """``RERANK_DISABLED`` wires ``reranker=None`` so the rerank stage is a no-op.

        This is the default the SEMANTIC tests use to avoid the torch
        import. With ``reranker=None``, ``pipeline._maybe_rerank``
        short-circuits before any ``sentence-transformers`` import, and the
        pipeline still returns the fused results unchanged.
        """
        vec_results = [
            _vec_hit(path="sem-a.md", distance=0.1, collection="shared"),
            _vec_hit(path="sem-b.md", distance=0.2, collection="shared"),
        ]
        pipeline = _build_pipeline(
            doc_repo=FakeDocumentRepository(),
            embed=FakeEmbeddingService(),
            vec_repo=FakeVectorRepository(results=vec_results),
            intent=QueryIntent.SEMANTIC,
            reranker_override=RERANK_DISABLED,
        )
        # The pipeline carries no reranker — the rerank stage cannot fire.
        assert pipeline.reranker is None

        result = pipeline.search("semantic query")

        assert result.vec_count == 2
        assert sorted(_result_paths(result)) == ["sem-a.md", "sem-b.md"]

    @staticmethod
    def _build_production_reranker_pipeline(config: RetrievalConfig):
        """Build a pipeline through the *production* reranker path (no override).

        Every component except the reranker is a fake so the only live
        wiring under test is the factory's config-driven choice of whether
        to build the cross-encoder closure. ``reranker_override`` is left
        unset, so ``cfg`` alone decides ``pipeline.reranker``.
        """
        return build_search_pipeline(
            config=config,
            paths=FakePaths(),
            deps=FactoryDeps(
                classifier_override=FakeClassifier(intent=QueryIntent.SEMANTIC),
                doc_repo_override=FakeDocumentRepository(),
                embed_service_override=FakeEmbeddingService(),
                vec_repo_override=FakeVectorRepository(
                    results=[_vec_hit(path="x.md", distance=0.1, collection="shared")]
                ),
                graph_override=FakeGraphRepository(available=False),
                fusion_override=RRFFusion(k=60),
                boosts_override=[],
                logger_override=FakeSearchLogger(),
                resolver_override=FakeCollectionResolver(),
                query_cache_override=QUERY_CACHE_DISABLED,
                # No reranker_override — the production default path runs.
            ),
        )

    @pytest.mark.integration
    def test_rerank_fully_disabled_in_config_wires_no_reranker(self) -> None:
        """A config with rerank disabled AND no rerank_intents wires ``reranker=None``.

        Exercises the *production default* path (no ``reranker_override``):
        when ``cfg.rerank.enabled`` is False AND ``cfg.rerank_intents`` is
        empty, the factory skips building the cross-encoder closure
        entirely — no ``sentence-transformers`` import even with production
        wiring. Proves the operator-config "rerank off" branch.
        """
        # rerank.enabled defaults to False; rerank_intents=() — BOTH operands
        # of ``not enabled and not intents`` are True → rerank disabled.
        pipeline = self._build_production_reranker_pipeline(RetrievalConfig(rerank_intents=()))

        assert pipeline.reranker is None

        result = pipeline.search("semantic query")
        assert _result_paths(result) == ["x.md"]

    @pytest.mark.integration
    def test_rerank_built_when_intents_registered_even_if_not_force_enabled(self) -> None:
        """Non-empty ``rerank_intents`` builds the closure even with ``enabled=False``.

        Pins the ``and`` in ``not enabled AND not intents``: with
        ``rerank.enabled`` False but ``rerank_intents`` non-empty (the
        ``RetrievalConfig.defaults()`` shape), the closure MUST be built so
        per-intent rerank can fire. An ``or`` here would wrongly disable
        rerank for the default config — this assertion kills that mutant.
        """
        # enabled=False, intents non-empty → ``not False and not (...)`` =
        # ``True and False`` = False → NOT disabled → closure built.
        config = RetrievalConfig(rerank_intents=("semantic",))
        assert config.rerank.enabled is False  # guards the precondition

        pipeline = self._build_production_reranker_pipeline(config)

        assert pipeline.reranker is not None


# ---------------------------------------------------------------------------
# Both backends together — exercises full hybrid path
# ---------------------------------------------------------------------------


class TestBothBackendsInPipeline:
    """Both backends populated — verifies hybrid composition end-to-end."""

    @pytest.mark.integration
    def test_hybrid_backends_produce_combined_results(self) -> None:
        docs = [
            _bm25_doc(
                path="bm25-only.md",
                title="BM25 Hit",
                content="literal keyword match",
                collection="shared",
            ),
        ]
        vec_results = [
            _vec_hit(path="vec-only.md", distance=0.1, collection="shared"),
        ]
        pipeline = _build_pipeline(
            doc_repo=FakeDocumentRepository(documents=docs),
            embed=FakeEmbeddingService(),
            vec_repo=FakeVectorRepository(results=vec_results),
            intent=QueryIntent.SEMANTIC,
            logger=FakeSearchLogger(),
        )

        result = pipeline.search("literal")

        assert result.bm25_count == 1
        assert result.vec_count == 1
        # RRFFusion merges into a single FusedResult per path; both should
        # appear because the docs do not share a path.
        assert sorted(_result_paths(result)) == ["bm25-only.md", "vec-only.md"]

    @pytest.mark.integration
    def test_pipeline_logger_records_backend_counts(self) -> None:
        """The injected SearchLogger receives the bm25_count / vec_count
        produced by the backends — proves the integration logged backend output."""
        docs = [
            _bm25_doc(
                path="logged.md",
                title="Logged",
                content="payload",
                collection="shared",
            ),
        ]
        vec_results = [_vec_hit(path="logged-vec.md", distance=0.1, collection="shared")]
        log = FakeSearchLogger()
        pipeline = _build_pipeline(
            doc_repo=FakeDocumentRepository(documents=docs),
            embed=FakeEmbeddingService(),
            vec_repo=FakeVectorRepository(results=vec_results),
            intent=QueryIntent.SEMANTIC,
            logger=log,
        )

        pipeline.search("payload")

        assert len(log.events) == 1
        event = log.events[0]
        assert event["bm25_count"] == 1
        assert event["vec_count"] == 1

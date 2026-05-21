"""Federation end-to-end tests — Plan B-parity Capability #5.

Wires a SearchPipeline with a real :class:`SQLiteFactStore` (in-memory
SQLite) and exercises the federation contract from the public ``search``
surface. Sister to the unit tests in ``tests/search/test_pipeline.py``
which use ``FakeFactStore``; this file proves the production store also
satisfies the federation surface.

Coverage matrix:

* ATTRIBUTE_FACT intent → fact retriever hit appears in fused top-K
* SEMANTIC (balanced) intent → chunk and fact both contribute, no
  intent-weighted dominance
* Backwards-compat: pipeline without fact_retriever ignores facts
  entirely (a regression-pinned safety net for vault-only deployments).
"""

from __future__ import annotations

import pytest

from kairix.core.facts import SQLiteFactStore, StoredFactRecord
from kairix.core.search.backends import BM25SearchBackend, VectorSearchBackend
from kairix.core.search.config import RetrievalConfig
from kairix.core.search.fusion import RRFFusion
from kairix.core.search.intent import QueryIntent
from kairix.core.search.pipeline import SearchPipeline
from tests.fakes import (
    FakeClassifier,
    FakeDocumentRepository,
    FakeEmbeddingService,
    FakeGraphRepository,
    FakeSearchLogger,
    FakeVectorRepository,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_pipeline(
    *,
    intent: QueryIntent,
    fact_retriever: SQLiteFactStore | None,
    documents: list[dict] | None = None,
) -> SearchPipeline:
    """Build a SearchPipeline tailored for federation integration tests."""
    docs = documents or []
    return SearchPipeline(
        classifier=FakeClassifier(intent=intent),
        bm25=BM25SearchBackend(FakeDocumentRepository(documents=docs)),
        vector=VectorSearchBackend(FakeEmbeddingService(), FakeVectorRepository()),
        graph=FakeGraphRepository(available=True),
        fusion=RRFFusion(),
        boosts=[],
        logger=FakeSearchLogger(),
        config=RetrievalConfig.defaults(),
        fact_retriever=fact_retriever,
    )


def _mint(
    *,
    entity: str,
    attribute: str,
    value: str,
    turn: str,
    namespace: str,
    extracted_at: str,
    confidence: float = 0.9,
) -> StoredFactRecord:
    """Build a StoredFactRecord whose id is derived deterministically."""
    fid = StoredFactRecord.mint_id(entity=entity, attribute=attribute, source_turn_ids=(turn,))
    return StoredFactRecord(
        id=fid,
        entity=entity,
        attribute=attribute,
        value=value,
        confidence=confidence,
        source_turn_ids=(turn,),
        extracted_at=extracted_at,
        superseded_by=None,
        namespace=namespace,
    )


def _seed_facts(store: SQLiteFactStore) -> None:
    """Add three canonical engagement-scoped facts to ``store``."""
    facts = [
        _mint(
            entity="acme",
            attribute="headquarters",
            value="1 Pier Lane Sydney",
            turn="turn-1",
            namespace="engagement-alpha",
            extracted_at="2026-05-19T10:00:00Z",
            confidence=0.95,
        ),
        _mint(
            entity="acme",
            attribute="contact",
            value="ops@acme.example",
            turn="turn-2",
            namespace="engagement-alpha",
            extracted_at="2026-05-19T10:01:00Z",
        ),
        _mint(
            entity="jordan",
            attribute="role",
            value="Head of Delivery",
            turn="turn-3",
            namespace="engagement-alpha",
            extracted_at="2026-05-19T10:02:00Z",
            confidence=0.92,
        ),
    ]
    for f in facts:
        store.add(f)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_attribute_fact_query_surfaces_matching_fact(tmp_path) -> None:
    """ATTRIBUTE_FACT query against real SQLiteFactStore surfaces the matching fact.

    Sabotage-proof: drop the ``fact_retriever=store`` wiring in the
    pipeline construction and ``result.fact_count`` flips to 0 — the
    matching fact silently disappears. Same outcome if the federation
    dispatch (``_dispatch_facts``) is short-circuited.
    """
    db_path = tmp_path / "facts.sqlite"
    store = SQLiteFactStore(db_path=db_path)
    _seed_facts(store)

    pipeline = _build_pipeline(intent=QueryIntent.ATTRIBUTE_FACT, fact_retriever=store)
    result = pipeline.search("acme headquarters", namespace="engagement-alpha")
    assert result.fact_count >= 1
    # The fused top-K must include at least one fact (path prefix facts://).
    fact_in_results = any(
        (r.result.path if hasattr(r, "result") else getattr(r, "path", "")).startswith("facts://")
        for r in result.results
    )
    assert fact_in_results, "Expected at least one fact in fused top-K"


@pytest.mark.integration
def test_semantic_query_balanced_fusion(tmp_path) -> None:
    """SEMANTIC intent → balanced fusion across chunks + facts.

    Both layers contribute; neither dominates by intent design (default
    weight tuple). Stale-by-intent confirms the federation isn't gated
    behind ATTRIBUTE_FACT only — vault-style semantic recall still
    benefits when facts happen to overlap.

    Sabotage-proof: hard-code the SEMANTIC branch to skip fact dispatch
    and ``result.fact_count`` flips to 0 — vault-only recall loses the
    fact-shaped contribution that today still helps semantic queries.
    """
    db_path = tmp_path / "facts.sqlite"
    store = SQLiteFactStore(db_path=db_path)
    _seed_facts(store)

    docs = [
        {
            "path": "delivery.md",
            "title": "Delivery Lead",
            "content": "jordan role notes",
            "collection": "notes",
        },
    ]
    pipeline = _build_pipeline(
        intent=QueryIntent.SEMANTIC,
        fact_retriever=store,
        documents=docs,
    )
    result = pipeline.search("jordan role", namespace="engagement-alpha")
    # Fact retriever fired and produced at least one hit.
    assert result.fact_count >= 1
    # Chunk side fired and produced at least one BM25 hit.
    assert result.bm25_count >= 1


@pytest.mark.integration
def test_backcompat_no_fact_retriever_ignores_facts(tmp_path) -> None:
    """Pipeline with no fact_retriever ignores facts entirely (regression).

    Vault-only deployments must not change behaviour when Cap #5 ships.
    Even with a fact store standing nearby in memory, a pipeline that
    doesn't accept it as a constructor arg must return fact_count=0.

    Sabotage-proof: hard-code ``self.fact_retriever = SQLiteFactStore(...)``
    in ``SearchPipeline.__post_init__`` and the assertion below would
    flip to fact_count > 0 — silently changing vault recall behaviour
    for every existing deployment.
    """
    db_path = tmp_path / "facts.sqlite"
    standalone_store = SQLiteFactStore(db_path=db_path)
    _seed_facts(standalone_store)

    pipeline = _build_pipeline(intent=QueryIntent.ATTRIBUTE_FACT, fact_retriever=None)
    result = pipeline.search("acme headquarters", namespace="engagement-alpha")
    assert result.fact_count == 0
    # The standalone store still has the data; it just wasn't wired into
    # the pipeline.
    assert len(standalone_store.search("acme headquarters", namespace="engagement-alpha")) >= 1

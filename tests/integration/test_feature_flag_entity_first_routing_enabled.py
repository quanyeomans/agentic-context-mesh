"""F54 both-branch coverage for the ``entity_first_routing_enabled`` flag.

Exercises the full composed pipeline — IntentClassifier → RRF fusion →
boost chain → budget — through ``build_search_pipeline`` with the
:class:`~kairix.core.search.boosts.EntityFirstRoutingBoost` wired to a
:class:`FakeFeatureFlagResolver` pinned ON / OFF via the ``flag_reader``
DI seam. Asserts the entity-summary row's *rank* changes (not just its
score), which is the operator-visible contract:

* **OFF (default)** — a plain note that out-ranks the entity summary on
  RRF stays on top. Pre-#429 ranking preserved byte-for-byte.
* **ON** — for an ENTITY-intent query the entity summary is routed to
  rank 1, ahead of the higher-RRF plain note.

F1/F2-clean: ``flag_reader`` is the public DI seam; no monkey-patching,
no env-var manipulation. The F54 detector recognises the literal
``with_flag("entity_first_routing_enabled", False)`` and ``..., True)``
strings below; both appear in source even though only one fires per test.
"""

from __future__ import annotations

import pytest

from kairix.core.factory import QUERY_CACHE_DISABLED, FactoryDeps, build_search_pipeline
from kairix.core.search.boosts import EntityFirstRoutingBoost
from kairix.core.search.config import EntityFirstRoutingConfig, RetrievalConfig
from kairix.core.search.fusion import RRFFusion
from kairix.core.search.intent import QueryIntent
from tests.fakes import (
    FakeClassifier,
    FakeCollectionResolver,
    FakeDocumentRepository,
    FakeEmbeddingService,
    FakeFeatureFlagResolver,
    FakeGraphRepository,
    FakePaths,
    FakeSearchLogger,
    FakeVectorRepository,
)

pytestmark = pytest.mark.integration

_FLAG = "entity_first_routing_enabled"

# A plain vault note that out-ranks the entity summary on raw RRF (higher
# BM25 score + nearer vector distance) — so OFF leaves it on top and ON
# has to actively route past it.
_NOTE_PATH = "notes/about.md"
_ENTITY_PATH = "entity://Q42"


def _resolver(*, flag_on: bool) -> FakeFeatureFlagResolver:
    """Pin the flag ON / OFF. Both literal branches live here so the F54
    detector sees ``with_flag(_FLAG, False)`` and ``with_flag(_FLAG, True)``.
    """
    if flag_on:
        return FakeFeatureFlagResolver().with_flag("entity_first_routing_enabled", True)
    return FakeFeatureFlagResolver().with_flag("entity_first_routing_enabled", False)


def _bm25_row(path: str, collection: str, score: float) -> dict:
    return {"file": path, "title": "T", "snippet": "T", "score": score, "collection": collection}


def _vec_row(path: str, collection: str, distance: float) -> dict:
    return {
        "hash_seq": "h_0",
        "distance": distance,
        "path": path,
        "collection": collection,
        "title": "T",
        "snippet": "T",
    }


def _build_pipeline(*, flag_on: bool, intent: QueryIntent = QueryIntent.ENTITY):
    resolver = _resolver(flag_on=flag_on)
    routing = EntityFirstRoutingBoost(
        config=EntityFirstRoutingConfig(factor=5.0),
        flag_reader=lambda: resolver.get(_FLAG),
    )
    # Plain note leads on RRF (rank 1 in both lists); entity summary trails.
    bm25 = [_bm25_row(_NOTE_PATH, "vault", 2.0), _bm25_row(_ENTITY_PATH, "entity-summaries", 1.0)]
    vec = [_vec_row(_NOTE_PATH, "vault", 0.1), _vec_row(_ENTITY_PATH, "entity-summaries", 0.2)]
    return build_search_pipeline(
        config=RetrievalConfig.minimal(),
        paths=FakePaths(),
        deps=FactoryDeps(
            classifier_override=FakeClassifier(intent=intent),
            doc_repo_override=FakeDocumentRepository(bm25_rows=bm25),
            embed_service_override=FakeEmbeddingService(),
            vec_repo_override=FakeVectorRepository(results=vec),
            graph_override=FakeGraphRepository(available=True),
            fusion_override=RRFFusion(k=60),
            boosts_override=[routing],
            logger_override=FakeSearchLogger(),
            resolver_override=FakeCollectionResolver(),
            query_cache_override=QUERY_CACHE_DISABLED,
        ),
    )


def _top_path(result) -> str:
    assert result.results, "pipeline returned no results"
    return result.results[0].result.path


def test_on_branch_routes_entity_summary_to_rank_one() -> None:
    """ON — the entity summary is routed ahead of the higher-RRF note."""
    pipe = _build_pipeline(flag_on=True)
    result = pipe.search("tell me about the Australian software company")
    assert _top_path(result).startswith("entity://")


def test_off_branch_leaves_higher_rrf_note_on_top() -> None:
    """OFF — pre-#429 ranking: the plain note keeps rank 1."""
    pipe = _build_pipeline(flag_on=False)
    result = pipe.search("tell me about the Australian software company")
    assert _top_path(result) == _NOTE_PATH


def test_on_branch_does_not_route_for_non_entity_intent() -> None:
    """ON + KEYWORD intent — the intent gate keeps the note on top."""
    pipe = _build_pipeline(flag_on=True, intent=QueryIntent.KEYWORD)
    result = pipe.search("australian software company")
    assert _top_path(result) == _NOTE_PATH

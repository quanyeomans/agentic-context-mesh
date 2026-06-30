"""Integration: the canonical ``source_uri`` breadcrumb threads end-to-end
through the composed search pipeline (PLA-274).

Composed via ``kairix.core.factory.build_search_pipeline`` with canonical
fakes (F47) — query in → BM25Result → FusedResult → BudgetedResult →
``SearchHit.source_ref()`` out. Proves the structural fix: a connector
document whose ``documents.source_uri`` differs from its (synthetic,
sometimes-munged) ``documents.path`` surfaces the CANONICAL source_uri as
the breadcrumb, while ``path`` is kept for display.

Sabotage anchor (executed — see test docstrings): removing the
``source_uri`` field from ``FusedResult`` (kairix/core/search/rrf.py) makes
``test_connector_source_uri_threads_through_to_search_hit`` fail on the
``source_ref().source_uri`` assertion (the breadcrumb falls back to the
munged path), and the F97 gate's real-repo leg also fails. Restored.
"""

from __future__ import annotations

from typing import Any

import pytest

from kairix.core.factory import QUERY_CACHE_DISABLED, FactoryDeps, build_search_pipeline
from kairix.core.health import HealthDeps
from kairix.core.search.config import RetrievalConfig
from kairix.core.search.fusion import RRFFusion
from kairix.core.search.intent import QueryIntent
from kairix.use_cases.search import SearchDeps, run_search
from tests.fakes import (
    FakeClassifier,
    FakeCollectionResolver,
    FakeDocumentRepository,
    FakeEmbeddingService,
    FakeGraphRepository,
    FakePaths,
    FakeVectorRepository,
)

pytestmark = pytest.mark.integration

# A connector document whose canonical source_uri differs from its synthetic
# chunk-key path — the exact case the breadcrumb contract exists for. The
# archive-chunk path carries the internal ``#<seq>`` suffix; the resolvable
# pointer is the connector source_uri.
_CONNECTOR_URI = "sharepoint://acme-site/handbook.zip"
_CONNECTOR_PATH = "archive/handbook.zip#1536"
# A vault document with NO connector URI (source_uri NULL) — the breadcrumb
# must fall back to the path so it is still resolvable.
_VAULT_PATH = "notes/onboarding.md"


def _corpus() -> list[dict[str, Any]]:
    return [
        {
            "path": _CONNECTOR_PATH,
            "file": _CONNECTOR_PATH,
            "source_uri": _CONNECTOR_URI,
            "collection": "shared",
            "title": "Acme Handbook",
            "snippet": "Deployment runbook extracted from the handbook archive.",
            "content": "deployment runbook deploy procedure",
            "score": 0.9,
        },
        {
            "path": _VAULT_PATH,
            "file": _VAULT_PATH,
            # No source_uri — passthrough vault note (column NULL).
            "collection": "shared",
            "title": "Onboarding",
            "snippet": "Deployment notes for new hires.",
            "content": "deployment onboarding deploy notes",
            "score": 0.7,
        },
    ]


def _build_pipeline(docs: list[dict[str, Any]]) -> Any:
    return build_search_pipeline(
        config=RetrievalConfig.defaults(),
        paths=FakePaths(),
        deps=FactoryDeps(
            classifier_override=FakeClassifier(intent=QueryIntent.SEMANTIC),
            doc_repo_override=FakeDocumentRepository(documents=docs),
            embed_service_override=FakeEmbeddingService(dim=8),
            vec_repo_override=FakeVectorRepository(results=[]),
            graph_override=FakeGraphRepository(available=True),
            fusion_override=RRFFusion(k=60),
            boosts_override=[],
            resolver_override=FakeCollectionResolver(),
            query_cache_override=QUERY_CACHE_DISABLED,
        ),
    )


def _hermetic_health_deps() -> HealthDeps:
    return HealthDeps(
        secrets_loaded_fn=lambda: True,
        embed_backend_available_fn=lambda: True,
        bm25_index_available_fn=lambda: True,
        neo4j_available_fn=lambda: True,
    )


def test_connector_source_uri_threads_through_fusion_to_fused_result() -> None:
    """The composed pipeline carries ``source_uri`` from the document row
    into the ``FusedResult``, distinct from the munged display path.

    Sabotage-proof (executed): deleted the ``source_uri`` field from
    ``FusedResult`` — the ``fr.source_uri == _CONNECTOR_URI`` assertion
    fired (AttributeError → empty). Restored.
    """
    pipeline = _build_pipeline(_corpus())
    result = pipeline.search("deployment runbook")

    fused = {b.result.path: b.result for b in result.results}
    assert _CONNECTOR_PATH in fused, f"connector chunk missing from results: {list(fused)}"
    connector_fr = fused[_CONNECTOR_PATH]
    # The canonical breadcrumb survived fusion, distinct from the path.
    assert connector_fr.source_uri == _CONNECTOR_URI
    assert connector_fr.path == _CONNECTOR_PATH


def test_connector_source_uri_threads_through_to_search_hit() -> None:
    """End-to-end (F47): run_search over the composed pipeline yields a
    ``SearchHit`` whose ``source_ref()`` resolves to the connector URI, not
    the synthetic chunk-key path.

    Sabotage-proof (executed): removed ``source_uri`` from ``FusedResult`` —
    ``hit.source_ref().source_uri`` fell back to ``_CONNECTOR_PATH`` and this
    assertion fired. Restored.
    """
    pipeline = _build_pipeline(_corpus())
    deps = SearchDeps(
        search_fn=pipeline.search,
        classify_fn=lambda _q: QueryIntent.SEMANTIC,
        entity_card_fn=lambda _n: None,
        health_deps=_hermetic_health_deps(),
    )
    out = run_search("deployment runbook", deps=deps, include_entity_card=False)

    by_path = {h.path: h for h in out.results}
    connector_hit = by_path[_CONNECTOR_PATH]
    ref = connector_hit.source_ref()
    # The breadcrumb an agent cites/re-opens is the canonical connector URI.
    assert connector_hit.source_uri == _CONNECTOR_URI
    assert ref.source_uri == _CONNECTOR_URI
    # ``path`` is kept for display — the synthetic archive chunk key.
    assert ref.path == _CONNECTOR_PATH
    # The envelope surfaces the breadcrumb keys (mirror SourceRef).
    from kairix.use_cases.search import search_output_to_envelope

    env_hit = {h["path"]: h for h in search_output_to_envelope(out)["results"]}[_CONNECTOR_PATH]
    assert env_hit["source_uri"] == _CONNECTOR_URI


def test_vault_document_source_uri_falls_back_to_path() -> None:
    """A passthrough vault note (no connector URI) still yields a resolvable
    breadcrumb — ``source_ref().source_uri`` falls back to the path.

    Sabotage-proof (executed): changed ``SourceRef.of`` to default
    source_uri to ``""`` instead of ``path`` — this assertion fired with
    ``"" != notes/onboarding.md``. Restored.
    """
    pipeline = _build_pipeline(_corpus())
    deps = SearchDeps(
        search_fn=pipeline.search,
        classify_fn=lambda _q: QueryIntent.SEMANTIC,
        entity_card_fn=lambda _n: None,
        health_deps=_hermetic_health_deps(),
    )
    # ``deployment`` matches both docs' content in the substring-keyed fake.
    out = run_search("deployment", deps=deps, include_entity_card=False)

    by_path = {h.path: h for h in out.results}
    vault_hit = by_path[_VAULT_PATH]
    # No connector URI on the row → source_uri is "" on the hit …
    assert vault_hit.source_uri == ""
    # … but the SourceRef still resolves to the path (never empty).
    assert vault_hit.source_ref().source_uri == _VAULT_PATH

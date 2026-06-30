"""Integration: tiered-context (L0/L1/L2) wired through the composed pipeline (PLA-270).

Builds a real ``SearchPipeline`` via ``kairix.core.factory.build_search_pipeline``
(F47) with a ``FakeSummaryLoader`` injected through ``FactoryDeps`` and asserts
that an agent's ``max_tier`` request flows from ``pipeline.search`` into the
budget stage:

* a ``max_tier="L0"`` request clamps a would-be-L2 row down to its abstract,
* the default ``max_tier="L2"`` serves the full snippet (loader not consulted),
* with NO loader wired (production default) every row stays L2 — default-safe.

No monkeypatch / @patch / inline stubs: all doubles are canonical fakes from
``tests/fakes.py``.
"""

from __future__ import annotations

import pytest

from kairix.core.factory import QUERY_CACHE_DISABLED, FactoryDeps, build_search_pipeline
from kairix.core.search.config import RetrievalConfig
from kairix.core.search.fusion import BM25PrimaryFusion
from tests.fakes import (
    FakeCollectionResolver,
    FakeDocumentRepository,
    FakeEmbeddingService,
    FakeGraphRepository,
    FakePaths,
    FakeSearchLogger,
    FakeSummaryLoader,
    FakeVectorRepository,
    RealClassifierAdapter,
)

pytestmark = pytest.mark.integration

_DOC_PATH = "reports/quarterly-revenue.md"
_QUERY = "quarterly revenue summary"


def _doc() -> dict:
    return {
        "path": _DOC_PATH,
        "file": _DOC_PATH,
        "title": "Quarterly Revenue",
        "content": "quarterly revenue summary full snippet body",
        "snippet": "quarterly revenue summary full snippet body",
        "collection": "reports",
        "score": 1.0,
    }


def _build(summary_loader: FakeSummaryLoader | None):
    """Construct a pipeline whose single BM25-primary hit scores 1.0 (would be L2)."""
    return build_search_pipeline(
        config=RetrievalConfig.minimal(),
        paths=FakePaths(),
        deps=FactoryDeps(
            classifier_override=RealClassifierAdapter(),
            doc_repo_override=FakeDocumentRepository(documents=[_doc()]),
            embed_service_override=FakeEmbeddingService(),
            vec_repo_override=FakeVectorRepository(results=[]),
            graph_override=FakeGraphRepository(available=True),
            fusion_override=BM25PrimaryFusion(),
            logger_override=FakeSearchLogger(),
            resolver_override=FakeCollectionResolver(),
            query_cache_override=QUERY_CACHE_DISABLED,
            summary_loader_override=summary_loader,
        ),
    )


def _first(out) -> tuple[str, str]:
    """Return (tier, content) of the first budgeted result."""
    budgeted = out.results[0]
    return budgeted.tier, budgeted.content


def test_max_tier_l0_request_clamps_a_high_score_row_to_its_abstract() -> None:
    loader = FakeSummaryLoader(l0_by_path={_DOC_PATH: "the L0 abstract"})
    out = _build(loader).search(_QUERY, budget=10_000, max_tier="L0")
    assert out.results, "the seeded doc must be retrieved"
    tier, content = _first(out)
    assert tier == "L0"
    assert content == "the L0 abstract"
    assert out.tiers_used == ["L0"]


def test_default_max_tier_l2_serves_full_snippet_without_consulting_loader() -> None:
    loader = FakeSummaryLoader(l0_by_path={_DOC_PATH: "the L0 abstract"})
    out = _build(loader).search(_QUERY, budget=10_000)  # default max_tier="L2"
    tier, content = _first(out)
    assert tier == "L2"
    assert content == "quarterly revenue summary full snippet body"
    # An L2 row returns the snippet directly — the loader is never queried.
    assert loader.l0_calls == []


def test_no_summary_loader_keeps_every_row_l2_even_when_l0_requested() -> None:
    """Default-safe: with no loader wired, a max_tier=L0 request can't demote below L2."""
    out = _build(summary_loader=None).search(_QUERY, budget=10_000, max_tier="L0")
    tier, _content = _first(out)
    assert tier == "L2"
    assert out.tiers_used == ["L2"]

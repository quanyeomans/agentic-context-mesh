"""End-to-end composed path for the capability recommender (F48).

Exercises the full composed production code path of Spec A:

  build_capability_corpus(real SQLite + FTS5)   # Feeder 1 → capabilities
    → factory.build_search_pipeline(paths=…, rerank force-on)
    → run_recommend(task, deps=RecommendDeps(search_fn=pipeline.search, …))
    → assertion that the top recommendation matches a seeded capability
      AND carries a non-empty ready-to-call invocation

This is the recommender's sibling to ``test_composed_production_path.py``.
It runs the genuinely composed path — the real corpus writer, the real
factory-built ``SearchPipeline`` (BM25 leg over the seeded ``capabilities``
collection), and the real ``run_recommend`` mapping — not fakes hiding
composition seams. The provider/vector leg is skipped (``skip_vector=True``
with a null embed service) so the test is provider-free; the BM25 leg is
the real lexical match against the corpus the builder wrote.

F48 contract: file carries ``@pytest.mark.e2e``, runs in CI Stage 4.5 under
``pytest -m e2e``, and exercises real composition end-to-end.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from kairix.core.db.schema import create_schema
from kairix.core.factory import (
    FactoryDeps,
    build_search_pipeline,
    reset_search_pipeline_cache,
)
from kairix.knowledge.capabilities.builder import (
    CapabilityCatalogueBuilder,
    CapabilityCorpusDeps,
    build_capability_corpus,
)
from kairix.use_cases.recommend import RecommendDeps, recommender_config, run_recommend

pytestmark = pytest.mark.e2e


class _NullEmbed:
    """Provider-free embed service for the skip_vector BM25-only E2E path."""

    def embed(self, text: str) -> list[float]:
        del text
        return []

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return [[] for _ in texts]


def _seeded_caps() -> list[dict[str, object]]:
    return [
        {
            "name": "contradict",
            "mcp_tool": "contradict",
            "cli": "kairix contradict",
            "category": "synthesis",
            "when_to_use": "Check new content against existing knowledge for conflicts.",
        },
        {
            "name": "timeline",
            "mcp_tool": "timeline",
            "cli": "kairix timeline",
            "category": "retrieval",
            "when_to_use": "Trace how a topic changed over time, in date order.",
        },
    ]


def test_composed_recommender_path_ranks_seeded_capability(tmp_path: Path) -> None:
    """config → build_capability_corpus → build_search_pipeline → run_recommend.

    The top recommendation matches a seeded capability and carries a
    non-empty ready-to-call invocation — the Spec A acceptance path.

    Sabotage anchor: write nothing in ``build_capability_corpus`` (e.g.
    ``writer.upsert([])``) → the BM25 leg finds no capability and the
    ``recommendations`` tuple is empty, failing the assertions.
    """
    db_path = tmp_path / "index.sqlite"
    db = sqlite3.connect(db_path)
    create_schema(db)
    corpus_deps = CapabilityCorpusDeps(
        builder=CapabilityCatalogueBuilder(catalogue_fn=_seeded_caps, now_fn=lambda: "2026-06-20T00:00:00+00:00"),
        embed_batch_fn=lambda texts: [],  # BM25-only
    )
    result = build_capability_corpus(db, deps=corpus_deps)
    db.commit()
    db.close()
    assert result.written == 2, f"corpus build must write both caps: {result}"
    assert result.error == ""

    reset_search_pipeline_cache()
    from kairix.core.search.config import RetrievalConfig
    from kairix.paths import KairixPaths

    from dataclasses import replace

    cfg = replace(recommender_config(RetrievalConfig.defaults()), skip_vector=True)
    paths = KairixPaths(
        db_path=db_path,
        document_root=tmp_path,
        log_dir=tmp_path,
        workspace_root=tmp_path,
    )
    pipeline = build_search_pipeline(
        config=cfg,
        paths=paths,
        deps=FactoryDeps(embed_service_override=_NullEmbed()),
    )

    deps = RecommendDeps(
        search_fn=lambda *, query, collections, agent, **_kw: pipeline.search(
            query=query, collections=collections, agent=agent
        ),
        catalogue_fn=lambda: _seeded_caps(),
        correlation_id_fn=lambda: "e2e-cid",
    )

    out = run_recommend("check this content for conflicts with what we know", limit=5, deps=deps)

    assert out.error == ""
    assert out.recommendations, "the composed BM25 leg must surface a capability"
    top = out.recommendations[0]
    assert top.name == "contradict", f"expected the conflict-checking cap ranked first; got {top.name!r}"
    # The top recommendation carries a non-empty ready-to-call invocation.
    assert top.cli == "kairix contradict"
    assert top.mcp_tool == "contradict"
    assert top.mcp_tool or top.cli, "the top recommendation must carry a non-empty invocation"

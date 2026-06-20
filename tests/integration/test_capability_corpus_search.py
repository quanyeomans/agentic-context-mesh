"""Integration: the capability corpus is BM25-searchable end-to-end.

Builds the kairix-native corpus into a real tmp sqlite via the production
schema + the F61-sanctioned chunk writer, then queries it through a
factory-built ``SearchPipeline`` over the ``capabilities`` collection
(``agent=None``). This proves Feeder 1's half is BM25-searchable; the
recommender at query time ranks over BOTH ``capabilities`` and ``skills``
(see ``tests/use_cases/test_recommend.py`` for the full collection
contract).

Sabotage-proof log (executed mutate -> fail -> restore): changed
``written = writer.upsert(chunks)`` in ``build_capability_corpus`` to
``writer.upsert([])`` (write nothing); ran this test -> FAILED on
``assert result.written == 1`` (got 0) and the corpus would be empty for the
BM25 query; restored the line -> PASS.
"""

import sqlite3

import pytest

from kairix.core.db.schema import create_schema
from kairix.core.factory import build_search_pipeline, reset_search_pipeline_cache
from kairix.core.search.config import RerankConfig, RetrievalConfig
from tests.fakes import FakePaths, FakeProvider, FakeProviderRegistry

pytestmark = pytest.mark.integration


def _seeded_caps():
    return [
        {
            "name": "contradict",
            "mcp_tool": "contradict",
            "cli": "kairix contradict",
            "category": "synthesis",
            "when_to_use": "Check new content against existing knowledge for conflicts.",
        },
    ]


def test_corpus_is_bm25_searchable(tmp_path):
    from kairix.knowledge.capabilities.builder import (
        CapabilityCatalogueBuilder,
        CapabilityCorpusDeps,
        build_capability_corpus,
    )

    db_path = tmp_path / "index.sqlite"
    db = sqlite3.connect(db_path)
    create_schema(db)
    deps = CapabilityCorpusDeps(
        builder=CapabilityCatalogueBuilder(catalogue_fn=_seeded_caps, now_fn=lambda: "2026-06-20T00:00:00+00:00"),
        embed_batch_fn=lambda texts: [],  # no provider -> BM25-only branch
    )
    result = build_capability_corpus(db, deps=deps)
    db.commit()
    db.close()
    assert result.written == 1
    assert result.error == ""

    reset_search_pipeline_cache()
    cfg = RetrievalConfig(provider="fake", rerank=RerankConfig(enabled=True), skip_vector=True)
    registry = FakeProviderRegistry({"fake": FakeProvider(name="fake", vector=[0.1] * 1536, dim=1536)})
    paths = FakePaths(
        db_path=db_path,
        document_root=tmp_path,
        log_dir=tmp_path,
        workspace_root=tmp_path,
    )
    pipeline = build_search_pipeline(config=cfg, registry=registry, paths=paths)
    # BM25 is lexical: the query shares tokens ("check", "content", "conflicts")
    # with the seeded capability's when_to_use text so the keyword leg matches.
    res = pipeline.search(
        query="check content for conflicts",
        collections=["capabilities"],
        agent=None,
        budget=3000,
    )
    paths_found = [getattr(getattr(r, "result", None), "path", "") for r in res.results]
    assert any(p.startswith("capability://kairix/contradict") for p in paths_found)

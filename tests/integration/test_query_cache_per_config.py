"""#554 regression: each retrieval config gets its own QueryResultCache.

Pre-fix: ``build_search_pipeline`` wired every config to a single
process-shared ``_QUERY_CACHE`` singleton. The in-memory cache key omits
the ``RetrievalConfig`` (``make_cache_key`` keys on query/scope/agent/
collections only), so config #1's cached result for query ``Q`` was
returned for config #2's identical ``Q`` — the ``hybrid-sweep`` "byte-
identical scores for every config" bug. In the sweep, config #1 ran
(~159s); configs #2..N returned cached results in ~0.1s and could never
recommend a config change.

Post-fix: ``_get_or_create_query_cache`` keys caches by ``cfg_hash``, so
two materially different configs receive two different
``QueryResultCache`` instances and never share results. Production runs a
single config and therefore still has exactly one cache.

Sabotage proof (executed during development): reverting ``factory.py`` to
the singleton (``_QUERY_CACHE``) makes both configs wire the same cache
instance, so the first assertion (``pa.query_cache is not pb.query_cache``)
fails.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from kairix.core.db.schema import create_schema
from kairix.core.factory import build_search_pipeline, reset_search_pipeline_cache
from kairix.core.search.config import RetrievalConfig
from tests.fakes import FakePaths, FakeProvider, FakeProviderRegistry

pytestmark = pytest.mark.integration


def _bootstrap_db(db_path: Path) -> None:
    """Create an empty schema + one active doc so the factory build path is happy."""
    db = sqlite3.connect(str(db_path))
    create_schema(db)
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    db.execute(
        "INSERT OR REPLACE INTO documents "
        "(collection, path, hash, source_name, source_uri, source_modified_at, "
        "sensitivity, created_at, modified_at, active) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1)",
        ("default", "seed.md", "seed-hash", "seed.md", "src://default/seed.md", now, "internal", now, now),
    )
    db.commit()
    db.close()


def test_distinct_configs_get_distinct_query_caches(tmp_path: Path) -> None:
    """Two materially different retrieval configs must not share a query cache (#554)."""
    db_path = tmp_path / "index.sqlite"
    _bootstrap_db(db_path)
    paths = FakePaths(
        document_root=tmp_path / "vault",
        db_path=db_path,
        log_dir=tmp_path / "logs",
        workspace_root=tmp_path / "workspaces",
    )
    registry = FakeProviderRegistry(
        {"fake": FakeProvider(name="fake", vector=[0.1] * 1536, dim=1536)},
    )
    reset_search_pipeline_cache()

    # Two configs that differ (different cfg_hash) — the shape a sweep evaluates.
    cfg_a = RetrievalConfig(provider="fake", rrf_k=60)
    cfg_b = RetrievalConfig(provider="fake", rrf_k=20)
    assert cfg_a != cfg_b, "test precondition: the two configs must differ"

    pa = build_search_pipeline(config=cfg_a, registry=registry, paths=paths)
    pb = build_search_pipeline(config=cfg_b, registry=registry, paths=paths)

    # The #554 fix: distinct configs resolve to distinct cache instances, so
    # config A's cached results can never be served to config B (the bug).
    assert pa.query_cache is not pb.query_cache, (
        "distinct retrieval configs shared one QueryResultCache — config #1's "
        "results would leak into config #2 (the hybrid-sweep #554 bug). "
        "fix: _get_or_create_query_cache must key caches by cfg_hash."
    )

    # Same config → same cache instance (config-stable, so production keeps one).
    pa2 = build_search_pipeline(config=cfg_a, registry=registry, paths=paths)
    assert pa.query_cache is pa2.query_cache, (
        "the same config resolved to two different query caches — cfg_hash keying broken."
    )

    reset_search_pipeline_cache()

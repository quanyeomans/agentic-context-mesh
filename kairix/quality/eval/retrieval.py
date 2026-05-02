"""
Shared retrieval interface for eval tooling.

Consolidates the _retrieve() implementations from runner.py, generate.py,
and hybrid_sweep.py into a single module. All eval code should import
retrieve() from here rather than maintaining local wrappers.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class RetrievalResult:
    """Unified result from any retrieval backend."""

    paths: list[str]
    snippets: list[str] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)


def retrieve(
    query: str,
    system: str = "hybrid",
    agent: str | None = None,
    limit: int = 10,
    db_path: str | None = None,
    collection: str | None = None,
    collections: list[str] | None = None,
    fusion_override: str | None = None,
    config: Any | None = None,
    search_fn: Any | None = None,
) -> RetrievalResult:
    """
    Run retrieval and return a RetrievalResult.

    Args:
        query:            Search query text.
        system:           Backend: 'hybrid', 'bm25', 'mock', or 'mock-reflib'.
        agent:            Agent name for collection scoping (hybrid/bm25).
        limit:            Max results (bm25/mock backends).
        db_path:          Optional path to a specific database.
        collection:       Single collection name (converted to collections list).
        collections:      Explicit collections list (takes precedence over collection).
        fusion_override:  Override fusion strategy (hybrid only).
        config:           Pre-built RetrievalConfig (hybrid only; overrides fusion_override).

    Returns:
        RetrievalResult with paths, snippets, and metadata.

    Raises:
        ValueError: Unknown system name.
    """
    if system == "hybrid":
        return _retrieve_hybrid(
            query=query,
            agent=agent,
            collection=collection,
            collections=collections,
            fusion_override=fusion_override,
            config=config,
            search_fn=search_fn,
        )
    elif system == "bm25":
        return _retrieve_bm25(query=query, agent=agent, limit=limit)
    elif system == "mock":
        from kairix.quality.benchmark.mock_retrieval import mock_retrieve

        paths, snippets, meta = mock_retrieve(query=query, limit=limit)
        return RetrievalResult(paths=paths, snippets=snippets, meta=meta)
    elif system == "mock-reflib":
        from kairix.quality.benchmark.mock_reflib_retrieval import mock_reflib_retrieve

        paths, snippets, meta = mock_reflib_retrieve(query=query, limit=limit)
        return RetrievalResult(paths=paths, snippets=snippets, meta=meta)
    else:
        raise ValueError(f"Unknown system: {system!r}. Use 'hybrid', 'bm25', 'mock', or 'mock-reflib'.")


def _retrieve_hybrid(
    query: str,
    agent: str | None = None,
    collection: str | None = None,
    collections: list[str] | None = None,
    fusion_override: str | None = None,
    config: Any | None = None,
    search_fn: Any | None = None,
) -> RetrievalResult:
    """Hybrid search backend."""
    if search_fn is None:
        from kairix.core.factory import build_search_pipeline

        _pipeline = build_search_pipeline(config=config)
        search_fn = _pipeline.search

    if config is None and fusion_override:
        from dataclasses import replace

        from kairix.core.search.config_loader import load_config

        config = replace(load_config(), fusion_strategy=fusion_override)

    # Build explicit collections list when --collection is set
    effective_collections = collections or ([collection] if collection else None)

    search_kwargs: dict[str, Any] = {
        "query": query,
        "budget": 500_000,
        "collections": effective_collections,
    }
    if agent:
        search_kwargs["agent"] = agent
        search_kwargs["scope"] = "shared+agent"

    sr = search_fn(**search_kwargs)
    paths = [b.result.path for b in sr.results]
    snippets = [b.content[:500] for b in sr.results]
    meta = {
        "intent": sr.intent.value,
        "bm25_count": sr.bm25_count,
        "vec_count": sr.vec_count,
        "fused_count": sr.fused_count,
        "vec_failed": sr.vec_failed,
        "latency_ms": round(sr.latency_ms, 1),
    }
    return RetrievalResult(paths=paths, snippets=snippets, meta=meta)


def _retrieve_bm25(
    query: str,
    agent: str | None = None,
    limit: int = 10,
) -> RetrievalResult:
    """BM25-only backend."""
    from kairix.core.search.bm25 import bm25_search

    results = bm25_search(query=query, agent=agent, limit=limit)
    paths = [r["file"] for r in results]
    snippets = [r.get("snippet") or "" for r in results]
    return RetrievalResult(paths=paths, snippets=snippets, meta={"system": "bm25"})

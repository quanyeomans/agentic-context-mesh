"""Integration: the persistent embedding cache eliminates duplicate provider calls.

Boundary chain under test::

  run_embed(deps=...) -> _embed_batch_only -> EmbeddingCache.get_many
                                          \\-> embed_batch_fn (counting fake)
                                          \\-> EmbeddingCache.put_many

The contract that matters: a second run over the same chunks must
issue ZERO provider calls. This is the production safety net — the
user paid $211 for the embed corpus once, the cache exists so they
never pay twice for the same chunks.

Real components: ``run_embed``, ``EmbeddingCache``, the SQLite schema.
The only fake is the embed callable (a counting wrapper that asserts
on invocation count after each run).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest

from kairix.core.db import EMBED_VECTOR_DIMS
from kairix.core.db.schema import create_schema
from kairix.core.embed.deps import EmbedDependencies
from kairix.core.embed.embed import run_embed
from kairix.core.embed.embedding_cache import EmbeddingCache

pytestmark = pytest.mark.integration


class _CountingEmbedder:
    """Counts every call + every text embedded; returns deterministic vectors."""

    def __init__(self) -> None:
        self.call_count: int = 0
        self.text_count: int = 0

    def __call__(
        self,
        texts: list[str],
        _api_key: str,
        _endpoint: str,
        _deployment: str,
        dims: int,
        **_kwargs: Any,
    ) -> list[list[float]]:
        self.call_count += 1
        self.text_count += len(texts)
        # Deterministic per-text vector so the same chunk produces the
        # same vector on every dispatch (matters for the round-trip
        # equality assertion below).
        return [[float(hash(t) % 1000) / 1000.0] * dims for t in texts]


def _seed_corpus(db_path: Path, n_docs: int = 4) -> None:
    db = sqlite3.connect(str(db_path))
    create_schema(db)
    for i in range(n_docs):
        body = f"document {i} body text " * 50
        db.execute("INSERT INTO content (hash, doc) VALUES (?, ?)", (f"h{i}", body))
        db.execute(
            "INSERT INTO documents (hash, path, active, collection) VALUES (?, ?, 1, ?)",
            (f"h{i}", f"docs/doc{i}.md", "test"),
        )
    db.commit()
    db.close()


def _make_deps(
    embedder: _CountingEmbedder,
    cache: EmbeddingCache,
) -> EmbedDependencies:
    return EmbedDependencies(
        get_azure_config=lambda: ("k", "https://endpoint.example", "test-model"),
        preflight_check=lambda *_a, **_kw: EMBED_VECTOR_DIMS,
        embed_batch=embedder,
        open_usearch_index=lambda: None,
        migrate_content_vectors=lambda _db: None,
        get_document_root=lambda: None,
        open_embedding_cache=lambda: cache,
    )


def test_second_run_makes_zero_provider_calls(tmp_path: Path) -> None:
    """Run embed twice over the same corpus; the second run hits the cache
    for every chunk and dispatches NO provider calls.

    This is the production money-saving assertion — the cache exists
    for exactly this property.
    """
    db_path = tmp_path / "index.sqlite"
    cache_path = tmp_path / ".kairix" / "cache" / "embedding_cache.sqlite"
    _seed_corpus(db_path)

    # First run — provider sees every text once. Cache fills.
    cache = EmbeddingCache(cache_path)
    embedder = _CountingEmbedder()
    deps = _make_deps(embedder, cache)
    db = sqlite3.connect(str(db_path))
    try:
        result_first = run_embed(db=db, force=False, batch_size=100, deps=deps)
    finally:
        db.close()

    assert result_first["embedded"] == 4
    assert embedder.call_count >= 1
    first_text_count = embedder.text_count
    assert first_text_count == 4

    # The cache was closed at the end of run_embed; the rows persisted.
    inspect_cache = EmbeddingCache(cache_path)
    assert inspect_cache.count(model="test-model", dimension=EMBED_VECTOR_DIMS) == 4
    inspect_cache.close()

    # Second run — wipe content_vectors so the work queue is the same
    # and the cache hit-skip path drives the result. force=True clears
    # content_vectors but re-embeds; cache must absorb every dispatch.
    cache = EmbeddingCache(cache_path)
    embedder = _CountingEmbedder()
    deps = _make_deps(embedder, cache)
    db = sqlite3.connect(str(db_path))
    try:
        result_second = run_embed(db=db, force=True, batch_size=100, deps=deps)
    finally:
        db.close()

    assert result_second["embedded"] == 4
    assert embedder.call_count == 0, (
        f"expected zero provider calls on second run, got {embedder.call_count} (text_count={embedder.text_count})"
    )
    assert embedder.text_count == 0


def test_partial_cache_hit_only_dispatches_misses(tmp_path: Path) -> None:
    """Half the corpus is pre-cached; the second run dispatches only the
    uncached half to the provider."""
    db_path = tmp_path / "index.sqlite"
    cache_path = tmp_path / ".kairix" / "cache" / "embedding_cache.sqlite"
    _seed_corpus(db_path, n_docs=4)

    # First run embeds all 4 — fully populates the cache.
    cache = EmbeddingCache(cache_path)
    embedder = _CountingEmbedder()
    deps = _make_deps(embedder, cache)
    db = sqlite3.connect(str(db_path))
    try:
        run_embed(db=db, force=False, batch_size=100, deps=deps)
    finally:
        db.close()

    # Delete two cache rows so half the corpus is a miss.
    surgery = EmbeddingCache(cache_path)
    import sqlite3 as _sql

    raw = _sql.connect(str(cache_path))
    raw.execute("DELETE FROM embedding_cache WHERE chunk_hash IN (SELECT chunk_hash FROM embedding_cache LIMIT 2)")
    raw.commit()
    raw.close()
    surgery.close()

    # Second run — 2 cache hits + 2 cache misses dispatched to provider.
    cache = EmbeddingCache(cache_path)
    embedder = _CountingEmbedder()
    deps = _make_deps(embedder, cache)
    db = sqlite3.connect(str(db_path))
    try:
        result = run_embed(db=db, force=True, batch_size=100, deps=deps)
    finally:
        db.close()

    assert result["embedded"] == 4
    assert embedder.text_count == 2, f"expected 2 provider dispatches (the cache misses), got {embedder.text_count}"


def test_provider_failure_keeps_cache_hits_persisted(tmp_path: Path) -> None:
    """When the provider raises, the cache-hit subset of the batch still
    persists to content_vectors. Only the miss subset is reported failed.

    The production property: a single transient provider 5xx must not
    invalidate work the cache already proves we paid for.
    """
    db_path = tmp_path / "index.sqlite"
    cache_path = tmp_path / ".kairix" / "cache" / "embedding_cache.sqlite"
    _seed_corpus(db_path, n_docs=4)

    # Pre-populate the cache for two of the four chunks via a successful
    # first run, then wipe content_vectors and force a second run where
    # the embedder raises on miss dispatch.
    cache = EmbeddingCache(cache_path)
    embedder = _CountingEmbedder()
    deps = _make_deps(embedder, cache)
    db = sqlite3.connect(str(db_path))
    try:
        run_embed(db=db, force=False, batch_size=100, deps=deps)
    finally:
        db.close()

    # Trim the cache to only 2 rows so the next run has 2 hits + 2 misses.
    raw = sqlite3.connect(str(cache_path))
    raw.execute("DELETE FROM embedding_cache WHERE chunk_hash IN (SELECT chunk_hash FROM embedding_cache LIMIT 2)")
    raw.commit()
    raw.close()

    class _RaisingEmbedder:
        def __init__(self) -> None:
            self.calls: int = 0

        def __call__(self, *_a: Any, **_kw: Any) -> list[list[float]]:
            self.calls += 1
            raise RuntimeError("simulated provider 5xx")

    raising = _RaisingEmbedder()
    cache = EmbeddingCache(cache_path)
    deps = EmbedDependencies(
        get_azure_config=lambda: ("k", "https://endpoint.example", "test-model"),
        preflight_check=lambda *_a, **_kw: EMBED_VECTOR_DIMS,
        embed_batch=raising,
        open_usearch_index=lambda: None,
        migrate_content_vectors=lambda _db: None,
        get_document_root=lambda: None,
        open_embedding_cache=lambda: cache,
    )
    db = sqlite3.connect(str(db_path))
    try:
        result = run_embed(db=db, force=True, batch_size=100, deps=deps)
    finally:
        db.close()

    # Two cache-hits embedded; two misses failed.
    assert result["embedded"] == 2
    assert result["failed"] == 2
    assert raising.calls == 1


def test_force_rebuild_cache_drops_then_re_dispatches(tmp_path: Path) -> None:
    """``--force-rebuild-cache`` flushes the cache so the next run
    dispatches every chunk to the provider again.

    Verifies the clear-cache path; the rest of the flow is the same
    cache-hit-skip mechanism as the other tests.
    """
    db_path = tmp_path / "index.sqlite"
    cache_path = tmp_path / ".kairix" / "cache" / "embedding_cache.sqlite"
    _seed_corpus(db_path, n_docs=3)

    cache = EmbeddingCache(cache_path)
    embedder = _CountingEmbedder()
    deps = _make_deps(embedder, cache)
    db = sqlite3.connect(str(db_path))
    try:
        run_embed(db=db, force=False, batch_size=100, deps=deps)
    finally:
        db.close()

    # Cache fully populated; flush it directly to mimic the
    # --force-rebuild-cache effect at the storage boundary.
    surgery = EmbeddingCache(cache_path)
    surgery.clear()
    surgery.close()

    cache = EmbeddingCache(cache_path)
    embedder = _CountingEmbedder()
    deps = _make_deps(embedder, cache)
    db = sqlite3.connect(str(db_path))
    try:
        result = run_embed(db=db, force=True, batch_size=100, deps=deps)
    finally:
        db.close()

    assert result["embedded"] == 3
    assert embedder.text_count == 3, (
        f"after cache clear expected 3 fresh provider dispatches, got {embedder.text_count}"
    )

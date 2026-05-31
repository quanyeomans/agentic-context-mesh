"""Step definitions for embedding_cache.feature.

Drives the embed pipeline through ``EmbedDependencies``-injected fakes
and a real :class:`kairix.core.embed.embedding_cache.EmbeddingCache`
against ``tmp_path``. No monkeypatch / @patch / KAIRIX_* env reads —
F1 / F2 / F46 / F47-clean.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from pytest_bdd import given, parsers, then, when

from kairix.core.db import EMBED_VECTOR_DIMS
from kairix.core.db.schema import create_schema
from kairix.core.embed.deps import EmbedDependencies
from kairix.core.embed.embed import run_embed
from kairix.core.embed.embedding_cache import EmbeddingCache, hash_chunk_text

pytestmark = pytest.mark.bdd


_MODEL = "bdd-model"


@pytest.fixture
def _cache_state(tmp_path: Path) -> dict[str, Any]:
    return {
        "tmp_path": tmp_path,
        "cache": None,
        "cache_path": tmp_path / ".kairix" / "cache" / "embedding_cache.sqlite",
        "vector": None,
        "vector_back": None,
        "stored_pairs": {},
        "model_writes": {},
        "provider_calls": 0,
        "provider_text_count": 0,
        "second_run_provider_calls": 0,
        "first_run_provider_calls": 0,
        "n_docs": 0,
        "preseed_count": 0,
    }


def _seed_corpus(db_path: Path, n_docs: int) -> list[str]:
    """Insert n_docs rows; return the chunk texts so the test can pre-cache them.

    Returns the EXACT text the chunker produces for each doc — small
    bodies pass through as one chunk verbatim, so the text the cache
    sees is the same string passed to INSERT.
    """
    db = sqlite3.connect(str(db_path))
    create_schema(db)
    texts: list[str] = []
    for i in range(n_docs):
        body = f"bdd document {i} body text " * 30
        texts.append(body)
        db.execute("INSERT INTO content (hash, doc) VALUES (?, ?)", (f"bdd-h{i}", body))
        db.execute(
            "INSERT INTO documents (hash, path, active, collection) VALUES (?, ?, 1, ?)",
            (f"bdd-h{i}", f"bdd/doc{i}.md", "test"),
        )
    db.commit()
    db.close()
    return texts


def _make_deps(
    embedder: Any,
    cache: EmbeddingCache,
) -> EmbedDependencies:
    return EmbedDependencies(
        get_azure_config=lambda: ("k", "https://ep.example", _MODEL),
        preflight_check=lambda *_a, **_kw: EMBED_VECTOR_DIMS,
        embed_batch=embedder,
        open_usearch_index=lambda: None,
        migrate_content_vectors=lambda _db: None,
        get_document_root=lambda: None,
        open_embedding_cache=lambda: cache,
    )


@given("an embedding cache backed by a fresh SQLite file")
def _given_fresh_cache(_cache_state: dict[str, Any]) -> None:
    _cache_state["cache"] = EmbeddingCache(_cache_state["cache_path"])


@given(parsers.parse('a vector for chunk hash "{chunk_hash}" under model "{model}" at dimension {dim:d}'))
def _given_vector(
    _cache_state: dict[str, Any],
    chunk_hash: str,
    model: str,
    dim: int,
) -> None:
    rng = np.random.default_rng(7)
    _cache_state["vector"] = rng.random(dim, dtype=np.float32).tolist()
    _cache_state["pending_put"] = (chunk_hash, model, dim, _cache_state["vector"])


@when("I put the vector into the cache")
def _when_put(_cache_state: dict[str, Any]) -> None:
    chunk_hash, model, dim, vector = _cache_state["pending_put"]
    _cache_state["cache"].put_many(model, dim, [(chunk_hash, vector)])


@when("I read the vector back from the cache")
def _when_read(_cache_state: dict[str, Any]) -> None:
    chunk_hash, model, dim, _vector = _cache_state["pending_put"]
    got = _cache_state["cache"].get_many(model, dim, [chunk_hash])
    _cache_state["vector_back"] = got.get(chunk_hash)


@then("the cache returns the same vector")
def _then_same_vector(_cache_state: dict[str, Any]) -> None:
    expected = np.asarray(_cache_state["vector"], dtype="float32")
    actual = _cache_state["vector_back"]
    assert actual is not None, "cache returned no row"
    assert np.allclose(actual, expected, atol=0.0)


@given(parsers.parse("a corpus of {n:d} chunks"))
def _given_corpus(_cache_state: dict[str, Any], n: int) -> None:
    db_path = _cache_state["tmp_path"] / "index.sqlite"
    _cache_state["db_path"] = db_path
    _cache_state["corpus_texts"] = _seed_corpus(db_path, n)
    _cache_state["n_docs"] = n


@given("an empty cache")
def _given_empty_cache(_cache_state: dict[str, Any]) -> None:
    if _cache_state["cache"] is not None:
        _cache_state["cache"].clear()


@given(parsers.parse("the cache already holds vectors for {n:d} of those chunks"))
def _given_preseeded(_cache_state: dict[str, Any], n: int) -> None:
    cache: EmbeddingCache = _cache_state["cache"]
    rng = np.random.default_rng(99)
    pairs = [
        (hash_chunk_text(text), rng.random(EMBED_VECTOR_DIMS, dtype=np.float32).tolist())
        for text in _cache_state["corpus_texts"][:n]
    ]
    cache.put_many(_MODEL, EMBED_VECTOR_DIMS, pairs)
    _cache_state["preseed_count"] = n


class _CountingProvider:
    def __init__(self) -> None:
        self.call_count = 0
        self.text_count = 0

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
        return [[float(hash(t) % 1000) / 1000.0] * dims for t in texts]


def _run_one_pipeline(_cache_state: dict[str, Any]) -> _CountingProvider:
    provider = _CountingProvider()
    cache = EmbeddingCache(_cache_state["cache_path"])
    deps = _make_deps(provider, cache)
    db = sqlite3.connect(str(_cache_state["db_path"]))
    try:
        run_embed(db=db, force=True, batch_size=100, deps=deps)
    finally:
        db.close()
    return provider


@when("the operator runs the embed pipeline once with a counting provider")
def _when_first_run(_cache_state: dict[str, Any]) -> None:
    provider = _run_one_pipeline(_cache_state)
    _cache_state["first_run_provider_calls"] = provider.call_count
    _cache_state["first_run_text_count"] = provider.text_count


@when("the operator runs the embed pipeline a second time with a counting provider")
def _when_second_run(_cache_state: dict[str, Any]) -> None:
    provider = _run_one_pipeline(_cache_state)
    _cache_state["second_run_provider_calls"] = provider.call_count
    _cache_state["second_run_text_count"] = provider.text_count


@then("the second run dispatches zero provider calls")
def _then_zero_calls(_cache_state: dict[str, Any]) -> None:
    assert _cache_state["second_run_provider_calls"] == 0, (
        f"expected 0 provider calls on second run, got {_cache_state['second_run_provider_calls']} "
        f"({_cache_state['second_run_text_count']} texts dispatched)"
    )


@when("the operator runs the embed pipeline with a counting provider")
def _when_single_run(_cache_state: dict[str, Any]) -> None:
    provider = _run_one_pipeline(_cache_state)
    _cache_state["provider_calls"] = provider.call_count
    _cache_state["provider_text_count"] = provider.text_count


@then(parsers.parse("the provider sees exactly {n:d} chunks"))
def _then_n_provider_chunks(_cache_state: dict[str, Any], n: int) -> None:
    assert _cache_state["provider_text_count"] == n, (
        f"expected provider to see {n} chunks, got {_cache_state['provider_text_count']}"
    )


@then(parsers.parse("the cache now holds vectors for all {n:d} chunks"))
def _then_cache_holds_all(_cache_state: dict[str, Any], n: int) -> None:
    inspect = EmbeddingCache(_cache_state["cache_path"])
    try:
        assert inspect.count(model=_MODEL, dimension=EMBED_VECTOR_DIMS) == n
    finally:
        inspect.close()


@given(parsers.parse('the cache holds a vector for chunk "{chunk_hash}" under model "{model}" at dimension {dim:d}'))
def _given_existing_cache_row(
    _cache_state: dict[str, Any],
    chunk_hash: str,
    model: str,
    dim: int,
) -> None:
    rng = np.random.default_rng(31)
    vec = rng.random(dim, dtype=np.float32).tolist()
    _cache_state["cache"].put_many(model, dim, [(chunk_hash, vec)])
    _cache_state["model_writes"][model] = vec


@when(parsers.parse('a put writes a vector for chunk "{chunk_hash}" under model "{model}" at dimension {dim:d}'))
def _when_put_other_model(
    _cache_state: dict[str, Any],
    chunk_hash: str,
    model: str,
    dim: int,
) -> None:
    rng = np.random.default_rng(53)
    vec = rng.random(dim, dtype=np.float32).tolist()
    _cache_state["cache"].put_many(model, dim, [(chunk_hash, vec)])
    _cache_state["model_writes"][model] = vec
    _cache_state["last_chunk_hash"] = chunk_hash
    _cache_state["last_dim"] = dim


@then("the cache holds a separate vector under each model name")
def _then_model_isolated(_cache_state: dict[str, Any]) -> None:
    cache: EmbeddingCache = _cache_state["cache"]
    chunk_hash = _cache_state["last_chunk_hash"]
    dim = _cache_state["last_dim"]
    rows = {model: cache.get_many(model, dim, [chunk_hash]).get(chunk_hash) for model in _cache_state["model_writes"]}
    assert all(v is not None for v in rows.values()), "expected every model slice to have the row"
    distinct = {tuple(v.tolist()) for v in rows.values()}
    assert len(distinct) == len(rows), "expected distinct vectors per model"

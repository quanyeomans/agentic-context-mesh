"""Unit tests for the persistent embedding cache.

Exercises :class:`kairix.core.embed.embedding_cache.EmbeddingCache`
roundtrip, multi-model coexistence, dimension isolation, and partial
hit behaviour. Pure SQLite — no network, no fakes; the cache itself is
the thing under test.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from kairix.core.embed.embedding_cache import (
    EmbeddingCache,
    cache_db_path,
    hash_chunk_text,
)

pytestmark = pytest.mark.unit


def _vec(seed: int, dim: int) -> list[float]:
    rng = np.random.default_rng(seed)
    return rng.random(dim, dtype=np.float32).tolist()


def test_put_get_roundtrip_preserves_vectors(tmp_path: Path) -> None:
    """Vectors written via put_many come back equal via get_many.

    Bit-for-bit equality matters because the cache is the source of
    truth — a noisy roundtrip would mean a vec-index rebuild from cache
    produces different distances than the original embed.
    """
    cache = EmbeddingCache(tmp_path / "cache.sqlite")
    model = "text-embedding-3-large"
    dim = 16

    chunks = {f"hash-{i}": _vec(i, dim) for i in range(5)}
    written = cache.put_many(model, dim, chunks.items())
    assert written == 5

    got = cache.get_many(model, dim, list(chunks.keys()))
    assert set(got.keys()) == set(chunks.keys())
    for h, expected in chunks.items():
        assert np.allclose(got[h], np.asarray(expected, dtype="float32"), atol=0.0)
    cache.close()


def test_multi_model_coexistence_isolated(tmp_path: Path) -> None:
    """Two model namespaces never collide on the same chunk hash."""
    cache = EmbeddingCache(tmp_path / "cache.sqlite")
    h = "shared-hash"
    cache.put_many("model-a", 8, [(h, _vec(1, 8))])
    cache.put_many("model-b", 8, [(h, _vec(2, 8))])

    a = cache.get_many("model-a", 8, [h])
    b = cache.get_many("model-b", 8, [h])
    assert h in a and h in b
    assert not np.allclose(a[h], b[h])
    assert cache.count() == 2
    cache.close()


def test_dimension_change_keeps_old_slice(tmp_path: Path) -> None:
    """A dimension switch leaves the old-dimension slice intact and
    independently queryable.

    Operationally: switching text-embedding-3-large from 3072 -> 1536
    must not orphan the previous-dimension records; they remain valid
    for any downstream caller that asks at the original dimension.
    """
    cache = EmbeddingCache(tmp_path / "cache.sqlite")
    cache.put_many("m", 4, [("h", _vec(1, 4))])
    cache.put_many("m", 8, [("h", _vec(2, 8))])

    at_4 = cache.get_many("m", 4, ["h"])
    at_8 = cache.get_many("m", 8, ["h"])
    assert at_4["h"].shape == (4,)
    assert at_8["h"].shape == (8,)
    cache.close()


def test_get_many_returns_only_hits(tmp_path: Path) -> None:
    """Missing hashes are simply absent — no None entries, no raise."""
    cache = EmbeddingCache(tmp_path / "cache.sqlite")
    cache.put_many("m", 4, [("present", _vec(1, 4))])
    got = cache.get_many("m", 4, ["present", "absent"])
    assert list(got.keys()) == ["present"]
    cache.close()


def test_get_many_empty_input_is_empty_dict(tmp_path: Path) -> None:
    """get_many([]) returns {} without touching the DB."""
    cache = EmbeddingCache(tmp_path / "cache.sqlite")
    assert cache.get_many("m", 4, []) == {}
    cache.close()


def test_put_many_empty_input_is_zero_writes(tmp_path: Path) -> None:
    """put_many([]) returns 0 — used for the all-cache-hit fast path."""
    cache = EmbeddingCache(tmp_path / "cache.sqlite")
    assert cache.put_many("m", 4, []) == 0
    cache.close()


def test_upsert_overwrites_same_key(tmp_path: Path) -> None:
    """Re-putting a hash overwrites — keeps the cache idempotent under retry."""
    cache = EmbeddingCache(tmp_path / "cache.sqlite")
    cache.put_many("m", 4, [("h", _vec(1, 4))])
    cache.put_many("m", 4, [("h", _vec(2, 4))])
    got = cache.get_many("m", 4, ["h"])
    assert cache.count() == 1
    assert np.allclose(got["h"], np.asarray(_vec(2, 4), dtype="float32"))
    cache.close()


def test_count_scopes_to_model_dimension_pair(tmp_path: Path) -> None:
    """count(model=..., dimension=...) only sees rows for that slice."""
    cache = EmbeddingCache(tmp_path / "cache.sqlite")
    cache.put_many("m", 4, [("a", _vec(1, 4))])
    cache.put_many("m", 4, [("b", _vec(2, 4))])
    cache.put_many("other", 4, [("c", _vec(3, 4))])
    assert cache.count() == 3
    assert cache.count(model="m", dimension=4) == 2
    assert cache.count(model="other", dimension=4) == 1
    cache.close()


def test_count_requires_both_or_neither_filter(tmp_path: Path) -> None:
    """Mixing one filter without the other is a programmer error — surfaces
    as ValueError with an actionable affordance, not silently returns the
    wrong slice."""
    cache = EmbeddingCache(tmp_path / "cache.sqlite")
    with pytest.raises(ValueError, match="both model and dimension or neither"):
        cache.count(model="m")
    cache.close()


def test_clear_drops_every_row(tmp_path: Path) -> None:
    """clear() drops every row across every model/dimension slice."""
    cache = EmbeddingCache(tmp_path / "cache.sqlite")
    cache.put_many("m", 4, [("a", _vec(1, 4))])
    cache.put_many("n", 8, [("b", _vec(2, 8))])
    cache.clear()
    assert cache.count() == 0
    assert cache.get_many("m", 4, ["a"]) == {}
    cache.close()


def test_decode_validates_blob_length(tmp_path: Path) -> None:
    """A blob whose byte length doesn't match the declared dimension surfaces
    as ValueError with an actionable affordance.

    The cache key includes dimension, so a mismatched query normally
    misses — this test simulates corruption by writing a wrong-length
    blob directly under a hash the get_many call asks for at the
    declared dimension.
    """
    import sqlite3 as _sql

    path = tmp_path / "cache.sqlite"
    cache = EmbeddingCache(path)
    cache.put_many("m", 8, [("h", _vec(1, 8))])
    cache.close()

    # Corrupt the stored vector — overwrite with too-short bytes.
    raw = _sql.connect(str(path))
    raw.execute(
        "UPDATE embedding_cache SET vector = ? WHERE model = ? AND dimension = ? AND chunk_hash = ?",
        (b"\x00\x00\x00\x00", "m", 8, "h"),
    )
    raw.commit()
    raw.close()

    cache = EmbeddingCache(path)
    with pytest.raises(ValueError, match=r"vector blob length .* != declared dimension"):
        cache.get_many("m", 8, ["h"])
    cache.close()


def test_cache_persists_across_handle_reopen(tmp_path: Path) -> None:
    """Closing and re-opening the same path returns the same rows.

    This is the production crash-recovery contract — the cache must
    survive a process restart so the next embed run finds the vectors
    written by the previous run.
    """
    path = tmp_path / "cache.sqlite"
    first = EmbeddingCache(path)
    first.put_many("m", 4, [("h", _vec(1, 4))])
    first.close()

    second = EmbeddingCache(path)
    got = second.get_many("m", 4, ["h"])
    assert "h" in got
    assert np.allclose(got["h"], np.asarray(_vec(1, 4), dtype="float32"))
    second.close()


def test_close_is_idempotent(tmp_path: Path) -> None:
    """Closing twice does not raise."""
    cache = EmbeddingCache(tmp_path / "cache.sqlite")
    cache.close()
    cache.close()


def test_cache_path_under_document_root(tmp_path: Path) -> None:
    """The cache file lives under .kairix/cache/embedding_cache.sqlite."""
    resolved = cache_db_path(tmp_path)
    assert resolved == tmp_path / ".kairix" / "cache" / "embedding_cache.sqlite"


def test_hash_chunk_text_is_stable_and_unique() -> None:
    """Same text -> same hash; different text -> different hash."""
    assert hash_chunk_text("hello") == hash_chunk_text("hello")
    assert hash_chunk_text("hello") != hash_chunk_text("hello world")
    assert len(hash_chunk_text("x")) == 64


def test_path_property_reports_constructor_path(tmp_path: Path) -> None:
    """``cache.path`` matches the path the cache was constructed with."""
    target = tmp_path / "sub" / "cache.sqlite"
    cache = EmbeddingCache(target)
    assert cache.path == target
    cache.close()


def test_get_many_batches_large_hash_lists(tmp_path: Path) -> None:
    """A hash list larger than the IN-clause batch size still resolves
    every present row.

    Sabotage target: a hand-rolled IN clause that skips the second
    batch would return half the hits.
    """
    cache = EmbeddingCache(tmp_path / "cache.sqlite")
    n = 750
    pairs = [(f"h{i}", _vec(i, 4)) for i in range(n)]
    cache.put_many("m", 4, pairs)
    got = cache.get_many("m", 4, [f"h{i}" for i in range(n)])
    assert len(got) == n
    cache.close()


def test_concurrent_reads_writes_from_multiple_threads_safe(tmp_path: Path) -> None:
    """20 worker threads hammer the cache concurrently — no SQLite errors.

    Production bug fixed: the parallel embed pipeline (--parallel N>1)
    runs Azure calls on a ThreadPoolExecutor and the cache get_many /
    put_many calls happen from worker threads. The original
    sqlite3.connect(...) defaulted to check_same_thread=True which
    crashed the first cross-thread call with 'SQLite objects created
    in a thread can only be used in that same thread' on a $211 prod
    embed run on 2026-05-31.

    Sabotage target: removing check_same_thread=False from the
    connection open OR removing the lock around get_many / put_many
    surfaces SQLite errors under this stress shape.
    """
    import threading

    cache = EmbeddingCache(tmp_path / "cache.sqlite")
    cache.put_many("m", 4, [("seed", _vec(0, 4))])

    errors: list[BaseException] = []

    def worker(i: int) -> None:
        try:
            cache.get_many("m", 4, ["seed", f"h_{i - 1}"])
            cache.put_many("m", 4, [(f"h_{i}", _vec(i, 4))])
            cache.count("m", 4)
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"expected zero SQLite errors under concurrent access; got {len(errors)}: {errors[:3]}"
    assert cache.count("m", 4) == 21, "1 seed + 20 worker upserts"
    cache.close()

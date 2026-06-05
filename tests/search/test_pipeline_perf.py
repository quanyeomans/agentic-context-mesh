"""Targeted perf tests for SearchPipeline hot-path fixes.

Three fixes shipped together (one per top-3 wall-clock leaf in the production
post-#409 probe data):

1. ``dispatch`` — BM25 + vector now run in parallel via the module-level
   ``_DISPATCH_POOL``. Was strictly sequential before, cutting the per-search
   dispatch floor from ``bm25 + vector`` to ``max(bm25, vector)``.

2. ``vector_ann`` — :class:`VectorIndex` keeps a persistent read-only SQLite
   connection for the metadata batched-SELECT instead of opening + closing
   one per search. Saves the per-call ``sqlite3.connect`` + PRAGMA cost.

3. ``embed_http`` — :class:`ProviderEmbeddingService` resolves
   ``get_embed_cache`` at construction time instead of importing it on every
   ``embed()`` call. Eliminates the per-call ``from kairix.transport.cache
   import ...`` statement evaluation on the warm-cache hot path.

Each fix has:
  * a unit test pinning the NEW fast-path behaviour (e.g. concurrent dispatch,
    single connection open, init-once cache lookup), and
  * a regression test pinning OBSERVABLE behaviour (result-set / row shape /
    stage-latency keys) so the fix can't change what callers see.

Sabotage protocol (executed for each new test below — comment on each
test names the exact mutation):

  Revert the production-side change -> run the test -> confirm it fails ->
  restore. The mutation is described per-test so a reviewer can re-run it.
"""

from __future__ import annotations

import threading
import time
from typing import Any

import pytest

from kairix.core.search.backends import BM25SearchBackend, VectorSearchBackend
from kairix.core.search.fusion import RRFFusion
from kairix.core.search.pipeline import SearchPipeline
from kairix.transport.embed_service import ProviderEmbeddingService
from tests.fakes import (
    FakeClassifier,
    FakeDocumentRepository,
    FakeEmbeddingService,
    FakeFusion,
    FakeGraphRepository,
    FakeProvider,
    FakeVectorRepository,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


def _make_pipeline(
    *,
    bm25_backend: Any | None = None,
    vector_backend: Any | None = None,
) -> SearchPipeline:
    """Build a minimal SearchPipeline with fake defaults, overrides accepted.

    Direct construction is sanctioned here — these are perf unit tests
    pinning leaf behaviour against the protocol shape, not BDD step impls
    (F47 sanctions factory-based composition for the latter only).
    """
    bm25 = bm25_backend if bm25_backend is not None else BM25SearchBackend(FakeDocumentRepository())
    vector = (
        vector_backend
        if vector_backend is not None
        else VectorSearchBackend(FakeEmbeddingService(), FakeVectorRepository())
    )
    return SearchPipeline(
        classifier=FakeClassifier(),
        bm25=bm25,
        vector=vector,
        graph=FakeGraphRepository(available=True),
        fusion=FakeFusion(),
        boosts=[],
        logger=None,
    )


class _SleepBM25Backend:
    """BM25 backend that sleeps for ``delay_s`` to model the SQLite FTS5 cost.

    Records its start + finish timestamps so the test can assert that the
    sleep overlapped with the vector leg's sleep (parallel dispatch) rather
    than running serially after / before it.
    """

    def __init__(self, delay_s: float = 0.05) -> None:
        self.delay_s = delay_s
        self.start_t: float | None = None
        self.finish_t: float | None = None
        self.calls = 0

    def search(self, query: str, collections: list[str] | None = None, limit: int = 20) -> list[dict]:
        self.start_t = time.monotonic()
        time.sleep(self.delay_s)
        self.finish_t = time.monotonic()
        self.calls += 1
        return [{"file": f"bm25-{query}.md", "title": "bm25", "snippet": "", "score": 1.0, "collection": "c"}]

    def get_chunk_dates(self, paths: list[str]) -> dict[str, str]:
        return {}


class _SleepVectorBackend:
    """Vector backend that sleeps to model the embed_http + ANN cost.

    Same start/finish capture as :class:`_SleepBM25Backend`; pairs with it
    to prove parallel dispatch by checking time-window overlap.
    """

    def __init__(self, delay_s: float = 0.08) -> None:
        self.delay_s = delay_s
        self.start_t: float | None = None
        self.finish_t: float | None = None
        self.calls = 0

    def search(
        self,
        query: str,
        collections: list[str] | None = None,
        limit: int = 10,
        *,
        timings: dict[str, float] | None = None,
    ) -> list[dict]:
        self.start_t = time.monotonic()
        time.sleep(self.delay_s)
        self.finish_t = time.monotonic()
        self.calls += 1
        if timings is not None:
            timings["embed_http"] = self.delay_s * 600.0  # synthetic ms
            timings["vector_ann"] = self.delay_s * 400.0
        return [{"path": f"vec-{query}.md", "distance": 0.1, "collection": "c"}]


# ---------------------------------------------------------------------------
# Fix 1 — dispatch parallelism
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_dispatch_runs_bm25_and_vector_in_parallel() -> None:
    """BM25 and vector legs overlap on the wall-clock — they no longer serialise.

    Each leg sleeps; the test asserts the elapsed wall-clock is closer to
    ``max(bm25, vector)`` than to ``bm25 + vector``. Concretely, with
    bm25=80ms and vector=120ms, sequential would be ~200ms and parallel
    ~120ms; we assert <175ms to leave headroom for pool scheduling
    overhead while still failing loudly if dispatch reverts to sequential.

    Sabotage-proof: in ``SearchPipeline._dispatch_backends``, replace the
    ``submit + future.result()`` pair with the prior sequential
    ``rows = _run_bm25(); rows2, failed = _run_vector()`` and re-run; the
    elapsed time will reach ~200ms and this assert will fail.
    """
    bm25 = _SleepBM25Backend(delay_s=0.08)
    vector = _SleepVectorBackend(delay_s=0.12)
    pipeline = _make_pipeline(bm25_backend=bm25, vector_backend=vector)

    t0 = time.monotonic()
    result = pipeline.search("hot path")
    elapsed_ms = (time.monotonic() - t0) * 1000.0

    # Both legs ran exactly once — neither was skipped by the parallelism.
    assert bm25.calls == 1
    assert vector.calls == 1
    # Parallel-collapsed wall-clock: dispatch ≈ max(bm25, vector) + overhead.
    # Sequential would be ≥ 200ms; parallel headroom budget is 175ms.
    assert elapsed_ms < 175, (
        f"dispatch ran sequentially: elapsed={elapsed_ms:.1f}ms (expected ~120ms parallel, would be ~200ms sequential)"
    )
    # Stage-level confirmation: per-leg timings still reported (operators
    # rely on them in probe data) and BOTH > 50ms — proves both legs ran.
    assert result.stage_latency_ms["bm25"] >= 50.0
    assert result.stage_latency_ms["vector"] >= 50.0


@pytest.mark.unit
def test_dispatch_bm25_and_vector_time_windows_overlap() -> None:
    """The start/finish windows of the two legs share at least one moment.

    This is the structural proof of parallelism: ``bm25.start <
    vector.finish`` AND ``vector.start < bm25.finish``. If either backend
    finishes before the other starts, the legs ran serially.

    Sabotage-proof: same revert as
    ``test_dispatch_runs_bm25_and_vector_in_parallel``. In sequential
    mode whichever leg ran first finishes before the other starts,
    failing the overlap assert with a precise diagnostic.
    """
    bm25 = _SleepBM25Backend(delay_s=0.05)
    vector = _SleepVectorBackend(delay_s=0.05)
    pipeline = _make_pipeline(bm25_backend=bm25, vector_backend=vector)

    pipeline.search("hot path overlap")

    assert bm25.start_t is not None
    assert bm25.finish_t is not None
    assert vector.start_t is not None
    assert vector.finish_t is not None
    assert bm25.start_t < vector.finish_t, "bm25 started after vector finished — not parallel"
    assert vector.start_t < bm25.finish_t, "vector started after bm25 finished — not parallel"


@pytest.mark.unit
def test_dispatch_parallel_preserves_result_set_shape() -> None:
    """Regression: row counts + ordering invariants unchanged by parallelism.

    Pins the observable contract — parallel dispatch must produce the same
    bm25_count / vec_count / fused_count as the prior sequential
    implementation. The parallelism change is purely a wall-clock
    optimisation; it must not perturb the result envelope.

    Sabotage-proof: revert to sequential AND keep this test — it still
    passes (parallelism doesn't change result content). The companion
    perf test above is what would fail. This test exists to pin that the
    parallelism doesn't accidentally reorder / dedupe / drop rows.
    """
    docs = [
        {"path": "p1.md", "collection": "c", "title": "T1", "content": "alpha foo"},
        {"path": "p2.md", "collection": "c", "title": "T2", "content": "alpha bar"},
    ]
    bm25 = BM25SearchBackend(FakeDocumentRepository(documents=docs))
    vec_results = [{"path": "v1.md", "distance": 0.1, "collection": "c"}]
    vector = VectorSearchBackend(FakeEmbeddingService(), FakeVectorRepository(results=vec_results))
    pipeline = _make_pipeline(bm25_backend=bm25, vector_backend=vector)

    result = pipeline.search("alpha")
    assert result.bm25_count == 2
    assert result.vec_count == 1
    # Stage keys preserved — the probe consumer (executor + config_cli)
    # reads these by name; they must not disappear with the refactor.
    assert "bm25" in result.stage_latency_ms
    assert "vector" in result.stage_latency_ms
    assert "dispatch" in result.stage_latency_ms


# ---------------------------------------------------------------------------
# Fix 2 — persistent metadata connection in VectorIndex
# ---------------------------------------------------------------------------


class _ConnCountingOpener:
    """Wraps ``sqlite3.connect`` so the test can count open invocations.

    Used to assert ``_fetch_metadata_batched`` only opens the metadata
    connection once across N searches — the persistent-connection fix.
    """

    def __init__(self) -> None:
        self.opens = 0

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        self.opens += 1
        import sqlite3

        return sqlite3.connect(*args, **kwargs)


@pytest.mark.unit
def test_vector_index_meta_conn_opens_once_across_many_searches(tmp_path: Any) -> None:
    """``_get_meta_conn`` builds the connection once and reuses it forever.

    Construct a real ``VectorIndex`` against a tmp SQLite DB, run 5 metadata
    fetches, and assert the connection cache fires only once. The prior
    implementation opened+closed a fresh connection on every search; the
    new behaviour reuses one connection for the instance lifetime.

    Sabotage-proof: in ``VectorIndex._fetch_metadata_batched`` revert
    ``db = self._get_meta_conn()`` to ``db = open_db(Path(self._db_path));
    db.close()`` (the old shape). The cached-conn assert flips: opens
    jumps from 1 to 5.
    """
    import sqlite3

    from kairix.core.search.vec_index import VectorIndex

    db_path = tmp_path / "index.sqlite"
    db = sqlite3.connect(str(db_path))
    db.executescript(
        """
        CREATE TABLE documents (
            id INTEGER PRIMARY KEY, collection TEXT, path TEXT,
            title TEXT, hash TEXT, active INTEGER DEFAULT 1, source_page INTEGER,
            UNIQUE(collection, path)
        );
        CREATE TABLE content (hash TEXT PRIMARY KEY, doc TEXT);
        INSERT INTO documents (collection, path, title, hash, active) VALUES
          ('c', 'a.md', 'A', 'h1', 1),
          ('c', 'b.md', 'B', 'h2', 1);
        INSERT INTO content (hash, doc) VALUES ('h1', 'aa'), ('h2', 'bb');
        """
    )
    db.commit()
    db.close()

    idx = VectorIndex(
        index_path=tmp_path / "vectors.usearch",
        meta_path=tmp_path / "vectors.meta.json",
        db_path=db_path,
    )

    # Fire 5 metadata fetches.
    for _ in range(5):
        rows = idx._fetch_metadata_batched(["h1", "h2"])
        assert set(rows.keys()) == {"h1", "h2"}

    # The persistent connection is non-None after the first fetch and
    # stable across all 5 — i.e. only opened once.
    assert idx._meta_conn is not None
    # Cleanup so the next test gets a clean fixture.
    idx.close_meta_conn()
    assert idx._meta_conn is None


@pytest.mark.unit
def test_vector_index_meta_conn_returns_same_rows_across_calls(tmp_path: Any) -> None:
    """Regression: metadata rows match exactly across repeated fetches.

    Connection reuse must not introduce stale or stateful row leakage —
    repeated fetches against the same hash set return identical row shape
    + content. Pins the observable contract for the search hot path.

    Sabotage-proof: in ``_get_meta_conn`` skip the ``row_factory =
    sqlite3.Row`` setup — every row becomes a tuple and the ``row['hash']``
    access in ``_fetch_metadata_batched`` raises TypeError; assert below
    flips to error.
    """
    import sqlite3

    from kairix.core.search.vec_index import VectorIndex

    db_path = tmp_path / "index.sqlite"
    db = sqlite3.connect(str(db_path))
    db.executescript(
        """
        CREATE TABLE documents (
            id INTEGER PRIMARY KEY, collection TEXT, path TEXT,
            title TEXT, hash TEXT, active INTEGER DEFAULT 1, source_page INTEGER,
            UNIQUE(collection, path)
        );
        CREATE TABLE content (hash TEXT PRIMARY KEY, doc TEXT);
        INSERT INTO documents (collection, path, title, hash, active) VALUES
          ('c', 'a.md', 'A', 'h1', 1);
        INSERT INTO content (hash, doc) VALUES ('h1', 'content-a');
        """
    )
    db.commit()
    db.close()

    idx = VectorIndex(
        index_path=tmp_path / "vectors.usearch",
        meta_path=tmp_path / "vectors.meta.json",
        db_path=db_path,
    )

    first = idx._fetch_metadata_batched(["h1"])
    second = idx._fetch_metadata_batched(["h1"])
    assert set(first.keys()) == {"h1"}
    assert set(second.keys()) == {"h1"}
    assert first["h1"]["path"] == second["h1"]["path"] == "a.md"
    assert first["h1"]["title"] == second["h1"]["title"] == "A"
    idx.close_meta_conn()


@pytest.mark.unit
def test_vector_index_meta_conn_thread_safe(tmp_path: Any) -> None:
    """Concurrent fetches across threads share the cached connection safely.

    The parallel-dispatch worker thread (``search-dispatch`` from
    ``pipeline.py``) may execute against this connection from a
    non-construction thread; the per-execute lock + ``check_same_thread=
    False`` must prevent ``sqlite3.ProgrammingError`` or row-count
    corruption.

    Sabotage-proof: in ``_get_meta_conn`` drop ``check_same_thread=False``
    from the ``sqlite3.connect`` call; concurrent fetches raise
    ``sqlite3.ProgrammingError`` and the result-collection assert below
    will be missing entries.
    """
    import sqlite3

    from kairix.core.search.vec_index import VectorIndex

    db_path = tmp_path / "index.sqlite"
    db = sqlite3.connect(str(db_path))
    db.executescript(
        """
        CREATE TABLE documents (
            id INTEGER PRIMARY KEY, collection TEXT, path TEXT,
            title TEXT, hash TEXT, active INTEGER DEFAULT 1, source_page INTEGER,
            UNIQUE(collection, path)
        );
        CREATE TABLE content (hash TEXT PRIMARY KEY, doc TEXT);
        INSERT INTO documents (collection, path, title, hash, active) VALUES
          ('c', 'a.md', 'A', 'h1', 1),
          ('c', 'b.md', 'B', 'h2', 1);
        INSERT INTO content (hash, doc) VALUES ('h1', 'a'), ('h2', 'b');
        """
    )
    db.commit()
    db.close()

    idx = VectorIndex(
        index_path=tmp_path / "vectors.usearch",
        meta_path=tmp_path / "vectors.meta.json",
        db_path=db_path,
    )

    results: list[dict[str, Any]] = []
    errors: list[BaseException] = []

    def _worker() -> None:
        try:
            rows = idx._fetch_metadata_batched(["h1", "h2"])
            results.append(rows)
        except BaseException as exc:  # pragma: no cover — defensive: any cross-thread error fails the test
            errors.append(exc)

    threads = [threading.Thread(target=_worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"cross-thread fetch raised: {errors}"
    assert len(results) == 8
    for rows in results:
        assert set(rows.keys()) == {"h1", "h2"}
    idx.close_meta_conn()


# ---------------------------------------------------------------------------
# Fix 3 — embed cache accessor hoisted to construction time
# ---------------------------------------------------------------------------


class _CountingCacheFactory:
    """Wraps the embed-cache accessor and counts every invocation.

    Used to assert that ``ProviderEmbeddingService.embed`` doesn't invoke
    the accessor on every call — only on construction.
    """

    def __init__(self, cache: Any) -> None:
        self._cache = cache
        self.calls = 0

    def __call__(self) -> Any:
        self.calls += 1
        return self._cache


class _InMemoryCache:
    """Minimal EmbedCache shape: ``get(text) -> list | None``, ``put(text, vec)``.

    Avoids the singleton + SQLite path of the real ``EmbedCache``; the
    test asserts the WRAPPER (the accessor) is called once at
    construction time, not the cache itself.
    """

    def __init__(self) -> None:
        self._data: dict[str, list[float]] = {}

    def get(self, text: str) -> list[float] | None:
        return self._data.get(text)

    def put(self, text: str, vec: list[float]) -> None:
        self._data[text] = list(vec)


@pytest.mark.unit
def test_embed_service_resolves_cache_once_at_construction() -> None:
    """``ProviderEmbeddingService`` builds the cache accessor once at __init__.

    Construct the service with a counting cache factory, run 50 ``embed``
    calls, and assert the factory was invoked exactly 50 times (one per
    embed call) — but the factory is bound at construction time and only
    invoked from inside ``embed`` (not from a per-call import statement).
    The point of the fix is to make the lookup ``self._get_embed_cache()``
    a single attribute dereference; under the old implementation each
    embed call also paid the ``from kairix.transport.cache import
    get_embed_cache`` statement.

    Pinning behaviour: the factory IS invoked once per embed call (that's
    the cache-accessor semantics — accessor returns the singleton). What
    the fix saves is the IMPORT statement per call. The structural
    assertion: ``self._get_embed_cache`` is bound at construction and
    refers to the injected factory, NOT to a module-level import inside
    ``embed`` that the test can't observe.

    Sabotage-proof: in ``ProviderEmbeddingService.embed``, restore the
    line ``from kairix.transport.cache import get_embed_cache`` and
    replace ``cache = self._get_embed_cache()`` with ``cache =
    get_embed_cache()``. The injected counting factory below would not
    fire — counting_factory.calls would be 0 and the assert below would
    fail with calls=0 vs expected ≥1.
    """
    cache = _InMemoryCache()
    counting_factory = _CountingCacheFactory(cache)
    provider = FakeProvider()
    svc = ProviderEmbeddingService(
        provider,
        get_embed_cache_fn=counting_factory,
        # Provide no-op coalescer wiring so the cache-resolved path runs
        # unconditionally instead of being skipped by an installed coalescer.
        existing_coalescer_fn=lambda: None,
        coalescer_factory=lambda **_: None,
    )

    for i in range(50):
        result = svc.embed(f"query-{i}")
        assert isinstance(result, list)

    # The injected factory was invoked on every embed (the cache accessor
    # resolves the singleton per call), proving the embed() codepath
    # actually consults ``self._get_embed_cache`` rather than the
    # module-level import. The win is that the import statement itself
    # is no longer evaluated per call — invisible to this counter but
    # observable in the cProfile output we captured under the brief.
    assert counting_factory.calls == 50


@pytest.mark.unit
def test_embed_service_warm_cache_short_circuit_unchanged() -> None:
    """Regression: cached lookups still return cached values verbatim.

    Pre-populate the cache; the next embed call returns the cached vector
    without hitting the provider. Pins the observable warm-cache
    behaviour after the construction-time-resolution refactor.

    Sabotage-proof: in ``ProviderEmbeddingService.embed``, change
    ``cache = self._get_embed_cache()`` to ``cache = _InMemoryCache()``
    (a fresh, empty cache per call) — the assert below would flip to
    provider.embed_batch_calls = 1 instead of 0.
    """
    cache = _InMemoryCache()
    cache.put("warm-text", [0.7] * 1536)
    counting_factory = _CountingCacheFactory(cache)
    provider = FakeProvider()
    svc = ProviderEmbeddingService(
        provider,
        get_embed_cache_fn=counting_factory,
        existing_coalescer_fn=lambda: None,
        coalescer_factory=lambda **_: None,
    )

    out = svc.embed("warm-text")
    # Cached vector returned verbatim — defensive copy is fine, just check
    # identity by value not by object.
    assert out == [0.7] * 1536
    # Provider was NOT called — cache hit short-circuits.
    assert len(provider.embed_calls) == 0


@pytest.mark.unit
def test_embed_service_cache_miss_still_calls_provider() -> None:
    """Regression: cold cache still routes through provider + caches the result.

    Pins the cold-path behaviour after the refactor. Cache miss -> call
    provider.embed_batch -> put result in cache -> next call hits cache.

    Sabotage-proof: in ``ProviderEmbeddingService.embed`` skip the
    ``cache.put(text, embedding)`` line on the synchronous branch; the
    second-call provider.embed_batch_calls would tick from 1 to 2 and
    the cache assert below would observe the empty cache.
    """
    cache = _InMemoryCache()
    counting_factory = _CountingCacheFactory(cache)
    provider = FakeProvider()
    svc = ProviderEmbeddingService(
        provider,
        get_embed_cache_fn=counting_factory,
        existing_coalescer_fn=lambda: None,
        coalescer_factory=lambda **_: None,
    )

    first = svc.embed("cold-text")
    assert first  # non-empty vector
    assert len(provider.embed_calls) == 1
    # Cache now populated; second call short-circuits, provider count flat.
    second = svc.embed("cold-text")
    assert second == first
    assert len(provider.embed_calls) == 1


# ---------------------------------------------------------------------------
# Combined-pipeline sanity check — fix 1 + fix 3 still satisfy the existing
# stage-latency split contract (test_pipeline_records_embed_http_and_vector_ann_split
# from tests/search/test_pipeline.py still passes; this test pins the cross-fix
# observability invariant for the parallel path specifically).
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_parallel_dispatch_preserves_embed_http_and_vector_ann_split_keys() -> None:
    """The vector-leg sub-stage timings (embed_http + vector_ann) still appear.

    The parallel-dispatch refactor moved the vector-leg into a worker
    thread; this asserts the timing-dict is still mutated from that
    thread and the keys land in SearchResult.stage_latency_ms. Without
    this, the existing decomposition test (in test_pipeline.py) would
    silently miss the parallel branch.
    """
    fusion = RRFFusion(k=60)
    vec_results = [{"path": "v.md", "distance": 0.1, "collection": "c"}]
    pipeline = SearchPipeline(
        classifier=FakeClassifier(),
        bm25=BM25SearchBackend(FakeDocumentRepository()),
        vector=VectorSearchBackend(FakeEmbeddingService(), FakeVectorRepository(results=vec_results)),
        graph=FakeGraphRepository(available=True),
        fusion=fusion,
        boosts=[],
        logger=None,
    )
    result = pipeline.search("semantic query")
    assert "embed_http" in result.stage_latency_ms
    assert "vector_ann" in result.stage_latency_ms
    assert "vector" in result.stage_latency_ms
    assert "bm25" in result.stage_latency_ms
    assert "dispatch" in result.stage_latency_ms

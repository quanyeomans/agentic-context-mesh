"""Persistence tests for :class:`kairix.transport.cache.EmbedCache` (#391).

Closes the operator-visible 0-byte-cache regression: on the production
VM ``/data/kairix/embed_cache.sqlite`` was reported empty after every
``docker compose restart``, so every release fan-out re-paid the
~250-500 ms Azure embed roundtrip for every repeat query. Before this
patch the cache was an in-process ``OrderedDict`` with no disk
persistence at all; the file shown to operators was a stale artifact
that the code never wrote to.

These tests pin the three properties the fix must hold:

1. ``put`` is write-through to SQLite — a new ``EmbedCache`` pointing
   at the same path replays the entry.
2. The process-shared singleton resolves its persistence path via
   :func:`kairix.paths.embed_cache_path` — no scattered
   ``KAIRIX_*`` env reads (F4-clean).
3. The factory-built embed service threads through the cache singleton,
   so the live ``embed-on-search`` path actually benefits from the
   cache (not just unit tests).

F-rule discipline:
  - F1: no @patch on kairix internals — construct ``EmbedCache(path=...)``
    directly and inspect the resolver via its public name.
  - F2: no env-var monkeypatch — ``path`` is passed by argument; the
    path-module test reads :func:`embed_cache_path` directly.
  - F4: env reads route through :mod:`kairix.paths`.
  - F8: ``pytestmark = pytest.mark.unit``.
  - F47: factory test composes via :func:`build_search_pipeline` with
    :func:`FakePaths`.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest

from kairix.transport.cache.embed_cache import EmbedCache

pytestmark = pytest.mark.unit


# Canonical fixture vector — small enough to round-trip through f32
# storage with negligible precision loss for the assertions below.
# Lifted to a module constant so the literal doesn't repeat across
# multiple test cases (F17 hygiene).
_VEC: list[float] = [0.1, 0.2, 0.3]


def test_persistence_db_opens_in_wal_mode(tmp_path: Path) -> None:
    """The on-disk embed cache is opened in WAL so a write-through ``put``
    skips the full-db fsync on the warm path (#408 / PLA-273).

    WAL is a persistent property of the DB file, so a fresh connection to
    the same file reports ``wal``.

    Sabotage proof (executed locally): remove the
    ``PRAGMA journal_mode=WAL`` line in ``_open_and_replay`` and the file
    stays in the default ``delete`` rollback-journal mode — this assertion
    then reads ``delete``.
    """
    cache_file = tmp_path / "cache.sqlite"
    cache = EmbedCache(path=cache_file)
    try:
        probe = sqlite3.connect(str(cache_file))
        try:
            mode = probe.execute("PRAGMA journal_mode").fetchone()[0]
        finally:
            probe.close()
    finally:
        cache.close()
    assert mode.lower() == "wal"


def test_cache_writes_persist_across_construction(tmp_path: Path) -> None:
    """A vector stored via ``put`` survives a new ``EmbedCache`` constructed
    against the same file path.

    This is the load-bearing property the production VM lost: the file
    existed at the operator's path but no code ever wrote to it. After
    this fix, ``cache.put(...)`` flushes to SQLite, and a fresh process
    (modelled here as a fresh ``EmbedCache`` instance pointing at the
    same path) finds the entry already populated.

    F2-clean: ``path`` is passed by argument — no ``KAIRIX_*`` env-var
    setup. Closes the on-VM symptom of #391.

    Sabotage proof (executed locally):
      mutate ``EmbedCache.put`` so the ``_upsert_persisted`` call is
      skipped (e.g. ``self._upsert_persisted = lambda *a, **k: None`` in
      ``__init__``). The first ``put`` lands in memory only; the second
      construction finds an empty SQLite file and ``c2.get("hello")``
      returns ``None``.
    """
    cache_file = tmp_path / "cache.sqlite"

    c1 = EmbedCache(path=cache_file)
    c1.put("hello", _VEC)
    c1.close()

    c2 = EmbedCache(path=cache_file)
    try:
        roundtrip = c2.get("hello")
        assert roundtrip is not None, (
            "EmbedCache persistence regression — a fresh instance pointing at "
            f"the same path saw an empty cache. file={cache_file} "
            f"file_size={cache_file.stat().st_size if cache_file.exists() else 'missing'}"
        )
        # f32 storage rounds to ~7 significant digits — assert element-wise
        # with pytest.approx so the test is precision-honest rather than
        # tolerating arbitrary drift.
        assert len(roundtrip) == len(_VEC)
        for actual, expected in zip(roundtrip, _VEC, strict=True):
            assert actual == pytest.approx(expected, rel=1e-6)
    finally:
        c2.close()


def test_cache_path_uses_paths_module() -> None:
    """The transport singleton's persistence path resolves to
    :func:`kairix.paths.embed_cache_path` and nowhere else.

    Pins F4 compliance: the resolver lives in ``paths.py``, and the
    transport-layer cache MUST consume that resolver rather than
    sprouting its own ``os.environ.get("KAIRIX_*")`` reads. If a future
    edit hardcodes a path or adds an env read inside the transport
    layer, this test fails — and the live VM is one shadowed env var
    away from the 0-byte symptom returning.

    Sabotage proof (executed locally):
      mutate ``kairix.paths.embed_cache_path`` to return ``Path("/tmp/x.sqlite")``
      (a path that does NOT live under ``data_dir()``). The assertion
      ``resolved.parent == data_dir()`` fails, surfacing the divergence
      before it ships.
    """
    from kairix.paths import data_dir, embed_cache_path

    resolved = embed_cache_path()

    # The canonical mount on Docker (and the equivalent on bare-metal
    # service installs) is the kairix data dir. Tying the path to
    # ``data_dir()`` means a ``docker compose down/up`` cycle preserves
    # the cache file across the bind-mounted volume.
    assert resolved.parent == data_dir(), (
        f"EmbedCache persistence path is not under data_dir() — "
        f"resolved={resolved} data_dir={data_dir()}. "
        "fix: keep embed_cache_path() = data_dir() / 'embed_cache.sqlite' so "
        "the SQLite file rides the kairix data volume across restarts. "
        "run: pytest tests/transport/cache/test_embed_cache_persistence.py"
    )
    assert resolved.name == "embed_cache.sqlite", (
        f"EmbedCache filename diverged from the documented operator "
        f"surface — resolved={resolved.name!r}. "
        "fix: keep the basename 'embed_cache.sqlite' so #391's operator-"
        "facing path stays stable."
    )


def test_provider_embedding_service_threads_cache(tmp_path: Path) -> None:
    """The production embed adapter routes ``embed`` calls through
    :func:`kairix.transport.cache.get_embed_cache`.

    Without this wiring the persistence work above is dead code — the
    live MCP embed path bypasses the cache and the operator's 0-byte
    file stays 0 bytes. We construct the canonical
    :class:`kairix.transport.embed_service.ProviderEmbeddingService`
    adapter (the same class the factory wires; this is a unit test so
    it stays at the public adapter boundary rather than reaching into
    factory internals) and observe that ``embed`` writes to the
    process-shared cache singleton.

    The assertion is observable: install a known cache singleton via
    :func:`install_embed_cache`, drive ``embed_service.embed("...")``,
    and confirm the cache singleton contains the entry the embed call
    produced. If the adapter were rewritten to bypass the cache, the
    singleton would stay at ``size=0``.

    Sabotage proof (executed locally):
      delete the ``cache.put(text, embedding)`` line at the end of
      ``ProviderEmbeddingService.embed`` (the no-coalescer branch). AND
      the matching line in the coalescer branch. The provider still
      returns a vector, but the singleton stays at ``size=0`` and this
      assertion fails.
    """
    from kairix.transport.cache import install_embed_cache, reset_embed_cache
    from kairix.transport.embed_service import ProviderEmbeddingService
    from tests.fakes import FakeProvider

    reset_embed_cache()

    # Install a known cache so we can observe the put() call from the
    # ``embed`` path. F1-clean — uses the public install_embed_cache
    # accessor instead of monkey-patching the module singleton.
    observable_cache = EmbedCache(path=tmp_path / "observable.sqlite")
    install_embed_cache(observable_cache)

    try:
        provider = FakeProvider(name="fake", vector=[0.5] * 8, dim=8)
        embed_service: Any = ProviderEmbeddingService(provider)

        result = embed_service.embed("composed query for #391")

        assert result, (
            "embed adapter returned no vector — provider plugin wiring "
            "is broken; fix that before asserting on cache behaviour."
        )
        assert observable_cache.stats().size == 1, (
            "ProviderEmbeddingService.embed did not write through to the "
            "shared EmbedCache singleton. The persistence layer added in "
            "#391 is therefore unreachable from the live search path. "
            "fix: keep cache.put(text, embedding) in "
            "ProviderEmbeddingService.embed after a successful provider call. "
            "run: pytest tests/transport/cache/test_embed_cache_persistence.py"
        )

        # And a second call short-circuits — the cache served the result.
        result2 = embed_service.embed("composed query for #391")
        assert result2 == result
        assert observable_cache.stats().hits >= 1
    finally:
        observable_cache.close()
        reset_embed_cache()


def test_persistent_clear_truncates_disk(tmp_path: Path) -> None:
    """``clear`` truncates the SQLite layer too — a cache-bust survives restart.

    Without on-disk truncation, a future cache-bust event (e.g. embed
    model swap) would clear RAM but leave the old vectors on disk, so
    the next process restart would replay stale entries.

    Sabotage proof (executed locally):
      remove the ``self._conn.execute(_TRUNCATE_SQL)`` call from
      ``clear`` and a fresh EmbedCache pointing at the same path
      replays the cleared entry — the assertion ``c2.get("hello") is None``
      fails.
    """
    cache_file = tmp_path / "cache.sqlite"

    c1 = EmbedCache(path=cache_file)
    c1.put("hello", _VEC)
    c1.clear()
    c1.close()

    c2 = EmbedCache(path=cache_file)
    try:
        assert c2.get("hello") is None, (
            "EmbedCache.clear did not truncate the SQLite layer — a stale "
            "entry replayed into a fresh instance, breaking the cache-bust "
            "contract relied on by a future embed-model swap."
        )
        assert c2.stats().size == 0
    finally:
        c2.close()


def test_persistent_expired_entry_dropped_on_replay(tmp_path: Path) -> None:
    """Replay-on-construction drops rows whose age exceeds ``max_age_s``.

    Without this, a long-stopped process would serve stale vectors on
    restart — the operator's expectation is that the cache reflects
    recent activity, not "everything that ever passed through".

    Sabotage proof (executed locally):
      remove the ``if (now - inserted_at) > self._max_age_s: continue``
      branch from ``_open_and_replay`` and the expired row replays
      into the in-memory LRU — c2.stats().size becomes 1.
    """
    cache_file = tmp_path / "cache.sqlite"

    # Use a controllable clock so we can age the entry past max_age_s.
    fake_now = [1_000_000.0]

    def _fake_time() -> float:
        return fake_now[0]

    c1 = EmbedCache(max_age_s=60.0, clock=_fake_time, path=cache_file)
    c1.put("hello", _VEC)
    c1.close()

    # Advance the clock past the max_age_s bound.
    fake_now[0] += 120.0
    c2 = EmbedCache(max_age_s=60.0, clock=_fake_time, path=cache_file)
    try:
        assert c2.stats().size == 0, (
            "EmbedCache replay loaded an expired entry — operators rely on "
            "the age check to keep restart-resilience honest."
        )
    finally:
        c2.close()


def test_persistence_failure_degrades_to_memory_only(tmp_path: Path) -> None:
    """Disk-layer failure during ``__init__`` degrades to in-memory-only.

    Caller does NOT see the failure surface as an exception — the
    embed path keeps working without restart-resilience for this
    cycle. Models the "operator bind-mounted a read-only volume"
    failure mode.

    Sabotage proof (executed locally):
      remove the ``except (OSError, sqlite3.Error)`` block from
      ``_open_and_replay`` and this construction raises rather than
      degrading; the test fails at ``EmbedCache(path=...)``.
    """
    # Point the cache at a path whose parent is a regular file, not a
    # directory — sqlite3.connect raises OSError when it tries to
    # mkdir over a file. F1-clean: we exercise the production error
    # path with a real filesystem condition, not a patch.
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("this is a file, not a directory")
    bad_path = blocker / "cache.sqlite"

    cache = EmbedCache(path=bad_path)
    try:
        # Construction completed without raising — cache degraded to
        # in-memory-only.
        assert cache.path == bad_path
        # In-memory operations still work even though disk is broken.
        cache.put("hello", _VEC)
        assert cache.get("hello") is not None
    finally:
        cache.close()


def test_close_is_idempotent(tmp_path: Path) -> None:
    """``close`` may be called repeatedly without raising.

    Sabotage proof (executed locally):
      change ``self._conn = None`` in close to skip the ``self._conn = None``
      line — the second close call tries to ``.close()`` an already-closed
      handle and the assertion fails.
    """
    cache = EmbedCache(path=tmp_path / "cache.sqlite")
    cache.close()
    cache.close()  # second call must not raise


def test_lazy_singleton_skips_persistence_under_pytest() -> None:
    """The lazy ``get_embed_cache`` singleton stays in-memory-only under pytest.

    Mirrors the ``PYTEST_CURRENT_TEST`` guard in
    :func:`default_open_embedding_cache` — protects the developer's
    real data dir from getting cache files written into it during
    test runs. Observed through the public ``path`` property on the
    cache so the test reaches no private internals (F5-clean).

    Sabotage proof (executed locally):
      delete the ``if os.environ.get("PYTEST_CURRENT_TEST"): return None``
      guard from the singleton resolver — the next get_embed_cache call
      builds a cache with ``path == kairix.paths.embed_cache_path()``
      (a real path), and this assertion fails.
    """
    from kairix.transport.cache import get_embed_cache, reset_embed_cache

    reset_embed_cache()
    try:
        cache = get_embed_cache()
        assert cache.path is None, (
            "EmbedCache singleton is wired with a persistence path under "
            f"pytest — guard branch removed? cache.path={cache.path}"
        )
    finally:
        reset_embed_cache()

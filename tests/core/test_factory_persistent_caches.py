"""Persistence tests for the factory-owned caches (#411 Phase 2).

Three caches gain on-disk persistence so a cold CLI process (with no
warm MCP to route through, per #411 Phase 1) rehydrates the LRU
contents from the previous warm process's disk file instead of paying
the ~2.3 s pipeline build + per-query embed + LLM-synthesis costs from
scratch.

This module pins the three load-bearing properties for the
:class:`QueryResultCache` + :class:`PipelineCacheMarker` halves:

1. ``put`` is write-through to SQLite — a NEW cache pointing at the
   same path replays the entry (no live in-process state shared).
2. Entries with an ``expires_at`` in the past are dropped on replay.
3. Entries written under one ``cfg_hash`` are not visible to a fresh
   cache constructed with a different ``cfg_hash`` (config-change
   invalidation).

F-rule discipline:
  - F1: no @patch on kairix internals — pass ``path`` + ``cfg_hash``
    by argument, drive expiry via the public ``clock`` seam.
  - F2: no env-var monkeypatch — path/cfg_hash are constructor args.
  - F4: env reads route through :mod:`kairix.paths`.
  - F8: ``pytestmark = pytest.mark.unit``.
  - F42: cache stats + payload classes are frozen dataclasses.
  - F47: marker integration test composes via
    :func:`kairix.core.factory.build_search_pipeline` with
    :func:`FakePaths`.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kairix.core.pipeline_cache_marker import PipelineCacheMarker, compute_cfg_hash
from kairix.core.search.intent import QueryIntent
from kairix.core.search.pipeline import SearchResult
from kairix.core.search.query_cache import (
    DEFAULT_DISK_MAX_AGE_S,
    QueryResultCache,
    make_cache_key,
)
from kairix.core.search.scope import Scope

pytestmark = pytest.mark.unit


def _sr(query: str = "agent-alpha briefing", latency: float = 100.0) -> SearchResult:
    """Build a small SearchResult fixture for the round-trip tests."""
    return SearchResult(
        query=query,
        intent=QueryIntent.SEMANTIC,
        latency_ms=latency,
        bm25_count=2,
    )


# ---------------------------------------------------------------------------
# QueryResultCache persistence
# ---------------------------------------------------------------------------


def test_query_cache_rehydrates_from_disk_in_fresh_process(tmp_path: Path) -> None:
    """A SearchResult stored via ``put`` survives a new ``QueryResultCache``
    constructed against the same file path.

    Models the cold-CLI scenario: warm MCP wrote rows; the MCP died;
    the next ``kairix search`` is a fresh process that should find the
    same cfg_hash rows on disk and serve from memory after replay —
    no pipeline rebuild needed for the query-hit path.

    Sabotage-proof (executed locally):
      Removed the ``self._upsert_persisted(key, now, value)`` call
      from ``QueryResultCache.put`` (the new-entry branch). Confirmed
      this test failed at the rehydration assertion ("rehydrated is
      None"), then restored.
    """
    cache_file = tmp_path / "query_cache.sqlite"
    key = make_cache_key("agent-alpha briefing", Scope.SHARED_AGENT, "agent-alpha", None)

    c1 = QueryResultCache(path=cache_file, cfg_hash="cfg-pinned")
    c1.put(key, _sr())
    c1.close()

    c2 = QueryResultCache(path=cache_file, cfg_hash="cfg-pinned")
    try:
        rehydrated = c2.get(key)
        assert rehydrated is not None, (
            "QueryResultCache persistence regression — a fresh instance "
            f"pointing at the same path saw an empty cache. file={cache_file} "
            f"file_size={cache_file.stat().st_size if cache_file.exists() else 'missing'}"
        )
        assert rehydrated.query == "agent-alpha briefing"
        assert rehydrated.intent == QueryIntent.SEMANTIC
        assert rehydrated.latency_ms == pytest.approx(100.0)
        assert rehydrated.bm25_count == 2
    finally:
        c2.close()


def test_query_cache_expires_entries_past_ttl(tmp_path: Path) -> None:
    """Rows whose ``expires_at`` is in the past are dropped on rehydrate.

    Without this drop, a long-stopped process would serve stale rows
    on restart — the operator's expectation is that the cache reflects
    recent activity.

    Sabotage-proof (executed locally):
      Removed the ``if expires_at <= now: ... continue`` branch from
      ``_open_and_replay``. Confirmed this test failed at the
      ``size == 0`` assertion (size became 1), then restored.
    """
    cache_file = tmp_path / "query_cache.sqlite"
    fake_now = [1_000_000.0]

    def _fake_time() -> float:
        return fake_now[0]

    key = make_cache_key("hello", Scope.SHARED_AGENT, "agent-alpha", None)

    # Use a short disk TTL so the test advances cleanly past it.
    c1 = QueryResultCache(
        path=cache_file,
        cfg_hash="cfg-x",
        clock=_fake_time,
        disk_max_age_s=60.0,
    )
    c1.put(key, _sr())
    c1.close()

    # Advance past the disk TTL.
    fake_now[0] += 120.0
    c2 = QueryResultCache(
        path=cache_file,
        cfg_hash="cfg-x",
        clock=_fake_time,
        disk_max_age_s=60.0,
    )
    try:
        assert c2.stats().size == 0, (
            "QueryResultCache rehydrate loaded an expired row — operators "
            "rely on the expires_at gate to keep restart-resilience honest."
        )
    finally:
        c2.close()


def test_query_cache_invalidates_on_cfg_hash_change(tmp_path: Path) -> None:
    """Rows written under one cfg_hash are invisible to a fresh cache
    constructed with a different cfg_hash.

    Models the config-change scenario: operator swaps the provider or
    fusion strategy; the persisted SearchResults are no longer valid;
    the rehydrate path skips them automatically.

    Sabotage-proof (executed locally):
      Replaced ``_SELECT_BY_CFG_SQL`` to drop its ``WHERE cfg_hash =
      ?`` clause (so every row replays regardless of cfg). Confirmed
      this test failed because ``c2.get(key)`` returned the cached
      SearchResult instead of None. Restored.
    """
    cache_file = tmp_path / "query_cache.sqlite"
    key = make_cache_key("hello", Scope.SHARED_AGENT, "agent-alpha", None)

    c1 = QueryResultCache(path=cache_file, cfg_hash="cfg-a")
    c1.put(key, _sr())
    c1.close()

    # Same path, different cfg_hash — rows from cfg-a should not appear.
    c2 = QueryResultCache(path=cache_file, cfg_hash="cfg-b")
    try:
        assert c2.get(key) is None, (
            "QueryResultCache served a row keyed under a different cfg_hash "
            "— config-change invalidation broken. fix: keep the WHERE "
            "cfg_hash = ? clause in _SELECT_BY_CFG_SQL."
        )
        assert c2.stats().size == 0
    finally:
        c2.close()


def test_query_cache_disk_ttl_default_is_one_hour() -> None:
    """The disk-tier TTL default matches the #411 brief (1 hour).

    The in-memory TTL stays at 5 minutes (operator-tunable per the
    KAIRIX_QUERY_CACHE_MAX_AGE_S env var); the disk tier is longer so
    cold CLI starts can hit recent rows even after the in-memory TTL
    expired in a separate warm process.

    Sabotage-proof (executed locally):
      Changed ``DEFAULT_DISK_MAX_AGE_S`` to 60.0. Confirmed this test
      failed (the constant didn't match 3600.0). Restored.
    """
    assert DEFAULT_DISK_MAX_AGE_S == pytest.approx(3600.0), (
        f"DEFAULT_DISK_MAX_AGE_S diverged from the #411 Phase 2 brief "
        f"(1 hour disk TTL) — got {DEFAULT_DISK_MAX_AGE_S}. "
        "fix: keep the 3600.0-second disk TTL or update the brief with rationale."
    )


# ---------------------------------------------------------------------------
# PipelineCacheMarker
# ---------------------------------------------------------------------------


def test_pipeline_cache_marker_rehydrates_from_disk_in_fresh_process(tmp_path: Path) -> None:
    """A recorded marker survives a new ``PipelineCacheMarker`` instance.

    Models the cold-CLI scenario: warm MCP wrote the marker after
    building the pipeline; the next CLI start reads the same file and
    sees the recorded cfg_hash.

    Sabotage-proof (executed locally):
      Removed the ``self._conn.execute(_UPSERT_SQL, ...)`` line from
      ``PipelineCacheMarker.record``. Confirmed this test failed at
      ``rehydrated_hash == 'hash-pinned'`` (last() returned None).
      Restored.
    """
    marker_file = tmp_path / "pipeline_cache.sqlite"

    m1 = PipelineCacheMarker(path=marker_file)
    m1.record("hash-pinned", 1_234_567.0)
    m1.close()

    m2 = PipelineCacheMarker(path=marker_file)
    try:
        last = m2.last()
        assert last is not None, (
            "PipelineCacheMarker persistence regression — a fresh instance "
            f"pointing at {marker_file} saw no recorded build."
        )
        rehydrated_hash, built_at = last
        assert rehydrated_hash == "hash-pinned"
        assert built_at == pytest.approx(1_234_567.0)
    finally:
        m2.close()


def test_pipeline_cache_marker_last_returns_most_recent(tmp_path: Path) -> None:
    """``last()`` returns the highest ``built_at`` row across cfg_hashes.

    The marker accumulates one row per cfg_hash; the most-recent
    write wins so a config-change cycle (cfg-a → cfg-b → cfg-a)
    surfaces the latest build.

    Sabotage-proof (executed locally):
      Changed ``ORDER BY built_at DESC`` to ``ORDER BY built_at ASC``
      in _SELECT_LAST_SQL. Confirmed this test failed at
      ``last[0] == 'hash-second'`` (the assertion got 'hash-first'
      because the ASC query returned the oldest row). Restored.
    """
    marker_file = tmp_path / "pipeline_cache.sqlite"
    m = PipelineCacheMarker(path=marker_file)
    try:
        m.record("hash-first", 1_000.0)
        m.record("hash-second", 2_000.0)
        last = m.last()
        assert last is not None
        assert last[0] == "hash-second"
        assert last[1] == pytest.approx(2_000.0)
    finally:
        m.close()


def test_compute_cfg_hash_is_stable_across_calls() -> None:
    """The same dataclass value hashes to the same string each call.

    Property the cfg-hash invalidation relies on: a cold process
    re-resolving the same config must produce the same cfg_hash so
    the rehydrate-by-cfg lookup hits.

    Sabotage-proof (executed locally):
      Mutated ``compute_cfg_hash`` to return ``str(uuid.uuid4())``.
      Confirmed this test failed because two calls produced different
      hashes. Restored.
    """
    from dataclasses import replace

    from kairix.core.search.config import RetrievalConfig

    cfg = replace(RetrievalConfig.defaults(), provider="fake")
    h1 = compute_cfg_hash(cfg)
    h2 = compute_cfg_hash(cfg)
    assert h1 == h2, "compute_cfg_hash is not deterministic — cfg-rehydrate path will miss every time."
    assert h1, "compute_cfg_hash returned empty string for a real dataclass — sentinel collapsed real cfgs."


def test_compute_cfg_hash_changes_with_config_change() -> None:
    """Different config values hash to different strings.

    Property the cfg-hash invalidation relies on the other direction:
    a config change must produce a new hash so the rehydrate-by-cfg
    lookup misses (invalidation).

    Sabotage-proof (executed locally):
      Replaced ``compute_cfg_hash`` body with ``return 'fixed'`` for
      every input. Confirmed this test failed at the inequality
      assertion. Restored.
    """
    from dataclasses import replace

    from kairix.core.search.config import RetrievalConfig

    cfg_a = replace(RetrievalConfig.defaults(), provider="fake")
    cfg_b = replace(RetrievalConfig.defaults(), provider="other")
    assert compute_cfg_hash(cfg_a) != compute_cfg_hash(cfg_b), (
        "compute_cfg_hash collapsed two different configs to the same hash — config-change invalidation broken."
    )


# ---------------------------------------------------------------------------
# QueryResultCache — defensive + cross-cutting paths
# These exercise behaviour that the happy-path tests above don't reach:
# schema drift, encoding rejection, replay-time drops, disk eviction.
# ---------------------------------------------------------------------------


def test_query_cache_drops_corrupt_payload_rows_at_replay(tmp_path: Path) -> None:
    """A row whose payload no longer decodes (e.g., schema change between
    versions, or an operator manually edited the file) is dropped on
    replay instead of crashing the cache constructor.

    Sabotage-proof: remove the ``except (ValueError, KeyError, TypeError)``
    clause around ``_decode_search_result`` in ``_open_and_replay``; the
    new cache construction raises ValueError instead of degrading.
    """
    import sqlite3

    cache_file = tmp_path / "query_cache.sqlite"
    # Build a valid cache first so the schema exists.
    c1 = QueryResultCache(path=cache_file, cfg_hash="cfg-x")
    key = make_cache_key("seed", Scope.SHARED_AGENT, "agent-alpha", None)
    c1.put(key, _sr())
    c1.close()

    # Corrupt one row's payload directly.
    conn = sqlite3.connect(str(cache_file))
    try:
        conn.execute("UPDATE query_cache SET payload = 'not-valid-json{'")
        conn.commit()
    finally:
        conn.close()

    # Fresh cache should rehydrate without crashing; corrupt row dropped.
    c2 = QueryResultCache(path=cache_file, cfg_hash="cfg-x")
    try:
        assert c2.stats().size == 0, "corrupt payload row should be dropped at replay"
    finally:
        c2.close()


def test_query_cache_schema_version_mismatch_truncates_and_recreates(tmp_path: Path) -> None:
    """When the on-disk schema_version differs from the running one (e.g.,
    after a kairix version bump), the cache truncates + recreates instead
    of failing to open. Operator never has to manually delete the file.

    Sabotage-proof: remove the ``with self._conn: TRUNCATE_SQL`` block
    from ``_check_schema_version_locked``; pre-existing rows persist
    across the version boundary and the next ``get`` returns stale data.
    """
    import sqlite3

    cache_file = tmp_path / "query_cache.sqlite"
    # Seed a cache with a pretend-old schema_version row.
    c1 = QueryResultCache(path=cache_file, cfg_hash="cfg-x")
    key = make_cache_key("ancient", Scope.SHARED_AGENT, "agent-alpha", None)
    c1.put(key, _sr())
    c1.close()

    # Bump the on-disk schema_version to mimic an upgrade discrepancy.
    conn = sqlite3.connect(str(cache_file))
    try:
        conn.execute(
            "INSERT OR REPLACE INTO query_cache_meta (key, value) VALUES (?, ?)",
            ("schema_version", "ancient-v0"),
        )
        conn.commit()
    finally:
        conn.close()

    # Fresh cache: schema mismatch → truncate; row gone.
    c2 = QueryResultCache(path=cache_file, cfg_hash="cfg-x")
    try:
        assert c2.get(key) is None, "schema mismatch should have truncated the cache"
        assert c2.stats().size == 0
    finally:
        c2.close()


def test_query_cache_clear_truncates_disk_too(tmp_path: Path) -> None:
    """``clear()`` removes rows from both the in-memory LRU AND the disk file.

    Without this, an operator who calls ``clear()`` mid-process would see
    the in-memory cache empty but the next cold start would replay the
    old rows — surprising. ``clear`` must purge both layers.

    Sabotage-proof: remove the ``self._conn.execute(_TRUNCATE_SQL)`` call
    inside ``clear``; a cold-restart cache still has the old rows.
    """
    cache_file = tmp_path / "query_cache.sqlite"
    key = make_cache_key("vanish", Scope.SHARED_AGENT, "agent-alpha", None)

    c1 = QueryResultCache(path=cache_file, cfg_hash="cfg-x")
    c1.put(key, _sr())
    c1.clear()
    c1.close()

    c2 = QueryResultCache(path=cache_file, cfg_hash="cfg-x")
    try:
        assert c2.get(key) is None, "clear() must truncate the disk layer too"
        assert c2.stats().size == 0
    finally:
        c2.close()


def test_query_cache_evicts_persisted_row_on_lru_overflow(tmp_path: Path) -> None:
    """When the in-memory LRU evicts an entry (because max_entries cap was
    hit), the corresponding disk row is deleted too. Without this, the
    on-disk file would grow unbounded across the in-memory eviction
    cycles.

    Sabotage-proof: remove the ``self._delete_persisted_for_in_memory_key(evicted_key)``
    call inside ``put``'s eviction branch; the next cold start rehydrates
    a row that the in-memory eviction had logically dropped.
    """
    cache_file = tmp_path / "query_cache.sqlite"
    # Tight cap so 2 puts trigger eviction.
    c1 = QueryResultCache(path=cache_file, cfg_hash="cfg-x", max_entries=1)
    k1 = make_cache_key("first", Scope.SHARED_AGENT, "agent-alpha", None)
    k2 = make_cache_key("second", Scope.SHARED_AGENT, "agent-alpha", None)
    c1.put(k1, _sr(query="first"))
    c1.put(k2, _sr(query="second"))  # evicts k1
    c1.close()

    c2 = QueryResultCache(path=cache_file, cfg_hash="cfg-x", max_entries=10)
    try:
        # Only the second key should rehydrate.
        assert c2.get(k1) is None, "evicted in-memory key should be evicted from disk too"
        assert c2.get(k2) is not None
    finally:
        c2.close()


def test_query_cache_degrades_gracefully_on_unopenable_path(tmp_path: Path) -> None:
    """If the cache file can't be opened (parent directory exists as a
    file, disk full, permissions), the cache degrades to in-memory-only
    instead of crashing the search pipeline.

    Sabotage-proof: remove the ``except (OSError, sqlite3.Error)`` clause
    around the open block in ``_open_and_replay``; cache construction
    raises and breaks every consumer.
    """
    # Make a file where the directory is supposed to be — sqlite3.connect
    # then fails because the parent isn't a directory.
    blocker = tmp_path / "blocker"
    blocker.write_text("not a dir")
    cache_file = blocker / "child" / "query_cache.sqlite"

    # Must not raise; cache should construct in-memory-only.
    c = QueryResultCache(path=cache_file, cfg_hash="cfg-x")
    try:
        key = make_cache_key("in-mem-only", Scope.SHARED_AGENT, "agent-alpha", None)
        c.put(key, _sr())
        # The in-memory path still works — no degradation of correctness.
        assert c.get(key) is not None
    finally:
        c.close()


# ---------------------------------------------------------------------------
# PipelineCacheMarker — defensive paths
# Closes the gap on the 'marker file unreachable' branches that the
# happy-path tests don't exercise.
# ---------------------------------------------------------------------------


def test_compute_cfg_hash_returns_empty_for_non_dataclass() -> None:
    """A non-dataclass input collapses to the empty cfg_hash.

    The caller (cold-CLI accessor) hands compute_cfg_hash whatever it
    has — sometimes None, sometimes a primitive when the test wires no
    config. compute_cfg_hash must not raise; the empty hash is a
    'cfg-scoping-disabled' sentinel the cache understands.

    Sabotage-proof: remove the ``if not is_dataclass(cfg) ...: return ""``
    guard; this test fails when asdict() raises TypeError on a string.
    """
    assert compute_cfg_hash("not a dataclass") == ""
    assert compute_cfg_hash(None) == ""
    assert compute_cfg_hash(42) == ""


def test_compute_cfg_hash_returns_empty_on_serialisation_error() -> None:
    """A dataclass whose ``asdict`` raises produces the empty hash.

    A field-with-unhashable-value (e.g., a live socket) shouldn't crash
    the cache; the empty hash means cfg-scoping-disabled for this build.

    Sabotage-proof: remove the ``except (TypeError, ValueError)`` block;
    this test raises through compute_cfg_hash instead of returning "".
    """
    from dataclasses import dataclass

    @dataclass
    class _Boom:
        """Dataclass whose ``asdict`` will raise via the recursive walk."""

        x: object

    class _Unhashable:
        """Object whose repr raises — _normalise / repr() blow up on it."""

        def __repr__(self) -> str:
            raise ValueError("repr boom")

    assert compute_cfg_hash(_Boom(x=_Unhashable())) == ""


def test_pipeline_cache_marker_path_property_round_trips(tmp_path: Path) -> None:
    """``path`` returns the constructor-provided path verbatim.

    Operators reading from ``probe caches`` need the actual file path
    to clear/inspect; the property is the canonical read surface.

    Sabotage-proof: change ``return self._path`` to ``return None``;
    this test fails when path != cache_file.
    """
    cache_file = tmp_path / "pipeline_marker.sqlite"
    marker = PipelineCacheMarker(path=cache_file)
    try:
        assert marker.path == cache_file
    finally:
        marker.close()


def test_pipeline_cache_marker_no_path_returns_none_for_last() -> None:
    """When constructed with ``path=None``, ``last()`` returns None.

    Models the in-memory-only mode used by Python-API consumers that
    don't have a writable data dir. Recording is a no-op; last() reports
    nothing was recorded.

    Sabotage-proof: change ``_ensure_open`` to construct a file at a
    default path when ``self._path is None``; this test fails because
    last() then returns a row instead of None.
    """
    marker = PipelineCacheMarker(path=None)
    try:
        marker.record("cfg-irrelevant", 100.0)
        assert marker.last() is None
        assert marker.path is None
    finally:
        marker.close()


def test_pipeline_cache_marker_degrades_on_unopenable_path(tmp_path: Path) -> None:
    """If the file can't be opened (parent is a file, not a dir),
    record + last become no-ops instead of raising.

    Mirrors the QueryResultCache degradation contract — cache failure
    must never break the pipeline.

    Sabotage-proof: remove the ``except (OSError, sqlite3.Error)``
    block in ``_ensure_open``; this test raises through PipelineCacheMarker
    construction at the first record/last call.
    """
    blocker = tmp_path / "blocker"
    blocker.write_text("not a dir")
    cache_file = blocker / "child" / "pipeline_marker.sqlite"

    marker = PipelineCacheMarker(path=cache_file)
    try:
        marker.record("cfg-x", 100.0)  # must not raise
        assert marker.last() is None  # degraded — file never opened
    finally:
        marker.close()


def test_pipeline_cache_marker_last_returns_none_when_empty(tmp_path: Path) -> None:
    """A freshly-constructed marker with no recorded rows returns None.

    Cold start with no prior pipeline build → empty marker → caller
    treats this as 'no cfg_hash known' (collapse to empty-string scope).

    Sabotage-proof: change ``_SELECT_LAST_SQL`` to omit the LIMIT 1 or
    swap the ORDER BY clause; this test still passes because the table
    is empty, so use a slightly different proof: change ``last()`` to
    return ``("forced", 0.0)`` when row is None; this test fails because
    last() returns the synthetic row.
    """
    cache_file = tmp_path / "pipeline_marker_empty.sqlite"
    marker = PipelineCacheMarker(path=cache_file)
    try:
        assert marker.last() is None
    finally:
        marker.close()


# ---------------------------------------------------------------------------
# QueryResultCache — encoder / decoder / property defensive paths
# ---------------------------------------------------------------------------


def test_query_cache_path_and_cfg_hash_properties_round_trip(tmp_path: Path) -> None:
    """``path`` + ``cfg_hash`` return the constructor values verbatim.

    Operators reading from ``kairix caches`` need these to identify
    which file backs the in-memory state.

    Sabotage-proof: change ``return self._cfg_hash`` to ``return ""``;
    this test fails when cfg_hash != "cfg-pinned".
    """
    cache_file = tmp_path / "qc.sqlite"
    cache = QueryResultCache(path=cache_file, cfg_hash="cfg-pinned")
    try:
        assert cache.path == cache_file
        assert cache.cfg_hash == "cfg-pinned"
    finally:
        cache.close()


def test_query_cache_replay_drops_non_object_payload(tmp_path: Path) -> None:
    """A row whose JSON payload decodes to a list (not an object) is
    dropped at replay instead of crashing the constructor.

    Models forward-compat / corruption: a row written by a different
    encoder shape, or an operator who hand-edited the file. Tested via
    the public surface — raw sqlite3 injection of the bad payload then
    a fresh ``QueryResultCache`` construction.

    Sabotage-proof: remove the ``except (ValueError, KeyError, TypeError)``
    block in ``_decode_or_drop``; this test fails because cache
    construction then raises ValueError instead of degrading.
    """
    import sqlite3

    cache_file = tmp_path / "qc_nonobj.sqlite"
    c1 = QueryResultCache(path=cache_file, cfg_hash="cfg-x")
    c1.put(make_cache_key("seed", Scope.SHARED_AGENT, "agent-alpha", None), _sr())
    c1.close()

    # Inject a JSON-list payload (decodes but isn't an object).
    conn = sqlite3.connect(str(cache_file))
    try:
        conn.execute('UPDATE query_cache SET payload = \'["list", "not", "object"]\'')
        conn.commit()
    finally:
        conn.close()

    c2 = QueryResultCache(path=cache_file, cfg_hash="cfg-x")
    try:
        assert c2.stats().size == 0
    finally:
        c2.close()


def test_query_cache_replay_skips_malformed_inner_result_rows(tmp_path: Path) -> None:
    """Forward-compat: BudgetedResult rows that aren't dicts get skipped
    cleanly; the SearchResult-shell still rehydrates with empty results.

    Tested through the public surface: write a valid row, then mutate
    the payload to contain a non-dict ``results`` element, then
    reconstruct.

    Sabotage-proof: remove the ``if not isinstance(r, dict): continue``
    guard in the decoder; this test fails because cache construction
    raises through to the constructor instead of degrading.
    """
    import sqlite3

    cache_file = tmp_path / "qc_malformed.sqlite"
    c1 = QueryResultCache(path=cache_file, cfg_hash="cfg-y")
    c1.put(make_cache_key("hi", Scope.SHARED_AGENT, "agent-alpha", None), _sr(query="hi"))
    c1.close()

    bad_payload = (
        '{"query": "hi", "intent": "semantic", "results": ["not a dict", {"result": "also not a dict", "tier": "L2"}]}'
    )
    conn = sqlite3.connect(str(cache_file))
    try:
        conn.execute("UPDATE query_cache SET payload = ?", (bad_payload,))
        conn.commit()
    finally:
        conn.close()

    c2 = QueryResultCache(path=cache_file, cfg_hash="cfg-y")
    try:
        rehydrated = c2.get(make_cache_key("hi", Scope.SHARED_AGENT, "agent-alpha", None))
        assert rehydrated is not None
        assert rehydrated.query == "hi"
        # Both malformed rows skipped — results list is empty.
        assert list(rehydrated.results) == []
    finally:
        c2.close()


def test_query_cache_replay_drops_unknown_inner_dataclass_fields(tmp_path: Path) -> None:
    """A payload with extra fields the current schema doesn't know about
    still rehydrates — extra keys are silently dropped.

    Forward-compat through the public surface: write a valid row, mutate
    the payload to include an unknown ``future_field``, reconstruct.

    Sabotage-proof: remove the field-name filter in the dataclass
    hydrator; this test fails because cache construction raises a
    TypeError on the unknown kwarg.
    """
    import sqlite3

    cache_file = tmp_path / "qc_unknown_field.sqlite"
    c1 = QueryResultCache(path=cache_file, cfg_hash="cfg-z")
    c1.put(make_cache_key("hi", Scope.SHARED_AGENT, "agent-alpha", None), _sr(query="hi"))
    c1.close()

    inner_with_future_field = '{"query": "hi", "intent": "semantic", "future_field_not_in_current_schema": "ignored"}'
    conn = sqlite3.connect(str(cache_file))
    try:
        conn.execute("UPDATE query_cache SET payload = ?", (inner_with_future_field,))
        conn.commit()
    finally:
        conn.close()

    c2 = QueryResultCache(path=cache_file, cfg_hash="cfg-z")
    try:
        rehydrated = c2.get(make_cache_key("hi", Scope.SHARED_AGENT, "agent-alpha", None))
        assert rehydrated is not None
        assert rehydrated.query == "hi"
    finally:
        c2.close()


def test_query_cache_replay_evicts_when_disk_exceeds_max(tmp_path: Path) -> None:
    """When the on-disk file holds more rows than ``max_entries``, the
    replay loop evicts oldest until the in-memory cap is honoured.

    Operators who shrink ``max_entries`` between restarts (or who
    backfill the file from a previous larger-cap process) should not
    end up with an over-cap in-memory LRU.

    Sabotage-proof: remove the eviction block inside _open_and_replay
    (``if len(self._entries) > self._max_entries``); this test fails
    because c2.stats().size exceeds max_entries=1.
    """
    cache_file = tmp_path / "qc_replay_evict.sqlite"
    c1 = QueryResultCache(path=cache_file, cfg_hash="cfg-z", max_entries=10)
    for i in range(5):
        c1.put(make_cache_key(f"q{i}", Scope.SHARED_AGENT, "agent-alpha", None), _sr(query=f"q{i}"))
    c1.close()

    c2 = QueryResultCache(path=cache_file, cfg_hash="cfg-z", max_entries=1)
    try:
        assert c2.stats().size <= 1, "replay should have evicted to honour the smaller max_entries"
    finally:
        c2.close()


# ---------------------------------------------------------------------------
# PrepSummaryCache — defensive paths
# ---------------------------------------------------------------------------


def test_prep_cache_path_and_cfg_hash_properties_round_trip(tmp_path: Path) -> None:
    """``path`` + ``cfg_hash`` return constructor values verbatim.

    Sabotage-proof: change either property to return a different value;
    this test fails when round-trip mismatches.
    """
    from kairix.core.search.prep_summary_cache import PrepSummaryCache

    cache_file = tmp_path / "prep_cache.sqlite"
    cache = PrepSummaryCache(path=cache_file, cfg_hash="cfg-prep-x")
    try:
        assert cache.path == cache_file
        assert cache.cfg_hash == "cfg-prep-x"
    finally:
        cache.close()


def test_prep_cache_degrades_on_unopenable_path(tmp_path: Path) -> None:
    """If the file can't be opened, the cache degrades to in-memory-only.

    Sabotage-proof: remove the ``except (OSError, sqlite3.Error)``
    block in _open_and_replay; this test raises through PrepSummaryCache
    construction.
    """
    from kairix.core.search.prep_summary_cache import (
        PrepSummaryCache,
        make_prep_cache_key,
    )

    blocker = tmp_path / "prep_blocker"
    blocker.write_text("not a dir")
    cache_file = blocker / "child" / "prep_cache.sqlite"

    c = PrepSummaryCache(path=cache_file, cfg_hash="cfg-x")
    try:
        key = make_prep_cache_key("q", "l0", "ctx")
        c.put(key, "summary")
        assert c.get(key) == "summary"  # in-memory layer still works
    finally:
        c.close()


def test_prep_cache_put_updates_existing_key_in_place(tmp_path: Path) -> None:
    """Re-puts under the same key refresh the value + timestamp without
    evicting + reinserting (which would inflate the eviction counter).

    Sabotage-proof: remove the ``if key in self._entries: ... return``
    early-return; this test fails because the eviction counter then
    drifts upward on the same-key update.
    """
    from kairix.core.search.prep_summary_cache import (
        PrepSummaryCache,
        make_prep_cache_key,
    )

    cache_file = tmp_path / "prep_put_update.sqlite"
    key = make_prep_cache_key("q", "l0", "ctx")
    c = PrepSummaryCache(path=cache_file, cfg_hash="cfg-x")
    try:
        c.put(key, "summary v1")
        c.put(key, "summary v2")
        assert c.get(key) == "summary v2"
        assert c.stats().evictions == 0, "same-key update should not register as eviction"
    finally:
        c.close()


def test_prep_cache_clear_truncates_disk(tmp_path: Path) -> None:
    """``clear()`` removes rows from disk too, not just in-memory.

    Sabotage-proof: remove the ``self._conn.execute(_TRUNCATE_SQL)``
    call inside ``clear``; this test fails because c2 (a fresh cache)
    finds the old row that should have been cleared.
    """
    from kairix.core.search.prep_summary_cache import (
        PrepSummaryCache,
        make_prep_cache_key,
    )

    cache_file = tmp_path / "prep_clear.sqlite"
    key = make_prep_cache_key("q", "l0", "ctx")

    c1 = PrepSummaryCache(path=cache_file, cfg_hash="cfg-x")
    c1.put(key, "summary")
    c1.clear()
    c1.close()

    c2 = PrepSummaryCache(path=cache_file, cfg_hash="cfg-x")
    try:
        assert c2.get(key) is None
        assert c2.stats().size == 0
    finally:
        c2.close()


def test_prep_cache_expiry_invokes_delete_persisted(tmp_path: Path) -> None:
    """Expired entries trigger ``_delete_persisted`` on the get() path.

    Tests the cleanup branch that keeps the disk file from growing
    unbounded with stale rows that the in-memory layer has already
    forgotten about.

    Sabotage-proof: remove the ``self._delete_persisted(key)`` call
    inside ``get``'s expiry branch; this test fails because the next
    cold cache rehydrates the expired row.
    """
    from kairix.core.search.prep_summary_cache import (
        PrepSummaryCache,
        make_prep_cache_key,
    )

    cache_file = tmp_path / "prep_expiry_delete.sqlite"
    fake_now = [1_000_000.0]

    def _fake_time() -> float:
        return fake_now[0]

    key = make_prep_cache_key("q", "l0", "ctx")
    c1 = PrepSummaryCache(
        path=cache_file,
        cfg_hash="cfg-x",
        clock=_fake_time,
        max_age_s=60.0,
        disk_max_age_s=7200.0,  # disk TTL won't fire; only in-memory TTL.
    )
    c1.put(key, "summary")
    fake_now[0] += 120.0  # past in-memory TTL but inside disk TTL.
    assert c1.get(key) is None  # triggers _delete_persisted.
    c1.close()

    c2 = PrepSummaryCache(path=cache_file, cfg_hash="cfg-x", clock=_fake_time)
    try:
        assert c2.get(key) is None, "expired row should have been deleted from disk by get()"
    finally:
        c2.close()

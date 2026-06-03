"""Concurrent-resolve regression test for the topology_v2 collection resolver (#399).

Production trace (logged at ``run_search`` line 378) showed a
``sqlite3.InterfaceError: bad parameter or other API misuse`` race
under load on the MCP search path. Root cause: the v2 collection
resolver is wired with a single ``sqlite3.Connection`` opened with
``check_same_thread=False`` (factory.py R0 fix), and the
:class:`ScopeCollectionCache` wrapper deliberately drops its lock
around the inner ``resolve()`` call to keep cache reads non-blocking.
The two design choices combined exposed the bare connection to
concurrent ``execute(...)`` calls — Python's sqlite3 driver clobbers
the cursor's internal state mid-fetch and raises InterfaceError.

This test drives the public
:func:`kairix.core.factory.build_collection_resolver` boundary with the
``topology_v2_collection_resolver`` flag ON, then fans 10 threads
through ``.resolve(...)`` against the same resolver. Pre-fix this
reliably surfaces the InterfaceError; post-fix every thread returns
the same superset cleanly.

F47-clean — composes via ``build_collection_resolver`` + the seeded
SQLite schema + :class:`FakeFeatureFlagResolver`; no direct construction
of the resolver, no monkey-patches, no env-var manipulation.
"""

from __future__ import annotations

import concurrent.futures
import sqlite3
from pathlib import Path

import pytest

from kairix.core.db.schema import create_schema
from kairix.core.factory import build_collection_resolver
from kairix.core.search.scope import Scope
from tests.fakes import FakeFeatureFlagResolver

pytestmark = pytest.mark.integration


_NUM_ACTORS = 40
_THREADS = 20


def _seeded_db_path(tmp_path: Path) -> Path:
    """Seed a sqlite DB with N distinct actors each owning four scope entries.

    Multiple actor profiles force the :class:`ScopeCollectionCache` to
    miss on its first call for each ``(agent, scope)`` key — concurrent
    misses are the path that exposes the shared-connection race.
    """
    db_path = tmp_path / "index.sqlite"
    db = sqlite3.connect(str(db_path), timeout=10.0)
    try:
        create_schema(db, dims=4)
        now = "2026-06-01T00:00:00Z"
        for actor_index in range(_NUM_ACTORS):
            actor_id = f"agent-{actor_index:02d}"
            cur = db.execute(
                "INSERT INTO topology_scope_profiles "
                "(actor_id, actor_kind, inherits_from_json, created_at, updated_at) "
                "VALUES (?, 'agent', '[]', ?, ?)",
                (actor_id, now, now),
            )
            profile_id = cur.lastrowid
            for collection_name, can_read, can_write in [
                ("sharepoint-all", 1, 0),
                ("obsidian-all", 1, 0),
                ("reflib", 1, 0),
                (f"{actor_id}-memory", 1, 1),
            ]:
                db.execute(
                    "INSERT INTO topology_scope_entries "
                    "(scope_profile_id, collection_name, can_read, can_write, max_sensitivity) "
                    "VALUES (?, ?, ?, ?, 'internal')",
                    (profile_id, collection_name, can_read, can_write),
                )
        db.commit()
    finally:
        db.close()
    return db_path


def test_concurrent_resolve_does_not_raise_interface_error(tmp_path: Path) -> None:
    """Ten threads resolve through one cached v2 resolver — none should raise.

    Pre-fix behaviour: the bare ``sqlite3.Connection`` shared across the
    resolver's call sites is hit concurrently by the executor threads,
    and at least one call raises
    ``sqlite3.InterfaceError: bad parameter or other API misuse``.

    Post-fix: every call returns the seeded superset for its actor; no
    exception escapes. The threads vary the actor so each call is a
    cache miss for ScopeCollectionCache and exercises the inner
    resolver's SQLite path on every iteration of the first round.

    Sabotage proof (recorded with the commit): revert the
    ``_SerializingSqliteConnection`` wrapping in
    ``kairix/core/factory.py:_build_topology_v2_collection_resolver``
    so the resolver receives the raw ``sqlite3.Connection``, then rerun
    this test — at least one of the ten threads raises InterfaceError
    and the ``concurrent.futures.Executor.result()`` re-raises here.
    """
    db_path = _seeded_db_path(tmp_path)
    flag_resolver = FakeFeatureFlagResolver().with_flag("topology_v2_collection_resolver", True)

    resolver = build_collection_resolver(db_path=db_path, flag_reader=flag_resolver.get)

    def _one_call(actor_index: int) -> tuple[str, list[str] | None]:
        actor_id = f"agent-{actor_index % _NUM_ACTORS:02d}"
        # Each thread issues several resolves so the executor stays busy
        # long enough for two cursor-active windows to overlap. Mixing
        # SHARED_AGENT (cache key A) and SHARED (cache key B) per actor
        # widens the cache-miss surface.
        results: list[str] = []
        for scope in (Scope.SHARED_AGENT, Scope.SHARED, Scope.SHARED_AGENT):
            out = resolver.resolve(agent=actor_id, scope=scope)
            if out:
                results.extend(out)
        return actor_id, results

    # Submit many more tasks than actors so every actor is hit by
    # several threads simultaneously; the first call per ``(agent,
    # scope)`` pair is a cache miss, and pre-fix the overlapping miss
    # windows are where the InterfaceError surfaces.
    with concurrent.futures.ThreadPoolExecutor(max_workers=_THREADS) as pool:
        futures = [pool.submit(_one_call, i) for i in range(_NUM_ACTORS * 4)]
        for fut in concurrent.futures.as_completed(futures):
            # Re-raises any exception the worker thread caught. Pre-fix
            # this is where the InterfaceError surfaces; post-fix every
            # future completes cleanly.
            actor_id, results = fut.result()
            assert results, f"resolver returned no entries for {actor_id!r}"
            # Every actor's superset includes its own memory bucket plus
            # the three shared collections.
            assert f"{actor_id}-memory" in results, (
                f"resolver dropped {actor_id}'s memory collection from the "
                f"superset under concurrent load: got {results!r}"
            )

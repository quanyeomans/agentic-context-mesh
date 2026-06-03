"""Integration test for the ``_PIPELINE_CACHE`` race fix (#396 W-B Commit 1).

Pre-fix: ``build_search_pipeline`` read ``_PIPELINE_CACHE`` and wrote
``_PIPELINE_CACHE[cfg] = pipeline`` without a lock. Under concurrent
first-call load, two threads could both miss + both rebuild the 2.3s
pipeline (and worse — under burst load, every thread that arrived
before the first build completed would race in parallel).

Post-fix: a module-level ``_PIPELINE_CACHE_LOCK`` serialises the build
path with double-checked locking. The lock-free read stays as the fast
steady-state path; the lock only fires on cache misses.

Pin: 50 threads firing ``build_search_pipeline(...)`` concurrently
against a freshly-reset cache MUST result in exactly one underlying
build. We measure build count by counting registry ``resolve`` calls —
``_build_embedding_service`` calls ``registry.resolve(name)`` exactly
once per build, so the registry's ``resolve_calls`` list length is a
direct sabotage-resistant count of pipeline constructions.

Sabotage proof (executed during development): removing the inner
``cached = _PIPELINE_CACHE.get(cfg)`` re-check inside the ``with
_PIPELINE_CACHE_LOCK:`` block makes the test fail — multiple threads
queue at the lock, the first one builds + writes, but every subsequent
thread proceeds to build again (writing into the cache redundantly).
With the re-check, every thread after the first observes the cached
pipeline and short-circuits.
"""

from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

import pytest

from kairix.core.db.schema import create_schema
from kairix.core.factory import build_search_pipeline, reset_search_pipeline_cache
from kairix.core.search.config import RetrievalConfig
from tests.fakes import FakePaths, FakeProvider, FakeProviderRegistry

pytestmark = pytest.mark.integration


def _bootstrap_db(db_path: Path) -> None:
    """Create an empty schema so the factory's repo-build steps don't blow up."""
    db = sqlite3.connect(str(db_path))
    create_schema(db)
    # Seed an active document so anywhere along the build path that
    # expects schema_v rows (e.g. health probes) finds something.
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


def test_concurrent_first_calls_build_pipeline_exactly_once(tmp_path: Path) -> None:
    """50 threads racing into a cold cache must produce exactly one build.

    Setup: bootstrap a tmp SQLite, reset the cache, then fire 50 threads
    all calling ``build_search_pipeline(...)`` with identical args. The
    cache key is the resolved ``RetrievalConfig`` so all threads share
    the same key, race to the same slot.

    Assertion: ``registry.resolve_calls`` length == 1. The registry's
    ``resolve(name)`` runs inside ``_build_embedding_service`` and is
    only reached on the build path — every cached return skips it.
    Therefore the call count is a direct measure of underlying pipeline
    constructions.

    Sabotage notes (documented above at module docstring): without the
    double-checked locking, registry.resolve_calls jumps from 1 to N
    where N is roughly the number of threads that arrived before the
    first build completed.
    """
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
    config = RetrievalConfig(provider="fake")

    reset_search_pipeline_cache()

    def _call() -> object:
        return build_search_pipeline(config=config, registry=registry, paths=paths)

    n_threads = 50
    with ThreadPoolExecutor(max_workers=n_threads) as executor:
        futures = [executor.submit(_call) for _ in range(n_threads)]
        pipelines = [f.result() for f in as_completed(futures)]

    # All 50 calls must return the same cached instance — id() is the
    # tightest assertion. (Equality would pass even with N separate
    # construction races returning equivalent shapes.)
    first_id = id(pipelines[0])
    assert all(id(p) == first_id for p in pipelines), (
        f"expected all 50 callers to share one pipeline instance; "
        f"got {len({id(p) for p in pipelines})} distinct ids — race not fixed."
    )

    # And the registry's resolve count proves only one underlying build
    # ran. Without the double-checked locking inside _PIPELINE_CACHE_LOCK,
    # this jumps to N>1.
    assert len(registry.resolve_calls) == 1, (
        f"expected exactly 1 pipeline build under concurrent load; "
        f"saw {len(registry.resolve_calls)} resolve calls — race not fixed. "
        f"fix: ensure _PIPELINE_CACHE_LOCK guards the build path with double-checked locking."
    )

    # Cleanup so subsequent tests don't pick up this fixture's pipeline.
    reset_search_pipeline_cache()

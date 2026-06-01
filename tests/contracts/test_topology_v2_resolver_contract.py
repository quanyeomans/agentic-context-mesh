"""Contract tests for :class:`TopologyV2CollectionResolver` (GH #372).

The load-bearing test: drive the Adapter end-to-end against a SEEDED
SQL database (no FakeScopeProfileResolver) and prove the superset
contract holds against the real ``ScopeProfileResolver`` + the real
schema.

Pins:

* ``test_default_search_returns_superset_of_scope_profile_collections`` —
  the exact assertion the GH issue calls out: an agent with 4 entries
  in scope (sharepoint-all + obsidian-all + reflib (read) + builder-memory
  (read_write)) gets all 4 back when ``collections=None``.
* ``test_failure_injection_resolver_raises_propagates`` — F68 mandate.
  When the underlying resolver raises, the Adapter propagates the error
  rather than silently degrading.
* ``test_protocol_compliance_with_runtime_checkable`` — F43 plugin
  contract; the Adapter satisfies the
  :class:`kairix.core.protocols.CollectionResolver` Protocol.

Sabotage proofs (executed before commit, restored on completion):

  1. ``test_default_search_returns_superset_of_scope_profile_collections``
     — DELETED the ``("sharepoint-all", ...)`` row from
     ``topology_scope_entries`` mid-test, re-ran. Got:
       AssertionError: scope superset must include every read-eligible
       collection; got {'obsidian-all', 'reflib', 'builder-memory'},
       missing: {'sharepoint-all'}
     Restored the row → test re-greened.

  2. ``test_failure_injection_resolver_raises_propagates`` — temporarily
     wrapped the Adapter's ``self._resolver.resolve(...)`` call in a
     bare ``try/except``. Got:
       Failed: DID NOT RAISE <class 'RuntimeError'>
     Restored the production code → test re-greened.
"""

from __future__ import annotations

import sqlite3

import pytest

from kairix.core.connectors.scope_profile_resolver import ScopeProfileResolver
from kairix.core.db.schema import create_schema
from kairix.core.protocols import CollectionResolver
from kairix.core.search.scope import Scope
from kairix.core.search.topology_v2_resolver import (
    TopologyV2CollectionResolver,
)
from tests.fakes import FakeScopeProfileResolver

pytestmark = pytest.mark.contract


@pytest.fixture
def seeded_db() -> sqlite3.Connection:
    """Seed an agent 'builder' with 4 collections in scope.

    sharepoint-all (read), obsidian-all (read), reflib (read),
    builder-memory (read_write) — the canonical GH #372 example.
    """
    db = sqlite3.connect(":memory:")
    create_schema(db, dims=4)
    now = "2026-06-01T00:00:00Z"
    cur = db.execute(
        "INSERT INTO topology_scope_profiles "
        "(actor_id, actor_kind, inherits_from_json, created_at, updated_at) "
        "VALUES (?, 'agent', '[]', ?, ?)",
        ("builder", now, now),
    )
    profile_id = cur.lastrowid
    for collection_name, can_read, can_write in [
        ("sharepoint-all", 1, 0),
        ("obsidian-all", 1, 0),
        ("reflib", 1, 0),
        ("builder-memory", 1, 1),
    ]:
        db.execute(
            "INSERT INTO topology_scope_entries "
            "(scope_profile_id, collection_name, can_read, can_write, max_sensitivity) "
            "VALUES (?, ?, ?, ?, 'internal')",
            (profile_id, collection_name, can_read, can_write),
        )
    db.commit()
    return db


def test_default_search_returns_superset_of_scope_profile_collections(
    seeded_db: sqlite3.Connection,
) -> None:
    """GH #372 — the load-bearing assertion.

    An agent with 4 read-eligible scope entries gets ALL 4 collection
    names back when calling resolve() with collections=None (default
    search). This is the superset contract — no in_default flag, no
    YAML lookup, just "everything the actor can read".
    """
    resolver = TopologyV2CollectionResolver(db=seeded_db)

    result = resolver.resolve(agent="builder", scope=Scope.SHARED_AGENT)

    assert result is not None, "resolver must return a non-None list for builder"
    expected = {"sharepoint-all", "obsidian-all", "reflib", "builder-memory"}
    missing = expected - set(result)
    assert set(result) == expected, (
        f"scope superset must include every read-eligible collection; got {set(result)}, missing: {missing}"
    )


def test_real_resolver_drives_agent_branch_through_sql(
    seeded_db: sqlite3.Connection,
) -> None:
    """Sanity check — the Adapter delegates to the SQL-backed
    :class:`ScopeProfileResolver` when no fake is injected, and the
    SQL path returns the same answer the contract guarantees.
    """
    resolver = TopologyV2CollectionResolver(db=seeded_db, scope_profile_resolver=ScopeProfileResolver(seeded_db))

    result = resolver.resolve(agent="builder", scope=Scope.SHARED_AGENT)

    assert result is not None
    assert "reflib" in result and "builder-memory" in result


def test_agent_scope_filters_to_writable_entries_only(
    seeded_db: sqlite3.Connection,
) -> None:
    """Scope.AGENT path — only ``can_write=1`` entries make it through.

    Of the 4 seeded entries, only ``builder-memory`` is can_write=1, so
    that's the entire AGENT-scope return.
    """
    resolver = TopologyV2CollectionResolver(db=seeded_db)

    result = resolver.resolve(agent="builder", scope=Scope.AGENT)

    assert result == ["builder-memory"], f"AGENT scope must be the writable subset; got {result!r}"


def test_failure_injection_resolver_raises_propagates() -> None:
    """F68 — every Protocol method has a failure-injection contract test.

    When :class:`ScopeProfileResolver.resolve` raises, the Adapter
    propagates the exception rather than silently returning ``None``
    (which would degrade to "no filter — search everything", a
    permissions-leak shape).
    """
    fake = FakeScopeProfileResolver().with_raises(RuntimeError("scope_profile table unavailable — db locked"))
    db = sqlite3.connect(":memory:")
    create_schema(db, dims=4)
    resolver = TopologyV2CollectionResolver(db=db, scope_profile_resolver=fake)

    with pytest.raises(RuntimeError, match="db locked"):
        resolver.resolve(agent="builder", scope=Scope.SHARED_AGENT)


def test_protocol_compliance_with_runtime_checkable() -> None:
    """F43 — the Adapter satisfies the
    :class:`kairix.core.protocols.CollectionResolver` Protocol.
    """
    db = sqlite3.connect(":memory:")
    create_schema(db, dims=4)
    resolver = TopologyV2CollectionResolver(db=db)

    assert isinstance(resolver, CollectionResolver), (
        "TopologyV2CollectionResolver must satisfy CollectionResolver Protocol"
    )

"""Unit tests for :class:`TopologyV2CollectionResolver` + default_in_scope wiring.

Scaffolding ahead of implementation (#373). Tests xfail with strict=False
until the production wiring lands; the implementation agent removes the
xfail decorator as each branch becomes real.

Pins (per docs/architecture/collection-v2-implementation-plan.md):

  * ``collections=None`` routes through ``ScopeProfileResolver(default_only=True)``.
  * Explicit collection in scope (any default_in_scope state) → returned.
  * Explicit collection NOT in scope → ``None`` + F21 error logged.
  * Explicit opt-in collection (``default_in_scope=False``) reachable by name.
  * Cross-agent isolation: ``shape`` cannot see ``builder-memory``.
  * Wildcard path (``agent=None``, ``ALL_AGENTS``) bypasses scope_profile.
  * Factory branch on the ``topology_v2_default_in_scope`` flag (off vs on).

F1-clean: every test injects a :class:`FakeScopeProfileResolver` via the
``scope_profile_resolver=`` kwarg — no monkeypatch of internals.
F2-clean: no env-var manipulation.
F8-clean: module-level ``pytestmark = pytest.mark.unit``.
"""

from __future__ import annotations

import sqlite3

import pytest

from kairix.core.db.schema import create_schema
from kairix.core.search.scope import Scope
from kairix.core.search.topology_v2_resolver import TopologyV2CollectionResolver
from tests.fakes import FakeScopeProfileResolver

pytestmark = pytest.mark.unit


def _fresh_db() -> sqlite3.Connection:
    db = sqlite3.connect(":memory:")
    create_schema(db, dims=4)
    return db


def test_no_collections_specified_returns_default_in_scope_superset() -> None:
    """``collections=None`` → only ``default_in_scope=True`` entries.

    The headline GH #373 behaviour. ``shape`` has 7 in-default + 1 opt-in
    (reflib); calling resolve with no explicit collections returns the 7
    in-default superset, NOT the 8-collection full read scope.
    """
    fake = FakeScopeProfileResolver().with_actor(
        "shape",
        entries=[
            ("sharepoint", "read", "internal", True),
            ("obsidian", "read", "internal", True),
            ("slack", "read", "personal", True),
            ("email", "read", "personal", True),
            ("calendar", "read", "personal", True),
            ("github", "read", "confidential", True),
            ("shape-memory", "read_write", "personal", True),
            ("reflib", "read", "public", False),
        ],
    )
    resolver = TopologyV2CollectionResolver(db=_fresh_db(), scope_profile_resolver=fake)

    result = resolver.resolve(agent="shape", scope=Scope.SHARED_AGENT)

    assert result is not None
    assert set(result) == {
        "sharepoint",
        "obsidian",
        "slack",
        "email",
        "calendar",
        "github",
        "shape-memory",
    }, f"default search must return the 7 in-default collections; got {set(result)!r}"
    assert "reflib" not in result, f"opt-in collection leaked into default search result: {result!r}"
    # The Adapter must propagate default_only=True through to the resolver.
    assert fake.last_default_only is True, (
        f"TopologyV2CollectionResolver must call scope resolver with default_only=True "
        f"on the collections=None path; saw default_only={fake.last_default_only!r}"
    )


def test_explicit_collection_in_scope_returns_that_collection() -> None:
    """``collections=['reflib']`` when reflib is in scope (any
    ``default_in_scope`` state) returns ``['reflib']``.

    Validates the explicit-path doesn't accidentally honour
    ``default_in_scope`` as a gate — opt-in collections must be reachable
    by name.
    """
    fake = FakeScopeProfileResolver().with_actor(
        "shape",
        entries=[
            ("sharepoint", "read", "internal", True),
            ("reflib", "read", "public", False),
        ],
    )
    resolver = TopologyV2CollectionResolver(db=_fresh_db(), scope_profile_resolver=fake)

    filtered, error = resolver.validate_explicit(agent="shape", collections=["reflib"], scope=Scope.SHARED_AGENT)

    assert error is None, f"validate_explicit on in-scope collection must not error; got {error!r}"
    assert filtered == ["reflib"], f"explicit ['reflib'] must pass through; got {filtered!r}"


def test_explicit_collection_not_in_scope_returns_none_with_f21_error() -> None:
    """``collections=['foo']`` when foo isn't in scope → ``(None, msg)``.

    The error message must carry F21 ``fix:``/``next:``/``run:`` action
    markers so the operator can self-correct without a runbook.
    """
    fake = FakeScopeProfileResolver().with_actor(
        "shape",
        entries=[
            ("sharepoint", "read", "internal", True),
        ],
    )
    resolver = TopologyV2CollectionResolver(db=_fresh_db(), scope_profile_resolver=fake)

    filtered, error = resolver.validate_explicit(agent="shape", collections=["foo"], scope=Scope.SHARED_AGENT)

    assert filtered is None, f"out-of-scope collection must return None; got {filtered!r}"
    assert error is not None
    assert "fix:" in error and "next:" in error and "run:" in error, (
        f"F21 affordance markers missing from error: {error!r}"
    )
    assert "foo" in error, f"error must name the offending collection 'foo'; got {error!r}"


def test_explicit_collection_opt_in_works_even_when_default_in_scope_false() -> None:
    """reflib has ``default_in_scope=False`` but IS in scope —
    ``collections=['reflib']`` retrieves it without error.

    Distinct from the previous test because the previous proves
    out-of-scope is rejected; this proves out-of-default but in-scope is
    accepted.
    """
    fake = FakeScopeProfileResolver().with_actor(
        "shape",
        entries=[
            ("sharepoint", "read", "internal", True),
            ("reflib", "read", "public", False),
        ],
    )
    resolver = TopologyV2CollectionResolver(db=_fresh_db(), scope_profile_resolver=fake)

    filtered, error = resolver.validate_explicit(agent="shape", collections=["reflib"], scope=Scope.SHARED_AGENT)

    assert error is None
    assert filtered == ["reflib"], f"opt-in collection must be reachable by explicit name; got {filtered!r}"


def test_default_only_true_excludes_other_agents_memory() -> None:
    """``agent='shape', collections=None`` → no builder-memory in result.

    Cross-agent memory isolation under default search. shape's scope
    contains shape-memory (own memory, in-default) but NOT
    builder-memory — default search must reflect that.
    """
    fake = FakeScopeProfileResolver().with_actor(
        "shape",
        entries=[
            ("sharepoint", "read", "internal", True),
            ("shape-memory", "read_write", "personal", True),
        ],
    )
    resolver = TopologyV2CollectionResolver(db=_fresh_db(), scope_profile_resolver=fake)

    result = resolver.resolve(agent="shape", scope=Scope.SHARED_AGENT)

    assert result is not None
    assert "builder-memory" not in result, f"cross-agent memory leak under default search: builder-memory in {result!r}"
    assert "shape-memory" in result, f"agent's own memory must be in default search result; got {result!r}"


def test_explicit_other_agent_memory_returns_none() -> None:
    """``agent='shape', collections=['builder-memory']`` → ``(None, F21 msg)``.

    Cross-agent memory isolation under EXPLICIT request. shape asking
    for builder-memory must be refused with an F21 error — there's no
    "well it's explicit so we'll allow it" loophole.
    """
    fake = FakeScopeProfileResolver().with_actor(
        "shape",
        entries=[
            ("sharepoint", "read", "internal", True),
            ("shape-memory", "read_write", "personal", True),
        ],
    )
    resolver = TopologyV2CollectionResolver(db=_fresh_db(), scope_profile_resolver=fake)

    filtered, error = resolver.validate_explicit(
        agent="shape", collections=["builder-memory"], scope=Scope.SHARED_AGENT
    )

    assert filtered is None
    assert error is not None
    assert "builder-memory" in error
    assert "fix:" in error, f"F21 affordance missing from cross-agent error: {error!r}"


def test_agent_none_all_agents_path_unaffected_by_default_only() -> None:
    """``agent=None, scope=ALL_AGENTS`` continues to use public-access fan-out.

    The wildcard / cross-agent path doesn't consult scope_profiles, so
    the new ``default_in_scope`` filter doesn't apply. Pins that the
    Adapter's branch dispatch is unchanged for the ALL_AGENTS / EVERYTHING
    scope values.
    """
    db = _fresh_db()
    now = "2026-06-01T00:00:00Z"
    # Seed one PUBLIC cc_pair + one collection_source so the public fan-out
    # has a row to return. (The Adapter's _public_collections joins
    # topology_collections → topology_collection_sources → topology_cc_pairs.)
    cur = db.execute(
        "INSERT INTO topology_connectors "
        "(kind, name, connector_specific_config, default_sensitivity, created_at, updated_at) "
        "VALUES ('obsidian', 'test-conn', '{}', 'public', ?, ?)",
        (now, now),
    )
    conn_id = cur.lastrowid
    cur = db.execute(
        "INSERT INTO topology_cc_pairs (connector_id, name, access_type, status, created_at, updated_at) "
        "VALUES (?, 'pub-cc', 'PUBLIC', 'INITIALIZING', ?, ?)",
        (conn_id, now, now),
    )
    cc_id = cur.lastrowid
    cur = db.execute(
        "INSERT INTO topology_collections (name, default_sensitivity, created_at, updated_at) "
        "VALUES ('public-collection', 'public', ?, ?)",
        (now, now),
    )
    coll_id = cur.lastrowid
    db.execute(
        "INSERT INTO topology_collection_sources (collection_id, cc_pair_id, source_path_filter) VALUES (?, ?, '*')",
        (coll_id, cc_id),
    )
    db.commit()

    fake = FakeScopeProfileResolver()  # never consulted on ALL_AGENTS path
    resolver = TopologyV2CollectionResolver(db=db, scope_profile_resolver=fake)

    result = resolver.resolve(agent=None, scope=Scope.ALL_AGENTS)

    assert result is not None
    assert "public-collection" in result, f"ALL_AGENTS path must surface public cc_pair collections; got {result!r}"
    # The fake's default_only signal must remain at its sentinel — the
    # Adapter shouldn't have called resolve() on the ALL_AGENTS path.
    assert fake.last_default_only is None, (
        f"ALL_AGENTS path must NOT consult scope_profile resolver; saw default_only={fake.last_default_only!r}"
    )


def test_factory_branch_on_topology_v2_collection_resolver_flag() -> None:
    """``build_collection_resolver`` returns the v2 Adapter when the
    ``topology_v2_default_in_scope`` flag is ON.

    Pins the factory's dispatch on the new flag — operators flipping
    ``topology_v2_default_in_scope`` get the default-only behaviour
    end-to-end through ``build_search_pipeline``.
    """
    from kairix.core.factory import build_collection_resolver

    fake_reader_on = FakeScopeProfileResolver  # placeholder — see below
    del fake_reader_on  # silence F19

    # Flag-reader returns True for topology_v2_default_in_scope → the
    # factory wires TopologyV2CollectionResolver with default_only routing.
    def _on_reader(name: str) -> bool:
        return name in {
            "topology_v2_collection_resolver",
            "topology_v2_default_in_scope",
        }

    resolver = build_collection_resolver(db_path=":memory:", flag_reader=_on_reader)

    # The v2 Adapter has a ``validate_explicit`` method; the legacy resolver
    # does not. This is the load-bearing branch assertion.
    assert hasattr(resolver, "validate_explicit"), (
        f"flag ON must yield TopologyV2CollectionResolver (with validate_explicit); got {type(resolver).__name__}"
    )

    # Inverse: flag OFF → legacy resolver.
    def _off_reader(_name: str) -> bool:
        return False

    legacy = build_collection_resolver(db_path=":memory:", flag_reader=_off_reader)
    assert not hasattr(legacy, "validate_explicit"), (
        f"flag OFF must yield legacy DefaultCollectionResolver; got {type(legacy).__name__}"
    )

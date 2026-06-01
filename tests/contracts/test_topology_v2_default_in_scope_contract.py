"""Contract tests for the default_in_scope extension (GH #373).

Drives :class:`TopologyV2CollectionResolver` + :class:`ScopeProfileResolver`
end-to-end against SEEDED in-memory SQLite (no fakes) to prove the new
``default_in_scope`` field flows through every protocol seam.

Pins (per docs/architecture/collection-v2-implementation-plan.md):

  * Protocol compliance preserved after the API extension.
  * Load-bearing: 7 in-default + 1 opt-in seeded → ``collections=None``
    returns the 7-name superset.
  * Composition: TopologyV2CollectionResolver.resolve(collections=None)
    propagates ``default_only=True`` to ScopeProfileResolver.
  * F68 failure-injection: pre-migration rows treated as default_in_scope=1.

Scaffolding pattern: every test is xfailed with strict=False until the
production change lands; the impl agent removes the decorator inline as
each path becomes real. F11-clean (reason= cites #373 + flag).
"""

from __future__ import annotations

import sqlite3

import pytest

from kairix.core.db.schema import create_schema
from kairix.core.protocols import CollectionResolver
from kairix.core.search.scope import Scope
from kairix.core.search.topology_v2_resolver import TopologyV2CollectionResolver
from tests.fakes import FakeScopeProfileResolver

pytestmark = pytest.mark.contract


def _seeded_db_seven_in_default_one_opt_in() -> sqlite3.Connection:
    """Seed the canonical 7-in-default + 1-opt-in shape for agent 'shape'.

    Mirrors the production v2 structure: sharepoint, obsidian, slack,
    email, calendar, github, shape-memory are in-default; reflib is opt-in.
    """
    db = sqlite3.connect(":memory:")
    create_schema(db, dims=4)
    now = "2026-06-01T00:00:00Z"
    cur = db.execute(
        "INSERT INTO topology_scope_profiles "
        "(actor_id, actor_kind, inherits_from_json, created_at, updated_at) "
        "VALUES (?, 'agent', '[]', ?, ?)",
        ("shape", now, now),
    )
    profile_id = cur.lastrowid
    cols = {row[1] for row in db.execute("PRAGMA table_info(topology_scope_entries)").fetchall()}
    has_default_col = "default_in_scope" in cols
    entries = [
        ("sharepoint", 1, 0, "internal", 1),
        ("obsidian", 1, 0, "internal", 1),
        ("slack", 1, 0, "personal", 1),
        ("email", 1, 0, "personal", 1),
        ("calendar", 1, 0, "personal", 1),
        ("github", 1, 0, "confidential", 1),
        ("shape-memory", 1, 1, "personal", 1),
        ("reflib", 1, 0, "public", 0),  # opt-in
    ]
    for name, can_read, can_write, max_sens, default_in_scope in entries:
        if has_default_col:
            db.execute(
                "INSERT INTO topology_scope_entries "
                "(scope_profile_id, collection_name, can_read, can_write, "
                "max_sensitivity, default_in_scope) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (profile_id, name, can_read, can_write, max_sens, default_in_scope),
            )
        else:
            db.execute(
                "INSERT INTO topology_scope_entries "
                "(scope_profile_id, collection_name, can_read, can_write, max_sensitivity) "
                "VALUES (?, ?, ?, ?, ?)",
                (profile_id, name, can_read, can_write, max_sens),
            )
    db.commit()
    return db


@pytest.mark.xfail(reason="impl pending — #373 / feature-flag topology_v2_default_in_scope", strict=False)
def test_topology_v2_resolver_satisfies_collection_resolver_protocol_with_default_only() -> None:
    """isinstance(resolver, CollectionResolver) still holds after the API
    extension — the new ``default_only`` kwarg lives on the underlying
    ScopeProfileResolver, not on the CollectionResolver Protocol.

    Pins that #373 does NOT broaden the public Protocol contract; the
    extension is internal to the Adapter ↔ Resolver composition.
    """
    db = _seeded_db_seven_in_default_one_opt_in()
    resolver = TopologyV2CollectionResolver(db=db)

    assert isinstance(resolver, CollectionResolver), (
        "TopologyV2CollectionResolver must still satisfy CollectionResolver after #373"
    )


@pytest.mark.xfail(reason="impl pending — #373 / feature-flag topology_v2_default_in_scope", strict=False)
def test_default_in_scope_default_search_returns_superset_load_bearing() -> None:
    """LOAD-BEARING — the GH #373 acceptance assertion.

    Seeded with 7 in-default + 1 opt-in for shape; resolve(agent='shape',
    scope=SHARED_AGENT) MUST return exactly the 7 in-default names.

    This is the test the implementation agent runs as the green-light
    signal — when it flips xpass → pass, the v2 default-in-scope wiring
    is end-to-end correct.
    """
    db = _seeded_db_seven_in_default_one_opt_in()
    resolver = TopologyV2CollectionResolver(db=db)

    result = resolver.resolve(agent="shape", scope=Scope.SHARED_AGENT)

    assert result is not None
    expected_in_default = {
        "sharepoint",
        "obsidian",
        "slack",
        "email",
        "calendar",
        "github",
        "shape-memory",
    }
    got = set(result)
    missing = expected_in_default - got
    leaked = got - expected_in_default
    assert got == expected_in_default, (
        f"default search must return exactly the 7 in-default collections.\n"
        f"  expected: {sorted(expected_in_default)}\n"
        f"  got: {sorted(got)}\n"
        f"  missing: {sorted(missing)}\n"
        f"  leaked: {sorted(leaked)}"
    )


@pytest.mark.xfail(reason="impl pending — #373 / feature-flag topology_v2_default_in_scope", strict=False)
def test_scope_profile_resolver_default_only_propagates_to_topology_v2_resolver() -> None:
    """Composition assertion — TopologyV2CollectionResolver MUST call
    ScopeProfileResolver.resolve with ``default_only=True`` when the
    caller passes ``collections=None``.

    Pins the composition direction so the Adapter doesn't silently drop
    the default_only kwarg on the floor (which would degrade #373 into
    "ignored and returned the full read scope").
    """
    fake = FakeScopeProfileResolver().with_actor(
        "shape",
        entries=[
            ("sharepoint", "read", "internal", True),
            ("reflib", "read", "public", False),
        ],
    )
    db = sqlite3.connect(":memory:")
    create_schema(db, dims=4)
    resolver = TopologyV2CollectionResolver(db=db, scope_profile_resolver=fake)

    resolver.resolve(agent="shape", scope=Scope.SHARED_AGENT)

    assert fake.last_default_only is True, (
        f"Adapter must propagate default_only=True to ScopeProfileResolver on "
        f"the collections=None code path; observed default_only={fake.last_default_only!r}"
    )


@pytest.mark.xfail(reason="impl pending — #373 / feature-flag topology_v2_default_in_scope", strict=False)
def test_f68_db_row_missing_default_in_scope_treats_as_default_true() -> None:
    """F68 failure injection — a row pre-dating the migration must be
    treated as ``default_in_scope=1`` (in-default).

    Pins the back-compat invariant: operators running the new resolver
    code against a DB whose ALTER TABLE hasn't run see EVERY legacy row
    surface in default search (no silent loss of every collection).
    """
    from kairix.core.connectors.scope_profile_resolver import ScopeProfileResolver

    # Build a pre-migration DB shape.
    db = sqlite3.connect(":memory:")
    create_schema(db, dims=4)
    db.execute("DROP TABLE topology_scope_entries")
    db.execute(
        """
        CREATE TABLE topology_scope_entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            scope_profile_id INTEGER NOT NULL,
            collection_name TEXT NOT NULL,
            can_read INTEGER NOT NULL DEFAULT 1,
            can_write INTEGER NOT NULL DEFAULT 0,
            max_sensitivity TEXT NOT NULL DEFAULT 'internal',
            UNIQUE(scope_profile_id, collection_name),
            FOREIGN KEY (scope_profile_id) REFERENCES topology_scope_profiles(id)
        )
        """
    )
    now = "2026-06-01T00:00:00Z"
    cur = db.execute(
        "INSERT INTO topology_scope_profiles "
        "(actor_id, actor_kind, inherits_from_json, created_at, updated_at) "
        "VALUES (?, 'agent', '[]', ?, ?)",
        ("shape", now, now),
    )
    profile_id = cur.lastrowid
    for name in ("legacy-1", "legacy-2", "legacy-3"):
        db.execute(
            "INSERT INTO topology_scope_entries "
            "(scope_profile_id, collection_name, can_read, can_write, max_sensitivity) "
            "VALUES (?, ?, 1, 0, 'internal')",
            (profile_id, name),
        )
    db.commit()

    scope_resolver = ScopeProfileResolver(db)
    scope = scope_resolver.resolve(actors=("shape",), default_only=True)

    names = {c.name for c in scope.collections}
    assert names == {"legacy-1", "legacy-2", "legacy-3"}, (
        f"pre-migration rows must surface under default_only=True (COALESCE default_in_scope → 1); got {names!r}"
    )

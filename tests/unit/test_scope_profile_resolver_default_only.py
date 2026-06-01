"""Unit tests for ``ScopeProfileResolver.resolve(default_only=...)`` — GH #373.

Scaffolding ahead of implementation (#373). Each test xfails with the
``impl pending`` reason — the implementation agent removes the xfail
decorator as the production code lands and the test naturally flips to
xpass → pass. Per CLAUDE.md F11 the xfail decorators carry a concrete
``reason=`` pointing at the issue + feature flag so any operator inspecting
a green-with-xfails run sees the in-flight work.

Pins (per docs/architecture/collection-v2-implementation-plan.md):

  * Back-compat: ``default_only=False`` returns every read-eligible entry.
  * GH #373 happy path: ``default_only=True`` filters by
    ``default_in_scope`` column.
  * Schema back-compat: rows without a ``default_in_scope`` column default
    to ``1`` (in-default).
  * Composition with sensitivity cap, intersection mode, union mode.
  * F68: graceful behaviour when the migration hasn't run yet.

F1-clean: every test seeds in-memory SQLite directly (the unit under test
IS the SQL → ResolvedScope path); no monkeypatch of resolver internals.
F2-clean: no ``KAIRIX_*`` env vars.
F8-clean: module-level ``pytestmark = pytest.mark.unit``.
"""

from __future__ import annotations

import sqlite3

import pytest

from kairix.core.db.schema import create_schema

pytestmark = pytest.mark.unit


def _fresh_db() -> sqlite3.Connection:
    """Open an in-memory SQLite DB with the production schema."""
    db = sqlite3.connect(":memory:")
    create_schema(db, dims=4)
    return db


def _seed_scope_profile(
    db: sqlite3.Connection,
    *,
    actor_id: str,
    entries: list[tuple[str, int, int, str, int]],
) -> int:
    """Seed one scope_profile + its entries.

    ``entries`` is a list of
    ``(collection_name, can_read, can_write, max_sensitivity, default_in_scope)``
    rows. The helper writes ``default_in_scope`` only when the migration's
    column exists — otherwise it silently skips it so pre-migration tests
    still seed.
    """
    now = "2026-06-01T00:00:00Z"
    cur = db.execute(
        "INSERT INTO topology_scope_profiles "
        "(actor_id, actor_kind, inherits_from_json, created_at, updated_at) "
        "VALUES (?, 'agent', '[]', ?, ?)",
        (actor_id, now, now),
    )
    profile_id = cur.lastrowid
    cols = {row[1] for row in db.execute("PRAGMA table_info(topology_scope_entries)").fetchall()}
    has_default_col = "default_in_scope" in cols
    for collection_name, can_read, can_write, max_sens, default_in_scope in entries:
        if has_default_col:
            db.execute(
                "INSERT INTO topology_scope_entries "
                "(scope_profile_id, collection_name, can_read, can_write, "
                "max_sensitivity, default_in_scope) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (profile_id, collection_name, can_read, can_write, max_sens, default_in_scope),
            )
        else:
            db.execute(
                "INSERT INTO topology_scope_entries "
                "(scope_profile_id, collection_name, can_read, can_write, max_sensitivity) "
                "VALUES (?, ?, ?, ?, ?)",
                (profile_id, collection_name, can_read, can_write, max_sens),
            )
    db.commit()
    return int(profile_id) if profile_id is not None else 0


def test_default_only_false_returns_all_entries() -> None:
    """Back-compat: ``default_only=False`` (the existing call-site shape)
    returns every read-eligible entry regardless of ``default_in_scope``.

    Pins the contract that pre-#373 callers see zero behaviour change when
    the schema migration lands — the default_only kwarg adds a NEW filter,
    it doesn't change the existing filter set.
    """
    from kairix.core.connectors.scope_profile_resolver import ScopeProfileResolver

    db = _fresh_db()
    _seed_scope_profile(
        db,
        actor_id="agent-alpha",
        entries=[
            ("col-a", 1, 0, "internal", 1),
            ("col-b", 1, 0, "internal", 0),
            ("col-c", 1, 0, "internal", 1),
        ],
    )
    resolver = ScopeProfileResolver(db)

    scope = resolver.resolve(actors=("agent-alpha",), default_only=False)

    names = {c.name for c in scope.collections}
    assert names == {"col-a", "col-b", "col-c"}, (
        f"default_only=False must surface every read-eligible entry; got {names!r}"
    )


def test_default_only_true_filters_to_default_in_scope_true_entries() -> None:
    """``default_only=True`` keeps the 3 entries flagged ``default_in_scope=1``.

    Headline GH #373 contract: the default-search superset is exactly the
    in-default subset of the actor's read scope.
    """
    from kairix.core.connectors.scope_profile_resolver import ScopeProfileResolver

    db = _fresh_db()
    _seed_scope_profile(
        db,
        actor_id="agent-alpha",
        entries=[
            ("sharepoint", 1, 0, "internal", 1),
            ("obsidian", 1, 0, "internal", 1),
            ("slack", 1, 0, "internal", 1),
            ("reflib", 1, 0, "public", 0),
            ("archive", 1, 0, "internal", 0),
        ],
    )
    resolver = ScopeProfileResolver(db)

    scope = resolver.resolve(actors=("agent-alpha",), default_only=True)

    names = {c.name for c in scope.collections}
    assert names == {"sharepoint", "obsidian", "slack"}, (
        f"default_only=True must filter to default_in_scope=1 entries; got {names!r}"
    )


def test_default_only_true_excludes_default_in_scope_false_entries() -> None:
    """Inverse of the happy path — 2 of 5 entries flagged out-of-default
    are dropped under ``default_only=True``.

    Distinct from the previous test because it asserts the exclusion is
    NAMED — operators reading the resolver output need to be able to tell
    that "reflib" was deliberately filtered, not silently lost.
    """
    from kairix.core.connectors.scope_profile_resolver import ScopeProfileResolver

    db = _fresh_db()
    _seed_scope_profile(
        db,
        actor_id="agent-alpha",
        entries=[
            ("kept-1", 1, 0, "internal", 1),
            ("kept-2", 1, 0, "internal", 1),
            ("kept-3", 1, 0, "internal", 1),
            ("dropped-1", 1, 0, "internal", 0),
            ("dropped-2", 1, 0, "public", 0),
        ],
    )
    resolver = ScopeProfileResolver(db)

    scope = resolver.resolve(actors=("agent-alpha",), default_only=True)

    names = {c.name for c in scope.collections}
    assert "dropped-1" not in names, f"default_in_scope=0 entry leaked into default-only collections: {names!r}"
    assert "dropped-2" not in names, f"default_in_scope=0 entry leaked into default-only collections: {names!r}"
    assert names == {"kept-1", "kept-2", "kept-3"}


def test_default_only_true_with_no_default_in_scope_true_returns_empty() -> None:
    """Edge: every entry has ``default_in_scope=0`` → ``collections`` is empty.

    Operators with a pathological config (every collection opt-in) should
    see a zero-result default search — not an "ignored the filter and
    returned everything" surprise.
    """
    from kairix.core.connectors.scope_profile_resolver import ScopeProfileResolver

    db = _fresh_db()
    _seed_scope_profile(
        db,
        actor_id="agent-alpha",
        entries=[
            ("opt-in-1", 1, 0, "public", 0),
            ("opt-in-2", 1, 0, "public", 0),
        ],
    )
    resolver = ScopeProfileResolver(db)

    scope = resolver.resolve(actors=("agent-alpha",), default_only=True)

    assert scope.collections == (), (
        f"every entry opt-in → default_only collections must be empty; got {scope.collections!r}"
    )


def test_default_in_scope_default_value_is_one_for_back_compat() -> None:
    """Schema migration: an INSERT without ``default_in_scope`` lands as 1.

    Pins the back-compat path — production rows written by pre-#373 code
    (no knowledge of the new column) materialise as in-default after the
    ALTER TABLE migration runs, so the cutover does not silently drop
    every collection from default search.
    """
    db = _fresh_db()
    cols = {row[1] for row in db.execute("PRAGMA table_info(topology_scope_entries)").fetchall()}
    assert "default_in_scope" in cols, (
        "schema migration #373 must add default_in_scope column to topology_scope_entries"
    )

    # Insert WITHOUT default_in_scope — simulates the pre-#373 writer.
    now = "2026-06-01T00:00:00Z"
    cur = db.execute(
        "INSERT INTO topology_scope_profiles "
        "(actor_id, actor_kind, inherits_from_json, created_at, updated_at) "
        "VALUES (?, 'agent', '[]', ?, ?)",
        ("agent-alpha", now, now),
    )
    profile_id = cur.lastrowid
    db.execute(
        "INSERT INTO topology_scope_entries "
        "(scope_profile_id, collection_name, can_read, can_write, max_sensitivity) "
        "VALUES (?, ?, 1, 0, 'internal')",
        (profile_id, "legacy-collection"),
    )

    row = db.execute(
        "SELECT default_in_scope FROM topology_scope_entries WHERE collection_name=?",
        ("legacy-collection",),
    ).fetchone()
    assert row is not None
    assert row[0] == 1, f"schema default for default_in_scope must be 1 (back-compat); got {row[0]!r}"


def test_max_sensitivity_cap_still_honored_with_default_only() -> None:
    """Sensitivity tier composes with the new default_only filter.

    The min-sensitivity intersection across actors continues to apply on
    the filtered subset — default_only doesn't bypass F39 tiering.
    """
    from kairix.core.connectors.scope_profile_resolver import ScopeProfileResolver

    db = _fresh_db()
    _seed_scope_profile(
        db,
        actor_id="agent-alpha",
        entries=[
            ("col-restricted", 1, 0, "restricted", 1),
            ("col-public", 1, 0, "public", 1),
        ],
    )
    _seed_scope_profile(
        db,
        actor_id="agent-beta",
        entries=[
            ("col-restricted", 1, 0, "public", 1),
            ("col-public", 1, 0, "public", 1),
        ],
    )
    resolver = ScopeProfileResolver(db)

    scope = resolver.resolve(actors=("agent-alpha", "agent-beta"), default_only=True)

    by_name = {c.name: c.max_sensitivity for c in scope.collections}
    assert by_name.get("col-restricted") == "public", (
        f"F39-min across actors must collapse restricted+public → public; got {by_name!r}"
    )
    assert by_name.get("col-public") == "public"


def test_failure_injection_db_missing_default_in_scope_column_falls_back_gracefully() -> None:
    """F68: pre-migration DB → resolver still returns rows (back-compat).

    Operators running the new resolver code against a DB whose ALTER TABLE
    hasn't run yet must NOT crash — the resolver should COALESCE the
    missing column to 1 (in-default) so the legacy rows continue to
    surface in default search.
    """
    from kairix.core.connectors.scope_profile_resolver import ScopeProfileResolver

    # Build a stripped-down DB without the default_in_scope column. We DROP
    # the table and recreate it in the pre-#373 shape to simulate the
    # pre-migration state.
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
    _seed_scope_profile(
        db,
        actor_id="agent-alpha",
        entries=[
            ("legacy-1", 1, 0, "internal", 1),  # default_in_scope ignored by helper when col missing
            ("legacy-2", 1, 0, "internal", 1),
        ],
    )
    resolver = ScopeProfileResolver(db)

    scope = resolver.resolve(actors=("agent-alpha",), default_only=True)

    names = {c.name for c in scope.collections}
    assert names == {"legacy-1", "legacy-2"}, (
        f"pre-migration DB must surface every row under default_only=True "
        f"(treated as default_in_scope=1); got {names!r}"
    )


def test_intersection_composition_with_default_only() -> None:
    """Intersection mode + default_only: every actor must list the entry
    AND the entry must be in-default for at least one of them.

    Pins the cross-actor composition: ``shape`` + ``builder`` both list
    ``sharepoint`` (in-default for both), only ``builder`` lists
    ``reflib`` (opt-in) — intersection drops ``reflib`` regardless of
    default_in_scope.
    """
    from kairix.core.connectors.scope_profile_resolver import ScopeProfileResolver

    db = _fresh_db()
    _seed_scope_profile(
        db,
        actor_id="agent-shape",
        entries=[
            ("sharepoint", 1, 0, "internal", 1),
            ("obsidian", 1, 0, "internal", 1),
        ],
    )
    _seed_scope_profile(
        db,
        actor_id="agent-builder",
        entries=[
            ("sharepoint", 1, 0, "internal", 1),
            ("obsidian", 1, 0, "internal", 1),
            ("reflib", 1, 0, "public", 0),
        ],
    )
    resolver = ScopeProfileResolver(db)

    scope = resolver.resolve(
        actors=("agent-shape", "agent-builder"),
        default_only=True,
        scope_composition="intersection",
    )

    names = {c.name for c in scope.collections}
    assert names == {"sharepoint", "obsidian"}, (
        f"intersection + default_only must drop non-shared AND non-default; got {names!r}"
    )


def test_union_composition_with_default_only() -> None:
    """Union mode + default_only: an entry surfaces if ANY actor flags
    it default_in_scope=1.

    Pins the union-side branch: when the operator deliberately opens the
    composition to union, ``default_only=True`` still filters by the
    default_in_scope column on the entries that the union admits.
    """
    from kairix.core.connectors.scope_profile_resolver import ScopeProfileResolver

    db = _fresh_db()
    _seed_scope_profile(
        db,
        actor_id="agent-shape",
        entries=[
            ("sharepoint", 1, 0, "internal", 1),
            ("private-shape", 1, 0, "internal", 0),
        ],
    )
    _seed_scope_profile(
        db,
        actor_id="agent-builder",
        entries=[
            ("reflib", 1, 0, "public", 1),
            ("private-builder", 1, 0, "internal", 0),
        ],
    )
    resolver = ScopeProfileResolver(db)

    scope = resolver.resolve(
        actors=("agent-shape", "agent-builder"),
        default_only=True,
        scope_composition="union",
        scope_composition_token="test-union-token",
    )

    names = {c.name for c in scope.collections}
    assert names == {"sharepoint", "reflib"}, (
        f"union + default_only must surface every in-default entry across actors; got {names!r}"
    )

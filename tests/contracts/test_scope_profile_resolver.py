"""Contract tests for ScopeProfileResolver (ADR v2 §6 composition).

Pins:
* Intersection mode collapses to the shared collection set.
* ``max_sensitivity`` is F39-min across actors for shared collections.
* Excluded collections carry reason + escalation hint.
* Union mode requires a non-empty scope_composition_token.
* F39-min on empty input raises ValueError.

Sabotage-prove targets:
- Intersection collapse: change ``if {e.actor_id for e in actor_entries}
  != set(actors)`` to ``if False`` → confirm
  test_intersection_collapses_to_shared_collections fails → restore.
"""

from __future__ import annotations

import sqlite3

import pytest

from kairix.core.connectors.scope_profile_resolver import (
    ScopeProfileResolver,
    min_sensitivity,
)
from kairix.core.db.schema import create_schema
from kairix.core.protocols import InsufficientPermissionsError

pytestmark = pytest.mark.contract


def _fresh_db() -> sqlite3.Connection:
    db = sqlite3.connect(":memory:")
    create_schema(db, dims=4)
    return db


def _seed_profile(
    db: sqlite3.Connection,
    *,
    actor_id: str,
    entries: list[tuple[str, bool, bool, str]],
    actor_kind: str = "agent",
) -> None:
    """Seed one scope_profile + N scope_entries.

    ``entries`` is a list of ``(collection_name, can_read, can_write, max_sensitivity)``.
    """
    now = "2026-05-23T00:00:00Z"
    cur = db.execute(
        "INSERT INTO topology_scope_profiles "
        "(actor_id, actor_kind, inherits_from_json, created_at, updated_at) "
        "VALUES (?, ?, '[]', ?, ?)",
        (actor_id, actor_kind, now, now),
    )
    profile_id = cur.lastrowid
    for collection_name, can_read, can_write, max_sens in entries:
        db.execute(
            "INSERT INTO topology_scope_entries "
            "(scope_profile_id, collection_name, can_read, can_write, max_sensitivity) "
            "VALUES (?, ?, ?, ?, ?)",
            (profile_id, collection_name, int(can_read), int(can_write), max_sens),
        )
    db.commit()


def test_min_sensitivity_picks_least_permissive() -> None:
    assert min_sensitivity(["public"]) == "public"
    assert min_sensitivity(["internal", "public", "restricted"]) == "public"
    assert min_sensitivity(["restricted", "confidential"]) == "confidential"


def test_min_sensitivity_empty_raises() -> None:
    with pytest.raises(ValueError):
        min_sensitivity([])


def test_intersection_collapses_to_shared_collections() -> None:
    """Two actors with overlapping collections: intersection returns the overlap."""
    db = _fresh_db()
    _seed_profile(
        db,
        actor_id="agent-alpha",
        entries=[
            ("vault-projects", True, True, "internal"),
            ("client-x", True, False, "confidential"),
        ],
    )
    _seed_profile(
        db,
        actor_id="team-shape",
        entries=[
            ("vault-projects", True, False, "internal"),
            ("decisions", True, True, "restricted"),
        ],
    )
    resolver = ScopeProfileResolver(db)
    scope = resolver.resolve(actors=("agent-alpha", "team-shape"))
    names = {c.name for c in scope.collections}
    assert names == {"vault-projects"}, f"intersection should be {{'vault-projects'}}; got {names}"
    excluded = {ex.name for ex in scope.excluded_collections}
    assert excluded == {"client-x", "decisions"}


def test_intersection_max_sensitivity_is_f39_min() -> None:
    """For shared collections, max_sensitivity is the minimum across actors."""
    db = _fresh_db()
    _seed_profile(
        db,
        actor_id="agent-alpha",
        entries=[("vault-projects", True, True, "restricted")],
    )
    _seed_profile(
        db,
        actor_id="team-shape",
        entries=[("vault-projects", True, True, "internal")],
    )
    resolver = ScopeProfileResolver(db)
    scope = resolver.resolve(actors=("agent-alpha", "team-shape"))
    assert len(scope.collections) == 1
    assert scope.collections[0].max_sensitivity == "internal"


def test_excluded_carries_reason_and_hint() -> None:
    """An actor lacking read on a shared collection moves it to excluded with hint."""
    db = _fresh_db()
    _seed_profile(
        db,
        actor_id="agent-alpha",
        entries=[("vault-projects", True, False, "internal")],
    )
    _seed_profile(
        db,
        actor_id="team-shape",
        entries=[("vault-projects", False, False, "internal")],
    )
    resolver = ScopeProfileResolver(db)
    scope = resolver.resolve(actors=("agent-alpha", "team-shape"))
    assert scope.collections == ()
    assert len(scope.excluded_collections) == 1
    ex = scope.excluded_collections[0]
    assert ex.name == "vault-projects"
    assert ex.reason == "actor_lacks_read"
    assert ex.escalation_hint is not None


def test_union_requires_composition_token() -> None:
    db = _fresh_db()
    _seed_profile(
        db,
        actor_id="agent-alpha",
        entries=[("vault-projects", True, True, "internal")],
    )
    resolver = ScopeProfileResolver(db)
    with pytest.raises(InsufficientPermissionsError):
        resolver.resolve(
            actors=("agent-alpha",),
            scope_composition="union",
            scope_composition_token=None,
        )
    with pytest.raises(InsufficientPermissionsError):
        resolver.resolve(
            actors=("agent-alpha",),
            scope_composition="union",
            scope_composition_token="   ",
        )


def test_union_with_token_returns_collection_set() -> None:
    """Union mode with a valid token returns the union, F39-max sensitivity."""
    db = _fresh_db()
    _seed_profile(
        db,
        actor_id="agent-alpha",
        entries=[("vault-projects", True, True, "internal")],
    )
    _seed_profile(
        db,
        actor_id="team-shape",
        entries=[("client-x", True, False, "restricted")],
    )
    resolver = ScopeProfileResolver(db)
    scope = resolver.resolve(
        actors=("agent-alpha", "team-shape"),
        scope_composition="union",
        scope_composition_token="probe-union-token",
    )
    names = {c.name for c in scope.collections}
    assert names == {"vault-projects", "client-x"}


def test_empty_actors_returns_empty_scope() -> None:
    db = _fresh_db()
    resolver = ScopeProfileResolver(db)
    scope = resolver.resolve(actors=())
    assert scope.collections == ()
    assert scope.excluded_collections == ()

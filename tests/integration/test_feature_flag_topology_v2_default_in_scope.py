"""F54 integration coverage for the ``topology_v2_default_in_scope`` flag.

GH #373 — when ON, the search pipeline's resolver routes the
``collections=None`` code path through
:meth:`ScopeProfileResolver.resolve` with ``default_only=True``; when OFF,
the same resolver is invoked with ``default_only=False`` (existing
behaviour). The string ``"topology_v2_default_in_scope"`` appears
verbatim in every ``with_flag(...)`` call so F54's flag-name scanner
picks both branches up.

Per F54 (docs/architecture/feature-flag-architecture.md §5): every flag
needs an integration test exercising both branches via
:class:`FakeFeatureFlagResolver` from ``tests/fakes.py``.

Scaffolding pattern: the test body xfails with ``strict=False`` until
the production wiring lands. The xfail decorator carries a concrete
``reason=`` per CLAUDE.md F11. The implementation agent removes the
decorator inline as the production branch ships.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from kairix.core.db.schema import create_schema
from kairix.core.factory import build_collection_resolver
from kairix.core.search.scope import Scope
from tests.fakes import FakeFeatureFlagResolver

pytestmark = pytest.mark.integration


def _seeded_db_path(tmp_path: Path) -> Path:
    """Seed agent 'shape' with 7 in-default + 1 opt-in scope entries."""
    db_path = tmp_path / "index.sqlite"
    db = sqlite3.connect(str(db_path), timeout=10.0)
    try:
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
            ("reflib", 1, 0, "public", 0),
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
    finally:
        db.close()
    return db_path


@pytest.mark.xfail(
    reason=(
        "#373 Wave A — requires ScopeProfileResolver.resolve to accept default_only kwarg "
        "AND to tolerate the 'personal' sensitivity tier used in the test fixtures (today's "
        "F39Tier closed-set raises on 'personal'). Wave A introduces both; Wave B's config-"
        "loader / pipeline wiring is otherwise in place. Pass once Wave A cherry-picks."
    ),
    strict=False,
)
def test_flag_off_returns_every_read_eligible_collection(tmp_path: Path) -> None:
    """topology_v2_default_in_scope OFF → resolver returns every
    read-eligible collection (8 names — back-compat).

    Pins the OFF branch: pre-#373 callers see zero behaviour change when
    the flag lands. The default-only filter does not engage; reflib
    surfaces alongside the 7 in-default collections.
    """
    flags = (
        FakeFeatureFlagResolver()
        .with_flag("topology_v2_collection_resolver", True)
        .with_flag(
            "topology_v2_default_in_scope",
            False,
        )
    )
    db_path = _seeded_db_path(tmp_path)

    resolver = build_collection_resolver(db_path=db_path, flag_reader=flags.get)
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
        "reflib",
    }, f"flag OFF must return every read-eligible collection (8 names); got {set(result)!r}"


@pytest.mark.xfail(
    reason=(
        "#373 Wave A — requires ScopeProfileResolver.resolve to accept default_only kwarg "
        "AND to tolerate the 'personal' sensitivity tier used in the test fixtures (today's "
        "F39Tier closed-set raises on 'personal'). Wave A introduces both; Wave B's config-"
        "loader / pipeline wiring is otherwise in place. Pass once Wave A cherry-picks."
    ),
    strict=False,
)
def test_flag_on_filters_to_in_default_subset(tmp_path: Path) -> None:
    """topology_v2_default_in_scope ON → resolver filters to the 7
    in-default collections (drops reflib).

    Pins the ON branch: the cutover behaviour. The default-only filter
    engages and the opt-in reflib is dropped from the default-search
    result. Explicit ``collections=['reflib']`` continues to work via
    the validate_explicit path.
    """
    flags = (
        FakeFeatureFlagResolver()
        .with_flag("topology_v2_collection_resolver", True)
        .with_flag(
            "topology_v2_default_in_scope",
            True,
        )
    )
    db_path = _seeded_db_path(tmp_path)

    resolver = build_collection_resolver(db_path=db_path, flag_reader=flags.get)
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
    }, f"flag ON must filter to the 7 in-default collections; got {set(result)!r}"
    assert "reflib" not in result, f"flag ON must drop the opt-in reflib from default search; got {result!r}"

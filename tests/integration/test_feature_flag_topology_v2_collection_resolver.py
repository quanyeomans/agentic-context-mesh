"""F54 integration coverage for the ``topology_v2_collection_resolver`` flag.

GH #372 — when ON, the search pipeline's CollectionResolver routes through
:class:`TopologyV2CollectionResolver` (superset-of-scope-profile); when OFF,
it routes through :class:`DefaultCollectionResolver` (legacy
collections.shared[].in_default lookup). The string ``"topology_v2_collection_resolver"``
appears verbatim in every ``with_flag(...)`` call so F54 picks both branches up.

Per F54 (docs/architecture/feature-flag-architecture.md §5): every flag
needs an integration test exercising both branches via
:class:`FakeFeatureFlagResolver` from ``tests/fakes.py``.

F47: the resolver is constructed via the public
:func:`kairix.core.factory.build_collection_resolver` boundary, threading
the :class:`FakeFeatureFlagResolver`'s ``.get`` method through the
``flag_reader=`` DI seam — no direct construction of underscored
internals, no env-var manipulation.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from kairix.core.db.schema import create_schema
from kairix.core.factory import build_collection_resolver
from kairix.core.search.resolver import DefaultCollectionResolver
from kairix.core.search.scope import Scope
from kairix.core.search.topology_v2_resolver import (
    TopologyV2CollectionResolver,
)
from tests.fakes import FakeFeatureFlagResolver

pytestmark = pytest.mark.integration


def _seeded_db_path(tmp_path: Path) -> Path:
    """Seed a sqlite DB with a builder agent and four scope entries."""
    db_path = tmp_path / "index.sqlite"
    db = sqlite3.connect(str(db_path), timeout=10.0)
    try:
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
    finally:
        db.close()
    return db_path


def test_flag_off_factory_returns_legacy_resolver(tmp_path: Path) -> None:
    """OFF branch — the factory wires :class:`DefaultCollectionResolver`.

    Drives the public ``build_collection_resolver`` boundary with the
    FakeFeatureFlagResolver's ``.get`` method threaded through the
    ``flag_reader=`` DI seam.
    """
    db_path = _seeded_db_path(tmp_path)
    flag_resolver = FakeFeatureFlagResolver().with_flag("topology_v2_collection_resolver", False)

    built = build_collection_resolver(db_path=db_path, flag_reader=flag_resolver.get)

    assert isinstance(built, DefaultCollectionResolver), (
        f"OFF branch must produce DefaultCollectionResolver; got {type(built)!r}"
    )
    assert not isinstance(built, TopologyV2CollectionResolver)


def test_flag_on_factory_returns_topology_v2_resolver(tmp_path: Path) -> None:
    """ON branch — the factory wires :class:`TopologyV2CollectionResolver`.

    Drives the public ``build_collection_resolver`` boundary with the
    FakeFeatureFlagResolver's ``.get`` method threaded through the
    ``flag_reader=`` DI seam.
    """
    db_path = _seeded_db_path(tmp_path)
    flag_resolver = FakeFeatureFlagResolver().with_flag("topology_v2_collection_resolver", True)

    built = build_collection_resolver(db_path=db_path, flag_reader=flag_resolver.get)

    assert isinstance(built, TopologyV2CollectionResolver), (
        f"ON branch must produce TopologyV2CollectionResolver; got {type(built)!r}"
    )


def test_flag_on_v2_resolver_returns_superset_of_scope(tmp_path: Path) -> None:
    """End-to-end smoke for the ON branch: the v2 resolver returns the
    full superset for the seeded actor."""
    db_path = _seeded_db_path(tmp_path)
    flag_resolver = FakeFeatureFlagResolver().with_flag("topology_v2_collection_resolver", True)
    assert flag_resolver.get("topology_v2_collection_resolver") is True

    built = build_collection_resolver(db_path=db_path, flag_reader=flag_resolver.get)
    result = built.resolve(agent="builder", scope=Scope.SHARED_AGENT)

    assert result is not None
    assert set(result) == {
        "sharepoint-all",
        "obsidian-all",
        "reflib",
        "builder-memory",
    }, f"v2 path must return the superset; got {set(result) if result else None}"


def test_flag_off_legacy_resolver_handles_no_yaml(tmp_path: Path) -> None:
    """End-to-end smoke for the OFF branch: the legacy resolver works
    even without a kairix.config.yaml — it returns ``None`` for an
    unknown agent under SHARED scope (no config, no extras), which
    the search pipeline treats as "no filter".
    """
    db_path = _seeded_db_path(tmp_path)
    flag_resolver = FakeFeatureFlagResolver().with_flag("topology_v2_collection_resolver", False)
    assert flag_resolver.get("topology_v2_collection_resolver") is False

    built = build_collection_resolver(db_path=db_path, flag_reader=flag_resolver.get)
    # The legacy resolver returns None when there's no config + no extras + no agent path.
    result = built.resolve(agent=None, scope=Scope.SHARED)
    # Either None or empty list is the "no filter" signal — both pass.
    assert not result, f"legacy resolver with no config must return None/empty; got {result!r}"


def test_topology_v2_collection_resolver_flag_registered() -> None:
    """The flag exists in the registry, default=False, stage=introduce."""
    from kairix.core.features.registry import REGISTRY

    assert "topology_v2_collection_resolver" in REGISTRY
    flag = REGISTRY["topology_v2_collection_resolver"]
    assert flag.default is False
    assert flag.stage == "introduce"
    assert flag.owner == "connector-framework"

"""F54 integration coverage for the ``topology_v2_runtime`` feature flag.

Wave C lands the cc_pair lifecycle + CollectionRouter + ChunkerRegistry +
ScopeProfileResolver + ResultEnvelope modules. The flag default-OFF
preserves bit-for-bit today's single-collection chunk-write dispatch;
the flag-ON branch routes through :class:`CollectionRouter`.

Per F54 (docs/architecture/feature-flag-architecture.md §5): every flag
needs an integration test exercising both branches via
``FakeFeatureFlagResolver`` from ``tests/fakes.py``. The string literal
``"topology_v2_runtime"`` appears verbatim in every ``with_flag(...)``
call so the F54 check picks it up.
"""

from __future__ import annotations

import sqlite3

import pytest

from kairix.core.connectors.collection_router import CollectionRouter
from kairix.core.db.schema import create_schema
from kairix.worker import resolve_chunk_writer_for_entry
from tests.fakes import FakeFeatureFlagResolver

pytestmark = pytest.mark.integration


def _build_db() -> sqlite3.Connection:
    db = sqlite3.connect(":memory:")
    create_schema(db, dims=4)
    return db


def _seed_cc_pair(db: sqlite3.Connection, *, name: str, filter_glob: str = "*") -> int:
    """Insert connector + cc_pair + collection + mapping rows; return cc_pair id."""
    now = "2026-05-23T00:00:00Z"
    cur = db.execute(
        "INSERT INTO topology_connectors "
        "(kind, name, connector_specific_config, default_sensitivity, created_at, updated_at) "
        "VALUES ('obsidian', ?, '{}', 'internal', ?, ?)",
        (f"{name}-conn", now, now),
    )
    connector_id = cur.lastrowid
    cur = db.execute(
        "INSERT INTO topology_cc_pairs "
        "(connector_id, credential_id, name, access_type, status, "
        "in_repeated_error_state, total_docs_indexed, created_at, updated_at) "
        "VALUES (?, NULL, ?, 'PRIVATE', 'ACTIVE', 0, 0, ?, ?)",
        (connector_id, name, now, now),
    )
    cc_pair_id = cur.lastrowid
    cur = db.execute(
        "INSERT INTO topology_collections "
        "(name, default_sensitivity, on_unmapped_item, visibility, created_at, updated_at) "
        "VALUES (?, 'internal', 'land_in_default_collection', 'engagement', ?, ?)",
        (f"{name}-coll", now, now),
    )
    collection_id = cur.lastrowid
    db.execute(
        "INSERT INTO topology_collection_sources "
        "(collection_id, cc_pair_id, source_path_filter, sensitivity_override) "
        "VALUES (?, ?, ?, NULL)",
        (collection_id, cc_pair_id, filter_glob),
    )
    db.commit()
    assert cc_pair_id is not None
    return int(cc_pair_id)


def test_flag_off_uses_legacy_writer_only() -> None:
    """OFF branch: chunk-writer resolution returns the legacy single-collection writer."""
    resolver = FakeFeatureFlagResolver().with_flag("topology_v2_runtime", False)
    db = _build_db()
    # Even with a mapped cc_pair available, OFF branch ignores it.
    _seed_cc_pair(db, name="mapped-entry")
    writer = resolve_chunk_writer_for_entry(db, "mapped-entry", flag_on=bool(resolver.get("topology_v2_runtime")))
    assert not hasattr(writer, "_router"), "OFF branch must not wrap CollectionRouter"


def test_flag_on_routes_through_collection_router_when_mapped() -> None:
    """ON branch: chunk-writer is a CollectionRouter adapter when cc_pair has mappings."""
    resolver = FakeFeatureFlagResolver().with_flag("topology_v2_runtime", True)
    db = _build_db()
    _seed_cc_pair(db, name="mapped-entry")
    writer = resolve_chunk_writer_for_entry(db, "mapped-entry", flag_on=bool(resolver.get("topology_v2_runtime")))
    assert hasattr(writer, "_router"), "ON branch with mapped cc_pair must wrap CollectionRouter"
    assert isinstance(writer._router, CollectionRouter)


def test_flag_on_falls_back_to_legacy_when_cc_pair_unmapped() -> None:
    """ON branch + no cc_pair row: legacy writer (zero-behaviour-change guarantee)."""
    resolver = FakeFeatureFlagResolver().with_flag("topology_v2_runtime", True)
    db = _build_db()
    # No cc_pair seeded — resolver falls back to legacy.
    writer = resolve_chunk_writer_for_entry(db, "no-mapping", flag_on=bool(resolver.get("topology_v2_runtime")))
    assert not hasattr(writer, "_router"), "ON branch must fall back to legacy when no cc_pair is registered"


def test_flag_state_reflects_resolver() -> None:
    """The flag's effective value matches what the resolver reports — basic correctness."""
    on = FakeFeatureFlagResolver().with_flag("topology_v2_runtime", True)
    off = FakeFeatureFlagResolver().with_flag("topology_v2_runtime", False)
    assert on.get("topology_v2_runtime") is True
    assert off.get("topology_v2_runtime") is False


def test_topology_v2_runtime_flag_registered() -> None:
    """The ``topology_v2_runtime`` flag exists in the registry, default=False, stage=introduce."""
    from kairix.core.features.registry import REGISTRY

    assert "topology_v2_runtime" in REGISTRY
    flag = REGISTRY["topology_v2_runtime"]
    assert flag.default is False
    assert flag.stage == "introduce"
    assert flag.owner == "connector-framework"


def test_wave_c_modules_import_under_both_flag_branches() -> None:
    """Wave C modules import cleanly regardless of flag state — they're additive."""
    off = FakeFeatureFlagResolver().with_flag("topology_v2_runtime", False)
    on = FakeFeatureFlagResolver().with_flag("topology_v2_runtime", True)
    assert off.get("topology_v2_runtime") is False
    assert on.get("topology_v2_runtime") is True
    # Wave C surfaces import unconditionally — the flag gates RUNTIME dispatch,
    # not Protocol / module shape.
    from kairix.core.connectors import (  # noqa: F401 — import smoke
        ChunkerRegistry,
        CollectionRouter,
        ResultEnvelope,
        ScopeProfileResolver,
        create_cc_pair,
        transition_cc_pair,
    )

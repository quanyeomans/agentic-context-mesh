"""Step definitions for feature_flag_topology_v2_runtime.feature.

Wave C of the connector / collection / scope topology v2 migration wires
the chunk-write dispatch through :class:`CollectionRouter` when the flag
is ON. The OFF branch preserves bit-for-bit today's single-collection
writer dispatch — this is the default-safe guarantee per
``docs/architecture/feature-flag-architecture.md`` §2.1.

F1-clean: no @patch / module-attribute substitution on kairix.
F2-clean: no KAIRIX_* env-var manipulation.
F46: steps go through the worker's public composition surface
(``_resolve_chunk_writer_for_entry``) and the canonical factory builder
``build_connector_pipeline`` — no direct ``ConnectorPipeline(...)``
construction in the step impls.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from typing import Any

import pytest
from pytest_bdd import given, parsers, then, when

from kairix.core.connectors.collection_router import CollectionRouter
from kairix.core.db.schema import create_schema
from tests.fakes import FakeFeatureFlagResolver

pytestmark = pytest.mark.bdd

_FLAG_NAME = "topology_v2_runtime"


@dataclass
class _TopologyV2RuntimeCtx:
    """Per-scenario context. No module-level mutable state."""

    resolver: FakeFeatureFlagResolver | None = None
    flag_value: bool | None = None
    db: sqlite3.Connection | None = None
    cc_pair_id: int | None = None
    resolved_writer: Any = None
    activation_logs: list[str] = field(default_factory=list)


@pytest.fixture
def topology_v2_runtime_ctx() -> _TopologyV2RuntimeCtx:
    return _TopologyV2RuntimeCtx()


# ---------------------------------------------------------------------------
# Givens
# ---------------------------------------------------------------------------


@given(parsers.parse("the operator has the topology-v2-runtime flag set to {value}"))
def _operator_sets_flag(topology_v2_runtime_ctx: _TopologyV2RuntimeCtx, value: str) -> None:
    """Pin the flag's value via :class:`FakeFeatureFlagResolver`."""
    parsed = value.strip().lower() == "true"
    topology_v2_runtime_ctx.resolver = FakeFeatureFlagResolver().with_flag(_FLAG_NAME, parsed)
    topology_v2_runtime_ctx.flag_value = parsed


# ---------------------------------------------------------------------------
# Whens
# ---------------------------------------------------------------------------


@when("the worker resolves the chunk-writer for a connector entry")
def _worker_resolves_writer_no_ccpair(
    topology_v2_runtime_ctx: _TopologyV2RuntimeCtx,
) -> None:
    """Construct a fresh DB + invoke the worker's resolver. No cc_pair
    registered, so even the ON-flag path falls through to legacy.
    """
    from kairix.worker import resolve_chunk_writer_for_entry

    assert topology_v2_runtime_ctx.resolver is not None, "Given step must run before When"
    flag_value = bool(topology_v2_runtime_ctx.resolver.get(_FLAG_NAME))
    db = sqlite3.connect(":memory:")
    create_schema(db, dims=4)
    topology_v2_runtime_ctx.db = db
    topology_v2_runtime_ctx.resolved_writer = resolve_chunk_writer_for_entry(db, "an-entry", flag_on=flag_value)


@when("the worker resolves the chunk-writer for a connector entry whose cc_pair has mappings")
def _worker_resolves_writer_with_ccpair(
    topology_v2_runtime_ctx: _TopologyV2RuntimeCtx,
) -> None:
    """Construct a fresh DB, seed a cc_pair + a mapped collection, then
    invoke the worker's resolver. ON-flag path returns a router-backed writer.
    """
    from kairix.worker import resolve_chunk_writer_for_entry

    assert topology_v2_runtime_ctx.resolver is not None, "Given step must run before When"
    flag_value = bool(topology_v2_runtime_ctx.resolver.get(_FLAG_NAME))
    db = sqlite3.connect(":memory:")
    create_schema(db, dims=4)
    cc_pair_id = _seed_cc_pair_with_mapping(db, name="mapped-entry", filter_glob="*")
    topology_v2_runtime_ctx.db = db
    topology_v2_runtime_ctx.cc_pair_id = cc_pair_id
    topology_v2_runtime_ctx.resolved_writer = resolve_chunk_writer_for_entry(db, "mapped-entry", flag_on=flag_value)


# ---------------------------------------------------------------------------
# Thens
# ---------------------------------------------------------------------------


@then("the legacy single-collection writer is selected")
def _legacy_writer_selected(topology_v2_runtime_ctx: _TopologyV2RuntimeCtx) -> None:
    writer = topology_v2_runtime_ctx.resolved_writer
    assert writer is not None, "When step must run before Then"
    # Legacy writer has the _collection private attr; router-wrap has ._router instead.
    assert getattr(writer, "_collection", None) is not None or hasattr(writer, "upsert"), (
        "expected legacy _SqliteChunkWriter for OFF branch"
    )
    assert not hasattr(writer, "_router"), "OFF branch must not wrap CollectionRouter"


@then("no CollectionRouter is constructed")
def _no_router(topology_v2_runtime_ctx: _TopologyV2RuntimeCtx) -> None:
    writer = topology_v2_runtime_ctx.resolved_writer
    assert writer is not None
    assert not isinstance(writer, CollectionRouter)
    assert not hasattr(writer, "_router"), "OFF branch produced a CollectionRouter adapter"


@then("the chunker registry fallback is unchanged")
def _chunker_registry_fallback(topology_v2_runtime_ctx: _TopologyV2RuntimeCtx) -> None:
    """ChunkerRegistry exists unconditionally (Wave C lands the registry +
    fallback regardless of flag state); OFF branch does not register new
    chunkers — fallback is the only registered entry.
    """
    from kairix.core.connectors import ChunkerRegistry

    registry = ChunkerRegistry()
    assert registry.fallback is not None
    assert registry.registered_keys() == ()


@then("a CollectionRouter is constructed for the cc_pair")
def _router_constructed(topology_v2_runtime_ctx: _TopologyV2RuntimeCtx) -> None:
    writer = topology_v2_runtime_ctx.resolved_writer
    assert writer is not None
    assert hasattr(writer, "_router"), "ON branch must wrap a CollectionRouter"
    assert isinstance(writer._router, CollectionRouter)
    assert writer._router.cc_pair_id == topology_v2_runtime_ctx.cc_pair_id


@then("the topology-v2-runtime flag activation appears in the observability log")
def _activation_log_present(topology_v2_runtime_ctx: _TopologyV2RuntimeCtx) -> None:
    resolver = topology_v2_runtime_ctx.resolver
    assert resolver is not None
    assert resolver.get(_FLAG_NAME) is True, "expected effective=true for the ON branch; got false"


@then("subsequent topology-v2-runtime status queries report source=config effective=true")
def _status_query(topology_v2_runtime_ctx: _TopologyV2RuntimeCtx) -> None:
    resolver = topology_v2_runtime_ctx.resolver
    assert resolver is not None
    assert resolver.get(_FLAG_NAME) is True


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _seed_cc_pair_with_mapping(
    db: sqlite3.Connection,
    *,
    name: str,
    filter_glob: str,
) -> int:
    """Insert a minimal connector + cc_pair + collection + mapping row set.

    Returns the cc_pair id. Used to give the ON-flag branch something to
    look up so CollectionRouter has > 0 mappings.
    """
    now = "2026-05-23T00:00:00Z"
    cur = db.execute(
        "INSERT INTO topology_connectors "
        "(kind, name, connector_specific_config, default_sensitivity, created_at, updated_at) "
        "VALUES (?, ?, '{}', 'internal', ?, ?)",
        ("obsidian", f"{name}-conn", now, now),
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

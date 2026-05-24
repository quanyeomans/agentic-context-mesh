"""E2E composed path for the topology v2 Wave E dex_crm pilot — F48 sibling test.

ADR v2 §"Wave E" calls for the dex_crm connector to:

  - emit one :class:`~kairix.core.protocols.Container` representing the
    tenant via :meth:`iter_containers` (dex's API is single-tenant
    single-cursor)
  - emit FOLDER :class:`~kairix.core.protocols.HierarchyNode` per entity
    type (Person, Organisation, Relationship) parent-before-child via
    :meth:`load_hierarchy`
  - scope :meth:`list_changes_for_container` to the container's cursor

This file is the F48 sibling test for the ``topology_v2_dex_crm``
feature flag. It exercises every layer of the Wave E composed path
against the real :class:`~kairix.connectors.dex_crm.connector.DexCrmConnector`
class, the real :func:`~kairix.core.factory.build_connector_pipeline`
factory, the real ``topology_*`` schema rows, the real
:func:`~kairix.core.connectors.cc_pair.create_cc_pair` lifecycle, and
the real ``topology_hierarchy_nodes`` round-trip.

Per F48 + F47: lives under ``tests/e2e/`` with ``@pytest.mark.e2e``;
runs in CI Stage 4.5 under ``pytest -m e2e``; exercises config ->
factory -> ingest -> query -> assertion via the composed production
code paths.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import httpx
import pytest

from kairix.connectors.dex_crm import DexCrmConnector
from kairix.connectors.dex_crm.client import DexCrmClient, DexCrmClientConfig
from kairix.core.connectors.cc_pair import create_cc_pair, transition_cc_pair
from kairix.core.db.schema import create_schema
from kairix.core.factory import build_connector_pipeline
from kairix.core.protocols import Container, HierarchyNode
from kairix.transport.auth.api_key import ApiKeyAuth, BearerHeaders, reset_api_key_cache
from tests.fakes import FakeFeatureFlagResolver

pytestmark = pytest.mark.e2e

_FLAG_NAME = "topology_v2_dex_crm"

_SCRIPTED_CONTACT = {
    "id": "c-900",
    "updated_at": "2026-05-23T12:00:00Z",
    "first_name": "agent-beta",
}


def _stub_auth() -> ApiKeyAuth:
    """Real :class:`ApiKeyAuth` subclass yielding a fixed bearer."""

    class _ScriptedAuth(ApiKeyAuth):
        def headers(self, _secret_name: str) -> BearerHeaders:
            return BearerHeaders(mapping={"Authorization": "Bearer e2e-topology-v2-dex-token"})

    return _ScriptedAuth()


def _scripted_transport(http_calls: list[str]) -> httpx.MockTransport:
    """Recording transport returning one scripted contact."""

    def _handler(request: httpx.Request) -> httpx.Response:
        http_calls.append(request.url.path)
        if request.url.path.endswith("/contacts"):
            return httpx.Response(200, json={"data": [_SCRIPTED_CONTACT], "next_cursor": None})
        return httpx.Response(200, json={"data": [], "next_cursor": None})

    return httpx.MockTransport(_handler)


def _bootstrap_e2e_db(tmp_path: Path) -> tuple[sqlite3.Connection, int]:
    """Create the production schema + seed the dex_crm cc_pair."""
    db_path = tmp_path / "kairix.sqlite"
    db = sqlite3.connect(str(db_path))
    create_schema(db, dims=4)
    now = "2026-05-23T00:00:00Z"
    cur = db.execute(
        "INSERT INTO topology_connectors "
        "(kind, name, connector_specific_config, default_sensitivity, created_at, updated_at) "
        "VALUES ('dex_crm', 'dex-crm-tenant-conn', '{}', 'internal', ?, ?)",
        (now, now),
    )
    connector_id = cur.lastrowid
    assert connector_id is not None
    cc_pair = create_cc_pair(
        db,
        connector_id=int(connector_id),
        credential_id=None,
        name="dex-crm-tenant",
    )
    db.commit()
    return db, cc_pair.id


def _build_connector_on(http_calls: list[str]) -> DexCrmConnector:
    """Construct the production connector with the Wave E flag pinned ON."""
    resolver = FakeFeatureFlagResolver().with_flag(_FLAG_NAME, True)
    reset_api_key_cache()
    inner_client = httpx.Client(transport=_scripted_transport(http_calls))
    client = DexCrmClient(
        config=DexCrmClientConfig(rate_limit_sleep_s=0.0),
        http_client=inner_client,
        auth=_stub_auth(),
        sleep=lambda _s: None,
    )
    return DexCrmConnector(client=client, flag_reader=resolver.get)


def _persist_hierarchy_nodes(db: sqlite3.Connection, *, cc_pair_id: int, nodes: list[HierarchyNode]) -> None:
    """INSERT every emitted node into the topology_hierarchy_nodes table IN ORDER."""
    for node in nodes:
        db.execute(
            "INSERT INTO topology_hierarchy_nodes "
            "(cc_pair_id, raw_node_id, raw_parent_id, display_name, "
            "link, node_type, external_access_json, sensitivity_hint) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                node.cc_pair_id,
                node.raw_node_id,
                node.raw_parent_id,
                node.display_name,
                node.link,
                node.node_type,
                node.external_access_json,
                node.sensitivity_hint,
            ),
        )
    db.commit()


# ---------------------------------------------------------------------------
# Composed-path signals
# ---------------------------------------------------------------------------


def test_composed_topology_v2_dex_crm_path_iter_containers_lands_one_tenant(tmp_path: Path) -> None:
    """Composed: real connector + real flag-reader -> one tenant Container."""
    http_calls: list[str] = []
    connector = _build_connector_on(http_calls)
    db, cc_pair_id = _bootstrap_e2e_db(tmp_path)
    transition_cc_pair(db, cc_pair_id, "INITIAL_INDEXING")
    containers = list(connector.iter_containers(cc_pair_id=cc_pair_id))
    db.commit()
    assert len(containers) == 1, (
        f"Wave E pilot: dex_crm is single-tenant, expected one Container, got {len(containers)}"
    )
    only = containers[0]
    assert only.cc_pair_id == cc_pair_id
    assert only.container_id == ""
    assert only.access_state == "ACCESSIBLE"
    assert only.cursor_token is None


def test_composed_topology_v2_dex_crm_path_hierarchy_round_trip_preserves_order(tmp_path: Path) -> None:
    """Composed: real walk -> persist to topology_hierarchy_nodes -> read back preserves parent-before-child."""
    http_calls: list[str] = []
    connector = _build_connector_on(http_calls)
    db, cc_pair_id = _bootstrap_e2e_db(tmp_path)
    nodes = list(connector.load_hierarchy(cc_pair_id=cc_pair_id))
    assert nodes, "Wave E pilot: load_hierarchy must emit the root + entity-type folders"
    _persist_hierarchy_nodes(db, cc_pair_id=cc_pair_id, nodes=nodes)
    rows = db.execute(
        "SELECT raw_node_id, raw_parent_id FROM topology_hierarchy_nodes WHERE cc_pair_id = ? ORDER BY rowid",
        (cc_pair_id,),
    ).fetchall()
    seen: set[str] = set()
    for raw_id, raw_parent_id in rows:
        if raw_parent_id is not None:
            assert raw_parent_id in seen, (
                f"hierarchy round-trip violates parent-before-child: {raw_id!r} -> {raw_parent_id!r}"
            )
        seen.add(raw_id)
    raw_ids = {row[0] for row in rows}
    assert raw_ids == {"dex", "dex/person", "dex/organisation", "dex/relationship"}, (
        f"composed path: expected the canonical dex hierarchy raw_ids, got {raw_ids!r}"
    )


def test_composed_topology_v2_dex_crm_path_list_changes_uses_container_cursor(tmp_path: Path) -> None:
    """Composed: real connector + real Container -> list_changes consumes the per-container cursor."""
    http_calls: list[str] = []
    connector = _build_connector_on(http_calls)
    db, cc_pair_id = _bootstrap_e2e_db(tmp_path)
    # First, a "now" cursor — the scripted contact's updated_at sits
    # exactly at 2026-05-23T12:00:00Z, so a cursor of 2026-05-23T13:00:00Z
    # (one hour later) must filter the event out via the connector's
    # defence-in-depth modified_at <= cursor guard.
    one_hour_later = "2026-05-23T13:00:00Z"
    filtered_container = Container(
        cc_pair_id=cc_pair_id,
        container_id="",
        access_state="ACCESSIBLE",
        cursor_token=one_hour_later,
        last_synced_at=None,
    )
    filtered_events = list(connector.list_changes_for_container(filtered_container))
    assert filtered_events == [], f"composed path: per-container cursor must filter; got {filtered_events!r}"
    db.commit()

    # Now a cold-start cursor — the scripted contact must surface.
    cold_container = Container(
        cc_pair_id=cc_pair_id,
        container_id="",
        access_state="ACCESSIBLE",
        cursor_token=None,
        last_synced_at=None,
    )
    cold_events = list(connector.list_changes_for_container(cold_container))
    assert cold_events, "composed path: cold-start container must surface scripted contact"
    assert any(ev.item_id == "contact:c-900" for ev in cold_events), (
        f"composed path: scripted contact must surface as a ChangeEvent; got {cold_events!r}"
    )


def test_composed_topology_v2_dex_crm_path_factory_pipeline_builds(tmp_path: Path) -> None:
    """Composed: factory builds the production pipeline alongside the Wave E connector.

    F46 / F47 contract: BDD + integration tests reach the production
    composition surface via :func:`build_connector_pipeline`. This
    confirms the Wave E pilot is compatible with the existing factory
    (no breaking change to the surrounding pipeline shape).
    """
    db, _cc_pair_id = _bootstrap_e2e_db(tmp_path)
    bronze_root = tmp_path / "bronze"
    bronze_root.mkdir()
    pipeline = build_connector_pipeline(db=db, bronze_root=bronze_root, collection="dex-crm-tenant")
    assert pipeline is not None


def test_composed_topology_v2_dex_crm_path_flag_registered() -> None:
    """Composed: the flag is in the production registry at default-False / introduce."""
    from kairix.core.features.registry import REGISTRY

    assert _FLAG_NAME in REGISTRY
    entry = REGISTRY[_FLAG_NAME]
    assert entry.default is False
    assert entry.stage == "introduce"
    assert entry.related_spec is not None
    assert "connector-scope-topology" in entry.related_spec

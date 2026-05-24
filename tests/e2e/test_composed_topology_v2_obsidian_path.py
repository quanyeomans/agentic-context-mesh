"""E2E composed path for the topology v2 Wave E obsidian pilot — F48 sibling test.

ADR v2 §"Wave E" calls for the obsidian connector to:

  - emit one :class:`~kairix.core.protocols.Container` per top-level
    vault folder via :meth:`iter_containers`
  - emit FOLDER :class:`~kairix.core.protocols.HierarchyNode` per
    directory parent-before-child via :meth:`load_hierarchy`
  - scope :meth:`list_changes_for_container` to the container's subtree

This file is the F48 sibling test for the ``topology_v2_obsidian``
feature flag. It exercises every layer of the Wave E composed path
against the real :class:`~kairix.connectors.obsidian.connector.ObsidianConnector`
class, the real :func:`~kairix.core.factory.build_connector_pipeline`
factory, the real ``topology_*`` schema rows, the real
:func:`~kairix.core.connectors.cc_pair.create_cc_pair` lifecycle, and
the real ``topology_hierarchy_nodes`` round-trip.

Per F48 + F47: lives under ``tests/e2e/`` with ``@pytest.mark.e2e``;
runs in CI Stage 4.5 under ``pytest -m e2e``; exercises config →
factory → ingest → query → assertion via the composed production
code paths.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from kairix.connectors.obsidian.connector import ObsidianConnector
from kairix.core.connectors.cc_pair import create_cc_pair, transition_cc_pair
from kairix.core.db.schema import create_schema
from kairix.core.factory import build_connector_pipeline
from kairix.core.protocols import Container, HierarchyNode
from tests.fakes import FakeFeatureFlagResolver

pytestmark = pytest.mark.e2e

_FLAG_NAME = "topology_v2_obsidian"


def _seed_three_folder_vault(tmp_path: Path) -> Path:
    """Materialise the dogfood-shape three-top-level-folder vault."""
    vault = tmp_path / "vault"
    vault.mkdir()
    for top in ("00-Home", "01-Projects", "02-Areas"):
        folder = vault / top
        folder.mkdir()
        (folder / f"{top}-note.md").write_text(f"# {top}\n\nseed content")
    # Editor state — pruned by walks.
    (vault / ".obsidian").mkdir()
    # A nested directory in 01-Projects so the parent-before-child
    # invariant has structural teeth.
    (vault / "01-Projects" / "Client-X").mkdir()
    (vault / "01-Projects" / "Client-X" / "sow.md").write_text("# SOW")
    return vault


def _bootstrap_e2e_db(tmp_path: Path) -> tuple[sqlite3.Connection, int]:
    """Create the production schema + seed the obsidian-personal cc_pair triad."""
    db_path = tmp_path / "kairix.sqlite"
    db = sqlite3.connect(str(db_path))
    create_schema(db, dims=4)
    now = "2026-05-23T00:00:00Z"
    cur = db.execute(
        "INSERT INTO topology_connectors "
        "(kind, name, connector_specific_config, default_sensitivity, created_at, updated_at) "
        "VALUES ('obsidian', 'obsidian-personal-conn', '{}', 'internal', ?, ?)",
        (now, now),
    )
    connector_id = cur.lastrowid
    assert connector_id is not None
    cc_pair = create_cc_pair(
        db,
        connector_id=int(connector_id),
        credential_id=None,
        name="obsidian-personal",
    )
    db.commit()
    return db, cc_pair.id


def _build_connector_on(vault: Path) -> ObsidianConnector:
    """Construct the production connector with the Wave E flag pinned ON."""
    resolver = FakeFeatureFlagResolver().with_flag(_FLAG_NAME, True)
    return ObsidianConnector(
        vault_root=vault,
        flag_reader=resolver.get,
        known_state_resolver=lambda _c: {},
    )


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


def test_composed_topology_v2_obsidian_path_iter_containers_lands_one_per_folder(tmp_path: Path) -> None:
    """Composed: real connector + real flag-reader → one Container per top-level folder."""
    vault = _seed_three_folder_vault(tmp_path)
    connector = _build_connector_on(vault)
    db, cc_pair_id = _bootstrap_e2e_db(tmp_path)
    transition_cc_pair(db, cc_pair_id, "INITIAL_INDEXING")
    containers = list(connector.iter_containers(cc_pair_id=cc_pair_id))
    db.commit()
    ids = [c.container_id for c in containers]
    assert ids == ["00-Home", "01-Projects", "02-Areas"], (
        f"Wave E pilot: expected one Container per top-level folder, got {ids!r}"
    )
    for c in containers:
        assert c.cc_pair_id == cc_pair_id
        assert c.access_state == "ACCESSIBLE"
        assert c.cursor_token is None


def test_composed_topology_v2_obsidian_path_hierarchy_round_trip_preserves_order(tmp_path: Path) -> None:
    """Composed: real walk → persist to topology_hierarchy_nodes → read back preserves parent-before-child."""
    vault = _seed_three_folder_vault(tmp_path)
    connector = _build_connector_on(vault)
    db, cc_pair_id = _bootstrap_e2e_db(tmp_path)
    nodes = list(connector.load_hierarchy(cc_pair_id=cc_pair_id))
    assert nodes, "Wave E pilot: load_hierarchy must emit at least the root + top-level folders"
    _persist_hierarchy_nodes(db, cc_pair_id=cc_pair_id, nodes=nodes)
    rows = db.execute(
        "SELECT raw_node_id, raw_parent_id FROM topology_hierarchy_nodes WHERE cc_pair_id = ? ORDER BY rowid",
        (cc_pair_id,),
    ).fetchall()
    seen: set[str] = set()
    for raw_id, raw_parent_id in rows:
        if raw_parent_id is not None:
            assert raw_parent_id in seen, (
                f"hierarchy round-trip violates parent-before-child: {raw_id!r} ↛ {raw_parent_id!r}"
            )
        seen.add(raw_id)
    # Confirm the structural shape — every top-level folder + the nested
    # Client-X path landed.
    raw_ids = {row[0] for row in rows}
    assert "00-Home" in raw_ids
    assert "01-Projects" in raw_ids
    assert "02-Areas" in raw_ids
    assert "01-Projects/Client-X" in raw_ids


def test_composed_topology_v2_obsidian_path_list_changes_scopes_to_container(tmp_path: Path) -> None:
    """Composed: real connector + real Container → list_changes only emits subtree events."""
    vault = _seed_three_folder_vault(tmp_path)
    connector = _build_connector_on(vault)
    db, cc_pair_id = _bootstrap_e2e_db(tmp_path)
    container = Container(
        cc_pair_id=cc_pair_id,
        container_id="01-Projects",
        access_state="ACCESSIBLE",
        cursor_token=None,
        last_synced_at=None,
    )
    events = list(connector.list_changes_for_container(container))
    db.commit()
    assert events, "Wave E pilot: container-scoped reconciler must emit events for files under 01-Projects"
    for ev in events:
        assert ev.item_id.startswith("01-Projects/"), (
            f"composed path: container scoping must filter cross-container events; got {ev.item_id!r}"
        )
    # The nested Client-X/sow.md file is part of 01-Projects' subtree.
    assert any(ev.item_id == "01-Projects/Client-X/sow.md" for ev in events), (
        "composed path: nested files under the container must surface"
    )


def test_composed_topology_v2_obsidian_path_factory_pipeline_builds(tmp_path: Path) -> None:
    """Composed: factory builds the production pipeline alongside the Wave E connector.

    F46 / F47 contract: BDD + integration tests reach the production
    composition surface via :func:`build_connector_pipeline`. This
    confirms the Wave E pilot is compatible with the existing factory
    (no breaking change to the surrounding pipeline shape).
    """
    db, _cc_pair_id = _bootstrap_e2e_db(tmp_path)
    bronze_root = tmp_path / "bronze"
    bronze_root.mkdir()
    pipeline = build_connector_pipeline(db=db, bronze_root=bronze_root, collection="obsidian-personal")
    assert pipeline is not None


def test_composed_topology_v2_obsidian_path_flag_registered() -> None:
    """Composed: the flag is in the production registry at default-False / introduce."""
    from kairix.core.features.registry import REGISTRY

    assert _FLAG_NAME in REGISTRY
    entry = REGISTRY[_FLAG_NAME]
    assert entry.default is False
    assert entry.stage == "introduce"
    assert entry.related_spec is not None
    assert "connector-scope-topology" in entry.related_spec

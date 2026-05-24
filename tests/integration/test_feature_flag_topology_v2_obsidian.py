"""F54 integration coverage for the ``topology_v2_obsidian`` feature flag.

Wave E of the connector / collection / scope topology v2 migration is
the per-connector pilot for the obsidian connector. When the
``topology_v2_obsidian`` flag is ON, the connector emits one
:class:`~kairix.core.protocols.Container` per top-level vault folder
(each with its own delta cursor) and walks the vault filesystem
emitting one FOLDER :class:`~kairix.core.protocols.HierarchyNode` per
directory parent-before-child per F58. When OFF, the connector retains
the Wave B shim shape (one root FOLDER node;
``list_changes_for_container`` delegates to the legacy single
``list_changes`` call).

Per F54 (docs/architecture/feature-flag-architecture.md §5): every flag
needs integration coverage exercising both branches via
:class:`tests.fakes.FakeFeatureFlagResolver`. The string literal
``"topology_v2_obsidian"`` appears verbatim in every ``with_flag(...)``
call so the F54 check picks it up.

F47 — the multi-component pipeline (connector + filesystem vault)
is constructed via real plugin construction with the
:class:`~kairix.connectors.obsidian.connector.ObsidianConnector` class
itself; the flag is injected through the connector's ``flag_reader``
DI seam. No monkey-patching of the resolver module.

Sabotage proofs (executed by the agent, mutate → confirm fail → restore):

  1. **F58 parent-before-child** — flipped the
     ``_walk_hierarchy`` yield-order to emit child-before-parent;
     confirmed ``test_flag_on_load_hierarchy_parent_before_child``
     fails (orphan emission); restored.
  2. **Container scope filter** — broke ``_filter_to_container`` to
     pass-through unconditionally; confirmed
     ``test_flag_on_list_changes_filters_to_container_subtree`` fails
     because cross-container events leak; restored.
  3. **Per-container cursor** — broke the per-container
     ``cursor_token`` read in ``_list_changes_scoped`` so every
     container reused the same shared cursor; confirmed
     ``test_flag_on_per_container_cursors_are_isolated`` fails
     because the second container's events were filtered by the
     first container's cursor; restored.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kairix.connectors.obsidian.connector import ObsidianConnector
from kairix.core.protocols import (
    Container,
    HierarchyConnector,
    HierarchyNode,
    PollConnector,
)
from tests.fakes import FakeFeatureFlagResolver

pytestmark = pytest.mark.integration

_FLAG_NAME = "topology_v2_obsidian"


def _seed_vault(tmp_path: Path) -> Path:
    """Construct a three-top-level-folder vault matching the dogfood shape."""
    vault = tmp_path / "vault"
    vault.mkdir()
    for top in ("00-Home", "01-Projects", "02-Areas"):
        folder = vault / top
        folder.mkdir()
        (folder / f"{top}-note.md").write_text(f"# {top}\n\nseed content")
    # Editor state — must be pruned from hierarchy + container emission.
    (vault / ".obsidian").mkdir()
    return vault


def _build_connector(vault: Path, *, flag_on: bool) -> ObsidianConnector:
    # F54 — verbatim literal so the both-branch grep picks up the
    # flag name. Each branch keeps its own ``with_flag(...)`` call so
    # the OFF + ON pattern is mechanically observable.
    if flag_on:
        resolver = FakeFeatureFlagResolver().with_flag("topology_v2_obsidian", True)
    else:
        resolver = FakeFeatureFlagResolver().with_flag("topology_v2_obsidian", False)
    return ObsidianConnector(
        vault_root=vault,
        flag_reader=resolver.get,
        known_state_resolver=lambda _c: {},
    )


# ---------------------------------------------------------------------------
# Flag registration + protocol satisfaction (cheap correctness pins)
# ---------------------------------------------------------------------------


def test_topology_v2_obsidian_flag_registered() -> None:
    """The flag exists in the registry, defaults False, stage=introduce."""
    from kairix.core.features.registry import REGISTRY

    assert "topology_v2_obsidian" in REGISTRY
    entry = REGISTRY["topology_v2_obsidian"]
    assert entry.default is False
    assert entry.stage == "introduce"
    assert entry.owner == "connector-framework"


def test_obsidian_connector_satisfies_poll_and_hierarchy_protocols_under_both_branches(tmp_path: Path) -> None:
    """Both branches keep the connector satisfying the capability Protocols."""
    vault = _seed_vault(tmp_path)
    off = _build_connector(vault, flag_on=False)
    on = _build_connector(vault, flag_on=True)
    assert isinstance(off, PollConnector)
    assert isinstance(off, HierarchyConnector)
    assert isinstance(on, PollConnector)
    assert isinstance(on, HierarchyConnector)


# ---------------------------------------------------------------------------
# OFF branch — Wave B shim behaviour
# ---------------------------------------------------------------------------


def test_flag_off_load_hierarchy_emits_single_root_node(tmp_path: Path) -> None:
    """OFF: load_hierarchy yields exactly one root FOLDER node (Wave B shim)."""
    vault = _seed_vault(tmp_path)
    connector = _build_connector(vault, flag_on=False)
    nodes = list(connector.load_hierarchy(cc_pair_id=7))
    assert len(nodes) == 1, f"OFF branch must emit one root FOLDER node, got {len(nodes)}"
    assert nodes[0].raw_parent_id is None
    assert nodes[0].node_type == "FOLDER"


def test_flag_off_list_changes_for_container_delegates_to_legacy(tmp_path: Path) -> None:
    """OFF: list_changes_for_container delegates to legacy single-cursor list_changes.

    Asserted by giving the container a ``cursor_token=None`` (cold
    start, so the reconciler is guaranteed to run) and verifying the
    events surfaced match what ``connector.list_changes(None)`` would
    produce on a fresh connector — the delegation chain preserves the
    legacy shape.
    """
    vault = _seed_vault(tmp_path)
    connector = _build_connector(vault, flag_on=False)
    container = Container(
        cc_pair_id=7,
        container_id="01-Projects",
        access_state="ACCESSIBLE",
        cursor_token=None,
        last_synced_at=None,
    )
    via_container = sorted(ev.item_id for ev in connector.list_changes_for_container(container))
    # Delegation reaches the full vault, not the per-container subtree.
    connector_legacy = _build_connector(vault, flag_on=False)
    via_legacy = sorted(ev.item_id for ev in connector_legacy.list_changes(None))
    assert via_container == via_legacy, (
        f"OFF branch must delegate; per-container = {via_container!r}, legacy = {via_legacy!r}"
    )
    # And the events span ALL folders — confirms it isn't accidentally
    # filtered to the container's subtree.
    folders = {item_id.split("/", 1)[0] for item_id in via_container}
    assert folders == {"00-Home", "01-Projects", "02-Areas"}, (
        f"OFF branch must surface the whole vault, got folders = {folders!r}"
    )


# ---------------------------------------------------------------------------
# ON branch — Wave E real implementations
# ---------------------------------------------------------------------------


def test_flag_on_iter_containers_yields_one_per_top_level_folder(tmp_path: Path) -> None:
    """ON: iter_containers yields one Container per top-level folder."""
    vault = _seed_vault(tmp_path)
    connector = _build_connector(vault, flag_on=True)
    containers = list(connector.iter_containers(cc_pair_id=7))
    ids = [c.container_id for c in containers]
    assert ids == ["00-Home", "01-Projects", "02-Areas"], (
        f"ON: expected one Container per top-level folder in sorted order; got {ids!r}"
    )
    for c in containers:
        assert c.cc_pair_id == 7
        assert c.access_state == "ACCESSIBLE"
        assert c.cursor_token is None
        assert c.last_synced_at is None


def test_flag_on_iter_containers_falls_back_to_root_on_empty_vault(tmp_path: Path) -> None:
    """ON: empty vault yields one root Container with container_id=''."""
    empty_vault = tmp_path / "empty"
    empty_vault.mkdir()
    connector = _build_connector(empty_vault, flag_on=True)
    containers = list(connector.iter_containers(cc_pair_id=7))
    assert len(containers) == 1
    assert containers[0].container_id == ""
    assert containers[0].access_state == "ACCESSIBLE"


def test_flag_on_load_hierarchy_parent_before_child(tmp_path: Path) -> None:
    """ON: load_hierarchy emits parent-before-child per F58.

    Sabotage proof for #1: flipping the ``_walk_hierarchy`` yield order
    to emit child before parent makes this test fail (orphan emission).
    """
    vault = _seed_vault(tmp_path)
    # Add a nested directory so the parent-before-child invariant has teeth.
    (vault / "01-Projects" / "Client-X").mkdir()
    (vault / "01-Projects" / "Client-X" / "deep" / "deeper").mkdir(parents=True)
    connector = _build_connector(vault, flag_on=True)
    nodes = list(connector.load_hierarchy(cc_pair_id=7))
    # The root + 3 top-level + 3 nested = at least 7 nodes.
    assert len(nodes) >= 7, f"expected multiple FOLDER nodes from the walk, got {len(nodes)}"
    seen: set[str] = set()
    for node in nodes:
        assert isinstance(node, HierarchyNode)
        assert node.node_type == "FOLDER"
        if node.raw_parent_id is not None:
            assert node.raw_parent_id in seen, (
                f"orphan emission: {node.raw_node_id!r} references unseen parent {node.raw_parent_id!r}"
            )
        seen.add(node.raw_node_id)
    # Confirm the expected nested path lands.
    raw_ids = {n.raw_node_id for n in nodes}
    assert "01-Projects/Client-X" in raw_ids
    assert "01-Projects/Client-X/deep" in raw_ids
    assert "01-Projects/Client-X/deep/deeper" in raw_ids
    # And that .obsidian is pruned.
    assert not any(".obsidian" in raw_id for raw_id in raw_ids), "ON: hidden directories must be pruned from the walk"


def test_flag_on_load_hierarchy_emits_obsidian_deep_links(tmp_path: Path) -> None:
    """ON: every emitted FOLDER node carries an obsidian:// deep-link."""
    vault = _seed_vault(tmp_path)
    connector = _build_connector(vault, flag_on=True)
    nodes = list(connector.load_hierarchy(cc_pair_id=7))
    for node in nodes:
        assert node.link is not None
        assert node.link.startswith("obsidian://open?vault=")


def test_flag_on_list_changes_filters_to_container_subtree(tmp_path: Path) -> None:
    """ON: list_changes_for_container scopes events to the container's subtree.

    Sabotage proof for #2: removing the prefix check in
    ``_filter_to_container`` so it returns all events unconditionally
    makes this test fail because events from sibling containers leak.
    """
    vault = _seed_vault(tmp_path)
    connector = _build_connector(vault, flag_on=True)
    container = Container(
        cc_pair_id=7,
        container_id="01-Projects",
        access_state="ACCESSIBLE",
        cursor_token=None,
        last_synced_at=None,
    )
    events = list(connector.list_changes_for_container(container))
    assert events, "ON: scoped reconciler must emit events for files under the container"
    for ev in events:
        assert ev.item_id.startswith("01-Projects/"), (
            f"ON: cross-container leak — got out-of-scope item_id {ev.item_id!r}"
        )


def test_flag_on_per_container_cursors_are_isolated(tmp_path: Path) -> None:
    """ON: each container's cursor_token gates its own events independently.

    Sabotage proof for #3: replacing ``cursor = container.cursor_token``
    in ``_list_changes_scoped`` with a shared module-level cursor makes
    this test fail because container B's events get filtered out by
    container A's cursor.
    """
    vault = _seed_vault(tmp_path)
    connector = _build_connector(vault, flag_on=True)
    # Container A has a future cursor — should yield NO events because the
    # reconciler events carry a "now" timestamp that's <= cursor when the
    # cursor is set to year 3000.
    future_cursor = "3000-01-01T00:00:00Z"
    container_a = Container(
        cc_pair_id=7,
        container_id="00-Home",
        access_state="ACCESSIBLE",
        cursor_token=future_cursor,
        last_synced_at=None,
    )
    # Container B has no cursor — must yield events normally.
    container_b = Container(
        cc_pair_id=7,
        container_id="01-Projects",
        access_state="ACCESSIBLE",
        cursor_token=None,
        last_synced_at=None,
    )
    events_a = list(connector.list_changes_for_container(container_a))
    events_b = list(connector.list_changes_for_container(container_b))
    # A's future cursor filters every event out — proves the cursor read
    # is per-container not shared.
    assert events_a == [], (
        f"ON: per-container cursor isolation broken — A with future cursor still emitted {events_a!r}"
    )
    # B with no cursor surfaces its own subtree events.
    assert events_b, "ON: container B with cursor=None must emit events"
    for ev in events_b:
        assert ev.item_id.startswith("01-Projects/")


def test_flag_on_iter_containers_prunes_hidden_directories(tmp_path: Path) -> None:
    """ON: ``.obsidian`` and other dot-folders never become Containers."""
    vault = _seed_vault(tmp_path)
    # The seeded vault has .obsidian — confirm it isn't a Container.
    connector = _build_connector(vault, flag_on=True)
    ids = {c.container_id for c in connector.iter_containers(cc_pair_id=7)}
    assert ".obsidian" not in ids, "ON: hidden directories must be pruned from iter_containers"

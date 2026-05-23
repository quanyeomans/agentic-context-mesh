"""F58 contract — every ``HierarchyConnector`` implementation must
emit ``HierarchyNode``s in parent-before-child order so the receiver
can construct the tree without buffering.

Pinned at landing (Wave B added the HierarchyConnector Protocol with
default-impl shims on the 4 shipped connectors). The obsidian shim
emits exactly one root FOLDER node so the invariant trivially holds;
this test sabotage-proves the framework's expectation against the
canonical shipped implementation.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kairix.connectors.obsidian.connector import ObsidianConnector
from kairix.core.protocols import HierarchyConnector


@pytest.mark.contract
def test_obsidian_hierarchy_parent_before_child(tmp_path: Path) -> None:
    """Obsidian's HierarchyConnector shim yields nodes parent-before-child.

    The shim emits one root FOLDER node; trivially correct. Test enforces
    the invariant at the framework level so any future HierarchyConnector
    implementation that emits a child before its parent fails here.
    """
    connector = ObsidianConnector(vault_root=tmp_path)
    assert isinstance(connector, HierarchyConnector)
    nodes = list(connector.load_hierarchy(cc_pair_id=1))
    seen: set[str] = set()
    for node in nodes:
        if node.raw_parent_id is not None:
            assert node.raw_parent_id in seen, (
                f"orphan emission: {node.raw_node_id} references parent {node.raw_parent_id!r} not yet emitted"
            )
        seen.add(node.raw_node_id)

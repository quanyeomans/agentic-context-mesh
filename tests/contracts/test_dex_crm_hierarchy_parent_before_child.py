"""F58 contract test for the dex_crm Wave E ``HierarchyConnector`` impl.

Pins the parent-before-child invariant on the real
:class:`kairix.connectors.dex_crm.connector.DexCrmConnector`. The
connector emits one root FOLDER (``dex``) with one FOLDER child per
top-level entity type (Person, Organisation, Relationship); each child
carries ``raw_parent_id="dex"`` so every non-root emission must follow
its parent within the same ``load_hierarchy(cc_pair_id)`` call.

F58 (``scripts/checks/check_f58_hierarchy_parent_before_child.py``)
requires at least one test under ``tests/contracts/`` whose function
name matches ``test_*hierarchy*parent_before_child*`` AND references
``HierarchyConnector``; this file is the dex_crm-specific F58 pin
shipped alongside the obsidian sibling.

Sabotage proof: swapping the yield order in ``_walk_hierarchy`` so a
child emits before its parent makes ``test_dex_crm_hierarchy_parent_before_child``
fail with the orphan-emission assertion. Restored on completion.
"""

from __future__ import annotations

import pytest

from kairix.connectors.dex_crm.connector import DexCrmConnector
from kairix.core.protocols import HierarchyConnector


@pytest.mark.contract
def test_dex_crm_hierarchy_parent_before_child() -> None:
    """Dex CRM's Wave E HierarchyConnector emits nodes parent-before-child.

    Pins the F58 invariant on the hierarchy walk (root + 3 entity-type
    children). Constructing the connector drives the real
    :func:`_walk_hierarchy`; mutating its yield order fails this test
    before any production caller can be affected.
    """
    connector = DexCrmConnector()
    assert isinstance(connector, HierarchyConnector)
    nodes = list(connector.load_hierarchy(cc_pair_id=1))
    assert len(nodes) == 4, f"expected root + 3 entity-type FOLDER nodes from the Wave E walk, got {len(nodes)}"
    seen: set[str] = set()
    for node in nodes:
        if node.raw_parent_id is not None:
            assert node.raw_parent_id in seen, (
                f"orphan emission: {node.raw_node_id} references parent {node.raw_parent_id!r} not yet emitted"
            )
        seen.add(node.raw_node_id)

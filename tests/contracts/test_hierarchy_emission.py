"""F58 contract test — HierarchyConnector parent-before-child invariant.

ADR v2 §4 defines :class:`~kairix.core.protocols.HierarchyConnector` to emit
:class:`~kairix.core.protocols.HierarchyNode` records parent-before-child.
Out-of-order emission produces orphan records — the hierarchy
reconstruction layer either drops them or stores forward-references that
explode at query time.

F58 (``scripts/checks/check_f58_hierarchy_parent_before_child.py``) requires
at least one test under ``tests/contracts/`` whose name matches
``test_*hierarchy*parent_before_child*`` AND references
``HierarchyConnector``. This file IS that test surface — it pins the
invariant against the canonical :class:`FakeHierarchyConnector` from
``tests/fakes.py``.

Wave E will add real ``HierarchyConnector`` implementations per shipped
connector; each will extend this contract via additional test cases that
import the real impl.

Sabotage-prove targets:
- Emit a child before its parent → assertion fails. See
  ``test_hierarchy_parent_before_child_sabotage_check`` for the canonical
  shape.
"""

from __future__ import annotations

import pytest

from kairix.core.protocols import HierarchyConnector, HierarchyNode
from tests.fakes import FakeHierarchyConnector

pytestmark = pytest.mark.contract


def _make_node(*, raw_node_id: str, raw_parent_id: str | None, name: str = "node") -> HierarchyNode:
    return HierarchyNode(
        cc_pair_id=1,
        raw_node_id=raw_node_id,
        raw_parent_id=raw_parent_id,
        display_name=name,
        link=None,
        node_type="FOLDER",
        external_access_json=None,
        sensitivity_hint=None,
    )


def test_hierarchy_parent_before_child_invariant() -> None:
    """Canonical FakeHierarchyConnector emits parent-before-child cleanly."""
    nodes = [
        _make_node(raw_node_id="root", raw_parent_id=None, name="Root"),
        _make_node(raw_node_id="child-a", raw_parent_id="root", name="Child A"),
        _make_node(raw_node_id="child-b", raw_parent_id="root", name="Child B"),
        _make_node(raw_node_id="grandchild", raw_parent_id="child-a", name="Grandchild"),
    ]
    connector: HierarchyConnector = FakeHierarchyConnector(nodes=nodes)
    assert isinstance(connector, HierarchyConnector)
    seen: set[str] = set()
    for node in connector.load_hierarchy(cc_pair_id=1):
        if node.raw_parent_id is not None:
            assert node.raw_parent_id in seen, (
                f"orphan emission: node {node.raw_node_id!r} references unseen parent {node.raw_parent_id!r}"
            )
        seen.add(node.raw_node_id)
    assert seen == {"root", "child-a", "child-b", "grandchild"}


def test_hierarchy_parent_before_child_with_multiple_roots() -> None:
    """Multiple roots (raw_parent_id=None) is valid — forest emission."""
    nodes = [
        _make_node(raw_node_id="root-1", raw_parent_id=None),
        _make_node(raw_node_id="root-2", raw_parent_id=None),
        _make_node(raw_node_id="child-of-1", raw_parent_id="root-1"),
        _make_node(raw_node_id="child-of-2", raw_parent_id="root-2"),
    ]
    connector: HierarchyConnector = FakeHierarchyConnector(nodes=nodes)
    seen: set[str] = set()
    for node in connector.load_hierarchy(cc_pair_id=1):
        if node.raw_parent_id is not None:
            assert node.raw_parent_id in seen
        seen.add(node.raw_node_id)


def test_hierarchy_parent_before_child_sabotage_check() -> None:
    """Out-of-order emission (child before parent) is detectable by the invariant.

    This test demonstrates the invariant CAN fail — feeding the canonical
    FakeHierarchyConnector an out-of-order list trips the parent-membership
    assertion. The sabotage proof for the real test is "mutate the
    connector to flip the order; confirm the invariant assertion fires".
    """
    nodes = [
        _make_node(raw_node_id="child", raw_parent_id="root"),  # child before parent
        _make_node(raw_node_id="root", raw_parent_id=None),
    ]
    connector: HierarchyConnector = FakeHierarchyConnector(nodes=nodes)
    seen: set[str] = set()
    with pytest.raises(AssertionError):
        for node in connector.load_hierarchy(cc_pair_id=1):
            if node.raw_parent_id is not None:
                assert node.raw_parent_id in seen, (
                    f"orphan emission: {node.raw_node_id!r} references unseen {node.raw_parent_id!r}"
                )
            seen.add(node.raw_node_id)


def test_hierarchy_connector_protocol_isinstance_runtime_check() -> None:
    """FakeHierarchyConnector satisfies the HierarchyConnector Protocol at runtime."""
    connector = FakeHierarchyConnector(nodes=[])
    assert isinstance(connector, HierarchyConnector)

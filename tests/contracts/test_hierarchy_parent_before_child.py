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

from kairix.connectors.m365_calendar.connector import (
    M365CalendarConfig,
    M365CalendarConnector,
)
from kairix.connectors.m365_email_headers.connector import (
    M365Credentials,
    M365EmailHeadersConnector,
)
from kairix.connectors.obsidian.connector import ObsidianConnector
from kairix.connectors.sharepoint.connector import (
    SharePointConnector,
    SharePointCredentials,
    SharePointDriveSpec,
)
from kairix.core.protocols import HierarchyConnector
from tests.fakes import FakeFeatureFlagResolver


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


@pytest.mark.contract
def test_m365_email_headers_hierarchy_parent_before_child() -> None:
    """M365 email-headers HierarchyConnector emits root before per-mailbox FOLDER nodes.

    Wave E pilot — when the ``topology_v2_m365_email_headers`` flag is ON,
    the connector emits one synthetic root FOLDER node followed by one
    FOLDER per configured mailbox UPN. The root is emitted FIRST so
    every per-mailbox node's ``raw_parent_id`` references a
    previously-emitted root (parent-before-child per F58).

    Sabotage proof: flipping the loop order in ``load_hierarchy`` so the
    per-mailbox FOLDER yield runs ahead of the root yield makes this
    assertion fail — each mailbox node references the unseen root.
    """
    resolver = FakeFeatureFlagResolver().with_flag("topology_v2_m365_email_headers", True)
    connector = M365EmailHeadersConnector(
        user_principal_name="agent-alpha@example.com",
        credentials=M365Credentials(
            tenant_id="fake-tenant",
            client_id="fake-client",
            client_secret="fake-secret-value",  # pragma: allowlist secret — test fixture
        ),
        mailboxes=["agent-beta@example.com", "agent-gamma@example.com"],
        flag_reader=resolver.get,
    )
    assert isinstance(connector, HierarchyConnector)
    nodes = list(connector.load_hierarchy(cc_pair_id=1))
    assert nodes, "Wave E pilot: load_hierarchy must emit at least the root + per-mailbox FOLDER nodes"
    seen: set[str] = set()
    for node in nodes:
        if node.raw_parent_id is not None:
            assert node.raw_parent_id in seen, (
                f"orphan emission: {node.raw_node_id} references parent {node.raw_parent_id!r} not yet emitted"
            )
        seen.add(node.raw_node_id)


@pytest.mark.contract
def test_sharepoint_hierarchy_parent_before_child() -> None:
    """SharePoint's Wave E HierarchyConnector yields nodes parent-before-child.

    Wave E emits a root SITE FOLDER (``sharepoint``) plus one DRIVE
    FOLDER per configured drive. Test sabotage-proves the F58 invariant
    — swapping the yield order in :meth:`load_hierarchy` so per-drive
    children emit before the root fails the orphan assertion because
    each child references the unseen root.
    """
    resolver = FakeFeatureFlagResolver().with_flag("topology_v2_sharepoint", True)
    connector = SharePointConnector(
        drives=[
            SharePointDriveSpec(drive_id="drive-alpha"),
            SharePointDriveSpec(drive_id="drive-beta"),
        ],
        credentials=SharePointCredentials(
            tenant_id="fake-tenant",
            client_id="fake-client",
            client_secret="fake-secret-value",  # pragma: allowlist secret — test fixture
        ),
        flag_reader=resolver.get,
    )
    assert isinstance(connector, HierarchyConnector)
    nodes = list(connector.load_hierarchy(cc_pair_id=1))
    assert len(nodes) == 3, f"expected root + 2 drive children, got {len(nodes)}"
    seen: set[str] = set()
    for node in nodes:
        if node.raw_parent_id is not None:
            assert node.raw_parent_id in seen, (
                f"orphan emission: {node.raw_node_id} references parent {node.raw_parent_id!r} not yet emitted"
            )
        seen.add(node.raw_node_id)
    # Root must be SITE-typed; children must be DRIVE-typed (per ADR v2 §4 vocabulary).
    assert nodes[0].raw_parent_id is None
    assert nodes[0].node_type == "SITE"
    for child in nodes[1:]:
        assert child.node_type == "DRIVE"
        assert child.raw_parent_id == "sharepoint"


@pytest.mark.contract
def test_m365_calendar_hierarchy_parent_before_child() -> None:
    """M365Calendar's Wave E HierarchyConnector yields nodes parent-before-child.

    Wave E emits a root FOLDER node (``m365-calendar``) plus one child
    FOLDER per configured calendar (per UPN). Test sabotage-proves the
    F58 invariant — swapping the yield order so children emit before
    the root fails the orphan assertion.
    """
    config = M365CalendarConfig(
        user_id="alice@example.com",
        tenant_id="tenant-placeholder",
        client_id="client-placeholder",
        client_secret="secret-placeholder",  # pragma: allowlist secret
        user_ids=("alice@example.com", "bob@example.com"),
    )
    connector = M365CalendarConnector(config, flag_reader=lambda _name: True)
    assert isinstance(connector, HierarchyConnector)
    nodes = list(connector.load_hierarchy(cc_pair_id=1))
    assert len(nodes) == 3, f"expected root + 2 calendar children, got {len(nodes)}"
    seen: set[str] = set()
    for node in nodes:
        if node.raw_parent_id is not None:
            assert node.raw_parent_id in seen, (
                f"orphan emission: {node.raw_node_id} references parent {node.raw_parent_id!r} not yet emitted"
            )
        seen.add(node.raw_node_id)

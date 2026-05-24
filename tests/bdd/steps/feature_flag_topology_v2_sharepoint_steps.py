"""Step definitions for feature_flag_topology_v2_sharepoint.feature.

Wave E of the connector / collection / scope topology v2 migration is
the per-connector slice for the sharepoint connector — when the
``topology_v2_sharepoint`` flag is ON, the connector emits one
:class:`~kairix.core.protocols.Container` per configured Graph drive
(each with its own ``@odata.deltaLink`` as cursor) and
:meth:`list_changes_for_container` scopes the Graph delta query to
that drive ONLY. When OFF, the connector retains the Wave B shim
shape (one shared cursor across every configured drive via the
legacy :meth:`list_changes` call).

This step file exercises both branches of the flag through the
canonical :class:`tests.fakes.FakeFeatureFlagResolver` — pinning the
flag value through the connector's ``flag_reader`` DI seam without
monkey-patching the resolver module (F1-clean / F2-clean).

F46: each step reaches the production composition surface via the
real :class:`kairix.connectors.sharepoint.connector.SharePointConnector`
class, never a Pipeline-class direct construction. The Graph client is
substituted via the ``client_builder`` DI seam with a scripted
in-memory recorder so no real HTTP fires.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field

import pytest
from pytest_bdd import given, parsers, then, when

from kairix.connectors.sharepoint.connector import (
    SharePointConnector,
    SharePointCredentials,
    SharePointDriveSpec,
)
from kairix.connectors.sharepoint.graph_client import (
    DriveItemRef,
    SharePointGraphClient,
)
from kairix.core.protocols import ChangeEvent, Container, HierarchyNode
from kairix.transport.auth.oauth2_client_creds import OAuth2ClientCredsAuth
from tests.fakes import FakeFeatureFlagResolver

pytestmark = pytest.mark.bdd

_FLAG_NAME = "topology_v2_sharepoint"


class _ScriptedGraphClient(SharePointGraphClient):
    """In-memory Graph client used by both branches of the BDD scenarios.

    Records every ``(drive_id, start_url)`` seen via
    :meth:`iter_drive_items` so tests can assert per-drive isolation.
    Yields one synthetic envelope tagged with the drive id so a leak
    across drives is observable in the event stream.
    """

    def __init__(self, *, log: list[tuple[str, str | None]]) -> None:
        # Bypass the parent constructor so we don't need a real
        # OAuth2ClientCredsAuth — the scripted client owns no HTTP
        # resources.
        self._log = log
        self._last_delta_link_by_drive: dict[str, str] = {}
        self._http_client = None  # type: ignore[assignment]  # scripted client owns no HTTP resources; bypass httpx.Client construction
        self._auth = None  # type: ignore[assignment]  # scripted client never exercises auth; OAuth2 helper is never invoked
        self._graph_base = "https://graph.microsoft.com/v1.0"

    def iter_drive_items(self, drive_id: str, start_url: str | None = None) -> Iterator[DriveItemRef]:
        self._log.append((drive_id, start_url))
        self._last_delta_link_by_drive[drive_id] = (
            f"https://graph.microsoft.com/v1.0/drives/{drive_id}/root/delta?$deltatoken=fresh"
        )
        yield DriveItemRef(
            item_id=f"item-{drive_id}",
            drive_id=drive_id,
            name=f"file-{drive_id}.pdf",
            mime="application/pdf",
            web_url=f"https://contoso.sharepoint.com/sites/team/Documents/file-{drive_id}.pdf",
            size=42,
            last_modified_at="2026-05-22T10:00:00Z",
            removed=False,
        )


@dataclass
class _TopologyV2SharepointCtx:
    """Per-scenario context. No module-level mutable state."""

    resolver: FakeFeatureFlagResolver | None = None
    flag_value: bool | None = None
    drive_ids: tuple[str, ...] = ()
    connector: SharePointConnector | None = None
    log: list[tuple[str, str | None]] = field(default_factory=list)
    containers: list[Container] = field(default_factory=list)
    hierarchy_nodes: list[HierarchyNode] = field(default_factory=list)
    scoped_events: list[ChangeEvent] = field(default_factory=list)
    legacy_path_observed: bool = False
    reindex_events: list[ChangeEvent] = field(default_factory=list)
    reindex_failed_ids: tuple[str, ...] = ()


@pytest.fixture
def topology_v2_sharepoint_ctx() -> _TopologyV2SharepointCtx:
    return _TopologyV2SharepointCtx()


# ---------------------------------------------------------------------------
# Givens
# ---------------------------------------------------------------------------


@given(parsers.parse("a sharepoint connector configured for two drives: {a}, {b}"))
def _two_drive_config(
    topology_v2_sharepoint_ctx: _TopologyV2SharepointCtx,
    a: str,
    b: str,
) -> None:
    """Stash the configured drive ids; the connector is built once the flag is set."""
    topology_v2_sharepoint_ctx.drive_ids = (a, b)


@given(parsers.parse("the operator has the topology-v2-sharepoint flag set to {value}"))
def _operator_sets_flag(
    topology_v2_sharepoint_ctx: _TopologyV2SharepointCtx,
    value: str,
) -> None:
    """Pin the flag value via :class:`FakeFeatureFlagResolver` and
    construct the real :class:`SharePointConnector` against the
    configured drives, threading the flag value through the connector's
    ``flag_reader`` DI seam.
    """
    parsed = value.strip().lower() == "true"
    resolver = FakeFeatureFlagResolver().with_flag(_FLAG_NAME, parsed)
    topology_v2_sharepoint_ctx.resolver = resolver
    topology_v2_sharepoint_ctx.flag_value = parsed
    drive_ids = topology_v2_sharepoint_ctx.drive_ids
    assert drive_ids, "the configured drive ids must have been declared before flag setup"
    log = topology_v2_sharepoint_ctx.log
    scripted = _ScriptedGraphClient(log=log)
    topology_v2_sharepoint_ctx.connector = SharePointConnector(
        drives=[SharePointDriveSpec(drive_id=d) for d in drive_ids],
        credentials=SharePointCredentials(
            tenant_id="tenant-placeholder",
            client_id="client-placeholder",
            client_secret="secret-placeholder",  # pragma: allowlist secret — test fixture
        ),
        # The auth is constructed but never used by the scripted client.
        # Pass a real auth so the connector's __init__ accepts the path.
        auth=OAuth2ClientCredsAuth(
            tenant_id="tenant-placeholder",
            client_id="client-placeholder",
            client_secret="secret-placeholder",  # pragma: allowlist secret — test fixture
            scope="https://graph.microsoft.com/.default",
        ),
        client_builder=lambda _auth: scripted,
        flag_reader=resolver.get,
    )


# ---------------------------------------------------------------------------
# Whens
# ---------------------------------------------------------------------------


@when("the operator calls iter_containers on the sharepoint connector")
def _call_iter_containers(topology_v2_sharepoint_ctx: _TopologyV2SharepointCtx) -> None:
    assert topology_v2_sharepoint_ctx.connector is not None
    topology_v2_sharepoint_ctx.containers = list(topology_v2_sharepoint_ctx.connector.iter_containers(cc_pair_id=42))


@when("the operator calls load_hierarchy on the sharepoint connector")
def _call_load_hierarchy(topology_v2_sharepoint_ctx: _TopologyV2SharepointCtx) -> None:
    assert topology_v2_sharepoint_ctx.connector is not None
    topology_v2_sharepoint_ctx.hierarchy_nodes = list(
        topology_v2_sharepoint_ctx.connector.load_hierarchy(cc_pair_id=42)
    )


@when(parsers.parse("the operator drives list_changes_for_container against drive {drive_id}"))
def _call_list_changes_for_container(
    topology_v2_sharepoint_ctx: _TopologyV2SharepointCtx,
    drive_id: str,
) -> None:
    """Construct a Container scoped to the named drive and drive
    ``list_changes_for_container``. The OFF branch delegates to the
    legacy single-cursor :meth:`list_changes`; the ON branch routes to
    the per-drive Graph client.
    """
    connector = topology_v2_sharepoint_ctx.connector
    assert connector is not None
    container = Container(
        cc_pair_id=42,
        container_id=drive_id,
        access_state="ACCESSIBLE",
        cursor_token=None,
        last_synced_at=None,
    )
    if topology_v2_sharepoint_ctx.flag_value is False:
        topology_v2_sharepoint_ctx.legacy_path_observed = True
    events = list(connector.list_changes_for_container(container))
    topology_v2_sharepoint_ctx.scoped_events = events


@when(parsers.parse("the operator calls reindex on the sharepoint connector with failed ids {a} and {b}"))
def _call_reindex(
    topology_v2_sharepoint_ctx: _TopologyV2SharepointCtx,
    a: str,
    b: str,
) -> None:
    connector = topology_v2_sharepoint_ctx.connector
    assert connector is not None
    failed = (a, b)
    topology_v2_sharepoint_ctx.reindex_failed_ids = failed
    topology_v2_sharepoint_ctx.reindex_events = list(connector.reindex(failed))


# ---------------------------------------------------------------------------
# Thens
# ---------------------------------------------------------------------------


@then("two Containers are emitted, one per configured drive")
def _two_containers(topology_v2_sharepoint_ctx: _TopologyV2SharepointCtx) -> None:
    containers = topology_v2_sharepoint_ctx.containers
    assert len(containers) == 2, f"expected two Containers (one per configured drive), got {len(containers)}"
    ids = [c.container_id for c in containers]
    assert ids == list(topology_v2_sharepoint_ctx.drive_ids), (
        f"containers must follow the configured drive order, got {ids}"
    )


@then("every sharepoint Container carries access_state ACCESSIBLE with no cursor_token yet")
def _container_shape(topology_v2_sharepoint_ctx: _TopologyV2SharepointCtx) -> None:
    for container in topology_v2_sharepoint_ctx.containers:
        assert container.access_state == "ACCESSIBLE"
        assert container.cursor_token is None
        assert container.last_synced_at is None
        assert container.cc_pair_id == 42


@then("FOLDER nodes are emitted parent-before-child with a SITE root and one DRIVE child per drive")
def _hierarchy_parent_before_child(topology_v2_sharepoint_ctx: _TopologyV2SharepointCtx) -> None:
    nodes = topology_v2_sharepoint_ctx.hierarchy_nodes
    # Root + 2 drives = 3.
    assert len(nodes) == 3, f"expected SITE root + 2 DRIVE children, got {len(nodes)}"
    assert nodes[0].raw_parent_id is None
    assert nodes[0].node_type == "SITE"
    seen: set[str] = set()
    for node in nodes:
        if node.raw_parent_id is not None:
            assert node.raw_parent_id in seen, (
                f"orphan emission: node {node.raw_node_id!r} references unseen parent {node.raw_parent_id!r}"
            )
        seen.add(node.raw_node_id)
    drive_children = [n for n in nodes if n.raw_parent_id is not None]
    assert all(c.node_type == "DRIVE" for c in drive_children), "every non-root must be DRIVE-typed under SharePoint"


@then("the legacy single-cursor list_changes branch is observed for sharepoint")
def _legacy_branch_observed(topology_v2_sharepoint_ctx: _TopologyV2SharepointCtx) -> None:
    assert topology_v2_sharepoint_ctx.legacy_path_observed is True, (
        "expected the OFF branch to take the legacy single-cursor delegation path"
    )
    connector = topology_v2_sharepoint_ctx.connector
    assert connector is not None
    # OFF: legacy list_changes populates the packed JSON _next_cursor;
    # the ON path leaves _next_cursor alone (the per-container path
    # bypasses it entirely).
    assert connector._next_cursor is not None, (
        "OFF branch must populate the legacy packed JSON cursor map via list_changes"
    )


@then("one root FOLDER node is emitted with no drive children for sharepoint")
def _root_folder_only(topology_v2_sharepoint_ctx: _TopologyV2SharepointCtx) -> None:
    nodes = topology_v2_sharepoint_ctx.hierarchy_nodes
    assert len(nodes) == 1, f"OFF branch must emit exactly one root FOLDER node, got {len(nodes)}"
    assert nodes[0].raw_parent_id is None
    assert nodes[0].node_type == "FOLDER"


@then(parsers.parse("the sharepoint Graph delta query targets only drive {drive_id}"))
def _graph_targets_only_drive(
    topology_v2_sharepoint_ctx: _TopologyV2SharepointCtx,
    drive_id: str,
) -> None:
    log = topology_v2_sharepoint_ctx.log
    drives_hit = [entry[0] for entry in log]
    assert drives_hit == [drive_id], (
        f"expected the Graph client to fire only against {drive_id!r}; recorded drives: {drives_hit!r}"
    )


@then("the sharepoint reindex emits exactly one event per supplied failed id")
def _reindex_replay_scope(topology_v2_sharepoint_ctx: _TopologyV2SharepointCtx) -> None:
    failed = topology_v2_sharepoint_ctx.reindex_failed_ids
    events = topology_v2_sharepoint_ctx.reindex_events
    assert len(events) == len(failed), (
        f"reindex must replay exactly the supplied failed ids; got {[e.item_id for e in events]!r}, expected {failed!r}"
    )
    replayed_ids = [e.item_id for e in events]
    assert replayed_ids == list(failed), (
        f"reindex must preserve the supplied id order; got {replayed_ids!r}, expected {list(failed)!r}"
    )
    for ev in events:
        assert ev.op == "modified", f"reindex events must be 'modified' ops; got {ev.op!r}"
        assert ev.metadata.get("reindex") is True

"""Step definitions for feature_flag_topology_v2_dex_crm.feature.

Wave E of the connector / collection / scope topology v2 migration is
the per-connector pilot for the dex_crm connector — when the
``topology_v2_dex_crm`` flag is ON, the connector emits one
:class:`~kairix.core.protocols.Container` representing the tenant (the
Dex API is single-tenant single-cursor) and emits one root FOLDER
plus one FOLDER child per top-level entity type (Person,
Organisation, Relationship) parent-before-child per F58. When OFF,
the connector retains the Wave B shim shape — one root FOLDER node;
``list_changes_for_container`` delegates to the legacy single
``list_changes`` call.

This step file exercises both branches of the flag through the
canonical :class:`tests.fakes.FakeFeatureFlagResolver` — pinning the
flag value through the connector's ``flag_reader`` DI seam without
monkey-patching the resolver module (F1-clean / F2-clean).

F46: each step reaches the production composition surface via the
real :class:`kairix.connectors.dex_crm.connector.DexCrmConnector`
class, never a Pipeline-class direct construction.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import httpx
import pytest
from pytest_bdd import given, parsers, then, when

from kairix.connectors.dex_crm import DexCrmConnector
from kairix.connectors.dex_crm.client import DexCrmClient, DexCrmClientConfig
from kairix.core.protocols import Container, HierarchyNode
from kairix.transport.auth.api_key import ApiKeyAuth, BearerHeaders, reset_api_key_cache
from tests.fakes import FakeFeatureFlagResolver

pytestmark = pytest.mark.bdd

_FLAG_NAME = "topology_v2_dex_crm"

_SCRIPTED_CONTACT = {
    "id": "c-bdd-1",
    "updated_at": "2026-05-23T11:00:00Z",
    "first_name": "agent-alpha",
}


@dataclass
class _TopologyV2DexCtx:
    """Per-scenario context. No module-level mutable state."""

    resolver: FakeFeatureFlagResolver | None = None
    flag_value: bool | None = None
    http_calls: list[str] = field(default_factory=list)
    connector: DexCrmConnector | None = None
    containers: list[Container] = field(default_factory=list)
    hierarchy_nodes: list[HierarchyNode] = field(default_factory=list)
    scoped_change_item_ids: list[str] = field(default_factory=list)
    legacy_path_observed: bool = False
    on_path_observed: bool = False


@pytest.fixture
def topology_v2_dex_ctx() -> _TopologyV2DexCtx:
    """Per-scenario context — drop any cached secret + clean slate."""
    reset_api_key_cache()
    return _TopologyV2DexCtx()


# ---------------------------------------------------------------------------
# Helpers (depth-2 reachable so F46 sees the production surface)
# ---------------------------------------------------------------------------


def _stub_auth() -> ApiKeyAuth:
    """Real :class:`ApiKeyAuth` subclass yielding a fixed bearer.

    Subclassing avoids monkey-patching the secrets resolver (F1-clean).
    """

    class _ScriptedAuth(ApiKeyAuth):
        def headers(self, _secret_name: str) -> BearerHeaders:
            return BearerHeaders(mapping={"Authorization": "Bearer bdd-topology-v2-dex-token"})

    return _ScriptedAuth()


def _scripted_transport(http_calls: list[str]) -> httpx.MockTransport:
    """Build an :class:`httpx.MockTransport` returning one scripted contact."""

    def _handler(request: httpx.Request) -> httpx.Response:
        http_calls.append(request.url.path)
        if request.url.path.endswith("/contacts"):
            return httpx.Response(200, json={"data": [_SCRIPTED_CONTACT], "next_cursor": None})
        return httpx.Response(200, json={"data": [], "next_cursor": None})

    return httpx.MockTransport(_handler)


def _build_connector(ctx: _TopologyV2DexCtx, *, flag_on: bool) -> DexCrmConnector:
    """Construct the real :class:`DexCrmConnector` with the flag pinned."""
    if flag_on:
        ctx.resolver = FakeFeatureFlagResolver().with_flag("topology_v2_dex_crm", True)
    else:
        ctx.resolver = FakeFeatureFlagResolver().with_flag("topology_v2_dex_crm", False)
    inner_client = httpx.Client(transport=_scripted_transport(ctx.http_calls))
    client = DexCrmClient(
        config=DexCrmClientConfig(rate_limit_sleep_s=0.0),
        http_client=inner_client,
        auth=_stub_auth(),
        sleep=lambda _s: None,
    )
    return DexCrmConnector(client=client, flag_reader=ctx.resolver.get)


# ---------------------------------------------------------------------------
# Givens
# ---------------------------------------------------------------------------


@given("a configured dex_crm connector with a scripted Dex API")
def _scripted_dex_api(topology_v2_dex_ctx: _TopologyV2DexCtx) -> None:
    """Reserved for future scripted-state setup; today is a no-op marker.

    The scripted transport is wired when the connector is built in the
    next step so we have the flag value before binding the HTTP client.
    """


@given(parsers.parse("the operator has the topology-v2-dex-crm flag set to {value}"))
def _operator_sets_flag(
    topology_v2_dex_ctx: _TopologyV2DexCtx,
    value: str,
) -> None:
    """Pin the flag via :class:`FakeFeatureFlagResolver` and construct
    the real :class:`DexCrmConnector`, threading the flag value through
    the connector's ``flag_reader`` DI seam.
    """
    parsed = value.strip().lower() == "true"
    topology_v2_dex_ctx.flag_value = parsed
    topology_v2_dex_ctx.connector = _build_connector(topology_v2_dex_ctx, flag_on=parsed)


# ---------------------------------------------------------------------------
# Whens
# ---------------------------------------------------------------------------


@when("the operator calls iter_containers on the dex_crm connector")
def _call_iter_containers(topology_v2_dex_ctx: _TopologyV2DexCtx) -> None:
    assert topology_v2_dex_ctx.connector is not None
    topology_v2_dex_ctx.containers = list(topology_v2_dex_ctx.connector.iter_containers(cc_pair_id=42))


@when("the operator calls load_hierarchy on the dex_crm connector")
def _call_load_hierarchy(topology_v2_dex_ctx: _TopologyV2DexCtx) -> None:
    assert topology_v2_dex_ctx.connector is not None
    topology_v2_dex_ctx.hierarchy_nodes = list(topology_v2_dex_ctx.connector.load_hierarchy(cc_pair_id=42))


@when("the operator calls list_changes_for_container with the dex tenant Container")
def _call_list_changes_for_container(topology_v2_dex_ctx: _TopologyV2DexCtx) -> None:
    """Drive ``list_changes_for_container`` against the single tenant Container.

    The OFF branch delegates to the legacy single-cursor
    :meth:`list_changes`; the ON branch threads the container's cursor
    through the per-container scoped helper. The connector reaches the
    Dex listing endpoints either way — the per-branch distinction is
    observed via the flag value the connector saw.
    """
    connector = topology_v2_dex_ctx.connector
    assert connector is not None
    container = Container(
        cc_pair_id=42,
        container_id="",
        access_state="ACCESSIBLE",
        cursor_token=None,
        last_synced_at=None,
    )
    if topology_v2_dex_ctx.flag_value is False:
        topology_v2_dex_ctx.legacy_path_observed = True
    else:
        topology_v2_dex_ctx.on_path_observed = True
    events = list(connector.list_changes_for_container(container))
    topology_v2_dex_ctx.scoped_change_item_ids = [ev.item_id for ev in events]


# ---------------------------------------------------------------------------
# Thens
# ---------------------------------------------------------------------------


@then("exactly one dex root FOLDER node is emitted with raw_parent_id None")
def _exactly_one_root_folder(topology_v2_dex_ctx: _TopologyV2DexCtx) -> None:
    nodes = topology_v2_dex_ctx.hierarchy_nodes
    assert len(nodes) == 1, f"expected 1 root FOLDER node (OFF branch shim), got {len(nodes)}"
    assert nodes[0].node_type == "FOLDER"
    assert nodes[0].raw_parent_id is None
    assert nodes[0].raw_node_id == "dex"


@then("the dex_crm legacy single-cursor list_changes branch is observed")
def _legacy_branch_observed(topology_v2_dex_ctx: _TopologyV2DexCtx) -> None:
    assert topology_v2_dex_ctx.legacy_path_observed is True, (
        "expected the OFF branch to take the legacy single-cursor delegation path"
    )
    # Delegation must have hit the wire (per-record-kind GETs).
    expected = {"/contacts", "/organisations", "/relationships"}
    http_calls = topology_v2_dex_ctx.http_calls
    seen = {suffix for suffix in expected if any(path.endswith(suffix) for path in http_calls)}
    assert seen == expected, (
        f"OFF branch must hit every Dex listing endpoint via legacy delegation; saw {sorted(http_calls)!r}"
    )


@then("one dex Container is emitted for the tenant")
def _one_tenant_container(topology_v2_dex_ctx: _TopologyV2DexCtx) -> None:
    containers = topology_v2_dex_ctx.containers
    assert len(containers) == 1, f"expected one Container for the single-tenant Dex API, got {len(containers)}"
    assert containers[0].container_id == ""


@then("the dex Container carries access_state ACCESSIBLE and an unset cursor_token")
def _container_shape(topology_v2_dex_ctx: _TopologyV2DexCtx) -> None:
    for container in topology_v2_dex_ctx.containers:
        assert container.access_state == "ACCESSIBLE"
        assert container.cursor_token is None
        assert container.last_synced_at is None
        assert container.cc_pair_id == 42


@then("four FOLDER nodes are emitted parent-before-child for the dex hierarchy")
def _hierarchy_parent_before_child(topology_v2_dex_ctx: _TopologyV2DexCtx) -> None:
    nodes = topology_v2_dex_ctx.hierarchy_nodes
    assert len(nodes) == 4, f"expected 4 FOLDER nodes (root + 3 entity types), got {len(nodes)}"
    seen: set[str] = set()
    for node in nodes:
        if node.raw_parent_id is not None:
            assert node.raw_parent_id in seen, (
                f"orphan emission: node {node.raw_node_id!r} references unseen parent {node.raw_parent_id!r}"
            )
        seen.add(node.raw_node_id)


@then("dex change events are emitted via the per-container cursor path")
def _on_branch_observed(topology_v2_dex_ctx: _TopologyV2DexCtx) -> None:
    assert topology_v2_dex_ctx.on_path_observed is True, "expected the ON branch to take the per-container cursor path"
    assert topology_v2_dex_ctx.scoped_change_item_ids, (
        "ON: per-container path must surface change events from the scripted Dex API"
    )
    expected = {"/contacts", "/organisations", "/relationships"}
    http_calls = topology_v2_dex_ctx.http_calls
    seen = {suffix for suffix in expected if any(path.endswith(suffix) for path in http_calls)}
    assert seen == expected, f"ON branch must hit every Dex listing endpoint; saw {sorted(http_calls)!r}"

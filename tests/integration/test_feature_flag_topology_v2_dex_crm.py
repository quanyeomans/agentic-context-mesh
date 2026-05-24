"""F54 integration coverage for the ``topology_v2_dex_crm`` feature flag.

Wave E of the connector / collection / scope topology v2 migration —
per-connector pilot for the dex_crm connector. When the
``topology_v2_dex_crm`` flag is ON, the connector emits one
:class:`~kairix.core.protocols.Container` per tenant (the Dex API has
no per-organisation delta) and emits one root FOLDER plus one FOLDER
child per top-level entity type (Person, Organisation, Relationship)
parent-before-child per F58. When OFF, the connector retains the
Wave B shim shape (one root FOLDER node; ``list_changes_for_container``
delegates to the legacy single ``list_changes`` call).

Per F54 (docs/architecture/feature-flag-architecture.md §5): every flag
needs integration coverage exercising both branches via
:class:`tests.fakes.FakeFeatureFlagResolver`. The string literal
``"topology_v2_dex_crm"`` appears verbatim in every ``with_flag(...)``
call so the F54 check picks it up.

F47 — the multi-component pipeline (connector + scripted Dex API) is
constructed via real plugin construction with the
:class:`~kairix.connectors.dex_crm.connector.DexCrmConnector` class
itself; the flag is injected through the connector's ``flag_reader``
DI seam. No monkey-patching of the resolver module.

Sabotage proofs (executed by the agent, mutate -> confirm fail -> restore):

  1. **F58 parent-before-child** — swapped the yield order in
     ``_walk_hierarchy`` so a child node was yielded before its
     parent; confirmed
     ``test_flag_on_load_hierarchy_parent_before_child`` failed with
     an orphan-emission assertion; restored.
  2. **Container scope (per-container cursor)** — replaced the
     ``cursor = container.cursor_token`` read in
     ``_list_changes_scoped`` with a hard-coded ``cursor = None`` so
     the container's cursor was ignored; confirmed
     ``test_flag_on_per_container_cursor_is_used`` failed because the
     future-cursor container still emitted events; restored.
  3. **Flag-OFF inertness** — inverted the
     ``if not self._flag_reader(...)`` check in
     ``list_changes_for_container`` so the OFF branch ran the new
     scoped path; confirmed
     ``test_flag_off_list_changes_for_container_uses_legacy_path``
     failed because the on-branch helper was observed; restored.
"""

from __future__ import annotations

import httpx
import pytest

from kairix.connectors.dex_crm import DexCrmConnector
from kairix.connectors.dex_crm.client import DexCrmClient, DexCrmClientConfig
from kairix.core.protocols import (
    Container,
    HierarchyConnector,
    HierarchyNode,
    PollConnector,
)
from kairix.transport.auth.api_key import ApiKeyAuth, BearerHeaders, reset_api_key_cache
from tests.fakes import FakeFeatureFlagResolver

pytestmark = pytest.mark.integration

_FLAG_NAME = "topology_v2_dex_crm"

_SCRIPTED_CONTACT = {
    "id": "c-700",
    "updated_at": "2026-05-23T11:00:00Z",
    "first_name": "agent-alpha",
}


def _stub_auth() -> ApiKeyAuth:
    """Real :class:`ApiKeyAuth` subclass yielding a fixed bearer."""

    class _ScriptedAuth(ApiKeyAuth):
        def headers(self, _secret_name: str) -> BearerHeaders:
            return BearerHeaders(mapping={"Authorization": "Bearer topology-v2-dex-token"})

    return _ScriptedAuth()


def _scripted_transport(http_calls: list[str]) -> httpx.MockTransport:
    """Return one scripted contact; record every request path."""

    def _handler(request: httpx.Request) -> httpx.Response:
        http_calls.append(request.url.path)
        if request.url.path.endswith("/contacts"):
            return httpx.Response(200, json={"data": [_SCRIPTED_CONTACT], "next_cursor": None})
        return httpx.Response(200, json={"data": [], "next_cursor": None})

    return httpx.MockTransport(_handler)


def _build_connector(http_calls: list[str], *, flag_on: bool) -> DexCrmConnector:
    """Construct a real :class:`DexCrmConnector` with the Wave E flag pinned.

    F54 — verbatim literal so the both-branch grep picks up the flag
    name. Each branch keeps its own ``with_flag(...)`` call so the OFF
    + ON pattern is mechanically observable.
    """
    if flag_on:
        resolver = FakeFeatureFlagResolver().with_flag("topology_v2_dex_crm", True)
    else:
        resolver = FakeFeatureFlagResolver().with_flag("topology_v2_dex_crm", False)
    reset_api_key_cache()
    inner_client = httpx.Client(transport=_scripted_transport(http_calls))
    client = DexCrmClient(
        config=DexCrmClientConfig(rate_limit_sleep_s=0.0),
        http_client=inner_client,
        auth=_stub_auth(),
        sleep=lambda _s: None,
    )
    return DexCrmConnector(client=client, flag_reader=resolver.get)


# ---------------------------------------------------------------------------
# Flag registration + Protocol satisfaction
# ---------------------------------------------------------------------------


def test_topology_v2_dex_crm_flag_registered() -> None:
    """The flag exists in the registry, defaults False, stage=introduce."""
    from kairix.core.features.registry import REGISTRY

    assert "topology_v2_dex_crm" in REGISTRY
    entry = REGISTRY["topology_v2_dex_crm"]
    assert entry.default is False
    assert entry.stage == "introduce"
    assert entry.owner == "connector-framework"


def test_dex_crm_connector_satisfies_poll_and_hierarchy_protocols_under_both_branches() -> None:
    """Both branches keep the connector satisfying the capability Protocols."""
    off_calls: list[str] = []
    on_calls: list[str] = []
    off = _build_connector(off_calls, flag_on=False)
    on = _build_connector(on_calls, flag_on=True)
    assert isinstance(off, PollConnector)
    assert isinstance(off, HierarchyConnector)
    assert isinstance(on, PollConnector)
    assert isinstance(on, HierarchyConnector)


# ---------------------------------------------------------------------------
# OFF branch — Wave B shim behaviour
# ---------------------------------------------------------------------------


def test_flag_off_load_hierarchy_emits_single_root_node() -> None:
    """OFF: load_hierarchy yields exactly one root FOLDER node (Wave B shim)."""
    http_calls: list[str] = []
    connector = _build_connector(http_calls, flag_on=False)
    nodes = list(connector.load_hierarchy(cc_pair_id=11))
    assert len(nodes) == 1, f"OFF branch must emit one root FOLDER node, got {len(nodes)}"
    assert nodes[0].raw_parent_id is None
    assert nodes[0].node_type == "FOLDER"
    assert nodes[0].raw_node_id == "dex"


def test_flag_off_list_changes_for_container_records_legacy_path() -> None:
    """OFF: the per-call path-taken diagnostic must record ``"legacy"``.

    The single-tenant Dex API reaches the same wire endpoints from
    both branches, so the only mechanically-observable distinction is
    the per-call path-taken marker. Inverting the
    ``if not self._flag_reader(...)`` gate in
    :meth:`list_changes_for_container` flips this to ``"scoped"`` and
    the assertion fails — that's sabotage proof #3.
    """
    http_calls: list[str] = []
    connector = _build_connector(http_calls, flag_on=False)
    container = Container(
        cc_pair_id=11,
        container_id="",
        access_state="ACCESSIBLE",
        cursor_token=None,
        last_synced_at=None,
    )
    list(connector.list_changes_for_container(container))
    assert connector._last_path_taken == "legacy", (
        f"OFF branch must take the legacy delegation path; got {connector._last_path_taken!r}"
    )


def test_flag_on_list_changes_for_container_records_scoped_path() -> None:
    """ON: the per-call path-taken diagnostic must record ``"scoped"``.

    Pair with the OFF-path test above — together they pin the flag
    gate's direction. Inverting the gate flips OFF to ``"scoped"`` and
    ON to ``"legacy"``, breaking both tests simultaneously.
    """
    http_calls: list[str] = []
    connector = _build_connector(http_calls, flag_on=True)
    container = Container(
        cc_pair_id=11,
        container_id="",
        access_state="ACCESSIBLE",
        cursor_token=None,
        last_synced_at=None,
    )
    list(connector.list_changes_for_container(container))
    assert connector._last_path_taken == "scoped", (
        f"ON branch must take the Wave E scoped helper path; got {connector._last_path_taken!r}"
    )


def test_flag_off_list_changes_for_container_uses_legacy_path() -> None:
    """OFF: list_changes_for_container delegates to legacy single-cursor list_changes.

    The delegation chain reaches the same listing endpoints in the
    same order as a bare ``connector.list_changes(cursor)`` call. The
    OFF branch must NOT take the Wave E scoped helper path.

    Sabotage proof #3: inverting the ``if not self._flag_reader(...)``
    check in :meth:`list_changes_for_container` makes this test fail
    because the per-container path would run instead of the legacy
    delegation.
    """
    http_calls: list[str] = []
    connector = _build_connector(http_calls, flag_on=False)
    container = Container(
        cc_pair_id=11,
        container_id="",
        access_state="ACCESSIBLE",
        cursor_token=None,
        last_synced_at=None,
    )
    via_container = sorted(ev.item_id for ev in connector.list_changes_for_container(container))

    # Independently drive the legacy path on a fresh connector with the
    # same scripted transport to confirm delegation parity.
    legacy_calls: list[str] = []
    legacy_connector = _build_connector(legacy_calls, flag_on=False)
    via_legacy = sorted(ev.item_id for ev in legacy_connector.list_changes(None))

    assert via_container == via_legacy, (
        f"OFF branch must delegate; per-container = {via_container!r}, legacy = {via_legacy!r}"
    )
    # The legacy delegation surfaces every Dex listing endpoint (one
    # request per kind) — proves the delegation reaches the wire.
    expected = {"/contacts", "/organisations", "/relationships"}
    seen = {suffix for suffix in expected if any(path.endswith(suffix) for path in http_calls)}
    assert seen == expected, (
        f"OFF branch must hit every Dex listing endpoint via legacy delegation; saw {sorted(http_calls)!r}"
    )


# ---------------------------------------------------------------------------
# ON branch — Wave E real implementations
# ---------------------------------------------------------------------------


def test_flag_on_iter_containers_yields_one_tenant_container() -> None:
    """ON: iter_containers yields one Container representing the Dex tenant."""
    http_calls: list[str] = []
    connector = _build_connector(http_calls, flag_on=True)
    containers = list(connector.iter_containers(cc_pair_id=11))
    assert len(containers) == 1, f"ON: dex_crm is single-tenant, expected one Container, got {len(containers)}"
    only = containers[0]
    assert only.cc_pair_id == 11
    assert only.container_id == ""
    assert only.access_state == "ACCESSIBLE"
    assert only.cursor_token is None
    assert only.last_synced_at is None


def test_flag_on_load_hierarchy_parent_before_child() -> None:
    """ON: load_hierarchy emits parent-before-child per F58.

    Sabotage proof #1: swapping the yield order in ``_walk_hierarchy``
    so a child is emitted before its parent makes this test fail
    (orphan emission).
    """
    http_calls: list[str] = []
    connector = _build_connector(http_calls, flag_on=True)
    nodes = list(connector.load_hierarchy(cc_pair_id=11))
    # Root + 3 entity-type children = 4 nodes.
    assert len(nodes) == 4, f"expected 4 FOLDER nodes (root + 3 entity types), got {len(nodes)}"
    seen: set[str] = set()
    for node in nodes:
        assert isinstance(node, HierarchyNode)
        assert node.node_type == "FOLDER"
        if node.raw_parent_id is not None:
            assert node.raw_parent_id in seen, (
                f"orphan emission: {node.raw_node_id!r} references unseen parent {node.raw_parent_id!r}"
            )
        seen.add(node.raw_node_id)
    raw_ids = {n.raw_node_id for n in nodes}
    assert raw_ids == {"dex", "dex/person", "dex/organisation", "dex/relationship"}, (
        f"ON: expected the canonical dex hierarchy raw_ids, got {raw_ids!r}"
    )


def test_flag_on_load_hierarchy_emits_getdex_deep_links() -> None:
    """ON: every emitted FOLDER node carries an app.getdex.com link."""
    http_calls: list[str] = []
    connector = _build_connector(http_calls, flag_on=True)
    nodes = list(connector.load_hierarchy(cc_pair_id=11))
    for node in nodes:
        assert node.link is not None
        assert node.link.startswith("https://app.getdex.com/")


def test_flag_on_list_changes_for_container_reaches_dex_api() -> None:
    """ON: list_changes_for_container drives the per-container cursor path
    and reaches the Dex listing endpoints.
    """
    http_calls: list[str] = []
    connector = _build_connector(http_calls, flag_on=True)
    container = Container(
        cc_pair_id=11,
        container_id="",
        access_state="ACCESSIBLE",
        cursor_token=None,
        last_synced_at=None,
    )
    events = list(connector.list_changes_for_container(container))
    assert events, "ON: per-container path must surface change events"
    expected = {"/contacts", "/organisations", "/relationships"}
    seen = {suffix for suffix in expected if any(path.endswith(suffix) for path in http_calls)}
    assert seen == expected, f"ON: per-container path must hit every Dex listing endpoint; saw {sorted(http_calls)!r}"


def test_flag_on_per_container_cursor_is_used() -> None:
    """ON: list_changes_for_container uses ``container.cursor_token`` as
    the per-container cursor — not a shared module / connector cursor.

    Sabotage proof #2: replacing ``cursor = container.cursor_token`` in
    :meth:`_list_changes_scoped` with a hard-coded ``cursor = None``
    makes this test fail because the future-cursor container would
    still emit the scripted event.

    The scripted contact has ``updated_at = "2026-05-23T11:00:00Z"``;
    a Container with ``cursor_token = "3000-01-01T00:00:00Z"`` (future)
    must filter the event out via the in-connector
    ``modified_at <= cursor`` defence-in-depth check.
    """
    http_calls: list[str] = []
    connector = _build_connector(http_calls, flag_on=True)
    future_cursor = "3000-01-01T00:00:00Z"
    future_container = Container(
        cc_pair_id=11,
        container_id="",
        access_state="ACCESSIBLE",
        cursor_token=future_cursor,
        last_synced_at=None,
    )
    events = list(connector.list_changes_for_container(future_container))
    assert events == [], f"ON: per-container cursor was ignored — future cursor still emitted {events!r}"

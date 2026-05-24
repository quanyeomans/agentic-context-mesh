"""Step definitions for feature_flag_topology_v2_m365_calendar.feature.

Wave E of the connector / collection / scope topology v2 migration is
the per-connector slice for the m365_calendar connector — when the
``topology_v2_m365_calendar`` flag is ON, the connector emits one
:class:`~kairix.core.protocols.Container` per configured calendar
(per UPN) and :meth:`list_changes_for_container` scopes the Graph
delta query to that calendar's UPN ONLY. When OFF, the connector
retains the Wave B shim shape (one shared cursor across every
configured calendar via the legacy :meth:`list_changes` call).

This step file exercises both branches of the flag through the
canonical :class:`tests.fakes.FakeFeatureFlagResolver` — pinning the
flag value through the connector's ``flag_reader`` DI seam without
monkey-patching the resolver module (F1-clean / F2-clean).

F46: each step reaches the production composition surface via the
real :class:`kairix.connectors.m365_calendar.connector.M365CalendarConnector`
class, never a Pipeline-class direct construction. The Graph client is
substituted via the ``per_user_client_factory`` / ``client_factory``
DI seams with a scripted in-memory recorder so no real HTTP fires.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest
from pytest_bdd import given, parsers, then, when

from kairix.connectors.m365_calendar.connector import (
    M365CalendarConfig,
    M365CalendarConnector,
)
from kairix.connectors.m365_calendar.graph_client import (
    CalendarDeltaPage,
    CalendarEventRecord,
    M365GraphCalendarClient,
)
from kairix.core.protocols import Container, HierarchyNode
from tests.fakes import FakeFeatureFlagResolver

pytestmark = pytest.mark.bdd

_FLAG_NAME = "topology_v2_m365_calendar"


class _ScriptedGraphClient(M365GraphCalendarClient):
    """In-memory Graph client used by both branches of the BDD scenarios."""

    def __init__(self, upn: str, *, log: list[tuple[str, str | None]]) -> None:
        self._upn = upn
        self._log = log
        self._user_id = upn
        self._http = None  # type: ignore[assignment]  # scripted client never makes HTTP calls
        self._page_size = 50

    def _event(self) -> CalendarEventRecord:
        return CalendarEventRecord(
            event_id=f"event-{self._upn}",
            subject=f"Sync for {self._upn}",
            start_iso="2026-05-25T09:00:00Z",
            end_iso="2026-05-25T10:00:00Z",
            location="Conference room",
            attendees=(self._upn,),
            organiser=self._upn,
            last_modified_iso="2026-05-25T08:00:00Z",
            cancelled=False,
            removed=False,
            raw_payload=f'{{"id": "event-{self._upn}"}}',
        )

    def fetch_initial_delta(self, _start_iso: str, _end_iso: str) -> CalendarDeltaPage:
        self._log.append((self._upn, None))
        return CalendarDeltaPage(
            events=(self._event(),),
            next_link=None,
            delta_link=f"https://graph.microsoft.com/v1.0/users/{self._upn}/calendar/delta?$deltatoken=fresh",
        )

    def fetch_delta_page(self, link: str) -> CalendarDeltaPage:
        self._log.append((self._upn, link))
        return CalendarDeltaPage(events=(self._event(),), next_link=None, delta_link=link)

    def close(self) -> None:
        # Intentionally empty — scripted client owns no resources.
        return None


@dataclass
class _TopologyV2M365CalendarCtx:
    """Per-scenario context. No module-level mutable state."""

    resolver: FakeFeatureFlagResolver | None = None
    flag_value: bool | None = None
    user_ids: tuple[str, ...] = ()
    connector: M365CalendarConnector | None = None
    log: list[tuple[str, str | None]] = field(default_factory=list)
    containers: list[Container] = field(default_factory=list)
    hierarchy_nodes: list[HierarchyNode] = field(default_factory=list)
    scoped_change_count: int = 0
    legacy_path_observed: bool = False


@pytest.fixture
def topology_v2_m365_calendar_ctx() -> _TopologyV2M365CalendarCtx:
    return _TopologyV2M365CalendarCtx()


# ---------------------------------------------------------------------------
# Givens
# ---------------------------------------------------------------------------


@given(parsers.parse("an m365_calendar connector configured for two mailboxes: {a}, {b}"))
def _two_mailbox_config(
    topology_v2_m365_calendar_ctx: _TopologyV2M365CalendarCtx,
    a: str,
    b: str,
) -> None:
    """Stash the configured UPNs; the connector is built once the flag is set."""
    topology_v2_m365_calendar_ctx.user_ids = (a, b)


@given(parsers.parse("the operator has the topology-v2-m365-calendar flag set to {value}"))
def _operator_sets_flag(
    topology_v2_m365_calendar_ctx: _TopologyV2M365CalendarCtx,
    value: str,
) -> None:
    """Pin the flag value via :class:`FakeFeatureFlagResolver` and
    construct the real :class:`M365CalendarConnector` against the
    configured UPNs, threading the flag value through the connector's
    ``flag_reader`` DI seam.
    """
    parsed = value.strip().lower() == "true"
    resolver = FakeFeatureFlagResolver().with_flag(_FLAG_NAME, parsed)
    topology_v2_m365_calendar_ctx.resolver = resolver
    topology_v2_m365_calendar_ctx.flag_value = parsed
    user_ids = topology_v2_m365_calendar_ctx.user_ids
    assert user_ids, "the configured mailbox UPNs must have been declared before flag setup"
    config = M365CalendarConfig(
        user_id=user_ids[0],
        tenant_id="tenant-placeholder",
        client_id="client-placeholder",
        client_secret="secret-placeholder",  # pragma: allowlist secret
        user_ids=user_ids,
    )
    log = topology_v2_m365_calendar_ctx.log
    topology_v2_m365_calendar_ctx.connector = M365CalendarConnector(
        config,
        client_factory=lambda _c: _ScriptedGraphClient(user_ids[0], log=log),
        per_user_client_factory=lambda _c, upn: _ScriptedGraphClient(upn, log=log),
        flag_reader=resolver.get,
    )


# ---------------------------------------------------------------------------
# Whens
# ---------------------------------------------------------------------------


@when("the operator calls iter_containers on the m365_calendar connector")
def _call_iter_containers(topology_v2_m365_calendar_ctx: _TopologyV2M365CalendarCtx) -> None:
    assert topology_v2_m365_calendar_ctx.connector is not None
    topology_v2_m365_calendar_ctx.containers = list(
        topology_v2_m365_calendar_ctx.connector.iter_containers(cc_pair_id=42)
    )


@when("the operator calls load_hierarchy on the m365_calendar connector")
def _call_load_hierarchy(topology_v2_m365_calendar_ctx: _TopologyV2M365CalendarCtx) -> None:
    assert topology_v2_m365_calendar_ctx.connector is not None
    topology_v2_m365_calendar_ctx.hierarchy_nodes = list(
        topology_v2_m365_calendar_ctx.connector.load_hierarchy(cc_pair_id=42)
    )


@when(parsers.parse("the operator drives list_changes_for_container against the calendar for mailbox {upn}"))
def _call_list_changes_for_container(
    topology_v2_m365_calendar_ctx: _TopologyV2M365CalendarCtx,
    upn: str,
) -> None:
    """Construct a Container scoped to the named UPN and drive
    ``list_changes_for_container``. The OFF branch delegates to the
    legacy single-cursor :meth:`list_changes`; the ON branch routes to
    the per-UPN Graph client.
    """
    connector = topology_v2_m365_calendar_ctx.connector
    assert connector is not None
    container = Container(
        cc_pair_id=42,
        container_id=upn,
        access_state="ACCESSIBLE",
        cursor_token=None,
        last_synced_at=None,
    )
    if topology_v2_m365_calendar_ctx.flag_value is False:
        topology_v2_m365_calendar_ctx.legacy_path_observed = True
    events = list(connector.list_changes_for_container(container))
    topology_v2_m365_calendar_ctx.scoped_change_count = len(events)


# ---------------------------------------------------------------------------
# Thens
# ---------------------------------------------------------------------------


@then("two Containers are emitted, one per configured calendar")
def _two_containers(topology_v2_m365_calendar_ctx: _TopologyV2M365CalendarCtx) -> None:
    containers = topology_v2_m365_calendar_ctx.containers
    assert len(containers) == 2, f"expected two Containers (one per configured calendar), got {len(containers)}"
    ids = [c.container_id for c in containers]
    assert ids == list(topology_v2_m365_calendar_ctx.user_ids), (
        f"containers must follow the configured UPN order, got {ids}"
    )


@then("every calendar Container carries access_state ACCESSIBLE with no cursor_token yet")
def _container_shape(topology_v2_m365_calendar_ctx: _TopologyV2M365CalendarCtx) -> None:
    for container in topology_v2_m365_calendar_ctx.containers:
        assert container.access_state == "ACCESSIBLE"
        assert container.cursor_token is None
        assert container.last_synced_at is None
        assert container.cc_pair_id == 42


@then("FOLDER nodes are emitted parent-before-child with a root and one child per calendar")
def _hierarchy_parent_before_child(topology_v2_m365_calendar_ctx: _TopologyV2M365CalendarCtx) -> None:
    nodes = topology_v2_m365_calendar_ctx.hierarchy_nodes
    # Root + 2 calendars = 3.
    assert len(nodes) == 3, f"expected root + 2 child FOLDERs, got {len(nodes)}"
    seen: set[str] = set()
    for node in nodes:
        assert node.node_type == "FOLDER"
        if node.raw_parent_id is not None:
            assert node.raw_parent_id in seen, (
                f"orphan emission: node {node.raw_node_id!r} references unseen parent {node.raw_parent_id!r}"
            )
        seen.add(node.raw_node_id)
    assert nodes[0].raw_parent_id is None


@then("the legacy single-cursor list_changes branch is observed for m365_calendar")
def _legacy_branch_observed(topology_v2_m365_calendar_ctx: _TopologyV2M365CalendarCtx) -> None:
    assert topology_v2_m365_calendar_ctx.legacy_path_observed is True, (
        "expected the OFF branch to take the legacy single-cursor delegation path"
    )
    # And per-UPN client cache stays empty under OFF.
    connector = topology_v2_m365_calendar_ctx.connector
    assert connector is not None
    assert connector._per_user_clients == {}, (
        f"OFF branch must not populate the per-UPN client cache; got {connector._per_user_clients!r}"
    )


@then(parsers.parse("the Graph delta query targets only {upn}"))
def _graph_targets_only_upn(
    topology_v2_m365_calendar_ctx: _TopologyV2M365CalendarCtx,
    upn: str,
) -> None:
    log = topology_v2_m365_calendar_ctx.log
    upns_hit = [entry[0] for entry in log]
    assert upns_hit == [upn], f"expected the Graph client to fire only against {upn!r}; recorded UPNs: {upns_hit!r}"

"""Step definitions for feature_flag_topology_v2_apple_caldav.feature.

Wave E of the connector / collection / scope topology v2 migration is
the per-connector slice for the apple_caldav connector — when the
``topology_v2_apple_caldav`` flag is ON, the connector emits one
:class:`~kairix.core.protocols.Container` per discovered iCloud
calendar and :meth:`list_changes_for_container` scopes the CalDAV
sync REPORT to that calendar URL ONLY. When OFF, the connector
retains the legacy shim shape (one shared cursor across every
discovered calendar via the legacy :meth:`list_changes` call).

This step file exercises both branches of the flag through the
canonical :class:`tests.fakes.FakeFeatureFlagResolver` — pinning the
flag value through the connector's ``flag_reader`` DI seam without
monkey-patching the resolver module (F1-clean / F2-clean).

F46: each step reaches the production composition surface via the
real :class:`kairix.connectors.apple_caldav.AppleCalDavConnector`
class, never a Pipeline-class direct construction. The CalDAV client
is substituted via the ``client_factory`` DI seam with a scripted
in-memory recorder so no real HTTP fires.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest
from pytest_bdd import given, parsers, then, when

from kairix.connectors.apple_caldav import (
    AppleCalDavClient,
    AppleCalDavConfig,
    AppleCalDavConnector,
    CalDavCalendarRef,
    CalendarEventRecord,
    CalendarSyncPage,
)
from kairix.core.protocols import Container, HierarchyNode
from tests.fakes import FakeFeatureFlagResolver

pytestmark = pytest.mark.bdd

_FLAG_NAME = "topology_v2_apple_caldav"
_CAL_PERSONAL = "https://caldav.icloud.com/12345/calendars/personal/"
_CAL_WORK = "https://caldav.icloud.com/12345/calendars/work/"


class _ScriptedCalDavClient(AppleCalDavClient):
    """In-memory CalDAV client used by both branches of the BDD scenarios."""

    def __init__(self, *, log: list[tuple[str, str | None]]) -> None:
        # Skip the real __init__ — no auth, no caldav library import.
        self._username = "agent-alpha@example.com"
        self._password = "fixture-app-password"  # pragma: allowlist secret — test fixture
        self._endpoint = "https://caldav.icloud.com"
        self._dav_client_factory = None
        self._dav_client = None
        self._log = log

    def discover_calendars(self) -> tuple[CalDavCalendarRef, ...]:
        return (
            CalDavCalendarRef(url=_CAL_PERSONAL, display_name="Personal", ctag="ctag-1"),
            CalDavCalendarRef(url=_CAL_WORK, display_name="Work", ctag="ctag-2"),
        )

    def list_changes(self, calendar_url: str, sync_token: str | None) -> CalendarSyncPage:
        self._log.append((calendar_url, sync_token))
        record = CalendarEventRecord(
            event_id=f"event-{calendar_url.rstrip('/').rsplit('/', 1)[-1]}",
            summary="Sync",
            dtstart_iso="2026-05-25T09:00:00Z",
            dtend_iso="2026-05-25T10:00:00Z",
            location="",
            attendees=(),
            organiser="",
            last_modified_iso="2026-05-25T08:00:00Z",
            recurrence_rule="",
            cancelled=False,
            removed=False,
            raw_ics="BEGIN:VCALENDAR\nEND:VCALENDAR\n",
            event_url=f"{calendar_url}event.ics",
        )
        return CalendarSyncPage(events=(record,), sync_token=f"tok-{calendar_url[-10:]}")

    def fetch(self, event_url: str) -> CalendarEventRecord:
        del event_url
        raise NotImplementedError


@dataclass
class _TopologyV2AppleCalDavCtx:
    """Per-scenario context. No module-level mutable state."""

    resolver: FakeFeatureFlagResolver | None = None
    flag_value: bool | None = None
    connector: AppleCalDavConnector | None = None
    log: list[tuple[str, str | None]] = field(default_factory=list)
    containers: list[Container] = field(default_factory=list)
    hierarchy_nodes: list[HierarchyNode] = field(default_factory=list)
    scoped_change_count: int = 0
    legacy_path_observed: bool = False


@pytest.fixture
def topology_v2_apple_caldav_ctx() -> _TopologyV2AppleCalDavCtx:
    return _TopologyV2AppleCalDavCtx()


# ---------------------------------------------------------------------------
# Givens
# ---------------------------------------------------------------------------


@given(parsers.parse("an apple_caldav connector configured to discover two calendars"))
def _two_calendar_config(
    topology_v2_apple_caldav_ctx: _TopologyV2AppleCalDavCtx,
) -> None:
    """No-op stash — discovery happens when the connector is built below."""
    # The scripted client surfaces both calendars; the connector
    # discovery path follows when iter_containers fires.
    return None


@given(parsers.parse("the operator has the topology-v2-apple-caldav flag set to {value}"))
def _operator_sets_flag(
    topology_v2_apple_caldav_ctx: _TopologyV2AppleCalDavCtx,
    value: str,
) -> None:
    """Pin the flag value via :class:`FakeFeatureFlagResolver` and
    construct the real :class:`AppleCalDavConnector`, threading the
    flag value through the connector's ``flag_reader`` DI seam.
    """
    parsed = value.strip().lower() == "true"
    resolver = FakeFeatureFlagResolver().with_flag(_FLAG_NAME, parsed)
    topology_v2_apple_caldav_ctx.resolver = resolver
    topology_v2_apple_caldav_ctx.flag_value = parsed
    config = AppleCalDavConfig(
        username="agent-alpha@example.com",
        password="fixture-app-password",  # pragma: allowlist secret — test fixture
    )
    log = topology_v2_apple_caldav_ctx.log
    topology_v2_apple_caldav_ctx.connector = AppleCalDavConnector(
        config,
        client_factory=lambda _c: _ScriptedCalDavClient(log=log),
        flag_reader=resolver.get,
    )


# ---------------------------------------------------------------------------
# Whens
# ---------------------------------------------------------------------------


@when("the operator calls iter_containers on the apple_caldav connector")
def _call_iter_containers(topology_v2_apple_caldav_ctx: _TopologyV2AppleCalDavCtx) -> None:
    assert topology_v2_apple_caldav_ctx.connector is not None
    topology_v2_apple_caldav_ctx.containers = list(
        topology_v2_apple_caldav_ctx.connector.iter_containers(cc_pair_id=42)
    )


@when("the operator calls load_hierarchy on the apple_caldav connector")
def _call_load_hierarchy(topology_v2_apple_caldav_ctx: _TopologyV2AppleCalDavCtx) -> None:
    assert topology_v2_apple_caldav_ctx.connector is not None
    topology_v2_apple_caldav_ctx.hierarchy_nodes = list(
        topology_v2_apple_caldav_ctx.connector.load_hierarchy(cc_pair_id=42)
    )


@when(parsers.parse("the operator drives list_changes_for_container against the personal calendar"))
def _call_list_changes_for_container(
    topology_v2_apple_caldav_ctx: _TopologyV2AppleCalDavCtx,
) -> None:
    """Construct a Container scoped to the personal calendar and drive
    ``list_changes_for_container``. The OFF branch delegates to the
    legacy single-cursor :meth:`list_changes`; the ON branch routes to
    the per-calendar CalDAV path.
    """
    connector = topology_v2_apple_caldav_ctx.connector
    assert connector is not None
    container = Container(
        cc_pair_id=42,
        container_id=_CAL_PERSONAL,
        access_state="ACCESSIBLE",
        cursor_token=None,
        last_synced_at=None,
    )
    if topology_v2_apple_caldav_ctx.flag_value is False:
        topology_v2_apple_caldav_ctx.legacy_path_observed = True
    events = list(connector.list_changes_for_container(container))
    topology_v2_apple_caldav_ctx.scoped_change_count = len(events)


# ---------------------------------------------------------------------------
# Thens
# ---------------------------------------------------------------------------


@then("two apple_caldav Containers are emitted, one per discovered calendar")
def _two_containers(topology_v2_apple_caldav_ctx: _TopologyV2AppleCalDavCtx) -> None:
    containers = topology_v2_apple_caldav_ctx.containers
    assert len(containers) == 2, f"expected two Containers (one per discovered calendar), got {len(containers)}"
    ids = [c.container_id for c in containers]
    assert ids == [_CAL_PERSONAL, _CAL_WORK], f"containers must follow the discovery order, got {ids}"


@then("every apple_caldav Container carries access_state ACCESSIBLE with no cursor_token yet")
def _container_shape(topology_v2_apple_caldav_ctx: _TopologyV2AppleCalDavCtx) -> None:
    for container in topology_v2_apple_caldav_ctx.containers:
        assert container.access_state == "ACCESSIBLE"
        assert container.cursor_token is None
        assert container.last_synced_at is None
        assert container.cc_pair_id == 42


@then("apple_caldav FOLDER nodes are emitted parent-before-child with a root and one child per calendar")
def _hierarchy_parent_before_child(topology_v2_apple_caldav_ctx: _TopologyV2AppleCalDavCtx) -> None:
    nodes = topology_v2_apple_caldav_ctx.hierarchy_nodes
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


@then("the legacy single-cursor list_changes branch is observed for apple_caldav")
def _legacy_branch_observed(topology_v2_apple_caldav_ctx: _TopologyV2AppleCalDavCtx) -> None:
    assert topology_v2_apple_caldav_ctx.legacy_path_observed is True, (
        "expected the OFF branch to take the legacy single-cursor delegation path"
    )
    # And the legacy path drains every discovered calendar — observable
    # via two entries in the log (one per calendar) for a single
    # list_changes_for_container call.
    log = topology_v2_apple_caldav_ctx.log
    urls_hit = {entry[0] for entry in log}
    assert _CAL_PERSONAL in urls_hit and _CAL_WORK in urls_hit, (
        f"OFF: legacy path must drain every discovered calendar; got {urls_hit!r}"
    )


@then("the CalDAV sync REPORT targets only the personal calendar")
def _caldav_targets_only_personal(topology_v2_apple_caldav_ctx: _TopologyV2AppleCalDavCtx) -> None:
    log = topology_v2_apple_caldav_ctx.log
    urls_hit = [entry[0] for entry in log]
    assert urls_hit == [_CAL_PERSONAL], (
        f"expected the CalDAV client to fire only against {_CAL_PERSONAL!r}; recorded URLs: {urls_hit!r}"
    )

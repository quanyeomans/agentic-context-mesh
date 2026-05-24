"""F54 integration coverage for the ``topology_v2_m365_calendar`` feature flag.

Wave E of the connector / collection / scope topology v2 migration is
the per-connector slice for the m365_calendar connector. When the
``topology_v2_m365_calendar`` flag is ON, the connector emits one
:class:`~kairix.core.protocols.Container` per configured calendar
(per UPN, each with its own Graph ``@odata.deltaLink`` as cursor) and
:meth:`list_changes_for_container` scopes the Graph delta query to
that calendar's UPN ONLY. When OFF, the connector retains the Wave B
shim shape — :meth:`list_changes_for_container` delegates to the
legacy single-cursor :meth:`list_changes` call that uses one shared
deltaLink across every configured calendar.

Per F54 (docs/architecture/feature-flag-architecture.md §5): every flag
needs integration coverage exercising both branches via
:class:`tests.fakes.FakeFeatureFlagResolver`. The string literal
``"topology_v2_m365_calendar"`` appears verbatim in every
``with_flag(...)`` call so the F54 check picks it up.

F47 — the multi-component pipeline (connector + scripted Graph client)
is constructed via real plugin construction with the
:class:`~kairix.connectors.m365_calendar.connector.M365CalendarConnector`
class itself; the flag is injected through the connector's
``flag_reader`` DI seam and the Graph client is injected through the
``per_user_client_factory`` / ``client_factory`` seams. No
monkey-patching of the resolver module, no real HTTP traffic.

Sabotage proofs (executed by the agent, mutate → confirm fail → restore):

  1. **Per-calendar cursor isolation** — in
     :meth:`M365CalendarConnector._list_changes_scoped`, mutated
     ``cursor = container.cursor_token`` to a shared module-level
     constant; confirmed
     ``test_flag_on_per_container_cursors_are_isolated`` fails because
     both calendars then fetch the same cursor instead of their own;
     restored.
  2. **F58 parent-before-child** — in
     :meth:`M365CalendarConnector.load_hierarchy`, swapped the yield
     order so the per-calendar children emit before the root;
     confirmed
     ``test_flag_on_load_hierarchy_parent_before_child`` fails with an
     orphan-emission assertion; restored.
  3. **Flag-OFF inertness** — replaced the
     ``if not self._flag_reader(...)`` guard in
     :meth:`M365CalendarConnector.list_changes_for_container` with
     ``if False:`` so the ON branch runs even when the flag is OFF;
     confirmed
     ``test_flag_off_list_changes_for_container_uses_legacy_path`` fails
     because the per-UPN client gets used instead of the legacy single
     client; restored.
"""

from __future__ import annotations

import pytest

from kairix.connectors.m365_calendar.connector import (
    M365CalendarConfig,
    M365CalendarConnector,
)
from kairix.connectors.m365_calendar.graph_client import (
    CalendarDeltaPage,
    CalendarEventRecord,
    M365GraphCalendarClient,
)
from kairix.core.protocols import (
    Container,
    HierarchyConnector,
    HierarchyNode,
    PollConnector,
)
from tests.fakes import FakeFeatureFlagResolver

pytestmark = pytest.mark.integration

_FLAG_NAME = "topology_v2_m365_calendar"
_UPN_ALICE = "alice@example.com"
_UPN_BOB = "bob@example.com"


# ---------------------------------------------------------------------------
# Scripted Graph client — records per-UPN cursor reads
# ---------------------------------------------------------------------------


class _RecordingGraphCalendarClient(M365GraphCalendarClient):
    """In-memory Graph client used by both branches.

    Records every ``(upn, cursor)`` tuple seen via
    :meth:`fetch_initial_delta` (cursor=None) and :meth:`fetch_delta_page`
    (cursor=<deltaLink>) so tests can assert per-calendar isolation.
    """

    def __init__(self, upn: str, *, log: list[tuple[str, str | None]]) -> None:
        self._upn = upn
        self._log = log
        # The parent class touches these attrs in close(); set defaults
        # so we never construct a real httpx.Client.
        self._user_id = upn
        self._http = None  # type: ignore[assignment]  # scripted client never makes HTTP calls; bypass httpx.Client construction
        self._page_size = 50

    def _event_for(self, upn: str) -> CalendarEventRecord:
        """One synthetic event tagged with the UPN so leaks are observable."""
        return CalendarEventRecord(
            event_id=f"event-{upn}",
            subject=f"Sync for {upn}",
            start_iso="2026-05-25T09:00:00Z",
            end_iso="2026-05-25T10:00:00Z",
            location="Conference room",
            attendees=(upn,),
            organiser=upn,
            last_modified_iso="2026-05-25T08:00:00Z",
            cancelled=False,
            removed=False,
            raw_payload=f'{{"id": "event-{upn}"}}',
        )

    def fetch_initial_delta(self, _start_iso: str, _end_iso: str) -> CalendarDeltaPage:
        self._log.append((self._upn, None))
        return CalendarDeltaPage(
            events=(self._event_for(self._upn),),
            next_link=None,
            delta_link=f"https://graph.microsoft.com/v1.0/users/{self._upn}/calendar/calendarView/delta?$deltatoken=fresh",
        )

    def fetch_delta_page(self, link: str) -> CalendarDeltaPage:
        self._log.append((self._upn, link))
        return CalendarDeltaPage(
            events=(self._event_for(self._upn),),
            next_link=None,
            delta_link=link,
        )

    def close(self) -> None:
        # Intentionally empty — scripted client owns no resources.
        return None


def _build_connector(
    *, flag_on: bool, log: list[tuple[str, str | None]], user_ids: tuple[str, ...] = ()
) -> M365CalendarConnector:
    """Construct the production connector with both DI seams pinned.

    F54 — verbatim literal so the both-branch grep picks up the flag
    name. Each branch keeps its own ``with_flag(...)`` call so the OFF
    + ON pattern is mechanically observable.
    """
    if flag_on:
        resolver = FakeFeatureFlagResolver().with_flag("topology_v2_m365_calendar", True)
    else:
        resolver = FakeFeatureFlagResolver().with_flag("topology_v2_m365_calendar", False)

    config = M365CalendarConfig(
        user_id=_UPN_ALICE,
        tenant_id="tenant-placeholder",
        client_id="client-placeholder",
        client_secret="secret-placeholder",  # pragma: allowlist secret
        user_ids=user_ids,
    )
    return M365CalendarConnector(
        config,
        client_factory=lambda _c: _RecordingGraphCalendarClient(_UPN_ALICE, log=log),
        per_user_client_factory=lambda _c, upn: _RecordingGraphCalendarClient(upn, log=log),
        flag_reader=resolver.get,
    )


# ---------------------------------------------------------------------------
# Flag registration + protocol satisfaction (cheap correctness pins)
# ---------------------------------------------------------------------------


def test_topology_v2_m365_calendar_flag_registered() -> None:
    """The flag exists in the registry, defaults False, stage=introduce."""
    from kairix.core.features.registry import REGISTRY

    assert "topology_v2_m365_calendar" in REGISTRY
    entry = REGISTRY["topology_v2_m365_calendar"]
    assert entry.default is False
    assert entry.stage == "introduce"
    assert entry.owner == "connector-framework"
    assert entry.target_retire_in == "v2027.5.24"


def test_m365_calendar_connector_satisfies_poll_and_hierarchy_protocols_under_both_branches() -> None:
    """Both branches keep the connector satisfying the capability Protocols."""
    log: list[tuple[str, str | None]] = []
    off = _build_connector(flag_on=False, log=log)
    on = _build_connector(flag_on=True, log=log)
    assert isinstance(off, PollConnector)
    assert isinstance(off, HierarchyConnector)
    assert isinstance(on, PollConnector)
    assert isinstance(on, HierarchyConnector)


# ---------------------------------------------------------------------------
# OFF branch — Wave B shim behaviour
# ---------------------------------------------------------------------------


def test_flag_off_iter_containers_still_yields_singleton_for_legacy_config() -> None:
    """OFF: iter_containers still yields one Container per configured UPN.

    iter_containers itself isn't flag-gated — the value-add is gating
    list_changes_for_container's per-cursor isolation. Confirming the
    OFF branch still emits a Container per configured calendar means
    the framework can plan routing identically; what differs is the
    cursor-read path.
    """
    log: list[tuple[str, str | None]] = []
    connector = _build_connector(flag_on=False, log=log)
    containers = list(connector.iter_containers(cc_pair_id=7))
    assert [c.container_id for c in containers] == [_UPN_ALICE]


def test_flag_off_list_changes_for_container_uses_legacy_path() -> None:
    """OFF: list_changes_for_container delegates to legacy single-cursor list_changes.

    Sabotage proof for #3: replacing the ``if not self._flag_reader(...)``
    guard with ``if False:`` makes this test fail because the per-UPN
    client gets used (logging the configured UPN as Alice) instead of
    the legacy single client (logging Alice as well, but via a
    different code path — the log entries above ``container.cursor_token``
    of None vs the legacy first-page fetch differ in whether the cursor
    is read from the container at all).

    The clearest assertion is that the legacy single client (created
    by ``client_factory``) is what serves the request, observed via the
    connector's ``last_delta_link`` getting populated by the legacy
    drain path. Under OFF, calling list_changes_for_container on a
    container with cursor_token=None must yield events AND the legacy
    delta link must surface.
    """
    log: list[tuple[str, str | None]] = []
    connector = _build_connector(flag_on=False, log=log)
    container = Container(
        cc_pair_id=7,
        container_id=_UPN_ALICE,
        access_state="ACCESSIBLE",
        cursor_token=None,
        last_synced_at=None,
    )
    events = list(connector.list_changes_for_container(container))
    assert events, "OFF: legacy single-cursor list_changes must still surface events"
    # Legacy path bumps the connector-wide deltaLink — proves the
    # OFF branch did NOT go through the per-UPN path (the per-UPN
    # path also stamps last_delta_link, so we additionally check that
    # the per_user_clients cache stays empty).
    assert connector.last_delta_link is not None
    assert connector._per_user_clients == {}, (
        "OFF: per-UPN client cache must stay empty when the legacy path serves the request"
    )


# ---------------------------------------------------------------------------
# ON branch — Wave E real implementations
# ---------------------------------------------------------------------------


def test_flag_on_iter_containers_yields_one_per_configured_upn() -> None:
    """ON: iter_containers yields one Container per configured UPN."""
    log: list[tuple[str, str | None]] = []
    connector = _build_connector(
        flag_on=True,
        log=log,
        user_ids=(_UPN_ALICE, _UPN_BOB),
    )
    containers = list(connector.iter_containers(cc_pair_id=7))
    ids = [c.container_id for c in containers]
    assert ids == [_UPN_ALICE, _UPN_BOB], (
        f"ON: expected one Container per configured UPN in declared order; got {ids!r}"
    )
    for c in containers:
        assert c.cc_pair_id == 7
        assert c.access_state == "ACCESSIBLE"
        assert c.cursor_token is None
        assert c.last_synced_at is None


def test_flag_on_iter_containers_singleton_when_only_user_id_set() -> None:
    """ON: single-calendar config (only user_id) yields one Container."""
    log: list[tuple[str, str | None]] = []
    connector = _build_connector(flag_on=True, log=log)
    containers = list(connector.iter_containers(cc_pair_id=7))
    assert [c.container_id for c in containers] == [_UPN_ALICE]


def test_flag_on_load_hierarchy_parent_before_child() -> None:
    """ON: load_hierarchy emits parent-before-child per F58.

    Sabotage proof for #2: swapping the yield order so per-calendar
    children emit before the root makes this test fail (orphan
    emission).
    """
    log: list[tuple[str, str | None]] = []
    connector = _build_connector(
        flag_on=True,
        log=log,
        user_ids=(_UPN_ALICE, _UPN_BOB),
    )
    nodes = list(connector.load_hierarchy(cc_pair_id=7))
    # Root + 2 per-calendar children = 3.
    assert len(nodes) == 3, f"ON: expected root + 2 children, got {len(nodes)}"
    seen: set[str] = set()
    for node in nodes:
        assert isinstance(node, HierarchyNode)
        assert node.node_type == "FOLDER"
        if node.raw_parent_id is not None:
            assert node.raw_parent_id in seen, (
                f"orphan emission: {node.raw_node_id!r} references unseen parent {node.raw_parent_id!r}"
            )
        seen.add(node.raw_node_id)
    # Structural check — root first, then per-calendar children.
    assert nodes[0].raw_parent_id is None
    assert nodes[0].raw_node_id == "m365-calendar"
    child_ids = {n.raw_node_id for n in nodes[1:]}
    assert child_ids == {_UPN_ALICE, _UPN_BOB}


def test_flag_on_list_changes_scopes_to_containers_upn() -> None:
    """ON: list_changes_for_container only hits the container's UPN.

    Calling list_changes_for_container against Alice's container then
    Bob's container must produce two distinct per-UPN Graph requests —
    no cross-calendar leakage.
    """
    log: list[tuple[str, str | None]] = []
    connector = _build_connector(
        flag_on=True,
        log=log,
        user_ids=(_UPN_ALICE, _UPN_BOB),
    )
    alice = Container(
        cc_pair_id=7,
        container_id=_UPN_ALICE,
        access_state="ACCESSIBLE",
        cursor_token=None,
        last_synced_at=None,
    )
    bob = Container(
        cc_pair_id=7,
        container_id=_UPN_BOB,
        access_state="ACCESSIBLE",
        cursor_token=None,
        last_synced_at=None,
    )
    list(connector.list_changes_for_container(alice))
    list(connector.list_changes_for_container(bob))
    upns_hit = [entry[0] for entry in log]
    assert upns_hit == [_UPN_ALICE, _UPN_BOB], f"ON: each container must hit its own UPN; got {upns_hit!r}"


def test_flag_on_per_container_cursors_are_isolated() -> None:
    """ON: each container's cursor_token drives its own Graph request.

    Sabotage proof for #1: replacing ``cursor = container.cursor_token``
    in ``_list_changes_scoped`` with a shared module-level constant
    makes this test fail because both containers then fetch the same
    cursor instead of their own.

    Concrete shape: Alice's container carries a real ``@odata.deltaLink``
    so the Graph client receives ``fetch_delta_page(alice_link)``; Bob's
    container is fresh (``cursor_token=None``) so the Graph client
    receives ``fetch_initial_delta(...)``. The recorded log proves the
    two paths are independent.
    """
    log: list[tuple[str, str | None]] = []
    connector = _build_connector(
        flag_on=True,
        log=log,
        user_ids=(_UPN_ALICE, _UPN_BOB),
    )
    alice_cursor = "https://graph.microsoft.com/v1.0/users/alice/calendar/delta?$deltatoken=alice-prev"
    alice = Container(
        cc_pair_id=7,
        container_id=_UPN_ALICE,
        access_state="ACCESSIBLE",
        cursor_token=alice_cursor,
        last_synced_at=None,
    )
    bob = Container(
        cc_pair_id=7,
        container_id=_UPN_BOB,
        access_state="ACCESSIBLE",
        cursor_token=None,
        last_synced_at=None,
    )
    list(connector.list_changes_for_container(alice))
    list(connector.list_changes_for_container(bob))
    # Alice hits her cursor; Bob hits initial-delta (cursor=None).
    assert (_UPN_ALICE, alice_cursor) in log, f"ON: Alice's per-container cursor was not read; log={log!r}"
    assert (_UPN_BOB, None) in log, f"ON: Bob's fresh container did not trigger initial delta; log={log!r}"
    # And the cursors must not be cross-contaminated.
    bob_entries = [entry for entry in log if entry[0] == _UPN_BOB]
    assert all(entry[1] is None or "bob" in (entry[1] or "") for entry in bob_entries), (
        f"ON: Bob's container saw Alice's cursor — per-container isolation broken; log={log!r}"
    )


def test_flag_on_list_changes_emits_change_events_for_container_upn() -> None:
    """ON: list_changes_for_container yields events whose item_id matches the UPN.

    Confirms the per-container request actually drains Graph delta and
    returns ChangeEvent objects keyed by the UPN-tagged event id.
    """
    log: list[tuple[str, str | None]] = []
    connector = _build_connector(
        flag_on=True,
        log=log,
        user_ids=(_UPN_ALICE,),
    )
    alice = Container(
        cc_pair_id=7,
        container_id=_UPN_ALICE,
        access_state="ACCESSIBLE",
        cursor_token=None,
        last_synced_at=None,
    )
    events = list(connector.list_changes_for_container(alice))
    assert events, "ON: per-container request must yield at least one ChangeEvent"
    for ev in events:
        assert ev.item_id == f"event-{_UPN_ALICE}", (
            f"ON: event item_id must be scoped to the container's UPN; got {ev.item_id!r}"
        )

"""F54 integration coverage for the ``topology_v2_apple_caldav`` feature flag.

Wave E of the connector / collection / scope topology v2 migration is
the per-connector slice for the apple_caldav connector. When the
``topology_v2_apple_caldav`` flag is ON, the connector emits one
:class:`~kairix.core.protocols.Container` per discovered (or
operator-pinned) iCloud calendar (each with its own CalDAV sync token
as cursor) and :meth:`list_changes_for_container` scopes the
``<sync-collection>`` REPORT to that calendar URL ONLY. When OFF, the
connector retains the legacy shim shape —
:meth:`list_changes_for_container` delegates to the legacy
single-cursor :meth:`list_changes` call.

Per F54 (docs/architecture/feature-flag-architecture.md §5): every
flag needs integration coverage exercising both branches via
:class:`tests.fakes.FakeFeatureFlagResolver`. The string literal
``"topology_v2_apple_caldav"`` appears verbatim in every
``with_flag(...)`` call so the F54 check picks it up.

F47 — the multi-component pipeline (connector + scripted CalDAV
client) is constructed via real plugin construction with the
:class:`~kairix.connectors.apple_caldav.AppleCalDavConnector` class
itself; the flag is injected through the connector's ``flag_reader``
DI seam and the CalDAV client is injected through the
``client_factory`` seam. No monkey-patching of the resolver module,
no real HTTP traffic.

Sabotage proofs (executed by the agent, mutate → confirm fail →
restore):

  1. **Per-calendar cursor isolation** — in
     :meth:`AppleCalDavConnector._list_changes_scoped`, replace the
     ``container.cursor_token`` read with a shared module-level
     constant; confirmed
     ``test_flag_on_per_container_cursors_are_isolated`` fails because
     both calendars then fetch the same cursor instead of their own;
     restored.
  2. **F58 parent-before-child** — in
     :meth:`AppleCalDavConnector.load_hierarchy`, swap the yield
     order so per-calendar children emit before the root; confirmed
     ``test_flag_on_load_hierarchy_parent_before_child`` fails with
     an orphan-emission assertion; restored.
  3. **Flag-OFF inertness** — replace the
     ``if not self._flag_reader(...)`` guard in
     :meth:`AppleCalDavConnector.list_changes_for_container` with
     ``if False:`` so the ON branch runs even when the flag is OFF;
     confirmed
     ``test_flag_off_list_changes_for_container_uses_legacy_path``
     fails because the scoped path records per-container calls
     instead of the composite-cursor legacy path; restored.
"""

from __future__ import annotations

import pytest

from kairix.connectors.apple_caldav import (
    AppleCalDavClient,
    AppleCalDavConfig,
    AppleCalDavConnector,
    CalDavCalendarRef,
    CalendarEventRecord,
    CalendarSyncPage,
)
from kairix.core.protocols import (
    Container,
    HierarchyConnector,
    HierarchyNode,
    PollConnector,
)
from tests.fakes import FakeFeatureFlagResolver

pytestmark = pytest.mark.integration

_FLAG_NAME = "topology_v2_apple_caldav"
_CAL_PERSONAL = "https://caldav.icloud.com/12345/calendars/personal/"
_CAL_WORK = "https://caldav.icloud.com/12345/calendars/work/"


class _RecordingClient(AppleCalDavClient):
    """In-memory CalDAV client used by both branches.

    Records every ``(calendar_url, sync_token)`` tuple seen via
    :meth:`list_changes` so tests can assert per-calendar isolation.
    """

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
            summary=f"Sync for {calendar_url}",
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


def _build_connector(
    *, flag_on: bool, log: list[tuple[str, str | None]], calendar_ids: tuple[str, ...] = ()
) -> AppleCalDavConnector:
    """Construct the production connector with both DI seams pinned.

    F54 — verbatim literal so the both-branch grep picks up the flag
    name. Each branch keeps its own ``with_flag(...)`` call so the
    OFF + ON pattern is mechanically observable.
    """
    if flag_on:
        resolver = FakeFeatureFlagResolver().with_flag("topology_v2_apple_caldav", True)
    else:
        resolver = FakeFeatureFlagResolver().with_flag("topology_v2_apple_caldav", False)

    config = AppleCalDavConfig(
        username="agent-alpha@example.com",
        password="fixture-app-password",  # pragma: allowlist secret — test fixture
        calendar_ids=calendar_ids,
    )
    return AppleCalDavConnector(
        config,
        client_factory=lambda _c: _RecordingClient(log=log),
        flag_reader=resolver.get,
    )


# ---------------------------------------------------------------------------
# Flag registration + protocol satisfaction (cheap correctness pins)
# ---------------------------------------------------------------------------


def test_topology_v2_apple_caldav_flag_registered() -> None:
    """The flag exists in the registry, defaults False, stage=introduce."""
    from kairix.core.features.registry import REGISTRY

    assert "topology_v2_apple_caldav" in REGISTRY
    entry = REGISTRY["topology_v2_apple_caldav"]
    assert entry.default is False
    assert entry.stage == "introduce"
    assert entry.owner == "connector-framework"
    # Uses the canonical _TOPOLOGY_V2_TARGET_RETIRE_IN constant
    # (currently v2027.5.23 — Wave A landing 2026-05-21 + 12 months).
    assert entry.target_retire_in.startswith("v2027.5.2")


def test_apple_caldav_connector_satisfies_poll_and_hierarchy_protocols_under_both_branches() -> None:
    """Both branches keep the connector satisfying the capability Protocols."""
    log: list[tuple[str, str | None]] = []
    off = _build_connector(flag_on=False, log=log)
    on = _build_connector(flag_on=True, log=log)
    assert isinstance(off, PollConnector)
    assert isinstance(off, HierarchyConnector)
    assert isinstance(on, PollConnector)
    assert isinstance(on, HierarchyConnector)


# ---------------------------------------------------------------------------
# OFF branch — legacy shim behaviour
# ---------------------------------------------------------------------------


def test_flag_off_iter_containers_still_yields_one_per_discovered_calendar() -> None:
    """OFF: iter_containers still yields one Container per discovered calendar."""
    log: list[tuple[str, str | None]] = []
    connector = _build_connector(flag_on=False, log=log)
    containers = list(connector.iter_containers(cc_pair_id=7))
    ids = [c.container_id for c in containers]
    assert ids == [_CAL_PERSONAL, _CAL_WORK]


def test_flag_off_list_changes_for_container_uses_legacy_path() -> None:
    """OFF: list_changes_for_container delegates to legacy list_changes.

    Sabotage proof for #3: replacing the ``if not self._flag_reader(...)``
    guard with ``if False:`` would make the ON branch run; the legacy
    path drains EVERY discovered calendar in one call, recording
    multiple entries in the log even for a single
    list_changes_for_container call.
    """
    log: list[tuple[str, str | None]] = []
    connector = _build_connector(flag_on=False, log=log)
    container = Container(
        cc_pair_id=7,
        container_id=_CAL_PERSONAL,
        access_state="ACCESSIBLE",
        cursor_token=None,
        last_synced_at=None,
    )
    events = list(connector.list_changes_for_container(container))
    assert events, "OFF: legacy list_changes must still surface events"
    # Legacy path drains every discovered calendar; the log records
    # both calendars even though we only asked for one container.
    urls_hit = [entry[0] for entry in log]
    assert _CAL_PERSONAL in urls_hit and _CAL_WORK in urls_hit, (
        f"OFF: legacy path must drain every discovered calendar; got {urls_hit!r}"
    )


# ---------------------------------------------------------------------------
# ON branch — Wave E real implementations
# ---------------------------------------------------------------------------


def test_flag_on_iter_containers_yields_one_per_discovered_calendar() -> None:
    """ON: iter_containers yields one Container per discovered calendar."""
    log: list[tuple[str, str | None]] = []
    connector = _build_connector(flag_on=True, log=log)
    containers = list(connector.iter_containers(cc_pair_id=7))
    ids = [c.container_id for c in containers]
    assert ids == [_CAL_PERSONAL, _CAL_WORK], (
        f"ON: expected one Container per discovered calendar in declared order; got {ids!r}"
    )
    for c in containers:
        assert c.cc_pair_id == 7
        assert c.access_state == "ACCESSIBLE"
        assert c.cursor_token is None
        assert c.last_synced_at is None


def test_flag_on_load_hierarchy_parent_before_child() -> None:
    """ON: load_hierarchy emits parent-before-child per F58.

    Sabotage proof for #2: swapping the yield order so per-calendar
    children emit before the root makes this test fail (orphan
    emission).
    """
    log: list[tuple[str, str | None]] = []
    connector = _build_connector(flag_on=True, log=log)
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
    assert nodes[0].raw_node_id == "apple-caldav"
    child_ids = {n.raw_node_id for n in nodes[1:]}
    assert child_ids == {_CAL_PERSONAL, _CAL_WORK}


def test_flag_on_list_changes_scopes_to_containers_calendar_url() -> None:
    """ON: list_changes_for_container only hits the container's calendar URL.

    Calling list_changes_for_container against the personal calendar
    then the work calendar must produce two distinct per-calendar
    CalDAV requests — no cross-calendar leakage.
    """
    log: list[tuple[str, str | None]] = []
    connector = _build_connector(flag_on=True, log=log)
    personal = Container(
        cc_pair_id=7,
        container_id=_CAL_PERSONAL,
        access_state="ACCESSIBLE",
        cursor_token=None,
        last_synced_at=None,
    )
    work = Container(
        cc_pair_id=7,
        container_id=_CAL_WORK,
        access_state="ACCESSIBLE",
        cursor_token=None,
        last_synced_at=None,
    )
    list(connector.list_changes_for_container(personal))
    list(connector.list_changes_for_container(work))
    urls_hit = [entry[0] for entry in log]
    assert urls_hit == [_CAL_PERSONAL, _CAL_WORK], f"ON: each container must hit its own URL; got {urls_hit!r}"


def test_flag_on_per_container_cursors_are_isolated() -> None:
    """ON: each container's cursor_token drives its own CalDAV request.

    Sabotage proof for #1: replacing
    ``client.list_changes(container.container_id, container.cursor_token)``
    in ``_list_changes_scoped`` with a shared module-level constant
    makes this test fail because both containers then fetch the same
    cursor instead of their own.
    """
    log: list[tuple[str, str | None]] = []
    connector = _build_connector(flag_on=True, log=log)
    personal_cursor = "personal-sync-token-prev"
    personal = Container(
        cc_pair_id=7,
        container_id=_CAL_PERSONAL,
        access_state="ACCESSIBLE",
        cursor_token=personal_cursor,
        last_synced_at=None,
    )
    work = Container(
        cc_pair_id=7,
        container_id=_CAL_WORK,
        access_state="ACCESSIBLE",
        cursor_token=None,
        last_synced_at=None,
    )
    list(connector.list_changes_for_container(personal))
    list(connector.list_changes_for_container(work))
    # Personal hits its cursor; Work hits initial (cursor=None).
    assert (_CAL_PERSONAL, personal_cursor) in log, f"ON: Personal's per-container cursor was not read; log={log!r}"
    assert (_CAL_WORK, None) in log, f"ON: Work's fresh container did not trigger initial sync; log={log!r}"


def test_flag_on_list_changes_emits_change_events_for_container() -> None:
    """ON: list_changes_for_container yields events for the right container."""
    log: list[tuple[str, str | None]] = []
    connector = _build_connector(flag_on=True, log=log)
    personal = Container(
        cc_pair_id=7,
        container_id=_CAL_PERSONAL,
        access_state="ACCESSIBLE",
        cursor_token=None,
        last_synced_at=None,
    )
    events = list(connector.list_changes_for_container(personal))
    assert events, "ON: per-container request must yield at least one ChangeEvent"
    for ev in events:
        assert "personal" in ev.item_id, f"ON: event item_id must be scoped to the container; got {ev.item_id!r}"

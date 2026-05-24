"""Unit tests for :class:`kairix.connectors.m365_calendar.M365CalendarConnector`.

Scope per the KP-3 brief:

  * First sync without cursor → drives the initial date-window query
    and emits ``created`` ChangeEvents.
  * Subsequent sync with persisted cursor → drives the delta-page
    query and distinguishes created vs modified by known-id tracking.
  * Cancelled event → surfaces as a ``deleted`` ChangeEvent.
  * Tombstoned (``@removed``) event → surfaces as a ``deleted`` ChangeEvent.
  * source_link → returns the Outlook web deep-link URL.
  * fetch → returns the cached Graph payload as a ``RawArtefact``;
    rejects ids never seen by ``list_changes``.
  * make_connector → required-key validation produces a typed error
    with an actionable affordance.
  * Sabotage proof (executed below): mutating the connector's
    ``_record_to_change_event`` mapping confirms the per-event op
    classification is load-bearing.

The Graph client is replaced with a recording stand-in that pulls
:class:`CalendarDeltaPage` instances from an in-memory queue. No
network I/O, no OAuth2 exchange.

F1-clean (no monkey-patching production code), F6-clean (every test
seam is a real callable default), F8 carries ``@pytest.mark.unit``.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest

from kairix.connectors.m365_calendar import (
    M365CalendarConfig,
    M365CalendarConnector,
    make_connector,
)
from kairix.connectors.m365_calendar.graph_client import (
    CalendarDeltaPage,
    CalendarEventRecord,
    M365GraphCalendarClient,
)
from kairix.core.protocols import ChangeEvent, RawArtefact


def _event(
    event_id: str,
    *,
    cancelled: bool = False,
    removed: bool = False,
    subject: str = "Team sync",
) -> CalendarEventRecord:
    return CalendarEventRecord(
        event_id=event_id,
        subject=subject if not removed else "",
        start_iso="2026-05-25T09:00:00Z" if not removed else "",
        end_iso="2026-05-25T10:00:00Z" if not removed else "",
        location="Conference room" if not removed else "",
        attendees=("alpha@example.com",) if not removed else (),
        organiser="organiser@example.com" if not removed else "",
        last_modified_iso="2026-05-25T08:00:00Z" if not removed else "",
        cancelled=cancelled,
        removed=removed,
        raw_payload=('{"id": "' + event_id + '"}') if not removed else "",
    )


def _page(*events: CalendarEventRecord, delta_link: str = "delta-link-1") -> CalendarDeltaPage:
    return CalendarDeltaPage(events=tuple(events), next_link=None, delta_link=delta_link)


class _RecordingClient(M365GraphCalendarClient):
    """In-memory stand-in. Drains a queue of pre-built pages.

    Tracks each Graph call into ``initial_calls`` / ``delta_calls`` so
    the tests assert which entry point fired without coupling to httpx.
    """

    def __init__(self, pages: list[CalendarDeltaPage]) -> None:
        self._queue = list(pages)
        self._user_id = "operator@example.com"
        self._http = None  # type: ignore[assignment]  # F3 rationale: scripted client never makes HTTP calls
        self._page_size = 50
        self.initial_calls: list[tuple[str, str]] = []
        self.delta_calls: list[str] = []

    def fetch_initial_delta(self, start_iso: str, end_iso: str) -> CalendarDeltaPage:
        self.initial_calls.append((start_iso, end_iso))
        return self._queue.pop(0)

    def fetch_delta_page(self, link: str) -> CalendarDeltaPage:
        self.delta_calls.append(link)
        return self._queue.pop(0)

    def close(self) -> None:
        # Intentionally empty — scripted client owns no resources.
        return None


def _config() -> M365CalendarConfig:
    return M365CalendarConfig(
        user_id="operator@example.com",
        tenant_id="placeholder-tenant",
        client_id="placeholder-client",
        client_secret="placeholder-secret",  # pragma: allowlist secret
    )


class _CapturingFactory:
    """Client factory that captures the constructed _RecordingClient.

    Exposes the constructed client as ``self.client`` so tests can
    assert on the recording state (initial_calls / delta_calls) after
    the connector has driven the factory. Used instead of attaching an
    attribute to a plain function — that pattern breaks mypy because
    function objects don't statically expose attributes.
    """

    def __init__(self, pages: list[CalendarDeltaPage]) -> None:
        self.client = _RecordingClient(pages)

    def __call__(self, _c: M365CalendarConfig) -> _RecordingClient:
        return self.client


def _factory_for(pages: list[CalendarDeltaPage]) -> _CapturingFactory:
    """Build a capturing factory wrapping a fresh _RecordingClient."""
    return _CapturingFactory(pages)


def _fixed_clock() -> datetime:
    """Deterministic clock anchored at 2026-05-22T00:00:00Z.

    Used so the date-window the connector requests is stable across
    runs — the unit tests can then assert exact start/end ISO values.
    """
    return datetime(2026, 5, 22, 0, 0, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# First-sync date-window query
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_first_sync_emits_created_for_each_event() -> None:
    """Empty cursor → connector queries calendarView/delta with date window
    and emits one ``created`` ChangeEvent per scripted event.

    Sabotage-proof: change :meth:`_record_to_change_event` to always
    return None for non-removed events; this test fails because no
    events surface.
    """
    factory = _factory_for([_page(_event("ev-alpha"), _event("ev-bravo"))])
    connector = M365CalendarConnector(_config(), client_factory=factory, clock=_fixed_clock)

    events = list(connector.list_changes(cursor=None))

    assert [(e.op, e.item_id) for e in events] == [
        ("created", "ev-alpha"),
        ("created", "ev-bravo"),
    ]
    # Initial-delta endpoint, not the delta-page endpoint.
    assert factory.client.initial_calls, "expected the initial-delta endpoint to fire on first sync"
    assert factory.client.delta_calls == [], "first sync must not call the delta-page endpoint"


@pytest.mark.unit
def test_first_sync_uses_configured_date_window() -> None:
    """The initial date-window respects window_days_back / window_days_forward.

    Sabotage-proof: change the connector to ignore ``window_days_back``;
    this test fails because the start_iso then no longer matches the
    expected 7-day window.
    """
    config = M365CalendarConfig(
        user_id="operator@example.com",
        tenant_id="placeholder-tenant",
        client_id="placeholder-client",
        client_secret="placeholder-secret",  # pragma: allowlist secret
        window_days_back=7,
        window_days_forward=30,
    )
    factory = _factory_for([_page(_event("ev-alpha"))])
    connector = M365CalendarConnector(config, client_factory=factory, clock=_fixed_clock)

    list(connector.list_changes(cursor=None))

    start_iso, end_iso = factory.client.initial_calls[0]
    # 2026-05-22T00:00:00Z minus 7 days = 2026-05-15T00:00:00Z
    assert start_iso == "2026-05-15T00:00:00Z", f"unexpected window start: {start_iso!r}"
    # 2026-05-22T00:00:00Z plus 30 days = 2026-06-21T00:00:00Z
    assert end_iso == "2026-06-21T00:00:00Z", f"unexpected window end: {end_iso!r}"


@pytest.mark.unit
def test_first_sync_exposes_persisted_delta_link() -> None:
    """The connector exposes the Graph-returned delta link as the next cursor.

    Sabotage-proof: drop the delta_link capture in :meth:`_drain`;
    this test fails because ``last_delta_link`` stays ``None``.
    """
    factory = _factory_for([_page(_event("ev-alpha"), delta_link="cursor-after-first")])
    connector = M365CalendarConnector(_config(), client_factory=factory, clock=_fixed_clock)

    list(connector.list_changes(cursor=None))

    assert connector.last_delta_link == "cursor-after-first"


# ---------------------------------------------------------------------------
# Delta-cursor follow-up query
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_delta_cursor_drives_delta_page_endpoint() -> None:
    """Non-None cursor → connector calls fetch_delta_page, not initial.

    Sabotage-proof: swap the if/else in :meth:`_fetch_first_page`;
    this test fails because the initial-delta endpoint then fires.
    """
    factory = _factory_for([_page(_event("ev-charlie"))])
    connector = M365CalendarConnector(_config(), client_factory=factory, clock=_fixed_clock)

    list(connector.list_changes(cursor="cursor-from-previous-tick"))

    assert factory.client.delta_calls == ["cursor-from-previous-tick"]
    assert factory.client.initial_calls == [], "delta-cursor sync must not call the initial-delta endpoint"


@pytest.mark.unit
def test_repeated_event_id_surfaces_as_modified() -> None:
    """An id the connector has already emitted as ``created`` surfaces as ``modified``.

    Sabotage-proof: drop the ``_known_ids`` membership check; this
    test fails because the second emission is then classified as
    ``created`` instead of ``modified``.
    """
    factory = _factory_for(
        [
            _page(_event("ev-alpha"), delta_link="cursor-1"),
            _page(_event("ev-alpha"), delta_link="cursor-2"),
        ]
    )
    connector = M365CalendarConnector(_config(), client_factory=factory, clock=_fixed_clock)

    first = list(connector.list_changes(cursor=None))
    second = list(connector.list_changes(cursor="cursor-1"))

    assert [(e.op, e.item_id) for e in first] == [("created", "ev-alpha")]
    assert [(e.op, e.item_id) for e in second] == [("modified", "ev-alpha")]


@pytest.mark.unit
def test_seed_known_ids_marks_first_emission_as_modified() -> None:
    """The :meth:`seed_known_ids` seam pre-populates the known-id set.

    Sabotage-proof: stub :meth:`seed_known_ids` to a no-op; this test
    fails because the first emission is then classified as ``created``.
    """
    factory = _factory_for([_page(_event("ev-alpha"))])
    connector = M365CalendarConnector(_config(), client_factory=factory, clock=_fixed_clock)
    connector.seed_known_ids({"ev-alpha"})

    events = list(connector.list_changes(cursor="resuming"))

    assert [(e.op, e.item_id) for e in events] == [("modified", "ev-alpha")]


# ---------------------------------------------------------------------------
# Cancelled + tombstoned events
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_cancelled_event_surfaces_as_deleted() -> None:
    """An event with ``isCancelled: true`` surfaces as a ``deleted`` ChangeEvent.

    Sabotage-proof: drop the cancelled-branch in
    :meth:`_record_to_change_event`; this test fails because the
    event is then classified as ``created`` / ``modified``.
    """
    factory = _factory_for([_page(_event("ev-alpha", cancelled=True))])
    connector = M365CalendarConnector(_config(), client_factory=factory, clock=_fixed_clock)

    events = list(connector.list_changes(cursor=None))

    assert [(e.op, e.item_id) for e in events] == [("deleted", "ev-alpha")]


@pytest.mark.unit
def test_removed_event_surfaces_as_deleted() -> None:
    """A tombstoned (@removed) event surfaces as a ``deleted`` ChangeEvent.

    Sabotage-proof: drop the removed-branch in
    :meth:`_record_to_change_event`; this test fails because the
    tombstone is then dropped silently.
    """
    factory = _factory_for([_page(_event("ev-alpha", removed=True))])
    connector = M365CalendarConnector(_config(), client_factory=factory, clock=_fixed_clock)

    events = list(connector.list_changes(cursor=None))

    assert [(e.op, e.item_id) for e in events] == [("deleted", "ev-alpha")]


# ---------------------------------------------------------------------------
# source_link, fetch, sensitivity_for
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_source_link_returns_outlook_deep_link() -> None:
    """``source_link`` returns ``https://outlook.office.com/calendar/item/<id>``.

    Sabotage-proof: replace the URL template with ``""``; this test
    fails on the substring assertions below.
    """
    factory = _factory_for([_page(_event("ev-alpha"))])
    connector = M365CalendarConnector(_config(), client_factory=factory, clock=_fixed_clock)

    link = connector.source_link("ev-alpha")

    assert link == "https://outlook.office.com/calendar/item/ev-alpha"


@pytest.mark.unit
def test_fetch_returns_cached_payload_after_list_changes() -> None:
    """``fetch`` returns the Graph payload cached during list_changes.

    Sabotage-proof: drop the payload-caching line in :meth:`_drain`;
    this test fails because ``fetch`` then raises with the
    'no cached payload' message.
    """
    factory = _factory_for([_page(_event("ev-alpha"))])
    connector = M365CalendarConnector(_config(), client_factory=factory, clock=_fixed_clock)

    list(connector.list_changes(cursor=None))
    artefact = connector.fetch("ev-alpha")

    assert isinstance(artefact, RawArtefact)
    assert artefact.mime == "application/json"
    assert b"ev-alpha" in artefact.raw


@pytest.mark.unit
def test_fetch_rejects_unseen_event_id() -> None:
    """``fetch`` raises when called for an id never seen by list_changes.

    Sabotage-proof: drop the cache-miss guard; this test fails because
    ``fetch`` silently returns an empty-payload artefact.
    """
    factory = _factory_for([_page(_event("ev-alpha"))])
    connector = M365CalendarConnector(_config(), client_factory=factory, clock=_fixed_clock)
    list(connector.list_changes(cursor=None))

    with pytest.raises(ValueError, match="no cached payload"):
        connector.fetch("ev-not-seen")


@pytest.mark.unit
def test_sensitivity_for_returns_configured_tier() -> None:
    """Constructor's ``sensitivity`` value applies to every item.

    Sabotage-proof: hard-code the return to ``"public"``; this test
    fails because the constructor configured ``"client-confidential"``.
    """
    config = M365CalendarConfig(
        user_id="operator@example.com",
        tenant_id="placeholder-tenant",
        client_id="placeholder-client",
        client_secret="placeholder-secret",  # pragma: allowlist secret
        sensitivity="client-confidential",
    )
    factory = _factory_for([_page(_event("ev-alpha"))])
    connector = M365CalendarConnector(config, client_factory=factory, clock=_fixed_clock)

    assert connector.sensitivity_for("ev-alpha") == "client-confidential"


# ---------------------------------------------------------------------------
# make_connector — config validation
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_make_connector_requires_full_credential_set() -> None:
    """``make_connector`` raises ValueError when required keys are missing.

    Sabotage-proof: remove the required-key check from
    ``make_connector``; this test fails because no exception is raised.
    """
    incomplete: dict[str, Any] = {
        "user_id": "operator@example.com",
        # tenant_id / client_id / client_secret all missing
    }

    with pytest.raises(ValueError, match="missing required key"):
        make_connector(incomplete)


@pytest.mark.unit
def test_make_connector_builds_with_required_keys() -> None:
    """``make_connector`` returns a connector when all required keys are present.

    Sabotage-proof: hard-code the factory to return ``None``; this test
    fails because the isinstance assertion below catches that.
    """
    config: dict[str, Any] = {
        "user_id": "operator@example.com",
        "tenant_id": "placeholder-tenant",
        "client_id": "placeholder-client",
        "client_secret": "placeholder-secret",  # pragma: allowlist secret
    }

    connector = make_connector(config)

    assert isinstance(connector, M365CalendarConnector)
    assert connector.name == "m365_calendar"
    assert connector.sensitivity_for("any-id") == "internal"


# ---------------------------------------------------------------------------
# ChangeEvent metadata fidelity
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_change_event_metadata_carries_subject_attendees_location() -> None:
    """The ChangeEvent metadata exposes the fields downstream consumers need.

    Sabotage-proof: drop one of the metadata keys; this test fails on
    the corresponding assertion below.
    """
    factory = _factory_for([_page(_event("ev-alpha", subject="Customer review"))])
    connector = M365CalendarConnector(_config(), client_factory=factory, clock=_fixed_clock)

    events = list(connector.list_changes(cursor=None))
    metadata: dict[str, Any] = dict(events[0].metadata)

    assert metadata["subject"] == "Customer review"
    assert metadata["start"] == "2026-05-25T09:00:00Z"
    assert metadata["end"] == "2026-05-25T10:00:00Z"
    assert metadata["location"] == "Conference room"
    assert metadata["attendees"] == ("alpha@example.com",)
    assert metadata["organiser"] == "organiser@example.com"


# ---------------------------------------------------------------------------
# typed ChangeEvent shape — defends against bare-dict regression
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_list_changes_emits_only_typed_change_events() -> None:
    """All emitted values are :class:`ChangeEvent` instances per F42.

    Sabotage-proof: have :meth:`_record_to_change_event` return a
    plain dict; this test fails because the isinstance check rejects
    the dict.
    """
    factory = _factory_for([_page(_event("ev-alpha"), _event("ev-bravo", cancelled=True))])
    connector = M365CalendarConnector(_config(), client_factory=factory, clock=_fixed_clock)

    events = list(connector.list_changes(cursor=None))

    for ev in events:
        assert isinstance(ev, ChangeEvent), f"non-ChangeEvent emitted: {ev!r}"


# ---------------------------------------------------------------------------
# Wave E production-default DI seams — coverage for the prod-only branches
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_make_connector_default_production_path_handles_single_calendar() -> None:
    """Drive ``make_connector`` (the public surface) with the canonical
    single-mailbox config and confirm the production-default Wave E
    surface emits exactly one Container per the operator's user_id.

    Sabotage-proof: change ``_configured_upns`` to return ``()`` for the
    single-mailbox case; the assertion below catches the regression.
    Tests the production-default factory paths through the public
    surface only (no internal-name imports per F5).
    """
    config: dict[str, Any] = {
        "user_id": "operator@example.com",
        "tenant_id": "placeholder-tenant",
        "client_id": "placeholder-client",
        "client_secret": "placeholder-secret",  # pragma: allowlist secret
    }
    connector = make_connector(config)
    # iter_containers is a public Wave E method — drives _configured_upns
    # through to the singleton-from-user_id fallback path.
    containers = list(connector.iter_containers(cc_pair_id=7))
    assert [c.container_id for c in containers] == ["operator@example.com"]
    # load_hierarchy is a public Wave E method — drives the production
    # hierarchy emission with the default flag-reader (resolves to False,
    # but the hierarchy emission is unflagged) and confirms the structural
    # shape (root + one calendar child).
    nodes = list(connector.load_hierarchy(cc_pair_id=7))
    assert len(nodes) == 2
    assert nodes[0].raw_node_id == "m365-calendar"
    assert nodes[0].raw_parent_id is None
    assert nodes[1].raw_node_id == "operator@example.com"
    assert nodes[1].raw_parent_id == "m365-calendar"


@pytest.mark.unit
def test_close_releases_every_per_upn_graph_client() -> None:
    """``close()`` releases both the legacy client and every per-UPN client.

    Drives the full lifecycle through public surface: construct the
    connector with Wave E flag ON, drain
    :meth:`list_changes_for_container` for two distinct UPNs (which
    causes the per-UPN factory to build two clients), call
    :meth:`close`, and assert the scripted clients all observed their
    ``close()`` call. Sabotage-proof: remove the ``for client in
    self._per_user_clients`` loop in :meth:`close`; the second-client
    assertion below catches the regression.
    """
    from kairix.core.protocols import Container

    closed: dict[str, bool] = {}

    class _ScriptedClient(M365GraphCalendarClient):
        def __init__(self, upn: str) -> None:
            self._user_id = upn
            self._http = None  # type: ignore[assignment]  # scripted client never makes HTTP calls
            self._page_size = 50
            self._upn = upn
            closed[upn] = False

        def fetch_initial_delta(self, _s: str, _e: str) -> CalendarDeltaPage:
            return CalendarDeltaPage(events=(), next_link=None, delta_link="dl")

        def fetch_delta_page(self, link: str) -> CalendarDeltaPage:
            return CalendarDeltaPage(events=(), next_link=None, delta_link=link)

        def close(self) -> None:
            closed[self._upn] = True

    config = M365CalendarConfig(
        user_id="alice@example.com",
        tenant_id="t",
        client_id="c",
        client_secret="s",  # pragma: allowlist secret
        user_ids=("alice@example.com", "bob@example.com"),
    )
    connector = M365CalendarConnector(
        config,
        per_user_client_factory=lambda _c, upn: _ScriptedClient(upn),
        flag_reader=lambda _name: True,
    )
    for upn in ("alice@example.com", "bob@example.com"):
        container = Container(
            cc_pair_id=1,
            container_id=upn,
            access_state="ACCESSIBLE",
            cursor_token=None,
            last_synced_at=None,
        )
        list(connector.list_changes_for_container(container))

    # Pre-close: every UPN's scripted client has fired __init__ but not
    # close() yet.
    assert closed == {"alice@example.com": False, "bob@example.com": False}
    connector.close()
    # Post-close: every UPN's scripted client received its close call.
    assert closed == {"alice@example.com": True, "bob@example.com": True}


@pytest.mark.unit
def test_make_connector_accepts_user_ids_for_multi_calendar() -> None:
    """``make_connector`` threads ``user_ids`` through to the config.

    Sabotage-proof: drop the ``user_ids=`` kwarg from the resolved
    config; this test fails because iter_containers then emits one
    Container instead of two.
    """
    config: dict[str, Any] = {
        "user_id": "alice@example.com",
        "tenant_id": "t",
        "client_id": "c",
        "client_secret": "s",  # pragma: allowlist secret
        "user_ids": ("alice@example.com", "bob@example.com"),
    }
    connector = make_connector(config)
    containers = list(connector.iter_containers(cc_pair_id=1))
    assert [c.container_id for c in containers] == ["alice@example.com", "bob@example.com"]

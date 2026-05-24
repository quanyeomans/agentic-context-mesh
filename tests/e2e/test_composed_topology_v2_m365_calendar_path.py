"""E2E composed path for the topology v2 Wave E m365_calendar slice — F48 sibling test.

ADR v2 §"Wave E" calls for the m365_calendar connector to:

  - emit one :class:`~kairix.core.protocols.Container` per configured
    calendar (per UPN) via :meth:`iter_containers`
  - scope :meth:`list_changes_for_container` to the container's UPN
    only, reading the container's own Graph ``@odata.deltaLink`` as
    cursor
  - emit a root FOLDER :class:`~kairix.core.protocols.HierarchyNode`
    plus one child per configured calendar, parent-before-child via
    :meth:`load_hierarchy`

This file is the F48 sibling test for the
``topology_v2_m365_calendar`` feature flag. It exercises every layer
of the Wave E composed path against the real
:class:`~kairix.connectors.m365_calendar.connector.M365CalendarConnector`
class, the real :func:`~kairix.core.factory.build_connector_pipeline`
factory, the real ``topology_*`` schema rows, the real
:func:`~kairix.core.connectors.cc_pair.create_cc_pair` lifecycle, and
the real ``topology_hierarchy_nodes`` round-trip.

Per F48 + F47: lives under ``tests/e2e/`` with ``@pytest.mark.e2e``;
runs in CI Stage 4.5 under ``pytest -m e2e``; exercises config →
factory → ingest → query → assertion via the composed production
code paths.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

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
from kairix.core.connectors.cc_pair import create_cc_pair, transition_cc_pair
from kairix.core.db.schema import create_schema
from kairix.core.factory import build_connector_pipeline
from kairix.core.protocols import Container, HierarchyNode
from tests.fakes import FakeFeatureFlagResolver

pytestmark = pytest.mark.e2e

_FLAG_NAME = "topology_v2_m365_calendar"
_UPN_ALICE = "alice@example.com"
_UPN_BOB = "bob@example.com"


# ---------------------------------------------------------------------------
# Scripted Graph client — records per-UPN cursor reads
# ---------------------------------------------------------------------------


class _ScriptedGraphClient(M365GraphCalendarClient):
    """In-memory Graph client used by the composed-path E2E."""

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


def _bootstrap_e2e_db(tmp_path: Path) -> tuple[sqlite3.Connection, int]:
    """Create the production schema + seed the m365_calendar cc_pair triad."""
    db_path = tmp_path / "kairix.sqlite"
    db = sqlite3.connect(str(db_path))
    create_schema(db, dims=4)
    now = "2026-05-24T00:00:00Z"
    cur = db.execute(
        "INSERT INTO topology_connectors "
        "(kind, name, connector_specific_config, default_sensitivity, created_at, updated_at) "
        "VALUES ('m365_calendar', 'm365-calendar-shared', '{}', 'internal', ?, ?)",
        (now, now),
    )
    connector_id = cur.lastrowid
    assert connector_id is not None
    cc_pair = create_cc_pair(
        db,
        connector_id=int(connector_id),
        credential_id=None,
        name="m365-calendar-shared",
    )
    db.commit()
    return db, cc_pair.id


def _build_connector_on(log: list[tuple[str, str | None]]) -> M365CalendarConnector:
    """Construct the production connector with the Wave E flag pinned ON."""
    resolver = FakeFeatureFlagResolver().with_flag(_FLAG_NAME, True)
    config = M365CalendarConfig(
        user_id=_UPN_ALICE,
        tenant_id="tenant-placeholder",
        client_id="client-placeholder",
        client_secret="secret-placeholder",  # pragma: allowlist secret
        user_ids=(_UPN_ALICE, _UPN_BOB),
    )
    return M365CalendarConnector(
        config,
        client_factory=lambda _c: _ScriptedGraphClient(_UPN_ALICE, log=log),
        per_user_client_factory=lambda _c, upn: _ScriptedGraphClient(upn, log=log),
        flag_reader=resolver.get,
    )


def _persist_hierarchy_nodes(db: sqlite3.Connection, *, cc_pair_id: int, nodes: list[HierarchyNode]) -> None:
    """INSERT every emitted node into the topology_hierarchy_nodes table IN ORDER."""
    for node in nodes:
        db.execute(
            "INSERT INTO topology_hierarchy_nodes "
            "(cc_pair_id, raw_node_id, raw_parent_id, display_name, "
            "link, node_type, external_access_json, sensitivity_hint) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                node.cc_pair_id,
                node.raw_node_id,
                node.raw_parent_id,
                node.display_name,
                node.link,
                node.node_type,
                node.external_access_json,
                node.sensitivity_hint,
            ),
        )
    db.commit()


# ---------------------------------------------------------------------------
# Composed-path signals
# ---------------------------------------------------------------------------


def test_composed_topology_v2_m365_calendar_path_iter_containers_lands_one_per_upn(tmp_path: Path) -> None:
    """Composed: real connector + real flag-reader → one Container per UPN."""
    log: list[tuple[str, str | None]] = []
    connector = _build_connector_on(log)
    db, cc_pair_id = _bootstrap_e2e_db(tmp_path)
    transition_cc_pair(db, cc_pair_id, "INITIAL_INDEXING")
    containers = list(connector.iter_containers(cc_pair_id=cc_pair_id))
    db.commit()
    ids = [c.container_id for c in containers]
    assert ids == [_UPN_ALICE, _UPN_BOB], f"Wave E slice: expected one Container per configured UPN, got {ids!r}"
    for c in containers:
        assert c.cc_pair_id == cc_pair_id
        assert c.access_state == "ACCESSIBLE"
        assert c.cursor_token is None


def test_composed_topology_v2_m365_calendar_path_hierarchy_round_trip_preserves_order(tmp_path: Path) -> None:
    """Composed: real emission → persist to topology_hierarchy_nodes → read back preserves parent-before-child."""
    log: list[tuple[str, str | None]] = []
    connector = _build_connector_on(log)
    db, cc_pair_id = _bootstrap_e2e_db(tmp_path)
    nodes = list(connector.load_hierarchy(cc_pair_id=cc_pair_id))
    assert nodes, "Wave E slice: load_hierarchy must emit at least the root + one calendar child"
    _persist_hierarchy_nodes(db, cc_pair_id=cc_pair_id, nodes=nodes)
    rows = db.execute(
        "SELECT raw_node_id, raw_parent_id FROM topology_hierarchy_nodes WHERE cc_pair_id = ? ORDER BY rowid",
        (cc_pair_id,),
    ).fetchall()
    seen: set[str] = set()
    for raw_id, raw_parent_id in rows:
        if raw_parent_id is not None:
            assert raw_parent_id in seen, (
                f"hierarchy round-trip violates parent-before-child: {raw_id!r} ↛ {raw_parent_id!r}"
            )
        seen.add(raw_id)
    raw_ids = {row[0] for row in rows}
    assert "m365-calendar" in raw_ids
    assert _UPN_ALICE in raw_ids
    assert _UPN_BOB in raw_ids


def test_composed_topology_v2_m365_calendar_path_list_changes_scopes_to_container(tmp_path: Path) -> None:
    """Composed: real connector + real Container → Graph delta hits only the container's UPN."""
    log: list[tuple[str, str | None]] = []
    connector = _build_connector_on(log)
    db, cc_pair_id = _bootstrap_e2e_db(tmp_path)
    container = Container(
        cc_pair_id=cc_pair_id,
        container_id=_UPN_ALICE,
        access_state="ACCESSIBLE",
        cursor_token=None,
        last_synced_at=None,
    )
    events = list(connector.list_changes_for_container(container))
    db.commit()
    assert events, "Wave E slice: container-scoped delta must emit at least one ChangeEvent"
    # Each event must be tagged with the container's UPN (cross-calendar leak detection).
    for ev in events:
        assert ev.item_id == f"event-{_UPN_ALICE}", (
            f"composed path: per-container scoping must keep events under {_UPN_ALICE!r}; got {ev.item_id!r}"
        )
    # And the Graph client was hit only for Alice — Bob's calendar was not touched.
    upns_hit = {entry[0] for entry in log}
    assert upns_hit == {_UPN_ALICE}, f"composed path: only the container's UPN must hit Graph; got {upns_hit!r}"


def test_composed_topology_v2_m365_calendar_path_per_container_cursors_drive_distinct_graph_reads(
    tmp_path: Path,
) -> None:
    """Composed: Alice's cursor and Bob's cursor produce distinct Graph reads."""
    log: list[tuple[str, str | None]] = []
    connector = _build_connector_on(log)
    db, _cc_pair_id = _bootstrap_e2e_db(tmp_path)
    alice_cursor = "https://graph.microsoft.com/v1.0/users/alice/calendar/delta?$deltatoken=alice-prev"
    alice = Container(
        cc_pair_id=42,
        container_id=_UPN_ALICE,
        access_state="ACCESSIBLE",
        cursor_token=alice_cursor,
        last_synced_at=None,
    )
    bob = Container(
        cc_pair_id=42,
        container_id=_UPN_BOB,
        access_state="ACCESSIBLE",
        cursor_token=None,
        last_synced_at=None,
    )
    list(connector.list_changes_for_container(alice))
    list(connector.list_changes_for_container(bob))
    db.commit()
    # Alice resumes from her cursor; Bob runs initial-delta (cursor=None).
    assert (_UPN_ALICE, alice_cursor) in log
    assert (_UPN_BOB, None) in log
    # Cross-contamination check.
    for upn, link in log:
        if upn == _UPN_BOB:
            assert link is None or _UPN_BOB in (link or ""), (
                f"composed path: Bob's container read a non-Bob cursor: {link!r}"
            )


def test_composed_topology_v2_m365_calendar_path_factory_pipeline_builds(tmp_path: Path) -> None:
    """Composed: factory builds the production pipeline alongside the Wave E connector.

    F46 / F47 contract: BDD + integration tests reach the production
    composition surface via :func:`build_connector_pipeline`. This
    confirms the Wave E slice is compatible with the existing factory
    (no breaking change to the surrounding pipeline shape).
    """
    db, _cc_pair_id = _bootstrap_e2e_db(tmp_path)
    bronze_root = tmp_path / "bronze"
    bronze_root.mkdir()
    pipeline = build_connector_pipeline(db=db, bronze_root=bronze_root, collection="m365-calendar-shared")
    assert pipeline is not None


def test_composed_topology_v2_m365_calendar_path_flag_registered() -> None:
    """Composed: the flag is in the production registry at default-False / introduce."""
    from kairix.core.features.registry import REGISTRY

    assert _FLAG_NAME in REGISTRY
    entry = REGISTRY[_FLAG_NAME]
    assert entry.default is False
    assert entry.stage == "introduce"
    assert entry.related_spec is not None
    assert "connector-scope-topology" in entry.related_spec

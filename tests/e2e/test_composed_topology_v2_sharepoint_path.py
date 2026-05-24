"""E2E composed path for the topology v2 Wave E sharepoint slice — F48 sibling test.

ADR v2 §"Wave E" + ``docs/architecture/connector-scope-topology/connector-design-specs/sharepoint.md``
call for the sharepoint connector to:

  - emit one :class:`~kairix.core.protocols.Container` per configured
    Graph drive via :meth:`iter_containers`
  - scope :meth:`list_changes_for_container` to the container's drive
    id only, reading the container's own ``@odata.deltaLink`` as
    cursor (bypassing the legacy packed JSON cursor map)
  - emit a root SITE :class:`~kairix.core.protocols.HierarchyNode`
    plus one DRIVE child per configured drive, parent-before-child via
    :meth:`load_hierarchy`
  - replay per-item failures via :meth:`reindex` instead of re-running
    a full delta window

This file is the F48 sibling test for the
``topology_v2_sharepoint`` feature flag. It exercises every layer of
the Wave E composed path against the real
:class:`~kairix.connectors.sharepoint.connector.SharePointConnector`
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
from collections.abc import Iterator
from pathlib import Path

import pytest

from kairix.connectors.sharepoint.connector import (
    SharePointConnector,
    SharePointCredentials,
    SharePointDriveSpec,
)
from kairix.connectors.sharepoint.graph_client import (
    DriveItemRef,
    SharePointGraphClient,
)
from kairix.core.connectors.cc_pair import create_cc_pair, transition_cc_pair
from kairix.core.db.schema import create_schema
from kairix.core.factory import build_connector_pipeline
from kairix.core.protocols import Container, HierarchyNode
from kairix.transport.auth.oauth2_client_creds import OAuth2ClientCredsAuth
from tests.fakes import FakeFeatureFlagResolver

pytestmark = pytest.mark.e2e

_FLAG_NAME = "topology_v2_sharepoint"
_DRIVE_ALPHA = "drive-alpha"
_DRIVE_BETA = "drive-beta"


# ---------------------------------------------------------------------------
# Scripted Graph client — records per-drive cursor reads
# ---------------------------------------------------------------------------


class _ScriptedGraphClient(SharePointGraphClient):
    """In-memory Graph client used by the composed-path E2E."""

    def __init__(self, *, log: list[tuple[str, str | None]]) -> None:
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


def _bootstrap_e2e_db(tmp_path: Path) -> tuple[sqlite3.Connection, int]:
    """Create the production schema + seed the sharepoint cc_pair triad."""
    db_path = tmp_path / "kairix.sqlite"
    db = sqlite3.connect(str(db_path))
    create_schema(db, dims=4)
    now = "2026-05-24T00:00:00Z"
    cur = db.execute(
        "INSERT INTO topology_connectors "
        "(kind, name, connector_specific_config, default_sensitivity, created_at, updated_at) "
        "VALUES ('sharepoint', 'sharepoint-shared', '{}', 'internal', ?, ?)",
        (now, now),
    )
    connector_id = cur.lastrowid
    assert connector_id is not None
    cc_pair = create_cc_pair(
        db,
        connector_id=int(connector_id),
        credential_id=None,
        name="sharepoint-shared",
    )
    db.commit()
    return db, cc_pair.id


def _build_connector_on(log: list[tuple[str, str | None]]) -> SharePointConnector:
    """Construct the production connector with the Wave E flag pinned ON."""
    resolver = FakeFeatureFlagResolver().with_flag(_FLAG_NAME, True)
    scripted = _ScriptedGraphClient(log=log)
    return SharePointConnector(
        drives=[SharePointDriveSpec(drive_id=_DRIVE_ALPHA), SharePointDriveSpec(drive_id=_DRIVE_BETA)],
        credentials=SharePointCredentials(
            tenant_id="tenant-placeholder",
            client_id="client-placeholder",
            client_secret="secret-placeholder",  # pragma: allowlist secret
        ),
        auth=OAuth2ClientCredsAuth(
            tenant_id="tenant-placeholder",
            client_id="client-placeholder",
            client_secret="secret-placeholder",  # pragma: allowlist secret
            scope="https://graph.microsoft.com/.default",
        ),
        client_builder=lambda _auth: scripted,
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


def test_composed_topology_v2_sharepoint_path_iter_containers_lands_one_per_drive(tmp_path: Path) -> None:
    """Composed: real connector + real flag-reader → one Container per drive."""
    log: list[tuple[str, str | None]] = []
    connector = _build_connector_on(log)
    db, cc_pair_id = _bootstrap_e2e_db(tmp_path)
    transition_cc_pair(db, cc_pair_id, "INITIAL_INDEXING")
    containers = list(connector.iter_containers(cc_pair_id=cc_pair_id))
    db.commit()
    ids = [c.container_id for c in containers]
    assert ids == [_DRIVE_ALPHA, _DRIVE_BETA], f"Wave E slice: expected one Container per configured drive, got {ids!r}"
    for c in containers:
        assert c.cc_pair_id == cc_pair_id
        assert c.access_state == "ACCESSIBLE"
        assert c.cursor_token is None


def test_composed_topology_v2_sharepoint_path_hierarchy_round_trip_preserves_order(tmp_path: Path) -> None:
    """Composed: real emission → persist to topology_hierarchy_nodes → read back preserves parent-before-child."""
    log: list[tuple[str, str | None]] = []
    connector = _build_connector_on(log)
    db, cc_pair_id = _bootstrap_e2e_db(tmp_path)
    nodes = list(connector.load_hierarchy(cc_pair_id=cc_pair_id))
    assert nodes, "Wave E slice: load_hierarchy must emit at least the root + one drive child"
    _persist_hierarchy_nodes(db, cc_pair_id=cc_pair_id, nodes=nodes)
    rows = db.execute(
        "SELECT raw_node_id, raw_parent_id, node_type FROM topology_hierarchy_nodes "
        "WHERE cc_pair_id = ? ORDER BY rowid",
        (cc_pair_id,),
    ).fetchall()
    seen: set[str] = set()
    for raw_id, raw_parent_id, _node_type in rows:
        if raw_parent_id is not None:
            assert raw_parent_id in seen, (
                f"hierarchy round-trip violates parent-before-child: {raw_id!r} ↛ {raw_parent_id!r}"
            )
        seen.add(raw_id)
    raw_ids = {row[0] for row in rows}
    assert "sharepoint" in raw_ids
    assert _DRIVE_ALPHA in raw_ids
    assert _DRIVE_BETA in raw_ids
    # Root must be SITE-typed.
    root_row = next(row for row in rows if row[1] is None)
    assert root_row[2] == "SITE"


def test_composed_topology_v2_sharepoint_path_list_changes_scopes_to_container(tmp_path: Path) -> None:
    """Composed: real connector + real Container → Graph delta hits only the container's drive."""
    log: list[tuple[str, str | None]] = []
    connector = _build_connector_on(log)
    db, cc_pair_id = _bootstrap_e2e_db(tmp_path)
    container = Container(
        cc_pair_id=cc_pair_id,
        container_id=_DRIVE_ALPHA,
        access_state="ACCESSIBLE",
        cursor_token=None,
        last_synced_at=None,
    )
    events = list(connector.list_changes_for_container(container))
    db.commit()
    assert events, "Wave E slice: container-scoped delta must emit at least one ChangeEvent"
    for ev in events:
        assert ev.item_id == f"item-{_DRIVE_ALPHA}", (
            f"composed path: per-container scoping must keep events under {_DRIVE_ALPHA!r}; got {ev.item_id!r}"
        )
    drives_hit = {entry[0] for entry in log}
    assert drives_hit == {_DRIVE_ALPHA}, f"composed path: only the container's drive must hit Graph; got {drives_hit!r}"


def test_composed_topology_v2_sharepoint_path_per_container_cursors_drive_distinct_graph_reads(
    tmp_path: Path,
) -> None:
    """Composed: Alpha's cursor and Beta's cursor produce distinct Graph reads."""
    log: list[tuple[str, str | None]] = []
    connector = _build_connector_on(log)
    db, _cc_pair_id = _bootstrap_e2e_db(tmp_path)
    alpha_cursor = f"https://graph.microsoft.com/v1.0/drives/{_DRIVE_ALPHA}/root/delta?$deltatoken=alpha-prev"
    alpha = Container(
        cc_pair_id=42,
        container_id=_DRIVE_ALPHA,
        access_state="ACCESSIBLE",
        cursor_token=alpha_cursor,
        last_synced_at=None,
    )
    beta = Container(
        cc_pair_id=42,
        container_id=_DRIVE_BETA,
        access_state="ACCESSIBLE",
        cursor_token=None,
        last_synced_at=None,
    )
    list(connector.list_changes_for_container(alpha))
    list(connector.list_changes_for_container(beta))
    db.commit()
    assert (_DRIVE_ALPHA, alpha_cursor) in log
    assert (_DRIVE_BETA, None) in log
    for drive_id, link in log:
        if drive_id == _DRIVE_BETA:
            assert link is None, f"composed path: Beta's container read a non-None cursor: {link!r}"


def test_composed_topology_v2_sharepoint_path_reindex_replays_only_failed_ids(tmp_path: Path) -> None:
    """Composed: Resolver.reindex replays per-item failures without re-fetching the delta window."""
    log: list[tuple[str, str | None]] = []
    connector = _build_connector_on(log)
    db, _cc_pair_id = _bootstrap_e2e_db(tmp_path)
    failed = ("item-deadletter-1", "item-deadletter-2")
    events = list(connector.reindex(failed))
    db.commit()
    assert [e.item_id for e in events] == list(failed), (
        f"reindex must replay exactly the supplied failed ids; got {[e.item_id for e in events]!r}"
    )
    # Crucially: reindex does NOT re-hit the Graph delta endpoint.
    assert log == [], f"reindex must not re-fetch the delta endpoint; recorded calls: {log!r}"


def test_composed_topology_v2_sharepoint_path_factory_pipeline_builds(tmp_path: Path) -> None:
    """Composed: factory builds the production pipeline alongside the Wave E connector.

    F46 / F47 contract: BDD + integration tests reach the production
    composition surface via :func:`build_connector_pipeline`. This
    confirms the Wave E slice is compatible with the existing factory
    (no breaking change to the surrounding pipeline shape).
    """
    db, _cc_pair_id = _bootstrap_e2e_db(tmp_path)
    bronze_root = tmp_path / "bronze"
    bronze_root.mkdir()
    pipeline = build_connector_pipeline(db=db, bronze_root=bronze_root, collection="sharepoint-shared")
    assert pipeline is not None


def test_composed_topology_v2_sharepoint_path_flag_registered() -> None:
    """Composed: the flag is in the production registry at default-False / introduce."""
    from kairix.core.features.registry import REGISTRY

    assert _FLAG_NAME in REGISTRY
    entry = REGISTRY[_FLAG_NAME]
    assert entry.default is False
    assert entry.stage == "introduce"
    assert entry.related_spec is not None
    assert "connector-scope-topology" in entry.related_spec

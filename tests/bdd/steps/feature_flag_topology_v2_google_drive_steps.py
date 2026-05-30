"""Step definitions for feature_flag_topology_v2_google_drive.feature.

Wave E of the connector / collection / scope topology v2 migration is
the per-connector slice for the google_drive connector. When the
``topology_v2_google_drive`` flag is ON, the connector emits one
:class:`~kairix.core.protocols.Container` per configured corpus (each
with its own newStartPageToken as cursor) and
:meth:`list_changes_for_container` scopes the Drive changes drain to
that corpus ONLY. When OFF, the connector retains the Wave B shim
shape (delegates to legacy :meth:`list_changes`).

This step file exercises both branches of the flag through the
canonical :class:`tests.fakes.FakeFeatureFlagResolver` — pinning the
flag value through the connector's ``flag_reader`` DI seam without
monkey-patching the resolver module (F1-clean / F2-clean).

F46: each step reaches the production composition surface via the
real :class:`kairix.connectors.google_drive.GoogleDriveConnector`
class, never a Pipeline-class direct construction. The Drive client
is substituted via the ``client_builder`` DI seam with a scripted
in-memory recorder so no real HTTP fires.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field

import pytest
from pytest_bdd import given, parsers, then, when

from kairix.connectors.google_drive.client import DriveFileRef, GoogleDriveClient
from kairix.connectors.google_drive.connector import (
    GoogleDriveConnector,
    GoogleDriveCorpusSpec,
    GoogleDriveCredentials,
)
from kairix.core.protocols import ChangeEvent, Container, HierarchyNode
from tests.fakes import FakeFeatureFlagResolver

pytestmark = pytest.mark.bdd

_FLAG_NAME = "topology_v2_google_drive"


class _ScriptedDriveClient(GoogleDriveClient):
    """In-memory Drive client used by both branches of the BDD scenarios."""

    def __init__(self, *, log: list[tuple[str, str | None]]) -> None:
        # Bypass parent constructor — scripted client owns no real HTTP.
        self._access_token = "scripted"  # pragma: allowlist secret — scripted test fake never hits the wire
        self._drive_base = "https://www.googleapis.com/drive/v3"
        self._http_client = None  # type: ignore[assignment]  # scripted client owns no HTTP resources
        self._sleep_fn = lambda _s: None
        self._max_attempts = 1
        self._last_new_start_page_token: str | None = None
        self._log = log

    def get_start_page_token(self) -> str:
        return "scripted-seed"

    def iter_changes(self, start_token: str) -> Iterator[DriveFileRef]:
        self._log.append(("scripted", start_token))
        self._last_new_start_page_token = "scripted-fresh"
        yield DriveFileRef(
            file_id="item-scripted",
            name="file-scripted.pdf",
            mime_type="application/pdf",
            web_view_link="https://drive.google.com/file/d/item-scripted/view",
            modified_time="2026-05-22T10:00:00Z",
            created_time=None,
            last_modifying_user_email=None,
            last_modifying_user_name=None,
            owner_emails=(),
            removed=False,
            parents=(),
            size=42,
        )


@dataclass
class _TopologyV2GoogleDriveCtx:
    """Per-scenario context. No module-level mutable state."""

    resolver: FakeFeatureFlagResolver | None = None
    flag_value: bool | None = None
    corpus_ids: tuple[str, ...] = ()
    connector: GoogleDriveConnector | None = None
    log: list[tuple[str, str | None]] = field(default_factory=list)
    containers: list[Container] = field(default_factory=list)
    hierarchy_nodes: list[HierarchyNode] = field(default_factory=list)
    scoped_events: list[ChangeEvent] = field(default_factory=list)
    legacy_path_observed: bool = False
    reindex_events: list[ChangeEvent] = field(default_factory=list)
    reindex_failed_ids: tuple[str, ...] = ()


@pytest.fixture
def topology_v2_google_drive_ctx() -> _TopologyV2GoogleDriveCtx:
    return _TopologyV2GoogleDriveCtx()


# ---------------------------------------------------------------------------
# Givens
# ---------------------------------------------------------------------------


@given(parsers.parse("a google drive connector configured for two corpora: {a}, {b}"))
def _two_corpora_config(
    topology_v2_google_drive_ctx: _TopologyV2GoogleDriveCtx,
    a: str,
    b: str,
) -> None:
    """Stash the configured corpus ids; the connector is built once the flag is set."""
    topology_v2_google_drive_ctx.corpus_ids = (a, b)


@given(parsers.parse("the operator has the topology-v2-google-drive flag set to {value}"))
def _operator_sets_flag(
    topology_v2_google_drive_ctx: _TopologyV2GoogleDriveCtx,
    value: str,
) -> None:
    """Pin the flag value via :class:`FakeFeatureFlagResolver` and
    construct the real :class:`GoogleDriveConnector` against the
    configured corpora, threading the flag value through the
    connector's ``flag_reader`` DI seam.
    """
    parsed = value.strip().lower() == "true"
    resolver = FakeFeatureFlagResolver().with_flag(_FLAG_NAME, parsed)
    topology_v2_google_drive_ctx.resolver = resolver
    topology_v2_google_drive_ctx.flag_value = parsed
    corpora = topology_v2_google_drive_ctx.corpus_ids
    assert corpora, "the configured corpus ids must have been declared before flag setup"
    log = topology_v2_google_drive_ctx.log
    scripted = _ScriptedDriveClient(log=log)
    topology_v2_google_drive_ctx.connector = GoogleDriveConnector(
        corpora=[GoogleDriveCorpusSpec(corpus_id=c) for c in corpora],
        credentials=GoogleDriveCredentials(
            access_token="placeholder-token",  # pragma: allowlist secret — test fixture
        ),
        client_builder=lambda _creds: scripted,
        flag_reader=resolver.get,
    )


# ---------------------------------------------------------------------------
# Whens
# ---------------------------------------------------------------------------


@when("the operator calls iter_containers on the google drive connector")
def _call_iter_containers(topology_v2_google_drive_ctx: _TopologyV2GoogleDriveCtx) -> None:
    assert topology_v2_google_drive_ctx.connector is not None
    topology_v2_google_drive_ctx.containers = list(
        topology_v2_google_drive_ctx.connector.iter_containers(cc_pair_id=42)
    )


@when("the operator calls load_hierarchy on the google drive connector")
def _call_load_hierarchy(topology_v2_google_drive_ctx: _TopologyV2GoogleDriveCtx) -> None:
    assert topology_v2_google_drive_ctx.connector is not None
    topology_v2_google_drive_ctx.hierarchy_nodes = list(
        topology_v2_google_drive_ctx.connector.load_hierarchy(cc_pair_id=42)
    )


@when(parsers.parse("the operator drives list_changes_for_container against google drive corpus {corpus_id}"))
def _call_list_changes_for_container(
    topology_v2_google_drive_ctx: _TopologyV2GoogleDriveCtx,
    corpus_id: str,
) -> None:
    """Construct a Container scoped to the named corpus and drive the
    per-container path. The OFF branch delegates to legacy
    :meth:`list_changes`; the ON branch routes to the per-corpus
    scoped path.
    """
    connector = topology_v2_google_drive_ctx.connector
    assert connector is not None
    container = Container(
        cc_pair_id=42,
        container_id=corpus_id,
        access_state="ACCESSIBLE",
        cursor_token=None,
        last_synced_at=None,
    )
    if topology_v2_google_drive_ctx.flag_value is False:
        topology_v2_google_drive_ctx.legacy_path_observed = True
    events = list(connector.list_changes_for_container(container))
    topology_v2_google_drive_ctx.scoped_events = events


@when(parsers.parse("the operator calls reindex on the google drive connector with failed ids {a} and {b}"))
def _call_reindex(
    topology_v2_google_drive_ctx: _TopologyV2GoogleDriveCtx,
    a: str,
    b: str,
) -> None:
    connector = topology_v2_google_drive_ctx.connector
    assert connector is not None
    failed = (a, b)
    topology_v2_google_drive_ctx.reindex_failed_ids = failed
    topology_v2_google_drive_ctx.reindex_events = list(connector.reindex(failed))


# ---------------------------------------------------------------------------
# Thens
# ---------------------------------------------------------------------------


@then("two google drive Containers are emitted, one per configured corpus")
def _two_containers(topology_v2_google_drive_ctx: _TopologyV2GoogleDriveCtx) -> None:
    containers = topology_v2_google_drive_ctx.containers
    assert len(containers) == 2, f"expected two Containers (one per configured corpus), got {len(containers)}"
    ids = [c.container_id for c in containers]
    assert ids == list(topology_v2_google_drive_ctx.corpus_ids), (
        f"containers must follow the configured corpus order, got {ids}"
    )


@then("every google drive Container carries access_state ACCESSIBLE with no cursor_token yet")
def _container_shape(topology_v2_google_drive_ctx: _TopologyV2GoogleDriveCtx) -> None:
    for container in topology_v2_google_drive_ctx.containers:
        assert container.access_state == "ACCESSIBLE"
        assert container.cursor_token is None
        assert container.last_synced_at is None
        assert container.cc_pair_id == 42


@then("FOLDER nodes are emitted parent-before-child with a root and one FOLDER child per corpus for google drive")
def _hierarchy_parent_before_child(topology_v2_google_drive_ctx: _TopologyV2GoogleDriveCtx) -> None:
    nodes = topology_v2_google_drive_ctx.hierarchy_nodes
    assert len(nodes) == 3, f"expected root + 2 corpus children, got {len(nodes)}"
    assert nodes[0].raw_parent_id is None
    assert nodes[0].node_type == "FOLDER"
    seen: set[str] = set()
    for node in nodes:
        if node.raw_parent_id is not None:
            assert node.raw_parent_id in seen, (
                f"orphan emission: node {node.raw_node_id!r} references unseen parent {node.raw_parent_id!r}"
            )
        seen.add(node.raw_node_id)


@then("the legacy single-cursor list_changes branch is observed for google drive")
def _legacy_branch_observed(topology_v2_google_drive_ctx: _TopologyV2GoogleDriveCtx) -> None:
    assert topology_v2_google_drive_ctx.legacy_path_observed is True, (
        "expected the OFF branch to take the legacy single-cursor delegation path"
    )
    connector = topology_v2_google_drive_ctx.connector
    assert connector is not None
    # OFF: legacy list_changes populates _next_cursor; the ON per-container
    # path leaves _next_cursor alone.
    assert connector._next_cursor is not None, "OFF branch must populate the legacy cursor via list_changes"


@then("one root FOLDER node is emitted with no corpus children for google drive")
def _root_folder_only(topology_v2_google_drive_ctx: _TopologyV2GoogleDriveCtx) -> None:
    nodes = topology_v2_google_drive_ctx.hierarchy_nodes
    assert len(nodes) == 1, f"OFF branch must emit exactly one root FOLDER node, got {len(nodes)}"
    assert nodes[0].raw_parent_id is None
    assert nodes[0].node_type == "FOLDER"


@then("the google drive reindex emits exactly one event per supplied failed id")
def _reindex_replay_scope(topology_v2_google_drive_ctx: _TopologyV2GoogleDriveCtx) -> None:
    failed = topology_v2_google_drive_ctx.reindex_failed_ids
    events = topology_v2_google_drive_ctx.reindex_events
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

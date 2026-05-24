"""F54 integration coverage for the ``topology_v2_sharepoint`` feature flag.

Wave E of the connector / collection / scope topology v2 migration is
the per-connector slice for the sharepoint connector. When the
``topology_v2_sharepoint`` flag is ON, the connector emits one
:class:`~kairix.core.protocols.Container` per configured Graph drive
(each with its own ``@odata.deltaLink`` as cursor) and
:meth:`list_changes_for_container` scopes the Graph delta query to
that drive's id ONLY. When OFF, the connector retains the Wave B
shim shape — :meth:`list_changes_for_container` delegates to the
legacy single-cursor :meth:`list_changes` call that uses one shared
packed JSON cursor map across every configured drive.

Per F54 (docs/architecture/feature-flag-architecture.md §5): every flag
needs integration coverage exercising both branches via
:class:`tests.fakes.FakeFeatureFlagResolver`. The string literal
``"topology_v2_sharepoint"`` appears verbatim in every
``with_flag(...)`` call so the F54 check picks it up.

F47 — the multi-component pipeline (connector + scripted Graph client)
is constructed via real plugin construction with the
:class:`~kairix.connectors.sharepoint.connector.SharePointConnector`
class itself; the flag is injected through the connector's
``flag_reader`` DI seam and the Graph client is injected through the
``client_builder`` seam. No monkey-patching of the resolver module, no
real HTTP traffic.

Sabotage proofs (executed by the agent, mutate → confirm fail → restore):

  1. **Per-drive cursor isolation** — in
     :meth:`SharePointConnector._list_changes_for_container_scoped`,
     mutated ``start_url = container.cursor_token`` to a shared
     module-level constant; confirmed
     ``test_flag_on_per_container_cursors_are_isolated`` fails because
     both drives then fetch the same cursor instead of their own;
     restored.
  2. **F58 parent-before-child** — in
     :meth:`SharePointConnector.load_hierarchy`, swapped the yield
     order so the per-drive children emit before the root; confirmed
     ``tests/contracts/test_hierarchy_parent_before_child.py::test_sharepoint_hierarchy_parent_before_child``
     fails with an orphan-emission assertion; restored.
  3. **ContainerAccessDenied semantics** — in
     :meth:`SharePointConnector._list_changes_for_container_scoped`,
     mutated the per-drive ``iter_drive_items`` call to fail the whole
     tick when any single drive's enumeration raises; confirmed
     ``test_flag_on_single_drive_403_does_not_poison_other_drives``
     fails because the other drive's events are not emitted; restored.
  4. **Resolver.reindex replay scope** — in
     :meth:`SharePointConnector.reindex`, broke the "replay only failed
     ids" filter to iterate :attr:`self._drives` instead of the
     supplied failed_item_ids; confirmed
     ``test_flag_on_reindex_replays_only_failed_ids`` fails because
     the emitted event ids no longer match the supplied tuple;
     restored.
  5. **Flag-OFF inertness** — replaced the
     ``if not self._flag_reader(...)`` guard in
     :meth:`SharePointConnector.list_changes_for_container` with
     ``if False:`` so the ON branch runs even when the flag is OFF;
     confirmed
     ``test_flag_off_list_changes_for_container_uses_legacy_path``
     fails because the legacy packed JSON cursor is not populated;
     restored.
"""

from __future__ import annotations

from collections.abc import Iterator

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
from kairix.core.protocols import (
    Container,
    HierarchyConnector,
    HierarchyNode,
    PollConnector,
    Resolver,
    SlimConnector,
)
from kairix.transport.auth.oauth2_client_creds import OAuth2ClientCredsAuth
from tests.fakes import FakeFeatureFlagResolver

pytestmark = pytest.mark.integration

_FLAG_NAME = "topology_v2_sharepoint"
_DRIVE_ALPHA = "drive-alpha"
_DRIVE_BETA = "drive-beta"


# ---------------------------------------------------------------------------
# Scripted Graph client — records per-drive cursor reads, supports failures
# ---------------------------------------------------------------------------


class _RecordingGraphClient(SharePointGraphClient):
    """In-memory Graph client used by both branches.

    Records every ``(drive_id, start_url)`` tuple seen via
    :meth:`iter_drive_items` so tests can assert per-drive isolation.
    Optionally raises a configured exception for a named drive — used
    by the access-denied / ContainerAccessDenied semantics test.
    """

    def __init__(
        self,
        *,
        log: list[tuple[str, str | None]],
        raise_for_drive: dict[str, Exception] | None = None,
    ) -> None:
        # Bypass the parent constructor — we own no real HTTP resources.
        self._log = log
        self._raise_for_drive = raise_for_drive or {}
        self._last_delta_link_by_drive: dict[str, str] = {}
        self._http_client = None  # type: ignore[assignment]  # scripted client owns no HTTP resources; bypass httpx.Client construction
        self._auth = None  # type: ignore[assignment]  # scripted client never exercises auth; OAuth2 helper is never invoked
        self._graph_base = "https://graph.microsoft.com/v1.0"

    def iter_drive_items(self, drive_id: str, start_url: str | None = None) -> Iterator[DriveItemRef]:
        self._log.append((drive_id, start_url))
        if drive_id in self._raise_for_drive:
            raise self._raise_for_drive[drive_id]
        # Each yielded id is tagged with the drive id so a cross-drive
        # leak in the connector's cursor handling is observable in the
        # event stream.
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


def _build_connector(
    *,
    flag_on: bool,
    log: list[tuple[str, str | None]],
    drives: tuple[str, ...] = (_DRIVE_ALPHA, _DRIVE_BETA),
    raise_for_drive: dict[str, Exception] | None = None,
) -> SharePointConnector:
    """Construct the production connector with both DI seams pinned.

    F54 — verbatim literal so the both-branch grep picks up the flag
    name. Each branch keeps its own ``with_flag(...)`` call so the OFF
    + ON pattern is mechanically observable.
    """
    if flag_on:
        resolver = FakeFeatureFlagResolver().with_flag("topology_v2_sharepoint", True)
    else:
        resolver = FakeFeatureFlagResolver().with_flag("topology_v2_sharepoint", False)

    scripted = _RecordingGraphClient(log=log, raise_for_drive=raise_for_drive)
    return SharePointConnector(
        drives=[SharePointDriveSpec(drive_id=d) for d in drives],
        credentials=SharePointCredentials(
            tenant_id="tenant-placeholder",
            client_id="client-placeholder",
            client_secret="secret-placeholder",  # pragma: allowlist secret — test fixture
        ),
        auth=OAuth2ClientCredsAuth(
            tenant_id="tenant-placeholder",
            client_id="client-placeholder",
            client_secret="secret-placeholder",  # pragma: allowlist secret — test fixture
            scope="https://graph.microsoft.com/.default",
        ),
        client_builder=lambda _auth: scripted,
        flag_reader=resolver.get,
    )


# ---------------------------------------------------------------------------
# Flag registration + protocol satisfaction (cheap correctness pins)
# ---------------------------------------------------------------------------


def test_topology_v2_sharepoint_flag_registered() -> None:
    """The flag exists in the registry, defaults False, stage=introduce."""
    from kairix.core.features.registry import REGISTRY

    assert "topology_v2_sharepoint" in REGISTRY
    entry = REGISTRY["topology_v2_sharepoint"]
    assert entry.default is False
    assert entry.stage == "introduce"
    assert entry.owner == "connector-framework"
    # Uses the canonical _TOPOLOGY_V2_TARGET_RETIRE_IN constant (currently
    # v2027.5.23, ~12 months from Wave A landing 2026-05-21).
    assert entry.target_retire_in.startswith("v2027.5.2")


def test_sharepoint_connector_satisfies_wave_e_protocols_under_both_branches() -> None:
    """Both branches keep the connector satisfying the Wave E capability Protocols."""
    log: list[tuple[str, str | None]] = []
    off = _build_connector(flag_on=False, log=log)
    on = _build_connector(flag_on=True, log=log)
    for connector in (off, on):
        assert isinstance(connector, PollConnector)
        assert isinstance(connector, HierarchyConnector)
        assert isinstance(connector, SlimConnector)
        assert isinstance(connector, Resolver)


# ---------------------------------------------------------------------------
# OFF branch — Wave B shim behaviour
# ---------------------------------------------------------------------------


def test_flag_off_iter_containers_still_yields_one_per_drive() -> None:
    """OFF: iter_containers still yields one Container per configured drive.

    iter_containers itself isn't flag-gated — the value-add is gating
    list_changes_for_container's per-cursor isolation. Confirming the
    OFF branch still emits a Container per configured drive means the
    framework can plan routing identically; what differs is the
    cursor-read path.
    """
    log: list[tuple[str, str | None]] = []
    connector = _build_connector(flag_on=False, log=log)
    containers = list(connector.iter_containers(cc_pair_id=7))
    assert [c.container_id for c in containers] == [_DRIVE_ALPHA, _DRIVE_BETA]


def test_flag_off_list_changes_for_container_uses_legacy_path() -> None:
    """OFF: list_changes_for_container delegates to legacy single-cursor list_changes.

    Sabotage proof for #5: replacing the ``if not self._flag_reader(...)``
    guard with ``if False:`` makes this test fail because the legacy
    packed JSON cursor map (``_next_cursor``) is no longer populated by
    the per-container call.
    """
    log: list[tuple[str, str | None]] = []
    connector = _build_connector(flag_on=False, log=log)
    container = Container(
        cc_pair_id=7,
        container_id=_DRIVE_ALPHA,
        access_state="ACCESSIBLE",
        cursor_token=None,
        last_synced_at=None,
    )
    events = list(connector.list_changes_for_container(container))
    assert events, "OFF: legacy single-cursor list_changes must still surface events"
    # Legacy path populates the packed JSON cursor map on the connector;
    # this is the load-bearing observable that distinguishes OFF from ON.
    assert connector._next_cursor is not None, (
        "OFF: legacy single-cursor list_changes must populate the packed JSON cursor map"
    )


def test_flag_off_load_hierarchy_emits_root_only() -> None:
    """OFF: load_hierarchy emits exactly one root FOLDER node (Wave B shim shape)."""
    log: list[tuple[str, str | None]] = []
    connector = _build_connector(flag_on=False, log=log)
    nodes = list(connector.load_hierarchy(cc_pair_id=7))
    assert len(nodes) == 1, f"OFF: expected one root FOLDER node, got {len(nodes)}"
    assert nodes[0].raw_parent_id is None
    assert nodes[0].node_type == "FOLDER"
    assert nodes[0].raw_node_id == "sharepoint"


# ---------------------------------------------------------------------------
# ON branch — Wave E real implementations
# ---------------------------------------------------------------------------


def test_flag_on_iter_containers_yields_one_per_configured_drive() -> None:
    """ON: iter_containers yields one Container per configured drive."""
    log: list[tuple[str, str | None]] = []
    connector = _build_connector(flag_on=True, log=log)
    containers = list(connector.iter_containers(cc_pair_id=7))
    ids = [c.container_id for c in containers]
    assert ids == [_DRIVE_ALPHA, _DRIVE_BETA], (
        f"ON: expected one Container per configured drive in declared order; got {ids!r}"
    )
    for c in containers:
        assert c.cc_pair_id == 7
        assert c.access_state == "ACCESSIBLE"
        assert c.cursor_token is None
        assert c.last_synced_at is None


def test_flag_on_load_hierarchy_parent_before_child() -> None:
    """ON: load_hierarchy emits parent-before-child per F58.

    Sabotage proof for #2: swapping the yield order so per-drive
    children emit before the root makes this test fail (orphan
    emission).
    """
    log: list[tuple[str, str | None]] = []
    connector = _build_connector(flag_on=True, log=log)
    nodes = list(connector.load_hierarchy(cc_pair_id=7))
    assert len(nodes) == 3, f"ON: expected SITE root + 2 DRIVE children, got {len(nodes)}"
    seen: set[str] = set()
    for node in nodes:
        assert isinstance(node, HierarchyNode)
        if node.raw_parent_id is not None:
            assert node.raw_parent_id in seen, (
                f"orphan emission: {node.raw_node_id!r} references unseen parent {node.raw_parent_id!r}"
            )
        seen.add(node.raw_node_id)
    assert nodes[0].raw_parent_id is None
    assert nodes[0].node_type == "SITE"
    child_ids = {n.raw_node_id for n in nodes[1:]}
    assert child_ids == {_DRIVE_ALPHA, _DRIVE_BETA}


def test_flag_on_list_changes_scopes_to_containers_drive() -> None:
    """ON: list_changes_for_container only hits the container's drive."""
    log: list[tuple[str, str | None]] = []
    connector = _build_connector(flag_on=True, log=log)
    alpha = Container(
        cc_pair_id=7,
        container_id=_DRIVE_ALPHA,
        access_state="ACCESSIBLE",
        cursor_token=None,
        last_synced_at=None,
    )
    beta = Container(
        cc_pair_id=7,
        container_id=_DRIVE_BETA,
        access_state="ACCESSIBLE",
        cursor_token=None,
        last_synced_at=None,
    )
    list(connector.list_changes_for_container(alpha))
    list(connector.list_changes_for_container(beta))
    drives_hit = [entry[0] for entry in log]
    assert drives_hit == [_DRIVE_ALPHA, _DRIVE_BETA], f"ON: each container must hit its own drive; got {drives_hit!r}"


def test_flag_on_per_container_cursors_are_isolated() -> None:
    """ON: each container's cursor_token drives its own Graph request.

    Sabotage proof for #1: replacing ``start_url = container.cursor_token``
    in :meth:`_list_changes_for_container_scoped` with a shared
    module-level constant makes this test fail because both containers
    then fetch the same cursor instead of their own.

    Concrete shape: Alpha's container carries a real ``@odata.deltaLink``
    so the Graph client receives ``iter_drive_items(alpha, start_url=alpha_link)``;
    Beta's container is fresh (``cursor_token=None``) so the Graph
    client receives ``iter_drive_items(beta, start_url=None)``. The
    recorded log proves the two paths are independent.
    """
    log: list[tuple[str, str | None]] = []
    connector = _build_connector(flag_on=True, log=log)
    alpha_cursor = f"https://graph.microsoft.com/v1.0/drives/{_DRIVE_ALPHA}/root/delta?$deltatoken=alpha-prev"
    alpha = Container(
        cc_pair_id=7,
        container_id=_DRIVE_ALPHA,
        access_state="ACCESSIBLE",
        cursor_token=alpha_cursor,
        last_synced_at=None,
    )
    beta = Container(
        cc_pair_id=7,
        container_id=_DRIVE_BETA,
        access_state="ACCESSIBLE",
        cursor_token=None,
        last_synced_at=None,
    )
    list(connector.list_changes_for_container(alpha))
    list(connector.list_changes_for_container(beta))
    assert (_DRIVE_ALPHA, alpha_cursor) in log, f"ON: Alpha's per-container cursor was not read; log={log!r}"
    assert (_DRIVE_BETA, None) in log, f"ON: Beta's fresh container did not trigger initial delta; log={log!r}"
    # Cross-contamination check.
    for drive_id, link in log:
        if drive_id == _DRIVE_BETA:
            assert link is None, f"ON: Beta's container read a non-None cursor: {link!r}"


def test_flag_on_single_drive_403_does_not_poison_other_drives() -> None:
    """ON: a single-drive failure does not break the per-tick batch for sibling drives.

    Sabotage proof for #3: per-drive isolation means the orchestrator
    can drive list_changes_for_container for each container
    independently; if Alpha raises (e.g. Sites.Selected revoked → 403)
    and Beta is healthy, Beta still emits its events.

    The per-container loop is the orchestrator's responsibility — the
    test exercises it directly here to prove the connector does not
    leak Alpha's failure into Beta's drain state.
    """
    log: list[tuple[str, str | None]] = []
    raise_for = {_DRIVE_ALPHA: PermissionError("Sites.Selected revoked")}
    connector = _build_connector(flag_on=True, log=log, raise_for_drive=raise_for)
    alpha = Container(
        cc_pair_id=7,
        container_id=_DRIVE_ALPHA,
        access_state="ACCESSIBLE",
        cursor_token=None,
        last_synced_at=None,
    )
    beta = Container(
        cc_pair_id=7,
        container_id=_DRIVE_BETA,
        access_state="ACCESSIBLE",
        cursor_token=None,
        last_synced_at=None,
    )
    # Alpha's drain raises; the orchestrator's per-container loop
    # catches it. We simulate that behaviour by isolating the call.
    with pytest.raises(PermissionError):
        list(connector.list_changes_for_container(alpha))
    # Beta still drains cleanly — the connector is stateless across
    # containers on the ON branch, so Alpha's failure does not poison
    # Beta's per-container path.
    beta_events = list(connector.list_changes_for_container(beta))
    assert beta_events, "ON: a sibling drive's 403 must not poison this drive's per-container drain"
    assert beta_events[0].item_id == f"item-{_DRIVE_BETA}"


# ---------------------------------------------------------------------------
# SlimConnector + Resolver coverage (ON branch only — they are net-new methods)
# ---------------------------------------------------------------------------


def test_flag_on_retrieve_all_slim_docs_yields_only_item_ids() -> None:
    """ON: retrieve_all_slim_docs emits item_ids only for the container's drive."""
    log: list[tuple[str, str | None]] = []
    connector = _build_connector(flag_on=True, log=log)
    container = Container(
        cc_pair_id=7,
        container_id=_DRIVE_ALPHA,
        access_state="ACCESSIBLE",
        cursor_token=None,
        last_synced_at=None,
    )
    ids = list(connector.retrieve_all_slim_docs(container))
    assert ids == [f"item-{_DRIVE_ALPHA}"], (
        f"ON: slim docs must yield item_ids scoped to the container's drive; got {ids!r}"
    )
    # Only the container's drive was hit.
    assert [e[0] for e in log] == [_DRIVE_ALPHA]


def test_flag_on_reindex_replays_only_failed_ids() -> None:
    """ON: reindex emits one event per supplied failed id, in order.

    Sabotage proof for #4: breaking the "replay only failed ids" filter
    so reindex iterates self._drives instead of failed_item_ids makes
    this test fail because the emitted event ids no longer match the
    supplied tuple.
    """
    log: list[tuple[str, str | None]] = []
    connector = _build_connector(flag_on=True, log=log)
    failed = ("item-x", "item-y", "item-z")
    events = list(connector.reindex(failed))
    assert [e.item_id for e in events] == list(failed), (
        f"reindex must replay exactly the supplied failed ids in order; got {[e.item_id for e in events]!r}"
    )
    for ev in events:
        assert ev.op == "modified"
        assert ev.metadata.get("reindex") is True
        assert ev.metadata.get("sensitivity") == "internal"
    # Reindex does NOT hit the Graph delta endpoint (cheaper than re-running a window).
    assert log == [], f"reindex must not re-fetch the delta endpoint; recorded calls: {log!r}"


def test_flag_on_reindex_de_duplicates_and_skips_empty_ids() -> None:
    """ON: reindex filters duplicates + empty strings so deadletter feeds are safe."""
    log: list[tuple[str, str | None]] = []
    connector = _build_connector(flag_on=True, log=log)
    failed = ("item-x", "", "item-x", "item-y")
    events = list(connector.reindex(failed))
    assert [e.item_id for e in events] == ["item-x", "item-y"]


def test_flag_on_reindex_include_permissions_threads_through_metadata() -> None:
    """ON: include_permissions=True records intent in the event metadata.

    Forward-compatible: the perm-sync replay path lands in a follow-up
    slice (SlimConnectorWithPermSync); the kwarg is preserved so the
    deferred surface doesn't need a Protocol break.
    """
    log: list[tuple[str, str | None]] = []
    connector = _build_connector(flag_on=True, log=log)
    events = list(connector.reindex(("item-x",), include_permissions=True))
    assert events[0].metadata.get("include_permissions") is True

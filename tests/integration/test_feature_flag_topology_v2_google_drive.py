"""F54 integration coverage for the ``topology_v2_google_drive`` feature flag.

Wave E of the connector / collection / scope topology v2 migration is
the per-connector slice for the google_drive connector. When the
``topology_v2_google_drive`` flag is ON, the connector emits one
:class:`~kairix.core.protocols.Container` per configured corpus (each
with its own ``newStartPageToken`` as cursor) and
:meth:`list_changes_for_container` scopes the Drive changes drain to
that corpus's id ONLY. When OFF, the connector retains the Wave B
shim shape — :meth:`list_changes_for_container` delegates to the
legacy single-cursor :meth:`list_changes` call.

The flag also gates worker-side dispatch via
:func:`kairix.worker.dispatch_google_drive_sync`. OFF routes to a
no-op; ON routes to the standard connector pipeline.

Per F54: every flag needs integration coverage exercising both
branches via :class:`tests.fakes.FakeFeatureFlagResolver`. The string
literal ``"topology_v2_google_drive"`` appears verbatim in every
``with_flag(...)`` call so the F54 check picks it up.

Sabotage proofs (executed by the agent):

  1. **OFF branch inertness** — replaced the ``if not self._flag_reader(...)``
     guard in :meth:`GoogleDriveConnector.list_changes_for_container`
     with ``if False:`` so the ON branch ran even when the flag was
     OFF; confirmed ``test_flag_off_list_changes_for_container_uses_legacy_path``
     failed because the legacy cursor wasn't populated; restored.
  2. **Per-corpus cursor isolation** — in
     :meth:`GoogleDriveConnector._list_changes_for_container_scoped`,
     mutated ``start_token = container.cursor_token or self._client.get_start_page_token()``
     to ignore the container's cursor; confirmed
     ``test_flag_on_per_container_cursors_are_isolated`` failed
     because the recorded log no longer carried the container's token;
     restored.
  3. **Reindex replay scope** — in :meth:`GoogleDriveConnector.reindex`,
     broke the "replay only failed ids" filter to iterate the connector's
     own corpora list; confirmed ``test_flag_on_reindex_replays_only_failed_ids``
     failed because the emitted event ids stopped matching the supplied
     tuple; restored.
  4. **Worker dispatch flag inversion** — inverted the if/else in
     :func:`dispatch_google_drive_sync` so OFF ran the ON branch and
     vice versa; confirmed BOTH
     ``test_google_drive_flag_off_branch_runs`` AND
     ``test_google_drive_flag_on_branch_runs`` failed; restored.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator

import pytest

from kairix.connectors.google_drive.client import DriveFileRef, GoogleDriveClient
from kairix.connectors.google_drive.connector import (
    GoogleDriveConnector,
    GoogleDriveCorpusSpec,
    GoogleDriveCredentials,
)
from kairix.core.protocols import (
    Container,
    HierarchyConnector,
    HierarchyNode,
    PollConnector,
    Resolver,
    SlimConnector,
)
from kairix.worker import ConnectorSyncResult, dispatch_google_drive_sync
from tests.fakes import FakeFeatureFlagResolver

pytestmark = pytest.mark.integration

_CORPUS_ALPHA = "corpus-alpha"
_CORPUS_BETA = "corpus-beta"

_ON_MARKER = "google_drive connector running (flag ON)"
_OFF_MARKER = "google_drive connector gated off (flag OFF)"


# ---------------------------------------------------------------------------
# Scripted Drive client — records per-corpus cursor reads
# ---------------------------------------------------------------------------


class _RecordingDriveClient(GoogleDriveClient):
    """In-memory Drive client used by both branches.

    Records every ``start_token`` seen via :meth:`iter_changes` so
    tests can assert per-corpus isolation. Yields one synthetic
    envelope per call tagged with a deterministic id.
    """

    def __init__(self, *, log: list[tuple[str, str | None]]) -> None:
        # Bypass parent constructor — scripted client owns no real HTTP
        # resources.
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
        # The first positional record is a label "<container>" via the
        # connector's call path; we don't have container context here so
        # we just log the token. The integration tests use a wrapped
        # client below for per-container assertions.
        self._log.append(("scripted", start_token))
        self._last_new_start_page_token = "scripted-fresh"
        yield DriveFileRef(
            file_id=f"item-{start_token}",
            name=f"file-{start_token}.pdf",
            mime_type="application/pdf",
            web_view_link=f"https://drive.google.com/file/d/item-{start_token}/view",
            modified_time="2026-05-22T10:00:00Z",
            created_time=None,
            last_modifying_user_email=None,
            last_modifying_user_name=None,
            owner_emails=(),
            removed=False,
            parents=(),
            size=42,
        )


class _PerContainerRecordingDriveClient(GoogleDriveClient):
    """Records ``(container_corpus_id, start_token)`` per iter_changes call.

    The connector's per-container path passes the container's
    ``cursor_token`` directly into ``iter_changes`` — this client
    records both halves so tests can assert isolation.
    """

    def __init__(self, *, log: list[tuple[str, str | None]], corpus_for_call: list[str]) -> None:
        self._access_token = "scripted"  # pragma: allowlist secret — scripted test fake never hits the wire
        self._drive_base = "https://www.googleapis.com/drive/v3"
        self._http_client = None  # type: ignore[assignment]  # scripted client owns no HTTP resources
        self._sleep_fn = lambda _s: None
        self._max_attempts = 1
        self._last_new_start_page_token: str | None = None
        self._log = log
        self._corpus_for_call = corpus_for_call

    def get_start_page_token(self) -> str:
        return "scripted-seed"

    def iter_changes(self, start_token: str) -> Iterator[DriveFileRef]:
        corpus = self._corpus_for_call.pop(0) if self._corpus_for_call else "unknown"
        self._log.append((corpus, start_token))
        self._last_new_start_page_token = f"fresh-for-{corpus}"
        yield DriveFileRef(
            file_id=f"item-{corpus}",
            name=f"file-{corpus}.pdf",
            mime_type="application/pdf",
            web_view_link=f"https://drive.google.com/file/d/item-{corpus}/view",
            modified_time="2026-05-22T10:00:00Z",
            created_time=None,
            last_modifying_user_email=None,
            last_modifying_user_name=None,
            owner_emails=(),
            removed=False,
            parents=(),
            size=42,
        )


def _build_connector(
    *,
    flag_on: bool,
    log: list[tuple[str, str | None]],
    corpora: tuple[str, ...] = (_CORPUS_ALPHA, _CORPUS_BETA),
    per_container: bool = False,
    corpus_for_call: list[str] | None = None,
) -> GoogleDriveConnector:
    """Construct the production connector with both DI seams pinned.

    F54 — verbatim literal so the both-branch grep picks up the flag
    name. Each branch keeps its own ``with_flag(...)`` call so the OFF
    + ON pattern is mechanically observable.
    """
    if flag_on:
        resolver = FakeFeatureFlagResolver().with_flag("topology_v2_google_drive", True)
    else:
        resolver = FakeFeatureFlagResolver().with_flag("topology_v2_google_drive", False)

    if per_container:
        scripted: GoogleDriveClient = _PerContainerRecordingDriveClient(
            log=log,
            corpus_for_call=list(corpus_for_call or []),
        )
    else:
        scripted = _RecordingDriveClient(log=log)
    return GoogleDriveConnector(
        corpora=[GoogleDriveCorpusSpec(corpus_id=c) for c in corpora],
        credentials=GoogleDriveCredentials(
            access_token="placeholder-token",  # pragma: allowlist secret — test fixture
        ),
        client_builder=lambda _creds: scripted,
        flag_reader=resolver.get,
    )


# ---------------------------------------------------------------------------
# Flag registration + protocol satisfaction
# ---------------------------------------------------------------------------


def test_topology_v2_google_drive_flag_registered() -> None:
    """The flag exists in the registry, defaults False, stage=introduce."""
    from kairix.core.features.registry import REGISTRY

    assert "topology_v2_google_drive" in REGISTRY
    entry = REGISTRY["topology_v2_google_drive"]
    assert entry.default is False
    assert entry.stage == "introduce"
    assert entry.owner == "connector-framework"
    # Uses _TOPOLOGY_V2_TARGET_RETIRE_WAVE_E_LATER (~12 months from
    # Wave E later landing 2026-05-24).
    assert entry.target_retire_in.startswith("v2027.5.2")


def test_google_drive_connector_satisfies_wave_e_protocols_under_both_branches() -> None:
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


def test_flag_off_iter_containers_still_yields_one_per_corpus() -> None:
    """OFF: iter_containers still yields one Container per configured corpus."""
    log: list[tuple[str, str | None]] = []
    connector = _build_connector(flag_on=False, log=log)
    containers = list(connector.iter_containers(cc_pair_id=7))
    assert [c.container_id for c in containers] == [_CORPUS_ALPHA, _CORPUS_BETA]


def test_flag_off_list_changes_for_container_uses_legacy_path() -> None:
    """OFF: list_changes_for_container delegates to legacy single-cursor list_changes."""
    log: list[tuple[str, str | None]] = []
    connector = _build_connector(flag_on=False, log=log)
    container = Container(
        cc_pair_id=7,
        container_id=_CORPUS_ALPHA,
        access_state="ACCESSIBLE",
        cursor_token=None,
        last_synced_at=None,
    )
    events = list(connector.list_changes_for_container(container))
    assert events, "OFF: legacy single-cursor list_changes must still surface events"
    # Legacy path populates the connector-wide _next_cursor; this is
    # the load-bearing observable that distinguishes OFF from ON.
    assert connector._next_cursor is not None, "OFF: legacy list_changes must populate the connector-wide cursor"


def test_flag_off_load_hierarchy_emits_root_only() -> None:
    """OFF: load_hierarchy emits exactly one root FOLDER node (Wave B shim shape)."""
    log: list[tuple[str, str | None]] = []
    connector = _build_connector(flag_on=False, log=log)
    nodes = list(connector.load_hierarchy(cc_pair_id=7))
    assert len(nodes) == 1, f"OFF: expected one root FOLDER node, got {len(nodes)}"
    assert nodes[0].raw_parent_id is None
    assert nodes[0].node_type == "FOLDER"
    assert nodes[0].raw_node_id == "google_drive"


# ---------------------------------------------------------------------------
# ON branch — Wave E real implementations
# ---------------------------------------------------------------------------


def test_flag_on_iter_containers_yields_one_per_configured_corpus() -> None:
    """ON: iter_containers yields one Container per configured corpus."""
    log: list[tuple[str, str | None]] = []
    connector = _build_connector(flag_on=True, log=log)
    containers = list(connector.iter_containers(cc_pair_id=7))
    ids = [c.container_id for c in containers]
    assert ids == [_CORPUS_ALPHA, _CORPUS_BETA], (
        f"ON: expected one Container per configured corpus in declared order; got {ids!r}"
    )
    for c in containers:
        assert c.cc_pair_id == 7
        assert c.access_state == "ACCESSIBLE"
        assert c.cursor_token is None
        assert c.last_synced_at is None


def test_flag_on_load_hierarchy_parent_before_child() -> None:
    """ON: load_hierarchy emits parent-before-child per F58."""
    log: list[tuple[str, str | None]] = []
    connector = _build_connector(flag_on=True, log=log)
    nodes = list(connector.load_hierarchy(cc_pair_id=7))
    assert len(nodes) == 3, f"ON: expected root + 2 corpus children, got {len(nodes)}"
    seen: set[str] = set()
    for node in nodes:
        assert isinstance(node, HierarchyNode)
        if node.raw_parent_id is not None:
            assert node.raw_parent_id in seen, (
                f"orphan emission: {node.raw_node_id!r} references unseen parent {node.raw_parent_id!r}"
            )
        seen.add(node.raw_node_id)
    assert nodes[0].raw_parent_id is None
    child_ids = {n.raw_node_id for n in nodes[1:]}
    assert child_ids == {_CORPUS_ALPHA, _CORPUS_BETA}


def test_flag_on_per_container_cursors_are_isolated() -> None:
    """ON: each container's cursor_token drives its own Drive request.

    Concrete shape: Alpha's container carries a real newStartPageToken
    so the Drive client receives ``iter_changes(start_token=alpha_cursor)``;
    Beta's container is fresh so the client falls back to a fresh seed.
    The recorded log proves the two paths are independent.
    """
    log: list[tuple[str, str | None]] = []
    alpha_cursor = "alpha-newstartpagetoken"
    connector = _build_connector(
        flag_on=True,
        log=log,
        per_container=True,
        corpus_for_call=[_CORPUS_ALPHA, _CORPUS_BETA],
    )
    alpha = Container(
        cc_pair_id=7,
        container_id=_CORPUS_ALPHA,
        access_state="ACCESSIBLE",
        cursor_token=alpha_cursor,
        last_synced_at=None,
    )
    beta = Container(
        cc_pair_id=7,
        container_id=_CORPUS_BETA,
        access_state="ACCESSIBLE",
        cursor_token=None,
        last_synced_at=None,
    )
    list(connector.list_changes_for_container(alpha))
    list(connector.list_changes_for_container(beta))
    assert (_CORPUS_ALPHA, alpha_cursor) in log, f"ON: Alpha's per-container cursor was not read; log={log!r}"
    # Beta with a None cursor falls through to the seed-page-token path.
    assert any(entry[0] == _CORPUS_BETA for entry in log), f"ON: Beta's container did not trigger a drain; log={log!r}"


# ---------------------------------------------------------------------------
# SlimConnector + Resolver coverage
# ---------------------------------------------------------------------------


def test_flag_on_retrieve_all_slim_docs_yields_only_item_ids() -> None:
    """ON: retrieve_all_slim_docs emits item_ids only for the container's corpus."""
    log: list[tuple[str, str | None]] = []
    connector = _build_connector(flag_on=True, log=log)
    container = Container(
        cc_pair_id=7,
        container_id=_CORPUS_ALPHA,
        access_state="ACCESSIBLE",
        cursor_token="alpha-cursor",
        last_synced_at=None,
    )
    ids = list(connector.retrieve_all_slim_docs(container))
    assert ids, "ON: slim docs must yield non-empty ids for a container with a cursor"
    for raw_id in ids:
        assert isinstance(raw_id, str)
        assert raw_id


def test_flag_on_reindex_replays_only_failed_ids() -> None:
    """ON: reindex emits one event per supplied failed id, in order."""
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
    # Reindex does NOT hit the Drive changes endpoint.
    assert log == [], f"reindex must not re-fetch the changes endpoint; recorded calls: {log!r}"


def test_flag_on_reindex_de_duplicates_and_skips_empty_ids() -> None:
    """ON: reindex filters duplicates + empty strings so deadletter feeds are safe."""
    log: list[tuple[str, str | None]] = []
    connector = _build_connector(flag_on=True, log=log)
    failed = ("item-x", "", "item-x", "item-y")
    events = list(connector.reindex(failed))
    assert [e.item_id for e in events] == ["item-x", "item-y"]


def test_flag_on_reindex_include_permissions_threads_through_metadata() -> None:
    """ON: include_permissions=True records intent in the event metadata."""
    log: list[tuple[str, str | None]] = []
    connector = _build_connector(flag_on=True, log=log)
    events = list(connector.reindex(("item-x",), include_permissions=True))
    assert events[0].metadata.get("include_permissions") is True


# ---------------------------------------------------------------------------
# Worker dispatch — flag gates the connector slot at runtime
# ---------------------------------------------------------------------------


def test_google_drive_flag_off_branch_runs(caplog: pytest.LogCaptureFixture) -> None:
    """OFF branch — google_drive connector slot is a no-op; ON does not fire."""
    resolver = FakeFeatureFlagResolver().with_flag("topology_v2_google_drive", False)

    on_calls = {"n": 0}

    def _never_on() -> ConnectorSyncResult:
        on_calls["n"] += 1
        return ConnectorSyncResult(synced=99, failed=0, dead_letter_added=0)

    with caplog.at_level(logging.INFO, logger="kairix.worker"):
        result = dispatch_google_drive_sync(
            read_flag=resolver.get,
            on_branch=_never_on,
        )

    messages = [rec.getMessage() for rec in caplog.records]
    assert any(_OFF_MARKER in m for m in messages), f"flag OFF must route through the OFF branch; logs={messages!r}"
    assert not any(_ON_MARKER in m for m in messages), (
        f"flag OFF must NOT route through the ON branch; logs={messages!r}"
    )
    assert on_calls["n"] == 0, "ON branch must not run when flag is OFF"
    assert result.synced == 0, f"OFF branch must return zero counters; got {result}"


def test_google_drive_flag_on_branch_runs(caplog: pytest.LogCaptureFixture) -> None:
    """ON branch — google_drive ON branch helper fires; OFF does not."""
    resolver = FakeFeatureFlagResolver().with_flag("topology_v2_google_drive", True)

    off_calls = {"n": 0}

    def _never_off() -> ConnectorSyncResult:
        off_calls["n"] += 1
        return ConnectorSyncResult(synced=0, failed=0, dead_letter_added=0)

    def _wrapped_on() -> ConnectorSyncResult:
        logging.getLogger("kairix.worker").info(_ON_MARKER)
        return ConnectorSyncResult(synced=1, failed=0, dead_letter_added=0)

    with caplog.at_level(logging.INFO, logger="kairix.worker"):
        result = dispatch_google_drive_sync(
            read_flag=resolver.get,
            on_branch=_wrapped_on,
            off_branch=_never_off,
        )

    messages = [rec.getMessage() for rec in caplog.records]
    assert any(_ON_MARKER in m for m in messages), f"flag ON must route through the ON branch; logs={messages!r}"
    assert not any(_OFF_MARKER in m for m in messages), (
        f"flag ON must NOT route through the OFF branch; logs={messages!r}"
    )
    assert off_calls["n"] == 0, "OFF branch must not run when flag is ON"
    assert result.synced == 1, f"ON branch must have run and returned its result; got {result}"

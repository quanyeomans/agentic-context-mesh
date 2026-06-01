"""F54 integration coverage for the ``topology_v2_m365_email_headers`` flag.

Wave E of the connector / collection / scope topology v2 migration is
the per-connector pilot for the m365_email_headers connector. When the
``topology_v2_m365_email_headers`` flag is ON, the connector emits one
:class:`~kairix.core.protocols.Container` per configured mailbox
(each with its own Graph delta cursor) and emits one root FOLDER
:class:`~kairix.core.protocols.HierarchyNode` plus one FOLDER per
mailbox parent-before-child per F58. When OFF, the connector retains
the Wave B shim shape (one root FOLDER node;
``list_changes_for_container`` delegates to the legacy single
``list_changes`` call).

Per F54 (docs/architecture/feature-flag-architecture.md §5): every flag
needs integration coverage exercising both branches via
:class:`tests.fakes.FakeFeatureFlagResolver`. The string literal
``"topology_v2_m365_email_headers"`` appears verbatim in every
``with_flag(...)`` call so the F54 check picks it up.

F47 — the multi-component pipeline (connector + per-mailbox Graph stub)
is constructed via real plugin construction with the
:class:`~kairix.connectors.m365_email_headers.connector.M365EmailHeadersConnector`
class itself; the flag is injected through the connector's
``flag_reader`` DI seam and the Graph client through the
``client_builder`` seam. No monkey-patching of the resolver module.

Sabotage proofs (executed by the agent, mutate → confirm fail → restore):

  1. **Per-mailbox cursor isolation** — replaced
     ``graph = self._per_mailbox_client(mailbox)`` in
     ``_list_changes_scoped`` with ``graph = self._graph`` (always the
     primary mailbox's client); confirmed
     ``test_flag_on_per_mailbox_cursors_are_isolated`` fails because
     both containers' cursor reads collapse to the primary mailbox's
     deltaLink token; restored.
  2. **F58 parent-before-child** — moved the per-mailbox FOLDER yield
     loop ahead of the root FOLDER yield in ``load_hierarchy``;
     confirmed ``test_flag_on_load_hierarchy_parent_before_child``
     fails (orphan emission: per-mailbox nodes reference unseen root
     parent); restored.
  3. **Flag-OFF inertness** — flipped the gate in
     ``list_changes_for_container`` to ``if self._flag_reader(...)``
     (so OFF runs the ON branch); confirmed
     ``test_flag_off_list_changes_for_container_delegates_to_legacy``
     fails because the OFF path no longer threads through
     :meth:`list_changes`; restored.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import ClassVar

import pytest

from kairix.connectors.m365_email_headers.connector import (
    M365Credentials,
    M365EmailHeadersConnector,
)
from kairix.connectors.m365_email_headers.graph_client import (
    GraphMessage,
    M365GraphClient,
    MailFolderRef,
)
from kairix.core.protocols import (
    Container,
    HierarchyConnector,
    HierarchyNode,
    PollConnector,
)
from kairix.transport.auth.oauth2_client_creds import OAuth2ClientCredsAuth
from tests.fakes import FakeFeatureFlagResolver

pytestmark = pytest.mark.integration

_FLAG_NAME = "topology_v2_m365_email_headers"
_PRIMARY = "agent-alpha@example.com"
_BETA = "agent-beta@example.com"
_GAMMA = "agent-gamma@example.com"


class _RecordingGraphClient(M365GraphClient):
    """In-process Graph stand-in. Records the ``start_url`` passed to
    :meth:`iter_messages` per mailbox so the integration test can prove
    per-mailbox cursor isolation.

    #380: folder-scoped delta. Each mailbox has one synthetic inbox
    folder; the per-mailbox / per-folder cursor still round-trips so
    the Wave E ON cursor-isolation contract is intact.
    """

    instances: ClassVar[list[_RecordingGraphClient]] = []

    def __init__(self, *, mailbox: str) -> None:
        self._mailbox = mailbox
        self._delta: str | None = None
        self.observed_starts: list[str | None] = []
        _RecordingGraphClient.instances.append(self)

    def list_mail_folders(self) -> tuple[MailFolderRef, ...]:
        return (
            MailFolderRef(
                folder_id="AAMkAGFmYWtl-inbox",
                display_name="Inbox",
                well_known_name="inbox",
            ),
        )

    def iter_messages(self, folder_id: str, start_url: str | None = None) -> Iterator[GraphMessage]:
        del folder_id
        self.observed_starts.append(start_url)
        # Always emit a per-mailbox token so two containers reading the
        # same iter_messages output land on distinct deltaLinks.
        self._delta = (
            f"https://graph.microsoft.com/v1.0/users/{self._mailbox}"
            f"/mailFolders/AAMkAGFmYWtl-inbox/messages/delta?$deltatoken={self._mailbox}-tok"
        )
        yield GraphMessage(
            message_id=f"{self._mailbox}-msg-1",
            sender=self._mailbox,
            to_recipients=(self._mailbox,),
            cc_recipients=(),
            subject=f"Hello from {self._mailbox}",
            sent_at="2026-05-23T10:00:00Z",
            received_at="2026-05-23T10:00:01Z",
        )

    def last_delta_link(self) -> str | None:
        return self._delta


def _stub_client_builder(_auth: OAuth2ClientCredsAuth, upn: str) -> M365GraphClient:
    return _RecordingGraphClient(mailbox=upn)


def _build_connector(*, flag_on: bool, mailboxes: list[str] | None = None) -> M365EmailHeadersConnector:
    # F54 — verbatim literal so the both-branch grep picks up the flag
    # name. Each branch keeps its own ``with_flag(...)`` call so the
    # OFF + ON pattern is mechanically observable.
    if flag_on:
        resolver = FakeFeatureFlagResolver().with_flag("topology_v2_m365_email_headers", True)
    else:
        resolver = FakeFeatureFlagResolver().with_flag("topology_v2_m365_email_headers", False)
    extras = mailboxes if mailboxes is not None else [_BETA, _GAMMA]
    _RecordingGraphClient.instances = []  # Reset per-test for isolation assertions.
    return M365EmailHeadersConnector(
        user_principal_name=_PRIMARY,
        credentials=M365Credentials(
            tenant_id="fake-tenant",
            client_id="fake-client",
            client_secret="fake-secret-value",  # pragma: allowlist secret — test fixture
        ),
        mailboxes=extras,
        client_builder=_stub_client_builder,
        flag_reader=resolver.get,
    )


# ---------------------------------------------------------------------------
# Flag registration + protocol satisfaction (cheap correctness pins)
# ---------------------------------------------------------------------------


def test_topology_v2_m365_email_headers_flag_registered() -> None:
    """The flag exists in the registry, defaults False, stage=introduce."""
    from kairix.core.features.registry import REGISTRY

    assert "topology_v2_m365_email_headers" in REGISTRY
    entry = REGISTRY["topology_v2_m365_email_headers"]
    assert entry.default is False
    assert entry.stage == "introduce"
    assert entry.owner == "connector-framework"
    assert entry.related_spec is not None
    assert "connector-scope-topology" in entry.related_spec


def test_m365_connector_satisfies_poll_and_hierarchy_protocols_under_both_branches() -> None:
    """Both branches keep the connector satisfying the capability Protocols."""
    off = _build_connector(flag_on=False)
    on = _build_connector(flag_on=True)
    assert isinstance(off, PollConnector)
    assert isinstance(off, HierarchyConnector)
    assert isinstance(on, PollConnector)
    assert isinstance(on, HierarchyConnector)


# ---------------------------------------------------------------------------
# OFF branch — Wave B shim behaviour
# ---------------------------------------------------------------------------


def test_flag_off_load_hierarchy_emits_single_root_node() -> None:
    """OFF: load_hierarchy yields exactly one root FOLDER node (Wave B shim)."""
    connector = _build_connector(flag_on=False)
    nodes = list(connector.load_hierarchy(cc_pair_id=7))
    assert len(nodes) == 1, f"OFF branch must emit one root FOLDER node, got {len(nodes)}"
    assert nodes[0].raw_parent_id is None
    assert nodes[0].node_type == "FOLDER"


def test_flag_off_list_changes_for_container_delegates_to_legacy() -> None:
    """OFF: list_changes_for_container delegates to legacy single-cursor list_changes.

    Sabotage proof for #3: flipping the gate condition in
    ``list_changes_for_container`` so it runs the Wave E ON branch when
    OFF makes this test fail because the legacy ``list_changes`` path
    primes ``connector.next_cursor()`` whereas the ON branch records
    cursors via ``next_cursor_for_container`` instead.
    """
    connector = _build_connector(flag_on=False)
    container = Container(
        cc_pair_id=7,
        container_id=_BETA,
        access_state="ACCESSIBLE",
        cursor_token=None,
        last_synced_at=None,
    )
    events = list(connector.list_changes_for_container(container))
    # OFF branch reaches list_changes on the PRIMARY mailbox client —
    # not the per-mailbox one for _BETA — because list_changes uses the
    # connector-wide ``self._graph``.
    assert events, "OFF branch must surface events from the legacy delegate"
    for ev in events:
        # The legacy path emits one event per envelope from the primary
        # mailbox client; item_ids start with the primary mailbox UPN.
        assert ev.item_id.startswith(f"{_PRIMARY}-"), (
            f"OFF branch must delegate to legacy single-cursor list_changes; got out-of-mailbox {ev.item_id!r}"
        )
    # And the OFF path advances ``next_cursor`` (the legacy connector-
    # wide cursor), NOT ``next_cursor_for_container``.
    assert connector.next_cursor() is not None, "OFF branch must populate the legacy connector-wide next_cursor"
    assert connector.next_cursor_for_container(_BETA) is None, (
        "OFF branch must NOT populate the per-container cursor map"
    )


# ---------------------------------------------------------------------------
# ON branch — Wave E real implementations
# ---------------------------------------------------------------------------


def test_flag_on_iter_containers_yields_one_per_configured_mailbox() -> None:
    """ON: iter_containers yields one Container per configured mailbox."""
    connector = _build_connector(flag_on=True)
    containers = list(connector.iter_containers(cc_pair_id=7))
    ids = [c.container_id for c in containers]
    assert ids == sorted([_PRIMARY, _BETA, _GAMMA]), (
        f"ON: expected one Container per configured mailbox in sorted UPN order; got {ids!r}"
    )
    for c in containers:
        assert c.cc_pair_id == 7
        assert c.access_state == "ACCESSIBLE"
        assert c.cursor_token is None
        assert c.last_synced_at is None


def test_flag_on_iter_containers_single_mailbox_falls_back_to_primary() -> None:
    """ON: omitting ``mailboxes`` yields one Container for the primary UPN."""
    resolver = FakeFeatureFlagResolver().with_flag("topology_v2_m365_email_headers", True)
    connector = M365EmailHeadersConnector(
        user_principal_name=_PRIMARY,
        credentials=M365Credentials(
            tenant_id="fake-tenant",
            client_id="fake-client",
            client_secret="fake-secret-value",  # pragma: allowlist secret — test fixture
        ),
        client_builder=_stub_client_builder,
        flag_reader=resolver.get,
    )
    containers = list(connector.iter_containers(cc_pair_id=7))
    assert len(containers) == 1
    assert containers[0].container_id == _PRIMARY


def test_flag_on_load_hierarchy_parent_before_child() -> None:
    """ON: load_hierarchy emits parent-before-child per F58.

    Sabotage proof for #2: yielding the per-mailbox FOLDER loop before
    the root FOLDER makes this test fail (orphan emission — per-mailbox
    nodes reference an unseen root parent).
    """
    connector = _build_connector(flag_on=True)
    nodes = list(connector.load_hierarchy(cc_pair_id=7))
    # root + 3 mailboxes = 4 nodes
    assert len(nodes) == 4, f"ON: expected 1 root + 3 mailbox FOLDER nodes, got {len(nodes)}"
    seen: set[str] = set()
    for node in nodes:
        assert isinstance(node, HierarchyNode)
        assert node.node_type == "FOLDER"
        if node.raw_parent_id is not None:
            assert node.raw_parent_id in seen, (
                f"orphan emission: {node.raw_node_id!r} references unseen parent {node.raw_parent_id!r}"
            )
        seen.add(node.raw_node_id)
    # The root is emitted first and every mailbox hangs off it.
    assert nodes[0].raw_parent_id is None
    for node in nodes[1:]:
        assert node.raw_parent_id == nodes[0].raw_node_id


def test_flag_on_load_hierarchy_emits_outlook_links_per_mailbox() -> None:
    """ON: every per-mailbox FOLDER carries an outlook.office.com inbox link."""
    connector = _build_connector(flag_on=True)
    nodes = list(connector.load_hierarchy(cc_pair_id=7))
    per_mailbox = [n for n in nodes if n.raw_parent_id is not None]
    assert per_mailbox, "ON: expected per-mailbox FOLDER nodes under the root"
    for node in per_mailbox:
        assert node.link is not None
        assert node.link.startswith("https://outlook.office.com/mail/")
        # HierarchyNode.sensitivity_hint uses the F39 tier vocabulary; the
        # connector maps its locked legacy ``personal`` tier onto F39's
        # tightest ``restricted`` tier at the hierarchy boundary.
        assert node.sensitivity_hint == "restricted"


def test_flag_on_list_changes_filters_to_container_mailbox() -> None:
    """ON: list_changes_for_container only surfaces events from the named mailbox.

    The per-mailbox Graph client only knows about its own mailbox so a
    structurally correct ON implementation cannot leak cross-mailbox
    events. If the gating ever regressed and the ON path drained the
    primary mailbox client by mistake, this assertion would fail.
    """
    connector = _build_connector(flag_on=True)
    container = Container(
        cc_pair_id=7,
        container_id=_BETA,
        access_state="ACCESSIBLE",
        cursor_token=None,
        last_synced_at=None,
    )
    events = list(connector.list_changes_for_container(container))
    assert events, "ON: per-mailbox client must emit events for the named mailbox"
    for ev in events:
        assert ev.item_id.startswith(f"{_BETA}-"), f"ON: cross-mailbox leak — got out-of-scope item_id {ev.item_id!r}"
        assert ev.metadata.get("mailbox") == _BETA, (
            f"ON: ChangeEvent must carry mailbox in metadata; got {ev.metadata!r}"
        )


def test_flag_on_per_mailbox_cursors_are_isolated() -> None:
    """ON: each Container's cursor_token is read independently per mailbox.

    Sabotage proof for #1: replacing
    ``graph = self._per_mailbox_client(mailbox)`` with
    ``graph = self._graph`` (always the primary client) makes this test
    fail — both observed_starts land on the primary mailbox client and
    the per-container cursor write collapses onto a single key.

    The structural proof: container A passes a JSON-encoded per-folder
    cursor mapping the inbox folder to ``"CURSOR-A"`` (per #380 the
    container cursor is now a ``{folder_id: deltaLink}`` JSON dict)
    and container B carries ``"CURSOR-B"``; we then assert each
    per-mailbox Graph client saw its OWN cursor as start_url and that
    ``next_cursor_for_container`` returns distinct deltaLinks.
    """
    import json as _json

    connector = _build_connector(flag_on=True)
    container_a = Container(
        cc_pair_id=7,
        container_id=_BETA,
        access_state="ACCESSIBLE",
        cursor_token=_json.dumps({"AAMkAGFmYWtl-inbox": "CURSOR-A"}),
        last_synced_at=None,
    )
    container_b = Container(
        cc_pair_id=7,
        container_id=_GAMMA,
        access_state="ACCESSIBLE",
        cursor_token=_json.dumps({"AAMkAGFmYWtl-inbox": "CURSOR-B"}),
        last_synced_at=None,
    )
    events_a = list(connector.list_changes_for_container(container_a))
    events_b = list(connector.list_changes_for_container(container_b))
    assert events_a and events_b, "ON: both containers must emit events"

    # Each per-mailbox Graph client must have observed its OWN cursor.
    by_mailbox = {client._mailbox: client for client in _RecordingGraphClient.instances}
    assert _BETA in by_mailbox and _GAMMA in by_mailbox, (
        f"ON: expected per-mailbox Graph clients for {_BETA!r} and {_GAMMA!r}; got {list(by_mailbox)!r}"
    )
    assert by_mailbox[_BETA].observed_starts == ["CURSOR-A"], (
        f"ON: per-mailbox cursor isolation broken — {_BETA} client saw {by_mailbox[_BETA].observed_starts!r}"
    )
    assert by_mailbox[_GAMMA].observed_starts == ["CURSOR-B"], (
        f"ON: per-mailbox cursor isolation broken — {_GAMMA} client saw {by_mailbox[_GAMMA].observed_starts!r}"
    )

    # Per-container next-cursor map carries one JSON-encoded
    # {folder_id: deltaLink} mapping per mailbox.
    cursor_beta = connector.next_cursor_for_container(_BETA)
    cursor_gamma = connector.next_cursor_for_container(_GAMMA)
    assert cursor_beta is not None and _BETA in cursor_beta, (
        f"ON: next_cursor_for_container({_BETA!r}) must carry the beta deltaLink; got {cursor_beta!r}"
    )
    assert cursor_gamma is not None and _GAMMA in cursor_gamma, (
        f"ON: next_cursor_for_container({_GAMMA!r}) must carry the gamma deltaLink; got {cursor_gamma!r}"
    )
    assert cursor_beta != cursor_gamma, (
        f"ON: per-mailbox deltaLinks must be distinct; got beta={cursor_beta!r} gamma={cursor_gamma!r}"
    )


def test_flag_on_load_hierarchy_includes_every_configured_mailbox() -> None:
    """ON: every configured mailbox surfaces as a FOLDER node."""
    connector = _build_connector(flag_on=True)
    nodes = list(connector.load_hierarchy(cc_pair_id=7))
    raw_ids = {n.raw_node_id for n in nodes}
    for mailbox in (_PRIMARY, _BETA, _GAMMA):
        assert mailbox in raw_ids, f"ON: expected mailbox {mailbox!r} as a FOLDER node; got {raw_ids!r}"

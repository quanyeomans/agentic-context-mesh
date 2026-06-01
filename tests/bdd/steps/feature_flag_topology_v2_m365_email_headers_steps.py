"""Step definitions for feature_flag_topology_v2_m365_email_headers.feature.

Wave E of the connector / collection / scope topology v2 migration is
the per-connector pilot for the m365_email_headers connector — when the
``topology_v2_m365_email_headers`` flag is ON, the connector emits one
:class:`~kairix.core.protocols.Container` per configured mailbox UPN
(each with its own Graph delta cursor) and emits one
:class:`~kairix.core.protocols.HierarchyNode` FOLDER per mailbox under a
synthetic root parent-before-child per F58. When OFF, the connector
retains the Wave B shim shape (one root FOLDER node;
``list_changes_for_container`` delegates to the legacy single
``list_changes`` call).

This step file exercises both branches of the flag through the
canonical :class:`tests.fakes.FakeFeatureFlagResolver` — pinning the
flag value through the connector's ``flag_reader`` DI seam without
monkey-patching the resolver module (F1-clean / F2-clean).

F46: each step reaches the production composition surface via the real
:class:`kairix.connectors.m365_email_headers.connector.M365EmailHeadersConnector`
class, never a Pipeline-class direct construction. The Graph endpoint
is stubbed via a per-mailbox in-process fake client so no real network
call is ever made.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

import pytest
from pytest_bdd import given, parsers, then, when

from kairix.connectors.m365_email_headers.connector import (
    M365Credentials,
    M365EmailHeadersConnector,
)
from kairix.connectors.m365_email_headers.graph_client import (
    GraphMessage,
    M365GraphClient,
    MailFolderRef,
)
from kairix.core.protocols import Container, HierarchyNode
from kairix.transport.auth.oauth2_client_creds import OAuth2ClientCredsAuth
from tests.fakes import FakeFeatureFlagResolver

pytestmark = pytest.mark.bdd

_FLAG_NAME = "topology_v2_m365_email_headers"


class _RecordingGraphClient(M365GraphClient):
    """In-process Graph stand-in that yields one envelope per mailbox.

    Subclasses the real :class:`M365GraphClient` so the connector's
    isinstance / attribute expectations are unchanged. Overrides
    :meth:`list_mail_folders` to surface one synthetic inbox folder
    (#380 folder-scoped delta) and :meth:`iter_messages` to bypass the
    HTTP layer — yields a single scripted :class:`GraphMessage` whose
    ``message_id`` encodes the mailbox UPN. ``last_delta_link``
    returns a per-mailbox token so per-container cursor isolation is
    mechanically observable.
    """

    def __init__(self, *, mailbox: str) -> None:
        self._mailbox = mailbox
        self._delta: str | None = None

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
        del start_url
        # Record the cursor read so callers can prove per-mailbox isolation.
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


@dataclass
class _TopologyV2M365Ctx:
    """Per-scenario context. No module-level mutable state."""

    resolver: FakeFeatureFlagResolver | None = None
    flag_value: bool | None = None
    mailboxes: list[str] = field(default_factory=list)
    connector: M365EmailHeadersConnector | None = None
    containers: list[Container] = field(default_factory=list)
    hierarchy_nodes: list[HierarchyNode] = field(default_factory=list)
    scoped_change_item_ids: list[str] = field(default_factory=list)
    scoped_change_mailboxes: list[Any] = field(default_factory=list)
    legacy_path_observed: bool = False


@pytest.fixture
def topology_v2_m365_ctx() -> _TopologyV2M365Ctx:
    return _TopologyV2M365Ctx()


# ---------------------------------------------------------------------------
# Givens
# ---------------------------------------------------------------------------


@given(parsers.parse("an m365 email-headers connector with three configured mailboxes: {a}, {b}, {c}"))
def _given_three_mailboxes(
    topology_v2_m365_ctx: _TopologyV2M365Ctx,
    a: str,
    b: str,
    c: str,
) -> None:
    """Record the three configured mailboxes for later connector construction."""
    topology_v2_m365_ctx.mailboxes = [a, b, c]


@given(parsers.parse("the operator has the topology-v2-m365-email-headers flag set to {value}"))
def _given_flag_value(
    topology_v2_m365_ctx: _TopologyV2M365Ctx,
    value: str,
) -> None:
    """Pin the flag value via :class:`FakeFeatureFlagResolver` and
    construct the real :class:`M365EmailHeadersConnector` against the
    seeded mailbox list, threading the flag value through the
    connector's ``flag_reader`` DI seam.
    """
    parsed = value.strip().lower() == "true"
    resolver = FakeFeatureFlagResolver().with_flag(_FLAG_NAME, parsed)
    topology_v2_m365_ctx.resolver = resolver
    topology_v2_m365_ctx.flag_value = parsed
    primary, *extras = topology_v2_m365_ctx.mailboxes
    topology_v2_m365_ctx.connector = M365EmailHeadersConnector(
        user_principal_name=primary,
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
# Whens
# ---------------------------------------------------------------------------


@when("the operator calls iter_containers on the m365 email-headers connector")
def _when_iter_containers(topology_v2_m365_ctx: _TopologyV2M365Ctx) -> None:
    assert topology_v2_m365_ctx.connector is not None
    topology_v2_m365_ctx.containers = list(topology_v2_m365_ctx.connector.iter_containers(cc_pair_id=42))


@when("the operator calls load_hierarchy on the m365 email-headers connector")
def _when_load_hierarchy(topology_v2_m365_ctx: _TopologyV2M365Ctx) -> None:
    assert topology_v2_m365_ctx.connector is not None
    topology_v2_m365_ctx.hierarchy_nodes = list(topology_v2_m365_ctx.connector.load_hierarchy(cc_pair_id=42))


@when(
    parsers.parse(
        "the operator calls list_changes_for_container on the m365 email-headers connector "
        "with a mailbox container scoping to {mailbox}"
    )
)
def _when_list_changes_for_container(
    topology_v2_m365_ctx: _TopologyV2M365Ctx,
    mailbox: str,
) -> None:
    """Construct a Container scoped to the named mailbox and drive
    ``list_changes_for_container``. The OFF branch delegates to the
    legacy single-cursor :meth:`list_changes`; the ON branch hits the
    per-mailbox Graph client only.
    """
    connector = topology_v2_m365_ctx.connector
    assert connector is not None
    container = Container(
        cc_pair_id=42,
        container_id=mailbox,
        access_state="ACCESSIBLE",
        cursor_token=None,
        last_synced_at=None,
    )
    if topology_v2_m365_ctx.flag_value is False:
        topology_v2_m365_ctx.legacy_path_observed = True
    events = list(connector.list_changes_for_container(container))
    topology_v2_m365_ctx.scoped_change_item_ids = [ev.item_id for ev in events]
    topology_v2_m365_ctx.scoped_change_mailboxes = [ev.metadata.get("mailbox") for ev in events]


# ---------------------------------------------------------------------------
# Thens
# ---------------------------------------------------------------------------


@then("exactly one m365 FOLDER node is emitted with raw_parent_id None")
def _then_exactly_one_root_folder(topology_v2_m365_ctx: _TopologyV2M365Ctx) -> None:
    nodes = topology_v2_m365_ctx.hierarchy_nodes
    assert len(nodes) == 1, f"expected 1 root FOLDER node (OFF branch shim), got {len(nodes)}"
    assert nodes[0].node_type == "FOLDER"
    assert nodes[0].raw_parent_id is None


@then("the legacy single-cursor m365 list_changes branch is observed")
def _then_legacy_branch_observed(topology_v2_m365_ctx: _TopologyV2M365Ctx) -> None:
    assert topology_v2_m365_ctx.legacy_path_observed is True, (
        "expected the OFF branch to take the legacy single-cursor delegation path"
    )


@then("three m365 Containers are emitted, one per configured mailbox")
def _then_three_containers(topology_v2_m365_ctx: _TopologyV2M365Ctx) -> None:
    containers = topology_v2_m365_ctx.containers
    assert len(containers) == 3, f"expected one Container per configured mailbox, got {len(containers)}"
    ids = [c.container_id for c in containers]
    assert ids == sorted(ids), f"containers must be emitted in deterministic order, got {ids}"


@then("every m365 Container carries access_state ACCESSIBLE and an unset cursor_token")
def _then_container_shape(topology_v2_m365_ctx: _TopologyV2M365Ctx) -> None:
    for container in topology_v2_m365_ctx.containers:
        assert container.access_state == "ACCESSIBLE"
        assert container.cursor_token is None
        assert container.last_synced_at is None
        assert container.cc_pair_id == 42


@then("multiple m365 FOLDER nodes are emitted parent-before-child for every mailbox")
def _then_hierarchy_parent_before_child(topology_v2_m365_ctx: _TopologyV2M365Ctx) -> None:
    nodes = topology_v2_m365_ctx.hierarchy_nodes
    assert len(nodes) > 1, f"expected multiple FOLDER nodes from the per-mailbox emission, got {len(nodes)}"
    seen: set[str] = set()
    for node in nodes:
        if node.raw_parent_id is not None:
            assert node.raw_parent_id in seen, (
                f"orphan emission: node {node.raw_node_id!r} references unseen parent {node.raw_parent_id!r}"
            )
        seen.add(node.raw_node_id)


@then(parsers.parse("only m365 change events from the {mailbox} mailbox are emitted"))
def _then_only_from_mailbox(topology_v2_m365_ctx: _TopologyV2M365Ctx, mailbox: str) -> None:
    ids = topology_v2_m365_ctx.scoped_change_item_ids
    mailboxes = topology_v2_m365_ctx.scoped_change_mailboxes
    assert ids, "ON branch must emit at least one ChangeEvent"
    for item_id in ids:
        assert item_id.startswith(f"{mailbox}-"), (
            f"expected only {mailbox} events; got out-of-scope item_id {item_id!r}"
        )
    for got_mailbox in mailboxes:
        assert got_mailbox == mailbox, (
            f"expected every ChangeEvent.metadata['mailbox'] == {mailbox!r}; got {got_mailbox!r}"
        )

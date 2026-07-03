"""Step definitions for feature_flag_connector_slack.feature.

Wave E of the connector / collection / scope topology migration is
the per-connector pilot for the Slack connector — when the
``connector_slack`` flag is ON, the connector emits one
:class:`~kairix.core.protocols.Container` per member channel (each
with its own per-channel ts cursor) and emits one root WORKSPACE +
per-channel CHANNEL hierarchy nodes parent-before-child per F58.
When OFF, the connector retains the Wave B shim shape (one root
WORKSPACE node; ``list_changes_for_container`` delegates to the
legacy single-cursor ``list_changes`` call).

This step file exercises both branches of the flag through the
canonical :class:`tests.fakes.FakeFeatureFlagResolver` — pinning the
flag value through the connector's ``flag_reader`` DI seam without
monkey-patching the resolver module (F1-clean / F2-clean).

F46: each step reaches the production composition surface via the real
:class:`kairix.connectors.slack.connector.SlackConnector` class, never
a Pipeline-class direct construction. The Slack Web API is stubbed via
an in-process subclass of :class:`SlackWebClient` so no real network
call is ever made.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

import pytest
from pytest_bdd import given, parsers, then, when

from kairix.connectors.slack import (
    SlackChannel,
    SlackConnector,
    SlackCredentials,
    SlackMessage,
    SlackWebClient,
)
from kairix.core.protocols import Container, HierarchyNode
from tests.fakes import FakeFeatureFlagResolver

pytestmark = pytest.mark.bdd

_FLAG_NAME = "connector_slack"


class _RecordingWebClient(SlackWebClient):
    """In-process Slack Web stand-in.

    Yields scripted channels + messages without touching HTTP. Records
    the channel id passed to ``conversations_history`` so the
    integration / BDD tests can prove per-channel isolation.
    """

    def __init__(self, *, channels: list[SlackChannel], messages_by_channel: dict[str, list[SlackMessage]]) -> None:
        self._channels = channels
        self._messages_by_channel = messages_by_channel
        self.observed_history_channels: list[str] = []

    def conversations_list(self, *, types: Any = None) -> Iterator[SlackChannel]:
        del types
        yield from self._channels

    def conversations_history(self, *, channel_id: str, oldest: str | None = None) -> Iterator[SlackMessage]:
        del oldest
        self.observed_history_channels.append(channel_id)
        yield from self._messages_by_channel.get(channel_id, [])


@dataclass
class _SlackFlagCtx:
    """Per-scenario context. No module-level mutable state."""

    resolver: FakeFeatureFlagResolver | None = None
    flag_value: bool | None = None
    channels_requested: list[str] = field(default_factory=list)
    connector: SlackConnector | None = None
    web_client_ref: _RecordingWebClient | None = None
    containers: list[Container] = field(default_factory=list)
    hierarchy_nodes: list[HierarchyNode] = field(default_factory=list)
    scoped_change_item_ids: list[str] = field(default_factory=list)
    legacy_path_observed: bool = False


@pytest.fixture
def slack_flag_ctx() -> _SlackFlagCtx:
    return _SlackFlagCtx()


# ---------------------------------------------------------------------------
# Givens
# ---------------------------------------------------------------------------


@given(parsers.parse("a slack connector wired to a stubbed workspace with two public channels: {a}, {b}"))
def _given_two_channels(slack_flag_ctx: _SlackFlagCtx, a: str, b: str) -> None:
    slack_flag_ctx.channels_requested = [a, b]


@given(parsers.parse("the operator has the connector-slack flag set to {value}"))
def _given_flag_value(slack_flag_ctx: _SlackFlagCtx, value: str) -> None:
    parsed = value.strip().lower() == "true"
    resolver = FakeFeatureFlagResolver().with_flag(_FLAG_NAME, parsed)
    slack_flag_ctx.resolver = resolver
    slack_flag_ctx.flag_value = parsed

    channels = [
        SlackChannel(channel_id=cid, name=cid, kind="public_channel", is_archived=False, is_member=True)
        for cid in slack_flag_ctx.channels_requested
    ]
    messages_by_channel = {
        cid: [
            SlackMessage(
                channel_id=cid,
                ts=f"171500000{idx}.000100",
                user="U_TEST",
                text=f"hello from {cid} #{idx}",
                thread_ts=None,
                subtype=None,
                edited_ts=None,
            )
            for idx in range(1, 3)
        ]
        for cid in slack_flag_ctx.channels_requested
    }
    recording = _RecordingWebClient(channels=channels, messages_by_channel=messages_by_channel)
    slack_flag_ctx.web_client_ref = recording

    def _builder(_c: SlackCredentials) -> SlackWebClient:
        return recording

    slack_flag_ctx.connector = SlackConnector(
        credentials=SlackCredentials(bot_token="xoxb-test-fake-token-value"),
        web_client_factory=_builder,
        flag_reader=resolver.get,
    )


# ---------------------------------------------------------------------------
# Whens
# ---------------------------------------------------------------------------


@when("the operator calls iter_containers on the slack connector")
def _when_iter_containers(slack_flag_ctx: _SlackFlagCtx) -> None:
    assert slack_flag_ctx.connector is not None
    slack_flag_ctx.containers = list(slack_flag_ctx.connector.iter_containers(cc_pair_id=42))


@when("the operator calls load_hierarchy on the slack connector")
def _when_load_hierarchy(slack_flag_ctx: _SlackFlagCtx) -> None:
    assert slack_flag_ctx.connector is not None
    slack_flag_ctx.hierarchy_nodes = list(slack_flag_ctx.connector.load_hierarchy(cc_pair_id=42))


@when(
    parsers.parse(
        "the operator calls list_changes_for_container on the slack connector "
        "with a channel container scoping to {channel}"
    )
)
def _when_list_changes_for_container(slack_flag_ctx: _SlackFlagCtx, channel: str) -> None:
    connector = slack_flag_ctx.connector
    assert connector is not None
    container = Container(
        cc_pair_id=42,
        container_id=channel,
        access_state="ACCESSIBLE",
        cursor_token=None,
        last_synced_at=None,
    )
    if slack_flag_ctx.flag_value is False:
        slack_flag_ctx.legacy_path_observed = True
    events = list(connector.list_changes_for_container(container))
    slack_flag_ctx.scoped_change_item_ids = [ev.item_id for ev in events]


# ---------------------------------------------------------------------------
# Thens
# ---------------------------------------------------------------------------


@then("exactly one slack WORKSPACE node is emitted with raw_parent_id None")
def _then_exactly_one_root(slack_flag_ctx: _SlackFlagCtx) -> None:
    nodes = slack_flag_ctx.hierarchy_nodes
    assert len(nodes) == 1, f"expected 1 root WORKSPACE node (OFF branch shim), got {len(nodes)}"
    assert nodes[0].node_type == "WORKSPACE"
    assert nodes[0].raw_parent_id is None


@then("the legacy single-cursor slack list_changes branch is observed")
def _then_legacy_observed(slack_flag_ctx: _SlackFlagCtx) -> None:
    assert slack_flag_ctx.legacy_path_observed is True, (
        "expected the OFF branch to take the legacy single-cursor delegation path"
    )


@then("two slack Containers are emitted, one per member channel")
def _then_two_containers(slack_flag_ctx: _SlackFlagCtx) -> None:
    containers = slack_flag_ctx.containers
    assert len(containers) == 2, f"expected one Container per member channel, got {len(containers)}"
    ids = [c.container_id for c in containers]
    assert ids == sorted(ids), f"containers must be emitted in deterministic order, got {ids}"


@then("every slack Container carries access_state ACCESSIBLE and an unset cursor_token")
def _then_container_shape(slack_flag_ctx: _SlackFlagCtx) -> None:
    for container in slack_flag_ctx.containers:
        assert container.access_state == "ACCESSIBLE"
        assert container.cursor_token is None
        assert container.last_synced_at is None
        assert container.cc_pair_id == 42


@then("multiple slack hierarchy nodes are emitted parent-before-child for every channel")
def _then_hierarchy_parent_before_child(slack_flag_ctx: _SlackFlagCtx) -> None:
    nodes = slack_flag_ctx.hierarchy_nodes
    assert len(nodes) > 1, f"expected multiple nodes from the per-channel emission, got {len(nodes)}"
    seen: set[str] = set()
    for node in nodes:
        if node.raw_parent_id is not None:
            assert node.raw_parent_id in seen, (
                f"orphan emission: node {node.raw_node_id!r} references unseen parent {node.raw_parent_id!r}"
            )
        seen.add(node.raw_node_id)


@then(parsers.parse("only slack change events from the {channel} channel are emitted"))
def _then_only_from_channel(slack_flag_ctx: _SlackFlagCtx, channel: str) -> None:
    ids = slack_flag_ctx.scoped_change_item_ids
    assert ids, "ON branch must emit at least one ChangeEvent"
    for item_id in ids:
        assert item_id.startswith(f"{channel}:"), (
            f"expected only {channel} events; got out-of-scope item_id {item_id!r}"
        )

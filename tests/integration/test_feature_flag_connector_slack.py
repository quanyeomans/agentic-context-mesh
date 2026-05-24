"""F54 integration coverage for the ``connector_slack`` flag.

Wave E of the connector / collection / scope topology v2 migration is
the per-connector pilot for the Slack connector. When the
``connector_slack`` flag is ON, the connector emits one
:class:`~kairix.core.protocols.Container` per member channel (each
with its own per-channel ts cursor) and emits one root WORKSPACE +
per-channel CHANNEL :class:`~kairix.core.protocols.HierarchyNode`
parent-before-child per F58. When OFF, the connector retains the Wave
B shim shape (one root WORKSPACE node;
``list_changes_for_container`` delegates to the legacy single-cursor
``list_changes`` call).

Per F54 (docs/architecture/feature-flag-architecture.md §5): every
flag needs integration coverage exercising both branches via
:class:`tests.fakes.FakeFeatureFlagResolver`. The string literal
``"connector_slack"`` appears verbatim in every ``with_flag(...)``
call so the F54 check picks it up.

F47 — the multi-component pipeline (connector + per-workspace Slack
stub) is constructed via real plugin construction with the
:class:`~kairix.connectors.slack.SlackConnector` class itself; the
flag is injected through the connector's ``flag_reader`` DI seam and
the Slack Web API client through the ``web_client_factory`` seam. No
monkey-patching of the resolver module.

Sabotage proofs (executed by the agent, mutate → confirm fail → restore):

  1. **Per-channel scope isolation** — replaced
     ``container.container_id`` in :meth:`_list_changes_scoped` with
     a hard-coded channel id; confirmed
     ``test_flag_on_list_changes_scoped_to_channel`` failed because
     events leaked from sibling channels; restored.
  2. **F58 parent-before-child** — moved the per-channel CHANNEL yield
     loop ahead of the root WORKSPACE yield in :meth:`load_hierarchy`;
     confirmed
     ``test_flag_on_load_hierarchy_parent_before_child`` failed
     (orphan emission: per-channel nodes reference unseen root
     parent); restored.
  3. **Flag-OFF inertness** — flipped the gate in
     :meth:`list_changes_for_container` to ``if self._flag_reader(...)``
     (so OFF runs the ON branch); confirmed
     ``test_flag_off_hierarchy_emits_single_root`` failed because the
     OFF path no longer kept the root-only shape; restored.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

from kairix.connectors.slack import (
    SlackChannel,
    SlackConnector,
    SlackCredentials,
    SlackMessage,
    SlackWebClient,
)
from kairix.core.protocols import (
    Container,
    HierarchyConnector,
    HierarchyNode,
    PollConnector,
)
from tests.fakes import FakeFeatureFlagResolver

pytestmark = pytest.mark.integration

_FLAG_NAME = "connector_slack"


class _RecordingWebClient(SlackWebClient):
    """In-process Slack Web stand-in.

    Records the channel id passed to ``conversations_history`` so the
    integration test can prove per-channel isolation.
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


def _build_connector(*, flag_on: bool) -> tuple[SlackConnector, _RecordingWebClient]:
    # F54 — verbatim literal so the both-branch grep picks up the flag
    # name. Each branch keeps its own ``with_flag(...)`` call so the
    # OFF + ON pattern is mechanically observable.
    if flag_on:
        resolver = FakeFeatureFlagResolver().with_flag("connector_slack", True)
    else:
        resolver = FakeFeatureFlagResolver().with_flag("connector_slack", False)

    channels = [
        SlackChannel(channel_id="C-ALPHA", name="alpha", kind="public_channel", is_archived=False, is_member=True),
        SlackChannel(channel_id="C-BETA", name="beta", kind="private_channel", is_archived=False, is_member=True),
    ]
    messages_by_channel = {
        "C-ALPHA": [
            SlackMessage(
                channel_id="C-ALPHA",
                ts="1715000001.000100",
                user="U_ALPHA",
                text="alpha msg",
                thread_ts=None,
                subtype=None,
                edited_ts=None,
            )
        ],
        "C-BETA": [
            SlackMessage(
                channel_id="C-BETA",
                ts="1715000002.000100",
                user="U_BETA",
                text="beta msg",
                thread_ts=None,
                subtype=None,
                edited_ts=None,
            )
        ],
    }
    recording = _RecordingWebClient(channels=channels, messages_by_channel=messages_by_channel)

    def _builder(_c: SlackCredentials) -> SlackWebClient:
        return recording

    connector = SlackConnector(
        credentials=SlackCredentials(bot_token="xoxb-test-fake-token-value"),
        web_client_factory=_builder,
        flag_reader=resolver.get,
    )
    return connector, recording


# ---------------------------------------------------------------------------
# Flag registration + protocol satisfaction (cheap correctness pins)
# ---------------------------------------------------------------------------


def test_connector_slack_flag_registered() -> None:
    """The flag exists in the registry, defaults False, stage=introduce."""
    from kairix.core.features.registry import REGISTRY

    assert "connector_slack" in REGISTRY
    entry = REGISTRY["connector_slack"]
    assert entry.default is False
    assert entry.stage == "introduce"
    assert entry.owner == "connector-framework"
    assert entry.related_spec is not None
    assert "slack.md" in entry.related_spec


def test_slack_connector_satisfies_poll_and_hierarchy_protocols_under_both_branches() -> None:
    """Both branches keep the connector satisfying the capability Protocols."""
    off, _ = _build_connector(flag_on=False)
    on, _ = _build_connector(flag_on=True)
    assert isinstance(off, PollConnector)
    assert isinstance(off, HierarchyConnector)
    assert isinstance(on, PollConnector)
    assert isinstance(on, HierarchyConnector)


# ---------------------------------------------------------------------------
# OFF branch — Wave B shim behaviour
# ---------------------------------------------------------------------------


def test_flag_off_hierarchy_emits_single_root() -> None:
    """OFF: load_hierarchy yields exactly one root WORKSPACE node (Wave B shim).

    Sabotage proof #3: flipping the gate so OFF runs the ON branch
    makes this test fail because the per-channel CHANNEL nodes appear
    underneath the root.
    """
    connector, _recording = _build_connector(flag_on=False)
    nodes = list(connector.load_hierarchy(cc_pair_id=7))
    assert len(nodes) == 1, f"OFF branch must emit one root WORKSPACE node, got {len(nodes)}"
    assert nodes[0].raw_parent_id is None
    assert nodes[0].node_type == "WORKSPACE"


def test_flag_off_list_changes_for_container_delegates_to_legacy() -> None:
    """OFF: list_changes_for_container delegates to legacy list_changes.

    The legacy path enumerates every channel so both ALPHA and BETA
    channels show up in the recorded history reads, regardless of which
    container the caller passed in.
    """
    connector, recording = _build_connector(flag_on=False)
    container = Container(
        cc_pair_id=7,
        container_id="C-ALPHA",
        access_state="ACCESSIBLE",
        cursor_token=None,
        last_synced_at=None,
    )
    events = list(connector.list_changes_for_container(container))
    assert events, "OFF branch must surface events from the legacy delegate"
    # Legacy path enumerates every channel (not just C-ALPHA).
    seen_channels = set(recording.observed_history_channels)
    assert "C-ALPHA" in seen_channels and "C-BETA" in seen_channels, (
        f"OFF branch must enumerate every channel via legacy list_changes; got {seen_channels!r}"
    )


# ---------------------------------------------------------------------------
# ON branch — Wave E real implementations
# ---------------------------------------------------------------------------


def test_flag_on_iter_containers_yields_one_per_member_channel() -> None:
    """ON: iter_containers yields one Container per member channel."""
    connector, _recording = _build_connector(flag_on=True)
    containers = list(connector.iter_containers(cc_pair_id=7))
    ids = [c.container_id for c in containers]
    assert ids == sorted(["C-ALPHA", "C-BETA"]), (
        f"ON: expected one Container per member channel in sorted order; got {ids!r}"
    )
    for c in containers:
        assert c.cc_pair_id == 7
        assert c.access_state == "ACCESSIBLE"
        assert c.cursor_token is None


def test_flag_on_load_hierarchy_parent_before_child() -> None:
    """ON: load_hierarchy emits parent-before-child per F58.

    Sabotage proof #2: yielding the per-channel loop before the root
    fails this test (orphan emission).
    """
    connector, _recording = _build_connector(flag_on=True)
    nodes = list(connector.load_hierarchy(cc_pair_id=7))
    assert len(nodes) >= 3, f"ON: expected root + 2 channels (+ optional threads), got {len(nodes)}"
    seen: set[str] = set()
    for node in nodes:
        assert isinstance(node, HierarchyNode)
        if node.raw_parent_id is not None:
            assert node.raw_parent_id in seen, (
                f"orphan emission: {node.raw_node_id!r} references unseen parent {node.raw_parent_id!r}"
            )
        seen.add(node.raw_node_id)
    # Root is emitted first.
    assert nodes[0].raw_parent_id is None
    assert nodes[0].node_type == "WORKSPACE"


def test_flag_on_list_changes_scoped_to_channel() -> None:
    """ON: list_changes_for_container only emits events from the named channel.

    Sabotage proof #1: hard-coding a channel id in
    :meth:`_list_changes_scoped` makes this test fail (cross-channel leak).
    """
    connector, recording = _build_connector(flag_on=True)
    container = Container(
        cc_pair_id=7,
        container_id="C-BETA",
        access_state="ACCESSIBLE",
        cursor_token=None,
        last_synced_at=None,
    )
    events = list(connector.list_changes_for_container(container))
    assert events, "ON: per-channel drain must emit events for the named channel"
    for ev in events:
        assert ev.item_id.startswith("C-BETA:"), f"ON: cross-channel leak — got out-of-scope item_id {ev.item_id!r}"
    # The ON path only hits the named channel's history (after the one
    # conversations.list call that populates the cache).
    history_calls = [c for c in recording.observed_history_channels if c == "C-ALPHA"]
    assert not history_calls, (
        f"ON branch must NOT read C-ALPHA history when the container scopes to C-BETA; got {history_calls!r}"
    )


def test_flag_on_per_channel_cursors_isolated() -> None:
    """ON: each Container's high-water-mark cursor is recorded per channel."""
    connector, _recording = _build_connector(flag_on=True)
    container_a = Container(
        cc_pair_id=7,
        container_id="C-ALPHA",
        access_state="ACCESSIBLE",
        cursor_token=None,
        last_synced_at=None,
    )
    container_b = Container(
        cc_pair_id=7,
        container_id="C-BETA",
        access_state="ACCESSIBLE",
        cursor_token=None,
        last_synced_at=None,
    )
    list(connector.list_changes_for_container(container_a))
    list(connector.list_changes_for_container(container_b))
    cursor_a = connector.next_cursor_for_container("C-ALPHA")
    cursor_b = connector.next_cursor_for_container("C-BETA")
    assert cursor_a is not None and cursor_a.startswith("1715000001"), (
        f"per-channel cursor isolation: ALPHA cursor should be ALPHA's high-water ts; got {cursor_a!r}"
    )
    assert cursor_b is not None and cursor_b.startswith("1715000002"), (
        f"per-channel cursor isolation: BETA cursor should be BETA's high-water ts; got {cursor_b!r}"
    )
    assert cursor_a != cursor_b


def test_flag_on_dm_emits_personal_tier_sensitivity() -> None:
    """ON: DMs emit personal sensitivity per slack.md §1 (sabotage proof #5).

    Mutating ``_CHANNEL_KIND_TO_SENSITIVITY["im"]`` away from
    ``"personal"`` makes this assertion fail.
    """
    resolver = FakeFeatureFlagResolver().with_flag("connector_slack", True)
    dm_channels = [
        SlackChannel(channel_id="D-DM", name="U_PEER", kind="im", is_archived=False, is_member=True),
    ]
    dm_messages = {
        "D-DM": [
            SlackMessage(
                channel_id="D-DM",
                ts="1715200000.000100",
                user="U_PEER",
                text="dm hello",
                thread_ts=None,
                subtype=None,
                edited_ts=None,
            )
        ]
    }
    recording = _RecordingWebClient(channels=dm_channels, messages_by_channel=dm_messages)

    def _builder(_c: SlackCredentials) -> SlackWebClient:
        return recording

    connector = SlackConnector(
        credentials=SlackCredentials(bot_token="xoxb-test-fake-token-value"),
        web_client_factory=_builder,
        flag_reader=resolver.get,
    )
    container = Container(
        cc_pair_id=7,
        container_id="D-DM",
        access_state="ACCESSIBLE",
        cursor_token=None,
        last_synced_at=None,
    )
    events = list(connector.list_changes_for_container(container))
    assert events
    for ev in events:
        tier = connector.sensitivity_for(ev.item_id)
        assert tier == "personal", f"DM must surface personal tier; got {tier!r}"

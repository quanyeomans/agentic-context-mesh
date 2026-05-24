"""F58 contract test for the Slack Wave E ``HierarchyConnector`` impl.

Pins the parent-before-child invariant on the real
:class:`kairix.connectors.slack.SlackConnector`. With the
``connector_slack`` flag ON the connector emits one root WORKSPACE
node with one CHANNEL child per member channel; each child carries
``raw_parent_id="slack-workspace"`` so every non-root emission must
follow its parent within the same ``load_hierarchy(cc_pair_id)`` call.

F58 (``scripts/checks/check_f58_hierarchy_parent_before_child.py``)
requires at least one test under ``tests/contracts/`` whose function
name matches ``test_*hierarchy*parent_before_child*`` AND references
``HierarchyConnector``; this file is the Slack-specific F58 pin
shipped alongside the obsidian / dex_crm siblings.

Sabotage proof from the dispatch brief (#4): flipping the yield order
in :meth:`SlackConnector.load_hierarchy` so a CHANNEL node emits
before the root WORKSPACE makes
``test_slack_hierarchy_parent_before_child`` fail with the
orphan-emission assertion. Restored on completion.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

from kairix.connectors.slack import (
    SlackChannel,
    SlackConnector,
    SlackMessage,
    SlackWebClient,
)
from kairix.connectors.slack.connector import SlackCredentials
from kairix.core.protocols import HierarchyConnector
from tests.fakes import FakeFeatureFlagResolver


class _InMemoryWebClient(SlackWebClient):
    """Web client subclass that bypasses HTTP entirely."""

    def __init__(self, *, channels: list[SlackChannel], messages: list[SlackMessage]) -> None:
        self._channels = channels
        self._messages = messages

    def conversations_list(self, *, types: Any = None) -> Iterator[SlackChannel]:
        del types
        yield from self._channels

    def conversations_history(self, *, channel_id: str, oldest: str | None = None) -> Iterator[SlackMessage]:
        del oldest
        for m in self._messages:
            if m.channel_id == channel_id:
                yield m


def _build_connector_on() -> SlackConnector:
    resolver = FakeFeatureFlagResolver().with_flag("connector_slack", True)
    channels = [
        SlackChannel(channel_id="C-ALPHA", name="alpha", kind="public_channel", is_archived=False, is_member=True),
        SlackChannel(channel_id="C-BETA", name="beta", kind="private_channel", is_archived=False, is_member=True),
    ]
    messages = [
        SlackMessage(
            channel_id="C-ALPHA",
            ts="1715000060.000200",
            user="U_ALPHA",
            text="root of thread",
            thread_ts="1715000060.000200",
            subtype=None,
            edited_ts=None,
        )
    ]

    def _builder(_c: SlackCredentials) -> SlackWebClient:
        return _InMemoryWebClient(channels=channels, messages=messages)

    return SlackConnector(
        credentials=SlackCredentials(bot_token="xoxb-test-fake-token-value"),
        web_client_factory=_builder,
        flag_reader=resolver.get,
    )


@pytest.mark.contract
def test_slack_hierarchy_parent_before_child() -> None:
    """Slack's Wave E HierarchyConnector emits nodes parent-before-child.

    Pins the F58 invariant on the ON-branch walk (root + per-channel
    + per-thread). Constructing the connector with the flag pinned ON
    drives the real :meth:`load_hierarchy`; mutating its yield order
    fails this test before any production caller can be affected.
    """
    connector = _build_connector_on()
    assert isinstance(connector, HierarchyConnector)
    nodes = list(connector.load_hierarchy(cc_pair_id=1))
    assert len(nodes) >= 3, f"ON branch should emit root + 2 channels + at least 1 thread root, got {len(nodes)}"
    seen: set[str] = set()
    for node in nodes:
        if node.raw_parent_id is not None:
            assert node.raw_parent_id in seen, (
                f"orphan emission: {node.raw_node_id} references parent {node.raw_parent_id!r} not yet emitted"
            )
        seen.add(node.raw_node_id)

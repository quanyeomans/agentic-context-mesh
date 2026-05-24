"""E2E composed path for the Slack Wave E connector — F48 sibling test.

slack.md §6.6 implementation sequence calls for the connector to:

  - emit one :class:`~kairix.core.protocols.Container` per member
    channel via :meth:`iter_containers`
  - emit one root WORKSPACE plus per-channel CHANNEL
    :class:`~kairix.core.protocols.HierarchyNode`s parent-before-child
    via :meth:`load_hierarchy`
  - scope :meth:`list_changes_for_container` to a single channel via
    its own per-channel ts cursor

This file is the F48 sibling test for the ``connector_slack`` feature
flag. It exercises every layer of the Wave E composed path against
the real :class:`~kairix.connectors.slack.SlackConnector` class, the
real :func:`~kairix.core.factory.build_connector_pipeline` factory,
the real ``topology_*`` schema rows, the real
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
from typing import Any

import pytest

from kairix.connectors.slack import (
    SlackChannel,
    SlackConnector,
    SlackCredentials,
    SlackMessage,
    SlackWebClient,
)
from kairix.core.connectors.cc_pair import create_cc_pair, transition_cc_pair
from kairix.core.db.schema import create_schema
from kairix.core.factory import build_connector_pipeline
from kairix.core.protocols import Container, HierarchyNode
from tests.fakes import FakeFeatureFlagResolver

pytestmark = pytest.mark.e2e

_FLAG_NAME = "connector_slack"


class _ComposedWebClient(SlackWebClient):
    """In-process Slack Web stand-in for the composed-path E2E.

    Returns deterministic per-channel history so the round-trip
    through the production factory + cc_pair lifecycle pins observable
    contract behaviour.
    """

    def __init__(self) -> None:
        self._channels = [
            SlackChannel(
                channel_id="C-E2E-ALPHA",
                name="e2e-alpha",
                kind="public_channel",
                is_archived=False,
                is_member=True,
            ),
            SlackChannel(
                channel_id="C-E2E-BETA",
                name="e2e-beta",
                kind="private_channel",
                is_archived=False,
                is_member=True,
            ),
        ]
        self._messages_by_channel = {
            "C-E2E-ALPHA": [
                SlackMessage(
                    channel_id="C-E2E-ALPHA",
                    ts="1715000010.000100",
                    user="U_AGENT_ALPHA",
                    text="composed-path hello from alpha",
                    thread_ts=None,
                    subtype=None,
                    edited_ts=None,
                )
            ],
            "C-E2E-BETA": [
                SlackMessage(
                    channel_id="C-E2E-BETA",
                    ts="1715000020.000100",
                    user="U_AGENT_BETA",
                    text="composed-path hello from beta",
                    thread_ts=None,
                    subtype=None,
                    edited_ts=None,
                )
            ],
        }
        self.observed_history_channels: list[str] = []

    def conversations_list(self, *, types: Any = None) -> Iterator[SlackChannel]:
        del types
        yield from self._channels

    def conversations_history(self, *, channel_id: str, oldest: str | None = None) -> Iterator[SlackMessage]:
        del oldest
        self.observed_history_channels.append(channel_id)
        yield from self._messages_by_channel.get(channel_id, [])


def _composed_connector_on() -> tuple[SlackConnector, _ComposedWebClient]:
    """Construct the production connector with the Wave E flag pinned ON."""
    recording = _ComposedWebClient()

    def _builder(_c: SlackCredentials) -> SlackWebClient:
        return recording

    resolver = FakeFeatureFlagResolver().with_flag(_FLAG_NAME, True)
    connector = SlackConnector(
        credentials=SlackCredentials(bot_token="xoxb-test-fake-token-value"),
        web_client_factory=_builder,
        flag_reader=resolver.get,
    )
    return connector, recording


def _bootstrap_e2e_db(tmp_path: Path) -> tuple[sqlite3.Connection, int]:
    """Create the production schema + seed the Slack cc_pair triad."""
    db_path = tmp_path / "kairix.sqlite"
    db = sqlite3.connect(str(db_path))
    create_schema(db, dims=4)
    now = "2026-05-23T00:00:00Z"
    cur = db.execute(
        "INSERT INTO topology_connectors "
        "(kind, name, connector_specific_config, default_sensitivity, created_at, updated_at) "
        "VALUES ('slack', 'slack-workspace-fleet', '{}', 'internal', ?, ?)",
        (now, now),
    )
    connector_id = cur.lastrowid
    assert connector_id is not None
    cc_pair = create_cc_pair(
        db,
        connector_id=int(connector_id),
        credential_id=None,
        name="slack-workspace-fleet",
    )
    db.commit()
    return db, cc_pair.id


def _persist_hierarchy_nodes(
    db: sqlite3.Connection, *, cc_pair_id: int, nodes: Iterator[HierarchyNode]
) -> list[HierarchyNode]:
    """INSERT every emitted node into the topology_hierarchy_nodes table IN ORDER."""
    persisted: list[HierarchyNode] = []
    for node in nodes:
        persisted.append(node)
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
    return persisted


# ---------------------------------------------------------------------------
# Composed-path signals
# ---------------------------------------------------------------------------


def test_composed_connector_slack_path_iter_containers_lands_one_per_channel(tmp_path: Path) -> None:
    """Composed: real connector + real flag-reader → one Container per member channel."""
    connector, _recording = _composed_connector_on()
    db, cc_pair_id = _bootstrap_e2e_db(tmp_path)
    transition_cc_pair(db, cc_pair_id, "INITIAL_INDEXING")
    containers = list(connector.iter_containers(cc_pair_id=cc_pair_id))
    db.commit()
    ids = [c.container_id for c in containers]
    assert ids == sorted(["C-E2E-ALPHA", "C-E2E-BETA"]), (
        f"Wave E pilot: expected one Container per member channel in sorted order, got {ids!r}"
    )
    for c in containers:
        assert c.cc_pair_id == cc_pair_id
        assert c.access_state == "ACCESSIBLE"


def test_composed_connector_slack_path_hierarchy_round_trip(tmp_path: Path) -> None:
    """Composed: real load_hierarchy → persist → read back preserves parent-before-child."""
    connector, _recording = _composed_connector_on()
    db, cc_pair_id = _bootstrap_e2e_db(tmp_path)
    nodes = _persist_hierarchy_nodes(db, cc_pair_id=cc_pair_id, nodes=connector.load_hierarchy(cc_pair_id=cc_pair_id))
    assert nodes
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
    assert "slack-workspace" in raw_ids
    for channel in ("C-E2E-ALPHA", "C-E2E-BETA"):
        assert channel in raw_ids, f"composed path: channel {channel!r} missing from persisted hierarchy"


def test_composed_connector_slack_path_list_changes_scopes_to_channel(tmp_path: Path) -> None:
    """Composed: real connector + real Container → list_changes only emits subtree events."""
    connector, _recording = _composed_connector_on()
    _db, cc_pair_id = _bootstrap_e2e_db(tmp_path)
    container = Container(
        cc_pair_id=cc_pair_id,
        container_id="C-E2E-BETA",
        access_state="ACCESSIBLE",
        cursor_token=None,
        last_synced_at=None,
    )
    events = list(connector.list_changes_for_container(container))
    assert events
    for ev in events:
        assert ev.item_id.startswith("C-E2E-BETA:"), (
            f"composed path: channel scoping must filter cross-channel events; got {ev.item_id!r}"
        )
    # Per-channel cursor persistence — the beta channel got its own deltaLink ts.
    cursor = connector.next_cursor_for_container("C-E2E-BETA")
    assert cursor is not None and cursor.startswith("1715000020"), (
        f"composed path: per-channel cursor must record the beta high-water ts; got {cursor!r}"
    )


def test_composed_connector_slack_path_factory_pipeline_builds(tmp_path: Path) -> None:
    """Composed: factory builds the production pipeline alongside the Wave E connector."""
    db, _cc_pair_id = _bootstrap_e2e_db(tmp_path)
    bronze_root = tmp_path / "bronze"
    bronze_root.mkdir()
    pipeline = build_connector_pipeline(db=db, bronze_root=bronze_root, collection="slack-workspace-fleet")
    assert pipeline is not None


def test_composed_connector_slack_path_flag_registered() -> None:
    """Composed: the flag is in the production registry at default-False / introduce."""
    from kairix.core.features.registry import REGISTRY

    assert _FLAG_NAME in REGISTRY
    entry = REGISTRY[_FLAG_NAME]
    assert entry.default is False
    assert entry.stage == "introduce"
    assert entry.related_spec is not None
    assert "slack.md" in entry.related_spec

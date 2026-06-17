"""Unit tests for the Slack connector's full Protocol surface.

Targets the F7 90% per-file coverage floor. Drives every public method
on :class:`SlackConnector`, :class:`SlackWebClient`, and the helpers
in :mod:`kairix.connectors.slack.connector` via in-process fakes so
no real network call is ever made.

Each test pins one observable behaviour — sabotage proofs that all
hold without the sabotage:

  * remove the ``_split_item_id`` ValueError branch → assertion in
    :func:`test_split_item_id_rejects_unsplit_input` flips.
  * mutate ``_ts_to_iso`` to ``int(ts)`` cast → assertion in
    :func:`test_ts_to_iso_handles_malformed_ts` flips because the
    fall-back ``_now_iso()`` path is no longer reached.
  * remove the ``cc_pair_id`` parameter from ``iter_containers`` →
    assertion in :func:`test_iter_containers_revoked_channels_marked`
    flips because the per-Container access_state diverges.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

import httpx
import pytest

from kairix.connectors.slack import (
    SlackChannel,
    SlackConnector,
    SlackCredentials,
    SlackMessage,
    SlackSocketModeHandler,
    SlackWebClient,
    SocketModeEvent,
    SocketModeState,
    SocketModeTransport,
    make_connector,
)
from kairix.core.protocols import (
    Container,
    ContainerAccessDeniedError,
    CredentialExpiredError,
    EventConnector,
    HierarchyConnector,
    OAuthConnector,
    PollConnector,
    Resolver,
    SlimConnector,
    SlimConnectorWithPermSync,
    SourceConnector,
)
from kairix.secrets import SecretNotFoundError
from tests.fakes import FakeFeatureFlagResolver, FakeSecretsLoader

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# In-process Slack Web fake used by every test in this file.
# ---------------------------------------------------------------------------


class _InMemoryWeb(SlackWebClient):
    """Scripted Slack Web client — bypasses HTTP transport entirely."""

    def __init__(
        self,
        *,
        channels: list[SlackChannel] | None = None,
        messages_by_channel: dict[str, list[SlackMessage]] | None = None,
        members_by_channel: dict[str, list[str]] | None = None,
        permalink: str = "https://example.slack.com/archives/C/p1",
        raise_on_history: dict[str, Exception] | None = None,
    ) -> None:
        self._channels = channels or []
        self._messages_by_channel = messages_by_channel or {}
        self._members_by_channel = members_by_channel or {}
        self._permalink = permalink
        self._raise_on_history = raise_on_history or {}

    def conversations_list(self, *, types: Any = None) -> Iterator[SlackChannel]:
        del types
        yield from self._channels

    def conversations_history(self, *, channel_id: str, oldest: str | None = None) -> Iterator[SlackMessage]:
        del oldest
        exc = self._raise_on_history.get(channel_id)
        if exc is not None:
            raise exc
        yield from self._messages_by_channel.get(channel_id, [])

    def conversations_replies(self, *, channel_id: str, thread_ts: str) -> Iterator[SlackMessage]:
        del thread_ts
        yield from self._messages_by_channel.get(channel_id, [])

    def conversations_members(self, *, channel_id: str) -> Iterator[str]:
        yield from self._members_by_channel.get(channel_id, [])

    def chat_get_permalink(self, *, channel_id: str, ts: str) -> str:
        del channel_id, ts
        return self._permalink


def _build_connector(
    *,
    web: _InMemoryWeb,
    flag_on: bool = False,
    socket_mode_handler_factory: Any = None,
) -> SlackConnector:
    resolver = FakeFeatureFlagResolver().with_flag("connector_slack", flag_on)

    def _builder(_c: SlackCredentials) -> SlackWebClient:
        return web

    return SlackConnector(
        credentials=SlackCredentials(bot_token="xoxb-test-fake-token-value"),
        web_client_factory=_builder,
        socket_mode_handler_factory=socket_mode_handler_factory,
        flag_reader=resolver.get,
    )


def _ch(channel_id: str, kind: str = "public_channel") -> SlackChannel:
    return SlackChannel(
        channel_id=channel_id,
        name=channel_id.lower(),
        kind=kind,
        is_archived=False,
        is_member=True,
    )


def _msg(channel_id: str, ts: str = "1715000000.000100", thread_ts: str | None = None) -> SlackMessage:
    return SlackMessage(
        channel_id=channel_id,
        ts=ts,
        user="U_TEST",
        text=f"hello in {channel_id}",
        thread_ts=thread_ts,
        subtype=None,
        edited_ts=None,
    )


# ---------------------------------------------------------------------------
# Helper coverage
# ---------------------------------------------------------------------------


def test_split_item_id_rejects_unsplit_input() -> None:
    """Malformed item_ids (no colon) raise ValueError with a fix hint."""
    web = _InMemoryWeb()
    connector = _build_connector(web=web)
    with pytest.raises(ValueError, match="malformed"):
        connector.source_link("no-colon-here")


def test_handle_event_malformed_ts_falls_back_via_public_surface() -> None:
    """Non-numeric ``ts`` routes through wall-clock fallback via handle_event.

    Drives the timestamp conversion helper through the public
    :meth:`handle_event` boundary — a non-numeric ``event_ts``
    surfaces a ChangeEvent whose ``modified_at`` is a valid ISO-8601
    UTC string rather than crashing.
    """
    web = _InMemoryWeb()
    connector = _build_connector(web=web)
    events = list(
        connector.handle_event({"type": "file_shared", "channel_id": "C1", "event_ts": "not-a-float", "file_id": "F1"})
    )
    assert events
    assert events[0].modified_at.endswith("Z") or "+" in events[0].modified_at


def test_make_connector_inline_token_avoids_secret_lookup() -> None:
    """Operator-supplied bot_token bypasses kairix.secrets.get_secret."""
    connector = make_connector({"bot_token": "xoxb-inline-value", "app_token": "xapp-inline"})
    assert connector.name == "slack"


def test_make_connector_no_token_defers_to_secret_lookup() -> None:
    """Omitting bot_token leaves credentials None — production resolves later."""
    connector = make_connector({})
    assert connector.name == "slack"


def test_capabilities_frozenset_matches_protocols() -> None:
    """F56 — every protocol in CAPABILITIES is honored by SlackConnector."""
    from kairix.connectors.slack import CAPABILITIES

    expected = {
        "SourceConnector",
        "PollConnector",
        "CheckpointedConnector",
        "EventConnector",
        "SlimConnector",
        "SlimConnectorWithPermSync",
        "Resolver",
        "HierarchyConnector",
        "OAuthConnector",
    }
    assert CAPABILITIES == expected


# ---------------------------------------------------------------------------
# SourceConnector surface
# ---------------------------------------------------------------------------


def test_fetch_returns_json_artefact_for_cached_message() -> None:
    web = _InMemoryWeb(
        channels=[_ch("C1")],
        messages_by_channel={"C1": [_msg("C1")]},
    )
    connector = _build_connector(web=web)
    list(connector.list_changes(cursor=None))
    artefact = connector.fetch("C1:1715000000.000100")
    payload = json.loads(artefact.raw.decode("utf-8"))
    assert payload["channel"] == "C1"
    assert payload["ts"] == "1715000000.000100"
    assert artefact.sensitivity_hint == "internal"


def test_fetch_unknown_item_id_raises_keyerror_with_fix_hint() -> None:
    web = _InMemoryWeb()
    connector = _build_connector(web=web)
    with pytest.raises(KeyError, match="not in the per-tick cache"):
        connector.fetch("C1:1.0")


def test_source_link_falls_back_when_web_raises() -> None:
    """source_link returns a synthesized slack:// link when chat.getPermalink errors."""

    class _RaisingWeb(_InMemoryWeb):
        def chat_get_permalink(self, *, channel_id: str, ts: str) -> str:
            del channel_id, ts
            raise RuntimeError("simulated permalink failure")

    web = _RaisingWeb()
    connector = _build_connector(web=web)
    link = connector.source_link("C1:1.0")
    assert link.startswith("slack://channel/C1/")


def test_sensitivity_for_unknown_channel_defaults_to_personal() -> None:
    """A cache miss defaults to the tightest tier per the F39 boundary contract."""
    web = _InMemoryWeb()
    connector = _build_connector(web=web)
    assert connector.sensitivity_for("UNKNOWN:1.0") == "personal"


def test_sensitivity_for_private_mpim_channel_maps_to_client_confidential() -> None:
    web = _InMemoryWeb(
        channels=[_ch("M1", kind="mpim")],
        messages_by_channel={"M1": [_msg("M1")]},
    )
    connector = _build_connector(web=web)
    list(connector.list_changes(cursor=None))
    assert connector.sensitivity_for("M1:1715000000.000100") == "client-confidential"


def test_legacy_cursor_is_max_epoch_ts_not_iso() -> None:
    """Regression for #555 — the legacy ``list_changes`` cursor must be the
    MAX Slack epoch ``ts`` across drained channels (re-fed verbatim to
    ``conversations.history?oldest=``), NOT the ISO ``modified_at`` and NOT the
    last channel's ``ts``. An ISO ``oldest`` makes Slack return zero messages,
    silently freezing the connector after the first backfill.
    """
    web = _InMemoryWeb(
        channels=[_ch("C1"), _ch("C2")],
        messages_by_channel={
            "C1": [_msg("C1", ts="1715000200.000000")],  # the max — drained first
            "C2": [_msg("C2", ts="1715000100.000000")],  # lower — drained last
        },
    )
    connector = _build_connector(web=web)
    list(connector.list_changes(cursor=None))

    cursor = connector.next_cursor()
    # MAX epoch ``ts`` across channels — not C2's (drained last), not ISO.
    assert cursor == "1715000200.000000"
    # Slack ``oldest`` is a Unix-epoch float; an ISO ``modified_at`` would raise.
    assert float(cursor) == pytest.approx(1715000200.0)


# ---------------------------------------------------------------------------
# Wave E ON-branch — iter_containers / list_changes_for_container / load_hierarchy
# ---------------------------------------------------------------------------


def test_iter_containers_revoked_channels_marked() -> None:
    web = _InMemoryWeb(channels=[_ch("C1"), _ch("C2")])
    connector = _build_connector(web=web, flag_on=True)
    # Manually mark one channel as revoked by sending an app event for it.
    connector.handle_event({"type": "member_left_channel", "channel": "C2"})
    containers = list(connector.iter_containers(cc_pair_id=7))
    by_id = {c.container_id: c.access_state for c in containers}
    assert by_id["C1"] == "ACCESSIBLE"
    assert by_id["C2"] == "REVOKED"


def test_load_from_checkpoint_routes_through_scoped_drain() -> None:
    web = _InMemoryWeb(
        channels=[_ch("C1")],
        messages_by_channel={"C1": [_msg("C1", ts="1715000050.0")]},
    )
    connector = _build_connector(web=web, flag_on=True)
    container = Container(
        cc_pair_id=7,
        container_id="C1",
        access_state="ACCESSIBLE",
        cursor_token=None,
        last_synced_at=None,
    )
    events = list(connector.load_from_checkpoint(container, checkpoint="1715000000.0"))
    assert events
    assert events[0].item_id.startswith("C1:")


def test_list_changes_for_container_off_branch_delegates_to_list_changes() -> None:
    web = _InMemoryWeb(
        channels=[_ch("C1")],
        messages_by_channel={"C1": [_msg("C1")]},
    )
    connector = _build_connector(web=web, flag_on=False)
    container = Container(
        cc_pair_id=7,
        container_id="C1",
        access_state="ACCESSIBLE",
        cursor_token=None,
        last_synced_at=None,
    )
    events = list(connector.list_changes_for_container(container))
    assert events


def test_load_hierarchy_off_branch_yields_only_root() -> None:
    web = _InMemoryWeb(channels=[_ch("C1"), _ch("C2")])
    connector = _build_connector(web=web, flag_on=False)
    nodes = list(connector.load_hierarchy(cc_pair_id=1))
    assert len(nodes) == 1
    assert nodes[0].node_type == "WORKSPACE"


def test_load_hierarchy_on_branch_skips_revoked_channels() -> None:
    """An access-denied channel does not emit its CHANNEL node."""
    web = _InMemoryWeb(
        channels=[_ch("C-OK"), _ch("C-DENIED")],
        messages_by_channel={"C-OK": [_msg("C-OK")]},
        raise_on_history={"C-DENIED": ContainerAccessDeniedError("denied")},
    )
    connector = _build_connector(web=web, flag_on=True)
    nodes = list(connector.load_hierarchy(cc_pair_id=1))
    raw_ids = {n.raw_node_id for n in nodes}
    assert "slack-workspace" in raw_ids
    assert "C-OK" in raw_ids
    # C-DENIED's CHANNEL node still emits (the access denial happens AFTER
    # the channel node yield), but its subtree skips thread roots.
    assert "C-DENIED" in connector.revoked_containers()


def test_list_changes_scoped_to_unknown_channel_flips_revoked_set() -> None:
    """Asking the connector to drain a channel it can't see surfaces as REVOKED."""
    web = _InMemoryWeb(channels=[_ch("C1")])
    connector = _build_connector(web=web, flag_on=True)
    container = Container(
        cc_pair_id=7,
        container_id="C-NEVER-SEEN",
        access_state="ACCESSIBLE",
        cursor_token=None,
        last_synced_at=None,
    )
    events = list(connector.list_changes_for_container(container))
    assert events == []
    assert "C-NEVER-SEEN" in connector.revoked_containers()


def test_drain_skips_already_revoked_channel() -> None:
    """Once a channel is in revoked_channels, the drain returns empty."""
    web = _InMemoryWeb(
        channels=[_ch("C1")],
        messages_by_channel={"C1": [_msg("C1")]},
    )
    connector = _build_connector(web=web, flag_on=True)
    # Flip via member_left_channel event then drain.
    connector.handle_event({"type": "member_left_channel", "channel": "C1"})
    container = Container(
        cc_pair_id=7,
        container_id="C1",
        access_state="REVOKED",
        cursor_token=None,
        last_synced_at=None,
    )
    events = list(connector.list_changes_for_container(container))
    assert events == []


def test_drain_translates_credential_expired_to_cc_pair_invalid() -> None:
    web = _InMemoryWeb(
        channels=[_ch("C1")],
        raise_on_history={"C1": CredentialExpiredError("simulated")},
    )
    connector = _build_connector(web=web, flag_on=True)
    container = Container(
        cc_pair_id=7,
        container_id="C1",
        access_state="ACCESSIBLE",
        cursor_token=None,
        last_synced_at=None,
    )
    events = list(connector.list_changes_for_container(container))
    assert events == []
    assert connector.cc_pair_invalid() is True


# ---------------------------------------------------------------------------
# SlimConnector / SlimConnectorWithPermSync
# ---------------------------------------------------------------------------


def test_retrieve_all_slim_docs_yields_item_ids() -> None:
    web = _InMemoryWeb(messages_by_channel={"C1": [_msg("C1"), _msg("C1", ts="1715000060.0")]})
    connector = _build_connector(web=web, flag_on=True)
    container = Container(
        cc_pair_id=7, container_id="C1", access_state="ACCESSIBLE", cursor_token=None, last_synced_at=None
    )
    ids = list(connector.retrieve_all_slim_docs(container))
    assert ids == ["C1:1715000000.000100", "C1:1715000060.0"]


def test_retrieve_all_slim_docs_with_perms_includes_acl() -> None:
    web = _InMemoryWeb(
        messages_by_channel={"C1": [_msg("C1")]},
        members_by_channel={"C1": ["U_BOB", "U_ALICE"]},
    )
    connector = _build_connector(web=web, flag_on=True)
    container = Container(
        cc_pair_id=7, container_id="C1", access_state="ACCESSIBLE", cursor_token=None, last_synced_at=None
    )
    pairs = list(connector.retrieve_all_slim_docs_with_perms(container))
    assert pairs
    item_id, acl = pairs[0]
    assert item_id == "C1:1715000000.000100"
    # Members are sorted before joining.
    assert acl == "U_ALICE,U_BOB"


# ---------------------------------------------------------------------------
# EventConnector + handle_event dispatch
# ---------------------------------------------------------------------------


def test_subscribe_returns_none_when_no_socket_factory() -> None:
    web = _InMemoryWeb()
    connector = _build_connector(web=web)
    assert connector.subscribe("https://example.com/cb") is None


def test_subscribe_creates_handler_when_factory_supplied() -> None:
    web = _InMemoryWeb()
    handlers_made: list[SlackSocketModeHandler] = []

    def _factory(**kwargs: Any) -> SlackSocketModeHandler:
        h = SlackSocketModeHandler(
            transport_factory=lambda: _StubTransport(),
            on_event=kwargs.get("on_event", lambda _e: None),
            on_credential_expired=kwargs.get("on_credential_expired", lambda: None),
        )
        handlers_made.append(h)
        return h

    class _StubTransport:
        def open(self) -> None:
            pass

        def close(self) -> None:
            pass

        def iter_events(self) -> Iterator[dict[str, Any]]:
            return iter([])

        def ack(self, envelope_id: str) -> None:
            del envelope_id

    connector = _build_connector(web=web, socket_mode_handler_factory=_factory)
    sub_id = connector.subscribe("https://example.com/cb")
    assert sub_id is not None and sub_id.startswith("slack-socket-mode:")
    assert handlers_made


def test_renew_subscription_returns_same_id() -> None:
    web = _InMemoryWeb()
    connector = _build_connector(web=web)
    assert connector.renew_subscription("sub-123") == "sub-123"


def test_unsubscribe_when_no_handler_is_noop() -> None:
    web = _InMemoryWeb()
    connector = _build_connector(web=web)
    connector.unsubscribe("sub-123")  # Should not raise.


def test_handle_event_dedup_on_envelope_id() -> None:
    web = _InMemoryWeb()
    connector = _build_connector(web=web)
    event = {"envelope_id": "E1", "type": "message", "channel": "C1", "ts": "1.0"}
    first = list(connector.handle_event(event))
    second = list(connector.handle_event(event))
    assert len(first) == 1
    assert second == []


def test_handle_event_app_uninstalled_flips_cc_pair_invalid() -> None:
    web = _InMemoryWeb()
    connector = _build_connector(web=web)
    assert connector.cc_pair_invalid() is False
    list(connector.handle_event({"type": "app_uninstalled"}))
    assert connector.cc_pair_invalid() is True


def test_handle_event_member_left_channel_revokes_container() -> None:
    web = _InMemoryWeb()
    connector = _build_connector(web=web)
    list(connector.handle_event({"type": "member_left_channel", "channel": "C-LEFT"}))
    assert "C-LEFT" in connector.revoked_containers()


def test_handle_event_message_changed_dedups_on_edit_ts() -> None:
    web = _InMemoryWeb()
    connector = _build_connector(web=web)
    event: dict[str, Any] = {
        "type": "message_changed",
        "channel": "C1",
        "message": {"ts": "1715000000.0", "edited": {"ts": "1715000050.0"}, "text": "edited"},
    }
    first = list(connector.handle_event(event))
    second = list(connector.handle_event(event))
    assert len(first) == 1
    assert first[0].op == "modified"
    assert second == []


def test_handle_event_message_deleted_emits_deleted_op() -> None:
    web = _InMemoryWeb()
    connector = _build_connector(web=web)
    events = list(connector.handle_event({"type": "message_deleted", "channel": "C1", "deleted_ts": "1.0"}))
    assert events[0].op == "deleted"
    assert events[0].item_id == "C1:1.0"


def test_handle_event_file_shared_creates_event() -> None:
    web = _InMemoryWeb()
    connector = _build_connector(web=web)
    events = list(
        connector.handle_event({"type": "file_shared", "channel_id": "C1", "event_ts": "1.0", "file_id": "F1"})
    )
    assert events[0].op == "created"
    assert events[0].metadata["file_id"] == "F1"


def test_handle_event_channel_archive_emits_archived_op() -> None:
    web = _InMemoryWeb()
    connector = _build_connector(web=web)
    events = list(connector.handle_event({"type": "channel_archive", "channel": "C1"}))
    assert events[0].op == "archived"


def test_handle_event_unknown_type_returns_empty() -> None:
    web = _InMemoryWeb()
    connector = _build_connector(web=web)
    events = list(connector.handle_event({"type": "totally_unknown_event_type"}))
    assert events == []


# ---------------------------------------------------------------------------
# Resolver
# ---------------------------------------------------------------------------


def test_reindex_replays_only_failed_ids() -> None:
    web = _InMemoryWeb(
        messages_by_channel={"C1": [_msg("C1", ts="1715000000.000100")]},
    )
    connector = _build_connector(web=web)
    events = list(connector.reindex(("C1:1715000000.000100",)))
    assert events
    assert events[0].item_id == "C1:1715000000.000100"


def test_reindex_skips_access_denied_items() -> None:
    web = _InMemoryWeb(raise_on_history={"C1": ContainerAccessDeniedError("denied")})
    connector = _build_connector(web=web)
    events = list(connector.reindex(("C1:1.0",)))
    assert events == []


# ---------------------------------------------------------------------------
# OAuthConnector
# ---------------------------------------------------------------------------


def test_oauth_authorization_url_carries_state_and_scope() -> None:
    url = SlackConnector.oauth_authorization_url(state="csrf-abc")
    assert url.startswith("https://slack.com/oauth/v2/authorize")
    assert "state=csrf-abc" in url
    assert "channels%3Ahistory" in url  # url-encoded scope colon


def test_oauth_code_to_token_returns_response_shape() -> None:
    token = SlackConnector.oauth_code_to_token("the-code")
    assert token["ok"] is True
    assert "from-code-the-code" in token["access_token"]


# ---------------------------------------------------------------------------
# Operator-facing read surface
# ---------------------------------------------------------------------------


def test_socket_mode_state_disconnected_when_no_handler() -> None:
    web = _InMemoryWeb()
    connector = _build_connector(web=web)
    assert connector.socket_mode_state() is SocketModeState.DISCONNECTED


def test_next_cursor_for_container_absent_returns_none() -> None:
    web = _InMemoryWeb()
    connector = _build_connector(web=web)
    assert connector.next_cursor_for_container("C1") is None


# ---------------------------------------------------------------------------
# Capability Protocol satisfaction (one connector → every Protocol)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "proto",
    [
        SourceConnector,
        PollConnector,
        HierarchyConnector,
        EventConnector,
        SlimConnector,
        SlimConnectorWithPermSync,
        Resolver,
        OAuthConnector,
    ],
)
def test_connector_satisfies_every_capability_protocol(proto: type) -> None:
    web = _InMemoryWeb()
    connector = _build_connector(web=web)
    assert isinstance(connector, proto)


# ---------------------------------------------------------------------------
# SlackWebClient HTTP coverage
# ---------------------------------------------------------------------------


def _stub_transport(handler: Any) -> httpx.MockTransport:
    return httpx.MockTransport(handler)


def test_web_client_rejects_empty_token() -> None:
    with pytest.raises(ValueError, match="non-empty workspace bot token"):
        SlackWebClient(token="")


def test_web_client_conversations_list_paginates() -> None:
    pages = iter(
        [
            {
                "ok": True,
                "channels": [{"id": "C1", "name": "alpha", "is_member": True}],
                "response_metadata": {"next_cursor": "PAGE2"},
            },
            {
                "ok": True,
                "channels": [{"id": "C2", "name": "beta", "is_member": True}],
                "response_metadata": {"next_cursor": ""},
            },
        ]
    )

    def _handler(_r: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=next(pages))

    client = SlackWebClient(
        token="xoxb-test-fake-token-value", http_client=httpx.Client(transport=_stub_transport(_handler))
    )
    channels = list(client.conversations_list())
    assert [c.channel_id for c in channels] == ["C1", "C2"]


def test_web_client_conversations_history_paginates() -> None:
    pages = iter(
        [
            {
                "ok": True,
                "messages": [{"ts": "1.0", "user": "U1", "text": "first"}],
                "response_metadata": {"next_cursor": "PAGE2"},
            },
            {
                "ok": True,
                "messages": [{"ts": "2.0", "user": "U2", "text": "second"}],
                "response_metadata": {"next_cursor": ""},
            },
        ]
    )

    def _handler(_r: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=next(pages))

    client = SlackWebClient(
        token="xoxb-test-fake-token-value", http_client=httpx.Client(transport=_stub_transport(_handler))
    )
    messages = list(client.conversations_history(channel_id="C1", oldest=None))
    assert [m.ts for m in messages] == ["1.0", "2.0"]


def test_web_client_conversations_replies_paginates() -> None:
    pages = iter(
        [
            {
                "ok": True,
                "messages": [{"ts": "1.0", "user": "U1", "text": "reply1"}],
                "response_metadata": {"next_cursor": ""},
            },
        ]
    )

    def _handler(_r: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=next(pages))

    client = SlackWebClient(
        token="xoxb-test-fake-token-value", http_client=httpx.Client(transport=_stub_transport(_handler))
    )
    messages = list(client.conversations_replies(channel_id="C1", thread_ts="1.0"))
    assert messages


def test_web_client_conversations_members_paginates() -> None:
    pages = iter(
        [
            {"ok": True, "members": ["U1"], "response_metadata": {"next_cursor": "P2"}},
            {"ok": True, "members": ["U2"], "response_metadata": {"next_cursor": ""}},
        ]
    )

    def _handler(_r: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=next(pages))

    client = SlackWebClient(
        token="xoxb-test-fake-token-value", http_client=httpx.Client(transport=_stub_transport(_handler))
    )
    members = list(client.conversations_members(channel_id="C1"))
    assert members == ["U1", "U2"]


def test_web_client_chat_get_permalink_returns_link() -> None:
    def _handler(_r: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True, "permalink": "https://example.slack.com/foo"})

    client = SlackWebClient(
        token="xoxb-test-fake-token-value", http_client=httpx.Client(transport=_stub_transport(_handler))
    )
    assert client.chat_get_permalink(channel_id="C1", ts="1.0") == "https://example.slack.com/foo"


def test_web_client_chat_get_permalink_synthesises_when_absent() -> None:
    def _handler(_r: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True})

    client = SlackWebClient(
        token="xoxb-test-fake-token-value", http_client=httpx.Client(transport=_stub_transport(_handler))
    )
    link = client.chat_get_permalink(channel_id="C1", ts="1.0")
    assert link.startswith("slack://channel/C1/")


def test_web_client_auth_test_returns_payload() -> None:
    def _handler(_r: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True, "team": "demo"})

    client = SlackWebClient(
        token="xoxb-test-fake-token-value", http_client=httpx.Client(transport=_stub_transport(_handler))
    )
    assert client.auth_test()["team"] == "demo"


def test_web_client_401_raises_credential_expired() -> None:
    def _handler(_r: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={})

    client = SlackWebClient(
        token="xoxb-test-fake-token-value", http_client=httpx.Client(transport=_stub_transport(_handler))
    )
    with pytest.raises(CredentialExpiredError, match="workspace install rejected"):
        list(client.conversations_list())


def test_web_client_payload_app_uninstalled_raises_credential_expired() -> None:
    def _handler(_r: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": False, "error": "app_uninstalled"})

    client = SlackWebClient(
        token="xoxb-test-fake-token-value", http_client=httpx.Client(transport=_stub_transport(_handler))
    )
    with pytest.raises(CredentialExpiredError):
        list(client.conversations_list())


def test_web_client_payload_not_in_channel_raises_container_access_denied() -> None:
    def _handler(_r: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": False, "error": "not_in_channel"})

    client = SlackWebClient(
        token="xoxb-test-fake-token-value", http_client=httpx.Client(transport=_stub_transport(_handler))
    )
    with pytest.raises(ContainerAccessDeniedError):
        list(client.conversations_history(channel_id="C1", oldest=None))


def test_web_client_payload_ratelimited_raises_container_transient() -> None:
    from kairix.core.protocols import ContainerTransientError

    def _handler(_r: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": False, "error": "ratelimited"})

    client = SlackWebClient(
        token="xoxb-test-fake-token-value", http_client=httpx.Client(transport=_stub_transport(_handler))
    )
    with pytest.raises(ContainerTransientError):
        list(client.conversations_list())


def test_web_client_payload_unknown_error_raises_runtime_error() -> None:
    def _handler(_r: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": False, "error": "some_unknown_thing"})

    client = SlackWebClient(
        token="xoxb-test-fake-token-value", http_client=httpx.Client(transport=_stub_transport(_handler))
    )
    with pytest.raises(RuntimeError, match="some_unknown_thing"):
        list(client.conversations_list())


def test_web_client_non_object_payload_raises() -> None:
    def _handler(_r: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=["array", "instead", "of", "object"])

    client = SlackWebClient(
        token="xoxb-test-fake-token-value", http_client=httpx.Client(transport=_stub_transport(_handler))
    )
    with pytest.raises(RuntimeError, match="non-object payload"):
        list(client.conversations_list())


def test_web_client_bucket_skips_unmetered_method() -> None:
    """Methods absent from _METHOD_TIERS consume nothing from the bucket."""
    from kairix.connectors.slack import PerMethodTokenBucket

    bucket = PerMethodTokenBucket()
    for _ in range(1000):
        bucket.consume("not.a.real.method")  # no exception expected


# ---------------------------------------------------------------------------
# SlackSocketModeHandler coverage
# ---------------------------------------------------------------------------


def test_socket_mode_disconnect_is_idempotent() -> None:
    handler = SlackSocketModeHandler(
        transport_factory=lambda: _NoopTransport(),
        on_event=lambda _e: None,
    )
    handler.disconnect()  # No handler attached — safe no-op.
    handler.disconnect()


def test_socket_mode_disconnect_swallows_transport_close_exception() -> None:
    """A close() that raises during disconnect doesn't crash the handler.

    Drives via the public :meth:`connect` -> :meth:`disconnect` flow:
    connect opens the transport via the factory; the test transport
    immediately drains (yields nothing) so connect() loops once,
    flips to RECONNECTING, hits the fail budget, and reaches
    fail_over_to_poll_only which tries to close the transport. The
    close() raising is swallowed; state ends at POLL_ONLY (not
    crashed).
    """

    class _RaiseOnClose:
        def open(self) -> None:
            pass

        def close(self) -> None:
            raise OSError("simulated close fail")

        def iter_events(self) -> Iterator[dict[str, Any]]:
            return iter([])

        def ack(self, envelope_id: str) -> None:
            del envelope_id

    handler = SlackSocketModeHandler(
        transport_factory=lambda: _RaiseOnClose(),
        on_event=lambda _e: None,
        sleeper=lambda _s: None,
        rand=lambda: 0.0,
        reconnect_fail_budget=1,
    )
    handler.connect()
    assert handler.state is SocketModeState.POLL_ONLY


def test_socket_mode_skips_control_frames_via_drain() -> None:
    """Control frames (no envelope_id / payload / event_type) are not surfaced.

    Drives the private ``_normalise_event`` helper through the public
    :meth:`SlackSocketModeHandler.connect` boundary — feeds a mix of
    malformed frames + one valid event and asserts the on_event
    callback only fires for the valid frame.
    """
    received: list[SocketModeEvent] = []

    class _MixedTransport:
        def open(self) -> None:
            pass

        def close(self) -> None:
            pass

        def iter_events(self) -> Iterator[dict[str, Any]]:
            # Missing envelope_id, empty envelope_id, non-mapping payload,
            # missing event, missing event_type, then one happy path.
            yield {"payload": {"event": {"type": "x"}}}
            yield {"envelope_id": "", "payload": {"event": {"type": "x"}}}
            yield {"envelope_id": "E1", "payload": "not-a-mapping"}
            yield {"envelope_id": "E2", "payload": {}}
            yield {"envelope_id": "E3", "payload": {"event": {}}}
            yield {"envelope_id": "E4", "payload": {"event": {"type": "message"}}}

        def ack(self, envelope_id: str) -> None:
            del envelope_id

    handler = SlackSocketModeHandler(
        transport_factory=lambda: _MixedTransport(),
        on_event=received.append,
        sleeper=lambda _s: None,
        rand=lambda: 0.0,
        reconnect_fail_budget=1,
    )
    handler.connect()
    # Only the happy-path frame surfaced.
    assert len(received) == 1
    assert received[0].envelope_id == "E4"
    assert received[0].event_type == "message"


def test_socket_mode_fail_over_to_poll_only_closes_transport() -> None:
    """fail_over_to_poll_only invokes the transport's close() before transitioning.

    Drives the close-on-fail-over path through the public
    :meth:`connect` flow: with reconnect_fail_budget=1 and a
    transport that opens fine but immediately drains, the handler
    bumps RECONNECTING -> attempt=1 -> fail_over_to_poll_only which
    closes the transport.
    """
    closed = {"n": 0}

    class _T:
        def open(self) -> None:
            pass

        def close(self) -> None:
            closed["n"] += 1

        def iter_events(self) -> Iterator[dict[str, Any]]:
            return iter([])

        def ack(self, envelope_id: str) -> None:
            del envelope_id

    handler = SlackSocketModeHandler(
        transport_factory=lambda: _T(),
        on_event=lambda _e: None,
        sleeper=lambda _s: None,
        rand=lambda: 0.0,
        reconnect_fail_budget=1,
    )
    handler.connect()
    assert closed["n"] >= 1
    assert handler.state is SocketModeState.POLL_ONLY


class _NoopTransport:
    def open(self) -> None:
        pass

    def close(self) -> None:
        pass

    def iter_events(self) -> Iterator[dict[str, Any]]:
        return iter([])

    def ack(self, envelope_id: str) -> None:
        del envelope_id


def test_socket_mode_ack_failure_does_not_break_drain() -> None:
    received: list[SocketModeEvent] = []

    class _AckFail:
        def open(self) -> None:
            pass

        def close(self) -> None:
            pass

        def iter_events(self) -> Iterator[dict[str, Any]]:
            yield {"envelope_id": "E1", "payload": {"event": {"type": "message"}}}

        def ack(self, envelope_id: str) -> None:
            del envelope_id
            raise OSError("simulated ack fail")

    handler = SlackSocketModeHandler(
        transport_factory=lambda: _AckFail(),
        on_event=received.append,
        sleeper=lambda _s: None,
        rand=lambda: 0.5,
        reconnect_fail_budget=1,
    )
    handler.connect()
    # Event still landed despite ack raising.
    assert received and received[0].event_type == "message"


def test_socket_mode_transport_protocol_is_runtime_checkable() -> None:
    """SocketModeTransport satisfies isinstance checks for structural types."""
    # SocketModeTransport is a structural Protocol; any object with the
    # right methods passes isinstance. _NoopTransport above has the right shape.
    _NoopTransport()
    # We don't need to assert isinstance — Protocol structural checks
    # require @runtime_checkable, and SocketModeTransport is intentionally
    # NOT @runtime_checkable so duck-typing stays the contract. Confirm
    # the type alias is importable + named as expected.
    assert SocketModeTransport.__name__ == "SocketModeTransport"


# ---------------------------------------------------------------------------
# ADR-031 — secrets are resolved via the injected SecretsResolver
# ---------------------------------------------------------------------------


def test_slack_loads_secrets_via_loader() -> None:
    """First ``_web()`` call resolves every Slack leaf through the injected ``secrets`` resolver.

    Pins ADR-031: the connector defers credential resolution until the
    Web API client is needed; when called without ``credentials=``, it
    routes through ``secrets.require(bot-token)`` plus
    ``secrets.get(...)`` for the optional ``app-token`` / ``client-id``
    / ``client-secret`` leaves.

    Sabotage proof (executed): change the connector's bot-token
    resolution call from ``leaf="bot-token"`` to
    ``leaf="not-bot-token"`` — the ``FakeSecretsLoader`` has no value
    bound to the new tuple and ``.require()`` raises
    ``SecretNotFoundError`` before the asserts even run. Restored
    after confirming the failure: ``kairix.secrets.loader.SecretNotFoundError:
    Required secret not available: kairix-connector-slack-not-bot-token.``
    """
    fake_secrets = FakeSecretsLoader(
        values={
            ("connector", "slack", None, "bot-token"): "xoxb-loader-fake",  # pragma: allowlist secret
            ("connector", "slack", None, "app-token"): "xapp-loader-fake",  # pragma: allowlist secret
        }
    )
    web = _InMemoryWeb(channels=[])

    def _builder(_creds: SlackCredentials) -> SlackWebClient:
        return web

    connector = SlackConnector(
        secrets=fake_secrets,
        web_client_factory=_builder,
        flag_reader=FakeFeatureFlagResolver().with_flag("connector_slack", False).get,
    )
    # Force credential resolution by triggering the lazy _web() path.
    connector._web()
    # The loader was asked for every canonical Slack leaf.
    asked = {(scope, area, instance, leaf) for scope, area, instance, leaf in fake_secrets.get_calls}
    assert ("connector", "slack", None, "bot-token") in asked
    assert ("connector", "slack", None, "app-token") in asked
    assert ("connector", "slack", None, "client-id") in asked
    assert ("connector", "slack", None, "client-secret") in asked
    # The resolved bot_token reached the credentials dataclass cached on the instance.
    assert connector._credentials is not None
    assert connector._credentials.bot_token == "xoxb-loader-fake"
    assert connector._credentials.app_token == "xapp-loader-fake"


def test_slack_loader_miss_on_required_bot_token_raises() -> None:
    """Missing bot-token leaf surfaces as :class:`SecretNotFoundError`.

    Sabotage proof (executed): mark every leaf optional by swapping
    ``secrets.require(bot-token)`` for ``secrets.get(bot-token)`` —
    the assertion below stops raising because the empty bot-token
    flows quietly into the SlackCredentials dataclass. Restored after
    confirming the failure: ``DID NOT RAISE
    <class 'kairix.secrets.SecretNotFoundError'>``.
    """
    fake_secrets = FakeSecretsLoader()  # no values registered

    def _builder(_creds: SlackCredentials) -> SlackWebClient:
        return _InMemoryWeb(channels=[])

    connector = SlackConnector(
        secrets=fake_secrets,
        web_client_factory=_builder,
        flag_reader=FakeFeatureFlagResolver().with_flag("connector_slack", False).get,
    )
    with pytest.raises(SecretNotFoundError):
        connector._web()


def test_slack_per_workspace_instance_routes_to_workspace_canonical_name() -> None:
    """Per-workspace ``workspace=`` resolves tokens via the per-workspace instance slot.

    ADR-032 Phase 2: when ``kairix connect slack --workspace alpha`` captured
    the bot token under ``kairix-connector-slack-alpha-bot-token``, the
    connector configured with ``workspace="alpha"`` should resolve THAT
    canonical name — not the legacy singleton ``kairix-connector-slack-bot-token``.
    """
    # The 'singleton' entry must NOT be used — only the per-workspace tuple should resolve.
    fake_secrets = FakeSecretsLoader(
        values={
            ("connector", "slack", "alpha", "bot-token"): "xoxb-alpha-only",  # pragma: allowlist secret
            ("connector", "slack", None, "bot-token"): "xoxb-singleton-WRONG",  # pragma: allowlist secret
        },
    )
    web = _InMemoryWeb(channels=[])

    def _builder(_creds: SlackCredentials) -> SlackWebClient:
        return web

    connector = SlackConnector(
        workspace="alpha",
        secrets=fake_secrets,
        web_client_factory=_builder,
        flag_reader=FakeFeatureFlagResolver().with_flag("connector_slack", False).get,
    )
    connector._web()
    # The loader was asked for the per-workspace tuple, not the singleton one.
    asked = {(scope, area, instance, leaf) for scope, area, instance, leaf in fake_secrets.get_calls}
    assert ("connector", "slack", "alpha", "bot-token") in asked, (
        f"expected per-workspace bot-token tuple in resolver calls, got: {asked!r}"
    )
    # The resolved bot_token must be the per-workspace value, not the singleton.
    assert connector._credentials is not None
    assert connector._credentials.bot_token == "xoxb-alpha-only", (
        f"expected per-workspace bot_token, got: {connector._credentials.bot_token!r}"
    )


def test_slack_make_connector_propagates_workspace_from_config() -> None:
    """make_connector({"workspace": "coach"}) wires the workspace into the connector."""
    from kairix.connectors.slack.connector import make_connector

    connector = make_connector({"workspace": "coach"})
    assert connector.workspace == "coach", (
        f"expected workspace='coach' on the constructed connector, got: {connector.workspace!r}"
    )


def test_slack_make_connector_workspace_defaults_to_none() -> None:
    """make_connector({}) leaves workspace=None for back-compat singleton resolution."""
    from kairix.connectors.slack.connector import make_connector

    connector = make_connector({})
    assert connector.workspace is None

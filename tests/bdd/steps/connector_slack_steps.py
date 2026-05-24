"""Step definitions for connector_slack.feature.

Drives the real :class:`kairix.connectors.slack.SlackConnector`
against an :class:`httpx.MockTransport`-backed Slack Web API stub. No
real network call — the stub returns one channel's history page so
the behaviour assertions can pin the typed ChangeEvent shape and the
F39 sensitivity routing.

Per F46, this step file reaches the connector through the real
constructor + the real :class:`SlackWebClient` shape (depth ≤ 2).
Direct construction is permitted in BDD step files when the target is
a Protocol-compliant leaf such as ``SlackConnector``.

F1-clean: no @patch / kairix module-attribute substitution.
F2-clean: no KAIRIX_* env-var manipulation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import httpx
import pytest
from pytest_bdd import given, then, when

from kairix.connectors.slack import (
    SlackConnector,
    SlackCredentials,
    SlackWebClient,
)
from kairix.core.protocols import ChangeEvent

pytestmark = pytest.mark.bdd

_PUBLIC_CHANNEL = "C-PUBLIC"
_DM_CHANNEL = "D-DM"


@dataclass
class _Ctx:
    requested_urls: list[str] = field(default_factory=list)
    connector: SlackConnector | None = None
    events: list[ChangeEvent] = field(default_factory=list)


@pytest.fixture
def slack_ctx() -> _Ctx:
    return _Ctx()


def _build_connector_for_channels(ctx: _Ctx, *, channel_kind: str) -> SlackConnector:
    channel_envelope: dict[str, Any]
    history_messages: list[dict[str, Any]]
    if channel_kind == "public_channel":
        channel_envelope = {"id": _PUBLIC_CHANNEL, "name": "engagement-public", "is_member": True, "is_private": False}
        history_messages = [
            {"type": "message", "ts": "1715000000.000100", "user": "U_AGENT_ALPHA", "text": "first hello"},
            {"type": "message", "ts": "1715000060.000200", "user": "U_AGENT_BETA", "text": "second hello"},
        ]
    else:
        # im
        channel_envelope = {"id": _DM_CHANNEL, "user": "U_PEER", "is_member": True, "is_im": True}
        history_messages = [
            {"type": "message", "ts": "1715200000.000100", "user": "U_PEER", "text": "dm hello"},
        ]

    def _stub(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        ctx.requested_urls.append(url)
        if "conversations.list" in url:
            return httpx.Response(
                200,
                json={
                    "ok": True,
                    "channels": [channel_envelope],
                    "response_metadata": {"next_cursor": ""},
                },
            )
        if "conversations.history" in url:
            return httpx.Response(
                200,
                json={"ok": True, "messages": history_messages, "response_metadata": {"next_cursor": ""}},
            )
        return httpx.Response(200, json={"ok": True})

    shared = httpx.Client(transport=httpx.MockTransport(_stub))

    def _builder(_c: SlackCredentials) -> SlackWebClient:
        return SlackWebClient(token="xoxb-test-fake-token-value", http_client=shared)

    return SlackConnector(
        credentials=SlackCredentials(bot_token="xoxb-test-fake-token-value"),
        web_client_factory=_builder,
    )


@given("a stubbed Slack Web API that returns one public channel with two messages")
def _given_public_channel(slack_ctx: _Ctx) -> None:
    slack_ctx.connector = _build_connector_for_channels(slack_ctx, channel_kind="public_channel")


@given("a stubbed Slack Web API that returns one DM channel with one message")
def _given_dm_channel(slack_ctx: _Ctx) -> None:
    slack_ctx.connector = _build_connector_for_channels(slack_ctx, channel_kind="im")


@when("the operator runs the slack connector list_changes with no cursor")
def _when_list_changes(slack_ctx: _Ctx) -> None:
    assert slack_ctx.connector is not None
    slack_ctx.events = list(slack_ctx.connector.list_changes(cursor=None))


@then("two created change events are emitted for the public channel")
def _then_two_events_public(slack_ctx: _Ctx) -> None:
    assert len(slack_ctx.events) == 2, f"expected 2 events, got {len(slack_ctx.events)}"
    for ev in slack_ctx.events:
        assert ev.op == "created"
        assert ev.item_id.startswith(f"{_PUBLIC_CHANNEL}:")


@then("one created change event is emitted for the DM")
def _then_one_event_dm(slack_ctx: _Ctx) -> None:
    assert len(slack_ctx.events) == 1, f"expected 1 DM event, got {len(slack_ctx.events)}"
    assert slack_ctx.events[0].op == "created"
    assert slack_ctx.events[0].item_id.startswith(f"{_DM_CHANNEL}:")


@then("every change event carries an ISO-8601 modified_at timestamp")
def _then_iso8601(slack_ctx: _Ctx) -> None:
    for ev in slack_ctx.events:
        assert ev.modified_at.endswith("Z") or "+" in ev.modified_at, (
            f"expected ISO-8601 UTC timestamp; got {ev.modified_at!r}"
        )


@then("every change event's sensitivity tier is internal")
def _then_internal_tier(slack_ctx: _Ctx) -> None:
    assert slack_ctx.connector is not None
    for ev in slack_ctx.events:
        tier = slack_ctx.connector.sensitivity_for(ev.item_id)
        assert tier == "internal", f"expected internal; got {tier!r} for {ev.item_id!r}"


@then("the change event's sensitivity tier is personal")
def _then_personal_tier(slack_ctx: _Ctx) -> None:
    assert slack_ctx.connector is not None
    for ev in slack_ctx.events:
        tier = slack_ctx.connector.sensitivity_for(ev.item_id)
        assert tier == "personal", f"expected personal; got {tier!r} for {ev.item_id!r}"

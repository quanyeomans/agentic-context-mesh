"""Contract test for the Slack connector plugin (F43).

Exercises the canonical fake
(:class:`tests.fakes.FakeSlackConnector`) AND the real implementation
(:class:`kairix.connectors.slack.SlackConnector`) through the same
:class:`~kairix.core.protocols.SourceConnector` Protocol assertions.
F43 requires this pairing — without it the fake can drift from the
real wire (or vice versa) and the production path silently diverges
from what BDD / unit tests measure.

Real-impl path is driven against a :class:`SlackWebClient` backed by an
:class:`httpx.MockTransport`-backed :class:`httpx.Client`; no real
Slack call is ever made.

Sabotage proofs (executed by the agent, mutate → confirm fail → restore):

  * **DM sensitivity** — mutated ``_CHANNEL_KIND_TO_SENSITIVITY["im"]``
    from ``"personal"`` to ``"internal"``; confirmed
    ``test_connector_sensitivity_for_dm_is_personal`` failed because
    the DM item_id surfaced ``"internal"``; restored.
  * **Protocol surface** — temporarily renamed
    :meth:`SlackConnector.list_changes` to ``_list_changes``; confirmed
    ``test_connector_satisfies_source_connector_protocol`` (real
    branch) flipped to False; restored.
  * **F39 channel-kind routing for private channels** — mutated
    ``_CHANNEL_KIND_TO_SENSITIVITY["private_channel"]`` to
    ``"internal"``; confirmed
    ``test_connector_sensitivity_for_private_channel_is_client_confidential``
    failed; restored.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest

from kairix.connectors.slack import (
    SlackConnector,
    SlackCredentials,
    SlackWebClient,
)
from kairix.core.protocols import ChangeEvent, RawArtefact, SourceConnector
from tests.fakes import FakeSlackConnector

pytestmark = pytest.mark.contract

_PUBLIC_CHANNEL = "C0001"
_PRIVATE_CHANNEL = "C0002"
_DM_CHANNEL = "D0003"


def _seed_channels() -> list[dict[str, Any]]:
    return [
        {"id": _PUBLIC_CHANNEL, "name": "engagement-public", "kind": "public_channel"},
        {"id": _PRIVATE_CHANNEL, "name": "engagement-private", "kind": "private_channel"},
        {"id": _DM_CHANNEL, "name": "U_PEER", "kind": "im"},
    ]


def _seed_messages() -> list[dict[str, Any]]:
    return [
        {"channel_id": _PUBLIC_CHANNEL, "ts": "1715000000.000100", "user": "U_AGENT_ALPHA", "text": "public hello"},
        {"channel_id": _PRIVATE_CHANNEL, "ts": "1715100000.000100", "user": "U_AGENT_BETA", "text": "private hello"},
        {"channel_id": _DM_CHANNEL, "ts": "1715200000.000100", "user": "U_PEER", "text": "dm hello"},
    ]


def _fake_factory() -> SourceConnector:
    """Canonical fake factory — seeds three channels (one of each kind) + one message per."""
    return FakeSlackConnector(channels=_seed_channels(), messages=_seed_messages())


def _real_factory() -> SourceConnector:
    """Real-impl factory — MockTransport-backed Slack Web API stub."""

    def _stub(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "conversations.list" in url:
            return httpx.Response(
                200,
                json={
                    "ok": True,
                    "channels": [
                        {"id": _PUBLIC_CHANNEL, "name": "engagement-public", "is_member": True, "is_private": False},
                        {"id": _PRIVATE_CHANNEL, "name": "engagement-private", "is_member": True, "is_private": True},
                        {"id": _DM_CHANNEL, "user": "U_PEER", "is_member": True, "is_im": True},
                    ],
                    "response_metadata": {"next_cursor": ""},
                },
            )
        if "conversations.history" in url:
            body = bytes(request.content).decode("ascii")
            if _PUBLIC_CHANNEL in body:
                return httpx.Response(
                    200,
                    json={"ok": True, "messages": [_seed_messages()[0]], "response_metadata": {"next_cursor": ""}},
                )
            if _PRIVATE_CHANNEL in body:
                return httpx.Response(
                    200,
                    json={"ok": True, "messages": [_seed_messages()[1]], "response_metadata": {"next_cursor": ""}},
                )
            return httpx.Response(
                200,
                json={"ok": True, "messages": [_seed_messages()[2]], "response_metadata": {"next_cursor": ""}},
            )
        if "chat.getPermalink" in url:
            return httpx.Response(200, json={"ok": True, "permalink": "https://example.slack.com/archives/C/p1"})
        return httpx.Response(200, json={"ok": True})

    shared = httpx.Client(transport=httpx.MockTransport(_stub))
    creds = SlackCredentials(bot_token="xoxb-test-fake-token-value")

    def _builder(_c: SlackCredentials) -> SlackWebClient:
        return SlackWebClient(token="xoxb-test-fake-token-value", http_client=shared)

    connector = SlackConnector(credentials=creds, web_client_factory=_builder)
    # Prime the cache so fetch() works (contract pattern same as the M365 sibling).
    list(connector.list_changes(cursor=None))
    return connector


_FACTORIES: list[tuple[str, Callable[[], SourceConnector]]] = [
    ("fake", _fake_factory),
    ("real", _real_factory),
]


@pytest.mark.contract
@pytest.mark.parametrize("name,factory", _FACTORIES)
def test_connector_satisfies_source_connector_protocol(name: str, factory: Callable[[], SourceConnector]) -> None:
    """F43: both fake and real impl satisfy the runtime-checkable Protocol.

    Sabotage-proof: renaming ``list_changes`` on SlackConnector flips
    the real-impl isinstance check to False; removing the attribute
    from FakeSlackConnector flips the fake branch.
    """
    connector = factory()
    assert isinstance(connector, SourceConnector), f"{name!r} factory output is not a SourceConnector"
    assert connector.name == "slack"


@pytest.mark.contract
@pytest.mark.parametrize("name,factory", _FACTORIES)
def test_connector_list_changes_emits_change_events(name: str, factory: Callable[[], SourceConnector]) -> None:
    """Both implementations stream :class:`ChangeEvent` instances.

    Sabotage-proof: mutate the real connector to yield raw dicts and
    this loop fails the isinstance check.
    """
    connector = factory()
    events = list(connector.list_changes(cursor=None))
    assert events, f"{name!r} factory produced no events"
    for ev in events:
        assert isinstance(ev, ChangeEvent), f"{name!r} yielded a non-ChangeEvent: {ev!r}"
        assert ev.op in ("created", "modified", "deleted", "archived", "access_lost")
        assert ":" in ev.item_id, f"slack item_id must be '<channel>:<ts>', got {ev.item_id!r}"


@pytest.mark.contract
@pytest.mark.parametrize("name,factory", _FACTORIES)
def test_connector_fetch_returns_json_artefact(name: str, factory: Callable[[], SourceConnector]) -> None:
    """Both implementations satisfy the ``fetch`` -> :class:`RawArtefact` shape.

    Sabotage-proof: mutate the real impl to return a raw ``bytes`` and
    the isinstance check fails.
    """
    connector = factory()
    artefact = connector.fetch(f"{_PUBLIC_CHANNEL}:1715000000.000100")
    assert isinstance(artefact, RawArtefact)
    assert artefact.mime == "application/json"
    payload = json.loads(artefact.raw.decode("utf-8"))
    assert isinstance(payload, dict)


@pytest.mark.contract
@pytest.mark.parametrize("name,factory", _FACTORIES)
def test_connector_source_link_round_trips_to_slack(name: str, factory: Callable[[], SourceConnector]) -> None:
    """``source_link`` returns a slack-rooted URL on both impls.

    Sabotage-proof: mutate the real impl to return an empty string —
    both ``startswith`` assertions then fail.
    """
    connector = factory()
    link = connector.source_link(f"{_PUBLIC_CHANNEL}:1715000000.000100")
    assert link.startswith(("https://", "slack://")), f"{name!r} produced unexpected link: {link!r}"


@pytest.mark.contract
@pytest.mark.parametrize("name,factory", _FACTORIES)
def test_connector_sensitivity_for_dm_is_personal(name: str, factory: Callable[[], SourceConnector]) -> None:
    """DMs always emit the ``personal`` tier per slack.md §1.

    Sabotage proof #5 from the dispatch brief: mutating the real
    ``_CHANNEL_KIND_TO_SENSITIVITY["im"]`` mapping from ``"personal"``
    to any other value makes this assertion fail.
    """
    connector = factory()
    tier = connector.sensitivity_for(f"{_DM_CHANNEL}:1715200000.000100")
    assert tier == "personal", f"{name!r} mapped a DM to {tier!r}; F39 contract requires 'personal'"


@pytest.mark.contract
@pytest.mark.parametrize("name,factory", _FACTORIES)
def test_connector_sensitivity_for_public_channel_is_internal(
    name: str,
    factory: Callable[[], SourceConnector],
) -> None:
    """Public channels emit the ``internal`` tier per slack.md §1.

    Sabotage-proof: mutate the public_channel mapping to "personal" and
    this assertion fails.
    """
    connector = factory()
    tier = connector.sensitivity_for(f"{_PUBLIC_CHANNEL}:1715000000.000100")
    assert tier == "internal"


@pytest.mark.contract
@pytest.mark.parametrize("name,factory", _FACTORIES)
def test_connector_sensitivity_for_private_channel_is_client_confidential(
    name: str, factory: Callable[[], SourceConnector]
) -> None:
    """Private channels emit ``client-confidential`` per slack.md §1.

    Sabotage proof from the dispatch brief — verified by mutating the
    real-impl mapping.
    """
    connector = factory()
    tier = connector.sensitivity_for(f"{_PRIVATE_CHANNEL}:1715100000.000100")
    assert tier == "client-confidential"

"""Contract test for the M365 email-headers connector plugin (F43).

Exercises the canonical fake
(:class:`tests.fakes.FakeM365EmailHeadersConnector`) AND the real
implementation
(:class:`kairix.connectors.m365_email_headers.M365EmailHeadersConnector`)
through the same :class:`~kairix.core.protocols.SourceConnector`
Protocol assertions. F43 requires this pairing — without it the fake
can drift away from the real wire (or vice versa) and the production
path silently diverges from what BDD / unit tests measure.

Real-impl path is driven against an :class:`httpx.MockTransport`-backed
Graph stub; no real network call is ever made.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest

from kairix.connectors.m365_email_headers import (
    M365EmailHeadersConnector,
    M365GraphClient,
)
from kairix.connectors.m365_email_headers.connector import M365Credentials
from kairix.core.protocols import ChangeEvent, RawArtefact, SourceConnector
from kairix.transport.auth.oauth2_client_creds import OAuth2ClientCredsAuth
from tests.fakes import FakeM365EmailHeadersConnector


def _envelopes() -> list[dict[str, Any]]:
    return [
        {
            "id": "msg-alpha",
            "from": {"emailAddress": {"address": "agent-alpha@example.com"}},
            "toRecipients": [{"emailAddress": {"address": "agent-beta@example.com"}}],
            "ccRecipients": [],
            "subject": "Project alpha",
            "sentDateTime": "2026-05-22T10:00:00Z",
            "receivedDateTime": "2026-05-22T10:00:01Z",
        },
        {
            "id": "msg-bravo",
            "from": {"emailAddress": {"address": "agent-beta@example.com"}},
            "toRecipients": [{"emailAddress": {"address": "agent-alpha@example.com"}}],
            "ccRecipients": [],
            "subject": "Re: Project alpha",
            "sentDateTime": "2026-05-22T11:00:00Z",
            "receivedDateTime": "2026-05-22T11:00:01Z",
        },
    ]


def _fake_factory() -> SourceConnector:
    """Canonical fake factory — seeds two envelopes."""
    return FakeM365EmailHeadersConnector(envelopes=_envelopes())


def _real_factory() -> SourceConnector:
    """Real-impl factory — MockTransport-backed Graph stub."""
    envelopes = _envelopes()

    def _stub(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "/oauth2/v2.0/token" in url:
            return httpx.Response(200, json={"access_token": "fake-bearer", "expires_in": 3600, "token_type": "Bearer"})
        return httpx.Response(
            200,
            json={
                "value": envelopes,
                "@odata.deltaLink": "https://graph.microsoft.com/v1.0/users/agent-alpha@example.com/messages/delta?$deltatoken=tok",
            },
        )

    shared = httpx.Client(transport=httpx.MockTransport(_stub))
    auth = OAuth2ClientCredsAuth(
        tenant_id="fake-tenant",
        client_id="fake-client",
        client_secret="fake-secret-value",  # pragma: allowlist secret — test fixture
        scope="https://graph.microsoft.com/.default",
        http_client=shared,
    )
    connector = M365EmailHeadersConnector(
        user_principal_name="agent-alpha@example.com",
        credentials=M365Credentials(
            tenant_id="fake-tenant",
            client_id="fake-client",
            client_secret="fake-secret-value",  # pragma: allowlist secret — test fixture
        ),
        auth=auth,
        client_builder=lambda a, u: M365GraphClient(user_principal_name=u, auth=a, http_client=shared),
    )
    # Prime the cache so fetch() works (contract pattern same as obsidian).
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

    Sabotage-proof: removing ``list_changes`` from
    :class:`M365EmailHeadersConnector` flips the real-impl isinstance
    check to False; deleting the attribute from
    :class:`FakeM365EmailHeadersConnector` flips the fake check to False.
    """
    connector = factory()
    assert isinstance(connector, SourceConnector), f"{name!r} factory output is not a SourceConnector"
    assert connector.name == "m365_email_headers"


@pytest.mark.contract
@pytest.mark.parametrize("name,factory", _FACTORIES)
def test_connector_list_changes_returns_change_events(name: str, factory: Callable[[], SourceConnector]) -> None:
    """Both implementations stream :class:`ChangeEvent` instances.

    Sabotage-proof: the real impl mutated to return ``[None]`` from
    ``list_changes`` flunks the isinstance loop below; the fake
    mutated to yield ``{"op": "created"}`` dicts flunks the same loop.
    """
    connector = factory()
    events = list(connector.list_changes(cursor=None))
    assert events, f"{name!r} factory produced no events"
    for ev in events:
        assert isinstance(ev, ChangeEvent), f"{name!r} yielded a non-ChangeEvent: {ev!r}"
        assert ev.op in ("created", "modified", "deleted")


@pytest.mark.contract
@pytest.mark.parametrize("name,factory", _FACTORIES)
def test_connector_fetch_returns_header_only_json_artefact(name: str, factory: Callable[[], SourceConnector]) -> None:
    """Both implementations satisfy the ``fetch`` -> :class:`RawArtefact` shape
    AND the artefact carries NO body content per ADR-004.

    Sabotage-proof: adding a ``body`` key to the JSON payload makes the
    forbidden-key assertion below fail for the real impl.
    """
    connector = factory()
    artefact = connector.fetch("msg-alpha")
    assert isinstance(artefact, RawArtefact)
    assert artefact.mime == "application/json"
    payload = json.loads(artefact.raw.decode("utf-8"))
    forbidden = {"body", "bodyPreview", "uniqueBody"}
    leaks = set(payload.keys()) & forbidden
    assert not leaks, f"{name!r} artefact leaked body fields: {leaks!r}"


@pytest.mark.contract
@pytest.mark.parametrize("name,factory", _FACTORIES)
def test_connector_source_link_round_trips_to_outlook(name: str, factory: Callable[[], SourceConnector]) -> None:
    """``source_link`` returns an outlook.office.com URL on both impls.

    Sabotage-proof: hard-code the real impl to return an empty string —
    both ``startswith`` assertions then fail.
    """
    connector = factory()
    link = connector.source_link("msg-alpha")
    assert link.startswith("https://outlook.office.com/"), f"{name!r} produced unexpected link: {link!r}"
    assert "msg-alpha" in link, f"{name!r} link does not carry item_id: {link!r}"


@pytest.mark.contract
@pytest.mark.parametrize("name,factory", _FACTORIES)
def test_connector_sensitivity_is_personal_tier(name: str, factory: Callable[[], SourceConnector]) -> None:
    """``sensitivity_for`` returns the locked ``personal`` tier per ADR-004 + ADR-005.

    Sabotage-proof: mutate the real impl to return ``"public"`` — the
    assertion below fails because the connector's tier is locked.
    """
    connector = factory()
    tier = connector.sensitivity_for("msg-alpha")
    assert tier == "personal", f"{name!r} returned unexpected sensitivity: {tier!r}"

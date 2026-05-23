"""Step definitions for connector_m365_email_headers.feature.

Drives the real :class:`kairix.connectors.m365_email_headers.M365EmailHeadersConnector`
against an :class:`httpx.MockTransport`-backed Graph client. No real
network call — the stub records every URL the connector requests so
the body-content-not-fetched scenario can pin the $select projection
shape.

Per F46, this step file reaches the connector through ``make_connector``
or through a direct constructor + the production
:class:`OAuth2ClientCredsAuth` helper — both are sanctioned (depth ≤ 2)
because the connector itself is a Protocol-compliant leaf.

F1-clean: no @patch / kairix module-attribute substitution.
F2-clean: no KAIRIX_* env-var manipulation.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

import httpx
import pytest
from pytest_bdd import given, parsers, then, when

from kairix.connectors.m365_email_headers import (
    M365EmailHeadersConnector,
    M365GraphClient,
)
from kairix.connectors.m365_email_headers.connector import M365Credentials
from kairix.core.protocols import ChangeEvent
from kairix.transport.auth.oauth2_client_creds import OAuth2ClientCredsAuth

pytestmark = pytest.mark.bdd


# ---------------------------------------------------------------------------
# Helpers — stub Graph endpoint that records every requested URL
# ---------------------------------------------------------------------------


def _three_header_messages_payload() -> dict[str, Any]:
    """One Graph delta page with three header-only messages + a deltaLink."""
    return {
        "value": [
            {
                "id": "msg-1",
                "from": {"emailAddress": {"address": "agent-alpha@example.com"}},
                "toRecipients": [{"emailAddress": {"address": "agent-beta@example.com"}}],
                "ccRecipients": [],
                "subject": "Project status",
                "sentDateTime": "2026-05-22T10:00:00Z",
                "receivedDateTime": "2026-05-22T10:00:01Z",
            },
            {
                "id": "msg-2",
                "from": {"emailAddress": {"address": "agent-beta@example.com"}},
                "toRecipients": [{"emailAddress": {"address": "agent-alpha@example.com"}}],
                "ccRecipients": [{"emailAddress": {"address": "agent-gamma@example.com"}}],
                "subject": "Re: Project status",
                "sentDateTime": "2026-05-22T11:00:00Z",
                "receivedDateTime": "2026-05-22T11:00:01Z",
            },
            {
                "id": "msg-3",
                "from": {"emailAddress": {"address": "agent-gamma@example.com"}},
                "toRecipients": [
                    {"emailAddress": {"address": "agent-alpha@example.com"}},
                    {"emailAddress": {"address": "agent-beta@example.com"}},
                ],
                "ccRecipients": [],
                "subject": "Re: Re: Project status",
                "sentDateTime": "2026-05-22T12:00:00Z",
                "receivedDateTime": "2026-05-22T12:00:01Z",
            },
        ],
        "@odata.deltaLink": "https://graph.microsoft.com/v1.0/users/agent-alpha@example.com/messages/delta?$deltatoken=tok-1",
    }


@dataclass
class _Ctx:
    """Per-scenario context — no module-level mutable state."""

    requested_urls: list[str] = field(default_factory=list)
    connector: M365EmailHeadersConnector | None = None
    events: list[ChangeEvent] = field(default_factory=list)


@pytest.fixture
def m365_ctx() -> _Ctx:
    return _Ctx()


def _build_connector_with_stubbed_graph(ctx: _Ctx) -> M365EmailHeadersConnector:
    """Construct the real connector wired to a recording stub Graph endpoint.

    The OAuth2 helper is exercised against a stub token endpoint that
    returns a fresh token. The Graph client is exercised against the
    same stub returning the three-message payload for any delta URL.
    """

    def _stub_handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        # OAuth2 token endpoint — return a fresh bearer + 1h expiry.
        if "/oauth2/v2.0/token" in url:
            return httpx.Response(
                200,
                json={"access_token": "fake-bearer-token-value", "expires_in": 3600, "token_type": "Bearer"},
            )
        # Graph delta endpoint — record the URL and return three messages.
        ctx.requested_urls.append(url)
        return httpx.Response(200, json=_three_header_messages_payload())

    transport = httpx.MockTransport(_stub_handler)
    shared_client = httpx.Client(transport=transport)

    auth = OAuth2ClientCredsAuth(
        tenant_id="fake-tenant",
        client_id="fake-client-id",
        client_secret="fake-client-secret-value",  # pragma: allowlist secret — test fixture
        scope="https://graph.microsoft.com/.default",
        http_client=shared_client,
    )

    def _client_builder(resolved_auth: OAuth2ClientCredsAuth, upn: str) -> M365GraphClient:
        return M365GraphClient(
            user_principal_name=upn,
            auth=resolved_auth,
            http_client=shared_client,
        )

    return M365EmailHeadersConnector(
        user_principal_name="agent-alpha@example.com",
        credentials=M365Credentials(
            tenant_id="fake-tenant",
            client_id="fake-client-id",
            client_secret="fake-client-secret-value",  # pragma: allowlist secret — test fixture
        ),
        auth=auth,
        client_builder=_client_builder,
    )


# ---------------------------------------------------------------------------
# Givens
# ---------------------------------------------------------------------------


@given(parsers.parse("a stubbed Microsoft Graph endpoint that returns three header-only messages"))
def _given_three_messages(m365_ctx: _Ctx) -> None:
    m365_ctx.connector = _build_connector_with_stubbed_graph(m365_ctx)


@given(parsers.parse("a stubbed Microsoft Graph endpoint that records every requested URL"))
def _given_recording_endpoint(m365_ctx: _Ctx) -> None:
    m365_ctx.connector = _build_connector_with_stubbed_graph(m365_ctx)


# ---------------------------------------------------------------------------
# Whens
# ---------------------------------------------------------------------------


@when(parsers.parse("the operator runs the m365_email_headers connector list_changes with no cursor"))
def _when_list_changes(m365_ctx: _Ctx) -> None:
    assert m365_ctx.connector is not None, "Given step must run before When"
    m365_ctx.events = list(m365_ctx.connector.list_changes(cursor=None))


# ---------------------------------------------------------------------------
# Thens
# ---------------------------------------------------------------------------


@then("three created change events are emitted")
def _three_created_events(m365_ctx: _Ctx) -> None:
    assert len(m365_ctx.events) == 3, f"expected 3 events, got {len(m365_ctx.events)}: {m365_ctx.events!r}"
    for ev in m365_ctx.events:
        assert ev.op == "created", f"expected 'created' op, got {ev.op!r} on {ev!r}"


@then("every change event carries an ISO-8601 modified_at timestamp")
def _every_event_has_iso(m365_ctx: _Ctx) -> None:
    for ev in m365_ctx.events:
        assert ev.modified_at, f"event {ev!r} missing modified_at"
        # ISO-8601 UTC ends in Z or carries a +HH:MM offset
        assert ev.modified_at.endswith("Z") or "+" in ev.modified_at, (
            f"event {ev!r} modified_at not ISO-8601: {ev.modified_at!r}"
        )


@then("every change event's sensitivity tier is personal")
def _every_event_personal_tier(m365_ctx: _Ctx) -> None:
    for ev in m365_ctx.events:
        tier = ev.metadata.get("sensitivity")
        assert tier == "personal", f"event {ev.item_id!r} sensitivity is not personal: {tier!r}"


def _assert_recorded(urls: Iterable[str]) -> str:
    """Return the first recorded URL, or fail with the recorded list."""
    url_list = list(urls)
    assert url_list, f"no Graph URL was recorded; got urls={url_list!r}"
    return url_list[0]


@then("the recorded Graph URL contains a $select projection")
def _recorded_has_select(m365_ctx: _Ctx) -> None:
    url = _assert_recorded(m365_ctx.requested_urls)
    assert "$select=" in url, f"Graph URL missing $select projection: {url!r}"


@then("the recorded Graph URL projection does not contain body")
def _recorded_no_body(m365_ctx: _Ctx) -> None:
    url = _assert_recorded(m365_ctx.requested_urls)
    # The $select projection segment ends at the next & or end-of-url
    select_part = url.split("$select=", 1)[1].split("&", 1)[0]
    fields = {f.strip() for f in select_part.split(",")}
    assert "body" not in fields, f"$select projection contains 'body': {select_part!r}"


@then("the recorded Graph URL projection does not contain bodyPreview")
def _recorded_no_body_preview(m365_ctx: _Ctx) -> None:
    url = _assert_recorded(m365_ctx.requested_urls)
    select_part = url.split("$select=", 1)[1].split("&", 1)[0]
    fields = {f.strip() for f in select_part.split(",")}
    assert "bodyPreview" not in fields, f"$select projection contains 'bodyPreview': {select_part!r}"


@then("the recorded Graph URL projection does not contain uniqueBody")
def _recorded_no_unique_body(m365_ctx: _Ctx) -> None:
    url = _assert_recorded(m365_ctx.requested_urls)
    select_part = url.split("$select=", 1)[1].split("&", 1)[0]
    fields = {f.strip() for f in select_part.split(",")}
    assert "uniqueBody" not in fields, f"$select projection contains 'uniqueBody': {select_part!r}"

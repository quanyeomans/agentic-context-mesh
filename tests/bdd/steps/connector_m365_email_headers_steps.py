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
    """One Graph delta page with three header-only messages + a folder-scoped deltaLink."""
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
        "@odata.deltaLink": (
            "https://graph.microsoft.com/v1.0/users/agent-alpha@example.com"
            "/mailFolders/AAMkAGFmYWtl-inbox/messages/delta?$deltatoken=tok-1"
        ),
    }


def _single_inbox_folder_payload() -> dict[str, Any]:
    """One mailFolders response carrying a single ``inbox`` well-known folder.

    #380: the connector enumerates folders before driving per-folder
    delta; the BDD scenarios pin behaviour against a single-folder
    mailbox to keep the assertions focused on header-only + cursor
    behaviour.
    """
    return {
        "value": [
            {
                "id": "AAMkAGFmYWtl-inbox",
                "displayName": "Inbox",
                "wellKnownName": "inbox",
            },
        ],
    }


@dataclass
class _Ctx:
    """Per-scenario context — no module-level mutable state."""

    requested_urls: list[str] = field(default_factory=list)
    connector: M365EmailHeadersConnector | None = None
    events: list[ChangeEvent] = field(default_factory=list)
    allowlist: list[str] | None = None
    failing_folder_ids: set[str] = field(default_factory=set)


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
        # mailFolders enumeration (#380) — return a single inbox folder.
        if "/mailFolders" in url and "/messages/delta" not in url:
            return httpx.Response(200, json=_single_inbox_folder_payload())
        # Graph per-folder delta endpoint — record the URL and return
        # three messages.
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


# ---------------------------------------------------------------------------
# Folder-scoped delta scenarios (#380)
# ---------------------------------------------------------------------------


_BDD_FOLDERS = [
    {"id": "AAMkAGFmYWtl-inbox", "displayName": "Inbox", "wellKnownName": "inbox"},
    {"id": "AAMkAGFmYWtl-sent", "displayName": "Sent Items", "wellKnownName": "sentitems"},
    {"id": "AAMkAGFmYWtl-archive", "displayName": "Archive", "wellKnownName": "archive"},
]


def _five_envelopes_for(folder_id: str) -> list[dict[str, Any]]:
    """Five header-only envelopes seeded per folder."""
    return [
        {
            "id": f"{folder_id}-msg-{idx}",
            "from": {"emailAddress": {"address": f"sender-{idx}@example.com"}},
            "toRecipients": [{"emailAddress": {"address": "agent-alpha@example.com"}}],
            "ccRecipients": [],
            "subject": f"{folder_id} subject {idx}",
            "sentDateTime": f"2026-05-22T10:{idx:02d}:00Z",
            "receivedDateTime": f"2026-05-22T10:{idx:02d}:01Z",
        }
        for idx in range(1, 6)
    ]


def _build_multi_folder_connector(ctx: _Ctx) -> M365EmailHeadersConnector:
    """Compose the real connector against a three-folder MockTransport stub.

    F47 — real plugin construction; ``client_builder`` is the standard
    DI seam already in production use.
    """

    def _handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "/oauth2/v2.0/token" in url:
            return httpx.Response(
                200,
                json={"access_token": "fake-bearer-token-value", "expires_in": 3600, "token_type": "Bearer"},
            )
        if "/mailFolders" in url and "/messages/delta" not in url:
            return httpx.Response(200, json={"value": list(_BDD_FOLDERS)})
        ctx.requested_urls.append(url)
        for folder in _BDD_FOLDERS:
            folder_id = folder["id"]
            if f"/mailFolders/{folder_id}/messages/delta" in url:
                if folder_id in ctx.failing_folder_ids:
                    return httpx.Response(500, json={"error": {"code": "InternalServerError"}})
                return httpx.Response(
                    200,
                    json={
                        "value": _five_envelopes_for(folder_id),
                        "@odata.deltaLink": (
                            f"https://graph.microsoft.com/v1.0/users/agent-alpha@example.com"
                            f"/mailFolders/{folder_id}/messages/delta?$deltatoken={folder_id}-tok"
                        ),
                    },
                )
        return httpx.Response(404, json={"error": {"code": "UnknownFolder"}})

    shared = httpx.Client(transport=httpx.MockTransport(_handler))
    auth = OAuth2ClientCredsAuth(
        tenant_id="fake-tenant",
        client_id="fake-client-id",
        client_secret="fake-client-secret-value",  # pragma: allowlist secret — test fixture
        scope="https://graph.microsoft.com/.default",
        http_client=shared,
    )

    def _builder(resolved_auth: OAuth2ClientCredsAuth, upn: str) -> M365GraphClient:
        return M365GraphClient(
            user_principal_name=upn,
            auth=resolved_auth,
            http_client=shared,
            sleep_fn=lambda _s: None,
        )

    return M365EmailHeadersConnector(
        user_principal_name="agent-alpha@example.com",
        credentials=M365Credentials(
            tenant_id="fake-tenant",
            client_id="fake-client-id",
            client_secret="fake-client-secret-value",  # pragma: allowlist secret — test fixture
        ),
        auth=auth,
        client_builder=_builder,
        folders_allowlist=ctx.allowlist,
    )


@given("a stubbed Microsoft Graph endpoint with three folders carrying five messages each")
def _given_three_folders(m365_ctx: _Ctx) -> None:
    m365_ctx.connector = _build_multi_folder_connector(m365_ctx)


@given("the operator configures the folders_allowlist to inbox and archive")
def _given_allowlist_inbox_archive(m365_ctx: _Ctx) -> None:
    m365_ctx.allowlist = ["inbox", "archive"]
    # Re-build the connector so the allowlist is wired through.
    m365_ctx.connector = _build_multi_folder_connector(m365_ctx)


@given("the Inbox folder returns repeated server errors")
def _given_inbox_5xx(m365_ctx: _Ctx) -> None:
    m365_ctx.failing_folder_ids.add("AAMkAGFmYWtl-inbox")
    # Re-build the connector so the failure injection takes effect.
    m365_ctx.connector = _build_multi_folder_connector(m365_ctx)


@then("fifteen created change events are emitted across all folders")
def _fifteen_events(m365_ctx: _Ctx) -> None:
    assert len(m365_ctx.events) == 15, f"expected 15 events (3 folders x 5 messages), got {len(m365_ctx.events)}"
    folders_seen = {ev.metadata.get("folder") for ev in m365_ctx.events}
    assert folders_seen == {"Inbox", "Sent Items", "Archive"}, (
        f"expected events from every folder; got {folders_seen!r}"
    )


@then("every recorded Graph URL is folder-scoped")
def _every_url_folder_scoped(m365_ctx: _Ctx) -> None:
    assert m365_ctx.requested_urls, "expected per-folder delta URLs to be recorded"
    for url in m365_ctx.requested_urls:
        assert "/mailFolders/" in url and "/messages/delta" in url, (
            f"recorded URL is not folder-scoped per #380: {url!r}"
        )


@then("ten created change events are emitted from only the allowed folders")
def _ten_events_only_allowed(m365_ctx: _Ctx) -> None:
    assert len(m365_ctx.events) == 10, (
        f"expected 10 events (2 allowed folders x 5 messages), got {len(m365_ctx.events)}"
    )
    folders_seen = {ev.metadata.get("folder") for ev in m365_ctx.events}
    assert folders_seen == {"Inbox", "Archive"}, f"expected only Inbox + Archive; got {folders_seen!r}"


@then("no change events come from the Sent Items folder")
def _no_events_from_sent_items(m365_ctx: _Ctx) -> None:
    for ev in m365_ctx.events:
        assert not ev.item_id.startswith("AAMkAGFmYWtl-sent-"), (
            f"allowlist must exclude Sent Items; cross-folder leak: {ev.item_id!r}"
        )


@then("ten created change events are emitted from the surviving folders")
def _ten_events_surviving(m365_ctx: _Ctx) -> None:
    assert len(m365_ctx.events) == 10, (
        f"failed folder must not poison siblings; expected 10 events from 2 surviving folders, "
        f"got {len(m365_ctx.events)}"
    )
    for ev in m365_ctx.events:
        assert not ev.item_id.startswith("AAMkAGFmYWtl-inbox-"), (
            f"failed Inbox folder should not surface events; leak: {ev.item_id!r}"
        )


@then("the surviving folders advance their cursors")
def _surviving_folders_advance(m365_ctx: _Ctx) -> None:
    assert m365_ctx.connector is not None
    cursor = m365_ctx.connector.next_cursor()
    assert cursor is not None, "surviving folders should record their deltaLinks"
    import json as _json

    decoded = _json.loads(cursor)
    assert "AAMkAGFmYWtl-sent" in decoded, f"Sent Items should advance; got {decoded!r}"
    assert "AAMkAGFmYWtl-archive" in decoded, f"Archive should advance; got {decoded!r}"

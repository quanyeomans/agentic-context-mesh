"""Step definitions for connector_sharepoint.feature.

Drives the real :class:`kairix.connectors.sharepoint.SharePointConnector`
against an :class:`httpx.MockTransport`-backed Graph stub. No real
network call — the stub returns one drive's delta page so the
behaviour assertions can pin the typed ChangeEvent shape and the
per-drive cursor encoding.

Per F46, this step file reaches the connector through the real
constructor + the production
:class:`OAuth2ClientCredsAuth` helper (depth ≤ 2). Direct construction
is permitted in BDD step files when the target is a Protocol-compliant
leaf such as ``SharePointConnector``.

F1-clean: no @patch / kairix module-attribute substitution.
F2-clean: no KAIRIX_* env-var manipulation.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import httpx
import pytest
from pytest_bdd import given, parsers, then, when

from kairix.connectors.sharepoint import (
    SharePointConnector,
    SharePointCredentials,
    SharePointDriveSpec,
    SharePointGraphClient,
)
from kairix.core.protocols import ChangeEvent
from kairix.transport.auth.oauth2_client_creds import OAuth2ClientCredsAuth

pytestmark = pytest.mark.bdd

_DRIVE_ID = "b!drive-fixture"
_DELTA_TOKEN = "https://graph.microsoft.com/v1.0/drives/b!drive-fixture/root/delta?$deltatoken=tok-1"


def _one_pdf_envelope_page() -> dict[str, Any]:
    """One Graph drive-delta page with a single PDF envelope + a deltaLink."""
    return {
        "@odata.context": f"https://graph.microsoft.com/v1.0/$metadata#drives/{_DRIVE_ID}/root/delta",
        "value": [
            {
                "id": "01ITEMPDFFIXTURE",
                "name": "agent-handbook.pdf",
                "size": 87231,
                "lastModifiedDateTime": "2026-05-22T10:00:00Z",
                "webUrl": "https://contoso.sharepoint.com/sites/team/Documents/agent-handbook.pdf",
                "file": {"mimeType": "application/pdf"},
                "parentReference": {"driveId": _DRIVE_ID},
            }
        ],
        "@odata.deltaLink": _DELTA_TOKEN,
    }


@dataclass
class _Ctx:
    """Per-scenario context — no module-level mutable state."""

    requested_urls: list[str] = field(default_factory=list)
    connector: SharePointConnector | None = None
    events: list[ChangeEvent] = field(default_factory=list)


@pytest.fixture
def sharepoint_ctx() -> _Ctx:
    return _Ctx()


def _build_connector_with_stubbed_graph(ctx: _Ctx) -> SharePointConnector:
    """Construct the real connector wired to a recording stub Graph endpoint.

    The OAuth2 helper exchanges client-credentials against the same stub
    transport; the Graph client returns the one-pdf delta page for any
    drive URL.
    """

    def _stub_handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "/oauth2/v2.0/token" in url:
            return httpx.Response(
                200,
                json={"access_token": "fake-bearer-token-value", "expires_in": 3600, "token_type": "Bearer"},
            )
        ctx.requested_urls.append(url)
        return httpx.Response(200, json=_one_pdf_envelope_page())

    transport = httpx.MockTransport(_stub_handler)
    shared_client = httpx.Client(transport=transport)

    auth = OAuth2ClientCredsAuth(
        tenant_id="fake-tenant",
        client_id="fake-client-id",
        client_secret="fake-client-secret-value",  # pragma: allowlist secret — test fixture
        scope="https://graph.microsoft.com/.default",
        http_client=shared_client,
    )

    def _client_builder(resolved_auth: OAuth2ClientCredsAuth) -> SharePointGraphClient:
        return SharePointGraphClient(auth=resolved_auth, http_client=shared_client)

    return SharePointConnector(
        drives=[SharePointDriveSpec(drive_id=_DRIVE_ID)],
        credentials=SharePointCredentials(
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


@given(parsers.parse("a stubbed Microsoft Graph endpoint that returns one configured drive with a sample pdf envelope"))
def _given_one_pdf(sharepoint_ctx: _Ctx) -> None:
    sharepoint_ctx.connector = _build_connector_with_stubbed_graph(sharepoint_ctx)


# ---------------------------------------------------------------------------
# Whens
# ---------------------------------------------------------------------------


@when(parsers.parse("the operator runs the sharepoint connector list_changes with no cursor"))
def _when_list_changes(sharepoint_ctx: _Ctx) -> None:
    assert sharepoint_ctx.connector is not None, "Given step must run before When"
    sharepoint_ctx.events = list(sharepoint_ctx.connector.list_changes(cursor=None))


# ---------------------------------------------------------------------------
# Thens
# ---------------------------------------------------------------------------


@then("one created change event is emitted")
def _one_created_event(sharepoint_ctx: _Ctx) -> None:
    events = sharepoint_ctx.events
    assert len(events) == 1, f"expected 1 event, got {len(events)}: {events!r}"
    assert events[0].op == "created", f"expected created op, got {events[0]!r}"


@then("the change event carries an ISO-8601 modified_at timestamp")
def _event_has_iso(sharepoint_ctx: _Ctx) -> None:
    event = sharepoint_ctx.events[0]
    assert event.modified_at, f"event {event!r} missing modified_at"
    assert event.modified_at.endswith("Z") or "+" in event.modified_at, (
        f"event {event!r} modified_at not ISO-8601: {event.modified_at!r}"
    )


@then("the change event's sensitivity tier is internal")
def _event_internal_tier(sharepoint_ctx: _Ctx) -> None:
    event = sharepoint_ctx.events[0]
    tier = event.metadata.get("sensitivity")
    assert tier == "internal", f"event {event.item_id!r} sensitivity is not internal: {tier!r}"


@then("the change event metadata records the source drive id")
def _event_records_drive(sharepoint_ctx: _Ctx) -> None:
    event = sharepoint_ctx.events[0]
    assert event.metadata.get("drive_id") == _DRIVE_ID, (
        f"event {event.item_id!r} drive_id metadata is wrong: {event.metadata.get('drive_id')!r}"
    )


@then("the connector exposes a non-empty next cursor")
def _connector_has_cursor(sharepoint_ctx: _Ctx) -> None:
    assert sharepoint_ctx.connector is not None
    cursor = sharepoint_ctx.connector.next_cursor()
    assert cursor, f"expected non-empty next cursor, got {cursor!r}"


@then("the next cursor encodes a per-drive delta link map")
def _cursor_encodes_drive_map(sharepoint_ctx: _Ctx) -> None:
    assert sharepoint_ctx.connector is not None
    cursor = sharepoint_ctx.connector.next_cursor()
    assert cursor is not None
    parsed = json.loads(cursor)
    assert isinstance(parsed, dict), f"cursor must decode to dict, got {parsed!r}"
    assert _DRIVE_ID in parsed, f"cursor map missing drive {_DRIVE_ID!r}: {parsed!r}"
    assert "deltatoken" in parsed[_DRIVE_ID], f"cursor map value missing delta token: {parsed[_DRIVE_ID]!r}"

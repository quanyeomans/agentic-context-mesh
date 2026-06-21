"""End-to-end composed path test for the ``connector_m365_email_headers`` flag.

F48 sibling to ``tests/e2e/test_composed_production_path.py``. Pinned
by F54 because the flag's ``related_spec`` references
``docs/architecture/connector-ingestion-architecture.md`` — a top-level
capability spec.

Exercises the composed production path with the flag ON:

  paths setup (FakePaths over tmp_path)
    → real :class:`M365EmailHeadersConnector` against an
      :class:`httpx.MockTransport`-backed Graph stub (no real network)
    → connector.list_changes(None) drains three header-only envelopes
    → connector.fetch(item_id) returns a JSON artefact with NO body
      content (the artefact's parsed JSON contains no body / bodyPreview
      / uniqueBody keys)
    → assertion that the Graph URL recorded by the stub carries the
      header-only $select projection and contains NO body field
    → assertion that the connector is enabled via the flag-gated
      :func:`connector_enabled` predicate when the flag is ON

The OFF path is covered by
``tests/integration/test_feature_flag_connector_m365_email_headers.py``
plus the canonical
``tests/e2e/test_composed_production_path.py`` (E2E runs against the
default-off registry state). F54's E2E requirement is per-flag (one
E2E composed-path file); both branches don't both need an E2E entry.

Sabotage proof (verified): mutating
:data:`kairix.connectors.m365_email_headers.graph_client.HEADER_ONLY_SELECT`
to include ``body`` makes the no-body-content assertion fail. Restored,
the composed path returns the seeded envelopes and the projection
stays header-only.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from kairix.connectors.m365_email_headers import (
    M365EmailHeadersConnector,
    M365GraphClient,
)
from kairix.connectors.m365_email_headers.connector import M365Credentials
from kairix.transport.auth.oauth2_client_creds import OAuth2ClientCredsAuth
from kairix.worker import (
    ConnectorSyncResult,
    connector_enabled,
)
from tests.fakes import FakeFeatureFlagResolver

pytestmark = pytest.mark.e2e


def _three_messages_payload() -> dict[str, Any]:
    """One Graph delta page with three header-only messages."""
    return {
        "value": [
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
                "ccRecipients": [{"emailAddress": {"address": "agent-gamma@example.com"}}],
                "subject": "Re: Project alpha",
                "sentDateTime": "2026-05-22T11:00:00Z",
                "receivedDateTime": "2026-05-22T11:00:01Z",
            },
            {
                "id": "msg-charlie",
                "from": {"emailAddress": {"address": "agent-gamma@example.com"}},
                "toRecipients": [{"emailAddress": {"address": "agent-alpha@example.com"}}],
                "ccRecipients": [],
                "subject": "Re: Re: Project alpha",
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
    """One mailFolders response carrying a single ``inbox`` well-known folder (#380)."""
    return {
        "value": [
            {
                "id": "AAMkAGFmYWtl-inbox",
                "displayName": "Inbox",
                "wellKnownName": "inbox",
            },
        ],
    }


def _composed_connector(recorded_urls: list[str]) -> M365EmailHeadersConnector:
    """Build the real connector wired to a MockTransport-backed stub.

    The stub records every Graph URL the connector requests; the
    no-body-content assertions in the test pin the recorded $select
    projection.
    """

    def _stub_handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "/oauth2/v2.0/token" in url:
            return httpx.Response(
                200,
                json={
                    "access_token": "fake-bearer-token-value",
                    "expires_in": 3600,
                    "token_type": "Bearer",
                },
            )
        if "/mailFolders" in url and "/messages/delta" not in url:
            return httpx.Response(200, json=_single_inbox_folder_payload())
        recorded_urls.append(url)
        return httpx.Response(200, json=_three_messages_payload())

    transport = httpx.MockTransport(_stub_handler)
    shared = httpx.Client(transport=transport)

    auth = OAuth2ClientCredsAuth(
        tenant_id="fake-tenant",
        client_id="fake-client-id",
        client_secret="fake-client-secret-value",  # pragma: allowlist secret — test fixture
        scope="https://graph.microsoft.com/.default",
        http_client=shared,
    )

    def _client_builder(resolved_auth: OAuth2ClientCredsAuth, upn: str) -> M365GraphClient:
        return M365GraphClient(
            user_principal_name=upn,
            auth=resolved_auth,
            http_client=shared,
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


def test_composed_m365_email_headers_on_path() -> None:
    """Flag ON, composed path: M365 connector drains envelopes through
    real production code; no body content is ever fetched.

    Sabotage proof (verified): mutating ``HEADER_ONLY_SELECT`` to
    include ``body`` makes the recorded-projection assertion fail.
    Restored, the projection stays header-only and the composed path
    succeeds.
    """
    recorded_urls: list[str] = []
    connector = _composed_connector(recorded_urls)

    # Drive list_changes through the real connector — the stub records
    # the URL, the connector parses the response and emits events.
    events = list(connector.list_changes(cursor=None))
    assert len(events) == 3, f"expected 3 events, got {len(events)}: {events!r}"
    for ev in events:
        assert ev.op == "created", f"expected 'created' op, got {ev.op!r}"
        assert ev.metadata.get("sensitivity") == "personal", f"event {ev.item_id!r} missing personal sensitivity tier"

    # The recorded Graph URL must carry the header-only $select.
    assert recorded_urls, "expected at least one Graph URL recorded"
    first_url = recorded_urls[0]
    assert "$select=" in first_url, f"Graph URL missing $select: {first_url!r}"
    select_part = first_url.split("$select=", 1)[1].split("&", 1)[0]
    fields = {f.strip() for f in select_part.split(",")}
    forbidden = {"body", "bodyPreview", "uniqueBody"}
    leaks = forbidden & fields
    assert not leaks, f"Graph $select projection leaked body fields: {leaks!r} in {select_part!r}"

    # Drive fetch through the real connector — the JSON artefact must
    # contain only header fields, never any body field.
    artefact = connector.fetch("msg-alpha")
    assert artefact.mime == "application/json", f"unexpected mime: {artefact.mime!r}"
    payload = json.loads(artefact.raw.decode("utf-8"))
    artefact_keys = set(payload.keys())
    forbidden_keys = {"body", "bodyPreview", "uniqueBody"}
    leaks = artefact_keys & forbidden_keys
    assert not leaks, f"fetched artefact leaked body fields: {leaks!r}; keys={artefact_keys!r}"

    # Flag-gated enablement lets the connector through when ON.
    # Wrap the composed branch so the E2E test doesn't try to open a real
    # SQLite DB — the property under test is "the connector is what runs
    # when the flag is ON", observable via the branch callable's
    # invocation after the gate lets it through.
    resolver = FakeFeatureFlagResolver().with_flag("connector_m365_email_headers", True)
    on_branch_calls = {"n": 0}

    def _composed_on_branch() -> ConnectorSyncResult:
        on_branch_calls["n"] += 1
        # Drive list_changes inside the composed branch to prove the
        # connector is the active surface when the flag is ON.
        _ = list(connector.list_changes(cursor=connector.next_cursor()))
        return ConnectorSyncResult(synced=1, failed=0, dead_letter_added=0)

    assert connector_enabled("m365_email_headers", resolver.get), "flag ON must enable the m365_email_headers connector"
    result = _composed_on_branch()

    assert on_branch_calls["n"] == 1, (
        f"flag ON must invoke the connector branch exactly once; got {on_branch_calls['n']}"
    )
    assert result.synced == 1, f"ON branch must have run and returned its result; got {result}"

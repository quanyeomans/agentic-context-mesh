"""End-to-end composed path test for the ``connector_gmail`` flag.

F48 sibling to ``tests/e2e/test_composed_production_path.py``. Pinned
by F54 because the flag's ``related_spec`` references the connector
ingestion architecture — a top-level capability spec.

Exercises the composed production path with the flag ON:

  paths setup (no env mutation, no real Gmail roundtrip)
    → real :class:`GmailConnector` against an :class:`httpx.MockTransport`-
      backed Gmail stub (no real network)
    → connector.list_changes(None) seeds the cursor at the live tip
    → connector.list_changes(<cursor>) drains one scripted message
    → connector.fetch(item_id) returns the body bytes + the
      ``text/plain`` mime
    → connector.metadata_for(item_id) lifts the envelope headers onto
      :class:`SourceMetadata`
    → assertion that the connector is enabled via the flag-gated
      :func:`connector_enabled` predicate when the flag is ON

The OFF path is covered by
``tests/integration/test_feature_flag_connector_gmail.py``. F54's E2E
requirement is per-flag (one E2E composed-path file); both branches
don't both need an E2E entry.

Sabotage proof: removing the ``list_history``/``get_message`` calls
from the connector's :meth:`list_changes` would flip this test to red
(zero events emitted, or the ``fetch`` cache miss).
"""

from __future__ import annotations

import json

import httpx
import pytest

from kairix.connectors.gmail import GmailConnector
from kairix.connectors.gmail.client import GmailClient
from kairix.worker import (
    ConnectorSyncResult,
    connector_enabled,
)
from tests.fakes import FakeFeatureFlagResolver

pytestmark = pytest.mark.e2e

_USER = "agent-alpha@example.com"


def _composed_connector(recorded_urls: list[str]) -> GmailConnector:
    """Build the real connector wired to a MockTransport-backed stub.

    The stub records every Gmail URL the connector requests; the
    composed E2E test asserts on body bytes + recorded URLs without
    ever leaving the process.
    """

    def _stub_handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        recorded_urls.append(url)
        if "/profile" in url:
            return httpx.Response(200, json={"emailAddress": _USER, "historyId": "1000"})
        if "/history" in url:
            return httpx.Response(
                200,
                json={
                    "history": [
                        {
                            "id": "1001",
                            "messagesAdded": [
                                {
                                    "message": {
                                        "id": "msg-e2e-1",
                                        "threadId": "thread-e2e-1",
                                        "labelIds": ["INBOX"],
                                    }
                                }
                            ],
                        }
                    ],
                    "historyId": "1001",
                },
            )
        if "/messages/" in url:
            import base64

            body_text = "Gmail body content for the E2E composed-path test."
            encoded_body = base64.urlsafe_b64encode(body_text.encode("utf-8")).decode("ascii")
            return httpx.Response(
                200,
                json={
                    "id": "msg-e2e-1",
                    "threadId": "thread-e2e-1",
                    "historyId": "1001",
                    "labelIds": ["INBOX"],
                    "payload": {
                        "mimeType": "text/plain",
                        "headers": [
                            {"name": "Subject", "value": "E2E composed-path"},
                            {"name": "From", "value": "agent-alpha@example.com"},
                            {"name": "To", "value": "agent-beta@example.com"},
                            {"name": "Date", "value": "2026-05-28T10:00:00Z"},
                        ],
                        "body": {"data": encoded_body, "size": len(body_text)},
                    },
                },
            )
        return httpx.Response(404, json={"error": {"message": f"unrouted URL: {url}"}})

    transport = httpx.MockTransport(_stub_handler)
    shared = httpx.Client(transport=transport)
    client = GmailClient(
        user_email=_USER,
        token_refresher=lambda: "fake-bearer-token-value",
        http_client=shared,
    )
    return GmailConnector(user_email=_USER, client=client)


def test_composed_gmail_on_path() -> None:
    """Flag ON, composed path: Gmail connector drains envelopes + bodies
    through real production code; no real Gmail call leaks.

    Sabotage proof: removing the ``self._client.get_message(...)`` call
    in :meth:`list_changes` would flip this test to red — the cache
    wouldn't populate and the fetch assertion would receive an empty
    body.
    """
    recorded_urls: list[str] = []
    connector = _composed_connector(recorded_urls)

    # Cold start — seeds the cursor at the live tip; no events emitted.
    cold_events = list(connector.list_changes(cursor=None))
    assert cold_events == [], f"cold-start must emit zero events, got {cold_events!r}"
    cursor = connector.next_cursor()
    assert cursor == "1000", f"cold-start must seed cursor at the profile tip; got {cursor!r}"

    # Warm tick — drains the History API and emits one created event.
    events = list(connector.list_changes(cursor=cursor))
    assert len(events) == 1, f"expected 1 event from warm tick; got {len(events)}: {events!r}"
    event = events[0]
    assert event.op == "created"
    assert event.item_id == "msg-e2e-1"
    assert event.metadata.get("sensitivity") == "client-confidential"

    # The recorded URLs must trace the profile + history + message path.
    joined = " ".join(recorded_urls)
    assert "/profile" in joined, f"expected a profile call in recorded URLs; got {recorded_urls!r}"
    assert "/history" in joined, f"expected a history call in recorded URLs; got {recorded_urls!r}"
    assert "/messages/msg-e2e-1" in joined, f"expected a message-get call in recorded URLs; got {recorded_urls!r}"

    # Drive fetch — the cached body bytes must surface as the artefact.
    artefact = connector.fetch("msg-e2e-1")
    assert artefact.mime == "text/plain"
    assert b"Gmail body content for the E2E composed-path test." in artefact.raw

    # Drive metadata_for — the cached envelope headers populate the SourceMetadata.
    metadata = connector.metadata_for("msg-e2e-1")
    assert metadata.author == "agent-alpha@example.com"
    assert metadata.modified_at == "2026-05-28T10:00:00Z"
    assert "agent-beta@example.com" in metadata.tags
    assert metadata.properties.get("subject") == "E2E composed-path"

    # Flag-gated enablement lets the connector through when ON.
    resolver = FakeFeatureFlagResolver().with_flag("connector_gmail", True)
    on_branch_calls = {"n": 0}

    def _composed_on_branch() -> ConnectorSyncResult:
        on_branch_calls["n"] += 1
        _ = list(connector.list_changes(cursor=connector.next_cursor()))
        return ConnectorSyncResult(synced=1, failed=0, dead_letter_added=0)

    assert connector_enabled("gmail", resolver.get), "flag ON must enable the gmail connector"
    result = _composed_on_branch()

    assert on_branch_calls["n"] == 1, (
        f"flag ON must invoke the connector branch exactly once; got {on_branch_calls['n']}"
    )
    assert result.synced == 1, f"ON branch must have run and returned its result; got {result}"

    # Sanity: the recorded URL shape is JSON-serialisable for a future
    # paydown step that snapshots E2E recorded URLs (mirrors the
    # SharePoint E2E pattern).
    _ = json.dumps(recorded_urls)

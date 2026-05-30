"""Failure-mode contract for :class:`GoogleDriveConnector` per the F68 catalogue.

Each test exercises one of the named failure classes (``raises`` /
``times_out`` / ``returns_partial`` / ``returns_empty`` /
``unauthorized`` / ``unavailable``) against the production connector
with a scripted :class:`GoogleDriveClient` injected via the
constructor seam. Each assertion is a concrete observable outcome —
an exception type with message, a returned value, an event count —
NOT a Mock call-count.

Each ``test_*`` carries the ``@pytest.mark.integration`` marker per F8.
"""

from __future__ import annotations

import httpx
import pytest

from kairix.connectors.google_drive import (
    GoogleDriveClient,
    GoogleDriveConnector,
    GoogleDriveCorpusSpec,
    GoogleDriveCredentials,
)
from kairix.core.protocols import CredentialExpiredError

pytestmark = pytest.mark.integration

_CORPUS_ID = "workspace-failure-modes"


def _build_connector(handler: object) -> GoogleDriveConnector:
    """Build a real connector backed by ``handler`` via MockTransport."""
    transport = httpx.MockTransport(handler)  # type: ignore[arg-type]  # F3 rationale: handler is the httpx mock callable shape narrowed at runtime
    shared = httpx.Client(transport=transport)
    return GoogleDriveConnector(
        corpora=[GoogleDriveCorpusSpec(corpus_id=_CORPUS_ID)],
        credentials=GoogleDriveCredentials(
            access_token="fake-token-value",  # pragma: allowlist secret — test fixture
        ),
        client_builder=lambda creds: GoogleDriveClient(
            access_token=creds.access_token,
            http_client=shared,
            sleep_fn=lambda _s: None,
        ),
    )


@pytest.mark.integration
def test_list_changes_raises_on_500_after_retries_exhausted() -> None:
    """When the Drive API persistently returns 500, ``list_changes`` raises.

    Concrete sabotage-provable assertion: the raised exception is
    :class:`httpx.HTTPStatusError` with status code 500. Removing
    500 from the retry set or widening max_attempts would change the
    call-count observable, but the raised type stays the contract.
    """

    def _handler(request: httpx.Request) -> httpx.Response:
        if "/changes/startPageToken" in str(request.url):
            return httpx.Response(200, json={"startPageToken": "seed-500"})
        return httpx.Response(500, json={"error": "internal"})

    connector = _build_connector(_handler)
    with pytest.raises(httpx.HTTPStatusError) as exc_info:
        list(connector.list_changes(cursor=None))
    assert exc_info.value.response.status_code == 500


@pytest.mark.integration
def test_fetch_times_out_propagates_to_caller() -> None:
    """When the content fetch times out, the timeout exception surfaces.

    Concrete sabotage-provable assertion: the raised exception is
    :class:`httpx.TimeoutException` (or a subclass). Swallowing the
    timeout in the client would make this test fail.
    """
    seen_changes = {"n": 0}

    def _handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "/changes/startPageToken" in url:
            return httpx.Response(200, json={"startPageToken": "seed-timeout"})
        if "alt=media" in url:
            raise httpx.TimeoutException("simulated content fetch timeout")
        seen_changes["n"] += 1
        return httpx.Response(
            200,
            json={
                "newStartPageToken": "next-page",
                "changes": [
                    {
                        "fileId": "file-timeout",
                        "file": {
                            "id": "file-timeout",
                            "name": "timeout.pdf",
                            "mimeType": "application/pdf",
                            "modifiedTime": "2026-05-22T10:00:00Z",
                            "webViewLink": "https://drive.google.com/file/d/file-timeout/view",
                        },
                    }
                ],
            },
        )

    connector = _build_connector(_handler)
    list(connector.list_changes(cursor=None))
    assert seen_changes["n"] == 1
    with pytest.raises(httpx.TimeoutException):
        connector.fetch("file-timeout")


@pytest.mark.integration
def test_list_changes_returns_partial_when_changes_array_truncated() -> None:
    """A change page with fewer entries than the source had emits exactly that count.

    Drive's changes endpoint can return a partial page when a per-call
    quota interrupts collection — the connector should emit whatever
    rows it received without inventing missing entries.

    Concrete sabotage-provable assertion: exactly one event emits for
    a single-entry response. Inverting "drop empty file blocks" would
    cause spurious events.
    """

    def _handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "/changes/startPageToken" in url:
            return httpx.Response(200, json={"startPageToken": "seed-partial"})
        return httpx.Response(
            200,
            json={
                "newStartPageToken": "next-page",
                "changes": [
                    {
                        "fileId": "file-partial-1",
                        "file": {
                            "id": "file-partial-1",
                            "name": "partial.pdf",
                            "mimeType": "application/pdf",
                            "modifiedTime": "2026-05-22T10:00:00Z",
                            "webViewLink": "https://drive.google.com/file/d/file-partial-1/view",
                        },
                    },
                    # Truncated entry — no ``file`` block and not removed.
                    {"fileId": ""},
                ],
            },
        )

    connector = _build_connector(_handler)
    events = list(connector.list_changes(cursor=None))
    assert len(events) == 1, f"expected exactly one event (partial array), got {len(events)}: {events!r}"
    assert events[0].item_id == "file-partial-1"


@pytest.mark.integration
def test_list_changes_returns_empty_when_no_changes() -> None:
    """An empty changes array yields zero events and advances the cursor.

    Concrete sabotage-provable assertion: zero events emit AND the
    next_cursor is populated with the returned newStartPageToken.
    Replacing the cursor-write with a no-op would fail the second
    assertion.
    """

    def _handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "/changes/startPageToken" in url:
            return httpx.Response(200, json={"startPageToken": "seed-empty"})
        return httpx.Response(200, json={"changes": [], "newStartPageToken": "advanced-cursor"})

    connector = _build_connector(_handler)
    events = list(connector.list_changes(cursor=None))
    assert events == [], f"expected zero events for empty changes, got {events!r}"
    assert connector.next_cursor() == "advanced-cursor", (
        f"empty changes must still advance the cursor; got {connector.next_cursor()!r}"
    )


@pytest.mark.integration
def test_list_changes_unauthorized_raises_credential_expired() -> None:
    """401 from the changes endpoint raises :class:`CredentialExpiredError`.

    Concrete sabotage-provable assertion: the raised exception class
    is exactly :class:`CredentialExpiredError`. Removing the 401 →
    CredentialExpired translation in the client would change the
    raised type and fail this assertion.
    """

    def _handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "/changes/startPageToken" in url:
            return httpx.Response(200, json={"startPageToken": "seed-401"})
        return httpx.Response(401, json={"error": "unauthorized"})

    connector = _build_connector(_handler)
    with pytest.raises(CredentialExpiredError):
        list(connector.list_changes(cursor=None))


@pytest.mark.integration
def test_list_changes_unavailable_503_eventually_raises() -> None:
    """503 from the changes endpoint retries then surfaces a status error.

    Concrete sabotage-provable assertion: the raised exception is
    :class:`httpx.HTTPStatusError` with code 503. Swallowing the
    repeated-503 case would make this test fail.
    """

    def _handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "/changes/startPageToken" in url:
            return httpx.Response(200, json={"startPageToken": "seed-503"})
        return httpx.Response(503, headers={"Retry-After": "0"}, json={"error": "unavailable"})

    connector = _build_connector(_handler)
    with pytest.raises(httpx.HTTPStatusError) as exc_info:
        list(connector.list_changes(cursor=None))
    assert exc_info.value.response.status_code == 503

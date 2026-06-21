"""Step definitions for connector_google_drive.feature.

Drives the real :class:`kairix.connectors.google_drive.GoogleDriveConnector`
against an :class:`httpx.MockTransport`-backed Drive stub. No real
network call — the stub returns one changes page so the behaviour
assertions can pin the typed ChangeEvent shape and the cursor
encoding.

Per F46, this step file reaches the connector through the real
constructor + the production HTTP client (depth ≤ 2). Direct
construction is permitted in BDD step files when the target is a
Protocol-compliant leaf such as ``GoogleDriveConnector``.

F1-clean: no @patch / kairix module-attribute substitution.
F2-clean: no KAIRIX_* env-var manipulation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import httpx
import pytest
from pytest_bdd import given, parsers, then, when

from kairix.connectors.google_drive import (
    GoogleDriveClient,
    GoogleDriveConnector,
    GoogleDriveCorpusSpec,
    GoogleDriveCredentials,
)
from kairix.core.protocols import ChangeEvent, RawArtefact

pytestmark = pytest.mark.bdd

_CORPUS_ID = "workspace-bdd"
_NEW_START_PAGE_TOKEN = "newpagetoken-bdd-1"
_NATIVE_DOC_ID = "drive-native-doc-bdd"
_NATIVE_DOC_MIME = "application/vnd.google-apps.document"
_EXPORTED_DOC_BODY = b"exported google doc body"


def _one_pdf_changes_page() -> dict[str, Any]:
    """One Drive changes page with a single PDF envelope + a newStartPageToken."""
    return {
        "newStartPageToken": _NEW_START_PAGE_TOKEN,
        "changes": [
            {
                "fileId": "drive-file-bdd",
                "file": {
                    "id": "drive-file-bdd",
                    "name": "agent-handbook.pdf",
                    "mimeType": "application/pdf",
                    "modifiedTime": "2026-05-22T10:00:00Z",
                    "webViewLink": "https://drive.google.com/file/d/drive-file-bdd/view",
                    "size": "87231",
                },
            }
        ],
    }


def _native_doc_changes_page() -> dict[str, Any]:
    """One Drive changes page with a single native Google Doc envelope."""
    return {
        "newStartPageToken": _NEW_START_PAGE_TOKEN,
        "changes": [
            {
                "fileId": _NATIVE_DOC_ID,
                "file": {
                    "id": _NATIVE_DOC_ID,
                    "name": "design-notes",
                    "mimeType": _NATIVE_DOC_MIME,
                    "modifiedTime": "2026-05-22T10:00:00Z",
                    "webViewLink": f"https://docs.google.com/document/d/{_NATIVE_DOC_ID}/edit",
                },
            }
        ],
    }


@dataclass
class _Ctx:
    """Per-scenario context — no module-level mutable state."""

    requested_urls: list[str] = field(default_factory=list)
    connector: GoogleDriveConnector | None = None
    events: list[ChangeEvent] = field(default_factory=list)
    artefact: RawArtefact | None = None


@pytest.fixture
def google_drive_ctx() -> _Ctx:
    return _Ctx()


def _build_connector_with_stubbed_drive(ctx: _Ctx) -> GoogleDriveConnector:
    """Construct the real connector wired to a recording stub Drive endpoint."""

    def _stub_handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        ctx.requested_urls.append(url)
        if "/changes/startPageToken" in url:
            return httpx.Response(200, json={"startPageToken": "seed-bdd"})
        return httpx.Response(200, json=_one_pdf_changes_page())

    transport = httpx.MockTransport(_stub_handler)
    shared_client = httpx.Client(transport=transport)

    return GoogleDriveConnector(
        corpora=[GoogleDriveCorpusSpec(corpus_id=_CORPUS_ID)],
        credentials=GoogleDriveCredentials(
            access_token="fake-bdd-token",  # pragma: allowlist secret — test fixture
        ),
        client_builder=lambda creds: GoogleDriveClient(
            access_token=creds.access_token,
            http_client=shared_client,
        ),
    )


def _connector_for_handler(ctx: _Ctx, handler: Any) -> GoogleDriveConnector:
    """Wire the real connector to ``handler`` via MockTransport."""
    transport = httpx.MockTransport(handler)
    shared_client = httpx.Client(transport=transport)
    return GoogleDriveConnector(
        corpora=[GoogleDriveCorpusSpec(corpus_id=_CORPUS_ID)],
        credentials=GoogleDriveCredentials(
            access_token="fake-bdd-token",  # pragma: allowlist secret — test fixture
        ),
        client_builder=lambda creds: GoogleDriveClient(
            access_token=creds.access_token,
            http_client=shared_client,
        ),
    )


def _build_connector_shared_drive_only(ctx: _Ctx) -> GoogleDriveConnector:
    """Stub that yields the file ONLY when the request is scoped to all drives.

    Mirrors the real Drive API's My-Drive-only default: the Shared-Drive
    file is invisible unless ``includeItemsFromAllDrives`` +
    ``supportsAllDrives`` are present.
    """

    def _handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        ctx.requested_urls.append(url)
        if "/changes/startPageToken" in url:
            return httpx.Response(200, json={"startPageToken": "seed-shared-bdd"})
        if "includeItemsFromAllDrives=true" not in url or "supportsAllDrives=true" not in url:
            return httpx.Response(200, json={"changes": [], "newStartPageToken": _NEW_START_PAGE_TOKEN})
        return httpx.Response(200, json=_one_pdf_changes_page())

    return _connector_for_handler(ctx, _handler)


def _build_connector_native_doc(ctx: _Ctx) -> GoogleDriveConnector:
    """Stub that 403s on alt=media for the native doc and exports its body."""

    def _handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        ctx.requested_urls.append(url)
        if "/changes/startPageToken" in url:
            return httpx.Response(200, json={"startPageToken": "seed-native-bdd"})
        if "alt=media" in url:
            return httpx.Response(403, json={"error": {"errors": [{"reason": "fileNotDownloadable"}]}})
        if "/export" in url:
            return httpx.Response(200, content=_EXPORTED_DOC_BODY, headers={"Content-Type": "text/plain"})
        return httpx.Response(200, json=_native_doc_changes_page())

    return _connector_for_handler(ctx, _handler)


# ---------------------------------------------------------------------------
# Givens
# ---------------------------------------------------------------------------


@given(parsers.parse("a stubbed Google Drive endpoint that returns one configured corpus with a sample pdf envelope"))
def _given_one_pdf(google_drive_ctx: _Ctx) -> None:
    google_drive_ctx.connector = _build_connector_with_stubbed_drive(google_drive_ctx)


@given(parsers.parse("a stubbed Google Drive endpoint where the file lives on a Shared Drive"))
def _given_shared_drive(google_drive_ctx: _Ctx) -> None:
    google_drive_ctx.connector = _build_connector_shared_drive_only(google_drive_ctx)


@given(parsers.parse("a stubbed Google Drive endpoint that returns a native Google Doc whose alt=media 403s"))
def _given_native_doc(google_drive_ctx: _Ctx) -> None:
    google_drive_ctx.connector = _build_connector_native_doc(google_drive_ctx)


# ---------------------------------------------------------------------------
# Whens
# ---------------------------------------------------------------------------


@when(parsers.parse("the operator runs the google drive connector list_changes with no cursor"))
def _when_list_changes(google_drive_ctx: _Ctx) -> None:
    assert google_drive_ctx.connector is not None, "Given step must run before When"
    google_drive_ctx.events = list(google_drive_ctx.connector.list_changes(cursor=None))


@when(parsers.parse("the operator fetches the native google doc content"))
def _when_fetch_native(google_drive_ctx: _Ctx) -> None:
    assert google_drive_ctx.connector is not None, "Given step must run before When"
    google_drive_ctx.artefact = google_drive_ctx.connector.fetch(_NATIVE_DOC_ID)


# ---------------------------------------------------------------------------
# Thens
# ---------------------------------------------------------------------------


@then("one created change event is emitted from the google drive connector")
def _one_created_event(google_drive_ctx: _Ctx) -> None:
    events = google_drive_ctx.events
    assert len(events) == 1, f"expected 1 event, got {len(events)}: {events!r}"
    assert events[0].op == "created", f"expected created op, got {events[0]!r}"


@then("the google drive change event carries an ISO-8601 modified_at timestamp")
def _event_has_iso(google_drive_ctx: _Ctx) -> None:
    event = google_drive_ctx.events[0]
    assert event.modified_at, f"event {event!r} missing modified_at"
    assert event.modified_at.endswith("Z") or "+" in event.modified_at, (
        f"event {event!r} modified_at not ISO-8601: {event.modified_at!r}"
    )


@then("the google drive change event's sensitivity tier is internal")
def _event_internal_tier(google_drive_ctx: _Ctx) -> None:
    event = google_drive_ctx.events[0]
    tier = event.metadata.get("sensitivity")
    assert tier == "internal", f"event {event.item_id!r} sensitivity is not internal: {tier!r}"


@then("the google drive change event metadata records the corpus id")
def _event_records_corpus(google_drive_ctx: _Ctx) -> None:
    event = google_drive_ctx.events[0]
    assert event.metadata.get("corpus_id") == _CORPUS_ID, (
        f"event {event.item_id!r} corpus_id metadata is wrong: {event.metadata.get('corpus_id')!r}"
    )


@then("the google drive connector exposes a non-empty next cursor")
def _connector_has_cursor(google_drive_ctx: _Ctx) -> None:
    assert google_drive_ctx.connector is not None
    cursor = google_drive_ctx.connector.next_cursor()
    assert cursor, f"expected non-empty next cursor, got {cursor!r}"


@then("the google drive next cursor is the persisted new start page token")
def _cursor_is_new_start_page_token(google_drive_ctx: _Ctx) -> None:
    assert google_drive_ctx.connector is not None
    cursor = google_drive_ctx.connector.next_cursor()
    assert cursor == _NEW_START_PAGE_TOKEN, (
        f"cursor must be the persisted newStartPageToken; got {cursor!r}, expected {_NEW_START_PAGE_TOKEN!r}"
    )


@then("the fetched google drive artefact carries the exported document body")
def _artefact_has_export_body(google_drive_ctx: _Ctx) -> None:
    artefact = google_drive_ctx.artefact
    assert artefact is not None, "When-fetch step must run before this Then"
    assert artefact.raw == _EXPORTED_DOC_BODY, (
        f"native doc must export its body instead of dead-lettering; got {artefact.raw!r}"
    )

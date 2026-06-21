"""Resilience-audit hardening for the google_drive connector (#571).

Two completeness bugs pinned here against the real
:class:`GoogleDriveClient` / :class:`GoogleDriveConnector` driven through
an :class:`httpx.MockTransport` so the assertions prove the real wire
contract — not a fake that papers over it:

1. **Shared / Team Drives are enumerated.** The Drive v3 ``files`` /
   ``changes`` surface only returns My-Drive items unless the request
   carries ``supportsAllDrives=true`` + ``includeItemsFromAllDrives=true``
   (and ``corpora=allDrives`` where the changes API requires it). These
   tests assert those query parameters are present on the actual
   outbound request URLs, so a Shared-Drive corpus is no longer
   invisible.

2. **Native Google Docs / Sheets / Slides export instead of
   dead-lettering.** ``alt=media`` returns HTTP 403 for
   ``application/vnd.google-apps.*`` types — they must be pulled via the
   Drive ``files.export`` endpoint with a mapped export MIME. These
   tests model the real 403-on-``alt=media`` wire behaviour and assert
   the connector exports the bytes instead of letting the fetch raise.

Each ``test_*`` carries the ``@pytest.mark.integration`` marker per F8.
F1 / F2 clean — no patching, no env mutation; the HTTP transport and
the sleeper are public constructor seams.
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

pytestmark = pytest.mark.integration

_CORPUS_ID = "shared-drive-corpus"
_NATIVE_DOC_MIME = "application/vnd.google-apps.document"
_NATIVE_SHEET_MIME = "application/vnd.google-apps.spreadsheet"
_NATIVE_SLIDES_MIME = "application/vnd.google-apps.presentation"


def _build_client(handler: object) -> GoogleDriveClient:
    """Wire a real :class:`GoogleDriveClient` to ``handler`` via MockTransport."""
    transport = httpx.MockTransport(handler)  # type: ignore[arg-type]  # F3 rationale: handler is the httpx mock callable shape narrowed at runtime
    shared = httpx.Client(transport=transport)
    return GoogleDriveClient(
        access_token="fake-token-value",  # pragma: allowlist secret — test fixture
        http_client=shared,
        sleep_fn=lambda _s: None,
    )


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


# ---------------------------------------------------------------------------
# Bug 1 — Shared / Team Drives are enumerated
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_changes_request_carries_shared_drive_params() -> None:
    """The ``/changes`` request enumerates Shared Drives.

    Without ``supportsAllDrives`` + ``includeItemsFromAllDrives`` +
    ``corpora=allDrives`` the Drive API silently scopes to My Drive and
    every Shared-Drive file is invisible.

    Sabotage proof: removing ``includeItemsFromAllDrives=true`` from the
    ``fetch_changes_page`` URL drops the assertion below and this test
    fails.
    """
    seen_urls: list[str] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        seen_urls.append(url)
        return httpx.Response(200, json={"changes": [], "newStartPageToken": "fresh"})

    client = _build_client(_handler)
    list(client.iter_changes("seed-token"))

    changes_url = next(u for u in seen_urls if "/changes?" in u)
    assert "supportsAllDrives=true" in changes_url, (
        f"changes request must set supportsAllDrives=true to see Shared Drives; got {changes_url!r}"
    )
    assert "includeItemsFromAllDrives=true" in changes_url, (
        f"changes request must set includeItemsFromAllDrives=true to enumerate Shared-Drive items; got {changes_url!r}"
    )
    assert "corpora=allDrives" in changes_url, (
        f"changes request must set corpora=allDrives when including all drives; got {changes_url!r}"
    )


@pytest.mark.integration
def test_start_page_token_request_carries_shared_drive_param() -> None:
    """The ``startPageToken`` request is scoped to all drives.

    The cold-start seed token must cover Shared Drives too, else the
    first drain's window excludes them. Drive requires
    ``supportsAllDrives=true`` on this call.

    Sabotage proof: removing ``supportsAllDrives=true`` from
    ``get_start_page_token`` fails the assertion below.
    """
    seen_urls: list[str] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        seen_urls.append(str(request.url))
        return httpx.Response(200, json={"startPageToken": "seed-shared"})

    client = _build_client(_handler)
    token = client.get_start_page_token()

    assert token == "seed-shared"
    seed_url = next(u for u in seen_urls if "startPageToken" in u)
    assert "supportsAllDrives=true" in seed_url, (
        f"startPageToken request must set supportsAllDrives=true; got {seed_url!r}"
    )


@pytest.mark.integration
def test_file_metadata_request_carries_shared_drive_param() -> None:
    """A per-file metadata fetch resolves Shared-Drive files.

    ``files.get`` returns 404 for a Shared-Drive file id unless
    ``supportsAllDrives=true`` is set.

    Sabotage proof: removing ``supportsAllDrives=true`` from
    ``fetch_file_metadata`` fails the assertion below.
    """
    seen_urls: list[str] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        seen_urls.append(str(request.url))
        return httpx.Response(200, json={"id": "shared-file", "name": "x.pdf", "mimeType": "application/pdf"})

    client = _build_client(_handler)
    ref = client.fetch_file_metadata("shared-file")

    assert ref.file_id == "shared-file"
    meta_url = next(u for u in seen_urls if "shared-file" in u)
    assert "supportsAllDrives=true" in meta_url, (
        f"files.get request must set supportsAllDrives=true to resolve Shared-Drive files; got {meta_url!r}"
    )


@pytest.mark.integration
def test_content_fetch_request_carries_shared_drive_param() -> None:
    """A binary content fetch downloads Shared-Drive file bytes.

    ``files.get?alt=media`` returns 404 for a Shared-Drive file unless
    ``supportsAllDrives=true`` is set.

    Sabotage proof: removing ``supportsAllDrives=true`` from the
    ``alt=media`` URL in ``fetch_file_content`` fails the assertion
    below.
    """
    seen_urls: list[str] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        seen_urls.append(str(request.url))
        return httpx.Response(200, content=b"pdf-bytes", headers={"Content-Type": "application/pdf"})

    client = _build_client(_handler)
    raw, mime = client.fetch_file_content("shared-binary", mime_type="application/pdf")

    assert raw == b"pdf-bytes"
    assert mime == "application/pdf"
    media_url = next(u for u in seen_urls if "alt=media" in u)
    assert "supportsAllDrives=true" in media_url, (
        f"alt=media request must set supportsAllDrives=true to download Shared-Drive bytes; got {media_url!r}"
    )


@pytest.mark.integration
def test_connector_surfaces_shared_drive_file_end_to_end() -> None:
    """A Shared-Drive file in the changes window surfaces as a created event.

    Drives the real connector: the changes drain returns one file that
    lives on a Shared Drive (the stub only answers when the
    all-drives params are present), proving the file is no longer
    invisible.

    Sabotage proof: removing the all-drives params from the changes URL
    makes the stub return its My-Drive-only (empty) page and the event
    count drops to zero.
    """

    def _handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "startPageToken" in url:
            return httpx.Response(200, json={"startPageToken": "seed-e2e"})
        # The stub only yields the Shared-Drive file when the request is
        # scoped to all drives — mirroring the real API's My-Drive-only
        # default.
        if "includeItemsFromAllDrives=true" not in url or "supportsAllDrives=true" not in url:
            return httpx.Response(200, json={"changes": [], "newStartPageToken": "advanced"})
        return httpx.Response(
            200,
            json={
                "newStartPageToken": "advanced",
                "changes": [
                    {
                        "fileId": "team-drive-file",
                        "file": {
                            "id": "team-drive-file",
                            "name": "team-plan.pdf",
                            "mimeType": "application/pdf",
                            "modifiedTime": "2026-05-22T10:00:00Z",
                            "webViewLink": "https://drive.google.com/file/d/team-drive-file/view",
                        },
                    }
                ],
            },
        )

    connector = _build_connector(_handler)
    events = list(connector.list_changes(cursor=None))
    assert [e.item_id for e in events] == ["team-drive-file"], (
        f"expected the Shared-Drive file to surface, got {events!r}"
    )


# ---------------------------------------------------------------------------
# Bug 2 — Native Google Docs / Sheets / Slides export instead of dead-letter
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_native_doc_exports_as_text_instead_of_dead_lettering() -> None:
    """A native Google Doc is exported via files.export, not alt=media.

    Models the real wire contract: ``alt=media`` returns HTTP 403 for a
    google-apps mime type; the export endpoint returns the body. The
    connector must branch on the mime so native files no longer
    dead-letter every sync.

    Sabotage proof: removing the export branch in ``fetch_file_content``
    (so native types still hit ``alt=media``) makes the 403 propagate as
    an ``httpx.HTTPStatusError`` and this test fails.
    """
    seen_urls: list[str] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        seen_urls.append(url)
        if "alt=media" in url:
            return httpx.Response(
                403,
                json={"error": {"errors": [{"reason": "fileNotDownloadable"}]}},
            )
        if "/export" in url:
            return httpx.Response(200, content=b"exported text body", headers={"Content-Type": "text/plain"})
        return httpx.Response(404, json={"error": "unexpected url"})

    client = _build_client(_handler)
    raw, mime = client.fetch_file_content("native-doc", mime_type=_NATIVE_DOC_MIME)

    assert raw == b"exported text body", f"native doc must be exported, got {raw!r}"
    assert mime == "text/plain", f"export content-type must surface, got {mime!r}"
    assert not any("alt=media" in u for u in seen_urls), (
        f"native types must NOT hit alt=media (it 403s); requests were {seen_urls!r}"
    )
    export_url = next(u for u in seen_urls if "/export" in u)
    assert "native-doc/export" in export_url, f"export must target files.export; got {export_url!r}"


@pytest.mark.integration
def test_native_spreadsheet_exports_as_csv() -> None:
    """A native Google Sheet exports with the spreadsheet→CSV mapping.

    Sabotage proof: dropping the spreadsheet entry from the export-MIME
    map (so it falls back to text/plain or 403s) fails the
    ``mimeType=text%2Fcsv`` assertion below.
    """
    seen_urls: list[str] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        seen_urls.append(url)
        if "/export" in url:
            return httpx.Response(200, content=b"a,b,c\n1,2,3", headers={"Content-Type": "text/csv"})
        return httpx.Response(403, json={"error": {"errors": [{"reason": "fileNotDownloadable"}]}})

    client = _build_client(_handler)
    raw, mime = client.fetch_file_content("native-sheet", mime_type=_NATIVE_SHEET_MIME)

    assert raw == b"a,b,c\n1,2,3"
    assert mime == "text/csv"
    export_url = next(u for u in seen_urls if "/export" in u)
    assert "mimeType=text%2Fcsv" in export_url or "mimeType=text/csv" in export_url, (
        f"spreadsheet must export to text/csv; got {export_url!r}"
    )


@pytest.mark.integration
def test_native_presentation_exports_as_text() -> None:
    """A native Google Slides deck exports with the presentation→text mapping.

    Sabotage proof: dropping the presentation entry from the export-MIME
    map fails the ``mimeType=text%2Fplain`` assertion below.
    """
    seen_urls: list[str] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        seen_urls.append(url)
        if "/export" in url:
            return httpx.Response(200, content=b"slide text", headers={"Content-Type": "text/plain"})
        return httpx.Response(403, json={"error": {"errors": [{"reason": "fileNotDownloadable"}]}})

    client = _build_client(_handler)
    raw, _mime = client.fetch_file_content("native-deck", mime_type=_NATIVE_SLIDES_MIME)

    assert raw == b"slide text"
    export_url = next(u for u in seen_urls if "/export" in u)
    assert "mimeType=text%2Fplain" in export_url or "mimeType=text/plain" in export_url, (
        f"presentation must export to text/plain; got {export_url!r}"
    )


@pytest.mark.integration
def test_binary_pdf_still_uses_alt_media_not_export() -> None:
    """A non-native (binary) file keeps the ``alt=media`` download path.

    Guards against the export branch swallowing the binary path — only
    ``application/vnd.google-apps.*`` types export.

    Sabotage proof: widening the native-type predicate to match every
    mime makes the PDF hit ``/export`` and this assertion fails.
    """
    seen_urls: list[str] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        seen_urls.append(url)
        return httpx.Response(200, content=b"%PDF-1.7", headers={"Content-Type": "application/pdf"})

    client = _build_client(_handler)
    raw, mime = client.fetch_file_content("binary-pdf", mime_type="application/pdf")

    assert raw == b"%PDF-1.7"
    assert mime == "application/pdf"
    assert any("alt=media" in u for u in seen_urls), f"binary types must use alt=media, got {seen_urls!r}"
    assert not any("/export" in u for u in seen_urls), (
        f"binary types must NOT use the export endpoint, got {seen_urls!r}"
    )


@pytest.mark.integration
def test_connector_fetch_exports_native_doc_end_to_end() -> None:
    """The connector's ``fetch`` exports a native doc surfaced by list_changes.

    End-to-end through the production ``GoogleDriveConnector``: a native
    Google Doc surfaces in the changes window, then ``fetch`` exports
    its body instead of dead-lettering on the ``alt=media`` 403.

    Sabotage proof: removing the export branch makes ``fetch`` raise
    ``httpx.HTTPStatusError`` (403) and this test fails.
    """

    def _handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "startPageToken" in url:
            return httpx.Response(200, json={"startPageToken": "seed-native"})
        if "alt=media" in url:
            return httpx.Response(403, json={"error": {"errors": [{"reason": "fileNotDownloadable"}]}})
        if "/export" in url:
            return httpx.Response(200, content=b"doc body text", headers={"Content-Type": "text/plain"})
        return httpx.Response(
            200,
            json={
                "newStartPageToken": "advanced",
                "changes": [
                    {
                        "fileId": "native-doc-e2e",
                        "file": {
                            "id": "native-doc-e2e",
                            "name": "design.gdoc",
                            "mimeType": _NATIVE_DOC_MIME,
                            "modifiedTime": "2026-05-22T10:00:00Z",
                            "webViewLink": "https://docs.google.com/document/d/native-doc-e2e/edit",
                        },
                    }
                ],
            },
        )

    connector = _build_connector(_handler)
    events = list(connector.list_changes(cursor=None))
    assert [e.item_id for e in events] == ["native-doc-e2e"]

    artefact = connector.fetch("native-doc-e2e")
    assert artefact.raw == b"doc body text", f"native doc must export its body, got {artefact.raw!r}"

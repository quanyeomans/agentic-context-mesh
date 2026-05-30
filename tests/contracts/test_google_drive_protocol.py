"""Contract test for the Google Drive connector plugin (F43).

Exercises the canonical fake
(:class:`tests.fakes.FakeGoogleDriveConnector`) AND the real
implementation
(:class:`kairix.connectors.google_drive.GoogleDriveConnector`) through
the same :class:`~kairix.core.protocols.SourceConnector` Protocol
assertions. F43 requires this pairing — without it the fake can drift
from the real wire (or vice versa) and the production path silently
diverges from what BDD / unit tests measure.

Real-impl path is driven against an :class:`httpx.MockTransport`-backed
Drive stub; no real network call is ever made.

Sabotage proofs:

  * Removing ``list_changes`` from
    :class:`GoogleDriveConnector` flips
    ``test_connector_satisfies_source_connector_protocol`` (real branch)
    to False.
  * Replacing the connector's ``fetch`` return shape with a plain
    ``bytes`` value (skipping the :class:`RawArtefact` wrapper) breaks
    ``test_connector_fetch_returns_binary_artefact``.
  * Mutating :data:`DEFAULT_SENSITIVITY` to ``"public"`` flips
    ``test_connector_default_sensitivity_is_internal``.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx
import pytest

from kairix.connectors.google_drive import (
    DEFAULT_SENSITIVITY,
    GoogleDriveClient,
    GoogleDriveConnector,
    GoogleDriveCorpusSpec,
    GoogleDriveCredentials,
)
from kairix.core.protocols import ChangeEvent, RawArtefact, SourceConnector
from tests.fakes import FakeGoogleDriveConnector

pytestmark = pytest.mark.contract

_CORPUS_ID = "workspace-contract"
_NEW_START_PAGE_TOKEN = "newpagetoken-contract"


def _envelope_files() -> list[dict[str, Any]]:
    """Two seeded envelopes that round-trip through both branches."""
    return [
        {
            "id": "drive-alpha",
            "name": "alpha.pdf",
            "mimeType": "application/pdf",
            "modifiedTime": "2026-05-22T10:00:00Z",
            "webViewLink": "https://drive.google.com/file/d/drive-alpha/view",
            "corpus_id": _CORPUS_ID,
            "_content": b"%PDF-1.4 fake pdf contract content",
        },
        {
            "id": "drive-bravo",
            "name": "bravo.docx",
            "mimeType": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "modifiedTime": "2026-05-22T11:00:00Z",
            "webViewLink": "https://drive.google.com/file/d/drive-bravo/view",
            "corpus_id": _CORPUS_ID,
            "_content": b"PK\x03\x04 fake docx contract content",
        },
    ]


def _fake_factory() -> SourceConnector:
    """Canonical fake factory — seeds two envelopes."""
    return FakeGoogleDriveConnector(files=_envelope_files(), new_start_page_token=_NEW_START_PAGE_TOKEN)


def _changes_page_payload() -> dict[str, Any]:
    return {
        "newStartPageToken": _NEW_START_PAGE_TOKEN,
        "changes": [
            {
                "fileId": entry["id"],
                "file": {
                    "id": entry["id"],
                    "name": entry["name"],
                    "mimeType": entry["mimeType"],
                    "modifiedTime": entry["modifiedTime"],
                    "webViewLink": entry["webViewLink"],
                    "size": str(len(entry["_content"])),
                },
            }
            for entry in _envelope_files()
        ],
    }


def _real_factory() -> SourceConnector:
    """Real-impl factory — MockTransport-backed Drive stub."""

    def _stub(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "/changes/startPageToken" in url:
            return httpx.Response(200, json={"startPageToken": "seed-token"})
        if "alt=media" in url:
            for entry in _envelope_files():
                if entry["id"] in url:
                    return httpx.Response(
                        200,
                        content=entry["_content"],
                        headers={"Content-Type": entry["mimeType"]},
                    )
            return httpx.Response(404)
        return httpx.Response(200, json=_changes_page_payload())

    shared = httpx.Client(transport=httpx.MockTransport(_stub))
    connector = GoogleDriveConnector(
        corpora=[GoogleDriveCorpusSpec(corpus_id=_CORPUS_ID)],
        credentials=GoogleDriveCredentials(
            access_token="fake-token-value",  # pragma: allowlist secret — test fixture
        ),
        client_builder=lambda creds: GoogleDriveClient(access_token=creds.access_token, http_client=shared),
    )
    # Prime the envelope cache so fetch() works in the contract assertions.
    list(connector.list_changes(cursor=None))
    return connector


_FACTORIES: list[tuple[str, Callable[[], SourceConnector]]] = [
    ("fake", _fake_factory),
    ("real", _real_factory),
]


@pytest.mark.contract
@pytest.mark.parametrize("name,factory", _FACTORIES)
def test_connector_satisfies_source_connector_protocol(name: str, factory: Callable[[], SourceConnector]) -> None:
    """F43: both fake and real impl satisfy the runtime-checkable Protocol."""
    connector = factory()
    assert isinstance(connector, SourceConnector), f"{name!r} factory output is not a SourceConnector"
    assert connector.name == "google_drive"


@pytest.mark.contract
@pytest.mark.parametrize("name,factory", _FACTORIES)
def test_connector_list_changes_returns_change_events(name: str, factory: Callable[[], SourceConnector]) -> None:
    """Both implementations stream :class:`ChangeEvent` instances."""
    connector = factory()
    events = list(connector.list_changes(cursor=None))
    assert events, f"{name!r} factory produced no events"
    for ev in events:
        assert isinstance(ev, ChangeEvent), f"{name!r} yielded a non-ChangeEvent: {ev!r}"
        assert ev.op in ("created", "modified", "deleted")


@pytest.mark.contract
@pytest.mark.parametrize("name,factory", _FACTORIES)
def test_connector_fetch_returns_binary_artefact(name: str, factory: Callable[[], SourceConnector]) -> None:
    """Both implementations satisfy the ``fetch`` -> :class:`RawArtefact` shape."""
    connector = factory()
    artefact = connector.fetch("drive-alpha")
    assert isinstance(artefact, RawArtefact), f"{name!r} fetch did not return a RawArtefact: {artefact!r}"
    assert artefact.mime == "application/pdf", f"{name!r} fetch mime is wrong: {artefact.mime!r}"
    assert artefact.raw, f"{name!r} fetch raw bytes is empty"


@pytest.mark.contract
@pytest.mark.parametrize("name,factory", _FACTORIES)
def test_connector_source_link_round_trips_to_drive(name: str, factory: Callable[[], SourceConnector]) -> None:
    """``source_link`` returns a Drive or gdrive:// URL on both impls."""
    connector = factory()
    link = connector.source_link("drive-alpha")
    assert link, f"{name!r} produced empty source_link"
    assert link.startswith(("https://", "gdrive://")), f"{name!r} unexpected link scheme: {link!r}"


@pytest.mark.contract
@pytest.mark.parametrize("name,factory", _FACTORIES)
def test_connector_default_sensitivity_is_internal(name: str, factory: Callable[[], SourceConnector]) -> None:
    """``sensitivity_for`` returns the documented default ``internal`` tier."""
    connector = factory()
    tier = connector.sensitivity_for("drive-alpha")
    assert tier == DEFAULT_SENSITIVITY == "internal", f"{name!r} returned unexpected sensitivity: {tier!r}"

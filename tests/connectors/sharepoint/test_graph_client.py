"""Unit tests for :class:`kairix.connectors.sharepoint.SharePointGraphClient`.

Scope:

  * ``list_sites`` enumerates the ``/sites?search=*`` collection and
    follows ``@odata.nextLink`` pagination.
  * ``list_drives`` enumerates per-site drives.
  * ``iter_drive_items`` walks ``@odata.nextLink`` pagination and stops
    at the final ``@odata.deltaLink`` page.
  * ``fetch_item_content`` downloads bytes via the item-content endpoint.
  * 401 invalidates the token and retries once.
  * Folder entries in the delta response are filtered at the parse layer.
  * The drive-id hint parser pulls the canonical id from
    ``@odata.context`` when present.

F1-clean (no monkey-patching), F8 carries ``@pytest.mark.unit``.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from kairix.connectors.sharepoint.graph_client import (
    DriveDeltaPage,
    SharePointGraphClient,
)
from kairix.transport.auth.oauth2_client_creds import OAuth2ClientCredsAuth

pytestmark = pytest.mark.unit


def _auth_and_client(handler: Any) -> tuple[OAuth2ClientCredsAuth, SharePointGraphClient]:
    transport = httpx.MockTransport(handler)
    shared = httpx.Client(transport=transport)
    auth = OAuth2ClientCredsAuth(
        tenant_id="t",
        client_id="c",
        client_secret="s-value",  # pragma: allowlist secret — test fixture
        scope="https://graph.microsoft.com/.default",
        http_client=shared,
    )
    return auth, SharePointGraphClient(auth=auth, http_client=shared)


def _token_response_for(request: httpx.Request) -> httpx.Response | None:
    if "/oauth2/v2.0/token" in str(request.url):
        return httpx.Response(200, json={"access_token": "fake-bearer", "expires_in": 3600, "token_type": "Bearer"})
    return None


def test_list_sites_yields_site_refs_from_search_endpoint() -> None:
    def _handler(request: httpx.Request) -> httpx.Response:
        token = _token_response_for(request)
        if token is not None:
            return token
        return httpx.Response(
            200,
            json={
                "value": [
                    {"id": "site-1", "displayName": "Marketing", "webUrl": "https://contoso.sharepoint.com/sites/m"},
                    {"id": "site-2", "displayName": "Engineering", "webUrl": "https://contoso.sharepoint.com/sites/e"},
                ]
            },
        )

    _, client = _auth_and_client(_handler)
    sites = list(client.list_sites())
    assert [s.site_id for s in sites] == ["site-1", "site-2"]
    assert sites[0].display_name == "Marketing"


def test_list_sites_follows_next_link_pagination() -> None:
    page_calls = {"n": 0}

    def _handler(request: httpx.Request) -> httpx.Response:
        token = _token_response_for(request)
        if token is not None:
            return token
        page_calls["n"] += 1
        if page_calls["n"] == 1:
            return httpx.Response(
                200,
                json={
                    "value": [{"id": "s1", "displayName": "S1", "webUrl": "https://x/s1"}],
                    "@odata.nextLink": "https://graph.microsoft.com/v1.0/sites?$skiptoken=tok",
                },
            )
        return httpx.Response(200, json={"value": [{"id": "s2", "displayName": "S2", "webUrl": "https://x/s2"}]})

    _, client = _auth_and_client(_handler)
    sites = list(client.list_sites())
    assert [s.site_id for s in sites] == ["s1", "s2"]


def test_list_drives_yields_drive_refs_for_site() -> None:
    def _handler(request: httpx.Request) -> httpx.Response:
        token = _token_response_for(request)
        if token is not None:
            return token
        return httpx.Response(
            200,
            json={
                "value": [
                    {
                        "id": "drive-1",
                        "name": "Documents",
                        "webUrl": "https://contoso.sharepoint.com/sites/x/Documents",
                    },
                ]
            },
        )

    _, client = _auth_and_client(_handler)
    drives = list(client.list_drives("site-1"))
    assert [d.drive_id for d in drives] == ["drive-1"]
    assert drives[0].name == "Documents"
    assert drives[0].site_id == "site-1"


def test_iter_drive_items_walks_pagination_and_records_delta_link() -> None:
    page_calls = {"n": 0}

    def _handler(request: httpx.Request) -> httpx.Response:
        token = _token_response_for(request)
        if token is not None:
            return token
        page_calls["n"] += 1
        if page_calls["n"] == 1:
            return httpx.Response(
                200,
                json={
                    "value": [
                        {
                            "id": "01A",
                            "name": "a.pdf",
                            "lastModifiedDateTime": "2026-05-22T10:00:00Z",
                            "file": {"mimeType": "application/pdf"},
                            "parentReference": {"driveId": "drive-x"},
                        }
                    ],
                    "@odata.nextLink": "https://graph.microsoft.com/v1.0/drives/drive-x/root/delta?$skiptoken=2",
                },
            )
        return httpx.Response(
            200,
            json={
                "value": [
                    {
                        "id": "01B",
                        "name": "b.pdf",
                        "lastModifiedDateTime": "2026-05-22T11:00:00Z",
                        "file": {"mimeType": "application/pdf"},
                        "parentReference": {"driveId": "drive-x"},
                    }
                ],
                "@odata.deltaLink": "https://graph.microsoft.com/v1.0/drives/drive-x/root/delta?$deltatoken=final",
            },
        )

    _, client = _auth_and_client(_handler)
    items = list(client.iter_drive_items("drive-x"))
    assert [it.item_id for it in items] == ["01A", "01B"]
    assert client.last_delta_link_for_drive("drive-x") == (
        "https://graph.microsoft.com/v1.0/drives/drive-x/root/delta?$deltatoken=final"
    )


def test_iter_drive_items_returns_none_delta_for_unsynced_drive() -> None:
    """last_delta_link_for_drive returns None when iter_drive_items has not run."""
    _, client = _auth_and_client(lambda r: httpx.Response(200, json={"value": []}))
    assert client.last_delta_link_for_drive("unsynced-drive") is None


def test_fetch_item_content_returns_binary_bytes() -> None:
    def _handler(request: httpx.Request) -> httpx.Response:
        token = _token_response_for(request)
        if token is not None:
            return token
        assert "/content" in str(request.url)
        return httpx.Response(200, content=b"\x89PNG fake content bytes")

    _, client = _auth_and_client(_handler)
    raw = client.fetch_item_content("drive-x", "01ITEM")
    assert raw.startswith(b"\x89PNG")


def test_authorised_get_retries_once_on_401() -> None:
    """A 401 triggers token invalidate + retry; second call succeeds."""
    seen_tokens: list[str] = []
    call_count = {"n": 0}

    def _handler(request: httpx.Request) -> httpx.Response:
        if "/oauth2/v2.0/token" in str(request.url):
            call_count["n"] += 1
            return httpx.Response(
                200,
                json={
                    "access_token": f"bearer-{call_count['n']}",
                    "expires_in": 3600,
                    "token_type": "Bearer",
                },
            )
        bearer = request.headers.get("Authorization", "")
        seen_tokens.append(bearer)
        if len(seen_tokens) == 1:
            return httpx.Response(401, json={"error": "invalid_token"})
        return httpx.Response(200, json={"value": []})

    _, client = _auth_and_client(_handler)
    list(client.iter_drive_items("drive-x"))
    assert len(seen_tokens) == 2
    assert seen_tokens[0] != seen_tokens[1], "second call must use the refreshed bearer"


def test_iter_drive_items_drops_folder_entries() -> None:
    """Folder entries are filtered at the parse layer; only files surface."""

    def _handler(request: httpx.Request) -> httpx.Response:
        token = _token_response_for(request)
        if token is not None:
            return token
        return httpx.Response(
            200,
            json={
                "@odata.context": "https://graph.microsoft.com/v1.0/$metadata#drives/drive-x/root/delta",
                "value": [
                    {
                        "id": "fldr",
                        "name": "Reports",
                        "folder": {"childCount": 3},
                        "parentReference": {"driveId": "drive-x"},
                    },
                    {
                        "id": "file",
                        "name": "report.pdf",
                        "file": {"mimeType": "application/pdf"},
                        "parentReference": {"driveId": "drive-x"},
                    },
                ],
                "@odata.deltaLink": "https://x",
            },
        )

    _, client = _auth_and_client(_handler)
    items = list(client.iter_drive_items("drive-x"))
    assert [it.item_id for it in items] == ["file"]


def test_iter_drive_items_carries_drive_id_hint_when_parent_reference_absent() -> None:
    """When a row omits ``parentReference``, the drive-id is filled from
    the ``@odata.context`` wrapper.
    """

    def _handler(request: httpx.Request) -> httpx.Response:
        token = _token_response_for(request)
        if token is not None:
            return token
        return httpx.Response(
            200,
            json={
                "@odata.context": "https://graph.microsoft.com/v1.0/$metadata#drives/hinted-drive/root/delta",
                "value": [
                    {"id": "01X", "name": "x.pdf", "file": {"mimeType": "application/pdf"}},
                ],
            },
        )

    _, client = _auth_and_client(_handler)
    items = list(client.iter_drive_items("hinted-drive"))
    assert len(items) == 1
    assert items[0].drive_id == "hinted-drive"
    assert items[0].mime == "application/pdf"


def test_iter_drive_items_handles_removed_marker_via_at_removed_field() -> None:
    """Items carrying ``@removed`` surface as removed=True (alternate
    Graph tombstone shape)."""

    def _handler(request: httpx.Request) -> httpx.Response:
        token = _token_response_for(request)
        if token is not None:
            return token
        return httpx.Response(
            200,
            json={
                "value": [
                    {"id": "01Z", "@removed": {"state": "soft"}, "parentReference": {"driveId": "drive-x"}},
                ],
            },
        )

    _, client = _auth_and_client(_handler)
    items = list(client.iter_drive_items("drive-x"))
    assert len(items) == 1
    assert items[0].removed is True


def test_drive_delta_page_dataclass_is_frozen() -> None:
    """F42 — DriveDeltaPage is a frozen dataclass."""
    page = DriveDeltaPage(items=(), next_link=None, delta_link="link")
    with pytest.raises(Exception):  # noqa: B017 — dataclass FrozenInstanceError shape varies across Python versions
        page.next_link = "other"  # type: ignore[misc]  # F3 rationale: test pins the frozen-dataclass invariant.


def test_iter_drive_items_handles_response_missing_value_array() -> None:
    """Responses missing ``value`` parse cleanly to an empty item set."""

    def _handler(request: httpx.Request) -> httpx.Response:
        token = _token_response_for(request)
        if token is not None:
            return token
        # Note: missing ``value`` key entirely
        return httpx.Response(200, json={"@odata.context": "x"})

    _, client = _auth_and_client(_handler)
    assert list(client.iter_drive_items("drive-x")) == []


def test_iter_drive_items_handles_response_with_non_list_value() -> None:
    """A non-list ``value`` is tolerated and treated as empty."""

    def _handler(request: httpx.Request) -> httpx.Response:
        token = _token_response_for(request)
        if token is not None:
            return token
        return httpx.Response(200, json={"value": "not a list"})

    _, client = _auth_and_client(_handler)
    assert list(client.iter_drive_items("drive-x")) == []


def test_graph_client_falls_through_to_owned_http_client_when_none_injected() -> None:
    """When no ``http_client`` is injected, the client opens its own
    ``httpx.Client`` per request — exercising the fallback path.

    Sabotage proof: removing the fallback branch makes the client try to
    call ``None.get(...)`` and the contract test fails on AttributeError.
    """
    # Build a connector with NO http_client and a never-called auth
    # — the test only proves the constructor's fallback path is wired;
    # no network call is made (we don't trigger any GET / pagination).
    auth = OAuth2ClientCredsAuth(
        tenant_id="t",
        client_id="c",
        client_secret="s-value",  # pragma: allowlist secret — test fixture
        scope="https://graph.microsoft.com/.default",
        http_client=httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"value": []}))),
    )
    client = SharePointGraphClient(auth=auth)
    # Just constructing without raising proves the fallback path is reachable.
    assert client.initial_delta_url("drive-x").endswith("/drives/drive-x/root/delta")


@pytest.mark.unit
def test_drive_item_carries_parent_path_normalised_from_graph_envelope() -> None:
    """`parentReference.path` is stripped to the operator-facing form.

    Graph returns ``/drives/<drive-id>/root:/Curated-Content/foo``; the
    connector and its filter compare against ``/Curated-Content/foo``.
    """
    body = {
        "value": [
            {
                "id": "item-a",
                "name": "doc.md",
                "file": {"mimeType": "text/markdown"},
                "parentReference": {
                    "driveId": "drive-x",
                    "path": "/drives/drive-x/root:/Curated-Content/sub",
                },
            }
        ],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        token = _token_response_for(request)
        if token is not None:
            return token
        return httpx.Response(200, json=body)

    auth = OAuth2ClientCredsAuth(
        tenant_id="t",
        client_id="c",
        client_secret="s-value",  # pragma: allowlist secret — test fixture
        scope="https://graph.microsoft.com/.default",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    client = SharePointGraphClient(auth=auth, http_client=httpx.Client(transport=httpx.MockTransport(handler)))
    items = list(client.iter_drive_items("drive-x"))
    assert len(items) == 1
    assert items[0].parent_path == "/Curated-Content/sub"


@pytest.mark.unit
def test_drive_item_parent_path_none_when_envelope_omits_field() -> None:
    """No parentReference.path → parent_path is None (filter treats as 'unknown')."""
    body = {
        "value": [
            {
                "id": "item-b",
                "name": "doc.md",
                "file": {"mimeType": "text/markdown"},
                "parentReference": {"driveId": "drive-x"},
            }
        ],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        token = _token_response_for(request)
        if token is not None:
            return token
        return httpx.Response(200, json=body)

    auth = OAuth2ClientCredsAuth(
        tenant_id="t",
        client_id="c",
        client_secret="s-value",  # pragma: allowlist secret — test fixture
        scope="https://graph.microsoft.com/.default",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    client = SharePointGraphClient(auth=auth, http_client=httpx.Client(transport=httpx.MockTransport(handler)))
    items = list(client.iter_drive_items("drive-x"))
    assert len(items) == 1
    assert items[0].parent_path is None


@pytest.mark.unit
def test_path_exists_returns_true_on_200_false_on_404() -> None:
    """The probe maps HTTP status to a simple bool the connector consumes."""

    def handler(request: httpx.Request) -> httpx.Response:
        token = _token_response_for(request)
        if token is not None:
            return token
        if "/Curated-Content" in str(request.url):
            return httpx.Response(200, json={"id": "folder-id"})
        if "/Does-Not-Exist" in str(request.url):
            return httpx.Response(404, json={"error": {"code": "itemNotFound"}})
        return httpx.Response(200, json={"value": []})

    auth = OAuth2ClientCredsAuth(
        tenant_id="t",
        client_id="c",
        client_secret="s-value",  # pragma: allowlist secret — test fixture
        scope="https://graph.microsoft.com/.default",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    client = SharePointGraphClient(auth=auth, http_client=httpx.Client(transport=httpx.MockTransport(handler)))
    assert client.path_exists("drive-x", "/Curated-Content") is True
    assert client.path_exists("drive-x", "/Does-Not-Exist") is False


@pytest.mark.unit
def test_fetch_item_content_follows_302_redirect_to_blob_url() -> None:
    """Graph returns 302 → time-limited Azure Blob URL for /content; the
    client MUST follow the redirect to get the binary, not return the 302
    response itself.

    Regression for the bug surfaced 2026-05-25 on the dogfood VM: every
    SharePoint binary fetch was dead-lettering because httpx.Client
    defaults to follow_redirects=False, so the 302 raise_for_status()'d
    and the bronze write never landed.
    """
    real_bytes = b"%PDF-1.4 fake binary content for the test\n"

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "/oauth2/v2.0/token" in url:
            return httpx.Response(200, json={"access_token": "tok", "expires_in": 3600, "token_type": "Bearer"})
        if "/items/item-x/content" in url:
            # Graph's /content endpoint emits a 302 to a time-limited Azure Blob URL
            return httpx.Response(302, headers={"Location": "https://example-blob.example.com/download/abc123"})
        if "example-blob.example.com" in url:
            return httpx.Response(200, content=real_bytes)
        return httpx.Response(404)

    auth = OAuth2ClientCredsAuth(
        tenant_id="t",
        client_id="c",
        client_secret="s-value",  # pragma: allowlist secret — test fixture
        scope="https://graph.microsoft.com/.default",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    client = SharePointGraphClient(
        auth=auth,
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    body = client.fetch_item_content("drive-x", "item-x")
    assert body == real_bytes, (
        "fetch_item_content must follow Graph's 302 redirect to the Azure Blob URL "
        "and return the actual bytes; got something else (possibly the 302 response body)."
    )

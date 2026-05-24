"""Unit tests for :class:`kairix.connectors.sharepoint.SharePointConnector`.

Scope:

  * Drive-delta response with one envelope → ``list_changes(None)``
    emits one ``created`` event; cursor advances to a JSON-encoded
    per-drive deltaLink map.
  * ``deleted`` envelopes surface as ``deleted`` ChangeEvent items
    carrying the source drive id in metadata.
  * ``fetch`` reads the per-tick envelope cache; misses raise KeyError
    with the fix-pointer message.
  * ``source_link`` returns the SharePoint webUrl when the envelope
    carries one; falls back to a ``sharepoint://`` URI otherwise.
  * Capability-mix-in shims (CheckpointedConnector,
    CredentialsConnector, OAuthConnector) match the documented surface.
  * ``make_connector`` validates the config — empty drives, malformed
    drive entries, and invalid sensitivity all raise with the
    fix-pointer template.

F1-clean (no monkey-patching), F6-clean (every test seam is a real
callable default), F8 carries ``@pytest.mark.unit``.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from kairix.connectors.sharepoint import (
    DEFAULT_SENSITIVITY,
    SharePointConnector,
    SharePointCredentials,
    SharePointDriveSpec,
    SharePointGraphClient,
    make_connector,
)
from kairix.core.protocols import Container
from kairix.transport.auth.oauth2_client_creds import OAuth2ClientCredsAuth

pytestmark = pytest.mark.unit


_DRIVE_ID = "b!unit-drive"


def _file_envelope(item_id: str, *, deleted: bool = False, name: str = "doc.pdf") -> dict[str, Any]:
    if deleted:
        return {"id": item_id, "deleted": {"state": "deleted"}, "parentReference": {"driveId": _DRIVE_ID}}
    return {
        "id": item_id,
        "name": name,
        "size": 100,
        "lastModifiedDateTime": "2026-05-22T10:00:00Z",
        "webUrl": f"https://contoso.sharepoint.com/sites/team/Documents/{name}",
        "file": {"mimeType": "application/pdf"},
        "parentReference": {"driveId": _DRIVE_ID},
    }


def _delta_response(items: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "@odata.context": f"https://graph.microsoft.com/v1.0/$metadata#drives/{_DRIVE_ID}/root/delta",
        "value": items,
        "@odata.deltaLink": f"https://graph.microsoft.com/v1.0/drives/{_DRIVE_ID}/root/delta?$deltatoken=unit",
    }


def _build_connector(handler: Any) -> SharePointConnector:
    transport = httpx.MockTransport(handler)
    shared = httpx.Client(transport=transport)
    auth = OAuth2ClientCredsAuth(
        tenant_id="t",
        client_id="c",
        client_secret="s-value",  # pragma: allowlist secret — test fixture
        scope="https://graph.microsoft.com/.default",
        http_client=shared,
    )
    return SharePointConnector(
        drives=[SharePointDriveSpec(drive_id=_DRIVE_ID)],
        credentials=SharePointCredentials(
            tenant_id="t",
            client_id="c",
            client_secret="s-value",  # pragma: allowlist secret — test fixture
        ),
        auth=auth,
        client_builder=lambda a: SharePointGraphClient(auth=a, http_client=shared),
    )


def _token_response(request: httpx.Request) -> httpx.Response | None:
    """Return a fake token response when the request is to the OAuth2 endpoint, else None."""
    if "/oauth2/v2.0/token" in str(request.url):
        return httpx.Response(200, json={"access_token": "fake-bearer", "expires_in": 3600, "token_type": "Bearer"})
    return None


def test_list_changes_emits_created_for_file_envelope() -> None:
    """A file envelope surfaces as a created ChangeEvent with drive metadata.

    Sabotage proof: setting the connector's ``_default_sensitivity`` to
    something other than ``"internal"`` would flip the metadata
    assertion. The default is the documented internal tier.
    """

    def _handler(request: httpx.Request) -> httpx.Response:
        token = _token_response(request)
        if token is not None:
            return token
        return httpx.Response(200, json=_delta_response([_file_envelope("01ITEMA")]))

    connector = _build_connector(_handler)
    events = list(connector.list_changes(cursor=None))
    assert len(events) == 1
    assert events[0].op == "created"
    assert events[0].item_id == "01ITEMA"
    assert events[0].metadata["drive_id"] == _DRIVE_ID
    assert events[0].metadata["sensitivity"] == "internal"
    assert events[0].metadata["mime"] == "application/pdf"


def test_list_changes_emits_deleted_for_tombstone_envelope() -> None:
    """A ``deleted`` envelope surfaces as a ``deleted`` ChangeEvent.

    Sabotage proof: flipping ``_item_to_event`` to ignore the
    ``removed`` flag drops the deleted event entirely; the assertion
    on ``op == 'deleted'`` would fail.
    """

    def _handler(request: httpx.Request) -> httpx.Response:
        token = _token_response(request)
        if token is not None:
            return token
        return httpx.Response(200, json=_delta_response([_file_envelope("01TOMB", deleted=True)]))

    connector = _build_connector(_handler)
    events = list(connector.list_changes(cursor=None))
    assert len(events) == 1
    assert events[0].op == "deleted"
    assert events[0].item_id == "01TOMB"


def test_list_changes_returns_empty_when_only_empty_id_envelopes() -> None:
    """Envelopes missing ``id`` are dropped at the per-item filter."""

    def _handler(request: httpx.Request) -> httpx.Response:
        token = _token_response(request)
        if token is not None:
            return token
        return httpx.Response(
            200,
            json={
                "value": [{"name": "no-id.pdf", "parentReference": {"driveId": _DRIVE_ID}}],
                "@odata.deltaLink": "https://graph.microsoft.com/v1.0/.../delta?$deltatoken=empty",
            },
        )

    connector = _build_connector(_handler)
    events = list(connector.list_changes(cursor=None))
    assert events == []


def test_next_cursor_round_trips_per_drive_delta_link() -> None:
    """The next-tick cursor is a JSON map keyed by drive id."""

    def _handler(request: httpx.Request) -> httpx.Response:
        token = _token_response(request)
        if token is not None:
            return token
        return httpx.Response(200, json=_delta_response([_file_envelope("01CURSOR")]))

    connector = _build_connector(_handler)
    list(connector.list_changes(cursor=None))
    cursor = connector.next_cursor()
    assert cursor is not None
    parsed = json.loads(cursor)
    assert _DRIVE_ID in parsed
    assert "deltatoken=unit" in parsed[_DRIVE_ID]


def test_list_changes_resumes_from_serialised_cursor() -> None:
    """A serialised cursor map drives the per-drive deltaLink lookup."""
    seen_urls: list[str] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        token = _token_response(request)
        if token is not None:
            return token
        seen_urls.append(str(request.url))
        return httpx.Response(200, json=_delta_response([_file_envelope("01RESUME")]))

    connector = _build_connector(_handler)
    cursor = json.dumps({_DRIVE_ID: "https://graph.microsoft.com/v1.0/drives/x/root/delta?$deltatoken=prior"})
    list(connector.list_changes(cursor=cursor))
    assert any("deltatoken=prior" in url for url in seen_urls), f"resume URL not requested; saw {seen_urls!r}"


def test_fetch_downloads_binary_content_for_cached_envelope() -> None:
    """``fetch`` resolves the per-tick envelope cache and downloads bytes."""

    def _handler(request: httpx.Request) -> httpx.Response:
        token = _token_response(request)
        if token is not None:
            return token
        if "/content" in str(request.url):
            return httpx.Response(200, content=b"%PDF-1.4 fake fetched content")
        return httpx.Response(200, json=_delta_response([_file_envelope("01FETCH")]))

    connector = _build_connector(_handler)
    list(connector.list_changes(cursor=None))
    artefact = connector.fetch("01FETCH")
    assert artefact.raw.startswith(b"%PDF")
    assert artefact.mime == "application/pdf"


def test_fetch_raises_key_error_when_cache_miss() -> None:
    """``fetch`` raises KeyError with the fix-pointer message when the
    envelope cache doesn't carry the requested id.
    """

    def _handler(request: httpx.Request) -> httpx.Response:
        token = _token_response(request)
        if token is not None:
            return token
        return httpx.Response(200, json=_delta_response([]))

    connector = _build_connector(_handler)
    list(connector.list_changes(cursor=None))  # empty drain
    with pytest.raises(KeyError, match="not in the per-tick envelope cache"):
        connector.fetch("01MISSING")


def test_source_link_returns_web_url_when_envelope_carries_one() -> None:
    """``source_link`` returns the SharePoint webUrl from the envelope."""

    def _handler(request: httpx.Request) -> httpx.Response:
        token = _token_response(request)
        if token is not None:
            return token
        return httpx.Response(200, json=_delta_response([_file_envelope("01LINK", name="link.pdf")]))

    connector = _build_connector(_handler)
    list(connector.list_changes(cursor=None))
    link = connector.source_link("01LINK")
    assert link == "https://contoso.sharepoint.com/sites/team/Documents/link.pdf"


def test_source_link_falls_back_to_sharepoint_uri_when_envelope_missing() -> None:
    """``source_link`` for an id not in the cache falls back to sharepoint://."""

    def _handler(request: httpx.Request) -> httpx.Response:
        token = _token_response(request)
        if token is not None:
            return token
        return httpx.Response(200, json=_delta_response([]))

    connector = _build_connector(_handler)
    list(connector.list_changes(cursor=None))
    assert connector.source_link("absent") == "sharepoint://items/absent"


def test_source_link_falls_back_to_drive_item_uri_when_web_url_missing() -> None:
    """``source_link`` falls back to a drive+item URI when webUrl is absent."""

    def _handler(request: httpx.Request) -> httpx.Response:
        token = _token_response(request)
        if token is not None:
            return token
        envelope = _file_envelope("01NOURL")
        envelope.pop("webUrl")
        return httpx.Response(200, json=_delta_response([envelope]))

    connector = _build_connector(_handler)
    list(connector.list_changes(cursor=None))
    link = connector.source_link("01NOURL")
    assert link == f"sharepoint://{_DRIVE_ID}/items/01NOURL"


def test_sensitivity_for_returns_configured_default() -> None:
    """``sensitivity_for`` returns the operator-configured default tier."""

    def _handler(request: httpx.Request) -> httpx.Response:
        token = _token_response(request)
        if token is not None:
            return token
        return httpx.Response(200, json=_delta_response([]))

    connector = _build_connector(_handler)
    assert connector.sensitivity_for("anything") == DEFAULT_SENSITIVITY


def test_constructor_rejects_empty_drives_list() -> None:
    """The constructor raises with a fix-pointer message on empty drives."""
    with pytest.raises(ValueError, match="drives list is empty"):
        SharePointConnector(drives=[])


def test_default_client_builder_uses_provided_auth_with_no_explicit_credentials() -> None:
    """The constructor falls through to the default SharePointGraphClient when
    no client_builder is provided AND uses the operator-supplied auth.
    """
    auth = OAuth2ClientCredsAuth(
        tenant_id="t",
        client_id="c",
        client_secret="s-value",  # pragma: allowlist secret — test fixture
        scope="https://graph.microsoft.com/.default",
        http_client=httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"value": []}))),
    )
    connector = SharePointConnector(drives=[SharePointDriveSpec(drive_id=_DRIVE_ID)], auth=auth)
    # The constructor completed without raising — the default client_builder
    # path executed successfully.
    assert connector.name == "sharepoint"


def test_load_from_checkpoint_delegates_to_list_changes() -> None:
    """CheckpointedConnector shim forwards the checkpoint to list_changes."""

    def _handler(request: httpx.Request) -> httpx.Response:
        token = _token_response(request)
        if token is not None:
            return token
        return httpx.Response(200, json=_delta_response([_file_envelope("01CHKPT")]))

    connector = _build_connector(_handler)
    container = Container(
        cc_pair_id=1, container_id=_DRIVE_ID, access_state="ACCESSIBLE", cursor_token=None, last_synced_at=None
    )
    events = list(connector.load_from_checkpoint(container, checkpoint=None))
    assert len(events) == 1
    assert events[0].item_id == "01CHKPT"


def test_load_credentials_returns_input_unchanged() -> None:
    """CredentialsConnector shim is a passthrough (no transformation)."""

    def _handler(_r: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_delta_response([]))

    connector = _build_connector(_handler)
    creds = {"tenant_id": "t", "client_id": "c", "client_secret": "s"}  # pragma: allowlist secret
    assert connector.load_credentials(creds) == creds


def test_oauth_authorization_url_raises_for_client_credentials_only() -> None:
    """OAuth user-flow methods raise NotImplementedError with a fix pointer."""
    with pytest.raises(NotImplementedError, match="client-credentials flow only"):
        SharePointConnector.oauth_authorization_url("state-token")


def test_oauth_code_to_token_raises_for_client_credentials_only() -> None:
    """Counterpart to oauth_authorization_url — same fix-pointer message."""
    with pytest.raises(NotImplementedError, match="client-credentials flow only"):
        SharePointConnector.oauth_code_to_token("code")


def test_next_cursor_is_none_when_no_drive_completes_a_delta_sweep() -> None:
    """When no drive yields a deltaLink (empty drive list, etc.), next_cursor is None."""

    # Empty-response handler — no items, no deltaLink
    def _handler(request: httpx.Request) -> httpx.Response:
        token = _token_response(request)
        if token is not None:
            return token
        return httpx.Response(200, json={"value": []})

    connector = _build_connector(_handler)
    list(connector.list_changes(cursor=None))
    assert connector.next_cursor() is None


def test_make_connector_with_string_drives_list_parses_specs(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    """``make_connector`` parses a list of drive_id strings into typed specs.

    Drives ``_resolve_credentials_from_secrets`` through the per-file
    secret resolver — write the three required secrets to a fake XDG
    secrets directory, then construct the connector. F2-clean: only
    ``XDG_CONFIG_HOME`` is set (not a ``KAIRIX_*`` env var).
    """
    secrets_dir = tmp_path / "xdg" / "kairix" / "secrets"
    secrets_dir.mkdir(parents=True)
    (secrets_dir / "connector-m365-tenant-id").write_text("fake-tenant\n")
    (secrets_dir / "connector-m365-client-id").write_text("fake-client\n")
    (secrets_dir / "connector-m365-client-secret").write_text("fake-secret-value\n")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))

    connector = make_connector({"drives": [_DRIVE_ID]})
    assert connector.name == "sharepoint"
    assert connector.sensitivity_for("any") == DEFAULT_SENSITIVITY


def test_make_connector_rejects_missing_drives() -> None:
    """``make_connector`` raises with the fix-pointer when drives is absent."""
    with pytest.raises(ValueError, match="'drives' must be a non-empty list"):
        make_connector({})


def test_make_connector_rejects_empty_drives_list() -> None:
    """``make_connector`` raises with the fix-pointer on empty drives list."""
    with pytest.raises(ValueError, match="'drives' must be a non-empty list"):
        make_connector({"drives": []})


def test_make_connector_rejects_invalid_sensitivity_tier() -> None:
    """``make_connector`` raises when default_sensitivity is not a valid F39 tier."""
    with pytest.raises(ValueError, match="default_sensitivity"):
        make_connector({"drives": [_DRIVE_ID], "default_sensitivity": "bogus"})


def test_make_connector_with_dict_drive_entries_parses_specs(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    """make_connector accepts dict drive entries with drive_id + site_id + display_name."""
    secrets_dir = tmp_path / "xdg" / "kairix" / "secrets"
    secrets_dir.mkdir(parents=True)
    (secrets_dir / "connector-m365-tenant-id").write_text("fake-tenant\n")
    (secrets_dir / "connector-m365-client-id").write_text("fake-client\n")
    (secrets_dir / "connector-m365-client-secret").write_text("fake-secret-value\n")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))

    connector = make_connector(
        {
            "drives": [
                {"drive_id": "id-1", "site_id": "site-1", "display_name": "Marketing"},
            ]
        }
    )
    assert connector.name == "sharepoint"


def test_make_connector_rejects_dict_drive_without_drive_id() -> None:
    """make_connector raises with the fix-pointer on a dict drive missing drive_id."""
    with pytest.raises(ValueError, match="drive_id"):
        make_connector({"drives": [{"site_id": "site-1"}]})


def test_make_connector_rejects_non_string_non_dict_drive_entries() -> None:
    """make_connector raises with the fix-pointer on numeric drive entries."""
    with pytest.raises(ValueError, match="is not a string or dict"):
        make_connector({"drives": [42]})


def test_list_changes_round_trips_cursor_with_two_drives(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    """The per-drive cursor map round-trips when the connector is
    configured with two drives — driven through the public
    ``next_cursor()`` accessor and a subsequent ``list_changes(cursor=...)``
    rather than direct serialise / deserialise calls.
    """
    secrets_dir = tmp_path / "xdg" / "kairix" / "secrets"
    secrets_dir.mkdir(parents=True)
    (secrets_dir / "connector-m365-tenant-id").write_text("fake-tenant\n")
    (secrets_dir / "connector-m365-client-id").write_text("fake-client\n")
    (secrets_dir / "connector-m365-client-secret").write_text("fake-secret-value\n")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))

    seen_urls: list[str] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        token = _token_response(request)
        if token is not None:
            return token
        seen_urls.append(str(request.url))
        # Tag the deltaLink with the drive id so the round-trip can
        # assert each drive resumed from its own checkpoint.
        url = str(request.url)
        if "drive-a" in url:
            link = "https://graph.microsoft.com/v1.0/drives/drive-a/root/delta?$deltatoken=tok-a"
        else:
            link = "https://graph.microsoft.com/v1.0/drives/drive-b/root/delta?$deltatoken=tok-b"
        return httpx.Response(200, json={"value": [], "@odata.deltaLink": link})

    transport = httpx.MockTransport(_handler)
    shared = httpx.Client(transport=transport)
    auth = OAuth2ClientCredsAuth(
        tenant_id="t",
        client_id="c",
        client_secret="s-value",  # pragma: allowlist secret — test fixture
        scope="https://graph.microsoft.com/.default",
        http_client=shared,
    )
    connector = SharePointConnector(
        drives=[SharePointDriveSpec(drive_id="drive-a"), SharePointDriveSpec(drive_id="drive-b")],
        auth=auth,
        client_builder=lambda a: SharePointGraphClient(auth=a, http_client=shared),
    )
    list(connector.list_changes(cursor=None))
    cursor = connector.next_cursor()
    assert cursor is not None
    parsed = json.loads(cursor)
    assert set(parsed.keys()) == {"drive-a", "drive-b"}
    assert "tok-a" in parsed["drive-a"]
    assert "tok-b" in parsed["drive-b"]

    # Round-trip — feed the cursor back in and the connector resumes
    # each drive at the right deltaLink.
    seen_urls.clear()
    list(connector.list_changes(cursor=cursor))
    # Each drive's resume URL is hit exactly once (single-page response).
    assert any("tok-a" in u for u in seen_urls)
    assert any("tok-b" in u for u in seen_urls)


def test_list_changes_tolerates_malformed_legacy_cursor_string() -> None:
    """A non-JSON cursor (legacy single-string) is treated as cold-start.

    This pins the deserialise tolerance via the public list_changes
    surface rather than the private helper — invoking list_changes
    with a non-JSON cursor exercises the same fall-through.
    """

    def _handler(request: httpx.Request) -> httpx.Response:
        token = _token_response(request)
        if token is not None:
            return token
        return httpx.Response(200, json=_delta_response([]))

    connector = _build_connector(_handler)
    # Empty list — no items, but the call MUST NOT raise on the malformed cursor.
    events = list(connector.list_changes(cursor="not-a-json-cursor"))
    assert events == []


def test_list_changes_preserves_prior_cursor_when_no_delta_link_yielded() -> None:
    """When a drive's delta sweep yields no new deltaLink (empty page,
    no terminal envelope), the connector keeps the prior cursor so the
    next tick resumes from the same point instead of re-running the
    full sync.
    """

    # The handler returns an empty page WITHOUT a deltaLink.
    def _handler(request: httpx.Request) -> httpx.Response:
        token = _token_response(request)
        if token is not None:
            return token
        return httpx.Response(200, json={"value": []})

    connector = _build_connector(_handler)
    prior = json.dumps({_DRIVE_ID: "https://graph.microsoft.com/v1.0/drives/x/root/delta?$deltatoken=prior"})
    list(connector.list_changes(cursor=prior))
    cursor = connector.next_cursor()
    assert cursor is not None, "cursor must be preserved when no new delta link arrives"
    parsed = json.loads(cursor)
    assert _DRIVE_ID in parsed
    assert "deltatoken=prior" in parsed[_DRIVE_ID]

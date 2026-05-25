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


# ---------------------------------------------------------------------------
# Path filtering — pure helpers
# ---------------------------------------------------------------------------


@pytest.mark.unit
def testpath_passes_filter_empty_filter_admits_everything() -> None:
    """No include + no exclude → every item passes (current behaviour)."""
    from kairix.connectors.sharepoint.connector import path_passes_filter

    assert path_passes_filter("/anything.md", include_paths=(), exclude_paths=()) is True
    assert path_passes_filter(None, include_paths=(), exclude_paths=()) is True


@pytest.mark.unit
def testpath_passes_filter_include_segment_boundary_match() -> None:
    """`/Foo` matches `/Foo/bar` and `/Foo` itself but NOT `/Foo-Backup`."""
    from kairix.connectors.sharepoint.connector import path_passes_filter

    inc = ("/Curated-Content",)
    assert path_passes_filter("/Curated-Content", include_paths=inc, exclude_paths=()) is True
    assert path_passes_filter("/Curated-Content/nested/file.md", include_paths=inc, exclude_paths=()) is True
    assert path_passes_filter("/Curated-Content-Backup/file.md", include_paths=inc, exclude_paths=()) is False
    assert path_passes_filter("/Other/file.md", include_paths=inc, exclude_paths=()) is False


@pytest.mark.unit
def testpath_passes_filter_multiple_includes_form_union() -> None:
    """Multiple include paths combine — match any one wins."""
    from kairix.connectors.sharepoint.connector import path_passes_filter

    inc = ("/A", "/B")
    assert path_passes_filter("/A/x.md", include_paths=inc, exclude_paths=()) is True
    assert path_passes_filter("/B/y.md", include_paths=inc, exclude_paths=()) is True
    assert path_passes_filter("/C/z.md", include_paths=inc, exclude_paths=()) is False


@pytest.mark.unit
def testpath_passes_filter_exclude_overrides_include() -> None:
    """Exclude wins when both include and exclude prefix match."""
    from kairix.connectors.sharepoint.connector import path_passes_filter

    inc = ("/Curated-Content",)
    exc = ("/Curated-Content/draft",)
    assert path_passes_filter("/Curated-Content/architecture.md", include_paths=inc, exclude_paths=exc) is True
    assert path_passes_filter("/Curated-Content/draft/spike.md", include_paths=inc, exclude_paths=exc) is False
    assert path_passes_filter("/Curated-Content/draft", include_paths=inc, exclude_paths=exc) is False


@pytest.mark.unit
def testpath_passes_filter_standalone_exclude_drops_matches() -> None:
    """Exclude without an include still filters — everything else passes."""
    from kairix.connectors.sharepoint.connector import path_passes_filter

    exc = ("/Vendor-Bulk-Materials",)
    assert path_passes_filter("/Curated-Content/a.md", include_paths=(), exclude_paths=exc) is True
    assert path_passes_filter("/Vendor-Bulk-Materials/deck.pptx", include_paths=(), exclude_paths=exc) is False


@pytest.mark.unit
def testpath_passes_filter_none_item_path_dropped_when_filter_active() -> None:
    """Item with no path is dropped when include is set — safe default."""
    from kairix.connectors.sharepoint.connector import path_passes_filter

    assert path_passes_filter(None, include_paths=("/Foo",), exclude_paths=()) is False
    # Exclude-only with no path: pass (we don't know it matches anything to exclude)
    assert path_passes_filter(None, include_paths=(), exclude_paths=("/Foo",)) is True


@pytest.mark.unit
def testpath_passes_filter_is_case_insensitive() -> None:
    """SharePoint paths are case-preserving but case-insensitive — match accordingly."""
    from kairix.connectors.sharepoint.connector import path_passes_filter

    inc = ("/Curated-Content",)
    assert path_passes_filter("/curated-content/a.md", include_paths=inc, exclude_paths=()) is True
    assert path_passes_filter("/CURATED-CONTENT/B.MD", include_paths=inc, exclude_paths=()) is True


# ---------------------------------------------------------------------------
# Path filtering — parser + overlap validation
# ---------------------------------------------------------------------------


@pytest.mark.unit
def testparse_drive_entry_accepts_include_and_exclude_paths() -> None:
    """include_paths + exclude_paths flow through parse_drive_entry as tuples."""
    from kairix.connectors.sharepoint.connector import parse_drive_entry

    spec = parse_drive_entry(
        {
            "drive_id": "b!d",
            "include_paths": ["/Curated-Content", "/Shared Documents"],
            "exclude_paths": ["/Curated-Content/draft"],
        }
    )
    assert spec.include_paths == ("/Curated-Content", "/Shared Documents")
    assert spec.exclude_paths == ("/Curated-Content/draft",)


@pytest.mark.unit
def testparse_drive_entry_strips_trailing_slashes_in_paths() -> None:
    """Trailing slashes are stripped so /Foo and /Foo/ behave identically."""
    from kairix.connectors.sharepoint.connector import parse_drive_entry

    spec = parse_drive_entry({"drive_id": "b!d", "include_paths": ["/Foo/"]})
    assert spec.include_paths == ("/Foo",)


@pytest.mark.unit
def testparse_drive_entry_rejects_path_without_leading_slash() -> None:
    """A path missing the leading slash gets a fix-pointer error."""
    from kairix.connectors.sharepoint.connector import parse_drive_entry

    with pytest.raises(ValueError, match="must start with '/'"):
        parse_drive_entry({"drive_id": "b!d", "include_paths": ["Curated-Content"]})


@pytest.mark.unit
def testparse_drive_entry_rejects_exact_overlap_between_include_and_exclude() -> None:
    """include + exclude pointing at the same exact path is almost always a typo."""
    from kairix.connectors.sharepoint.connector import parse_drive_entry

    with pytest.raises(ValueError, match="include_paths and exclude_paths both contain"):
        parse_drive_entry(
            {
                "drive_id": "b!d",
                "include_paths": ["/Foo"],
                "exclude_paths": ["/Foo"],
            }
        )


@pytest.mark.unit
def testparse_drive_entry_allows_exclude_inside_include() -> None:
    """Exclude as a strict child of include is the intended use case."""
    from kairix.connectors.sharepoint.connector import parse_drive_entry

    spec = parse_drive_entry(
        {
            "drive_id": "b!d",
            "include_paths": ["/Foo"],
            "exclude_paths": ["/Foo/draft"],
        }
    )
    assert spec.include_paths == ("/Foo",)
    assert spec.exclude_paths == ("/Foo/draft",)


# ---------------------------------------------------------------------------
# display_name synthesis
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_effective_display_name_uses_explicit_value_when_set() -> None:
    """An operator-supplied display_name wins over any synthesis."""
    handler = _make_handler_returning_empty_delta()
    connector = _build_connector_with_spec(
        handler,
        SharePointDriveSpec(drive_id=_DRIVE_ID, display_name="My Custom Label", include_paths=("/Foo",)),
    )
    assert connector._effective_display_name(connector._drives[0]) == "My Custom Label"


@pytest.mark.unit
def test_effective_display_name_synthesises_from_include_path_when_unset() -> None:
    """No display_name + include_paths → '<drive-id-prefix> [<first-include>]'."""
    handler = _make_handler_returning_empty_delta()
    connector = _build_connector_with_spec(
        handler,
        SharePointDriveSpec(drive_id="b!a0rphFH2longopaqueidentifier", include_paths=("/Curated-Content",)),
    )
    label = connector._effective_display_name(connector._drives[0])
    assert "Curated-Content" in label
    assert "b!a0rphF" in label  # short prefix preserved


@pytest.mark.unit
def test_effective_display_name_falls_back_to_drive_id_when_no_filter() -> None:
    """No display_name + no include_paths → legacy drive_id label (back-compat)."""
    handler = _make_handler_returning_empty_delta()
    connector = _build_connector_with_spec(
        handler,
        SharePointDriveSpec(drive_id="b!short"),
    )
    assert connector._effective_display_name(connector._drives[0]) == "b!short"


# ---------------------------------------------------------------------------
# End-to-end through list_changes — filter active
# ---------------------------------------------------------------------------


def _file_envelope_with_path(item_id: str, *, parent_path: str, name: str = "doc.md") -> dict[str, Any]:
    return {
        "id": item_id,
        "name": name,
        "size": 100,
        "lastModifiedDateTime": "2026-05-22T10:00:00Z",
        "webUrl": f"https://contoso.sharepoint.com/sites/team/Documents{parent_path}/{name}",
        "file": {"mimeType": "text/markdown"},
        "parentReference": {
            "driveId": _DRIVE_ID,
            "path": f"/drives/{_DRIVE_ID}/root:{parent_path}",
        },
    }


def _make_handler_returning_empty_delta() -> Any:
    def handler(request: httpx.Request) -> httpx.Response:
        token = _token_response(request)
        if token:
            return token
        return httpx.Response(200, json=_delta_response([]))

    return handler


def _build_connector_with_spec(handler: Any, spec: SharePointDriveSpec) -> SharePointConnector:
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
        drives=[spec],
        credentials=SharePointCredentials(
            tenant_id="t",
            client_id="c",
            client_secret="s-value",  # pragma: allowlist secret — test fixture
        ),
        auth=auth,
        client_builder=lambda a: SharePointGraphClient(auth=a, http_client=shared),
    )


@pytest.mark.unit
def test_list_changes_with_include_path_drops_items_outside_scope() -> None:
    """Items whose parent path doesn't match include_paths are skipped end-to-end."""

    def handler(request: httpx.Request) -> httpx.Response:
        token = _token_response(request)
        if token:
            return token
        # Probe call for the include path — return 200 so startup is clean
        if "/root:/Curated-Content" in str(request.url) and "delta" not in str(request.url):
            return httpx.Response(200, json={"id": "folder-id", "name": "Curated-Content"})
        return httpx.Response(
            200,
            json=_delta_response(
                [
                    _file_envelope_with_path("item-1", parent_path="/Curated-Content", name="a.md"),
                    _file_envelope_with_path("item-2", parent_path="/Vendor-Bulk-Materials", name="b.pptx"),
                    _file_envelope_with_path("item-3", parent_path="/Curated-Content/nested", name="c.md"),
                ]
            ),
        )

    connector = _build_connector_with_spec(
        handler,
        SharePointDriveSpec(drive_id=_DRIVE_ID, include_paths=("/Curated-Content",)),
    )
    events = list(connector.list_changes(None))
    assert len(events) == 2
    emitted_ids = {e.item_id for e in events}
    assert emitted_ids == {"item-1", "item-3"}


@pytest.mark.unit
def test_list_changes_with_exclude_path_drops_matching_items() -> None:
    """exclude_paths drops items even when no include is set."""

    def handler(request: httpx.Request) -> httpx.Response:
        token = _token_response(request)
        if token:
            return token
        return httpx.Response(
            200,
            json=_delta_response(
                [
                    _file_envelope_with_path("keep-1", parent_path="/Curated-Content", name="a.md"),
                    _file_envelope_with_path("drop-1", parent_path="/Vendor-Bulk-Materials", name="b.pptx"),
                ]
            ),
        )

    connector = _build_connector_with_spec(
        handler,
        SharePointDriveSpec(drive_id=_DRIVE_ID, exclude_paths=("/Vendor-Bulk-Materials",)),
    )
    events = list(connector.list_changes(None))
    assert {e.item_id for e in events} == {"keep-1"}


@pytest.mark.unit
def test_list_changes_unfiltered_preserves_prior_behaviour() -> None:
    """Empty include+exclude — every emitted-eligible item lands (regression check)."""

    def handler(request: httpx.Request) -> httpx.Response:
        token = _token_response(request)
        if token:
            return token
        return httpx.Response(
            200,
            json=_delta_response(
                [
                    _file_envelope_with_path("a", parent_path="/Curated-Content", name="x.md"),
                    _file_envelope_with_path("b", parent_path="/Vendor-Bulk-Materials", name="y.pptx"),
                ]
            ),
        )

    connector = _build_connector_with_spec(handler, SharePointDriveSpec(drive_id=_DRIVE_ID))
    events = list(connector.list_changes(None))
    assert len(events) == 2


# ---------------------------------------------------------------------------
# Startup probe — warns on missing include_path
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_init_probe_warns_when_include_path_returns_404(caplog: pytest.LogCaptureFixture) -> None:
    """Connector __init__ probes each include path; 404 logs a warning."""
    import logging

    def handler(request: httpx.Request) -> httpx.Response:
        token = _token_response(request)
        if token:
            return token
        if "/root:/Does-Not-Exist" in str(request.url):
            return httpx.Response(404, json={"error": {"code": "itemNotFound"}})
        if "/root:/Curated-Content" in str(request.url):
            return httpx.Response(200, json={"id": "ok"})
        return httpx.Response(200, json=_delta_response([]))

    with caplog.at_level(logging.WARNING, logger="kairix.connectors.sharepoint.connector"):
        _build_connector_with_spec(
            handler,
            SharePointDriveSpec(
                drive_id=_DRIVE_ID,
                include_paths=("/Curated-Content", "/Does-Not-Exist"),
            ),
        )

    warning_records = [r for r in caplog.records if "sharepoint_probe_missing_folder" in r.getMessage()]
    assert len(warning_records) == 1
    assert "/Does-Not-Exist" in warning_records[0].getMessage()


@pytest.mark.unit
def test_init_probe_swallows_transient_errors_without_failing_init() -> None:
    """Network failure during probe must not block connector construction."""

    def handler(request: httpx.Request) -> httpx.Response:
        token = _token_response(request)
        if token:
            return token
        if "/root:" in str(request.url) and "delta" not in str(request.url):
            return httpx.Response(503, json={"error": {"code": "serviceUnavailable"}})
        return httpx.Response(200, json=_delta_response([]))

    # Constructor must not raise even when the probe call fails
    connector = _build_connector_with_spec(
        handler,
        SharePointDriveSpec(drive_id=_DRIVE_ID, include_paths=("/Foo",)),
    )
    assert connector is not None

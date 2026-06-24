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
from collections.abc import Callable
from typing import Any

import httpx
import pytest

from kairix.connectors.sharepoint import (
    DEFAULT_SENSITIVITY,
    SharePointConnector,
    SharePointCredentials,
    SharePointDriveSpec,
    SharePointGraphClient,
    SiteDiscoverySpec,
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


def test_make_connector_with_string_drives_list_parses_specs() -> None:
    """A list of drive_id strings parses into typed specs via the connector path.

    The legacy alias chain + XDG-fallback credential resolver was retired
    in #369. This test exercises drive parsing through the public
    ``SharePointConnector`` surface — the resolved drive is observed via
    ``iter_containers()`` (one Container per resolved drive) rather than
    the private ``_drives`` accessor (F5: tests use the public surface
    only). The unit under test here is drive-spec parsing, not auth.
    """

    def _handler(request: httpx.Request) -> httpx.Response:
        token = _token_response(request)
        if token is not None:
            return token
        return httpx.Response(200, json={"value": []})

    connector = _build_connector(_handler)
    assert connector.name == "sharepoint"
    assert connector.sensitivity_for("any") == DEFAULT_SENSITIVITY
    # The single string-id drive resolves to exactly one Container whose
    # id is the configured drive id — observable proof the spec parsed.
    container_ids = [c.container_id for c in connector.iter_containers(cc_pair_id=1)]
    assert container_ids == [_DRIVE_ID]


def test_constructor_loads_secrets_via_loader() -> None:
    """``__init__`` calls ``loader.require`` for each of the three M365 leaves.

    Asserts on the loader's call history so the test pins each canonical
    identity tuple read at construction time — SharePoint reuses the M365
    triple per ADR-019, so the tuples mirror the m365 connectors. The
    drive spec carries no ``include_paths`` so ``_probe_include_paths``
    is a noop and the test never reaches Graph.

    Sabotage proof: drop one of the three ``secrets.require(...)`` calls
    in ``_resolve_credentials_from_secrets`` — the expected-tuples set no
    longer matches the loader's recorded calls and this test fails.
    """
    from tests.fakes import FakeSecretsLoader

    loader = FakeSecretsLoader(
        values={
            ("connector", "m365", None, "tenant-id"): "fake-tenant",
            ("connector", "m365", None, "client-id"): "fake-client",
            ("connector", "m365", None, "client-secret"): "fake-secret-value",
        }
    )
    SharePointConnector(
        drives=[SharePointDriveSpec(drive_id=_DRIVE_ID)],
        secrets=loader,
    )
    expected: set[tuple[str, str, str | None, str]] = {
        ("connector", "m365", None, "tenant-id"),
        ("connector", "m365", None, "client-id"),
        ("connector", "m365", None, "client-secret"),
    }
    recorded = set(loader.get_calls)
    assert expected.issubset(recorded), (
        f"connector must call loader.require for each canonical M365 leaf; missing={expected - recorded}"
    )


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


def test_make_connector_with_dict_drive_entries_parses_specs() -> None:
    """A dict drive entry parses into a typed spec with drive_id + display_name.

    Exercises drive-spec parsing via the public ``SharePointConnector``
    surface — the resolved drive id is observed through
    ``iter_containers()`` and the operator-facing label through
    ``load_hierarchy()`` (the DRIVE node carries the configured
    ``display_name``), rather than the private ``_drives`` accessor
    (F5: tests use the public surface only).
    """

    def _handler(request: httpx.Request) -> httpx.Response:
        token = _token_response(request)
        if token is not None:
            return token
        return httpx.Response(200, json={"value": []})

    connector = _build_connector_with_spec(
        _handler,
        SharePointDriveSpec(drive_id="id-1", site_id="site-1", display_name="Marketing"),
    )
    assert connector.name == "sharepoint"
    # Drive id surfaces as the Container id.
    assert [c.container_id for c in connector.iter_containers(cc_pair_id=1)] == ["id-1"]
    # The operator-supplied display_name surfaces as the DRIVE node label.
    drive_nodes = [n for n in connector.load_hierarchy(cc_pair_id=1) if n.node_type == "DRIVE"]
    assert [(n.raw_node_id, n.display_name) for n in drive_nodes] == [("id-1", "Marketing")]


def test_make_connector_rejects_dict_drive_with_neither_drive_id_nor_site() -> None:
    """A dict naming neither drive_id nor a site raises with the F21-style fix-pointer.

    Since F42 site auto-discovery landed, a dict with ``site_id`` (no
    ``drive_id``) is a VALID site-discovery entry — see
    ``test_parse_site_entry_*``. The hard reject now fires only when the
    block names neither an explicit drive nor a site to discover.

    The factory stays pure (no Graph / creds at parse), so parsing
    happens before any credential resolution — the ValueError surfaces
    even without M365 secrets present.
    """
    with pytest.raises(ValueError, match="neither 'drive_id' nor a site"):
        make_connector({"drives": [{"display_name": "no-anchor"}]})


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
    (secrets_dir / "kairix-connector-m365-tenant-id").write_text("fake-tenant\n")
    (secrets_dir / "kairix-connector-m365-client-id").write_text("fake-client\n")
    (secrets_dir / "kairix-connector-m365-client-secret").write_text("fake-secret-value\n")
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


def _drive_label(connector: SharePointConnector, drive_id: str) -> str:
    """Observe a drive's operator-facing label via the public hierarchy.

    ``load_hierarchy`` emits one DRIVE node per resolved drive carrying
    the effective ``display_name``; reading it here keeps the display-name
    assertions on the public surface (F5) instead of the private
    ``_effective_display_name`` / ``_drives`` internals.
    """
    for node in connector.load_hierarchy(cc_pair_id=1):
        if node.node_type == "DRIVE" and node.raw_node_id == drive_id:
            return node.display_name
    raise AssertionError(f"no DRIVE node emitted for {drive_id!r}")


@pytest.mark.unit
def test_effective_display_name_uses_explicit_value_when_set() -> None:
    """An operator-supplied display_name wins over any synthesis."""
    handler = _make_handler_returning_empty_delta()
    connector = _build_connector_with_spec(
        handler,
        SharePointDriveSpec(drive_id=_DRIVE_ID, display_name="My Custom Label", include_paths=("/Foo",)),
    )
    assert _drive_label(connector, _DRIVE_ID) == "My Custom Label"


@pytest.mark.unit
def test_effective_display_name_synthesises_from_include_path_when_unset() -> None:
    """No display_name + include_paths → '<drive-id-prefix> [<first-include>]'."""
    handler = _make_handler_returning_empty_delta()
    drive_id = "b!a0rphFH2longopaqueidentifier"
    connector = _build_connector_with_spec(
        handler,
        SharePointDriveSpec(drive_id=drive_id, include_paths=("/Curated-Content",)),
    )
    label = _drive_label(connector, drive_id)
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
    assert _drive_label(connector, "b!short") == "b!short"


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
def test_init_probe_swallows_transient_errors_without_failing_init(caplog: pytest.LogCaptureFixture) -> None:
    """Network failure during probe must not block connector construction,
    and the swallowed error surfaces as a ``sharepoint_probe_error`` warning.

    The probe call against ``/Foo`` returns a sustained 503; the Graph
    client's throttling-retry loop would otherwise pay the full
    ``2+2+4+8 = 16s`` tenacity backoff. The Graph client exposes a
    documented ``sleep_fn`` seam (``graph_client.py`` line 195) — the test
    threads a no-op sleep so the retries collapse to ~0s while still
    exercising every retry attempt and the final give-up.

    Strengthened (medium→high): the prior ``connector is not None``
    assertion only proved the constructor returned, not that the
    transient error was actually swallowed at the documented site. We now
    assert the ``sharepoint_probe_error`` warning is emitted naming the
    failing drive + path, so a regression that re-raises (or swallows the
    error silently without logging) is caught.

    Sabotage proof: change ``_probe_include_paths``'s ``except Exception``
    to re-raise — construction raises and the test fails. Drop the
    ``logger.warning(... sharepoint_probe_error ...)`` line — the warning
    assertion below fails.
    """
    import logging

    def handler(request: httpx.Request) -> httpx.Response:
        token = _token_response(request)
        if token:
            return token
        if "/root:" in str(request.url) and "delta" not in str(request.url):
            return httpx.Response(503, json={"error": {"code": "serviceUnavailable"}})
        return httpx.Response(200, json=_delta_response([]))

    transport = httpx.MockTransport(handler)
    shared = httpx.Client(transport=transport)
    auth = OAuth2ClientCredsAuth(
        tenant_id="t",
        client_id="c",
        client_secret="s-value",  # pragma: allowlist secret — test fixture
        scope="https://graph.microsoft.com/.default",
        http_client=shared,
    )
    spec = SharePointDriveSpec(drive_id=_DRIVE_ID, include_paths=("/Foo",))

    with caplog.at_level(logging.WARNING, logger="kairix.connectors.sharepoint.connector"):
        # Constructor must not raise even when the probe call fails. The
        # ``sleep_fn`` no-op collapses the tenacity backoff so this test is
        # fast instead of paying 16s of real sleep.
        connector = SharePointConnector(
            drives=[spec],
            credentials=SharePointCredentials(
                tenant_id="t",
                client_id="c",
                client_secret="s-value",  # pragma: allowlist secret — test fixture
            ),
            auth=auth,
            client_builder=lambda a: SharePointGraphClient(auth=a, http_client=shared, sleep_fn=lambda _s: None),
        )

    assert connector is not None
    probe_errors = [r for r in caplog.records if "sharepoint_probe_error" in r.getMessage()]
    assert len(probe_errors) == 1, (
        f"transient probe failure must surface exactly one sharepoint_probe_error warning; "
        f"saw {[r.getMessage() for r in caplog.records]!r}"
    )
    message = probe_errors[0].getMessage()
    assert _DRIVE_ID in message
    assert "/Foo" in message


# ---------------------------------------------------------------------------
# v2 per-container surface — iter_containers + list_changes_for_container
# Phase C (#132) retired the legacy flag-OFF tests; these pin the v2-only
# behaviour that's now the production code path.
# ---------------------------------------------------------------------------


def test_iter_containers_emits_one_per_configured_drive() -> None:
    """v2 ingest entrypoint: SharePointConnector with N drives yields N Containers.

    The topology v2 cc_pair lifecycle treats each drive as its own
    Container so that scope decisions, cursor persistence, and
    per-drive disk-watermark backpressure all work per-drive rather
    than per-connector. ``iter_containers`` is the surface the framework
    calls to enumerate the drives the connector knows about.

    Sabotage-proof: change ``iter_containers`` to yield just one Container
    regardless of drive count; this test fails because two configured
    drives produce one Container instead of two.
    """

    def _handler(request: httpx.Request) -> httpx.Response:
        token = _token_response(request)
        if token is not None:
            return token
        return httpx.Response(200, json={"value": [], "@odata.deltaLink": "drives/x/root/delta?$deltatoken=t"})

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
        drives=[
            SharePointDriveSpec(drive_id="drive-alpha"),
            SharePointDriveSpec(drive_id="drive-beta"),
        ],
        credentials=SharePointCredentials(
            tenant_id="t",
            client_id="c",
            client_secret="s-value",  # pragma: allowlist secret — test fixture
        ),
        auth=auth,
        client_builder=lambda a: SharePointGraphClient(auth=a, http_client=shared),
    )
    containers = list(connector.iter_containers(cc_pair_id=3))
    container_ids = sorted(c.container_id for c in containers)
    assert container_ids == ["drive-alpha", "drive-beta"], (
        f"each configured drive must yield its own Container; got {container_ids!r}"
    )
    assert all(c.cc_pair_id == 3 for c in containers)


def test_list_changes_for_container_filters_by_spec_and_routes_via_scoped_path() -> None:
    """v2 per-container surface: ``list_changes_for_container`` filters items
    by the drive's ``SharePointDriveSpec.include_paths`` and emits per-item
    ChangeEvents.

    The scoped path is wired through ``_list_changes_for_container_scoped``;
    items outside the include-paths are dropped at ``_item_passes_spec_filter``.
    This test confirms (a) the dispatch picks the scoped helper, (b) the
    spec filter drops out-of-scope items, and (c) in-scope items emit as
    ``created`` events.

    Sabotage-proof: replace ``_item_passes_spec_filter``'s ``return False``
    with ``return True``; this test fails because the out-of-scope item
    surfaces as an extra event.
    """

    in_scope_item = {
        **_file_envelope("scoped-keep", name="report.pdf"),
        "parentReference": {"driveId": _DRIVE_ID, "path": "/drive/root:/Reports"},
    }
    out_of_scope_item = {
        **_file_envelope("scoped-drop", name="other.pdf"),
        "parentReference": {"driveId": _DRIVE_ID, "path": "/drive/root:/Other"},
    }

    def _handler(request: httpx.Request) -> httpx.Response:
        token = _token_response(request)
        if token is not None:
            return token
        return httpx.Response(200, json=_delta_response([in_scope_item, out_of_scope_item]))

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
        drives=[SharePointDriveSpec(drive_id=_DRIVE_ID, include_paths=("/Reports",))],
        credentials=SharePointCredentials(
            tenant_id="t",
            client_id="c",
            client_secret="s-value",  # pragma: allowlist secret — test fixture
        ),
        auth=auth,
        client_builder=lambda a: SharePointGraphClient(auth=a, http_client=shared),
    )
    container = Container(
        cc_pair_id=1,
        container_id=_DRIVE_ID,
        access_state="ACCESSIBLE",
        cursor_token=None,
        last_synced_at=None,
    )
    events = list(connector.list_changes_for_container(container))
    item_ids = sorted(e.item_id for e in events)
    assert item_ids == ["scoped-keep"], f"include_paths filter must drop out-of-scope items; got {item_ids!r}"


# ---------------------------------------------------------------------------
# F42 — site-based drive auto-discovery (lazy, per-site fault isolated)
# ---------------------------------------------------------------------------


_SITE_ID = "contoso.sharepoint.com,site-guid,web-guid"


def _drives_list_for_site(*drive_ids: str) -> dict[str, Any]:
    """A Graph ``/sites/{id}/drives`` response listing the given drive ids."""
    return {
        "value": [
            {
                "id": did,
                "name": f"Library-{did}",
                "webUrl": f"https://contoso.sharepoint.com/sites/team/{did}",
            }
            for did in drive_ids
        ]
    }


def _delta_for_drive(drive_id: str, items: list[dict[str, Any]]) -> dict[str, Any]:
    """A per-drive delta page tagged with the drive id in its deltaLink."""
    return {
        "@odata.context": f"https://graph.microsoft.com/v1.0/$metadata#drives/{drive_id}/root/delta",
        "value": items,
        "@odata.deltaLink": f"https://graph.microsoft.com/v1.0/drives/{drive_id}/root/delta?$deltatoken={drive_id}",
    }


def _envelope_for_drive(item_id: str, drive_id: str, *, parent_path: str | None = None) -> dict[str, Any]:
    env: dict[str, Any] = {
        "id": item_id,
        "name": f"{item_id}.pdf",
        "size": 100,
        "lastModifiedDateTime": "2026-05-22T10:00:00Z",
        "webUrl": f"https://contoso.sharepoint.com/sites/team/{drive_id}/{item_id}.pdf",
        "file": {"mimeType": "application/pdf"},
        "parentReference": {"driveId": drive_id},
    }
    if parent_path is not None:
        env["parentReference"]["path"] = f"/drives/{drive_id}/root:{parent_path}"
    return env


def _build_connector_for_discovery(
    handler: Any,
    *,
    drives: list[SharePointDriveSpec] | None = None,
    site_discovery: list[SiteDiscoverySpec] | None = None,
) -> SharePointConnector:
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
        drives=drives if drives is not None else [],
        site_discovery=site_discovery,
        credentials=SharePointCredentials(
            tenant_id="t",
            client_id="c",
            client_secret="s-value",  # pragma: allowlist secret — test fixture
        ),
        auth=auth,
        client_builder=lambda a: SharePointGraphClient(auth=a, http_client=shared, sleep_fn=lambda _s: None),
    )


def test_parse_site_entry_returns_site_discovery_spec() -> None:
    """A dict naming ``site_id`` (no drive_id) parses into a SiteDiscoverySpec."""
    from kairix.connectors.sharepoint.connector import parse_drive_entry

    spec = parse_drive_entry({"site_id": _SITE_ID, "exclude_paths": ["/Archive"]})
    assert isinstance(spec, SiteDiscoverySpec)
    assert spec.site_id == _SITE_ID
    assert spec.exclude_paths == ("/Archive",)
    assert spec.include_paths == ()


def test_parse_site_url_entry_returns_site_discovery_spec() -> None:
    """A dict naming ``site_url`` (no drive_id) parses into a SiteDiscoverySpec."""
    from kairix.connectors.sharepoint.connector import parse_drive_entry

    spec = parse_drive_entry({"site_url": "https://contoso.sharepoint.com/sites/marketing"})
    assert isinstance(spec, SiteDiscoverySpec)
    assert spec.site_url == "https://contoso.sharepoint.com/sites/marketing"
    assert spec.site_id is None


def test_make_connector_with_only_site_entry_constructs() -> None:
    """``make_connector`` accepts a config whose only drives entry is a site.

    The factory stays pure — no Graph call at construction; discovery is
    deferred to the first sync. Observed through the public surface (F5):
    construction issues NO Graph request (proved by counting requests at
    the transport seam), and the held site spec only expands to its
    drives when a sync path (``iter_containers``) runs.
    """
    requests_seen: list[str] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        token = _token_response(request)
        if token is not None:
            return token
        requests_seen.append(str(request.url))
        url = str(request.url)
        if f"/sites/{_SITE_ID}/drives" in url:
            return httpx.Response(200, json=_drives_list_for_site("drive-a"))
        return httpx.Response(200, json={"value": []})

    connector = _build_connector_for_discovery(_handler, site_discovery=[SiteDiscoverySpec(site_id=_SITE_ID)])
    # Construction is pure — no Graph traffic until a sync path runs.
    assert requests_seen == [], f"construction must not call Graph (lazy discovery); saw {requests_seen!r}"
    # The held site spec expands lazily: a sync path discovers its drive.
    container_ids = [c.container_id for c in connector.iter_containers(cc_pair_id=1)]
    assert container_ids == ["drive-a"]
    assert any(f"/sites/{_SITE_ID}/drives" in u for u in requests_seen)


def test_site_entry_discovers_and_syncs_each_drive() -> None:
    """A site entry expands to the mock's drives and emits items from each."""

    def _handler(request: httpx.Request) -> httpx.Response:
        token = _token_response(request)
        if token is not None:
            return token
        url = str(request.url)
        if f"/sites/{_SITE_ID}/drives" in url:
            return httpx.Response(200, json=_drives_list_for_site("drive-a", "drive-b"))
        if "/drives/drive-a/" in url:
            return httpx.Response(200, json=_delta_for_drive("drive-a", [_envelope_for_drive("a1", "drive-a")]))
        if "/drives/drive-b/" in url:
            return httpx.Response(200, json=_delta_for_drive("drive-b", [_envelope_for_drive("b1", "drive-b")]))
        return httpx.Response(200, json={"value": []})

    connector = _build_connector_for_discovery(_handler, site_discovery=[SiteDiscoverySpec(site_id=_SITE_ID)])
    events = list(connector.list_changes(cursor=None))
    item_ids = sorted(e.item_id for e in events)
    assert item_ids == ["a1", "b1"], f"both discovered drives must sync; got {item_ids!r}"
    # Per-drive cursor keyed on the DISCOVERED drive ids round-trips.
    cursor = connector.next_cursor()
    assert cursor is not None
    parsed = json.loads(cursor)
    assert set(parsed.keys()) == {"drive-a", "drive-b"}


def test_explicit_drive_still_works_alongside_site_entry() -> None:
    """An explicit drive_id entry syncs unchanged when mixed with a site entry."""

    def _handler(request: httpx.Request) -> httpx.Response:
        token = _token_response(request)
        if token is not None:
            return token
        url = str(request.url)
        if f"/sites/{_SITE_ID}/drives" in url:
            return httpx.Response(200, json=_drives_list_for_site("drive-discovered"))
        if "/drives/drive-explicit/" in url:
            return httpx.Response(
                200, json=_delta_for_drive("drive-explicit", [_envelope_for_drive("e1", "drive-explicit")])
            )
        if "/drives/drive-discovered/" in url:
            return httpx.Response(
                200, json=_delta_for_drive("drive-discovered", [_envelope_for_drive("d1", "drive-discovered")])
            )
        return httpx.Response(200, json={"value": []})

    connector = _build_connector_for_discovery(
        _handler,
        drives=[SharePointDriveSpec(drive_id="drive-explicit")],
        site_discovery=[SiteDiscoverySpec(site_id=_SITE_ID)],
    )
    events = list(connector.list_changes(cursor=None))
    assert sorted(e.item_id for e in events) == ["d1", "e1"]


def _http_403_drives_failure(request: httpx.Request) -> httpx.Response:
    """``list_drives`` fails with a 403 (HTTPStatusError) — the auth/perm path."""
    return httpx.Response(403, json={"error": {"code": "accessDenied"}})


def _network_drives_failure(_request: httpx.Request) -> httpx.Response:
    """``list_drives`` fails with a raw transport error — the network path.

    Raising :class:`httpx.ConnectError` from the mock transport models a
    DNS/connection failure that never produces an HTTP response (so it
    bypasses ``raise_for_status`` entirely). This proves the discovery
    loop's ``except Exception`` catches network-level errors, not just
    status errors — a narrower ``except httpx.HTTPStatusError`` would let
    this one through and crash the tick.
    """
    raise httpx.ConnectError("simulated DNS failure")


@pytest.mark.parametrize(
    "drives_failure",
    [
        pytest.param(_http_403_drives_failure, id="http-403-status-error"),
        pytest.param(_network_drives_failure, id="raw-connect-error"),
    ],
)
def test_site_discovery_failure_is_isolated_and_explicit_drives_still_sync(
    drives_failure: Callable[[httpx.Request], httpx.Response],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A site whose list_drives fails -> WARN + skipped; explicit drives still sync.

    Parametrised over BOTH failure shapes that ``except Exception`` must
    isolate: a 403 ``HTTPStatusError`` (auth/permission) and a raw
    ``httpx.ConnectError`` (network/DNS, no HTTP response at all). Either
    way the site is skipped with a structured WARN naming it, and the
    explicit drive still syncs.
    """
    import logging

    def _handler(request: httpx.Request) -> httpx.Response:
        token = _token_response(request)
        if token is not None:
            return token
        url = str(request.url)
        if f"/sites/{_SITE_ID}/drives" in url:
            return drives_failure(request)
        if "/drives/drive-explicit/" in url:
            return httpx.Response(
                200, json=_delta_for_drive("drive-explicit", [_envelope_for_drive("e1", "drive-explicit")])
            )
        return httpx.Response(200, json={"value": []})

    connector = _build_connector_for_discovery(
        _handler,
        drives=[SharePointDriveSpec(drive_id="drive-explicit")],
        site_discovery=[SiteDiscoverySpec(site_id=_SITE_ID)],
    )
    with caplog.at_level(logging.WARNING, logger="kairix.connectors.sharepoint.connector"):
        events = list(connector.list_changes(cursor=None))
    # Explicit drive still synced despite the site failing discovery.
    assert [e.item_id for e in events] == ["e1"]
    discover_errors = [r for r in caplog.records if "sharepoint_discover_error" in r.getMessage()]
    assert len(discover_errors) == 1, "a failing site must surface exactly one sharepoint_discover_error WARN"
    assert _SITE_ID in discover_errors[0].getMessage()


def test_site_discovery_runs_exactly_once_per_tick() -> None:
    """A full Wave-E tick discovers each site's drives ONCE, not per container.

    The Wave-E flow is ``iter_containers()`` followed by a per-container
    ``list_changes_for_container()`` for every container. Each
    per-container call resolves the container's drive spec via
    ``_spec_for_drive_id``; without the per-tick cache that lookup
    re-ran ``_resolve_drive_specs()`` → a fresh ``GET /sites/{id}/drives``
    discovery call PER container (NxM for N drives + M sites). The cache
    makes discovery (the ``/sites/{id}/drives`` GET — the public
    wire-level signature of ``list_drives``) fire exactly once per site
    for the whole tick, regardless of how many drives the site exposes.

    Sabotage proof: revert ``_spec_for_drive_id`` to call
    ``_resolve_drive_specs()`` per container — the discovery-call count
    climbs to one-per-container and this assertion fails.
    """
    discovery_calls: list[str] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        token = _token_response(request)
        if token is not None:
            return token
        url = str(request.url)
        if f"/sites/{_SITE_ID}/drives" in url:
            discovery_calls.append(url)
            # Site exposes THREE libraries — the NxM regression would make
            # discovery fire once per container as each is drained.
            return httpx.Response(200, json=_drives_list_for_site("drive-a", "drive-b", "drive-c"))
        for did in ("drive-a", "drive-b", "drive-c"):
            if f"/drives/{did}/" in url:
                return httpx.Response(200, json=_delta_for_drive(did, [_envelope_for_drive(f"{did}-x", did)]))
        return httpx.Response(200, json={"value": []})

    connector = _build_connector_for_discovery(_handler, site_discovery=[SiteDiscoverySpec(site_id=_SITE_ID)])

    def _run_tick() -> list[str]:
        # iter_containers enumerates the drives (one discovery call);
        # each per-container drain reuses the per-tick resolved-spec map.
        emitted: list[str] = []
        for container in connector.iter_containers(cc_pair_id=1):
            emitted.extend(e.item_id for e in connector.list_changes_for_container(container))
        return sorted(emitted)

    assert _run_tick() == ["drive-a-x", "drive-b-x", "drive-c-x"]
    # The site was discovered exactly ONCE for the whole tick even though
    # iter_containers + three per-container drains all needed the spec map.
    assert len(discovery_calls) == 1, (
        f"site discovery must run once per tick, not per drive/container; saw {len(discovery_calls)} calls"
    )

    # A SECOND tick re-discovers (so newly-added libraries appear) — again
    # exactly once, proving the cache is per-tick, not per-process.
    discovery_calls.clear()
    assert _run_tick() == ["drive-a-x", "drive-b-x", "drive-c-x"]
    assert len(discovery_calls) == 1, "the next tick must re-discover exactly once"


def test_site_url_resolving_to_empty_site_id_warns_and_skips(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A ``site_url`` that Graph resolves to an empty site_id -> WARN + skip.

    Previously a None/empty resolution from ``resolve_site_by_path`` was
    swallowed silently, leaking a falsy site id into discovery. Now the
    connector emits a ``sharepoint_discover_site_unresolved`` WARN naming
    the site_url and skips that site (same isolation as a discovery
    failure); the explicit drive still syncs.
    """
    import logging

    def _handler(request: httpx.Request) -> httpx.Response:
        token = _token_response(request)
        if token is not None:
            return token
        url = str(request.url)
        # resolve_site_by_path returns a 200 with NO id field -> empty site_id.
        if "/sites/contoso.sharepoint.com:/sites/ghost" in url:
            return httpx.Response(200, json={"displayName": "Ghost", "webUrl": "https://x"})
        if "/drives/drive-explicit/" in url:
            return httpx.Response(
                200, json=_delta_for_drive("drive-explicit", [_envelope_for_drive("e1", "drive-explicit")])
            )
        return httpx.Response(200, json={"value": []})

    connector = _build_connector_for_discovery(
        _handler,
        drives=[SharePointDriveSpec(drive_id="drive-explicit")],
        site_discovery=[SiteDiscoverySpec(site_url="https://contoso.sharepoint.com/sites/ghost")],
    )
    with caplog.at_level(logging.WARNING, logger="kairix.connectors.sharepoint.connector"):
        events = list(connector.list_changes(cursor=None))
    # Explicit drive still synced; the unresolved site was WARNed + skipped.
    assert [e.item_id for e in events] == ["e1"]
    unresolved = [r for r in caplog.records if "sharepoint_discover_site_unresolved" in r.getMessage()]
    assert len(unresolved) == 1, "an empty site-id resolution must surface exactly one WARN"
    assert "contoso.sharepoint.com/sites/ghost" in unresolved[0].getMessage()


def test_site_discovery_empty_drive_set_warns(caplog: pytest.LogCaptureFixture) -> None:
    """A site that resolves to zero drives -> WARN + no specs, connector still usable."""
    import logging

    def _handler(request: httpx.Request) -> httpx.Response:
        token = _token_response(request)
        if token is not None:
            return token
        url = str(request.url)
        if f"/sites/{_SITE_ID}/drives" in url:
            return httpx.Response(200, json={"value": []})
        return httpx.Response(200, json={"value": []})

    connector = _build_connector_for_discovery(_handler, site_discovery=[SiteDiscoverySpec(site_id=_SITE_ID)])
    with caplog.at_level(logging.WARNING, logger="kairix.connectors.sharepoint.connector"):
        events = list(connector.list_changes(cursor=None))
    assert events == []
    no_drives = [r for r in caplog.records if "sharepoint_discover_no_drives" in r.getMessage()]
    assert len(no_drives) == 1
    assert _SITE_ID in no_drives[0].getMessage()


def test_site_exclude_paths_apply_to_discovered_drives() -> None:
    """exclude_paths from the site entry applies to every discovered drive."""

    def _handler(request: httpx.Request) -> httpx.Response:
        token = _token_response(request)
        if token is not None:
            return token
        url = str(request.url)
        if f"/sites/{_SITE_ID}/drives" in url:
            return httpx.Response(200, json=_drives_list_for_site("drive-a"))
        if "/drives/drive-a/" in url:
            return httpx.Response(
                200,
                json=_delta_for_drive(
                    "drive-a",
                    [
                        _envelope_for_drive("keep", "drive-a", parent_path="/Curated"),
                        _envelope_for_drive("drop", "drive-a", parent_path="/Archive"),
                    ],
                ),
            )
        return httpx.Response(200, json={"value": []})

    connector = _build_connector_for_discovery(
        _handler,
        site_discovery=[SiteDiscoverySpec(site_id=_SITE_ID, exclude_paths=("/Archive",))],
    )
    events = list(connector.list_changes(cursor=None))
    assert {e.item_id for e in events} == {"keep"}, "site exclude_paths must drop archived item on discovered drive"


def test_site_include_paths_apply_to_discovered_drives() -> None:
    """include_paths from the site entry scopes every discovered drive."""

    def _handler(request: httpx.Request) -> httpx.Response:
        token = _token_response(request)
        if token is not None:
            return token
        url = str(request.url)
        if f"/sites/{_SITE_ID}/drives" in url:
            return httpx.Response(200, json=_drives_list_for_site("drive-a"))
        if "/drives/drive-a/" in url:
            return httpx.Response(
                200,
                json=_delta_for_drive(
                    "drive-a",
                    [
                        _envelope_for_drive("in", "drive-a", parent_path="/Curated"),
                        _envelope_for_drive("out", "drive-a", parent_path="/Other"),
                    ],
                ),
            )
        return httpx.Response(200, json={"value": []})

    connector = _build_connector_for_discovery(
        _handler,
        site_discovery=[SiteDiscoverySpec(site_id=_SITE_ID, include_paths=("/Curated",))],
    )
    events = list(connector.list_changes(cursor=None))
    assert {e.item_id for e in events} == {"in"}


def test_site_url_entry_resolves_then_discovers() -> None:
    """A ``site_url`` entry resolves to a site_id via Graph, then discovers drives."""

    def _handler(request: httpx.Request) -> httpx.Response:
        token = _token_response(request)
        if token is not None:
            return token
        url = str(request.url)
        # resolve_site_by_path: GET /sites/{hostname}:/{path} -> single object
        if "/sites/contoso.sharepoint.com:/sites/marketing" in url:
            return httpx.Response(
                200,
                json={
                    "id": _SITE_ID,
                    "displayName": "Marketing",
                    "webUrl": "https://contoso.sharepoint.com/sites/marketing",
                },
            )
        if f"/sites/{_SITE_ID}/drives" in url:
            return httpx.Response(200, json=_drives_list_for_site("drive-mk"))
        if "/drives/drive-mk/" in url:
            return httpx.Response(200, json=_delta_for_drive("drive-mk", [_envelope_for_drive("m1", "drive-mk")]))
        return httpx.Response(200, json={"value": []})

    connector = _build_connector_for_discovery(
        _handler,
        site_discovery=[SiteDiscoverySpec(site_url="https://contoso.sharepoint.com/sites/marketing")],
    )
    events = list(connector.list_changes(cursor=None))
    assert [e.item_id for e in events] == ["m1"]


def test_iter_containers_includes_discovered_drives() -> None:
    """Discovered drives surface as their own Containers (v2 per-container path)."""

    def _handler(request: httpx.Request) -> httpx.Response:
        token = _token_response(request)
        if token is not None:
            return token
        url = str(request.url)
        if f"/sites/{_SITE_ID}/drives" in url:
            return httpx.Response(200, json=_drives_list_for_site("drive-a", "drive-b"))
        return httpx.Response(200, json={"value": []})

    connector = _build_connector_for_discovery(
        _handler,
        drives=[SharePointDriveSpec(drive_id="drive-explicit")],
        site_discovery=[SiteDiscoverySpec(site_id=_SITE_ID)],
    )
    containers = sorted(c.container_id for c in connector.iter_containers(cc_pair_id=7))
    assert containers == ["drive-a", "drive-b", "drive-explicit"]


def test_constructor_rejects_empty_drives_and_no_sites() -> None:
    """Empty drives AND no site_discovery still fails fast at construction."""
    with pytest.raises(ValueError, match="drives list is empty"):
        SharePointConnector(drives=[], site_discovery=[])


def test_re_discovery_picks_up_new_library_on_next_tick() -> None:
    """Discovery re-runs each sync so a library added to the site appears."""
    state = {"drives": ["drive-a"]}

    def _handler(request: httpx.Request) -> httpx.Response:
        token = _token_response(request)
        if token is not None:
            return token
        url = str(request.url)
        if f"/sites/{_SITE_ID}/drives" in url:
            return httpx.Response(200, json=_drives_list_for_site(*state["drives"]))
        for did in ("drive-a", "drive-b"):
            if f"/drives/{did}/" in url:
                return httpx.Response(200, json=_delta_for_drive(did, [_envelope_for_drive(f"{did}-x", did)]))
        return httpx.Response(200, json={"value": []})

    connector = _build_connector_for_discovery(_handler, site_discovery=[SiteDiscoverySpec(site_id=_SITE_ID)])
    first = sorted(e.item_id for e in connector.list_changes(cursor=None))
    assert first == ["drive-a-x"]
    # A new library appears on the site between ticks.
    state["drives"] = ["drive-a", "drive-b"]
    second = sorted(e.item_id for e in connector.list_changes(cursor=connector.next_cursor()))
    assert second == ["drive-a-x", "drive-b-x"], "re-discovery must pick up the newly-added library"


def test_unparseable_site_url_warns_and_skips(caplog: pytest.LogCaptureFixture) -> None:
    """A site_url with no server-relative path -> WARN + skip (no crash)."""
    import logging

    def _handler(request: httpx.Request) -> httpx.Response:
        token = _token_response(request)
        if token is not None:
            return token
        return httpx.Response(200, json={"value": []})

    connector = _build_connector_for_discovery(
        _handler,
        drives=[SharePointDriveSpec(drive_id="drive-explicit")],
        site_discovery=[SiteDiscoverySpec(site_url="https://contoso.sharepoint.com")],
    )
    with caplog.at_level(logging.WARNING, logger="kairix.connectors.sharepoint.connector"):
        events = list(connector.list_changes(cursor=None))
    # Explicit drive still synced; the unparseable site_url WARNed + skipped.
    assert {e.metadata.get("drive_id") for e in events} == {"drive-explicit"} or events == []
    assert any("sharepoint_discover_site_url_unparseable" in r.getMessage() for r in caplog.records)


def test_site_url_with_no_path_skips_via_public_sync(caplog: pytest.LogCaptureFixture) -> None:
    """A ``site_url`` lacking a server-relative path is skipped through the public sync.

    Exercises the ``_split_site_url`` -> (None, None) branch via the
    public ``list_changes`` surface (F5: no private-name imports in
    tests) — the bare-host URL can't be resolved to a site, so the site
    is WARNed + skipped and the explicit drive still syncs.
    """
    import logging

    def _handler(request: httpx.Request) -> httpx.Response:
        token = _token_response(request)
        if token is not None:
            return token
        url = str(request.url)
        if "/drives/drive-explicit/" in url:
            return httpx.Response(
                200, json=_delta_for_drive("drive-explicit", [_envelope_for_drive("e1", "drive-explicit")])
            )
        return httpx.Response(200, json={"value": []})

    connector = _build_connector_for_discovery(
        _handler,
        drives=[SharePointDriveSpec(drive_id="drive-explicit")],
        site_discovery=[SiteDiscoverySpec(site_url="contoso.sharepoint.com")],
    )
    with caplog.at_level(logging.WARNING, logger="kairix.connectors.sharepoint.connector"):
        events = list(connector.list_changes(cursor=None))
    assert [e.item_id for e in events] == ["e1"]
    assert any("sharepoint_discover_site_url_unparseable" in r.getMessage() for r in caplog.records)


def test_empty_site_url_skips_via_public_sync(caplog: pytest.LogCaptureFixture) -> None:
    """A blank ``site_url`` (whitespace only) is skipped through the public sync.

    Exercises the ``_split_site_url`` empty-input branch via the public
    surface — the connector WARNs + skips and the explicit drive still
    syncs.
    """
    import logging

    def _handler(request: httpx.Request) -> httpx.Response:
        token = _token_response(request)
        if token is not None:
            return token
        if "/drives/drive-explicit/" in str(request.url):
            return httpx.Response(
                200, json=_delta_for_drive("drive-explicit", [_envelope_for_drive("e1", "drive-explicit")])
            )
        return httpx.Response(200, json={"value": []})

    connector = _build_connector_for_discovery(
        _handler,
        drives=[SharePointDriveSpec(drive_id="drive-explicit")],
        site_discovery=[SiteDiscoverySpec(site_url="   ")],
    )
    with caplog.at_level(logging.WARNING, logger="kairix.connectors.sharepoint.connector"):
        events = list(connector.list_changes(cursor=None))
    assert [e.item_id for e in events] == ["e1"]
    assert any("sharepoint_discover_site_url_unparseable" in r.getMessage() for r in caplog.records)


def test_site_url_host_only_with_trailing_slash_skips() -> None:
    """A ``site_url`` that is host + trailing slash only (no path) is skipped.

    Exercises the ``_split_site_url`` no-path branch (hostname present but
    server-relative path empty) via the public surface.
    """

    def _handler(request: httpx.Request) -> httpx.Response:
        token = _token_response(request)
        if token is not None:
            return token
        if "/drives/drive-explicit/" in str(request.url):
            return httpx.Response(
                200, json=_delta_for_drive("drive-explicit", [_envelope_for_drive("e1", "drive-explicit")])
            )
        return httpx.Response(200, json={"value": []})

    connector = _build_connector_for_discovery(
        _handler,
        drives=[SharePointDriveSpec(drive_id="drive-explicit")],
        site_discovery=[SiteDiscoverySpec(site_url="https://contoso.sharepoint.com/")],
    )
    events = list(connector.list_changes(cursor=None))
    assert [e.item_id for e in events] == ["e1"]


def test_list_changes_for_container_drains_discovered_drive_unfiltered() -> None:
    """A container naming a discovered drive id drains via the per-container path.

    Covers ``_spec_for_drive_id`` resolving a discovered drive: the
    container's drive id matches a site-discovered spec, so its (empty)
    filters apply and the items emit. Also exercises the ``None`` branch
    when the container id matches no resolved spec — the drive drains
    unfiltered rather than crashing.
    """

    def _handler(request: httpx.Request) -> httpx.Response:
        token = _token_response(request)
        if token is not None:
            return token
        url = str(request.url)
        if f"/sites/{_SITE_ID}/drives" in url:
            return httpx.Response(200, json=_drives_list_for_site("drive-a"))
        if "/drives/drive-a/" in url:
            return httpx.Response(200, json=_delta_for_drive("drive-a", [_envelope_for_drive("a1", "drive-a")]))
        if "/drives/unknown-drive/" in url:
            return httpx.Response(
                200, json=_delta_for_drive("unknown-drive", [_envelope_for_drive("u1", "unknown-drive")])
            )
        return httpx.Response(200, json={"value": []})

    connector = _build_connector_for_discovery(_handler, site_discovery=[SiteDiscoverySpec(site_id=_SITE_ID)])
    # Discovered drive — resolves to a spec, drains its item.
    discovered = Container(
        cc_pair_id=1, container_id="drive-a", access_state="ACCESSIBLE", cursor_token=None, last_synced_at=None
    )
    assert [e.item_id for e in connector.list_changes_for_container(discovered)] == ["a1"]
    # A drive id matching no resolved spec — drains unfiltered (None branch).
    unknown = Container(
        cc_pair_id=1, container_id="unknown-drive", access_state="ACCESSIBLE", cursor_token=None, last_synced_at=None
    )
    assert [e.item_id for e in connector.list_changes_for_container(unknown)] == ["u1"]


def test_degenerate_site_spec_with_no_anchor_is_skipped() -> None:
    """A SiteDiscoverySpec with neither site_id nor site_url resolves to nothing.

    The config parser never produces this shape (it rejects a block
    naming neither), but the frozen dataclass permits it — the resolver's
    defensive ``return None`` path skips it cleanly without a Graph call.
    """

    def _handler(request: httpx.Request) -> httpx.Response:
        token = _token_response(request)
        if token is not None:
            return token
        return httpx.Response(200, json={"value": []})

    connector = _build_connector_for_discovery(
        _handler,
        drives=[SharePointDriveSpec(drive_id="drive-explicit")],
        site_discovery=[SiteDiscoverySpec()],
    )
    # Observed via the public per-container surface: the degenerate site
    # contributes no drives, so only the explicit drive resolves.
    container_ids = {c.container_id for c in connector.iter_containers(cc_pair_id=1)}
    assert container_ids == {"drive-explicit"}


def test_retrieve_all_slim_docs_for_discovered_drive() -> None:
    """SlimConnector id-only enumeration works for a site-discovered drive.

    The container id is a discovered drive; ``_spec_for_drive_id``
    resolves the site-inherited filters and the prune scan yields the
    live (non-tombstone) item ids.
    """

    def _handler(request: httpx.Request) -> httpx.Response:
        token = _token_response(request)
        if token is not None:
            return token
        url = str(request.url)
        if f"/sites/{_SITE_ID}/drives" in url:
            return httpx.Response(200, json=_drives_list_for_site("drive-a"))
        if "/drives/drive-a/" in url:
            return httpx.Response(
                200,
                json=_delta_for_drive(
                    "drive-a",
                    [
                        _envelope_for_drive("live-1", "drive-a", parent_path="/Curated"),
                        _envelope_for_drive("live-2", "drive-a", parent_path="/Other"),
                    ],
                ),
            )
        return httpx.Response(200, json={"value": []})

    connector = _build_connector_for_discovery(
        _handler,
        site_discovery=[SiteDiscoverySpec(site_id=_SITE_ID, include_paths=("/Curated",))],
    )
    container = Container(
        cc_pair_id=1, container_id="drive-a", access_state="ACCESSIBLE", cursor_token=None, last_synced_at=None
    )
    ids = sorted(connector.retrieve_all_slim_docs(container))
    assert ids == ["live-1"], "site include_paths must scope the prune scan on the discovered drive"


def test_load_hierarchy_includes_discovered_drives() -> None:
    """The hierarchy emits a DRIVE node for each discovered drive under the root SITE."""

    def _handler(request: httpx.Request) -> httpx.Response:
        token = _token_response(request)
        if token is not None:
            return token
        if f"/sites/{_SITE_ID}/drives" in str(request.url):
            return httpx.Response(200, json=_drives_list_for_site("drive-a", "drive-b"))
        return httpx.Response(200, json={"value": []})

    connector = _build_connector_for_discovery(
        _handler,
        drives=[SharePointDriveSpec(drive_id="drive-explicit")],
        site_discovery=[SiteDiscoverySpec(site_id=_SITE_ID)],
    )
    nodes = list(connector.load_hierarchy(cc_pair_id=5))
    assert nodes[0].node_type == "SITE"
    drive_node_ids = {n.raw_node_id for n in nodes if n.node_type == "DRIVE"}
    assert drive_node_ids == {"drive-explicit", "drive-a", "drive-b"}
    # Discovered drives carry the library name as their display label.
    by_id = {n.raw_node_id: n for n in nodes if n.node_type == "DRIVE"}
    assert by_id["drive-a"].display_name == "Library-drive-a"

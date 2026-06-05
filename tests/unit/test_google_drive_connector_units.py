"""Unit tests for kairix.connectors.google_drive — coverage paydown.

Exercises the connector + client surfaces the integration and contract
suites don't reach: config parsing, error paths, source_link fallback,
retry-after parsing, response-shape edge cases, dispatch wiring.

Each test carries the ``@pytest.mark.unit`` marker per F8 + the
``@pytest.mark.contract`` marker where the assertion is a single-layer
boundary proof.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timezone

import httpx
import pytest

from kairix.connectors.google_drive import (
    GoogleDriveClient,
    GoogleDriveConnector,
    GoogleDriveCorpusSpec,
    GoogleDriveCredentials,
    make_connector,
    version,
)
from kairix.connectors.google_drive.client import (
    DriveFileRef,
)
from kairix.core.protocols import (
    Container,
    CredentialExpiredError,
    HierarchyConnector,
    PollConnector,
    Resolver,
    SlimConnector,
    SourceConnector,
    SourceMetadata,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Module-level surface
# ---------------------------------------------------------------------------


def test_version_string_present() -> None:
    """``version`` is a non-empty string per F40 plugin-version convention."""
    assert isinstance(version, str)
    assert version, "version must be a non-empty string"


# ---------------------------------------------------------------------------
# Config parsing
# ---------------------------------------------------------------------------


def _ensure_token_env() -> None:
    """Seed the access-token env var so make_connector's secret resolver
    succeeds in unit-test context (where no real KV / .env file is
    present).

    F4 rationale: ``CONNECTOR_GOOGLE_DRIVE_ACCESS_TOKEN`` is the
    canonical env-var form declared in :mod:`kairix.secrets` — touching
    it here is using the documented secrets-layer boundary, not
    reaching into kairix internals via a KAIRIX_ env var (F2's
    target).
    """
    import os

    os.environ.setdefault(
        "CONNECTOR_GOOGLE_DRIVE_ACCESS_TOKEN",
        "unit-test-token",  # pragma: allowlist secret — test fixture
    )


def test_make_connector_accepts_string_corpus_list() -> None:
    """A list of corpus-id strings parses cleanly."""
    _ensure_token_env()
    connector = make_connector({"corpora": ["corpus-1", "corpus-2"]})
    assert isinstance(connector, GoogleDriveConnector)
    assert isinstance(connector, SourceConnector)


def test_make_connector_accepts_dict_corpus_blocks() -> None:
    """A list of dict corpus blocks parses cleanly with display_name."""
    _ensure_token_env()
    connector = make_connector({"corpora": [{"corpus_id": "corpus-1", "display_name": "Workspace Alpha"}]})
    assert isinstance(connector, GoogleDriveConnector)


def test_make_connector_rejects_empty_corpora() -> None:
    """Empty corpora list raises with an actionable fix pointer."""
    with pytest.raises(ValueError, match=r"corpora.*non-empty"):
        make_connector({"corpora": []})


def test_make_connector_rejects_missing_corpus_id_in_block() -> None:
    """A dict block missing corpus_id raises with a fix pointer."""
    with pytest.raises(ValueError, match="corpus_id"):
        make_connector({"corpora": [{"display_name": "no id"}]})


def test_make_connector_rejects_invalid_entry_type() -> None:
    """A non-string non-dict entry raises with a fix pointer."""
    with pytest.raises(ValueError, match="not a string or dict"):
        make_connector({"corpora": [123]})


def test_make_connector_rejects_invalid_sensitivity() -> None:
    """An unknown sensitivity tier raises with a fix pointer."""
    with pytest.raises(ValueError, match="default_sensitivity"):
        make_connector({"corpora": ["c-1"], "default_sensitivity": "top-secret"})


def test_make_connector_rejects_non_list_corpora() -> None:
    """A non-list ``corpora`` config value raises with a fix pointer."""
    with pytest.raises(ValueError, match="non-empty list"):
        make_connector({"corpora": "just a string"})


def test_make_connector_rejects_empty_string_corpus_entry() -> None:
    """An empty-string corpus entry parses as a non-string and raises."""
    with pytest.raises(ValueError, match="not a string or dict"):
        make_connector({"corpora": [""]})


def test_make_connector_rejects_non_string_corpus_id_in_dict() -> None:
    """A dict block whose corpus_id is not a string raises."""
    with pytest.raises(ValueError, match="corpus_id"):
        make_connector({"corpora": [{"corpus_id": 123}]})


def test_connector_constructor_rejects_empty_corpora() -> None:
    """Bare constructor also rejects empty corpora."""
    with pytest.raises(ValueError, match="corpora list is empty"):
        GoogleDriveConnector(
            corpora=[],
            credentials=GoogleDriveCredentials(access_token="t"),  # pragma: allowlist secret — test fixture
            client_builder=lambda _c: _FakeDriveClient(),
        )


# ---------------------------------------------------------------------------
# Connector behaviour — using a fake DriveClient stand-in
# ---------------------------------------------------------------------------


class _FakeDriveClient:
    """Minimal stand-in for GoogleDriveClient in unit tests."""

    def __init__(
        self,
        *,
        changes: list[DriveFileRef] | None = None,
        new_start_page_token: str | None = "fresh-token",
        raise_on_get_start: Exception | None = None,
        fetch_content: tuple[bytes, str] = (b"x", "application/octet-stream"),
    ) -> None:
        self._changes = list(changes) if changes is not None else []
        self._new_start_page_token = new_start_page_token
        self._raise_on_get_start = raise_on_get_start
        self._fetch_content = fetch_content
        self.iter_calls: list[str] = []

    def get_start_page_token(self) -> str:
        if self._raise_on_get_start is not None:
            raise self._raise_on_get_start
        return "scripted-start-token"

    def iter_changes(self, start_token: str) -> Iterator[DriveFileRef]:
        self.iter_calls.append(start_token)
        yield from self._changes

    def fetch_file_content(self, file_id: str) -> tuple[bytes, str]:
        return self._fetch_content

    def last_new_start_page_token(self) -> str | None:
        return self._new_start_page_token


def _build_drive_file(
    file_id: str = "f-1",
    *,
    removed: bool = False,
    mime_type: str | None = "application/pdf",
    web_view_link: str | None = "https://drive.google.com/file/d/f-1/view",
    modified_time: str | None = "2026-05-22T10:00:00Z",
    last_email: str | None = "agent-alpha@example.com",
    last_name: str | None = "agent-alpha",
) -> DriveFileRef:
    return DriveFileRef(
        file_id=file_id,
        name=f"{file_id}.pdf",
        mime_type=mime_type,
        web_view_link=web_view_link,
        modified_time=modified_time,
        created_time="2026-05-20T10:00:00Z",
        last_modifying_user_email=last_email,
        last_modifying_user_name=last_name,
        owner_emails=("agent-beta@example.com",),
        removed=removed,
        parents=("parent-1",),
        size=42,
    )


def _build_connector(
    *,
    fake_client: _FakeDriveClient,
    corpora: tuple[str, ...] = ("workspace-unit",),
    flag_reader=lambda _name: False,
) -> GoogleDriveConnector:
    return GoogleDriveConnector(
        corpora=[GoogleDriveCorpusSpec(corpus_id=c) for c in corpora],
        credentials=GoogleDriveCredentials(access_token="unit-token"),  # pragma: allowlist secret — test fixture
        client_builder=lambda _c: fake_client,
        flag_reader=flag_reader,
    )


def test_list_changes_emits_deleted_op_for_removed_entry() -> None:
    """Removed entries surface as ``deleted`` ChangeEvent ops."""
    client = _FakeDriveClient(changes=[_build_drive_file(removed=True)])
    connector = _build_connector(fake_client=client)
    events = list(connector.list_changes(cursor=None))
    assert len(events) == 1
    assert events[0].op == "deleted"


def test_list_changes_skips_empty_file_ids() -> None:
    """Entries with an empty file_id are dropped silently."""
    empty = _build_drive_file(file_id="").__class__(**{**_build_drive_file().__dict__, "file_id": ""})
    client = _FakeDriveClient(changes=[empty, _build_drive_file()])
    connector = _build_connector(fake_client=client)
    events = list(connector.list_changes(cursor=None))
    assert len(events) == 1


def test_list_changes_persists_returned_cursor() -> None:
    """``next_cursor()`` returns the new-start-page-token from the client."""
    client = _FakeDriveClient(changes=[_build_drive_file()], new_start_page_token="advanced")
    connector = _build_connector(fake_client=client)
    list(connector.list_changes(cursor=None))
    assert connector.next_cursor() == "advanced"


def test_list_changes_with_cursor_string_uses_it_directly() -> None:
    """A non-empty cursor string is passed straight to ``iter_changes``."""
    client = _FakeDriveClient(changes=[_build_drive_file()])
    connector = _build_connector(fake_client=client)
    list(connector.list_changes(cursor="resume-token"))
    assert client.iter_calls == ["resume-token"]


def test_list_changes_falls_back_to_seed_when_cursor_missing() -> None:
    """A None / empty cursor triggers a get_start_page_token call."""
    client = _FakeDriveClient(changes=[_build_drive_file()])
    connector = _build_connector(fake_client=client)
    list(connector.list_changes(cursor=None))
    assert client.iter_calls == ["scripted-start-token"]


def test_fetch_raises_keyerror_when_item_not_in_cache() -> None:
    """A fetch for an unknown id raises with an actionable fix pointer."""
    client = _FakeDriveClient()
    connector = _build_connector(fake_client=client)
    with pytest.raises(KeyError, match="not in the per-tick envelope cache"):
        connector.fetch("never-seen")


def test_fetch_returns_artefact_with_envelope_mime() -> None:
    """fetch surfaces the envelope mime when present (preferred over content-type)."""
    client = _FakeDriveClient(
        changes=[_build_drive_file()],
        fetch_content=(b"raw", "application/x-fallback"),
    )
    connector = _build_connector(fake_client=client)
    list(connector.list_changes(cursor=None))
    artefact = connector.fetch("f-1")
    assert artefact.mime == "application/pdf"
    assert artefact.raw == b"raw"


def test_fetch_falls_back_to_content_type_when_envelope_mime_absent() -> None:
    """When the envelope mime is None, fetch uses the response content-type."""
    client = _FakeDriveClient(
        changes=[_build_drive_file(mime_type=None)],
        fetch_content=(b"raw", "application/x-fallback"),
    )
    connector = _build_connector(fake_client=client)
    list(connector.list_changes(cursor=None))
    artefact = connector.fetch("f-1")
    assert artefact.mime == "application/x-fallback"


def test_source_link_falls_back_when_envelope_has_no_web_view_link() -> None:
    """Source link falls back to gdrive://files/<id> when envelope has no URL."""
    client = _FakeDriveClient(changes=[_build_drive_file(web_view_link=None)])
    connector = _build_connector(fake_client=client)
    list(connector.list_changes(cursor=None))
    link = connector.source_link("f-1")
    assert link == "gdrive://files/f-1"


def test_source_link_falls_back_for_unknown_id() -> None:
    """Source link for an uncached id returns the gdrive://files/<id> fallback."""
    client = _FakeDriveClient()
    connector = _build_connector(fake_client=client)
    assert connector.source_link("ghost-id") == "gdrive://files/ghost-id"


def test_metadata_for_returns_empty_for_unknown_id() -> None:
    """metadata_for an uncached id returns an empty SourceMetadata."""
    client = _FakeDriveClient()
    connector = _build_connector(fake_client=client)
    md = connector.metadata_for("never-seen")
    assert md == SourceMetadata()


def test_metadata_for_falls_back_to_email_when_display_name_missing() -> None:
    """When ``last_modifying_user_name`` is None, author falls back to email."""
    client = _FakeDriveClient(changes=[_build_drive_file(last_name=None, last_email="agent-charlie@example.com")])
    connector = _build_connector(fake_client=client)
    list(connector.list_changes(cursor=None))
    md = connector.metadata_for("f-1")
    assert md.author == "agent-charlie@example.com"
    assert md.author_email == "agent-charlie@example.com"


def test_metadata_for_carries_properties_and_tags() -> None:
    """metadata_for surfaces name + mime + web_view_link as properties; owners as tags."""
    client = _FakeDriveClient(changes=[_build_drive_file()])
    connector = _build_connector(fake_client=client)
    list(connector.list_changes(cursor=None))
    md = connector.metadata_for("f-1")
    assert md.properties.get("mime_type") == "application/pdf"
    assert md.properties.get("name") == "f-1.pdf"
    assert "agent-beta@example.com" in md.tags


def test_iter_containers_emits_one_per_corpus() -> None:
    """iter_containers yields one Container per configured corpus."""
    client = _FakeDriveClient()
    connector = _build_connector(fake_client=client, corpora=("alpha", "beta"))
    containers = list(connector.iter_containers(cc_pair_id=11))
    assert [c.container_id for c in containers] == ["alpha", "beta"]
    for c in containers:
        assert c.cc_pair_id == 11
        assert c.access_state == "ACCESSIBLE"


# NOTE: test_load_hierarchy_flag_off_emits_root_only retired with the
# topology_v2_google_drive flag (#132). Post-cutover load_hierarchy always
# emits root + corpus children; the OFF "root only" branch no longer exists.


def test_load_hierarchy_flag_on_emits_root_plus_corpus_children() -> None:
    """ON branch: load_hierarchy emits root + one FOLDER child per corpus."""
    client = _FakeDriveClient()
    connector = _build_connector(
        fake_client=client,
        corpora=("alpha", "beta"),
        flag_reader=lambda _n: True,
    )
    nodes = list(connector.load_hierarchy(cc_pair_id=11))
    assert len(nodes) == 3
    assert nodes[0].raw_parent_id is None
    assert {n.raw_node_id for n in nodes[1:]} == {"alpha", "beta"}


# NOTE: test_list_changes_for_container_flag_off_delegates_to_legacy retired
# with the topology_v2_google_drive flag (#132). Post-cutover
# list_changes_for_container always uses the per-container cursor; the OFF
# delegate-to-list_changes branch no longer exists.


def test_list_changes_for_container_flag_on_scopes_per_container() -> None:
    """ON: list_changes_for_container reads the container's own cursor_token."""
    client = _FakeDriveClient(changes=[_build_drive_file()])
    connector = _build_connector(fake_client=client, flag_reader=lambda _n: True)
    container = Container(
        cc_pair_id=11,
        container_id="workspace-unit",
        access_state="ACCESSIBLE",
        cursor_token="per-container-token",
        last_synced_at=None,
    )
    list(connector.list_changes_for_container(container))
    assert "per-container-token" in client.iter_calls


def test_retrieve_all_slim_docs_filters_tombstones_and_empty_ids() -> None:
    """SlimConnector emits only non-tombstone, non-empty ids."""
    client = _FakeDriveClient(
        changes=[
            _build_drive_file(file_id="f-1"),
            _build_drive_file(file_id="f-2", removed=True),
        ]
    )
    connector = _build_connector(fake_client=client)
    container = Container(
        cc_pair_id=11,
        container_id="workspace-unit",
        access_state="ACCESSIBLE",
        cursor_token=None,
        last_synced_at=None,
    )
    ids = list(connector.retrieve_all_slim_docs(container))
    assert ids == ["f-1"]


def test_reindex_filters_duplicates_and_empty_ids() -> None:
    """reindex preserves order, removes duplicates and empty strings."""
    client = _FakeDriveClient()
    connector = _build_connector(fake_client=client)
    events = list(connector.reindex(("a", "", "a", "b")))
    assert [e.item_id for e in events] == ["a", "b"]


def test_reindex_carries_include_permissions_flag_through_metadata() -> None:
    """include_permissions=True surfaces on every emitted event."""
    client = _FakeDriveClient()
    connector = _build_connector(fake_client=client)
    events = list(connector.reindex(("a",), include_permissions=True))
    assert events[0].metadata.get("include_permissions") is True


def test_connector_satisfies_capability_protocols() -> None:
    """Connector satisfies the Wave-E capability Protocols at runtime."""
    client = _FakeDriveClient()
    connector = _build_connector(fake_client=client)
    assert isinstance(connector, PollConnector)
    assert isinstance(connector, HierarchyConnector)
    assert isinstance(connector, SlimConnector)
    assert isinstance(connector, Resolver)


# ---------------------------------------------------------------------------
# Client surface — driven through the public methods, not private parsers
# ---------------------------------------------------------------------------


def test_client_retry_after_missing_header_falls_back_to_backoff() -> None:
    """A 429 without ``Retry-After`` triggers exponential backoff fallback.

    Drives the wait_strategy missing-header branch via the public
    iter_changes path so the test stays F5-clean (no direct
    _parse_retry_after import).
    """
    state = {"n": 0}
    recorded: list[float] = []

    def _handler(_request: httpx.Request) -> httpx.Response:
        state["n"] += 1
        if state["n"] == 1:
            # 429 with no Retry-After header → falls back to exponential.
            return httpx.Response(429, json={"err": "throttled"})
        return httpx.Response(200, json={"changes": [], "newStartPageToken": "fresh"})

    shared = httpx.Client(transport=httpx.MockTransport(_handler))
    client = GoogleDriveClient(
        access_token="t",  # pragma: allowlist secret — test fixture
        http_client=shared,
        sleep_fn=recorded.append,
    )
    list(client.iter_changes("seed"))
    assert state["n"] == 2
    # Exponential floor is _DEFAULT_BACKOFF_MIN_S = 2.0
    assert recorded[0] >= 2.0


def test_client_retry_after_malformed_header_falls_back_to_backoff() -> None:
    """A 429 with an unparseable ``Retry-After`` value falls back to backoff."""
    state = {"n": 0}
    recorded: list[float] = []

    def _handler(_request: httpx.Request) -> httpx.Response:
        state["n"] += 1
        if state["n"] == 1:
            return httpx.Response(429, headers={"Retry-After": "not-a-number"}, json={"err": "throttled"})
        return httpx.Response(200, json={"changes": [], "newStartPageToken": "fresh"})

    shared = httpx.Client(transport=httpx.MockTransport(_handler))
    client = GoogleDriveClient(
        access_token="t",  # pragma: allowlist secret — test fixture
        http_client=shared,
        sleep_fn=recorded.append,
    )
    list(client.iter_changes("seed"))
    assert state["n"] == 2
    assert recorded[0] >= 2.0


def test_client_503_plain_body_treated_as_throttle_and_retries() -> None:
    """503 with no Retry-After retries via the throttled-status code path."""
    state = {"n": 0}
    recorded: list[float] = []

    def _handler(_request: httpx.Request) -> httpx.Response:
        state["n"] += 1
        if state["n"] == 1:
            return httpx.Response(503, json={"err": "unavailable"})
        return httpx.Response(200, json={"changes": [], "newStartPageToken": "fresh"})

    shared = httpx.Client(transport=httpx.MockTransport(_handler))
    client = GoogleDriveClient(
        access_token="t",  # pragma: allowlist secret — test fixture
        http_client=shared,
        sleep_fn=recorded.append,
    )
    list(client.iter_changes("seed"))
    assert state["n"] == 2


def test_client_403_with_unknown_reason_does_not_retry() -> None:
    """A 403 with a reason outside the documented rate-limit set is permanent."""
    state = {"n": 0}

    def _handler(_request: httpx.Request) -> httpx.Response:
        state["n"] += 1
        return httpx.Response(403, json={"error": {"errors": [{"reason": "something-else"}]}})

    shared = httpx.Client(transport=httpx.MockTransport(_handler))
    client = GoogleDriveClient(
        access_token="t",  # pragma: allowlist secret — test fixture
        http_client=shared,
        sleep_fn=lambda _s: None,
    )
    with pytest.raises(httpx.HTTPStatusError):
        list(client.iter_changes("seed"))
    assert state["n"] == 1


def test_client_403_with_non_json_body_does_not_retry() -> None:
    """A 403 whose body isn't JSON returns False from the quota classifier."""
    state = {"n": 0}

    def _handler(_request: httpx.Request) -> httpx.Response:
        state["n"] += 1
        return httpx.Response(403, text="not json at all")

    shared = httpx.Client(transport=httpx.MockTransport(_handler))
    client = GoogleDriveClient(
        access_token="t",  # pragma: allowlist secret — test fixture
        http_client=shared,
        sleep_fn=lambda _s: None,
    )
    with pytest.raises(httpx.HTTPStatusError):
        list(client.iter_changes("seed"))
    assert state["n"] == 1


def test_client_changes_page_with_tombstone_only_entry() -> None:
    """A thin tombstone (only ``fileId`` + ``removed=True``) surfaces via fetch_changes_page."""

    def _handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "changes": [
                    {"fileId": "f-tomb", "removed": True},
                ],
                "newStartPageToken": "next",
            },
        )

    shared = httpx.Client(transport=httpx.MockTransport(_handler))
    client = GoogleDriveClient(
        access_token="t",  # pragma: allowlist secret — test fixture
        http_client=shared,
        sleep_fn=lambda _s: None,
    )
    page = client.fetch_changes_page("seed")
    assert len(page.files) == 1
    assert page.files[0].file_id == "f-tomb"
    assert page.files[0].removed is True


def test_client_changes_page_drops_entries_without_file_block() -> None:
    """A non-removed entry without a ``file`` block is dropped."""

    def _handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"changes": [{"fileId": "f-no-block"}]})

    shared = httpx.Client(transport=httpx.MockTransport(_handler))
    client = GoogleDriveClient(
        access_token="t",  # pragma: allowlist secret — test fixture
        http_client=shared,
        sleep_fn=lambda _s: None,
    )
    page = client.fetch_changes_page("seed")
    assert page.files == ()


def test_client_changes_page_parses_string_size_as_int() -> None:
    """A ``size`` numeric string parses to int via fetch_changes_page."""

    def _handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "changes": [
                    {
                        "fileId": "f-1",
                        "file": {"id": "f-1", "name": "x", "size": "12345"},
                    }
                ]
            },
        )

    shared = httpx.Client(transport=httpx.MockTransport(_handler))
    client = GoogleDriveClient(
        access_token="t",  # pragma: allowlist secret — test fixture
        http_client=shared,
        sleep_fn=lambda _s: None,
    )
    page = client.fetch_changes_page("seed")
    assert page.files[0].size == 12345


def test_client_changes_page_handles_invalid_size_string() -> None:
    """A non-numeric ``size`` string surfaces as None via fetch_changes_page."""

    def _handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"changes": [{"fileId": "f-1", "file": {"id": "f-1", "name": "x", "size": "abc"}}]},
        )

    shared = httpx.Client(transport=httpx.MockTransport(_handler))
    client = GoogleDriveClient(
        access_token="t",  # pragma: allowlist secret — test fixture
        http_client=shared,
        sleep_fn=lambda _s: None,
    )
    page = client.fetch_changes_page("seed")
    assert page.files[0].size is None


def test_client_changes_page_handles_missing_last_modifying_user() -> None:
    """A file envelope without lastModifyingUser surfaces None author fields."""

    def _handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"changes": [{"fileId": "f-1", "file": {"id": "f-1", "name": "x"}}]},
        )

    shared = httpx.Client(transport=httpx.MockTransport(_handler))
    client = GoogleDriveClient(
        access_token="t",  # pragma: allowlist secret — test fixture
        http_client=shared,
        sleep_fn=lambda _s: None,
    )
    page = client.fetch_changes_page("seed")
    assert page.files[0].last_modifying_user_email is None
    assert page.files[0].last_modifying_user_name is None


def test_client_get_start_page_token_raises_on_malformed_response() -> None:
    """When the API returns a malformed startPageToken, a typed error fires."""

    def _handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"unexpected": "shape"})

    shared = httpx.Client(transport=httpx.MockTransport(_handler))
    client = GoogleDriveClient(
        access_token="t",  # pragma: allowlist secret — test fixture
        http_client=shared,
        sleep_fn=lambda _s: None,
    )
    with pytest.raises(RuntimeError, match="startPageToken"):
        client.get_start_page_token()


def test_client_fetch_file_metadata_returns_typed_ref() -> None:
    """fetch_file_metadata returns a typed DriveFileRef for the requested id."""

    def _handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": "f-meta",
                "name": "meta.pdf",
                "mimeType": "application/pdf",
                "modifiedTime": "2026-05-22T10:00:00Z",
                "createdTime": "2026-05-20T10:00:00Z",
                "webViewLink": "https://drive.google.com/file/d/f-meta/view",
                "lastModifyingUser": {"emailAddress": "agent-delta@example.com", "displayName": "agent-delta"},
                "owners": [{"emailAddress": "agent-delta@example.com"}],
                "parents": ["parent-1"],
                "size": "42",
            },
        )

    shared = httpx.Client(transport=httpx.MockTransport(_handler))
    client = GoogleDriveClient(
        access_token="t",  # pragma: allowlist secret — test fixture
        http_client=shared,
        sleep_fn=lambda _s: None,
    )
    ref = client.fetch_file_metadata("f-meta")
    assert ref.file_id == "f-meta"
    assert ref.last_modifying_user_email == "agent-delta@example.com"


def test_client_last_new_start_page_token_returns_none_before_iter() -> None:
    """Before any iter_changes call, last_new_start_page_token is None."""
    shared = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200)))
    client = GoogleDriveClient(
        access_token="t",  # pragma: allowlist secret — test fixture
        http_client=shared,
        sleep_fn=lambda _s: None,
    )
    assert client.last_new_start_page_token() is None


def test_client_fetch_changes_page_returns_typed_page() -> None:
    """fetch_changes_page returns a typed ChangesPage with parsed rows."""

    def _handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "newStartPageToken": "next-tok",
                "changes": [
                    {
                        "fileId": "f-a",
                        "file": {
                            "id": "f-a",
                            "name": "alpha.pdf",
                            "mimeType": "application/pdf",
                        },
                    }
                ],
            },
        )

    shared = httpx.Client(transport=httpx.MockTransport(_handler))
    client = GoogleDriveClient(
        access_token="t",  # pragma: allowlist secret — test fixture
        http_client=shared,
        sleep_fn=lambda _s: None,
    )
    page = client.fetch_changes_page("page-1")
    assert page.new_start_page_token == "next-tok"
    assert len(page.files) == 1
    assert page.files[0].file_id == "f-a"


def test_client_iter_changes_walks_multiple_pages() -> None:
    """iter_changes walks nextPageToken across pages and persists the new start token."""
    pages = {
        "p1": {"changes": [{"fileId": "f1", "file": {"id": "f1", "name": "x"}}], "nextPageToken": "p2"},
        "p2": {"changes": [{"fileId": "f2", "file": {"id": "f2", "name": "y"}}], "newStartPageToken": "final"},
    }

    def _handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        token = "p1" if "pageToken=p1" in url else "p2"
        return httpx.Response(200, json=pages[token])

    shared = httpx.Client(transport=httpx.MockTransport(_handler))
    client = GoogleDriveClient(
        access_token="t",  # pragma: allowlist secret — test fixture
        http_client=shared,
        sleep_fn=lambda _s: None,
    )
    rows = list(client.iter_changes("p1"))
    assert [r.file_id for r in rows] == ["f1", "f2"]
    assert client.last_new_start_page_token() == "final"


def test_client_fetch_file_content_returns_bytes_and_content_type() -> None:
    """fetch_file_content returns (bytes, mime) parsed from Content-Type."""

    def _handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=b"binary-bytes",
            headers={"Content-Type": "application/pdf; charset=utf-8"},
        )

    shared = httpx.Client(transport=httpx.MockTransport(_handler))
    client = GoogleDriveClient(
        access_token="t",  # pragma: allowlist secret — test fixture
        http_client=shared,
        sleep_fn=lambda _s: None,
    )
    raw, mime = client.fetch_file_content("file-id-1")
    assert raw == b"binary-bytes"
    # ``application/pdf`` part stripped of charset
    assert mime == "application/pdf"


def test_client_401_raises_credential_expired_error() -> None:
    """401 from the Drive API surfaces as :class:`CredentialExpiredError`."""

    def _handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "unauthorized"})

    shared = httpx.Client(transport=httpx.MockTransport(_handler))
    client = GoogleDriveClient(
        access_token="t",  # pragma: allowlist secret — test fixture
        http_client=shared,
        sleep_fn=lambda _s: None,
    )
    with pytest.raises(CredentialExpiredError):
        list(client.iter_changes("seed"))


def test_client_429_with_retry_after_retries_after_honouring_header() -> None:
    """A single 429 with ``Retry-After: 2`` retries after sleeping ~2s, then 200."""
    state = {"n": 0}
    recorded: list[float] = []

    def _handler(_request: httpx.Request) -> httpx.Response:
        state["n"] += 1
        if state["n"] == 1:
            return httpx.Response(429, headers={"Retry-After": "2"}, json={"err": "throttled"})
        return httpx.Response(200, json={"changes": [], "newStartPageToken": "fresh"})

    shared = httpx.Client(transport=httpx.MockTransport(_handler))
    client = GoogleDriveClient(
        access_token="t",  # pragma: allowlist secret — test fixture
        http_client=shared,
        sleep_fn=recorded.append,
    )
    list(client.iter_changes("seed"))
    assert state["n"] == 2
    assert recorded == [pytest.approx(2.0, abs=0.01)]


def test_client_403_with_user_rate_limit_exceeded_retries() -> None:
    """403 carrying ``userRateLimitExceeded`` retries via the quota classifier."""
    state = {"n": 0}
    recorded: list[float] = []

    def _handler(_request: httpx.Request) -> httpx.Response:
        state["n"] += 1
        if state["n"] == 1:
            return httpx.Response(
                403,
                json={"error": {"errors": [{"reason": "userRateLimitExceeded"}]}},
            )
        return httpx.Response(200, json={"changes": [], "newStartPageToken": "fresh"})

    shared = httpx.Client(transport=httpx.MockTransport(_handler))
    client = GoogleDriveClient(
        access_token="t",  # pragma: allowlist secret — test fixture
        http_client=shared,
        sleep_fn=recorded.append,
    )
    list(client.iter_changes("seed"))
    assert state["n"] == 2
    assert len(recorded) == 1


def test_client_500_without_retry_after_uses_exponential_backoff() -> None:
    """5xx without ``Retry-After`` falls back to bounded exponential backoff."""
    state = {"n": 0}
    recorded: list[float] = []

    def _handler(_request: httpx.Request) -> httpx.Response:
        state["n"] += 1
        if state["n"] == 1:
            return httpx.Response(500, json={"err": "internal"})
        return httpx.Response(200, json={"changes": [], "newStartPageToken": "fresh"})

    shared = httpx.Client(transport=httpx.MockTransport(_handler))
    client = GoogleDriveClient(
        access_token="t",  # pragma: allowlist secret — test fixture
        http_client=shared,
        sleep_fn=recorded.append,
    )
    list(client.iter_changes("seed"))
    assert state["n"] == 2
    assert recorded[0] >= 2.0


def test_client_plain_403_raises_immediately() -> None:
    """403 without a rate-limit reason raises HTTPStatusError immediately."""
    state = {"n": 0}

    def _handler(_request: httpx.Request) -> httpx.Response:
        state["n"] += 1
        return httpx.Response(403, json={"error": {"errors": [{"reason": "permissionDenied"}]}})

    shared = httpx.Client(transport=httpx.MockTransport(_handler))
    client = GoogleDriveClient(
        access_token="t",  # pragma: allowlist secret — test fixture
        http_client=shared,
        sleep_fn=lambda _s: None,
    )
    with pytest.raises(httpx.HTTPStatusError) as exc_info:
        list(client.iter_changes("seed"))
    assert exc_info.value.response.status_code == 403
    assert state["n"] == 1


def test_client_429_persistent_eventually_raises() -> None:
    """N+1 429 responses exhaust the retry budget and raise."""
    state = {"n": 0}
    recorded: list[float] = []

    def _handler(_request: httpx.Request) -> httpx.Response:
        state["n"] += 1
        return httpx.Response(429, headers={"Retry-After": "1"}, json={"err": "throttled"})

    shared = httpx.Client(transport=httpx.MockTransport(_handler))
    client = GoogleDriveClient(
        access_token="t",  # pragma: allowlist secret — test fixture
        http_client=shared,
        sleep_fn=recorded.append,
        max_attempts=3,
    )
    with pytest.raises(httpx.HTTPStatusError) as exc_info:
        list(client.iter_changes("seed"))
    assert exc_info.value.response.status_code == 429
    assert state["n"] == 3


def test_change_event_modified_at_defaults_when_envelope_missing_modified_time() -> None:
    """When envelope.modified_time is None, the event carries a fresh ISO timestamp."""
    client = _FakeDriveClient(changes=[_build_drive_file(modified_time=None)])
    connector = _build_connector(fake_client=client)
    events = list(connector.list_changes(cursor=None))
    assert events[0].modified_at.endswith("Z")
    # Should be a plausible 2026+ ISO string
    parsed = datetime.fromisoformat(events[0].modified_at.replace("Z", "+00:00"))
    assert parsed.tzinfo == timezone.utc

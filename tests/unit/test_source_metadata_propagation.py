"""Unit-coverage tests for ADR-021 SourceMetadata propagation.

Lifts per-file unit coverage on the connector + extractor classes whose
new ``metadata_for`` methods (and supporting helpers) the F7 floor would
otherwise reject. Pure unit-level tests — no pipeline orchestration,
no factory composition, no integration fixtures. The integration
tests under ``tests/integration/test_<name>_metadata_propagation.py``
exercise the full pipeline path; this file only pins the method-level
contracts the F7 90% floor requires.

F1-clean: no monkeypatching. F2-clean: no env-var manipulation. Each
test constructs the production class with the smallest possible
seam-friendly inputs.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest

from kairix.connectors.dex_crm import DexCrmConnector
from kairix.connectors.dex_crm.client import DexCrmClient, DexCrmClientConfig
from kairix.connectors.github import GitHubConnector
from kairix.connectors.github.api_client import GitHubRepoRef
from kairix.connectors.m365_calendar import M365CalendarConfig, M365CalendarConnector
from kairix.connectors.m365_calendar.graph_client import (
    CalendarDeltaPage,
    CalendarEventRecord,
    M365GraphCalendarClient,
)
from kairix.connectors.m365_email_headers import M365EmailHeadersConnector
from kairix.connectors.m365_email_headers.connector import M365Credentials
from kairix.connectors.m365_email_headers.graph_client import GraphMessage, M365GraphClient
from kairix.connectors.notion import NotionApiClient, NotionConnector, NotionCredentials
from kairix.connectors.obsidian import ObsidianConnector
from kairix.connectors.sharepoint import (
    SharePointConnector,
    SharePointCredentials,
    SharePointDriveSpec,
    SharePointGraphClient,
)
from kairix.connectors.slack import SlackChannel, SlackConnector, SlackCredentials, SlackMessage, SlackWebClient
from kairix.core.connectors.escalation import EscalatingExtractor
from kairix.core.protocols import SourceMetadata
from kairix.extractors.passthrough import PassthroughExtractor
from kairix.transport.auth.api_key import ApiKeyAuth, BearerHeaders, reset_api_key_cache
from kairix.transport.auth.oauth2_client_creds import OAuth2ClientCredsAuth

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Obsidian — frontmatter + file stat envelope path
# ---------------------------------------------------------------------------


def test_obsidian_metadata_for_returns_frontmatter_and_mtime(tmp_path: Path) -> None:
    """ObsidianConnector lifts ``author`` + ``tags`` from YAML frontmatter."""
    vault = tmp_path / "vault"
    vault.mkdir()
    note = vault / "note.md"
    note.write_text(
        "---\nauthor: agent-alpha\ntags:\n  - x\n  - y\n---\n\n# body\n\ntext",
        encoding="utf-8",
    )
    connector = ObsidianConnector(vault_root=vault)
    try:
        result = connector.metadata_for("note.md")
    finally:
        connector.close()
    assert result.author == "agent-alpha"
    assert set(result.tags) == {"x", "y"}
    assert result.modified_at is not None  # file mtime
    assert result.created_at is not None


def test_obsidian_metadata_for_missing_file_returns_empty(tmp_path: Path) -> None:
    """Missing file → empty SourceMetadata, no exception."""
    vault = tmp_path / "vault"
    vault.mkdir()
    connector = ObsidianConnector(vault_root=vault)
    try:
        result = connector.metadata_for("missing.md")
    finally:
        connector.close()
    assert result == SourceMetadata()


def test_obsidian_metadata_for_no_frontmatter_returns_only_stat(tmp_path: Path) -> None:
    """File without frontmatter still surfaces mtime + ctime."""
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "plain.md").write_text("# heading\n\ntext", encoding="utf-8")
    connector = ObsidianConnector(vault_root=vault)
    try:
        result = connector.metadata_for("plain.md")
    finally:
        connector.close()
    assert result.modified_at is not None
    assert result.author is None
    assert result.tags == ()


def test_obsidian_metadata_for_tags_as_string_normalised(tmp_path: Path) -> None:
    """Single-string ``tags:`` value becomes a one-element tuple."""
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "single-tag.md").write_text("---\ntags: solo-tag\n---\n\nbody", encoding="utf-8")
    connector = ObsidianConnector(vault_root=vault)
    try:
        result = connector.metadata_for("single-tag.md")
    finally:
        connector.close()
    assert result.tags == ("solo-tag",)


def test_obsidian_metadata_for_path_traversal_returns_empty(tmp_path: Path) -> None:
    """Absolute item_id (rejected by _safe_resolve) collapses to empty metadata."""
    vault = tmp_path / "vault"
    vault.mkdir()
    connector = ObsidianConnector(vault_root=vault)
    try:
        result = connector.metadata_for("/absolute/path.md")
    finally:
        connector.close()
    assert result == SourceMetadata()


def test_obsidian_metadata_for_unclosed_frontmatter_returns_only_stat(tmp_path: Path) -> None:
    """Unclosed ``---`` block falls back to file-stat only (no author/tags)."""
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "unclosed.md").write_text("---\nauthor: agent-alpha\n\nbody-no-close", encoding="utf-8")
    connector = ObsidianConnector(vault_root=vault)
    try:
        result = connector.metadata_for("unclosed.md")
    finally:
        connector.close()
    assert result.author is None  # unclosed block → no frontmatter author
    assert result.tags == ()
    assert result.modified_at is not None  # file stat still works


def test_obsidian_metadata_for_malformed_yaml_returns_only_stat(tmp_path: Path) -> None:
    """yaml.safe_load failure on malformed block falls back to file-stat."""
    vault = tmp_path / "vault"
    vault.mkdir()
    # Tab inside list-item indent makes safe_load raise.
    (vault / "bad-yaml.md").write_text("---\n\t- bad: yaml: here\n---\n\nbody", encoding="utf-8")
    connector = ObsidianConnector(vault_root=vault)
    try:
        result = connector.metadata_for("bad-yaml.md")
    finally:
        connector.close()
    assert result.author is None
    assert result.tags == ()


def test_obsidian_metadata_for_yaml_list_root_returns_only_stat(tmp_path: Path) -> None:
    """YAML that parses to a list (not a dict) falls back to file-stat."""
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "list-root.md").write_text("---\n- a\n- b\n---\n\nbody", encoding="utf-8")
    connector = ObsidianConnector(vault_root=vault)
    try:
        result = connector.metadata_for("list-root.md")
    finally:
        connector.close()
    assert result.author is None
    assert result.tags == ()


def test_obsidian_metadata_for_unreadable_file_returns_empty(tmp_path: Path) -> None:
    """A path that exists but read_bytes OSError-s collapses cleanly.

    Simulated by passing a directory name as the item_id — ``is_file``
    returns False, exercising the early-return path.
    """
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "subdir").mkdir()
    connector = ObsidianConnector(vault_root=vault)
    try:
        result = connector.metadata_for("subdir")
    finally:
        connector.close()
    assert result == SourceMetadata()


# ---------------------------------------------------------------------------
# Dex CRM — record cache lift
# ---------------------------------------------------------------------------


class _ScriptedDexAuth(ApiKeyAuth):
    def headers(self, _secret_name: str) -> BearerHeaders:
        return BearerHeaders(mapping={"Authorization": "Bearer x"})


def _build_dex_connector(records: list[dict[str, Any]]) -> DexCrmConnector:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/contacts"):
            return httpx.Response(200, json={"data": records, "next_cursor": None})
        return httpx.Response(200, json={"data": [], "next_cursor": None})

    transport = httpx.MockTransport(handler)
    reset_api_key_cache()
    inner = httpx.Client(transport=transport)
    client = DexCrmClient(
        config=DexCrmClientConfig(rate_limit_sleep_s=0.0),
        http_client=inner,
        auth=_ScriptedDexAuth(),
        sleep=lambda _s: None,
    )
    return DexCrmConnector(client=client)


def test_dex_crm_metadata_for_returns_envelope_fields() -> None:
    """DexCrmConnector lifts modified_by + tags + updated_at from cached record."""
    connector = _build_dex_connector(
        [
            {
                "id": "c-1",
                "updated_at": "2026-05-28T08:00:00Z",
                "created_at": "2026-05-20T00:00:00Z",
                "modified_by": "agent-alpha",
                "created_by": "agent-beta",
                "tags": ["tag-one"],
            }
        ]
    )
    list(connector.list_changes(cursor=None))
    result = connector.metadata_for("contact:c-1")
    assert result.author == "agent-alpha"
    assert result.modified_at == "2026-05-28T08:00:00Z"
    assert result.created_at == "2026-05-20T00:00:00Z"
    assert result.tags == ("tag-one",)


def test_dex_crm_metadata_for_falls_back_to_created_by() -> None:
    """When ``modified_by`` is missing, ``created_by`` becomes the author."""
    connector = _build_dex_connector(
        [
            {
                "id": "c-2",
                "updated_at": "2026-05-28T08:00:00Z",
                "created_by": "fallback-author",
                "tags": "single",
            }
        ]
    )
    list(connector.list_changes(cursor=None))
    result = connector.metadata_for("contact:c-2")
    assert result.author == "fallback-author"
    assert result.tags == ("single",)


def test_dex_crm_metadata_for_cache_miss_returns_empty() -> None:
    """Unknown item_id (cache miss) collapses to empty SourceMetadata."""
    connector = _build_dex_connector([])
    result = connector.metadata_for("contact:missing")
    assert result == SourceMetadata()


# ---------------------------------------------------------------------------
# M365 email headers — envelope cache lift
# ---------------------------------------------------------------------------


class _MailGraphClient(M365GraphClient):
    def __init__(self, message: GraphMessage) -> None:
        self._message = message

    def iter_messages(self, start_url: str | None = None) -> Iterator[GraphMessage]:
        del start_url
        yield self._message

    def last_delta_link(self) -> str | None:
        return "https://graph.microsoft.com/v1.0/users/agent-alpha@example.com/messages/delta?$deltatoken=tok"


def test_m365_email_metadata_for_returns_sender_and_recipients() -> None:
    """M365EmailHeadersConnector lifts sender + received_at + to_recipients."""
    message = GraphMessage(
        message_id="m-1",
        sender="agent-alpha@example.com",
        to_recipients=("to1@example.com",),
        cc_recipients=(),
        subject="hello",
        sent_at="2026-05-28T07:00:00Z",
        received_at="2026-05-28T07:00:01Z",
    )
    connector = M365EmailHeadersConnector(
        user_principal_name="agent-alpha@example.com",
        credentials=M365Credentials(
            tenant_id="t",
            client_id="c",
            client_secret="s-value",  # pragma: allowlist secret — unit test fixture
        ),
        client_builder=lambda _a, _u: _MailGraphClient(message),
    )
    list(connector.list_changes(cursor=None))
    result = connector.metadata_for("m-1")
    assert result.author == "agent-alpha@example.com"
    assert result.author_email == "agent-alpha@example.com"
    assert result.modified_at == "2026-05-28T07:00:01Z"
    assert result.created_at == "2026-05-28T07:00:00Z"
    assert "to1@example.com" in result.tags


def test_m365_email_metadata_for_cache_miss_returns_empty() -> None:
    """Unknown message_id (cache miss) collapses to empty SourceMetadata."""
    connector = M365EmailHeadersConnector(
        user_principal_name="agent-alpha@example.com",
        credentials=M365Credentials(
            tenant_id="t",
            client_id="c",
            client_secret="s-value",  # pragma: allowlist secret — unit test fixture
        ),
        client_builder=lambda _a, _u: _MailGraphClient(
            GraphMessage(
                message_id="other",
                sender=None,
                to_recipients=(),
                cc_recipients=(),
                subject=None,
                sent_at=None,
                received_at=None,
            )
        ),
    )
    result = connector.metadata_for("not-seen")
    assert result == SourceMetadata()


# ---------------------------------------------------------------------------
# M365 calendar — event cache lift
# ---------------------------------------------------------------------------


class _CalendarClient(M365GraphCalendarClient):
    def __init__(self, page: CalendarDeltaPage) -> None:
        self._page = page
        self._user_id = "agent-alpha@example.com"
        self._http = None  # type: ignore[assignment]  # F3 rationale: scripted client owns no HTTP — boundary-only suppression.
        self._page_size = 50

    def fetch_initial_delta(self, _start_iso: str, _end_iso: str) -> CalendarDeltaPage:
        return self._page

    def fetch_delta_page(self, _link: str) -> CalendarDeltaPage:
        return self._page

    def close(self) -> None:
        # Intentionally empty — scripted client owns no resources.
        return None


def _calendar_page() -> CalendarDeltaPage:
    record = CalendarEventRecord(
        event_id="ev-1",
        subject="meeting",
        start_iso="2026-05-28T09:00:00Z",
        end_iso="2026-05-28T10:00:00Z",
        location="room",
        attendees=("att1@example.com",),
        organiser="agent-alpha@example.com",
        last_modified_iso="2026-05-28T08:30:00Z",
        cancelled=False,
        removed=False,
        raw_payload="{}",
    )
    return CalendarDeltaPage(events=(record,), next_link=None, delta_link="tok")


def test_m365_calendar_metadata_for_returns_organiser_and_attendees() -> None:
    """M365CalendarConnector lifts organiser + last_modified + attendees."""
    config = M365CalendarConfig(
        user_id="agent-alpha@example.com",
        tenant_id="t",
        client_id="c",
        client_secret="s-value",  # pragma: allowlist secret — unit test fixture
    )
    scripted = _CalendarClient(_calendar_page())
    connector = M365CalendarConnector(config, client_factory=lambda _c: scripted)
    list(connector.list_changes(cursor=None))
    result = connector.metadata_for("ev-1")
    assert result.author == "agent-alpha@example.com"
    assert result.author_email == "agent-alpha@example.com"
    assert result.modified_at == "2026-05-28T08:30:00Z"
    assert "att1@example.com" in result.tags


def test_m365_calendar_metadata_for_cache_miss_returns_empty() -> None:
    """Unknown event id collapses to empty SourceMetadata."""
    config = M365CalendarConfig(
        user_id="agent-alpha@example.com",
        tenant_id="t",
        client_id="c",
        client_secret="s-value",  # pragma: allowlist secret — unit test fixture
    )
    scripted = _CalendarClient(_calendar_page())
    connector = M365CalendarConnector(config, client_factory=lambda _c: scripted)
    result = connector.metadata_for("absent")
    assert result == SourceMetadata()


# ---------------------------------------------------------------------------
# SharePoint — drive-item cache lift
# ---------------------------------------------------------------------------


def _sharepoint_handler(envelope: dict[str, object], delta_link: str) -> httpx.MockTransport:
    body = {
        "@odata.context": "https://graph.microsoft.com/v1.0/$metadata#drives/d/root/delta",
        "value": [envelope],
        "@odata.deltaLink": delta_link,
    }

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "/oauth2/v2.0/token" in url:
            return httpx.Response(
                200,
                json={"access_token": "tok", "expires_in": 3600, "token_type": "Bearer"},
            )
        return httpx.Response(200, json=body)

    return httpx.MockTransport(handler)


def _build_sharepoint_connector(envelope: dict[str, object]) -> SharePointConnector:
    transport = _sharepoint_handler(envelope, "https://graph.example/delta?token=t")
    shared = httpx.Client(transport=transport)
    auth = OAuth2ClientCredsAuth(
        tenant_id="t",
        client_id="c",
        client_secret="s-value",  # pragma: allowlist secret — unit test fixture
        scope="https://graph.microsoft.com/.default",
        http_client=shared,
    )
    return SharePointConnector(
        drives=[SharePointDriveSpec(drive_id="b!d")],
        credentials=SharePointCredentials(
            tenant_id="t",
            client_id="c",
            client_secret="s-value",  # pragma: allowlist secret — unit test fixture
        ),
        auth=auth,
        client_builder=lambda a: SharePointGraphClient(auth=a, http_client=shared),
    )


def test_sharepoint_metadata_for_returns_createdby_and_tags() -> None:
    """SharePointConnector lifts createdBy + lastModifiedDateTime + path tags."""
    envelope = {
        "id": "sp-1",
        "name": "doc.md",
        "size": 50,
        "lastModifiedDateTime": "2026-05-28T09:30:00Z",
        "createdDateTime": "2026-05-20T00:00:00Z",
        "webUrl": "https://example/doc.md",
        "file": {"mimeType": "text/markdown"},
        "parentReference": {"driveId": "b!d", "path": "/drives/b!d/root:/Folder-A/Sub"},
        "createdBy": {"user": {"displayName": "agent-alpha"}},
        "lastModifiedBy": {"user": {"displayName": "agent-beta"}},
    }
    connector = _build_sharepoint_connector(envelope)
    list(connector.list_changes(cursor=None))
    result = connector.metadata_for("sp-1")
    assert result.author == "agent-alpha"
    assert result.modified_at == "2026-05-28T09:30:00Z"
    assert "Folder-A" in result.tags
    assert "Sub" in result.tags


def test_sharepoint_metadata_for_cache_miss_returns_empty() -> None:
    """Unknown id collapses to empty SourceMetadata."""
    envelope = {"id": "other", "name": "x.md", "size": 10}
    connector = _build_sharepoint_connector(envelope)
    result = connector.metadata_for("missing")
    assert result == SourceMetadata()


# ---------------------------------------------------------------------------
# Slack — message cache lift
# ---------------------------------------------------------------------------


class _ScriptedSlackClient(SlackWebClient):
    def __init__(self, *, channels: list[SlackChannel], messages: list[SlackMessage]) -> None:
        self._channels = channels
        self._messages = messages

    def conversations_list(self, *, types: Any = None) -> Iterator[SlackChannel]:
        del types
        yield from self._channels

    def conversations_history(self, *, channel_id: str, oldest: str | None = None) -> Iterator[SlackMessage]:
        del oldest
        yield from (m for m in self._messages if m.channel_id == channel_id)


def test_slack_metadata_for_returns_user_and_channel_tag() -> None:
    """SlackConnector lifts message user + channel name as tag."""
    channels = [
        SlackChannel(
            channel_id="C-1",
            name="general",
            kind="public_channel",
            is_archived=False,
            is_member=True,
        )
    ]
    messages = [
        SlackMessage(
            channel_id="C-1",
            ts="1716894000.000100",
            user="U-AGENT",
            text="hi",
            thread_ts="1716894000.000050",
            subtype=None,
            edited_ts=None,
        )
    ]
    client = _ScriptedSlackClient(channels=channels, messages=messages)
    connector = SlackConnector(
        credentials=SlackCredentials(bot_token="xoxb-test-fake-token-value"),
        web_client_factory=lambda _c: client,
    )
    list(connector.list_changes(cursor=None))
    result = connector.metadata_for("C-1:1716894000.000100")
    assert result.author == "U-AGENT"
    assert "general" in result.tags
    assert result.properties.get("thread_ts") == "1716894000.000050"


def test_slack_metadata_for_cache_miss_returns_empty() -> None:
    """Unknown item_id collapses to empty SourceMetadata."""
    client = _ScriptedSlackClient(channels=[], messages=[])
    connector = SlackConnector(
        credentials=SlackCredentials(bot_token="xoxb-test-fake-token-value"),
        web_client_factory=lambda _c: client,
    )
    result = connector.metadata_for("C-X:0.0")
    assert result == SourceMetadata()


# ---------------------------------------------------------------------------
# GitHub — envelope cache lift
# ---------------------------------------------------------------------------


class _GhClient:
    def __init__(self) -> None:
        self._repos = (
            GitHubRepoRef(
                repo_id=1,
                full_name="org/repo",
                default_branch="main",
                visibility="public",
                archived=False,
            ),
        )

    def list_installation_repositories(self) -> tuple[GitHubRepoRef, ...]:
        return self._repos

    def list_commits_since(self, *, full_name: str, since: str | None) -> Iterator[Any]:
        from kairix.connectors.github.api_client import GitHubCommitRef

        del since
        if full_name == "org/repo":
            yield GitHubCommitRef(
                sha="s1",
                committed_at="2026-05-28T10:00:00Z",
                message="m",
                author="agent-alpha",
            )

    def list_issues_since(self, *, full_name: str, since: str | None) -> Iterator[Any]:
        del full_name, since
        return iter(())

    def get_tree_recursive(self, *, full_name: str, ref: str) -> tuple[tuple[Any, ...], bool]:
        del full_name, ref
        return (), False

    def fetch_blob(self, *, full_name: str, sha: str) -> bytes:
        del full_name, sha
        return b""

    def stats(self) -> object:
        class _Stats:
            rest_requests = 0
            rest_rate_remaining = 5000
            rest_rate_reset_epoch = 0
            rest_403_secondary_total = 0
            installation_token_rotations = 0

        return _Stats()

    def invalidate_token(self) -> None:
        return None


def test_github_metadata_for_returns_author_and_committed_at() -> None:
    """GitHubConnector lifts commit author + committed_at + repo tag."""
    connector = GitHubConnector(client=_GhClient())  # type: ignore[arg-type]  # F3 rationale: scripted client mirrors GitHubApiClient shape for the test seam only.
    list(connector.list_changes(cursor=None))
    result = connector.metadata_for("github://org/repo/commit/s1")
    assert result.author == "agent-alpha"
    assert result.modified_at == "2026-05-28T10:00:00Z"
    assert "org/repo" in result.tags
    assert result.properties.get("kind") == "commit"


def test_github_metadata_for_cache_miss_returns_empty() -> None:
    """Unknown item_id collapses to empty SourceMetadata."""
    connector = GitHubConnector(client=_GhClient())  # type: ignore[arg-type]  # F3 rationale: scripted client mirrors GitHubApiClient shape for the test seam only.
    result = connector.metadata_for("github://org/other/commit/zzz")
    assert result == SourceMetadata()


# ---------------------------------------------------------------------------
# Notion — page cache lift
# ---------------------------------------------------------------------------


def _notion_handler(payload: dict[str, Any]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "/blocks/" in url and "/children" in url:
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "object": "block",
                            "id": "b",
                            "type": "paragraph",
                            "has_children": False,
                            "paragraph": {"rich_text": [{"plain_text": "body"}]},
                        }
                    ],
                    "next_cursor": None,
                    "has_more": False,
                },
            )
        return httpx.Response(200, json=payload)

    return httpx.MockTransport(handler)


def test_notion_metadata_for_returns_user_id_and_parent_type() -> None:
    """NotionConnector lifts last_edited_by id + last_edited_time + parent_type tag."""
    payload = {
        "results": [
            {
                "object": "page",
                "id": "p-1",
                "url": "https://notion.so/your-team/p-1",
                "last_edited_time": "2026-05-28T10:00:00.000Z",
                "created_time": "2026-05-20T00:00:00.000Z",
                "archived": False,
                "parent": {"type": "workspace", "workspace": True},
                "created_by": {"object": "user", "id": "u-alpha"},
                "last_edited_by": {"object": "user", "id": "u-alpha"},
                "properties": {
                    "title": {
                        "type": "title",
                        "title": [{"type": "text", "plain_text": "title-1"}],
                    }
                },
            }
        ],
        "next_cursor": None,
        "has_more": False,
    }
    shared = httpx.Client(transport=_notion_handler(payload))
    connector = NotionConnector(
        credentials=NotionCredentials(token="secret_fake_token_value"),  # pragma: allowlist secret — unit test fixture
        client_builder=lambda creds: NotionApiClient(token=creds.token, http_client=shared),
    )
    list(connector.list_changes(cursor=None))
    result = connector.metadata_for("p-1")
    assert result.author == "u-alpha"
    assert result.modified_at == "2026-05-28T10:00:00.000Z"
    assert "workspace" in result.tags


def test_notion_metadata_for_cache_miss_returns_empty() -> None:
    """Unknown id collapses to empty SourceMetadata."""
    payload = {"results": [], "next_cursor": None, "has_more": False}
    shared = httpx.Client(transport=_notion_handler(payload))
    connector = NotionConnector(
        credentials=NotionCredentials(token="secret_fake_token_value"),  # pragma: allowlist secret — unit test fixture
        client_builder=lambda creds: NotionApiClient(token=creds.token, http_client=shared),
    )
    result = connector.metadata_for("nope")
    assert result == SourceMetadata()


# ---------------------------------------------------------------------------
# Passthrough extractor — frontmatter parsing branches
# ---------------------------------------------------------------------------


def test_passthrough_metadata_for_extracts_markdown_frontmatter() -> None:
    """PassthroughExtractor parses ``---`` YAML frontmatter on text/markdown."""
    extractor = PassthroughExtractor(version="1.0.0")
    raw = b'---\nauthor: agent-alpha\ntags:\n  - a\n  - b\ndate: "2026-05-28"\n---\n\nbody'
    result = extractor.metadata_for(raw, "text/markdown")
    assert result.author == "agent-alpha"
    assert set(result.tags) == {"a", "b"}
    assert result.modified_at == "2026-05-28"


def test_passthrough_metadata_for_string_tag_normalised() -> None:
    """Single-string ``tags:`` value becomes a one-element tuple."""
    extractor = PassthroughExtractor(version="1.0.0")
    raw = b"---\ntags: single\n---\nbody"
    result = extractor.metadata_for(raw, "text/markdown")
    assert result.tags == ("single",)


def test_passthrough_metadata_for_non_markdown_returns_empty() -> None:
    """Non-markdown mime collapses to empty SourceMetadata."""
    extractor = PassthroughExtractor(version="1.0.0")
    result = extractor.metadata_for(b"binary blob", "application/octet-stream")
    assert result == SourceMetadata()


def test_passthrough_metadata_for_no_frontmatter_returns_empty() -> None:
    """Markdown without ``---`` block returns empty SourceMetadata."""
    extractor = PassthroughExtractor(version="1.0.0")
    result = extractor.metadata_for(b"# heading only\n\nbody", "text/markdown")
    assert result == SourceMetadata()


def test_passthrough_metadata_for_unclosed_frontmatter_returns_empty() -> None:
    """Frontmatter without a closing ``---`` returns empty SourceMetadata."""
    extractor = PassthroughExtractor(version="1.0.0")
    result = extractor.metadata_for(b"---\nauthor: agent-alpha\n\nbody-no-close", "text/markdown")
    assert result == SourceMetadata()


def test_passthrough_metadata_for_malformed_yaml_returns_empty() -> None:
    """Frontmatter that fails YAML parse returns empty SourceMetadata."""
    extractor = PassthroughExtractor(version="1.0.0")
    result = extractor.metadata_for(b"---\nthis: is: not: yaml: ok\n---\nbody", "text/markdown")
    assert result == SourceMetadata()


def test_passthrough_metadata_for_yaml_list_root_returns_empty() -> None:
    """Frontmatter parsing to a list (not a dict) returns empty SourceMetadata."""
    extractor = PassthroughExtractor(version="1.0.0")
    result = extractor.metadata_for(b"---\n- one\n- two\n---\nbody", "text/markdown")
    assert result == SourceMetadata()


# ---------------------------------------------------------------------------
# EscalatingExtractor — chain-wide metadata merge
# ---------------------------------------------------------------------------


class _MetadataMember:
    name = "scripted"
    version = "1.0.0"

    def __init__(self, metadata: SourceMetadata | None) -> None:
        self._metadata = metadata

    def can_extract(self, _mime: str, _magic: bytes) -> bool:
        return True

    def extract(self, _raw: bytes, _mime: str) -> Any:
        from kairix.core.protocols import DocMetadata, ExtractedDocument

        return ExtractedDocument(
            markdown="body",
            pages=(),
            images=(),
            metadata=DocMetadata(title=None, author=None, created_date=None, language=None, page_count=None),
            confidence=1.0,
        )

    def quality_ok(self, _doc: Any) -> bool:
        return True

    def metadata_for(self, _raw: bytes, _mime: str) -> SourceMetadata:
        if self._metadata is None:
            raise RuntimeError("scripted failure")
        return self._metadata


def test_escalating_extractor_metadata_for_unions_member_outputs() -> None:
    """First non-None field wins; tags + properties union."""
    chain = EscalatingExtractor(
        members=(
            _MetadataMember(SourceMetadata(author="from-first", tags=("a",))),
            _MetadataMember(SourceMetadata(modified_at="2026-05-28T00:00:00Z", tags=("b",))),
        )
    )
    result = chain.metadata_for(b"raw", "text/plain")
    assert result.author == "from-first"
    assert result.modified_at == "2026-05-28T00:00:00Z"
    assert set(result.tags) == {"a", "b"}


def test_escalating_extractor_metadata_for_member_failure_isolated() -> None:
    """A raising member is skipped; the rest of the chain still contributes."""
    chain = EscalatingExtractor(
        members=(
            _MetadataMember(None),  # raises
            _MetadataMember(SourceMetadata(author="surviving")),
        )
    )
    result = chain.metadata_for(b"raw", "text/plain")
    assert result.author == "surviving"


def test_escalating_extractor_metadata_for_empty_chain_safe() -> None:
    """Single-member chain still produces the member's metadata."""
    chain = EscalatingExtractor(members=(_MetadataMember(SourceMetadata(author="solo")),))
    result = chain.metadata_for(b"raw", "text/plain")
    assert result.author == "solo"

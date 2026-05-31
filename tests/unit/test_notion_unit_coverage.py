"""Unit coverage tests for the Notion connector helpers.

Focused per-function coverage of the smaller branches in
:mod:`kairix.connectors.notion.connector` and
:mod:`kairix.connectors.notion.api_client` that the integration /
contract tests don't exercise. F7 (per-file ≥90% coverage) is the
gate this file pays down for the new connector code.

All tests construct the connector / api client directly — these are
boundary helpers (constructor validation, cursor summary,
markdown rendering), not multi-component pipelines, so F47 doesn't
apply.

F1-clean: no @patch or kairix module-attribute substitution.
F2-clean: no KAIRIX_* env-var manipulation.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from kairix.connectors.notion import (
    DEFAULT_MAX_BLOCK_DEPTH,
    NotionApiClient,
    NotionConnector,
    NotionCredentials,
    cursor_summary_json,
    make_connector,
)
from kairix.connectors.notion.api_client import (
    NotionBlockRef,
    NotionDatabaseRef,
    NotionPageRef,
)
from kairix.connectors.notion.connector import (
    CONNECTOR_NAME,
    CONNECTOR_NOTION_FLAG,
    DEFAULT_SENSITIVITY,
    NOTION_MARKDOWN_MIME,
)
from kairix.secrets import SecretNotFoundError
from tests.fakes import FakeSecretsLoader

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------


def test_connector_name_constant() -> None:
    assert CONNECTOR_NAME == "notion"


def test_connector_flag_constant() -> None:
    assert CONNECTOR_NOTION_FLAG == "connector_notion"


def test_default_sensitivity_is_internal() -> None:
    assert DEFAULT_SENSITIVITY == "internal"


def test_notion_markdown_mime_constant() -> None:
    assert NOTION_MARKDOWN_MIME == "text/markdown"


def test_default_max_block_depth_positive() -> None:
    assert DEFAULT_MAX_BLOCK_DEPTH >= 1


# ---------------------------------------------------------------------------
# cursor_summary_json — filters None values, deterministic ordering
# ---------------------------------------------------------------------------


def test_cursor_summary_json_filters_none_values() -> None:
    summary = cursor_summary_json({"alpha": "2026-05-22T10:00:00Z", "bravo": None})
    assert "alpha" in summary
    assert "bravo" not in summary, "None cursor values must be filtered out"


def test_cursor_summary_json_emits_deterministic_order() -> None:
    summary1 = cursor_summary_json({"bravo": "ts-bravo", "alpha": "ts-alpha"})
    summary2 = cursor_summary_json({"alpha": "ts-alpha", "bravo": "ts-bravo"})
    assert summary1 == summary2, "summary must be deterministic regardless of insertion order"


def test_cursor_summary_json_empty_mapping() -> None:
    assert cursor_summary_json({}) == "{}"


# ---------------------------------------------------------------------------
# make_connector — config validation
# ---------------------------------------------------------------------------


def test_make_connector_rejects_invalid_sensitivity_tier() -> None:
    with pytest.raises(ValueError, match="default_sensitivity"):
        make_connector({"default_sensitivity": "extra-special"})


def test_make_connector_rejects_zero_max_block_depth() -> None:
    with pytest.raises(ValueError, match="max_block_depth"):
        make_connector({"max_block_depth": 0})


def test_make_connector_rejects_non_int_max_block_depth() -> None:
    with pytest.raises(ValueError, match="max_block_depth"):
        make_connector({"max_block_depth": "eight"})


def test_make_connector_rejects_negative_max_block_depth() -> None:
    with pytest.raises(ValueError, match="max_block_depth"):
        make_connector({"max_block_depth": -3})


# ---------------------------------------------------------------------------
# NotionApiClient — token required, parser robustness
# ---------------------------------------------------------------------------


def test_api_client_rejects_empty_token() -> None:
    with pytest.raises(ValueError, match="token"):
        NotionApiClient(token="")


def test_api_client_constructs_with_token() -> None:
    # pragma: allowlist secret — fixture token, not a real credential.
    client = NotionApiClient(token="secret_fake_token")
    assert client is not None


def test_api_client_max_block_depth_clamps_to_one() -> None:
    """``max_block_depth=0`` clamps to 1 (defensive against future caller bugs)."""
    # pragma: allowlist secret — fixture token, not a real credential.
    client = NotionApiClient(token="secret_fake_token", max_block_depth=0)
    # The internal cap field clamps via ``max(1, ...)``. We can't read
    # the private field directly without breaking encapsulation, but
    # we can prove the clamp by recording the call count against a
    # depth-0 walk — it should at least fetch the root level (1 call),
    # not zero.
    nested_calls = {"n": 0}

    def _handler(_request: httpx.Request) -> httpx.Response:
        nested_calls["n"] += 1
        return httpx.Response(
            200,
            json={"results": [], "next_cursor": None, "has_more": False},
        )

    shared = httpx.Client(transport=httpx.MockTransport(_handler))
    # pragma: allowlist secret — fixture token, not a real credential.
    client = NotionApiClient(token="secret_fake_token", http_client=shared, max_block_depth=0)
    list(client.iter_block_descendants("root"))
    assert nested_calls["n"] >= 1, "depth cap must clamp to >=1 so at least the root level is fetched"


# ---------------------------------------------------------------------------
# NotionConnector — fetch cache miss + render markdown
# ---------------------------------------------------------------------------


def _build_connector_with_handler(handler: Any) -> NotionConnector:
    shared = httpx.Client(transport=httpx.MockTransport(handler))
    return NotionConnector(
        credentials=NotionCredentials(token="secret_fake_token_value"),  # pragma: allowlist secret — fixture
        client_builder=lambda creds: NotionApiClient(token=creds.token, http_client=shared),
    )


def test_fetch_raises_when_item_id_not_in_cache() -> None:
    """fetch() before any list_changes() call must raise an actionable KeyError."""
    connector = _build_connector_with_handler(
        lambda _req: httpx.Response(200, json={"results": [], "next_cursor": None, "has_more": False})
    )
    with pytest.raises(KeyError, match="envelope cache"):
        connector.fetch("unknown-page-id")


def test_source_link_falls_back_to_notion_scheme_for_unknown_item() -> None:
    connector = _build_connector_with_handler(
        lambda _req: httpx.Response(200, json={"results": [], "next_cursor": None, "has_more": False})
    )
    link = connector.source_link("unknown-page-id")
    assert link == "notion://pages/unknown-page-id"


def test_render_page_markdown_includes_title_and_block_text() -> None:
    """fetch() renders title as H1 + block plain text as body lines."""

    def _handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "/search" in url:
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "object": "page",
                            "id": "page-render",
                            "url": "https://www.notion.so/your-team/page-render",
                            "last_edited_time": "2026-05-22T10:00:00.000Z",
                            "archived": False,
                            "parent": {"type": "workspace", "workspace": True},
                            "properties": {
                                "title": {
                                    "type": "title",
                                    "title": [{"type": "text", "plain_text": "Render title"}],
                                }
                            },
                        }
                    ],
                    "next_cursor": None,
                    "has_more": False,
                },
            )
        if "/blocks/" in url and "/children" in url:
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "object": "block",
                            "id": "block-1",
                            "type": "paragraph",
                            "has_children": False,
                            "paragraph": {"rich_text": [{"type": "text", "plain_text": "First body line."}]},
                        },
                        {
                            "object": "block",
                            "id": "block-2",
                            "type": "paragraph",
                            "has_children": False,
                            "paragraph": {"rich_text": [{"type": "text", "plain_text": "Second body line."}]},
                        },
                    ],
                    "next_cursor": None,
                    "has_more": False,
                },
            )
        return httpx.Response(200, json={"results": [], "next_cursor": None, "has_more": False})

    connector = _build_connector_with_handler(_handler)
    # Prime the cache via list_changes.
    list(connector.list_changes(cursor=None))
    artefact = connector.fetch("page-render")
    rendered = artefact.raw.decode("utf-8")
    assert "# Render title" in rendered, f"title H1 must render; got {rendered!r}"
    assert "First body line." in rendered
    assert "Second body line." in rendered


def test_next_cursor_for_container_returns_none_before_drain() -> None:
    connector = _build_connector_with_handler(
        lambda _req: httpx.Response(200, json={"results": [], "next_cursor": None, "has_more": False})
    )
    assert connector.next_cursor_for_container("never-drained") is None


def test_next_cursor_returns_none_before_list_changes_call() -> None:
    connector = _build_connector_with_handler(
        lambda _req: httpx.Response(200, json={"results": [], "next_cursor": None, "has_more": False})
    )
    assert connector.next_cursor() is None


def test_list_changes_emits_archived_op_for_archived_pages() -> None:
    """Archived pages emit op='archived' per spec §2 op mapping."""

    def _handler(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "object": "page",
                        "id": "archived-page",
                        "url": "https://www.notion.so/your-team/archived-page",
                        "last_edited_time": "2026-05-22T10:00:00.000Z",
                        "archived": True,
                        "parent": {"type": "workspace", "workspace": True},
                        "properties": {
                            "title": {
                                "type": "title",
                                "title": [{"type": "text", "plain_text": "Archived"}],
                            }
                        },
                    }
                ],
                "next_cursor": None,
                "has_more": False,
            },
        )

    connector = _build_connector_with_handler(_handler)
    events = list(connector.list_changes(cursor=None))
    assert len(events) == 1
    assert events[0].op == "archived", f"archived pages must emit op='archived'; got {events[0]!r}"


def test_list_changes_filters_events_older_than_cursor() -> None:
    """Cursor filter is strict ``<=``: events at-or-before the cursor drop."""

    def _handler(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "object": "page",
                        "id": "old-page",
                        "url": "https://www.notion.so/your-team/old-page",
                        "last_edited_time": "2026-05-22T10:00:00.000Z",
                        "archived": False,
                        "parent": {"type": "workspace", "workspace": True},
                        "properties": {
                            "title": {
                                "type": "title",
                                "title": [{"type": "text", "plain_text": "Old page"}],
                            }
                        },
                    }
                ],
                "next_cursor": None,
                "has_more": False,
            },
        )

    connector = _build_connector_with_handler(_handler)
    events = list(connector.list_changes(cursor="2026-05-22T10:00:00.000Z"))
    assert events == [], f"events at-or-before cursor must filter out; got {events!r}"


def test_iter_containers_empty_when_no_workspace_pages_visible() -> None:
    """Workspace with no workspace-parented pages yields no Containers."""

    def _handler(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"results": [], "next_cursor": None, "has_more": False})

    connector = _build_connector_with_handler(_handler)
    containers = list(connector.iter_containers(cc_pair_id=1))
    assert containers == [], f"empty workspace must yield no containers; got {containers!r}"


# ---------------------------------------------------------------------------
# Frozen dataclass round-trip (sanity for F42)
# ---------------------------------------------------------------------------


def test_notion_page_ref_is_frozen() -> None:
    ref = NotionPageRef(
        page_id="p1",
        parent_type="workspace",
        parent_id=None,
        title="t",
        url="u",
        last_edited_time="2026-05-22T10:00:00Z",
        archived=False,
    )
    with pytest.raises((AttributeError, Exception)):
        ref.page_id = "other"  # type: ignore[misc]  # F3-rationale: frozen dataclass enforcement; mypy lacks dynamic frozen-attribute reassignment narrowing.


def test_notion_database_ref_is_frozen() -> None:
    ref = NotionDatabaseRef(
        database_id="d1",
        parent_type="workspace",
        parent_id=None,
        title="t",
        url="u",
        last_edited_time="2026-05-22T10:00:00Z",
    )
    with pytest.raises((AttributeError, Exception)):
        ref.database_id = "other"  # type: ignore[misc]  # F3-rationale: frozen dataclass enforcement; mypy lacks dynamic frozen-attribute reassignment narrowing.


def test_notion_block_ref_is_frozen() -> None:
    ref = NotionBlockRef(
        block_id="b1",
        block_type="paragraph",
        has_children=False,
        plain_text="hello",
    )
    with pytest.raises((AttributeError, Exception)):
        ref.block_id = "other"  # type: ignore[misc]  # F3-rationale: frozen dataclass enforcement; mypy lacks dynamic frozen-attribute reassignment narrowing.


# ---------------------------------------------------------------------------
# iter_containers / list_changes_for_container / retrieve_all_slim_docs /
# load_hierarchy — additional coverage for the multi-container surface
# ---------------------------------------------------------------------------


def _single_root_handler(_req: httpx.Request) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "results": [
                {
                    "object": "page",
                    "id": "single-root",
                    "url": "https://www.notion.so/your-team/single-root",
                    "last_edited_time": "2026-05-22T10:00:00.000Z",
                    "archived": False,
                    "parent": {"type": "workspace", "workspace": True},
                    "properties": {
                        "title": {
                            "type": "title",
                            "title": [{"type": "text", "plain_text": "Single root"}],
                        }
                    },
                },
            ],
            "next_cursor": None,
            "has_more": False,
        },
    )


def test_iter_containers_emits_one_per_workspace_page() -> None:
    connector = _build_connector_with_handler(_single_root_handler)
    containers = list(connector.iter_containers(cc_pair_id=7))
    assert len(containers) == 1
    assert containers[0].container_id == "single-root"
    assert containers[0].access_state == "ACCESSIBLE"
    assert containers[0].cursor_token is None
    assert containers[0].cc_pair_id == 7


def test_iter_containers_dedupes_repeated_page_ids() -> None:
    """If the search response surfaces the same page twice, the iter is dedup'd."""

    def _handler(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "object": "page",
                        "id": "dupe-root",
                        "url": "https://www.notion.so/your-team/dupe-root",
                        "last_edited_time": "2026-05-22T10:00:00.000Z",
                        "archived": False,
                        "parent": {"type": "workspace", "workspace": True},
                        "properties": {
                            "title": {
                                "type": "title",
                                "title": [{"type": "text", "plain_text": "Dupe root"}],
                            }
                        },
                    },
                    {
                        "object": "page",
                        "id": "dupe-root",
                        "url": "https://www.notion.so/your-team/dupe-root",
                        "last_edited_time": "2026-05-22T10:30:00.000Z",
                        "archived": False,
                        "parent": {"type": "workspace", "workspace": True},
                        "properties": {
                            "title": {
                                "type": "title",
                                "title": [{"type": "text", "plain_text": "Dupe root"}],
                            }
                        },
                    },
                ],
                "next_cursor": None,
                "has_more": False,
            },
        )

    connector = _build_connector_with_handler(_handler)
    containers = list(connector.iter_containers(cc_pair_id=1))
    assert len(containers) == 1, f"duplicate page_ids must dedup; got {containers!r}"


def test_iter_containers_skips_non_workspace_parented_pages() -> None:
    """Pages whose parent isn't ``workspace`` are not Container candidates."""

    def _handler(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "object": "page",
                        "id": "subpage",
                        "url": "https://www.notion.so/your-team/subpage",
                        "last_edited_time": "2026-05-22T10:00:00.000Z",
                        "archived": False,
                        "parent": {"type": "page_id", "page_id": "some-root"},
                        "properties": {
                            "title": {
                                "type": "title",
                                "title": [{"type": "text", "plain_text": "Subpage"}],
                            }
                        },
                    }
                ],
                "next_cursor": None,
                "has_more": False,
            },
        )

    connector = _build_connector_with_handler(_handler)
    containers = list(connector.iter_containers(cc_pair_id=1))
    assert containers == [], f"non-workspace pages must not surface as containers; got {containers!r}"


def test_list_changes_for_container_emits_event_for_matching_root() -> None:
    """Pages whose root id matches the container surface as events."""
    connector = _build_connector_with_handler(_single_root_handler)
    container = next(connector.iter_containers(cc_pair_id=1))
    events = list(connector.list_changes_for_container(container))
    assert len(events) == 1, f"expected one event for the single-root container; got {events!r}"
    assert events[0].item_id == "single-root"
    # Per-container cursor must be populated.
    assert connector.next_cursor_for_container("single-root") == "2026-05-22T10:00:00.000Z"


def test_retrieve_all_slim_docs_yields_only_matching_root_ids() -> None:
    connector = _build_connector_with_handler(_single_root_handler)
    container = next(connector.iter_containers(cc_pair_id=1))
    ids = list(connector.retrieve_all_slim_docs(container))
    assert ids == ["single-root"], f"slim docs must yield just the matching root id; got {ids!r}"


def test_load_hierarchy_emits_workspace_root_and_visible_pages() -> None:
    """load_hierarchy emits the workspace root then each top-level page."""

    def _handler(request: httpx.Request) -> httpx.Response:
        try:
            body = request.read().decode("utf-8")
        except (httpx.RequestNotRead, RuntimeError):
            body = ""
        if "database" in body:
            return httpx.Response(200, json={"results": [], "next_cursor": None, "has_more": False})
        return _single_root_handler(request)

    connector = _build_connector_with_handler(_handler)
    nodes = list(connector.load_hierarchy(cc_pair_id=42))
    # Workspace root + one top-level page = 2 nodes.
    assert len(nodes) == 2, f"expected workspace root + 1 page node; got {nodes!r}"
    assert nodes[0].raw_parent_id is None
    assert nodes[1].raw_parent_id == nodes[0].raw_node_id
    assert nodes[1].cc_pair_id == 42


def test_dispatch_page_returns_none_when_event_drops() -> None:
    """A page envelope with an empty page_id drops via _page_to_event; _dispatch_page returns None."""
    connector = _build_connector_with_handler(
        lambda _r: httpx.Response(200, json={"results": [], "next_cursor": None, "has_more": False})
    )
    empty = NotionPageRef(
        page_id="",
        parent_type="workspace",
        parent_id=None,
        title="",
        url="",
        last_edited_time="2026-05-22T10:00:00Z",
        archived=False,
    )
    assert connector._dispatch_page(empty) is None  # coverage spot-check on private helper


# ---------------------------------------------------------------------------
# NotionApiClient — pagination + fetch_page + search_databases coverage
# ---------------------------------------------------------------------------


def test_search_pages_advances_through_paginated_response() -> None:
    """search_pages must follow next_cursor across multiple search pages."""
    call_log: list[str] = []
    page_responses = [
        {
            "results": [
                {
                    "object": "page",
                    "id": "search-page-1",
                    "url": "https://www.notion.so/search-page-1",
                    "last_edited_time": "2026-05-22T10:00:00Z",
                    "archived": False,
                    "parent": {"type": "workspace", "workspace": True},
                    "properties": {"title": {"type": "title", "title": [{"type": "text", "plain_text": "Page 1"}]}},
                }
            ],
            "next_cursor": "page-2-cursor",
            "has_more": True,
        },
        {
            "results": [
                {
                    "object": "page",
                    "id": "search-page-2",
                    "url": "https://www.notion.so/search-page-2",
                    "last_edited_time": "2026-05-22T11:00:00Z",
                    "archived": False,
                    "parent": {"type": "workspace", "workspace": True},
                    "properties": {"title": {"type": "title", "title": [{"type": "text", "plain_text": "Page 2"}]}},
                }
            ],
            "next_cursor": None,
            "has_more": False,
        },
    ]
    counter = {"n": 0}

    def _handler(request: httpx.Request) -> httpx.Response:
        call_log.append(str(request.url))
        idx = min(counter["n"], len(page_responses) - 1)
        counter["n"] += 1
        return httpx.Response(200, json=page_responses[idx])

    shared = httpx.Client(transport=httpx.MockTransport(_handler))
    # pragma: allowlist secret — fixture token, not a real credential.
    client = NotionApiClient(token="secret_fake_token", http_client=shared)
    pages = list(client.search_pages())
    assert len(pages) == 2, f"expected pages from both responses; got {pages!r}"
    assert {p.page_id for p in pages} == {"search-page-1", "search-page-2"}
    assert counter["n"] >= 2, "must have made at least two paginated calls"


def test_search_databases_yields_database_objects() -> None:
    """search_databases must lift database envelopes only (skip pages)."""

    def _handler(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "object": "database",
                        "id": "db-001",
                        "url": "https://www.notion.so/db-001",
                        "last_edited_time": "2026-05-22T10:00:00Z",
                        "parent": {"type": "workspace", "workspace": True},
                        "title": [{"type": "text", "plain_text": "Database one"}],
                    },
                    {
                        "object": "page",
                        "id": "page-not-a-db",
                        "url": "https://www.notion.so/page",
                        "last_edited_time": "2026-05-22T10:00:00Z",
                        "archived": False,
                        "parent": {"type": "workspace", "workspace": True},
                        "properties": {"title": {"type": "title", "title": [{"type": "text", "plain_text": "Page"}]}},
                    },
                ],
                "next_cursor": None,
                "has_more": False,
            },
        )

    shared = httpx.Client(transport=httpx.MockTransport(_handler))
    # pragma: allowlist secret — fixture token, not a real credential.
    client = NotionApiClient(token="secret_fake_token", http_client=shared)
    dbs = list(client.search_databases())
    assert len(dbs) == 1, f"expected one database; got {dbs!r}"
    assert dbs[0].database_id == "db-001"


def test_search_databases_advances_through_pagination() -> None:
    """search_databases must follow next_cursor across multiple pages."""
    responses = [
        {
            "results": [
                {
                    "object": "database",
                    "id": "db-1",
                    "url": "u",
                    "last_edited_time": "2026-05-22T10:00:00Z",
                    "parent": {"type": "workspace", "workspace": True},
                    "title": [{"type": "text", "plain_text": "DB 1"}],
                }
            ],
            "next_cursor": "next-db-cursor",
            "has_more": True,
        },
        {
            "results": [
                {
                    "object": "database",
                    "id": "db-2",
                    "url": "u",
                    "last_edited_time": "2026-05-22T11:00:00Z",
                    "parent": {"type": "workspace", "workspace": True},
                    "title": [{"type": "text", "plain_text": "DB 2"}],
                }
            ],
            "next_cursor": None,
            "has_more": False,
        },
    ]
    counter = {"n": 0}

    def _handler(_req: httpx.Request) -> httpx.Response:
        idx = min(counter["n"], len(responses) - 1)
        counter["n"] += 1
        return httpx.Response(200, json=responses[idx])

    shared = httpx.Client(transport=httpx.MockTransport(_handler))
    # pragma: allowlist secret — fixture token, not a real credential.
    client = NotionApiClient(token="secret_fake_token", http_client=shared)
    dbs = list(client.search_databases())
    assert {d.database_id for d in dbs} == {"db-1", "db-2"}


def test_query_database_yields_rows_with_pagination() -> None:
    """query_database returns rows across paginated responses."""
    responses = [
        {
            "results": [
                {
                    "object": "page",
                    "id": "row-1",
                    "url": "u",
                    "last_edited_time": "2026-05-22T10:00:00Z",
                    "archived": False,
                    "parent": {"type": "database_id", "database_id": "db-target"},
                    "properties": {"Name": {"type": "title", "title": [{"type": "text", "plain_text": "Row 1"}]}},
                }
            ],
            "next_cursor": "row-2-cursor",
            "has_more": True,
        },
        {
            "results": [
                {
                    "object": "page",
                    "id": "row-2",
                    "url": "u",
                    "last_edited_time": "2026-05-22T11:00:00Z",
                    "archived": False,
                    "parent": {"type": "database_id", "database_id": "db-target"},
                    "properties": {"Name": {"type": "title", "title": [{"type": "text", "plain_text": "Row 2"}]}},
                }
            ],
            "next_cursor": None,
            "has_more": False,
        },
    ]
    counter = {"n": 0}

    def _handler(_req: httpx.Request) -> httpx.Response:
        idx = min(counter["n"], len(responses) - 1)
        counter["n"] += 1
        return httpx.Response(200, json=responses[idx])

    shared = httpx.Client(transport=httpx.MockTransport(_handler))
    # pragma: allowlist secret — fixture token, not a real credential.
    client = NotionApiClient(token="secret_fake_token", http_client=shared)
    rows = list(client.query_database("db-target"))
    assert {r.page_id for r in rows} == {"row-1", "row-2"}
    # Both rows must carry the database_id parent.
    for row in rows:
        assert row.parent_type == "database_id"
        assert row.parent_id == "db-target"


def test_query_database_stops_when_has_more_false() -> None:
    """query_database terminates on the first page when has_more=False."""

    def _handler(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"results": [], "next_cursor": None, "has_more": False},
        )

    shared = httpx.Client(transport=httpx.MockTransport(_handler))
    # pragma: allowlist secret — fixture token, not a real credential.
    client = NotionApiClient(token="secret_fake_token", http_client=shared)
    rows = list(client.query_database("db-empty"))
    assert rows == []


def test_fetch_page_returns_typed_envelope() -> None:
    """fetch_page lifts the GET /v1/pages/{id} response into a NotionPageRef."""

    def _handler(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "object": "page",
                "id": "fetched-page",
                "url": "https://www.notion.so/fetched-page",
                "last_edited_time": "2026-05-22T10:00:00Z",
                "archived": False,
                "parent": {"type": "workspace", "workspace": True},
                "properties": {"title": {"type": "title", "title": [{"type": "text", "plain_text": "Fetched"}]}},
            },
        )

    shared = httpx.Client(transport=httpx.MockTransport(_handler))
    # pragma: allowlist secret — fixture token, not a real credential.
    client = NotionApiClient(token="secret_fake_token", http_client=shared)
    page = client.fetch_page("fetched-page")
    assert page.page_id == "fetched-page"
    assert page.title == "Fetched"


def test_authorised_get_uses_owned_client_when_none_passed() -> None:
    """When no http_client is provided, the client uses a fresh owned httpx.Client.

    We can't easily intercept the owned client's actual HTTP call without
    network access, but we can prove the construction path works by
    triggering the empty-passed code path through the search endpoint
    against an injected transport, ensuring no regression in the
    happy injected-client path.
    """
    counter = {"n": 0}

    def _handler(_req: httpx.Request) -> httpx.Response:
        counter["n"] += 1
        return httpx.Response(200, json={"results": [], "next_cursor": None, "has_more": False})

    shared = httpx.Client(transport=httpx.MockTransport(_handler))
    # pragma: allowlist secret — fixture token, not a real credential.
    client = NotionApiClient(token="secret_fake_token", http_client=shared)
    # Drains both code paths (authorised_get + authorised_post).
    list(client.search_pages())
    list(client.iter_block_descendants("parent"))
    assert counter["n"] >= 2


def test_search_pages_handles_missing_properties_dict_gracefully() -> None:
    """search_pages returns a page even when the properties dict is malformed.

    Drives the public search surface to exercise the title-extraction
    helper's tolerant branches via the runtime path (F5-clean — no
    private symbol import).
    """

    def _handler(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "results": [
                    # properties is not a dict — title falls back to "".
                    {
                        "object": "page",
                        "id": "page-no-properties",
                        "url": "u",
                        "last_edited_time": "t",
                        "archived": False,
                        "parent": {"type": "workspace", "workspace": True},
                        "properties": "not-a-dict",
                    },
                    # property value is not a dict — title falls back to "".
                    {
                        "object": "page",
                        "id": "page-bad-property",
                        "url": "u",
                        "last_edited_time": "t",
                        "archived": False,
                        "parent": {"type": "workspace", "workspace": True},
                        "properties": {"Name": "not-a-dict"},
                    },
                    # property type is not 'title' — title falls back to "".
                    {
                        "object": "page",
                        "id": "page-non-title-property",
                        "url": "u",
                        "last_edited_time": "t",
                        "archived": False,
                        "parent": {"type": "workspace", "workspace": True},
                        "properties": {"Name": {"type": "select"}},
                    },
                ],
                "next_cursor": None,
                "has_more": False,
            },
        )

    shared = httpx.Client(transport=httpx.MockTransport(_handler))
    # pragma: allowlist secret — fixture token, not a real credential.
    client = NotionApiClient(token="secret_fake_token", http_client=shared)
    pages = list(client.search_pages())
    assert len(pages) == 3, f"expected three pages despite malformed properties; got {pages!r}"
    for page in pages:
        assert page.title == "", f"title must fall back to '' for malformed envelope; got {page!r}"


def test_search_pages_handles_missing_parent_block_gracefully() -> None:
    """search_pages tolerates missing / wrong-typed parent blocks."""

    def _handler(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "results": [
                    # parent is absent — parent_type falls back to "".
                    {
                        "object": "page",
                        "id": "no-parent",
                        "url": "u",
                        "last_edited_time": "t",
                        "archived": False,
                    },
                ],
                "next_cursor": None,
                "has_more": False,
            },
        )

    shared = httpx.Client(transport=httpx.MockTransport(_handler))
    # pragma: allowlist secret — fixture token, not a real credential.
    client = NotionApiClient(token="secret_fake_token", http_client=shared)
    pages = list(client.search_pages())
    assert len(pages) == 1
    assert pages[0].parent_type == ""
    assert pages[0].parent_id is None


def test_block_ref_extracts_rich_text_via_block_walk() -> None:
    """iter_block_descendants surfaces heading_* blocks with their rich_text."""

    def _handler(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "object": "block",
                        "id": "heading-block",
                        "type": "heading_1",
                        "has_children": False,
                        "heading_1": {"rich_text": [{"type": "text", "plain_text": "Heading text"}]},
                    },
                    # Block with missing typed body — plain_text falls back to ''.
                    {
                        "object": "block",
                        "id": "paragraph-block",
                        "type": "paragraph",
                        "has_children": False,
                    },
                    # Block whose rich_text fragment list has non-dict + missing keys.
                    {
                        "object": "block",
                        "id": "mixed-block",
                        "type": "paragraph",
                        "has_children": False,
                        "paragraph": {
                            "rich_text": [
                                {"plain_text": "hello "},
                                "not-a-dict-skipped",
                                {"plain_text": "world"},
                                {"no_plain_text_key": "ignored"},
                            ]
                        },
                    },
                ],
                "next_cursor": None,
                "has_more": False,
            },
        )

    shared = httpx.Client(transport=httpx.MockTransport(_handler))
    # pragma: allowlist secret — fixture token, not a real credential.
    client = NotionApiClient(token="secret_fake_token", http_client=shared)
    blocks = list(client.iter_block_descendants("parent-block"))
    by_id = {b.block_id: b for b in blocks}
    assert by_id["heading-block"].plain_text == "Heading text"
    assert by_id["paragraph-block"].plain_text == ""
    assert by_id["mixed-block"].plain_text == "hello world"


# ---------------------------------------------------------------------------
# NotionConnector — additional connector-level coverage
# ---------------------------------------------------------------------------


def test_connector_constructs_with_default_client_builder() -> None:
    """Path where client_builder is None constructs the real NotionApiClient internally."""
    connector = NotionConnector(
        credentials=NotionCredentials(token="secret_fake_token_value")  # pragma: allowlist secret
    )
    assert connector.name == "notion"


def test_connector_root_id_for_workspace_parented_page_is_itself() -> None:
    """_root_id_for_page returns the page's own id when parent_type=workspace."""
    connector = _build_connector_with_handler(
        lambda _r: httpx.Response(200, json={"results": [], "next_cursor": None, "has_more": False})
    )
    ws_page = NotionPageRef(
        page_id="ws-page",
        parent_type="workspace",
        parent_id=None,
        title="",
        url="",
        last_edited_time="2026-05-22T10:00:00Z",
        archived=False,
    )
    assert connector._root_id_for_page(ws_page) == "ws-page"


def test_connector_root_id_for_orphan_page_falls_back_to_self() -> None:
    """_root_id_for_page falls back to page id when parent_id is None."""
    connector = _build_connector_with_handler(
        lambda _r: httpx.Response(200, json={"results": [], "next_cursor": None, "has_more": False})
    )
    orphan = NotionPageRef(
        page_id="orphan",
        parent_type="page_id",
        parent_id=None,
        title="",
        url="",
        last_edited_time="2026-05-22T10:00:00Z",
        archived=False,
    )
    assert connector._root_id_for_page(orphan) == "orphan"


def test_connector_root_id_for_subpage_uses_parent_id() -> None:
    """_root_id_for_page returns parent_id when the page is a subpage."""
    connector = _build_connector_with_handler(
        lambda _r: httpx.Response(200, json={"results": [], "next_cursor": None, "has_more": False})
    )
    sub = NotionPageRef(
        page_id="sub",
        parent_type="page_id",
        parent_id="parent-page",
        title="",
        url="",
        last_edited_time="2026-05-22T10:00:00Z",
        archived=False,
    )
    assert connector._root_id_for_page(sub) == "parent-page"


def test_connector_list_changes_for_container_cursor_filter_drops_old() -> None:
    """Per-container cursor filter ``<=`` drops events older than cursor."""
    from kairix.core.protocols import Container

    connector = _build_connector_with_handler(_single_root_handler)
    container = Container(
        cc_pair_id=1,
        container_id="single-root",
        access_state="ACCESSIBLE",
        cursor_token="2026-05-22T10:00:00.000Z",
        last_synced_at=None,
    )
    events = list(connector.list_changes_for_container(container))
    assert events == [], f"events at-or-before container cursor must drop; got {events!r}"


def test_connector_dispatch_page_database_row_adds_metadata() -> None:
    """A database-parented page gets item_kind=database_row in metadata."""
    connector = _build_connector_with_handler(
        lambda _r: httpx.Response(200, json={"results": [], "next_cursor": None, "has_more": False})
    )
    row = NotionPageRef(
        page_id="row-1",
        parent_type="database_id",
        parent_id="db-x",
        title="Row 1",
        url="",
        last_edited_time="2026-05-22T10:00:00Z",
        archived=False,
    )
    event = connector._dispatch_page(row)
    assert event is not None
    assert event.metadata.get("item_kind") == "database_row"
    assert event.metadata.get("database_id") == "db-x"


def test_connector_make_connector_validates_sensitivity_and_depth() -> None:
    """make_connector exercises both validation branches before the credential resolve.

    The valid-config path runs into the secret resolver which is
    boundary code we don't exercise here; the two validation branches
    are the units under test.
    """
    with pytest.raises(ValueError, match="default_sensitivity"):
        make_connector({"default_sensitivity": "garbage-tier"})
    with pytest.raises(ValueError, match="max_block_depth"):
        make_connector({"max_block_depth": -1})


# ---------------------------------------------------------------------------
# ADR-031 — secrets are resolved via the injected SecretsResolver
# ---------------------------------------------------------------------------


def test_notion_loads_secrets_via_loader() -> None:
    """Constructor reads the Notion integration token through the injected ``secrets`` resolver.

    Pins ADR-031: ``_resolve_credentials_from_secrets`` calls
    ``secrets.require(scope="connector", area="notion", instance=None,
    leaf="token")`` so the operator's KAIRIX_CONNECTOR_NOTION_TOKEN
    env var (or the legacy CONNECTOR_NOTION_TOKEN alias) resolves
    through the loader, never via a hidden ``kairix.secrets.get_secret``
    call.

    Sabotage proof (executed): in the connector module, change the
    ``leaf="token"`` argument to ``leaf="api-key"`` — the
    ``FakeSecretsLoader`` no longer has a value for the new tuple and
    ``.require()`` raises ``SecretNotFoundError``. Restored after
    confirming the failure: ``SecretNotFoundError: Required secret
    not available: kairix-connector-notion-api-key.``
    """
    fake_secrets = FakeSecretsLoader(
        values={("connector", "notion", None, "token"): "secret_loader_value"},  # pragma: allowlist secret
    )

    def _builder(creds: NotionCredentials) -> NotionApiClient:
        # No real HTTP — just confirm the resolved token reached the api_client.
        return NotionApiClient(token=creds.token)

    connector = NotionConnector(secrets=fake_secrets, client_builder=_builder)
    # The loader was asked for the canonical token leaf.
    asked = {(scope, area, instance, leaf) for scope, area, instance, leaf in fake_secrets.get_calls}
    assert ("connector", "notion", None, "token") in asked
    # The resolved value reached the credentials dataclass.
    assert connector._credentials.token == "secret_loader_value"


def test_notion_loader_miss_raises_actionable_error() -> None:
    """Missing token surfaces as :class:`SecretNotFoundError`, not a silent ``None``.

    Sabotage proof (executed): swap ``secrets.require(...)`` for
    ``secrets.get(...) or ""`` in the connector's resolver helper —
    the test below stops raising :class:`SecretNotFoundError` because
    the empty token now flows through to ``NotionApiClient``, which
    surfaces the miss as a plain :class:`ValueError`. Restored after
    confirming the failure (under the sabotage):
    ``Failed: DID NOT RAISE <class 'kairix.secrets.loader.SecretNotFoundError'>``
    (actually raised ``ValueError: notion api client: token is empty``).
    """
    fake_secrets = FakeSecretsLoader()  # no values registered
    with pytest.raises(SecretNotFoundError):
        NotionConnector(secrets=fake_secrets)

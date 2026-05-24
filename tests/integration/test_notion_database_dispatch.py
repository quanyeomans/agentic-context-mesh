"""Integration test for Notion database vs page differentiation.

Sabotage proof #4 (per spec §5 + the spec's brief): when a Notion
page's ``parent.type == "database_id"``, the page is a database row;
its ChangeEvent must carry ``metadata["item_kind"] == "database_row"``
so downstream Silver / chunker code can route it through the per-row
chunking path. Breaking the dispatch (treating database rows as plain
pages) drops the metadata tag and causes this test to fail.

The connector's :meth:`_dispatch_page` helper is the single dispatch
site. Replacing it with a passthrough to :meth:`_page_to_event` causes
the assertion below to fail because the database-row metadata is
never added.

F47: this integration test pins a connector-Protocol invariant, so it
constructs the connector directly. The wider pipeline composition
tests go through factories.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from kairix.connectors.notion import (
    NotionApiClient,
    NotionConnector,
    NotionCredentials,
)
from kairix.core.protocols import Container

pytestmark = pytest.mark.integration

_ROOT_ID = "root-with-database"
_DATABASE_ID = "db-inside-root"
_ROW_ID = "page-database-row-001"


def _mixed_pages_payload() -> dict[str, Any]:
    """One workspace-parented page (the root) + one database-parented page (the row)."""
    return {
        "results": [
            {
                "object": "page",
                "id": _ROOT_ID,
                "url": f"https://www.notion.so/your-team/{_ROOT_ID}",
                "last_edited_time": "2026-05-22T10:00:00.000Z",
                "archived": False,
                "parent": {"type": "workspace", "workspace": True},
                "properties": {
                    "title": {
                        "type": "title",
                        "title": [{"type": "text", "plain_text": "Container root"}],
                    }
                },
            },
            {
                "object": "page",
                "id": _ROW_ID,
                "url": f"https://www.notion.so/your-team/{_ROW_ID}",
                "last_edited_time": "2026-05-22T11:00:00.000Z",
                "archived": False,
                "parent": {"type": "database_id", "database_id": _DATABASE_ID},
                "properties": {
                    "title": {
                        "type": "title",
                        "title": [{"type": "text", "plain_text": "Row entry one"}],
                    }
                },
            },
        ],
        "next_cursor": None,
        "has_more": False,
    }


def _build_connector() -> NotionConnector:
    def _stub(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_mixed_pages_payload())

    shared = httpx.Client(transport=httpx.MockTransport(_stub))
    return NotionConnector(
        credentials=NotionCredentials(token="secret_fake_token_value"),  # pragma: allowlist secret — test fixture
        client_builder=lambda creds: NotionApiClient(token=creds.token, http_client=shared),
    )


def test_database_rows_dispatch_through_database_path() -> None:
    """A database-parented page carries the database-row metadata tag.

    Sabotage proof #4: bypassing the dispatch (treating database rows
    as plain pages) drops the ``item_kind`` + ``database_id`` keys and
    breaks this assertion.
    """
    connector = _build_connector()
    # Use the container surface so the dispatch path runs (it's only
    # invoked on the per-container code path).
    container = Container(
        cc_pair_id=1,
        container_id=_ROOT_ID,
        access_state="ACCESSIBLE",
        cursor_token=None,
        last_synced_at=None,
    )
    events = list(connector.list_changes_for_container(container))
    # The root page itself maps to the container; the row's parent is
    # the database, so its root resolution falls back to the database
    # id (different from _ROOT_ID), so it filters out of this drain.
    # Re-drain with a Container scoped to the row's database id so we
    # capture the row event.
    container_for_db = Container(
        cc_pair_id=1,
        container_id=_DATABASE_ID,
        access_state="ACCESSIBLE",
        cursor_token=None,
        last_synced_at=None,
    )
    row_events = list(connector.list_changes_for_container(container_for_db))
    assert row_events, "expected the database row to surface as a ChangeEvent"
    row_event = next((ev for ev in row_events if ev.item_id == _ROW_ID), None)
    assert row_event is not None, f"expected event for the database row id; got {row_events!r}"
    # The dispatch must have added the database-row metadata.
    assert row_event.metadata.get("item_kind") == "database_row", (
        f"database-row dispatch must tag item_kind; got metadata={row_event.metadata!r}. "
        f"Sabotage hint: check _dispatch_page in NotionConnector."
    )
    assert row_event.metadata.get("database_id") == _DATABASE_ID, (
        f"database-row dispatch must carry database_id; got metadata={row_event.metadata!r}"
    )
    # And the container drain above (scoped to the root) should NOT
    # have lifted the database row — separation of concerns.
    assert all(ev.item_id != _ROW_ID for ev in events), (
        f"root-container drain must not include database row; got events={events!r}"
    )

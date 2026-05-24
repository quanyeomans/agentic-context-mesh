"""Step definitions for connector_notion.feature.

Drives the real :class:`kairix.connectors.notion.NotionConnector`
against an :class:`httpx.MockTransport`-backed Notion REST stub. No
real network call — the stub returns one page envelope so the
behaviour assertions can pin the typed ChangeEvent shape and the
per-container cursor encoding.

Per F46, this step file reaches the connector through the real
constructor + the production :class:`NotionApiClient` helper (depth
≤ 2). Direct construction is permitted in BDD step files when the
target is a Protocol-compliant leaf such as ``NotionConnector``.

F1-clean: no @patch / kairix module-attribute substitution.
F2-clean: no KAIRIX_* env-var manipulation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import httpx
import pytest
from pytest_bdd import given, parsers, then, when

from kairix.connectors.notion import (
    NotionApiClient,
    NotionConnector,
    NotionCredentials,
)
from kairix.core.protocols import ChangeEvent

pytestmark = pytest.mark.bdd

_PAGE_ID = "page-fixture-0001"
_PAGE_TITLE = "Engagement brief - agent-alpha"
_PAGE_URL = "https://www.notion.so/your-team/page-fixture-0001"
_LAST_EDITED = "2026-05-22T10:00:00.000Z"


def _one_visible_page_response() -> dict[str, Any]:
    """One Notion search response with a single visible page envelope."""
    return {
        "results": [
            {
                "object": "page",
                "id": _PAGE_ID,
                "url": _PAGE_URL,
                "last_edited_time": _LAST_EDITED,
                "archived": False,
                "parent": {"type": "workspace", "workspace": True},
                "properties": {
                    "title": {
                        "type": "title",
                        "title": [{"type": "text", "plain_text": _PAGE_TITLE}],
                    }
                },
            }
        ],
        "next_cursor": None,
        "has_more": False,
    }


@dataclass
class _Ctx:
    """Per-scenario context — no module-level mutable state."""

    requested_urls: list[str] = field(default_factory=list)
    connector: NotionConnector | None = None
    events: list[ChangeEvent] = field(default_factory=list)


@pytest.fixture
def notion_ctx() -> _Ctx:
    return _Ctx()


def _build_connector_with_stubbed_api(ctx: _Ctx) -> NotionConnector:
    """Construct the real connector wired to a recording stub Notion endpoint."""

    def _stub_handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        ctx.requested_urls.append(url)
        if "/search" in url:
            return httpx.Response(200, json=_one_visible_page_response())
        if "/blocks/" in url and "/children" in url:
            return httpx.Response(200, json={"results": [], "next_cursor": None, "has_more": False})
        return httpx.Response(200, json={"results": [], "next_cursor": None, "has_more": False})

    transport = httpx.MockTransport(_stub_handler)
    shared_client = httpx.Client(transport=transport)

    def _client_builder(creds: NotionCredentials) -> NotionApiClient:
        return NotionApiClient(token=creds.token, http_client=shared_client)

    return NotionConnector(
        credentials=NotionCredentials(token="secret_fake_token_value"),  # pragma: allowlist secret — test fixture
        client_builder=_client_builder,
    )


# ---------------------------------------------------------------------------
# Givens
# ---------------------------------------------------------------------------


@given(parsers.parse("a stubbed Notion REST endpoint that returns one visible page envelope"))
def _given_one_page(notion_ctx: _Ctx) -> None:
    notion_ctx.connector = _build_connector_with_stubbed_api(notion_ctx)


# ---------------------------------------------------------------------------
# Whens
# ---------------------------------------------------------------------------


@when(parsers.parse("the operator runs the notion connector list_changes with no cursor"))
def _when_list_changes(notion_ctx: _Ctx) -> None:
    assert notion_ctx.connector is not None, "Given step must run before When"
    notion_ctx.events = list(notion_ctx.connector.list_changes(cursor=None))


# ---------------------------------------------------------------------------
# Thens
# ---------------------------------------------------------------------------


@then("one notion modified change event is emitted")
def _one_modified_event(notion_ctx: _Ctx) -> None:
    events = notion_ctx.events
    assert len(events) == 1, f"expected 1 event, got {len(events)}: {events!r}"
    assert events[0].op == "modified", f"expected modified op, got {events[0]!r}"


@then("the notion change event carries an ISO-8601 modified_at timestamp")
def _event_has_iso(notion_ctx: _Ctx) -> None:
    event = notion_ctx.events[0]
    assert event.modified_at, f"event {event!r} missing modified_at"
    assert event.modified_at.endswith("Z") or "+" in event.modified_at, (
        f"event {event!r} modified_at not ISO-8601: {event.modified_at!r}"
    )


@then("the notion change event's sensitivity tier is internal")
def _event_internal_tier(notion_ctx: _Ctx) -> None:
    event = notion_ctx.events[0]
    tier = event.metadata.get("sensitivity")
    assert tier == "internal", f"event {event.item_id!r} sensitivity is not internal: {tier!r}"


@then("the notion change event metadata records the source parent type")
def _event_records_parent_type(notion_ctx: _Ctx) -> None:
    event = notion_ctx.events[0]
    assert event.metadata.get("parent_type") == "workspace", (
        f"event {event.item_id!r} parent_type metadata is wrong: {event.metadata.get('parent_type')!r}"
    )


@then("the notion connector exposes a non-empty next cursor")
def _connector_has_cursor(notion_ctx: _Ctx) -> None:
    assert notion_ctx.connector is not None
    cursor = notion_ctx.connector.next_cursor()
    assert cursor, f"expected non-empty next cursor, got {cursor!r}"


@then("the notion next cursor matches the highest last_edited_time seen")
def _cursor_matches_high_water(notion_ctx: _Ctx) -> None:
    assert notion_ctx.connector is not None
    cursor = notion_ctx.connector.next_cursor()
    assert cursor == _LAST_EDITED, f"cursor must equal the page's last_edited_time; got {cursor!r}"

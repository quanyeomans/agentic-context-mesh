"""Integration test for Notion Wave E per-container cursor isolation.

Sabotage proof #1 (per spec §5 + spec brief): when the connector emits
multiple Containers (one per top-level Notion root), each Container
must carry its own cursor token. Collapsing the cursor into a single
shared variable would cause the two Containers' cursors to overwrite
each other after independent drains.

This test simulates two Containers backed by two distinct visible
roots. After running :meth:`list_changes_for_container` against each,
the per-container cursor accessor must return the correct
high-water-mark for each Container independently.

Sabotage walk: replacing ``self._next_cursor_by_container[...] =
latest_edited`` with ``self._next_cursor = latest_edited`` in
:meth:`NotionConnector.list_changes_for_container` causes both
:meth:`next_cursor_for_container` calls to return ``None`` (the per-
container dict is never populated) — the assertion fails. Restoring
the per-container write returns the test to green.

F47: this multi-component integration test still constructs the
connector directly because it pins a connector-internal invariant.
The wider pipeline composition tests go through factories per F47;
this file's scope is the connector-Protocol boundary itself.
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

_ROOT_ALPHA = "root-alpha"
_ROOT_BRAVO = "root-bravo"
_LAST_EDITED_ALPHA = "2026-05-22T10:00:00.000Z"
_LAST_EDITED_BRAVO = "2026-05-22T15:00:00.000Z"


def _two_root_search_payload() -> dict[str, Any]:
    """Two distinct root pages visible to the integration."""
    return {
        "results": [
            {
                "object": "page",
                "id": _ROOT_ALPHA,
                "url": f"https://www.notion.so/your-team/{_ROOT_ALPHA}",
                "last_edited_time": _LAST_EDITED_ALPHA,
                "archived": False,
                "parent": {"type": "workspace", "workspace": True},
                "properties": {
                    "title": {
                        "type": "title",
                        "title": [{"type": "text", "plain_text": "Alpha root"}],
                    }
                },
            },
            {
                "object": "page",
                "id": _ROOT_BRAVO,
                "url": f"https://www.notion.so/your-team/{_ROOT_BRAVO}",
                "last_edited_time": _LAST_EDITED_BRAVO,
                "archived": False,
                "parent": {"type": "workspace", "workspace": True},
                "properties": {
                    "title": {
                        "type": "title",
                        "title": [{"type": "text", "plain_text": "Bravo root"}],
                    }
                },
            },
        ],
        "next_cursor": None,
        "has_more": False,
    }


def _build_connector() -> NotionConnector:
    def _stub(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_two_root_search_payload())

    shared = httpx.Client(transport=httpx.MockTransport(_stub))
    return NotionConnector(
        credentials=NotionCredentials(token="secret_fake_token_value"),  # pragma: allowlist secret — test fixture
        client_builder=lambda creds: NotionApiClient(token=creds.token, http_client=shared),
    )


def test_per_container_cursors_are_isolated() -> None:
    """Each Container's cursor must round-trip independently.

    Sabotage proof #1: collapsing the per-container cursor dict into a
    single shared cursor variable breaks both assertions because the
    second drain's high-water-mark overwrites the first.
    """
    connector = _build_connector()
    containers = list(connector.iter_containers(cc_pair_id=1))
    assert len(containers) == 2, f"expected 2 Containers (one per root), got {len(containers)}"
    container_alpha = next(c for c in containers if c.container_id == _ROOT_ALPHA)
    container_bravo = next(c for c in containers if c.container_id == _ROOT_BRAVO)

    # Drain each Container independently.
    events_alpha = list(connector.list_changes_for_container(container_alpha))
    events_bravo = list(connector.list_changes_for_container(container_bravo))

    # Each Container's page surfaces as one event.
    assert len(events_alpha) == 1, f"alpha container should yield 1 event, got {events_alpha!r}"
    assert events_alpha[0].item_id == _ROOT_ALPHA
    assert len(events_bravo) == 1, f"bravo container should yield 1 event, got {events_bravo!r}"
    assert events_bravo[0].item_id == _ROOT_BRAVO

    # Per-container cursors must round-trip independently — this is
    # the sabotage proof. Collapsing into a shared cursor variable
    # breaks one of these two assertions.
    cursor_alpha = connector.next_cursor_for_container(_ROOT_ALPHA)
    cursor_bravo = connector.next_cursor_for_container(_ROOT_BRAVO)
    assert cursor_alpha == _LAST_EDITED_ALPHA, (
        f"alpha cursor must equal its high-water-mark; got {cursor_alpha!r}, expected {_LAST_EDITED_ALPHA!r}"
    )
    assert cursor_bravo == _LAST_EDITED_BRAVO, (
        f"bravo cursor must equal its high-water-mark; got {cursor_bravo!r}, expected {_LAST_EDITED_BRAVO!r}"
    )
    assert cursor_alpha != cursor_bravo, (
        "per-container cursors must NOT collapse into the same value — that's the sabotage failure mode"
    )


def test_container_cursor_filters_old_events() -> None:
    """When the Container's cursor_token is set, older events drop.

    Pins the high-water-mark contract: events whose modified_at is
    ``<=`` the cursor are filtered out.
    """
    connector = _build_connector()
    # Build a Container whose cursor token is exactly the alpha
    # page's last_edited_time — the connector should drop the event
    # because the filter is strict `<=`.
    container = Container(
        cc_pair_id=1,
        container_id=_ROOT_ALPHA,
        access_state="ACCESSIBLE",
        cursor_token=_LAST_EDITED_ALPHA,
        last_synced_at=None,
    )
    events = list(connector.list_changes_for_container(container))
    assert events == [], f"expected zero events when cursor matches last_edited_time, got {events!r}"

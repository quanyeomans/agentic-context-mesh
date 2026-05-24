"""F58 contract test for the Notion Wave E ``HierarchyConnector`` impl.

Pins the parent-before-child invariant on the real
:class:`kairix.connectors.notion.NotionConnector`. The connector emits
the workspace root (``raw_parent_id=None``), then one PAGE node per
top-level visible page (``raw_parent_id="notion"``), then one PAGE
node per visible database (``raw_parent_id`` = parent page or
``"notion"`` for top-level databases). Every non-root emission must
follow its parent within the same ``load_hierarchy(cc_pair_id)``
call.

F58 (``scripts/checks/check_f58_hierarchy_parent_before_child.py``)
requires at least one test under ``tests/contracts/`` whose function
name matches ``test_*hierarchy*parent_before_child*`` AND references
``HierarchyConnector``; this file is the Notion-specific F58 pin
shipped alongside the dex_crm + obsidian siblings.

Sabotage proof #2 (executed by the agent, restored on completion):
swapping the yield order in ``NotionConnector.load_hierarchy`` so a
database emits before its parent root page makes
``test_notion_hierarchy_parent_before_child`` fail with the
orphan-emission assertion. Restored on completion.
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
from kairix.core.protocols import HierarchyConnector

pytestmark = pytest.mark.contract

_ROOT_ID_ALPHA = "page-root-alpha"
_ROOT_ID_BRAVO = "page-root-bravo"
_DATABASE_ID = "db-inside-alpha"


def _search_pages_payload() -> dict[str, Any]:
    return {
        "results": [
            {
                "object": "page",
                "id": _ROOT_ID_ALPHA,
                "url": f"https://www.notion.so/your-team/{_ROOT_ID_ALPHA}",
                "last_edited_time": "2026-05-22T10:00:00.000Z",
                "archived": False,
                "parent": {"type": "workspace", "workspace": True},
                "properties": {
                    "title": {
                        "type": "title",
                        "title": [{"type": "text", "plain_text": "Root alpha"}],
                    }
                },
            },
            {
                "object": "page",
                "id": _ROOT_ID_BRAVO,
                "url": f"https://www.notion.so/your-team/{_ROOT_ID_BRAVO}",
                "last_edited_time": "2026-05-22T11:00:00.000Z",
                "archived": False,
                "parent": {"type": "workspace", "workspace": True},
                "properties": {
                    "title": {
                        "type": "title",
                        "title": [{"type": "text", "plain_text": "Root bravo"}],
                    }
                },
            },
        ],
        "next_cursor": None,
        "has_more": False,
    }


def _search_databases_payload() -> dict[str, Any]:
    return {
        "results": [
            {
                "object": "database",
                "id": _DATABASE_ID,
                "url": f"https://www.notion.so/your-team/{_DATABASE_ID}",
                "last_edited_time": "2026-05-22T12:00:00.000Z",
                "parent": {"type": "page_id", "page_id": _ROOT_ID_ALPHA},
                "title": [{"type": "text", "plain_text": "DB inside alpha"}],
            },
        ],
        "next_cursor": None,
        "has_more": False,
    }


def _build_real_connector() -> NotionConnector:
    """Construct the real connector against a MockTransport Notion stub."""

    def _stub(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "/search" not in url:
            return httpx.Response(200, json={"results": [], "next_cursor": None, "has_more": False})
        # The connector's load_hierarchy calls search_pages() AND
        # search_databases(); both POST to /search with different
        # filter bodies. We can dispatch via the request body.
        try:
            body = request.read().decode("utf-8")
        except (httpx.RequestNotRead, RuntimeError):  # pragma: no cover - defensive
            body = ""
        if "database" in body:
            return httpx.Response(200, json=_search_databases_payload())
        return httpx.Response(200, json=_search_pages_payload())

    shared = httpx.Client(transport=httpx.MockTransport(_stub))
    return NotionConnector(
        credentials=NotionCredentials(token="secret_fake_token_value"),  # pragma: allowlist secret — test fixture
        client_builder=lambda creds: NotionApiClient(token=creds.token, http_client=shared),
    )


@pytest.mark.contract
def test_notion_hierarchy_parent_before_child() -> None:
    """Notion's Wave E HierarchyConnector emits nodes parent-before-child.

    Pins the F58 invariant on the real walk — workspace root, then
    two top-level pages, then one database parented to the alpha
    page. Every non-root emission's ``raw_parent_id`` must reference
    an already-emitted ``raw_node_id``.

    Sabotage proof #2: swapping the order of the three loops in
    :meth:`NotionConnector.load_hierarchy` (e.g. databases before
    roots) causes the assertion below to fail because the database
    references ``_ROOT_ID_ALPHA`` which is no longer emitted yet.
    """
    connector = _build_real_connector()
    assert isinstance(connector, HierarchyConnector)
    nodes = list(connector.load_hierarchy(cc_pair_id=1))
    # Expect: 1 workspace root + 2 top-level pages + 1 database = 4
    assert len(nodes) == 4, f"expected 4 nodes (root + 2 pages + 1 db), got {len(nodes)}: {nodes!r}"
    seen: set[str] = set()
    for node in nodes:
        if node.raw_parent_id is not None:
            assert node.raw_parent_id in seen, (
                f"orphan emission: {node.raw_node_id} references parent {node.raw_parent_id!r} not yet emitted; "
                f"seen so far={seen!r}"
            )
        seen.add(node.raw_node_id)
    # Spot check: the database parents to the alpha page, not to the workspace root.
    db_node = next(n for n in nodes if n.raw_node_id == _DATABASE_ID)
    assert db_node.raw_parent_id == _ROOT_ID_ALPHA, (
        f"database should parent to its containing page; got raw_parent_id={db_node.raw_parent_id!r}"
    )

"""Integration test for Notion block-fetching pagination correctness.

Sabotage proof #3 (per spec §5 + the spec's brief): the
``GET /v1/blocks/{id}/children`` endpoint paginates via
``next_cursor`` / ``has_more``. The client's :meth:`_iter_one_level`
helper advances ``start_cursor`` between pages — breaking that cursor
advance drops every block past the first page.

This test seeds a two-page block-children response and asserts the
client yields blocks from BOTH pages. Breaking the cursor advance in
:meth:`NotionApiClient._iter_one_level` causes the test to fail
because only the first page's blocks come through.

The depth cap is also pinned: a block tree deeper than the cap stops
descending so a synced-block loop can't pin the worker.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from kairix.connectors.notion import NotionApiClient

pytestmark = pytest.mark.integration

_PARENT_BLOCK = "parent-block-001"
_PAGE_1_CURSOR = "cursor-after-page-1"


def _stub_two_page_children() -> Any:
    """Two-page block-children response with page_2 unlocked only via cursor."""
    call_log: list[str] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        call_log.append(url)
        # Decide which page to return based on the start_cursor query param.
        if "start_cursor" in url and _PAGE_1_CURSOR in url:
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "object": "block",
                            "id": "block-page-2-alpha",
                            "type": "paragraph",
                            "has_children": False,
                            "paragraph": {"rich_text": [{"type": "text", "plain_text": "page 2 alpha"}]},
                        }
                    ],
                    "next_cursor": None,
                    "has_more": False,
                },
            )
        # First page — yields one block + a next_cursor pointer.
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "object": "block",
                        "id": "block-page-1-alpha",
                        "type": "paragraph",
                        "has_children": False,
                        "paragraph": {"rich_text": [{"type": "text", "plain_text": "page 1 alpha"}]},
                    }
                ],
                "next_cursor": _PAGE_1_CURSOR,
                "has_more": True,
            },
        )

    return _handler, call_log


def test_block_pagination_advances_through_multi_page_response() -> None:
    """Multi-page block-children responses must yield blocks from every page.

    Sabotage proof #3: breaking the cursor advance in
    :meth:`NotionApiClient._iter_one_level` causes the second-page
    block to never be fetched, dropping the assertion below.
    """
    handler, call_log = _stub_two_page_children()
    shared = httpx.Client(transport=httpx.MockTransport(handler))
    # pragma: allowlist secret — fixture token; not a real credential.
    client = NotionApiClient(token="secret_fake_token_value", http_client=shared)

    blocks = list(client.iter_block_descendants(_PARENT_BLOCK))

    block_ids = {b.block_id for b in blocks}
    assert "block-page-1-alpha" in block_ids, f"missing page-1 block; got {block_ids!r}"
    assert "block-page-2-alpha" in block_ids, (
        f"missing page-2 block — cursor advance must have broken; got {block_ids!r}. "
        f"Sabotage hint: check _iter_one_level cursor advance in api_client.py."
    )
    # Confirm both pages were actually requested.
    assert len(call_log) >= 2, f"expected at least two children-API calls, got {call_log!r}"
    assert any(_PAGE_1_CURSOR in url for url in call_log), (
        f"second-page call must carry the start_cursor param; got {call_log!r}"
    )


def test_block_depth_cap_bounds_recursion() -> None:
    """Block-tree depth cap prevents unbounded recursion per spec §5.

    A child block with ``has_children=True`` past the depth cap must
    not trigger another fetch — bounded rate-limit consumption.
    """
    nested_call_count = {"n": 0}

    def _handler(request: httpx.Request) -> httpx.Response:
        nested_call_count["n"] += 1
        # Every level returns one block claiming has_children=True so a
        # buggy walker would recurse forever.
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "object": "block",
                        "id": f"nested-block-{nested_call_count['n']}",
                        "type": "paragraph",
                        "has_children": True,
                        "paragraph": {"rich_text": [{"type": "text", "plain_text": "nested"}]},
                    }
                ],
                "next_cursor": None,
                "has_more": False,
            },
        )

    shared = httpx.Client(transport=httpx.MockTransport(_handler))
    client = NotionApiClient(
        token="secret_fake_token_value",  # pragma: allowlist secret — test fixture
        http_client=shared,
        max_block_depth=3,
    )

    list(client.iter_block_descendants("root-block"))

    # With max_block_depth=3, the walker fetches the root level + 2
    # nested levels (depth 0, 1, 2). One call per level seeded by the
    # depth-cap arithmetic.
    assert nested_call_count["n"] == 3, (
        f"depth cap must bound recursion at max_block_depth=3, got {nested_call_count['n']} calls. "
        f"Sabotage hint: check the depth comparison in iter_block_descendants."
    )

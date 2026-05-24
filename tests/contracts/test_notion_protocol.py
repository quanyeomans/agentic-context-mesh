"""Contract test for the Notion connector plugin (F43).

Exercises the canonical fake
(:class:`tests.fakes.FakeNotionConnector`) AND the real
implementation
(:class:`kairix.connectors.notion.NotionConnector`) through
the same :class:`~kairix.core.protocols.SourceConnector` Protocol
assertions. F43 requires this pairing — without it the fake can
drift from the real wire (or vice versa) and the production path
silently diverges from what BDD / unit tests measure.

Real-impl path is driven against an :class:`httpx.MockTransport`-backed
Notion REST stub; no real network call is ever made.

Sabotage proofs (per docs/architecture/connector-scope-topology/connector-design-specs/notion.md §5):

  * Removing ``list_changes`` from :class:`NotionConnector` flips
    ``test_connector_satisfies_source_connector_protocol`` (real branch)
    to False.
  * Replacing the connector's ``fetch`` return shape with a plain
    ``bytes`` value (skipping the :class:`RawArtefact` wrapper) breaks
    ``test_connector_fetch_returns_markdown_artefact``.
  * Mutating :data:`DEFAULT_SENSITIVITY` to ``"public"`` flips
    ``test_connector_default_sensitivity_is_internal``.

Hierarchy + parent-before-child + database-vs-page contracts have
dedicated tests in :mod:`tests.contracts.test_notion_hierarchy_parent_before_child`
and :mod:`tests.contracts.test_notion_database_dispatch`.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx
import pytest

from kairix.connectors.notion import (
    DEFAULT_SENSITIVITY,
    NotionApiClient,
    NotionConnector,
    NotionCredentials,
)
from kairix.core.protocols import ChangeEvent, RawArtefact, SourceConnector
from tests.fakes import FakeNotionConnector

pytestmark = pytest.mark.contract

_PAGE_ID_ALPHA = "page-alpha-contract"
_PAGE_ID_BRAVO = "page-bravo-contract"


def _envelope_pages() -> list[dict[str, Any]]:
    """Two seeded page envelopes that round-trip through both branches."""
    return [
        {
            "id": _PAGE_ID_ALPHA,
            "title": "Engagement scope phase 2",
            "url": "https://www.notion.so/your-team/page-alpha-contract",
            "last_edited_time": "2026-05-22T10:00:00.000Z",
            "parent_type": "workspace",
            "archived": False,
            "body_markdown": "# Engagement scope phase 2\n\nProject body content here.",
        },
        {
            "id": _PAGE_ID_BRAVO,
            "title": "Risk register",
            "url": "https://www.notion.so/your-team/page-bravo-contract",
            "last_edited_time": "2026-05-22T11:00:00.000Z",
            "parent_type": "workspace",
            "archived": False,
            "body_markdown": "# Risk register\n\nLine one of the register.",
        },
    ]


def _fake_factory() -> SourceConnector:
    """Canonical fake factory — seeds two page envelopes."""
    return FakeNotionConnector(pages=_envelope_pages())


def _search_response_payload() -> dict[str, Any]:
    return {
        "results": [
            {
                "object": "page",
                "id": entry["id"],
                "url": entry["url"],
                "last_edited_time": entry["last_edited_time"],
                "archived": entry["archived"],
                "parent": {"type": "workspace", "workspace": True},
                "properties": {
                    "title": {
                        "type": "title",
                        "title": [{"type": "text", "plain_text": entry["title"]}],
                    }
                },
            }
            for entry in _envelope_pages()
        ],
        "next_cursor": None,
        "has_more": False,
    }


def _blocks_response_payload(page_id: str) -> dict[str, Any]:
    for entry in _envelope_pages():
        if entry["id"] == page_id:
            text = entry["body_markdown"].split("\n\n", 1)[-1]
            return {
                "results": [
                    {
                        "object": "block",
                        "id": f"{page_id}-block-001",
                        "type": "paragraph",
                        "has_children": False,
                        "paragraph": {"rich_text": [{"type": "text", "plain_text": text}]},
                    }
                ],
                "next_cursor": None,
                "has_more": False,
            }
    return {"results": [], "next_cursor": None, "has_more": False}


def _real_factory() -> SourceConnector:
    """Real-impl factory — MockTransport-backed Notion REST stub."""

    def _stub(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "/search" in url:
            return httpx.Response(200, json=_search_response_payload())
        if "/blocks/" in url and "/children" in url:
            # extract page_id from URL path /blocks/{id}/children
            parts = url.split("/blocks/")[-1].split("/children")[0]
            return httpx.Response(200, json=_blocks_response_payload(parts))
        return httpx.Response(200, json={"results": [], "next_cursor": None, "has_more": False})

    shared = httpx.Client(transport=httpx.MockTransport(_stub))
    connector = NotionConnector(
        credentials=NotionCredentials(token="secret_fake_token_value"),  # pragma: allowlist secret — test fixture
        client_builder=lambda creds: NotionApiClient(token=creds.token, http_client=shared),
    )
    # Prime the envelope cache so fetch() works in the contract assertions
    # (same shape as the SharePoint contract test).
    list(connector.list_changes(cursor=None))
    return connector


_FACTORIES: list[tuple[str, Callable[[], SourceConnector]]] = [
    ("fake", _fake_factory),
    ("real", _real_factory),
]


@pytest.mark.contract
@pytest.mark.parametrize("name,factory", _FACTORIES)
def test_connector_satisfies_source_connector_protocol(name: str, factory: Callable[[], SourceConnector]) -> None:
    """F43: both fake and real impl satisfy the runtime-checkable Protocol."""
    connector = factory()
    assert isinstance(connector, SourceConnector), f"{name!r} factory output is not a SourceConnector"
    assert connector.name == "notion"


@pytest.mark.contract
@pytest.mark.parametrize("name,factory", _FACTORIES)
def test_connector_list_changes_returns_change_events(name: str, factory: Callable[[], SourceConnector]) -> None:
    """Both implementations stream :class:`ChangeEvent` instances."""
    connector = factory()
    events = list(connector.list_changes(cursor=None))
    assert events, f"{name!r} factory produced no events"
    for ev in events:
        assert isinstance(ev, ChangeEvent), f"{name!r} yielded a non-ChangeEvent: {ev!r}"
        assert ev.op in ("created", "modified", "archived", "deleted", "access_lost")


@pytest.mark.contract
@pytest.mark.parametrize("name,factory", _FACTORIES)
def test_connector_fetch_returns_markdown_artefact(name: str, factory: Callable[[], SourceConnector]) -> None:
    """Both implementations satisfy the ``fetch`` -> :class:`RawArtefact` shape."""
    connector = factory()
    artefact = connector.fetch(_PAGE_ID_ALPHA)
    assert isinstance(artefact, RawArtefact), f"{name!r} fetch did not return a RawArtefact: {artefact!r}"
    assert artefact.mime == "text/markdown", f"{name!r} fetch mime is wrong: {artefact.mime!r}"
    assert artefact.raw, f"{name!r} fetch raw bytes is empty"


@pytest.mark.contract
@pytest.mark.parametrize("name,factory", _FACTORIES)
def test_connector_source_link_round_trips_to_notion(name: str, factory: Callable[[], SourceConnector]) -> None:
    """``source_link`` returns a Notion URL on both implementations."""
    connector = factory()
    link = connector.source_link(_PAGE_ID_ALPHA)
    assert link, f"{name!r} produced empty source_link"
    assert link.startswith(("https://www.notion.so/", "notion://")), f"{name!r} unexpected link scheme: {link!r}"


@pytest.mark.contract
@pytest.mark.parametrize("name,factory", _FACTORIES)
def test_connector_default_sensitivity_is_internal(name: str, factory: Callable[[], SourceConnector]) -> None:
    """``sensitivity_for`` returns the documented default ``internal`` tier."""
    connector = factory()
    tier = connector.sensitivity_for(_PAGE_ID_ALPHA)
    assert tier == DEFAULT_SENSITIVITY == "internal", f"{name!r} returned unexpected sensitivity: {tier!r}"

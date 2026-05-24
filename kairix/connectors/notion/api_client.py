"""Thin Notion API client for the kairix Notion connector.

A focused wrapper around ``httpx.Client`` for the Notion API surface
the Wave E Notion connector exercises — workspace search (top-level
shared roots), page fetch (properties), block-children walk (block-tree
→ Markdown source), and database row enumeration. Three commitments:

  1. **Cursor pagination.** The Notion REST surface uses
     ``next_cursor`` / ``has_more`` for every list endpoint
     (``/v1/search``, ``/v1/databases/{id}/query``,
     ``/v1/blocks/{id}/children``). The client surfaces the pagination
     via the typed page value object so the connector can advance
     cursors without parsing the wire shape itself.

  2. **Bearer-token auth via the Notion integration token.** Every
     request adds an ``Authorization: Bearer <token>`` header plus the
     Notion-required ``Notion-Version`` header so requests pin to the
     same API version across deploys.

  3. **Block-tree depth cap.** Per the spec §5, synced-block and
     column-layout recursion can blow up rate-limit budget; the client
     enforces a configurable max depth on
     :meth:`iter_block_descendants` so the connector never traverses
     unbounded trees.

Per F37, ``notion_client`` imports are allowed only under
``kairix/connectors/notion/``. We deliberately avoid the official
``notion_client`` SDK (it pulls a heavy transitive set for what is
essentially a small REST surface) and use raw ``httpx``. The client
stays under F37's allowed surface (this module lives at
``kairix/connectors/notion/api_client.py``).
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any, Final

import httpx

logger = logging.getLogger(__name__)

# Default Notion REST base URL. Pinned to v1 — the surface is stable
# across the 2022/2023/2024 Notion-Version pivots; the version pin is
# carried per-request via the Notion-Version header below.
_DEFAULT_NOTION_BASE: Final[str] = "https://api.notion.com/v1"

# Per the Notion API contract, every request MUST carry a Notion-Version
# header. We pin to the 2022-06-28 stable revision used by the official
# SDK at the time this connector was authored — bumping is a deliberate
# operator action, not silent drift.
_DEFAULT_NOTION_VERSION: Final[str] = "2022-06-28"

# Per-request timeout. Notion list endpoints typically reply in <1s;
# 60s covers a cold connection on a paginated reply with a large
# database query.
_NOTION_REQUEST_TIMEOUT_S: Final[float] = 60.0

# Block-tree depth cap default. Spec §5 (Notion-specific failure mode):
# synced blocks + column layouts can recurse deeply; the cap bounds
# rate-limit budget consumption. Operator-tunable via the connector
# config; 8 covers normal page nesting comfortably and stops a runaway
# transclusion loop dead.
DEFAULT_MAX_BLOCK_DEPTH: Final[int] = 8

# Notion REST list-payload pagination keys — extracted once so renames
# have a single edit site and the F17 dup-literal gate stays green as
# the client grows (every list / iter method follows the same shape).
_PARENT_TYPE_DATABASE: Final[str] = "database_id"
_QUERY_PARAM_START_CURSOR: Final[str] = "start_cursor"
_RESPONSE_KEY_NEXT_CURSOR: Final[str] = "next_cursor"

# Parent-type → id-key dispatch. Notion's parent block carries the
# parent id under a key that mirrors the parent type — page_id parents
# carry page_id, database_id parents carry database_id, etc.; the
# workspace parent has no id (it's the workspace root itself). Shared
# between the page-ref and database-ref parsers (F17 dup-literal
# avoidance — extracted to one module-level constant).
_PARENT_ID_KEY_BY_TYPE: Final[dict[str, str | None]] = {
    "page_id": "page_id",
    _PARENT_TYPE_DATABASE: _PARENT_TYPE_DATABASE,
    "workspace": None,
    "block_id": "block_id",
}


@dataclass(frozen=True)
class NotionPageRef:
    """One Notion page envelope (search result or database row).

    Frozen per F42. ``parent_type`` distinguishes ``workspace`` /
    ``page_id`` / ``database_id`` parents so the connector can dispatch
    database rows vs free-standing pages correctly (sabotage proof #4).
    """

    page_id: str
    parent_type: str
    parent_id: str | None
    title: str
    url: str
    last_edited_time: str
    archived: bool


@dataclass(frozen=True)
class NotionDatabaseRef:
    """One Notion database envelope.

    Frozen per F42. Databases are page-type containers whose rows are
    themselves pages — the connector treats database queries as the
    row enumeration surface (spec §0 + §1).
    """

    database_id: str
    parent_type: str
    parent_id: str | None
    title: str
    url: str
    last_edited_time: str


@dataclass(frozen=True)
class NotionBlockRef:
    """One Notion block envelope.

    Frozen per F42. ``has_children`` drives the recursive walk in
    :meth:`NotionApiClient.iter_block_descendants`; the depth cap stops
    the walk before unbounded transclusion.
    """

    block_id: str
    block_type: str
    has_children: bool
    plain_text: str


@dataclass(frozen=True)
class NotionSearchPage:
    """One page of a ``POST /v1/search`` response.

    Frozen per F42. ``next_cursor`` is non-``None`` while more pages
    remain; ``None`` on the final page marks the cursor exhausted.
    """

    pages: tuple[NotionPageRef, ...]
    databases: tuple[NotionDatabaseRef, ...]
    next_cursor: str | None


class NotionApiClient:
    """Thin Notion REST wrapper for the kairix connector.

    Args:
        token: The Notion integration token (``secret_…``). Required.
            Never logged — F15 boundary discipline.
        notion_base: Optional override for the API base URL. Defaults
            to the public ``api.notion.com/v1`` endpoint.
        notion_version: Notion-Version header value sent on every
            request. Defaults to the stable 2022-06-28 revision.
        http_client: Optional ``httpx.Client`` for the request path.
            Tests pass an :class:`httpx.MockTransport`-backed client so
            no real Notion call leaks from the test suite.
        max_block_depth: Recursion cap for
            :meth:`iter_block_descendants`. Bounds rate-limit budget on
            pathological synced-block trees per spec §5.
    """

    def __init__(
        self,
        *,
        token: str,
        notion_base: str | None = None,
        notion_version: str | None = None,
        http_client: httpx.Client | None = None,
        max_block_depth: int = DEFAULT_MAX_BLOCK_DEPTH,
    ) -> None:
        if not token:
            raise ValueError(
                "notion api client: token is empty. "
                "fix: provide a Notion integration token (secret_…) via the connector config or kairix.secrets. "
                "next: see kairix/connectors/notion/connector.py for the credential contract."
            )
        # F15: token captured into private state; never logged.
        self._token = token
        self._notion_base = (notion_base or _DEFAULT_NOTION_BASE).rstrip("/")
        self._notion_version = notion_version or _DEFAULT_NOTION_VERSION
        self._http_client = http_client
        self._max_block_depth = max(1, max_block_depth)

    # ------------------------------------------------------------------
    # Public Notion surface
    # ------------------------------------------------------------------

    def search_pages(self, *, page_size: int = 100) -> Iterator[NotionPageRef]:
        """Yield every top-level page visible to the integration.

        Calls ``POST /v1/search`` with the page filter; the response
        carries every page the integration has been shared with (or
        inherits via a shared ancestor). Top-level visibility derives
        from the page's ``parent.type`` — ``workspace`` parents are the
        roots the connector maps to Containers.
        """
        body: dict[str, Any] = {
            "filter": {"value": "page", "property": "object"},
            "page_size": page_size,
        }
        yield from self._search_iter_pages(body)

    def search_databases(self, *, page_size: int = 100) -> Iterator[NotionDatabaseRef]:
        """Yield every database visible to the integration.

        Calls ``POST /v1/search`` with the database filter. Used by the
        hierarchy walk + by the database-row dispatch in
        :meth:`NotionConnector.list_changes_for_container`.
        """
        body: dict[str, Any] = {
            "filter": {"value": "database", "property": "object"},
            "page_size": page_size,
        }
        yield from self._search_iter_databases(body)

    def query_database(self, database_id: str, *, page_size: int = 100) -> Iterator[NotionPageRef]:
        """Yield every row of one database as a page reference.

        Calls ``POST /v1/databases/{id}/query``. Notion database rows
        are pages whose parent is the database; the spec §0 mermaid
        diagram has this as the second ChangeEvent surface alongside
        ``/v1/search`` for page edits.
        """
        url = f"{self._notion_base}/databases/{database_id}/query"
        cursor: str | None = None
        while True:
            body: dict[str, Any] = {"page_size": page_size}
            if cursor is not None:
                body[_QUERY_PARAM_START_CURSOR] = cursor
            payload = self._authorised_post(url, body).json()
            for entry in _entries(payload):
                yield _page_ref_from(entry)
            if not _bool_or_false(payload.get("has_more")):
                return
            cursor = _string_or_none(payload.get(_RESPONSE_KEY_NEXT_CURSOR))
            if cursor is None:
                return

    def iter_block_descendants(self, block_id: str) -> Iterator[NotionBlockRef]:
        """Walk one page's block tree breadth-first up to the depth cap.

        Calls ``GET /v1/blocks/{id}/children`` recursively. The depth
        cap (``max_block_depth``) bounds rate-limit consumption on
        synced-block / column-layout trees that recurse pathologically
        — spec §5 (Notion-specific failure mode).

        Sabotage proof #3 (block-fetching pagination correctness):
        breaking the cursor advance in :meth:`_iter_one_level` flips
        the multi-page test to fail because the second page is never
        fetched.
        """
        # Breadth-first walk so a depth-cap truncation lops a whole
        # subtree rather than a stripe. ``frontier`` carries
        # ``(block_id, depth)`` pairs.
        frontier: list[tuple[str, int]] = [(block_id, 0)]
        while frontier:
            current_id, depth = frontier.pop(0)
            for child in self._iter_one_level(current_id):
                yield child
                if child.has_children and depth + 1 < self._max_block_depth:
                    frontier.append((child.block_id, depth + 1))

    def fetch_page(self, page_id: str) -> NotionPageRef:
        """Fetch a single page's envelope.

        Calls ``GET /v1/pages/{id}``. Used when the connector needs to
        resolve a stale envelope (e.g. on a reconcile sweep or
        Resolver-replay path). For the standard list_changes path the
        envelopes come from :meth:`search_pages` directly.
        """
        url = f"{self._notion_base}/pages/{page_id}"
        body = self._authorised_get(url).json()
        return _page_ref_from(body)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _search_iter_pages(self, body: dict[str, Any]) -> Iterator[NotionPageRef]:
        """Helper for :meth:`search_pages` — handles the cursor advance."""
        url = f"{self._notion_base}/search"
        cursor: str | None = None
        while True:
            req_body = dict(body)
            if cursor is not None:
                req_body[_QUERY_PARAM_START_CURSOR] = cursor
            payload = self._authorised_post(url, req_body).json()
            for entry in _entries(payload):
                if entry.get("object") == "page":
                    yield _page_ref_from(entry)
            if not _bool_or_false(payload.get("has_more")):
                return
            cursor = _string_or_none(payload.get(_RESPONSE_KEY_NEXT_CURSOR))
            if cursor is None:
                return

    def _search_iter_databases(self, body: dict[str, Any]) -> Iterator[NotionDatabaseRef]:
        """Helper for :meth:`search_databases` — handles the cursor advance."""
        url = f"{self._notion_base}/search"
        cursor: str | None = None
        while True:
            req_body = dict(body)
            if cursor is not None:
                req_body[_QUERY_PARAM_START_CURSOR] = cursor
            payload = self._authorised_post(url, req_body).json()
            for entry in _entries(payload):
                if entry.get("object") == "database":
                    yield _database_ref_from(entry)
            if not _bool_or_false(payload.get("has_more")):
                return
            cursor = _string_or_none(payload.get(_RESPONSE_KEY_NEXT_CURSOR))
            if cursor is None:
                return

    def _iter_one_level(self, parent_block_id: str) -> Iterator[NotionBlockRef]:
        """Stream the immediate children of one block across all pages.

        Notion uses ``next_cursor`` query-string pagination on the
        children endpoint; this helper drives the page advance. Sabotage
        proof #3 lives here — breaking the ``start_cursor`` advance
        below causes the multi-page test to drop every block after the
        first page.
        """
        url = f"{self._notion_base}/blocks/{parent_block_id}/children"
        cursor: str | None = None
        while True:
            params = {"page_size": "100"}
            if cursor is not None:
                params[_QUERY_PARAM_START_CURSOR] = cursor
            payload = self._authorised_get(url, params=params).json()
            for entry in _entries(payload):
                yield _block_ref_from(entry)
            if not _bool_or_false(payload.get("has_more")):
                return
            cursor = _string_or_none(payload.get(_RESPONSE_KEY_NEXT_CURSOR))
            if cursor is None:
                return

    def _authorised_get(
        self,
        url: str,
        *,
        params: dict[str, str] | None = None,
    ) -> httpx.Response:
        """Issue a GET with the Notion bearer + Notion-Version header.

        F15-clean: the bearer is composed into the header here only;
        never logged, never returned. Logger lines name endpoint paths
        only — no token, no PII.
        """
        headers = self._headers()
        client = self._http_client
        if client is not None:
            response = client.get(url, headers=headers, params=params, timeout=_NOTION_REQUEST_TIMEOUT_S)
        else:
            with httpx.Client(timeout=_NOTION_REQUEST_TIMEOUT_S) as owned:
                response = owned.get(url, headers=headers, params=params)
        response.raise_for_status()
        return response

    def _authorised_post(self, url: str, body: dict[str, Any]) -> httpx.Response:
        """Issue a POST with the Notion bearer + JSON body."""
        headers = self._headers()
        client = self._http_client
        if client is not None:
            response = client.post(url, headers=headers, json=body, timeout=_NOTION_REQUEST_TIMEOUT_S)
        else:
            with httpx.Client(timeout=_NOTION_REQUEST_TIMEOUT_S) as owned:
                response = owned.post(url, headers=headers, json=body)
        response.raise_for_status()
        return response

    def _headers(self) -> dict[str, str]:
        """Build the per-request headers including the Notion-Version pin.

        F15: the bearer is composed into the Authorization value here
        only; the helper is private and never returns the token via any
        other path.
        """
        return {
            "Authorization": f"Bearer {self._token}",
            "Notion-Version": self._notion_version,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }


# ---------------------------------------------------------------------------
# Free-function parsers — kept module-level so tests can pin them
# without constructing a client. Parser robustness is part of the
# contract test (real-impl branch) — every field tolerates the absent
# / wrong-type shapes Notion's documented schema leaves as optional.
# ---------------------------------------------------------------------------


def _entries(body: dict[str, Any]) -> list[dict[str, Any]]:
    """Return the Notion list's ``results`` array (always a list of dicts)."""
    raw = body.get("results")
    if isinstance(raw, list):
        return [entry for entry in raw if isinstance(entry, dict)]
    return []


def _page_ref_from(entry: dict[str, Any]) -> NotionPageRef:
    """Lift one Notion page envelope into the typed frozen dataclass.

    Sabotage proof #4 (database vs page differentiation): a database
    row carries ``parent.type == "database_id"`` so the connector can
    dispatch it through the database path. Breaking the parser to drop
    ``parent_type`` from the output causes the database-vs-page
    dispatch test to fail because the connector falls back to the
    page-fetch path for database rows.
    """
    parent_raw = entry.get("parent")
    parent: dict[str, Any] = parent_raw if isinstance(parent_raw, dict) else {}
    parent_type = _string_or_empty(parent.get("type"))
    parent_id_key = _PARENT_ID_KEY_BY_TYPE.get(parent_type)
    parent_id: str | None = None
    if parent_id_key is not None:
        parent_id = _string_or_none(parent.get(parent_id_key))
    title = _extract_page_title(entry)
    return NotionPageRef(
        page_id=_string_or_empty(entry.get("id")),
        parent_type=parent_type,
        parent_id=parent_id,
        title=title,
        url=_string_or_empty(entry.get("url")),
        last_edited_time=_string_or_empty(entry.get("last_edited_time")),
        archived=_bool_or_false(entry.get("archived")),
    )


def _database_ref_from(entry: dict[str, Any]) -> NotionDatabaseRef:
    """Lift one Notion database envelope into the typed frozen dataclass."""
    parent_raw = entry.get("parent")
    parent: dict[str, Any] = parent_raw if isinstance(parent_raw, dict) else {}
    parent_type = _string_or_empty(parent.get("type"))
    parent_id_key = _PARENT_ID_KEY_BY_TYPE.get(parent_type)
    parent_id: str | None = None
    if parent_id_key is not None:
        parent_id = _string_or_none(parent.get(parent_id_key))
    title = _extract_database_title(entry)
    return NotionDatabaseRef(
        database_id=_string_or_empty(entry.get("id")),
        parent_type=parent_type,
        parent_id=parent_id,
        title=title,
        url=_string_or_empty(entry.get("url")),
        last_edited_time=_string_or_empty(entry.get("last_edited_time")),
    )


def _block_ref_from(entry: dict[str, Any]) -> NotionBlockRef:
    """Lift one Notion block envelope into the typed frozen dataclass."""
    block_type = _string_or_empty(entry.get("type"))
    return NotionBlockRef(
        block_id=_string_or_empty(entry.get("id")),
        block_type=block_type,
        has_children=_bool_or_false(entry.get("has_children")),
        plain_text=_extract_block_plain_text(entry, block_type),
    )


def _extract_page_title(entry: dict[str, Any]) -> str:
    """Pull a display-name string from the page's ``properties.title``.

    Notion stores page titles as a list of rich-text fragments under
    the property named ``title`` (for free pages) or under whichever
    title property the parent database declared (for database rows).
    We scan ``properties`` for the first ``type == "title"`` entry and
    join its plain-text fragments — robust to the property-name
    variations across database schemas.
    """
    properties_raw = entry.get("properties")
    properties: dict[str, Any] = properties_raw if isinstance(properties_raw, dict) else {}
    for value in properties.values():
        if not isinstance(value, dict):
            continue
        if value.get("type") != "title":
            continue
        return _join_rich_text(value.get("title"))
    return ""


def _extract_database_title(entry: dict[str, Any]) -> str:
    """Pull a display-name string from the database's ``title`` array."""
    return _join_rich_text(entry.get("title"))


def _extract_block_plain_text(entry: dict[str, Any], block_type: str) -> str:
    """Pull plain text from a block envelope.

    Most text-bearing block types (paragraph, heading_*, bulleted_list_item,
    numbered_list_item, quote, callout) store text under
    ``<block_type>.rich_text``. The exhaustive list is in the Notion
    docs; we read ``rich_text`` whenever the typed body carries it.
    """
    typed_body_raw = entry.get(block_type)
    typed_body: dict[str, Any] = typed_body_raw if isinstance(typed_body_raw, dict) else {}
    return _join_rich_text(typed_body.get("rich_text"))


def _join_rich_text(raw: object) -> str:
    """Join a Notion rich-text fragment list into a plain string.

    Each fragment carries ``plain_text``; missing fragments default to
    empty so a sparse envelope still parses without crashing.
    """
    if not isinstance(raw, list):
        return ""
    parts: list[str] = []
    for frag in raw:
        if not isinstance(frag, dict):
            continue
        text = frag.get("plain_text")
        if isinstance(text, str):
            parts.append(text)
    return "".join(parts)


def _string_or_empty(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _string_or_none(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _bool_or_false(value: Any) -> bool:
    return bool(value) if isinstance(value, bool) else False

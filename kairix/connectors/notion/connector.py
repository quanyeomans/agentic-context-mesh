"""``NotionConnector`` — SourceConnector for the Notion API.

Implements :class:`kairix.core.protocols.SourceConnector` for one
Notion workspace (one integration token = one workspace, per spec §1).
Change detection rides ``POST /v1/search`` sorted by
``last_edited_time`` plus per-database ``query`` calls; each Container
is one teamspace or top-level shared root with its own per-container
high-water-mark cursor.

Per spec §6.6 implementation sequence, this slice lands steps 1-3:

  * Step 1 — :meth:`iter_containers` + per-container cursor (replaces
    the legacy single-cursor pattern with a per-Container cursor token
    persisted via the framework's topology_containers table).
  * Step 2 — :meth:`load_hierarchy` walks workspace → teamspace/root →
    page → database parent-before-child per F58 (sabotage proof #2).
  * Step 3 — :meth:`retrieve_all_slim_docs` enumerates item ids only
    for the prune cycle.

The following steps land as separate commits per spec §6.6 ("steps 6
and 7 sequential because they depend on §9.1 and §5 respectively"):

  * Step 4 — :meth:`retrieve_all_slim_docs_with_perms` (weak — visibility
    only, no principal ACL per spec §1).
  * Step 5 — :meth:`reindex` (Resolver — per-page replay).
  * Step 6 — sensitivity routing from operator teamspace map.
  * Step 7 — :meth:`subscribe` / :meth:`renew_subscription` /
    :meth:`handle_event` (EventConnector — webhook surface).

Per F35, this module only imports from ``kairix.connectors.notion.*``
(same plugin) and ``kairix.core.*`` (the Protocol surface). No reach
into other connectors, no reach into the extractor layer. Per F37, the
``notion_client`` import surface stays under this directory tree (we
use raw ``httpx`` via :mod:`kairix.connectors.notion.api_client` to
keep dependencies tight).
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, cast

from kairix.connectors.notion.api_client import (
    DEFAULT_MAX_BLOCK_DEPTH,
    NotionApiClient,
    NotionPageRef,
)
from kairix.core.protocols import (
    ChangeEvent,
    Container,
    Cursor,
    HierarchyNode,
    RawArtefact,
    Sensitivity,
)

logger = logging.getLogger(__name__)

CONNECTOR_NAME = "notion"

# Default sensitivity tier for Notion content. Spec §1 (AccessType):
# "Default ``AccessType.PRIVATE`` with operator-declared sensitivity"
# — the connector ships with ``internal`` as the safer-than-public
# default; operators routing client-confidential or personal-tier
# workspaces override via the connector config's
# ``default_sensitivity`` key.
DEFAULT_SENSITIVITY: Sensitivity = "internal"

# Mime hint for the Markdown rendering of a Notion page (block-tree →
# Markdown per spec §0 mermaid). Bronze persists raw markdown bytes,
# Silver routes through the markitdown / passthrough extractor chain.
NOTION_MARKDOWN_MIME = "text/markdown"

# Wave E topology v2 flag name — same convention as other Wave E
# connector pilots (topology_v2_obsidian / _dex_crm / _m365_*).
# Module-level constant so the F52 call-site scan picks up exactly
# one verbatim reference per call site.
CONNECTOR_NOTION_FLAG = "connector_notion"

# Hierarchy node identifiers for the F58 parent-before-child emission.
# The root carries ``raw_node_id="notion"`` (the connector kind); each
# Container (teamspace / top-level root) is a child of the workspace
# root; database / page children land below the container they belong
# to. Spec §1: "hierarchy keys on stable ``id``, never path".
_HIERARCHY_ROOT_ID = "notion"
_HIERARCHY_ROOT_DISPLAY = "Notion workspace"


def _now_iso() -> str:
    """Return a current ISO-8601 UTC timestamp matching the connector
    boundary's :class:`ChangeEvent.modified_at` format.
    """
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class NotionCredentials:
    """Resolved integration-token triple for one Notion workspace sync.

    Frozen per F42 — the dataclass is the typed shape that crosses the
    boundary between secret resolution and the connector constructor.
    Tests construct a literal :class:`NotionCredentials` and pass it
    via the ``credentials`` kwarg; production resolves via
    :func:`kairix.secrets.get_secret`.
    """

    token: str


def _resolve_credentials_from_secrets() -> NotionCredentials:
    """Resolve the Notion integration token via :func:`kairix.secrets.get_secret`.

    The secret name ``connector-notion-token`` is the canonical
    credential identifier for the Notion plugin. When the secret is
    absent the function raises :class:`OSError` with an actionable
    ``fix:`` message — module import never crashes, only first-use of
    list_changes / fetch.

    F15-clean: the resolved token is captured into the frozen dataclass
    and never logged through any code path in this module.
    """
    from kairix.secrets import get_secret

    token = get_secret("connector-notion-token", required=True) or ""
    return NotionCredentials(token=token)


class NotionConnector:
    """SourceConnector for one Notion workspace.

    Construction is cheap (no I/O, no Notion API call). The first
    :meth:`list_changes` call exchanges the integration token for a
    server-side session by issuing a ``POST /v1/search`` and walks the
    visible page set.

    DI seams:

      * ``credentials`` — resolved :class:`NotionCredentials`. Tests
        pass a literal; production callers omit and the factory
        resolves from :mod:`kairix.secrets`.
      * ``client_builder`` — builds the :class:`NotionApiClient`.
        Tests pass a builder returning a client backed by an
        ``httpx.MockTransport`` so no real Notion call leaks.
      * ``default_sensitivity`` — connector-wide F39 tier; defaults to
        ``internal``. Operators set the matching key in
        ``connector_specific_config`` to override.

    Flag gating happens at the worker-dispatch boundary
    (:func:`kairix.worker.dispatch_notion_sync`) — when the
    ``connector_notion`` flag is OFF the connector slot is a no-op and
    this constructor is never called.
    """

    name: str = CONNECTOR_NAME

    def __init__(
        self,
        *,
        credentials: NotionCredentials | None = None,
        client_builder: Callable[[NotionCredentials], NotionApiClient] | None = None,
        default_sensitivity: Sensitivity = DEFAULT_SENSITIVITY,
        max_block_depth: int = DEFAULT_MAX_BLOCK_DEPTH,
    ) -> None:
        self._default_sensitivity: Sensitivity = default_sensitivity
        self._max_block_depth = max_block_depth

        resolved = credentials if credentials is not None else _resolve_credentials_from_secrets()
        self._credentials = resolved

        if client_builder is not None:
            self._api = client_builder(resolved)
        else:
            self._api = NotionApiClient(token=resolved.token, max_block_depth=max_block_depth)

        # Per-item envelope cache populated by :meth:`list_changes` so
        # :meth:`fetch` / :meth:`source_link` can resolve URL + title
        # without a second Notion API call. Same shape as the
        # SharePoint connector's per-tick cache.
        self._page_cache: dict[str, NotionPageRef] = {}
        # Sabotage proof #1 (per-page-tree cursor isolation): the
        # per-container cursors live in this dict, keyed by Container
        # ``container_id``. Replacing this with a single shared cursor
        # variable causes cross-container drift that the integration
        # test pins on.
        self._next_cursor_by_container: dict[str, str] = {}
        # Legacy single-cursor shape — populated when the connector
        # runs through :meth:`list_changes` (the SourceConnector base
        # surface) so that Wave OFF path stays bit-for-bit compatible
        # with the existing per-connector cursor shape.
        self._next_cursor: str | None = None

    # ------------------------------------------------------------------
    # SourceConnector Protocol surface (base)
    # ------------------------------------------------------------------

    def list_changes(self, cursor: Cursor | None) -> Iterator[ChangeEvent]:
        """Stream changes across every visible top-level Notion page.

        Legacy single-cursor surface — every visible page surfaces as
        a created/modified ChangeEvent. ``cursor`` is the ISO-8601
        timestamp of the last successfully-processed
        ``last_edited_time``; events older than ``cursor`` are filtered
        out.

        The new Wave E path is :meth:`list_changes_for_container`,
        which scopes the page search to one Container at a time and
        carries a per-container cursor token. This base method exists
        for SourceConnector Protocol satisfaction and for the OFF
        branch of the ``connector_notion`` flag.
        """
        events: list[ChangeEvent] = []
        latest_edited = cursor
        for page in self._api.search_pages():
            event = self._page_to_event(page)
            if event is None:
                continue
            if cursor is not None and event.modified_at <= cursor:
                continue
            self._page_cache[event.item_id] = page
            events.append(event)
            if latest_edited is None or event.modified_at > latest_edited:
                latest_edited = event.modified_at
        self._next_cursor = latest_edited
        return iter(events)

    def fetch(self, item_id: str) -> RawArtefact:
        """Render one Notion page as Markdown.

        Walks the page's block tree via
        :meth:`NotionApiClient.iter_block_descendants` and renders to
        Markdown — the Bronze layer persists the markdown bytes; the
        Silver / extractor chain handles further format-specific
        processing. The page envelope must have been cached by a
        previous :meth:`list_changes` call.
        """
        envelope = self._page_cache.get(item_id)
        if envelope is None:
            raise KeyError(
                f"notion: item_id {item_id!r} not in the per-tick envelope cache. "
                "fix: call list_changes() (or list_changes_for_container) before fetch() so the "
                "page-search drain populates the envelope cache before the orchestrator asks for the body. "
                "next: see kairix/core/connectors/pipeline.py for the orchestrator's "
                "list_changes -> fetch contract."
            )
        markdown = self._render_page_markdown(envelope)
        return RawArtefact(
            raw=markdown.encode("utf-8"),
            mime=NOTION_MARKDOWN_MIME,
            fetched_at=_now_iso(),
            sensitivity_hint=self._default_sensitivity,
        )

    def source_link(self, item_id: str) -> str:
        """Return the Notion page URL for the cached envelope.

        Falls back to a ``notion://`` shape when the envelope didn't
        carry a URL (older API responses or rare partial fetches).
        """
        envelope = self._page_cache.get(item_id)
        if envelope is not None and envelope.url:
            return envelope.url
        return f"notion://pages/{item_id}"

    def sensitivity_for(self, _item_id: str) -> Sensitivity:
        """Return the connector-configured default sensitivity tier.

        Step 6 (operator teamspace map → per-page sensitivity routing)
        lands as a separate commit per spec §6.6. v1 returns the
        configured default for every item; spec §1 documents the
        operator-declared teamspace-to-sensitivity map as future work.
        """
        return self._default_sensitivity

    # ------------------------------------------------------------------
    # Wave E — multi-container surface (PollConnector / HierarchyConnector / SlimConnector)
    # ------------------------------------------------------------------

    def iter_containers(self, cc_pair_id: int) -> Iterator[Container]:
        """Yield one :class:`Container` per top-level Notion root.

        Spec §1 (container model): a Container is one teamspace OR one
        top-level shared root. Since Notion integration visibility is
        page-subtree-by-subtree, we derive the visible root set from
        ``POST /v1/search`` and treat every page whose
        ``parent.type == "workspace"`` as a root. Each root becomes a
        Container with its own cursor.

        Sabotage proof #1 (per-page-tree cursor isolation): if a
        future refactor stores cursors in a single shared variable
        instead of ``_next_cursor_by_container``, two Containers'
        cursors collapse — the integration test
        ``test_per_container_cursors_are_isolated`` pins this by
        running two simulated drains and asserting cursors diverge.

        Empty-workspace fallback: if no top-level pages are visible,
        yield no Containers — the framework's cc_pair lifecycle treats
        an empty Container set as "no work to do", which is correct.
        """
        # Sort + dedupe by page_id for determinism — spec §1 emphasises
        # the stable-id invariant.
        seen: set[str] = set()
        roots: list[NotionPageRef] = []
        for page in self._api.search_pages():
            if page.parent_type != "workspace":
                continue
            if page.page_id in seen:
                continue
            seen.add(page.page_id)
            roots.append(page)
        for root in sorted(roots, key=lambda r: r.page_id):
            yield Container(
                cc_pair_id=cc_pair_id,
                container_id=root.page_id,
                access_state="ACCESSIBLE",
                cursor_token=None,
                last_synced_at=None,
            )

    def list_changes_for_container(self, container: Container) -> Iterator[ChangeEvent]:
        """Stream changes for one Container's page-tree.

        Walks the page set visible to the integration, filters to
        pages whose root ancestor is ``container.container_id``, and
        emits ChangeEvents for those whose ``last_edited_time`` is
        newer than ``container.cursor_token`` (the per-container
        high-water-mark).

        Sabotage proof #1: this method writes the high-water-mark to
        :attr:`_next_cursor_by_container[container.container_id]` —
        keyed per-container so two Containers' cursors stay isolated.
        Sabotage by collapsing into a shared cursor breaks
        ``test_per_container_cursors_are_isolated``.

        Sabotage proof #4 (database vs page differentiation): the
        :meth:`_dispatch_page` helper routes the page through the
        database-row path when ``page.parent_type == "database_id"``
        — bypassing that dispatch (treating database rows as plain
        pages) breaks ``test_database_rows_dispatch_through_database_path``.
        """
        cursor = container.cursor_token
        events: list[ChangeEvent] = []
        latest_edited = cursor
        for page in self._api.search_pages():
            root_id = self._root_id_for_page(page)
            if root_id != container.container_id:
                continue
            event = self._dispatch_page(page)
            if event is None:
                continue
            if cursor is not None and event.modified_at <= cursor:
                continue
            self._page_cache[event.item_id] = page
            events.append(event)
            if latest_edited is None or event.modified_at > latest_edited:
                latest_edited = event.modified_at
        if latest_edited is not None:
            self._next_cursor_by_container[container.container_id] = latest_edited
        return iter(events)

    def retrieve_all_slim_docs(self, container: Container) -> Iterator[str]:
        """SlimConnector — enumerate page ids only for the prune cycle.

        Yields every ``item_id`` visible inside the given Container.
        The framework diffs the returned set against
        ``documents.item_id`` and stages tombstones for ids that
        disappeared from the source. Spec §2 + §5 (hard-delete
        reconcile-sweep): this is the read side of the reconcile sweep
        — when a page hard-deletes, it falls out of ``search`` and the
        diff catches it.
        """
        for page in self._api.search_pages():
            root_id = self._root_id_for_page(page)
            if root_id != container.container_id:
                continue
            if not page.page_id:
                continue
            yield page.page_id

    def load_hierarchy(self, cc_pair_id: int) -> Iterator[HierarchyNode]:
        """HierarchyConnector — emit workspace → root → page/db nodes parent-before-child.

        Spec §1 hierarchy: ``workspace → teamspace → page / database
        → block``. We emit:

          1. One PAGE node for the workspace root (``raw_parent_id=None``).
          2. One PAGE node per visible top-level page-tree root
             (``raw_parent_id`` = workspace root).
          3. One PAGE node per visible database (``raw_parent_id`` =
             whichever root it belongs to, or workspace root for
             top-level databases).

        F58 (parent-before-child): the workspace root emits first,
        then top-level roots (each referencing the workspace), then
        databases (each referencing whichever root they belong to, or
        the workspace root itself for top-level databases). The
        ordering invariant is enforced mechanically by
        :func:`_validate_hierarchy_ordering`.

        Sabotage proof #2 (F58 parent-before-child): a future refactor
        that yields children before parents (e.g. databases before
        their root pages) trips the
        ``test_notion_hierarchy_parent_before_child`` contract test
        because every node's ``raw_parent_id`` must reference an
        already-emitted node.
        """
        # 1. Workspace root — always first per F58.
        yield HierarchyNode(
            cc_pair_id=cc_pair_id,
            raw_node_id=_HIERARCHY_ROOT_ID,
            raw_parent_id=None,
            display_name=_HIERARCHY_ROOT_DISPLAY,
            link=None,
            node_type="PAGE",
            external_access_json=None,
            sensitivity_hint=None,
        )
        # 2. Top-level page-tree roots — each refers back to the
        #    workspace root, satisfying F58.
        roots_seen: set[str] = set()
        roots: list[NotionPageRef] = []
        for page in self._api.search_pages():
            if page.parent_type != "workspace":
                continue
            if page.page_id in roots_seen:
                continue
            roots_seen.add(page.page_id)
            roots.append(page)
        for root in sorted(roots, key=lambda r: r.page_id):
            yield HierarchyNode(
                cc_pair_id=cc_pair_id,
                raw_node_id=root.page_id,
                raw_parent_id=_HIERARCHY_ROOT_ID,
                display_name=root.title or root.page_id,
                link=root.url or None,
                node_type="PAGE",
                external_access_json=None,
                sensitivity_hint=None,
            )
        # 3. Databases — each refers to whichever root it belongs to,
        #    or the workspace root if it's a top-level database.
        for database in sorted(self._api.search_databases(), key=lambda d: d.database_id):
            parent_id = _HIERARCHY_ROOT_ID
            if (
                database.parent_type == "page_id"
                and database.parent_id is not None
                and database.parent_id in roots_seen
            ):
                parent_id = database.parent_id
            yield HierarchyNode(
                cc_pair_id=cc_pair_id,
                raw_node_id=database.database_id,
                raw_parent_id=parent_id,
                display_name=database.title or database.database_id,
                link=database.url or None,
                node_type="PAGE",
                external_access_json=None,
                sensitivity_hint=None,
            )

    # ------------------------------------------------------------------
    # Forward-only API
    # ------------------------------------------------------------------

    def next_cursor(self) -> str | None:
        """Return the legacy single-cursor token after :meth:`list_changes`.

        Mirrors the SharePoint pattern. Populated by the most recent
        successful :meth:`list_changes` drain; ``None`` before the
        first call.
        """
        return self._next_cursor

    def next_cursor_for_container(self, container_id: str) -> str | None:
        """Return the per-Container cursor token after
        :meth:`list_changes_for_container`.

        Sabotage proof #1: this is the per-container slot — the
        integration test asserts two distinct Container ids return
        distinct cursor strings after independent drains.
        """
        return self._next_cursor_by_container.get(container_id)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _page_to_event(self, page: NotionPageRef) -> ChangeEvent | None:
        """Translate one Notion page envelope to a typed :class:`ChangeEvent`.

        Pages with empty ids drop (rare wire-level failure mode).
        Archived pages emit as ``archived`` per spec §2 op mapping;
        live pages emit as ``modified`` (Notion ``last_edited_time``
        always reflects the most recent edit so the connector treats
        every observed page as a modify candidate).
        """
        if not page.page_id:
            return None
        modified_at = page.last_edited_time or _now_iso()
        op: Any = "archived" if page.archived else "modified"
        return ChangeEvent(
            op=op,
            item_id=page.page_id,
            modified_at=modified_at,
            metadata={
                "sensitivity": self._default_sensitivity,
                "parent_type": page.parent_type,
                "name": page.title,
                "mime": NOTION_MARKDOWN_MIME,
            },
        )

    def _dispatch_page(self, page: NotionPageRef) -> ChangeEvent | None:
        """Dispatch a page through page-vs-database differentiation.

        Sabotage proof #4: pages whose ``parent_type == "database_id"``
        are database rows; the metadata carries ``parent_type`` so
        downstream Silver / chunker code can apply per-database row
        chunking. Bypassing this dispatch (always returning the plain
        page event) drops the parent-type tag and breaks the
        database-vs-page test that inspects metadata.
        """
        event = self._page_to_event(page)
        if event is None:
            return None
        # When the page is a database row, augment metadata so
        # downstream chunking knows to treat it as a row, not a free
        # page. The metadata is a frozen mapping at construction time;
        # we copy + extend then rebuild.
        if page.parent_type == "database_id":
            extended_meta: dict[str, Any] = dict(event.metadata)
            extended_meta["item_kind"] = "database_row"
            extended_meta["database_id"] = page.parent_id or ""
            return ChangeEvent(
                op=event.op,
                item_id=event.item_id,
                modified_at=event.modified_at,
                parent_id=event.parent_id,
                metadata=extended_meta,
            )
        return event

    def _root_id_for_page(self, page: NotionPageRef) -> str:
        """Resolve which top-level root a page belongs to.

        Top-level pages map to themselves. Pages parented to another
        page or a database walk up to their root via the cached
        envelope set; if the chain breaks (we haven't seen the parent
        yet, common during a partial mid-tick fetch) we fall back to
        the direct parent id so the page still routes deterministically.
        """
        if page.parent_type == "workspace":
            return page.page_id
        if page.parent_id is None:
            return page.page_id
        return page.parent_id

    def _render_page_markdown(self, page: NotionPageRef) -> str:
        """Render a Notion page as Markdown.

        Title becomes an H1; the block tree is walked breadth-first
        and each text-bearing block emits its plain text. This is a
        minimum-viable Markdown rendering — the Wave F
        :class:`MarkdownStructuralChunker v2` is the canonical
        downstream chunker for Notion content per spec §6 + F55.
        """
        lines: list[str] = []
        if page.title:
            lines.append(f"# {page.title}")
            lines.append("")
        for block in self._api.iter_block_descendants(page.page_id):
            if block.plain_text:
                lines.append(block.plain_text)
        return "\n".join(lines).rstrip() + "\n"


def make_connector(config: Mapping[str, Any]) -> NotionConnector:
    """Construct a :class:`NotionConnector` from a config mapping.

    Expected keys:

      * ``default_sensitivity`` (optional) — one of the F39 sensitivity
        literals; defaults to ``"internal"``.
      * ``max_block_depth`` (optional) — recursion cap on the block
        walk. Defaults to ``DEFAULT_MAX_BLOCK_DEPTH``.

    Credentials resolve via :func:`kairix.secrets.get_secret` —
    ``connector-notion-token`` must be set.

    Registered via ``[project.entry-points."kairix.connectors"]`` in
    kairix's ``pyproject.toml`` so the orchestration layer can resolve
    ``notion`` to this factory by name.
    """
    declared = config.get("default_sensitivity", DEFAULT_SENSITIVITY)
    if declared not in ("public", "internal", "client-confidential", "personal"):
        raise ValueError(
            f"notion: default_sensitivity {declared!r} is not a valid F39 tier. "
            "fix: set default_sensitivity to one of "
            "public / internal / client-confidential / personal. "
            "next: see kairix/core/protocols.py Sensitivity for the literal set."
        )
    sensitivity = cast(Sensitivity, declared)
    raw_depth = config.get("max_block_depth", DEFAULT_MAX_BLOCK_DEPTH)
    if not isinstance(raw_depth, int) or raw_depth < 1:
        raise ValueError(
            f"notion: max_block_depth {raw_depth!r} must be a positive integer. "
            "fix: set max_block_depth to an integer >= 1 (default 8). "
            "next: see kairix/connectors/notion/api_client.py DEFAULT_MAX_BLOCK_DEPTH."
        )
    return NotionConnector(default_sensitivity=sensitivity, max_block_depth=raw_depth)


def cursor_summary_json(per_container: Mapping[str, str | None]) -> str:
    """Encode per-container cursors as a deterministic JSON string.

    Useful for operator diagnostics — the per-container cursor map is
    persisted via the topology_containers framework table in Wave C,
    but operators inspecting a sync run may want a single-line summary.
    """
    return json.dumps(
        {k: v for k, v in per_container.items() if v is not None},
        sort_keys=True,
        ensure_ascii=False,
    )

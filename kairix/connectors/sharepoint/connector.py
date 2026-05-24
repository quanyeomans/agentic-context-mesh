"""``SharePointConnector`` — SourceConnector for SharePoint document libraries.

Implements :class:`kairix.core.protocols.SourceConnector` for one or
more SharePoint document libraries in a Microsoft 365 tenant. Change
detection rides the Graph drive delta-query token:

* First sync — no cursor — calls
  :meth:`SharePointGraphClient.iter_drive_items` from the seed delta
  URL for each configured drive. Every envelope surfaces as a
  ``created`` :class:`ChangeEvent`.
* Subsequent syncs — cursor is a persisted JSON map of
  ``drive_id -> deltaLink`` — calls
  :meth:`SharePointGraphClient.fetch_delta_page` from each drive's
  resume link. Envelopes surface as ``created`` / ``modified`` /
  ``deleted`` based on the delta payload.

Per ADR-019 (provider plugin architecture), this connector shares the
same Azure AD app registration with the M365 email-headers + calendar
siblings. The operator grants ``Sites.Read.All`` + ``Files.Read.All``
on the same app (alongside ``Mail.Read`` + ``Calendars.Read`` for the
siblings) and reuses the tenant/client/secret triple.

Out of scope for this slice (deferred to follow-up):

* Multi-container Wave E methods (``iter_containers`` / ``load_hierarchy``
  with per-drive emission) — today every drive in the configured set is
  driven through the single :meth:`list_changes` surface for
  back-compat with the legacy single-cursor pattern.
* SharePoint list items (only document libraries this slice).
* Per-Purview-label sensitivity routing — default ``internal`` with a
  ``default_sensitivity`` operator override.

Per F35, this module only imports from
``kairix.connectors.sharepoint.*`` (same plugin), ``kairix.core.*``
(the Protocol surface), and ``kairix.transport.auth.*`` (the shared
OAuth2 helper). No reach into other connectors, no reach into the
extractor layer.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, cast

from kairix.connectors.sharepoint.graph_client import (
    DriveItemRef,
    SharePointGraphClient,
)
from kairix.core.protocols import (
    ChangeEvent,
    Container,
    Cursor,
    HierarchyNode,
    RawArtefact,
    Sensitivity,
)
from kairix.transport.auth.oauth2_client_creds import (
    OAuth2ClientCredsAuth,
)

logger = logging.getLogger(__name__)

CONNECTOR_NAME = "sharepoint"

# Default sensitivity tier for SharePoint content. SharePoint document
# libraries default to internal-tier corporate content; operators
# routing client-confidential or personal-tier libraries override via
# the connector config's ``default_sensitivity`` key.
DEFAULT_SENSITIVITY: Sensitivity = "internal"

# Microsoft Graph client-credentials scope for app-only reads.
# Always ``.default`` per the Microsoft v2 endpoint convention.
GRAPH_DEFAULT_SCOPE = "https://graph.microsoft.com/.default"

# Mime hint for binaries whose Graph envelope didn't declare one.
DEFAULT_FETCH_MIME = "application/octet-stream"

# Wave E topology v2 pilot — name of the per-connector flag that gates
# the multi-container shape. Module-level constant so the F52 call-site
# scan picks up exactly one verbatim reference per call site.
TOPOLOGY_V2_SHAREPOINT_FLAG = "topology_v2_sharepoint"

# Wave E hierarchy root node id. Each configured drive becomes a DRIVE-
# typed child FOLDER under this root SITE node.
_HIERARCHY_ROOT_ID = "sharepoint"
_HIERARCHY_ROOT_DISPLAY = "SharePoint"

# F17 — metadata key for the sensitivity tier carried on every emitted
# ChangeEvent. Extracted as a constant so the repeated literal across
# the legacy + Wave E emission paths has one edit site.
_META_SENSITIVITY_KEY = "sensitivity"


def _now_iso() -> str:
    """Return a current ISO-8601 UTC timestamp matching the connector
    boundary's :class:`ChangeEvent.modified_at` format.
    """
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class SharePointCredentials:
    """Resolved client-credentials triple for one SharePoint sync.

    Frozen per F42 — the dataclass is the typed shape that crosses the
    boundary between secret resolution and the connector constructor.
    Tests construct a literal :class:`SharePointCredentials` and pass it
    via the ``credentials`` kwarg; production resolves via the same
    pattern the sibling M365 connectors use.
    """

    tenant_id: str
    client_id: str
    client_secret: str


@dataclass(frozen=True)
class SharePointDriveSpec:
    """One configured drive the connector should sync.

    Frozen per F42. ``drive_id`` is the Graph drive identifier; the
    operator obtains it once at deployment time (e.g. via the
    ``GET /sites?search=*`` enumeration call exposed by
    :meth:`SharePointGraphClient.list_sites`) and pins it in
    ``kairix.config.yaml``. Pinning by id (not by URL) makes the sync
    deterministic across site renames.
    """

    drive_id: str
    site_id: str | None = None
    display_name: str | None = None


def _default_flag_reader(name: str) -> bool:
    """Production default for the topology-v2-sharepoint flag check.

    Delegates to :func:`kairix.core.features.flag` so the production
    path threads through the env-var → config-overlay → registry
    resolution chain. Tests inject a different callable (typically one
    backed by :class:`tests.fakes.FakeFeatureFlagResolver`) so the
    branch under test is pinned without monkey-patching the resolver
    module (F1-clean / F2-clean).

    Lifted to a module-level helper so the connector's signature can
    carry a real callable default (F6-clean) without a per-call
    ``Optional[...] = None`` shape.
    """
    from kairix.core.features import flag as _prod_flag

    return _prod_flag(name)


def _resolve_credentials_from_secrets() -> SharePointCredentials:
    """Resolve the three required secrets via :func:`kairix.secrets.get_secret`.

    All three must be present — :func:`kairix.secrets.get_secret` with
    ``required=True`` raises :class:`OSError` when the underlying secret
    cannot be resolved, so each call below either returns a non-empty
    string or aborts the resolve. Per ADR-019, SharePoint reuses the
    M365 secret-name convention (``connector-m365-*``) so a single AAD
    app registration drives every sibling connector.
    """
    from kairix.secrets import get_secret

    tenant = get_secret("connector-m365-tenant-id", required=True) or ""
    client = get_secret("connector-m365-client-id", required=True) or ""
    secret = get_secret("connector-m365-client-secret", required=True) or ""
    return SharePointCredentials(tenant_id=tenant, client_id=client, client_secret=secret)


class SharePointConnector:
    """SourceConnector for one or more SharePoint document libraries.

    Construction is cheap (no I/O, no OAuth exchange). The first
    :meth:`list_changes` call exchanges client-credentials for a bearer
    token (via the injected :class:`OAuth2ClientCredsAuth`) and drains
    each configured drive's delta query in turn.

    DI seams:

      * ``credentials`` — resolved :class:`SharePointCredentials`. Tests
        pass a literal; production callers omit and the factory resolves
        from :mod:`kairix.secrets`.
      * ``client_builder`` — builds the :class:`SharePointGraphClient`.
        Tests pass a builder returning a client backed by an
        ``httpx.MockTransport`` so no real Graph call leaks.
      * ``auth`` — pre-built :class:`OAuth2ClientCredsAuth`. Tests pass
        an auth bound to the mock-transport client; production omits
        and the connector builds one from ``credentials``.
      * ``default_sensitivity`` — connector-wide F39 tier; defaults to
        ``internal`` per ADR-005. Operators set the matching key in
        ``connector_specific_config`` to override.
    """

    name: str = CONNECTOR_NAME

    def __init__(
        self,
        drives: list[SharePointDriveSpec],
        *,
        credentials: SharePointCredentials | None = None,
        client_builder: Callable[[OAuth2ClientCredsAuth], SharePointGraphClient] | None = None,
        auth: OAuth2ClientCredsAuth | None = None,
        default_sensitivity: Sensitivity = DEFAULT_SENSITIVITY,
        flag_reader: Callable[[str], bool] = _default_flag_reader,
    ) -> None:
        if not drives:
            raise ValueError(
                "sharepoint: drives list is empty. "
                "fix: declare at least one drive_id under the sharepoint connector block. "
                "next: see docs/architecture/connector-ingestion-architecture.md §8 "
                "for the SharePoint connector config shape."
            )
        self._drives: tuple[SharePointDriveSpec, ...] = tuple(drives)
        self._default_sensitivity: Sensitivity = default_sensitivity
        self._flag_reader = flag_reader

        resolved_auth: OAuth2ClientCredsAuth
        if auth is not None:
            resolved_auth = auth
        else:
            creds = credentials if credentials is not None else _resolve_credentials_from_secrets()
            resolved_auth = OAuth2ClientCredsAuth(
                tenant_id=creds.tenant_id,
                client_id=creds.client_id,
                client_secret=creds.client_secret,
                scope=GRAPH_DEFAULT_SCOPE,
            )
        self._auth = resolved_auth

        if client_builder is not None:
            self._graph = client_builder(resolved_auth)
        else:
            self._graph = SharePointGraphClient(auth=resolved_auth)

        # Per-item envelope cache — populated by :meth:`list_changes`
        # so :meth:`fetch` can resolve drive id, web URL, and mime
        # without a second Graph call.
        self._cache: dict[str, DriveItemRef] = {}
        # Next-tick cursor — populated after :meth:`list_changes` drains
        # every configured drive. Serialised as a JSON map
        # ``drive_id -> deltaLink`` so a single opaque string round-trips
        # through the orchestrator's cursor_store.
        self._next_cursor: str | None = None

    # ------------------------------------------------------------------
    # SourceConnector Protocol surface
    # ------------------------------------------------------------------

    def list_changes(self, cursor: Cursor | None) -> Iterator[ChangeEvent]:
        """Stream changes across every configured drive.

        ``cursor`` is the JSON map persisted on the previous tick (or
        ``None`` for cold start). The connector walks each configured
        drive's delta endpoint, caches envelopes on the way through, and
        records the next-tick cursor on :attr:`_next_cursor`.
        """
        per_drive_cursor = _deserialise_cursor(cursor)
        events: list[ChangeEvent] = []
        next_links: dict[str, str] = {}
        for spec in self._drives:
            drive_id = spec.drive_id
            start_url = per_drive_cursor.get(drive_id)
            for item in self._graph.iter_drive_items(drive_id, start_url=start_url):
                event = self._item_to_event(item, drive_id=drive_id)
                if event is None:
                    continue
                self._cache[event.item_id] = item
                events.append(event)
            drive_delta = self._graph.last_delta_link_for_drive(drive_id)
            if drive_delta is not None:
                next_links[drive_id] = drive_delta
            elif start_url is not None:
                next_links[drive_id] = start_url
        self._next_cursor = _serialise_cursor(next_links) if next_links else None
        return iter(events)

    def fetch(self, item_id: str) -> RawArtefact:
        """Download the binary content for ``item_id``.

        Uses the per-tick envelope cache populated by
        :meth:`list_changes` to resolve the drive id; raises with a
        fix-pointer when the orchestrator asks for an id outside the
        cache (typically because ``fetch`` was called without a prior
        ``list_changes`` drain in this process).
        """
        envelope = self._cache.get(item_id)
        if envelope is None:
            raise KeyError(
                f"sharepoint: item_id {item_id!r} not in the per-tick envelope cache. "
                "fix: call list_changes() before fetch() so the delta drain "
                "populates the envelope cache before the orchestrator asks for the body. "
                "next: see kairix/core/connectors/pipeline.py for the orchestrator's "
                "list_changes -> fetch contract."
            )
        raw = self._graph.fetch_item_content(envelope.drive_id, envelope.item_id)
        mime = envelope.mime or DEFAULT_FETCH_MIME
        return RawArtefact(raw=raw, mime=mime, fetched_at=_now_iso())

    def source_link(self, item_id: str) -> str:
        """Return the SharePoint web URL for the cached envelope.

        Falls back to a Graph drive-item URI shape when the envelope
        didn't carry a web URL (older Graph responses, or items whose
        canonical URL is computed lazily by SharePoint). The fallback
        is still deterministic and round-trips back to the source via
        the Graph items endpoint.
        """
        envelope = self._cache.get(item_id)
        if envelope is not None and envelope.web_url:
            return envelope.web_url
        if envelope is not None:
            return f"sharepoint://{envelope.drive_id}/items/{envelope.item_id}"
        return f"sharepoint://items/{item_id}"

    def sensitivity_for(self, _item_id: str) -> Sensitivity:
        """Return the connector-configured default sensitivity.

        v1 has no per-item overrides — every envelope from the connector
        carries the configured tier. A future ADR can read Microsoft
        Purview labels off the envelope and downgrade specific items
        without breaking the Protocol.
        """
        return self._default_sensitivity

    # ------------------------------------------------------------------
    # Topology v2 Wave B — capability mix-in shims (no behavioural change)
    # ------------------------------------------------------------------
    # The shims below let the connector satisfy the new capability
    # Protocols (CheckpointedConnector, CredentialsConnector,
    # OAuthConnector) by delegating to existing methods OR raising
    # actionable NotImplementedError where the source kind does not
    # support the surface. Production routing through these methods is
    # gated by ``topology_v2_protocol`` (default-off).

    def load_from_checkpoint(self, _container: Container, checkpoint: str | None) -> Iterator[ChangeEvent]:
        """CheckpointedConnector shim — forward to :meth:`list_changes`.

        Graph delta works on opaque deltaLink strings (serialised as a
        per-drive JSON map by this connector); the shim forwards
        ``checkpoint`` directly so observable behaviour matches the v1
        path. ``_container`` is accepted for Protocol compliance but the
        legacy path is single-cursor per cc_pair (Wave E activates
        per-container routing).
        """
        return self.list_changes(checkpoint)

    def load_credentials(self, credentials: dict[str, Any]) -> dict[str, Any] | None:
        """CredentialsConnector shim — return the input unchanged.

        Client-credentials flow consumes the operator-supplied tenant /
        client / secret triple as-is; no transformation, no token
        exchange at this surface (the OAuth2 helper exchanges at
        first-fetch time).
        """
        return credentials

    @classmethod
    def oauth_authorization_url(cls, _state: str) -> str:
        """OAuthConnector shim — raise actionable NotImplementedError.

        This connector uses the OAuth2 client-credentials flow (app-only,
        no operator-in-the-loop) — there is no authorization URL to
        visit. The shim raises so a framework path that mistakenly
        routes to the three-legged flow fails loudly with a fix hint.
        """
        raise NotImplementedError(
            "sharepoint: client-credentials flow only; OAuth user flow not supported for this plugin. "
            "fix: drive auth via the configured tenant_id / client_id / client_secret triple. "
            "next: see kairix/connectors/sharepoint/connector.py for the credential contract."
        )

    @classmethod
    def oauth_code_to_token(cls, _code: str) -> dict[str, Any]:
        """OAuthConnector shim — raise actionable NotImplementedError.

        Counterpart to :meth:`oauth_authorization_url` — no code-to-token
        exchange because this connector does not surface an OAuth
        consent screen.
        """
        raise NotImplementedError(
            "sharepoint: client-credentials flow only; OAuth user flow not supported for this plugin. "
            "fix: drive auth via the configured tenant_id / client_id / client_secret triple. "
            "next: see kairix/connectors/sharepoint/connector.py for the credential contract."
        )

    # ------------------------------------------------------------------
    # Topology v2 Wave E — per-connector multi-container pilot
    # ------------------------------------------------------------------
    # Wave B landed shim implementations of the capability Protocols
    # (CheckpointedConnector / CredentialsConnector / OAuthConnector).
    # Wave E adds real implementations behind the
    # ``topology_v2_sharepoint`` flag:
    #
    #   * :meth:`iter_containers` — one :class:`Container` per configured
    #     Graph drive, each with its own ``@odata.deltaLink`` persisted
    #     as the container's ``cursor_token`` (replaces the v1 single
    #     packed JSON map).
    #   * :meth:`list_changes_for_container` — when flag ON, reads
    #     ``container.cursor_token`` (a per-drive Graph deltaLink) and
    #     runs the Graph delta query against ``container.container_id``
    #     (the drive id) ONLY. When flag OFF, retains the Wave B shim
    #     behaviour (delegate to legacy :meth:`list_changes`).
    #   * :meth:`load_hierarchy` — when flag ON, emits a root SITE-typed
    #     FOLDER node plus one DRIVE-typed FOLDER per configured drive
    #     parent-before-child per F58. When flag OFF, emits one root
    #     FOLDER node only (Wave B shim shape).
    #   * :meth:`retrieve_all_slim_docs` — id-only enumeration for the
    #     prune cycle; drains the per-container delta with envelope
    #     items only.
    #   * :meth:`reindex` — :class:`Resolver` — per-item failure replay;
    #     emits one :class:`ChangeEvent` per failed item id without
    #     re-running the full delta window.
    #
    # The flag defaults OFF so existing operators see bit-for-bit
    # current behaviour. The ON branch is the per-container pattern
    # that mirrors the obsidian / m365_calendar / m365_email_headers
    # Wave E pilots.

    def iter_containers(self, cc_pair_id: int) -> Iterator[Container]:
        """Yield one :class:`Container` per configured Graph drive.

        Topology v2 §4: each Container has its own delta cursor — the
        Wave E pilot maps each operator-declared drive to its own
        Container so the operator can add or remove individual drives
        without disturbing the cursor state of the others.

        ``access_state`` is always ``ACCESSIBLE`` at iteration time;
        per-drive permission drift (Sites.Selected revocation) surfaces
        as a request-time error from :meth:`list_changes_for_container`,
        not at iteration. ``cursor_token`` and ``last_synced_at`` start
        ``None``; the framework persists subsequent values (the Graph
        ``@odata.deltaLink``) to the ``topology_containers`` table.

        Calling convention mirrors the sibling Wave E pilots: the
        framework's lifecycle layer (``kairix/core/connectors/cc_pair.py``)
        passes ``cc_pair_id`` so the connector can construct the
        Container without reaching back into the cc_pair store.
        """
        for spec in self._drives:
            yield Container(
                cc_pair_id=cc_pair_id,
                container_id=spec.drive_id,
                access_state="ACCESSIBLE",
                cursor_token=None,
                last_synced_at=None,
            )

    def list_changes_for_container(self, container: Container) -> Iterator[ChangeEvent]:
        """Stream changes for one container's Graph drive.

        When the ``topology_v2_sharepoint`` flag is ON: reads
        ``container.cursor_token`` as the per-drive Graph deltaLink
        (None on first sync) and walks the delta pages for THAT drive
        only. Per-drive isolation means adding or removing one drive
        does not affect the cursor state of the others — bypasses the
        legacy packed JSON cursor map entirely so a single-drive 403
        cannot poison the shared cursor.

        When the flag is OFF: retains the Wave B shim behaviour —
        delegate to :meth:`list_changes` with the container's cursor
        (serialised through ``_serialise_cursor`` so the legacy
        per-drive map shape round-trips) so the observable shape is
        identical to the legacy v1 path.
        """
        if not self._flag_reader(TOPOLOGY_V2_SHAREPOINT_FLAG):
            # OFF branch — bit-for-bit Wave B shim. Wrap the container's
            # cursor as a single-drive JSON map and forward to the
            # legacy single-cursor list_changes path so the observable
            # shape (events emitted, _next_cursor populated) matches v1.
            legacy_cursor: Cursor | None = (
                _serialise_cursor({container.container_id: container.cursor_token})
                if container.cursor_token is not None
                else None
            )
            return self.list_changes(legacy_cursor)
        return self._list_changes_for_container_scoped(container)

    def load_hierarchy(self, cc_pair_id: int) -> Iterator[HierarchyNode]:
        """HierarchyConnector — emit nodes parent-before-child per F58.

        When the ``topology_v2_sharepoint`` flag is ON: emits a root
        SITE-typed FOLDER node (``raw_node_id="sharepoint"``,
        ``raw_parent_id=None``) followed by one DRIVE-typed FOLDER per
        configured drive, with ``raw_node_id`` set to the drive id and
        ``raw_parent_id`` pointing at the root. Parent-before-child per
        F58.

        When the flag is OFF: emits one root FOLDER node only (Wave B
        shim shape) so the observable shape matches the sibling
        connectors' OFF-branch behaviour.

        Per-drive sub-folder hierarchy (Documents / Shared with me /
        custom libraries) is a Wave-E+1 enhancement — this slice keeps
        the hierarchy at drive-as-folder granularity to mirror the
        obsidian / m365_calendar / dex_crm Wave E pilots.
        """
        if not self._flag_reader(TOPOLOGY_V2_SHAREPOINT_FLAG):
            yield HierarchyNode(
                cc_pair_id=cc_pair_id,
                raw_node_id=_HIERARCHY_ROOT_ID,
                raw_parent_id=None,
                display_name=_HIERARCHY_ROOT_DISPLAY,
                link=None,
                node_type="FOLDER",
                external_access_json=None,
                sensitivity_hint=None,
            )
            return
        yield HierarchyNode(
            cc_pair_id=cc_pair_id,
            raw_node_id=_HIERARCHY_ROOT_ID,
            raw_parent_id=None,
            display_name=_HIERARCHY_ROOT_DISPLAY,
            link=None,
            node_type="SITE",
            external_access_json=None,
            sensitivity_hint=None,
        )
        for spec in self._drives:
            yield HierarchyNode(
                cc_pair_id=cc_pair_id,
                raw_node_id=spec.drive_id,
                raw_parent_id=_HIERARCHY_ROOT_ID,
                display_name=spec.display_name or spec.drive_id,
                link=None,
                node_type="DRIVE",
                external_access_json=None,
                sensitivity_hint=None,
            )

    def retrieve_all_slim_docs(self, container: Container) -> Iterator[str]:
        """SlimConnector — id-only enumeration for the prune cycle.

        Drains the per-container delta endpoint (or full enumeration
        when the container's cursor is None) and emits only the
        ``item_id`` strings. The orchestrator diffs this against the
        ``documents`` table to detect deletes and stage tombstones —
        much cheaper than re-fetching every body.

        Reads ``container.cursor_token`` so the prune scan honours the
        per-container resume position; ``None`` triggers a full
        enumeration (cold-prune). Filters tombstones (removed items)
        out because the prune cycle is asking "what ids does the source
        still have?".
        """
        drive_id = container.container_id
        start_url = container.cursor_token
        for item in self._graph.iter_drive_items(drive_id, start_url=start_url):
            if not item.item_id or item.removed:
                continue
            yield item.item_id

    def reindex(
        self,
        failed_item_ids: tuple[str, ...],
        *,
        include_permissions: bool = False,
    ) -> Iterator[ChangeEvent]:
        """Resolver — per-item failure replay.

        Cheaper than re-running a delta window after a partial-fetch
        failure: yields one :class:`ChangeEvent` per id in
        ``failed_item_ids`` so the orchestrator can re-drive the
        downstream pipeline (fetch → extract → silver → index) against
        ONLY the items that failed.

        Each emitted event is shaped as a ``modified`` op (the item
        existed before the failure and still exists; reindex is a
        replay of the silver/index path, not a tombstone scan). The
        event's ``modified_at`` carries the wall-clock at replay time
        so any downstream recency-sort sees the replay as recent.

        ``include_permissions`` is accepted per the Protocol surface
        but the Wave E slice ships only the bare reindex path —
        permission-replay layers on top when SlimConnectorWithPermSync
        lands in a follow-up slice. The kwarg is recorded in metadata
        so a future slice can route to the perm-sync replay without a
        Protocol break.

        Filters duplicate ids and empty strings so the orchestrator's
        deadletter table can safely feed the raw tuple without
        pre-cleaning. The "replay only failed ids" filter is the
        load-bearing invariant — sabotage-proved by integration
        coverage that asserts the emitted ids match the failures tuple
        and nothing else.
        """
        seen: set[str] = set()
        for raw_id in failed_item_ids:
            if not raw_id or raw_id in seen:
                continue
            seen.add(raw_id)
            yield ChangeEvent(
                op="modified",
                item_id=raw_id,
                modified_at=_now_iso(),
                metadata={
                    _META_SENSITIVITY_KEY: self._default_sensitivity,
                    "reindex": True,
                    "include_permissions": include_permissions,
                },
            )

    # ------------------------------------------------------------------
    # Forward-only API
    # ------------------------------------------------------------------

    def next_cursor(self) -> str | None:
        """Return the JSON cursor map the orchestrator should persist.

        Populated by the most recent successful :meth:`list_changes`
        drain; ``None`` before the first call or when no drive completed
        a delta sweep.
        """
        return self._next_cursor

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _list_changes_for_container_scoped(self, container: Container) -> Iterator[ChangeEvent]:
        """Wave E ON-branch: drain Graph delta for one container's drive only.

        Reads the container's own ``cursor_token`` (the per-drive Graph
        ``@odata.deltaLink``) and walks delta pages for the container's
        drive id only. Each container's cursor is read independently —
        adding or removing one drive does not disturb another drive's
        resume position.

        Bypasses the legacy ``_serialise_cursor`` / ``_deserialise_cursor``
        packed JSON map entirely so a single-drive failure cannot
        poison the shared cursor. The per-container path's events still
        populate ``self._cache`` so :meth:`fetch` can resolve the drive
        id without a second Graph call.
        """
        drive_id = container.container_id
        start_url = container.cursor_token
        events: list[ChangeEvent] = []
        for item in self._graph.iter_drive_items(drive_id, start_url=start_url):
            event = self._item_to_event(item, drive_id=drive_id)
            if event is None:
                continue
            self._cache[event.item_id] = item
            events.append(event)
        return iter(events)

    def _item_to_event(self, item: DriveItemRef, *, drive_id: str) -> ChangeEvent | None:
        """Translate one envelope to a typed :class:`ChangeEvent`.

        Folder rows are filtered upstream in :func:`_parse_delta_page`;
        items missing both an id and a tombstone flag are dropped here
        (Graph occasionally yields empty markers at sync boundaries).
        """
        if not item.item_id:
            return None
        modified_at = item.last_modified_at or _now_iso()
        if item.removed:
            return ChangeEvent(
                op="deleted",
                item_id=item.item_id,
                modified_at=modified_at,
                metadata={_META_SENSITIVITY_KEY: self._default_sensitivity, "drive_id": drive_id},
            )
        return ChangeEvent(
            op="created",
            item_id=item.item_id,
            modified_at=modified_at,
            metadata={
                _META_SENSITIVITY_KEY: self._default_sensitivity,
                "drive_id": drive_id,
                "name": item.name,
                "mime": item.mime or "",
            },
        )


def _serialise_cursor(per_drive: Mapping[str, str]) -> str:
    """Encode per-drive cursors as a deterministic JSON string."""
    return json.dumps(dict(per_drive), sort_keys=True, ensure_ascii=False)


def _deserialise_cursor(cursor: Cursor | None) -> dict[str, str]:
    """Decode the JSON-encoded per-drive cursor map.

    Tolerant of empty / malformed input — returns an empty dict so a
    cold-start tick (cursor=None) drives a full sweep without crashing
    on a stale legacy single-string cursor.
    """
    if not cursor:
        return {}
    try:
        parsed = json.loads(cursor)
    except (TypeError, ValueError):
        return {}
    if not isinstance(parsed, dict):
        return {}
    return {str(k): str(v) for k, v in parsed.items() if isinstance(v, str)}


def _parse_drive_entry(entry: object) -> SharePointDriveSpec:
    """Parse one operator-config drive entry into a typed spec.

    Extracted from ``_drive_specs_from_config`` to keep that function
    under F16's cognitive-complexity ceiling — the per-entry isinstance
    branching pushed the parent function over 15.
    """
    if isinstance(entry, str) and entry:
        return SharePointDriveSpec(drive_id=entry)
    if isinstance(entry, dict):
        drive_id = entry.get("drive_id")
        if not isinstance(drive_id, str) or not drive_id:
            raise ValueError(
                "sharepoint: drive block missing 'drive_id'. "
                "fix: every drive entry must declare drive_id as a non-empty string. "
                "next: see docs/architecture/connector-ingestion-architecture.md §8."
            )
        site_id = entry.get("site_id") if isinstance(entry.get("site_id"), str) else None
        display = entry.get("display_name") if isinstance(entry.get("display_name"), str) else None
        return SharePointDriveSpec(drive_id=drive_id, site_id=site_id, display_name=display)
    raise ValueError(
        f"sharepoint: drive entry {entry!r} is not a string or dict. "
        "fix: each drive entry must be a drive_id string or a block with drive_id. "
        "next: see docs/architecture/connector-ingestion-architecture.md §8."
    )


def _drive_specs_from_config(raw: object) -> list[SharePointDriveSpec]:
    """Translate operator config drive entries to typed specs.

    Accepts a list of strings (treated as ``drive_id`` only) OR a list
    of dicts with ``drive_id`` plus optional ``site_id`` / ``display_name``
    keys. Anything else raises with a fix pointer so misconfigured
    operators see the contract surface loudly.
    """
    if not isinstance(raw, list) or not raw:
        raise ValueError(
            "sharepoint: 'drives' must be a non-empty list of drive ids or drive blocks. "
            "fix: declare at least one drive under sharepoint -> drives in kairix.config.yaml. "
            "next: see docs/architecture/connector-ingestion-architecture.md §8 "
            "for the SharePoint connector config shape."
        )
    return [_parse_drive_entry(entry) for entry in raw]


def make_connector(config: Mapping[str, Any]) -> SharePointConnector:
    """Construct a :class:`SharePointConnector` from a config mapping.

    Expected keys:

      * ``drives`` (required) — non-empty list of drive specs. Each
        entry is either a drive-id string or a mapping with ``drive_id``
        plus optional ``site_id`` / ``display_name``.
      * ``default_sensitivity`` (optional) — one of the F39 sensitivity
        literals; defaults to ``"internal"``.

    Credentials resolve via :func:`kairix.secrets.get_secret` —
    ``connector-m365-tenant-id`` / ``connector-m365-client-id`` /
    ``connector-m365-client-secret`` must all be set. The same triple
    drives the M365 email-headers + calendar siblings per ADR-019.

    Registered via ``[project.entry-points."kairix.connectors"]`` in
    kairix's ``pyproject.toml`` so the orchestration layer can resolve
    ``sharepoint`` to this factory by name.
    """
    drives = _drive_specs_from_config(config.get("drives"))
    declared = config.get("default_sensitivity", DEFAULT_SENSITIVITY)
    if declared not in ("public", "internal", "client-confidential", "personal"):
        raise ValueError(
            f"sharepoint: default_sensitivity {declared!r} is not a valid F39 tier. "
            "fix: set default_sensitivity to one of "
            "public / internal / client-confidential / personal. "
            "next: see kairix/core/protocols.py Sensitivity for the literal set."
        )
    sensitivity = cast(Sensitivity, declared)
    return SharePointConnector(drives=drives, default_sensitivity=sensitivity)

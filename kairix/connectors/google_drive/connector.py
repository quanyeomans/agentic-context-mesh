"""``GoogleDriveConnector`` — SourceConnector for Google Drive v3.

Implements :class:`kairix.core.protocols.SourceConnector` for one
Google Drive corpus (one OAuth grant = one workspace user's view of
Drive, including shared-with-me files). Change detection rides the
Drive v3 ``/changes`` endpoint:

* First sync — no cursor — calls
  :meth:`GoogleDriveClient.get_start_page_token` to seed, then drains
  the changes endpoint from that token. Every file surfaces as a
  ``created`` :class:`ChangeEvent`.
* Subsequent syncs — cursor is the persisted ``newStartPageToken`` —
  resumes from the stored token. Files surface as ``created`` /
  ``modified`` / ``deleted`` based on the changes payload.

Per ADR-019-derived guidance (provider plugin architecture), the
connector reuses the workspace-wide OAuth credential pattern. The
operator grants ``drive.readonly`` on the configured service-account
(or end-user OAuth grant) for the workspace user whose Drive view the
connector pulls from.

Out of scope for this slice (deferred to follow-up):

* Google-native file export — files whose ``mimeType`` is
  ``application/vnd.google-apps.*`` (Docs / Sheets / Slides) require
  the ``/export`` endpoint instead of ``alt=media``. v1 surfaces the
  native mime to the extractor registry and lets the extractor decide;
  a follow-up slice adds the export step.
* Per-actor sharing-ACL sync — the connector pulls every file the
  configured credential can see, with the operator-declared sensitivity
  tier applied uniformly. Per-file ACL propagation is a Wave-E+1
  enhancement.

Per F35, this module only imports from
``kairix.connectors.google_drive.*`` (same plugin) and
``kairix.core.*`` (the Protocol surface). No reach into other
connectors, no reach into the extractor layer.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, cast

from kairix.connectors.google_drive.client import (
    DriveFileRef,
    GoogleDriveClient,
)
from kairix.core.protocols import (
    ChangeEvent,
    Container,
    Cursor,
    HierarchyNode,
    RawArtefact,
    Sensitivity,
    SourceMetadata,
)

logger = logging.getLogger(__name__)

CONNECTOR_NAME = "google_drive"

# Default sensitivity tier for Google Drive content. Drive document
# corpora are internal-tier by default; operators routing
# client-confidential or personal-tier corpora override via the
# connector config's ``default_sensitivity`` key.
DEFAULT_SENSITIVITY: Sensitivity = "internal"

# Mime hint for files whose changes-list envelope didn't declare one.
DEFAULT_FETCH_MIME = "application/octet-stream"

# Hierarchy root node id for the Wave B shim shape.
_HIERARCHY_ROOT_ID = CONNECTOR_NAME
_HIERARCHY_ROOT_DISPLAY = "Google Drive"

# F17 — metadata key for the sensitivity tier carried on every emitted
# ChangeEvent.
_META_SENSITIVITY_KEY = "sensitivity"


def _now_iso() -> str:
    """Return a current ISO-8601 UTC timestamp matching the connector
    boundary's :class:`ChangeEvent.modified_at` format.
    """
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class GoogleDriveCredentials:
    """Resolved OAuth grant for one Google Drive sync.

    Frozen per F42 — the dataclass is the typed shape that crosses the
    boundary between secret resolution and the connector constructor.
    Tests construct a literal :class:`GoogleDriveCredentials` and pass
    it via the ``credentials`` kwarg; production resolves via
    :func:`kairix.secrets.get_secret` at construction time.

    ``access_token`` is the bearer the client sends as ``Authorization:
    Bearer <token>``. Production resolves a fresh token via the
    out-of-band refresh-token rotation handled by the operator (the
    OAuth refresh dance itself is not in scope for this slice — the
    cc_pair lifecycle transitions to a credential-renewal state on
    401 and the operator rotates the grant).
    """

    access_token: str


@dataclass(frozen=True)
class GoogleDriveCorpusSpec:
    """One configured Drive corpus the connector should sync.

    Frozen per F42. ``corpus_id`` is an operator-chosen identifier that
    distinguishes one Drive corpus from another (e.g. a workspace user
    email, a shared-drive id). v1 supports one corpus per connector
    instance — the spec exists so the multi-container Wave E branch
    has a per-corpus Container shape ready when shared-drive
    enumeration lands.

    ``display_name`` is the operator-facing label; defaults to
    ``corpus_id``.
    """

    corpus_id: str
    display_name: str | None = None


def _default_flag_reader(name: str) -> bool:
    """Production default for the topology-v2-google-drive flag check.

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


def _resolve_credentials_from_secrets() -> GoogleDriveCredentials:
    """Resolve Drive OAuth credentials with auto-refresh on cold-start.

    Per ADR-032 §"Refresh handling (connector-side)" this delegates to
    :func:`kairix.connectors.google_drive.auth.resolve_drive_credentials_with_refresh`
    which reads the full canonical OAuth credential set (client_id +
    client_secret + refresh_token + access_token), mints a fresh
    access_token via the refresh dance, and returns the credentials
    with the fresh bearer.

    Legacy path: if only ``connector-google-drive-access-token`` is set
    (no refresh material), the function preserves the old behaviour —
    returns the static token and the connector raises
    :class:`CredentialExpiredError` on 401 as before.

    F15-clean: the resolved token is captured into the frozen dataclass
    and never logged through any code path in this module.
    """
    from kairix.connectors.google_drive.auth import resolve_drive_credentials_with_refresh

    return resolve_drive_credentials_with_refresh()


class GoogleDriveConnector:
    """SourceConnector for one Google Drive corpus.

    Construction is cheap (no I/O, no OAuth exchange). The first
    :meth:`list_changes` call resolves a start-page-token (cold start)
    or resumes from the stored cursor, then drains the ``/changes``
    endpoint in turn.

    DI seams:

      * ``credentials`` — resolved :class:`GoogleDriveCredentials`.
        Tests pass a literal; production callers omit and the factory
        resolves from :mod:`kairix.secrets`.
      * ``client_builder`` — builds the :class:`GoogleDriveClient`.
        Tests pass a builder returning a client backed by an
        ``httpx.MockTransport`` so no real Drive call leaks.
      * ``default_sensitivity`` — connector-wide F39 tier; defaults to
        ``internal``. Operators set the matching key in
        ``connector_specific_config`` to override.
      * ``flag_reader`` — callable returning bool for a flag name.
        Defaults to the production resolver. Tests inject a fake
        backed by :class:`tests.fakes.FakeFeatureFlagResolver`.
    """

    name: str = CONNECTOR_NAME
    per_tick_max_items: int = 500
    disk_watermark_min_free_bytes: int | None = 5_000_000_000  # 5 GB — Drive binaries can be large

    def __init__(
        self,
        corpora: list[GoogleDriveCorpusSpec],
        *,
        credentials: GoogleDriveCredentials | None = None,
        client_builder: Callable[[GoogleDriveCredentials], GoogleDriveClient] | None = None,
        default_sensitivity: Sensitivity = DEFAULT_SENSITIVITY,
        flag_reader: Callable[[str], bool] = _default_flag_reader,
    ) -> None:
        if not corpora:
            raise ValueError(
                "google_drive: corpora list is empty. "
                "fix: declare at least one corpus_id under the google_drive connector block. "
                "next: see kairix/connectors/google_drive/README.md for the connector config shape."
            )
        self._corpora: tuple[GoogleDriveCorpusSpec, ...] = tuple(corpora)
        self._spec_by_corpus_id: dict[str, GoogleDriveCorpusSpec] = {spec.corpus_id: spec for spec in self._corpora}
        self._default_sensitivity: Sensitivity = default_sensitivity
        self._flag_reader = flag_reader

        resolved = credentials if credentials is not None else _resolve_credentials_from_secrets()
        self._credentials = resolved

        if client_builder is not None:
            self._client = client_builder(resolved)
        else:
            self._client = GoogleDriveClient(access_token=resolved.access_token)

        # Per-item envelope cache — populated by :meth:`list_changes`
        # so :meth:`fetch` / :meth:`source_link` / :meth:`metadata_for`
        # can resolve mime + URL + author without a second Drive call.
        self._cache: dict[str, DriveFileRef] = {}
        # Next-tick cursor — populated after :meth:`list_changes`
        # completes. v1 ships a single cursor for the connector
        # (mirrors the SharePoint legacy shape); the per-container
        # ON branch persists per-Container cursors via the framework
        # topology_containers table directly.
        self._next_cursor: str | None = None

    # ------------------------------------------------------------------
    # SourceConnector Protocol surface (base)
    # ------------------------------------------------------------------

    def list_changes(self, cursor: Cursor | None) -> Iterator[ChangeEvent]:
        """Stream changes for the configured corpus.

        ``cursor`` is the ``newStartPageToken`` persisted on the
        previous tick (or ``None`` for cold start — the connector
        fetches a fresh start-page-token via ``GET /changes/startPageToken``).
        Every visible file in the change window surfaces as a typed
        ChangeEvent.
        """
        events: list[ChangeEvent] = []
        start_token = cursor if isinstance(cursor, str) and cursor else self._client.get_start_page_token()
        for entry in self._client.iter_changes(start_token):
            event = self._entry_to_event(entry)
            if event is None:
                continue
            self._cache[event.item_id] = entry
            events.append(event)
        next_token = self._client.last_new_start_page_token()
        if next_token is not None:
            self._next_cursor = next_token
        elif start_token:
            self._next_cursor = start_token
        return iter(events)

    def fetch(self, item_id: str) -> RawArtefact:
        """Download the binary content for ``item_id``.

        Uses the per-tick envelope cache populated by
        :meth:`list_changes` to resolve the mime hint; raises with a
        fix-pointer when the orchestrator asks for an id outside the
        cache.
        """
        envelope = self._cache.get(item_id)
        if envelope is None:
            raise KeyError(
                f"google_drive: item_id {item_id!r} not in the per-tick envelope cache. "
                "fix: call list_changes() before fetch() so the changes drain "
                "populates the envelope cache before the orchestrator asks for the body. "
                "next: see kairix/core/connectors/pipeline.py for the orchestrator's "
                "list_changes -> fetch contract."
            )
        raw, content_type = self._client.fetch_file_content(envelope.file_id)
        mime = envelope.mime_type or content_type or DEFAULT_FETCH_MIME
        return RawArtefact(raw=raw, mime=mime, fetched_at=_now_iso())

    def source_link(self, item_id: str) -> str:
        """Return the Drive web URL for the cached envelope.

        Falls back to a ``gdrive://`` shape when the envelope didn't
        carry a webViewLink (rare; mostly tombstones).
        """
        envelope = self._cache.get(item_id)
        if envelope is not None and envelope.web_view_link:
            return envelope.web_view_link
        return f"gdrive://files/{item_id}"

    def sensitivity_for(self, _item_id: str) -> Sensitivity:
        """Return the connector-configured default sensitivity tier.

        v1 has no per-item overrides — every file from the connector
        carries the configured tier. A future ADR can read Drive
        sharing-ACL signals and downgrade specific items without
        breaking the Protocol.
        """
        return self._default_sensitivity

    def next_cursor(self) -> str | None:
        """Return the cursor token to persist after the most recent drain."""
        return self._next_cursor

    # ------------------------------------------------------------------
    # ADR-021 (Wave E.5) — per-source envelope metadata
    # ------------------------------------------------------------------

    def metadata_for(self, item_id: str) -> SourceMetadata:
        """Return cached Drive file envelope metadata.

        ADR-021: surfaces ``modifiedTime`` as modified_at,
        ``createdTime`` as created_at,
        ``lastModifyingUser.emailAddress`` as author_email,
        ``lastModifyingUser.displayName`` (falling back to email) as
        author, and the ``mimeType`` / ``webViewLink`` as properties.
        """
        envelope = self._cache.get(item_id)
        if envelope is None:
            return SourceMetadata()
        properties: dict[str, str] = {}
        if envelope.name:
            properties["name"] = envelope.name
        if envelope.mime_type:
            properties["mime_type"] = envelope.mime_type
        if envelope.web_view_link:
            properties["web_view_link"] = envelope.web_view_link
        author = envelope.last_modifying_user_name or envelope.last_modifying_user_email
        return SourceMetadata(
            modified_at=envelope.modified_time,
            created_at=envelope.created_time,
            author=author,
            author_email=envelope.last_modifying_user_email,
            tags=envelope.owner_emails,
            properties=properties,
        )

    # ------------------------------------------------------------------
    # Topology v2 Wave E — per-connector multi-container pilot
    # ------------------------------------------------------------------

    def iter_containers(self, cc_pair_id: int) -> Iterator[Container]:
        """Yield one :class:`Container` per configured Drive corpus.

        v1 supports one corpus per connector instance so this typically
        emits exactly one Container; the per-corpus shape mirrors the
        sibling Wave E pilots (SharePoint per-drive, m365_calendar
        per-calendar) so the framework can route uniformly.
        """
        for spec in self._corpora:
            yield Container(
                cc_pair_id=cc_pair_id,
                container_id=spec.corpus_id,
                access_state="ACCESSIBLE",
                cursor_token=None,
                last_synced_at=None,
            )

    def list_changes_for_container(self, container: Container) -> Iterator[ChangeEvent]:
        """Stream changes for one container's Drive corpus.

        Reads ``container.cursor_token`` as the per-corpus
        newStartPageToken (None on first sync) and walks the changes
        pages scoped to that corpus only. Per-corpus isolation means
        adding or removing one corpus does not affect the cursor state
        of the others.

        ``topology_v2_google_drive`` retired post-cutover (task #132);
        the per-corpus path is now the only behaviour.
        """
        return self._list_changes_for_container_scoped(container)

    def load_hierarchy(self, cc_pair_id: int) -> Iterator[HierarchyNode]:
        """HierarchyConnector — emit nodes parent-before-child per F58.

        Emits a root FOLDER node (``raw_node_id="google_drive"``,
        ``raw_parent_id=None``) followed by one FOLDER child per
        configured corpus.

        ``topology_v2_google_drive`` retired post-cutover (task #132);
        the root + per-corpus emission is now the only behaviour.
        """
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
        for spec in self._corpora:
            yield HierarchyNode(
                cc_pair_id=cc_pair_id,
                raw_node_id=spec.corpus_id,
                raw_parent_id=_HIERARCHY_ROOT_ID,
                display_name=spec.display_name or spec.corpus_id,
                link=None,
                node_type="FOLDER",
                external_access_json=None,
                sensitivity_hint=None,
            )

    def retrieve_all_slim_docs(self, container: Container) -> Iterator[str]:
        """SlimConnector — id-only enumeration for the prune cycle.

        Drains the changes endpoint for the container's corpus and
        emits only the ``file_id`` strings. Filters tombstones
        (removed items) out because the prune cycle is asking "what
        ids does the source still have?".
        """
        start_token = container.cursor_token or self._client.get_start_page_token()
        for entry in self._client.iter_changes(start_token):
            if not entry.file_id or entry.removed:
                continue
            yield entry.file_id

    def reindex(
        self,
        failed_item_ids: tuple[str, ...],
        *,
        include_permissions: bool = False,
    ) -> Iterator[ChangeEvent]:
        """Resolver — per-item failure replay.

        Cheaper than re-running a changes window after a partial-fetch
        failure: yields one :class:`ChangeEvent` per id in
        ``failed_item_ids`` so the orchestrator can re-drive the
        downstream pipeline against ONLY the items that failed.

        Filters duplicate ids and empty strings so the orchestrator's
        deadletter table can safely feed the raw tuple without
        pre-cleaning.
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
    # Internals
    # ------------------------------------------------------------------

    def _list_changes_for_container_scoped(self, container: Container) -> Iterator[ChangeEvent]:
        """Wave E ON-branch: drain Drive changes for one container's corpus only.

        Reads the container's own ``cursor_token`` (the per-corpus
        ``newStartPageToken``) and walks the changes pages. Each
        container's cursor is read independently — adding or removing
        one corpus does not disturb another corpus's resume position.
        """
        start_token = container.cursor_token or self._client.get_start_page_token()
        events: list[ChangeEvent] = []
        for entry in self._client.iter_changes(start_token):
            event = self._entry_to_event(entry)
            if event is None:
                continue
            self._cache[event.item_id] = entry
            events.append(event)
        return iter(events)

    def _entry_to_event(self, entry: DriveFileRef) -> ChangeEvent | None:
        """Translate one envelope to a typed :class:`ChangeEvent`.

        Files missing an id are dropped (Drive occasionally yields
        empty markers). Removed files emit as ``deleted`` ops; new /
        modified files emit as ``created`` ops (Drive's changes endpoint
        does not distinguish between create and modify — both surface
        as a fresh changelog entry).
        """
        if not entry.file_id:
            return None
        modified_at = entry.modified_time or _now_iso()
        if entry.removed:
            return ChangeEvent(
                op="deleted",
                item_id=entry.file_id,
                modified_at=modified_at,
                metadata={
                    _META_SENSITIVITY_KEY: self._default_sensitivity,
                    "corpus_id": self._corpora[0].corpus_id if self._corpora else "",
                },
            )
        return ChangeEvent(
            op="created",
            item_id=entry.file_id,
            modified_at=modified_at,
            metadata={
                _META_SENSITIVITY_KEY: self._default_sensitivity,
                "corpus_id": self._corpora[0].corpus_id if self._corpora else "",
                "name": entry.name,
                "mime": entry.mime_type or "",
            },
        )


def _corpus_specs_from_config(raw: object) -> list[GoogleDriveCorpusSpec]:
    """Translate operator config corpus entries to typed specs.

    Accepts a list of strings (treated as ``corpus_id`` only) OR a list
    of dicts with ``corpus_id`` plus optional ``display_name`` keys.
    Anything else raises with a fix pointer so misconfigured operators
    see the contract surface loudly.
    """
    if not isinstance(raw, list) or not raw:
        raise ValueError(
            "google_drive: 'corpora' must be a non-empty list of corpus ids or corpus blocks. "
            "fix: declare at least one corpus under google_drive -> corpora in kairix.config.yaml. "
            "next: see kairix/connectors/google_drive/README.md for the connector config shape."
        )
    out: list[GoogleDriveCorpusSpec] = []
    for entry in raw:
        if isinstance(entry, str) and entry:
            out.append(GoogleDriveCorpusSpec(corpus_id=entry))
            continue
        if isinstance(entry, dict):
            corpus_id = entry.get("corpus_id")
            if not isinstance(corpus_id, str) or not corpus_id:
                raise ValueError(
                    "google_drive: corpus block missing 'corpus_id'. "
                    "fix: every corpus entry must declare corpus_id as a non-empty string. "
                    "next: see kairix/connectors/google_drive/README.md for the config shape."
                )
            display = entry.get("display_name") if isinstance(entry.get("display_name"), str) else None
            out.append(GoogleDriveCorpusSpec(corpus_id=corpus_id, display_name=display))
            continue
        raise ValueError(
            f"google_drive: corpus entry {entry!r} is not a string or dict. "
            "fix: each corpus entry must be a corpus_id string or a block with corpus_id. "
            "next: see kairix/connectors/google_drive/README.md for the config shape."
        )
    return out


def make_connector(config: Mapping[str, Any]) -> GoogleDriveConnector:
    """Construct a :class:`GoogleDriveConnector` from a config mapping.

    Expected keys:

      * ``corpora`` (required) — non-empty list of corpus specs. Each
        entry is either a corpus-id string or a mapping with
        ``corpus_id`` plus optional ``display_name``.
      * ``default_sensitivity`` (optional) — one of the F39 sensitivity
        literals; defaults to ``"internal"``.

    Credentials resolve via :func:`kairix.secrets.get_secret` —
    ``connector-google-drive-access-token`` must be set.

    Registered via ``[project.entry-points."kairix.connectors"]`` in
    kairix's ``pyproject.toml`` so the orchestration layer can resolve
    ``google_drive`` to this factory by name.
    """
    corpora = _corpus_specs_from_config(config.get("corpora"))
    declared = config.get("default_sensitivity", DEFAULT_SENSITIVITY)
    if declared not in ("public", "internal", "client-confidential", "personal"):
        raise ValueError(
            f"google_drive: default_sensitivity {declared!r} is not a valid F39 tier. "
            "fix: set default_sensitivity to one of "
            "public / internal / client-confidential / personal. "
            "next: see kairix/core/protocols.py Sensitivity for the literal set."
        )
    sensitivity = cast(Sensitivity, declared)
    return GoogleDriveConnector(corpora=corpora, default_sensitivity=sensitivity)

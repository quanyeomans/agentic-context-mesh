"""``DexCrmConnector`` — SourceConnector for the Dex CRM API.

Implements the :class:`kairix.core.protocols.SourceConnector` Protocol
for the Dex CRM (https://getdex.com) — pulls contacts (Person nodes),
organisations (Org nodes), and contact-organisation relationships
(graph edges) into the kairix ingest path. The Silver processor lifts
those into :class:`~kairix.core.protocols.EntitySignal` rows staged in
the SQLite ``entity_signals`` table (per ADR-018); a separate worker
job pushes the staged signals to Neo4j asynchronously.

Cursor: the Dex API supports filtering by ``updated_after`` (ISO-8601
UTC last-modified timestamp). The connector stores the timestamp of
the most recent processed record as the cursor token, so subsequent
:meth:`list_changes` calls fetch only the delta since the last tick.

Per F35 the module only imports from itself plus ``kairix.core.*``
(Protocol surface) and ``kairix.transport.auth.*`` (reusable auth
helper). No reach into another connector tree.

When the ``connector-dex-api-key`` secret is unset the connector still
constructs OK — operator misconfiguration shouldn't crash kairix at
module import. The first :meth:`list_changes` call surfaces a typed
:class:`~kairix.transport.auth.api_key.MissingCredentialsError` with an
actionable ``fix:`` message pointing at the secret-loading runbook.

F15-clean: this module never logs the API key or any bearer token in
plaintext; diagnostic logs name the endpoint or record kind only.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote

from kairix.connectors.dex_crm.client import (
    DEFAULT_SECRET_NAME,
    DexCrmClient,
    DexCrmClientConfig,
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
from kairix.secrets import Scope, SecretsResolver
from kairix.transport.auth.api_key import ApiKeyAuth, BearerHeaders, MissingCredentialsError

CONNECTOR_NAME = "dex_crm"

# Canonical secret identity tuple for the dex_crm connector's bearer
# API key. The legacy alias map maps this to ``CONNECTOR_DEX_API_KEY``
# so existing operator deployments keep working unchanged through the
# loader's alias-fallback path.
_SECRET_SCOPE_API_KEY: tuple[Scope, str, str | None, str] = ("connector", "dex", None, "api-key")

# Hierarchy node identifiers for the Wave E ON-branch ``load_hierarchy``
# emission. The root carries ``raw_node_id="dex"`` (the connector kind)
# with one FOLDER child per top-level entity type the connector polls.
# Module-level so the BDD step + integration test reference the exact
# canonical literal — drift here would break the F58 invariant test
# silently rather than at edit time.
_HIERARCHY_ROOT_ID = "dex"
_HIERARCHY_PERSON_ID = "dex/person"
_HIERARCHY_ORG_ID = "dex/organisation"
_HIERARCHY_RELATIONSHIP_ID = "dex/relationship"

# Dex web-UI base used by both ``source_link`` (deep-link routing per
# record) and the Wave E ``load_hierarchy`` walk (per-FOLDER link).
# Extracted to a single F17-clean constant so a rename of the customer
# portal hostname is a one-edit operation.
_DEX_UI_BASE = "https://app.getdex.com/"

# Dex listing endpoint names (plural — the API's wire form) and the
# singular kind tag the connector emits in ``item_id`` / ``source_link``.
# Module-level constants because each string appears in ≥3 sites
# (record-kind tuple, normalise-singular map, source-link router); a
# rename of the wire surface should be a one-edit operation.
_LISTING_CONTACTS = "contacts"
_LISTING_ORGANISATIONS = "organisations"
_LISTING_RELATIONSHIPS = "relationships"
_KIND_CONTACT = "contact"
_KIND_ORGANISATION = "organisation"
_KIND_RELATIONSHIP = "relationship"

# Record kinds the connector polls. Order matters for deterministic
# event interleave: contacts first (Person), organisations second
# (Org), relationships last (graph edges that reference Person + Org
# entities). The Silver processor downstream is order-tolerant; this
# ordering simplifies the test expectations.
_RECORD_KINDS: tuple[str, ...] = (_LISTING_CONTACTS, _LISTING_ORGANISATIONS, _LISTING_RELATIONSHIPS)


def _iso_utc(dt: datetime) -> str:
    """Render ``dt`` as an ISO-8601 UTC string with trailing ``Z``."""
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _now_iso() -> str:
    return _iso_utc(datetime.now(timezone.utc))


@dataclass(frozen=True)
class _PreBoundApiKeyAuth(ApiKeyAuth):
    """:class:`ApiKeyAuth` subclass bound to a pre-resolved bearer token.

    The base :class:`ApiKeyAuth` resolves its secret on every
    :meth:`headers` call via :func:`kairix.secrets.get_secret`. When the
    connector has already resolved the api key via the canonical
    :class:`SecretsLoader`, this subclass surfaces the value directly so
    the per-request path never re-walks the resolver chain.

    Frozen dataclass — the bound token is held as a field rather than
    on a mutable attribute, so the auth instance stays safely shareable
    across threads. F15 is preserved: the token never appears in logs;
    only the secret-slot name is referenced in diagnostic output.
    """

    api_key: str = ""

    def headers(self, _secret_name: str) -> BearerHeaders:
        """Return the pre-bound Bearer header — never re-walks the resolver chain."""
        # The secret_name kwarg is accepted for Protocol-shape compatibility
        # with the base ApiKeyAuth but is intentionally unused: the token is
        # already bound at construction time via the canonical SecretsLoader,
        # so re-walking the resolver chain on every request would be redundant
        # work.
        return BearerHeaders(mapping={"Authorization": f"Bearer {self.api_key}"})


@dataclass(frozen=True)
class DexCrmRecord:
    """One record fetched from the Dex API, kind-tagged.

    Frozen-dataclass per F42 — the typed-boundary representation the
    connector internally carries between ``list_changes`` and
    ``fetch``. ``kind`` is one of ``contact`` / ``organisation`` /
    ``relationship``; the connector's item_id encodes the kind so a
    single ``fetch(item_id)`` call resolves to the right record type.

    ``raw`` is the original Dex payload as a frozen mapping so the
    downstream extractor (passthrough) can lift the JSON into a chunk
    body without forcing the connector to pre-render it.
    """

    kind: str
    item_id: str
    modified_at: str
    raw: Mapping[str, Any]


@dataclass(frozen=True)
class _CursorTimestamps:
    """Per-kind tracked cursor timestamps.

    The Dex API has three listing endpoints (contacts / organisations /
    relationships); each advances independently. The persisted cursor
    token is the minimum across kinds, so a worker restart never
    regresses any single endpoint.
    """

    contacts: str | None
    organisations: str | None
    relationships: str | None


class DexCrmConnector:
    """SourceConnector implementation for the Dex CRM API.

    Construction is cheap (no I/O, no auth resolution). The first
    :meth:`list_changes` call triggers the Bearer-header resolution via
    :class:`kairix.transport.auth.api_key.ApiKeyAuth`. If the secret is
    unset the call raises :class:`MissingCredentialsError` carrying a
    ``fix:`` message — the connector itself never crashes at import
    time, only at the first attempted poll.

    DI seams (all keyword arguments with real defaults — F6-clean):

      * ``client`` — :class:`DexCrmClient`; tests pass a recording
        stand-in that drives :class:`httpx.MockTransport` so the suite
        never touches the public Dex API.
      * ``client_config`` — :class:`DexCrmClientConfig`; controls the
        base URL, secret name, page size, and rate-limit cadence.
      * ``sensitivity`` — :class:`Sensitivity` tier; defaults to
        ``"internal"`` per ADR-005's "CRM data is internal by default".
        Operators can opt to ``"client-confidential"`` via
        ``connectors[].sensitivity`` in ``kairix.config.yaml``.
      * ``secrets`` — optional :class:`SecretsResolver` injection seam.
        When set AND no ``client`` is supplied, the connector eagerly
        resolves the canonical ``connector/dex/-/api-key`` identity
        tuple and wires a pre-bound :class:`ApiKeyAuth` into the
        default :class:`DexCrmClient`. This routes credential reads
        through the canonical secrets surface (the loader's
        env -> legacy aliases -> KV mount -> legacy chain) so the
        connector's __init__ has one cred surface, not two. Tests
        inject :class:`tests.fakes.FakeSecretsLoader` so no env-var
        monkey-patching is needed (F2-clean).
    """

    name: str = CONNECTOR_NAME
    per_tick_max_items: int = 500
    # F66-watermark-exempt: Dex CRM emits small JSON envelopes; no large fetched payloads
    disk_watermark_min_free_bytes: int | None = None

    def __init__(
        self,
        *,
        client: DexCrmClient | None = None,
        client_config: DexCrmClientConfig | None = None,
        sensitivity: Sensitivity = "internal",
        secrets: SecretsResolver | None = None,
    ) -> None:
        cfg = client_config if client_config is not None else DexCrmClientConfig()
        if client is not None:
            # Explicit client wins: tests pass a recording stand-in here,
            # and production-style callers that want full control of the
            # transport surface own the auth wiring themselves.
            self._client = client
        elif secrets is not None:
            # Canonical surface: eagerly resolve via the loader and bind
            # the bearer into a pre-bound ApiKeyAuth so the client's
            # per-request `.headers(...)` call never re-walks the legacy
            # chain. The SecretsLoader's resolution chain
            # (env -> legacy aliases -> KV mount -> legacy chain) means
            # production deployments keep working through the alias
            # fallback. Translate SecretNotFoundError to the connector's
            # typed MissingCredentialsError so the worker dead-letter
            # surface stays uniform across the auth-error shape (the F21
            # fix/next markers ride along).
            from kairix.secrets import SecretNotFoundError

            try:
                api_key = secrets.require(*_SECRET_SCOPE_API_KEY)
            except SecretNotFoundError as exc:
                raise MissingCredentialsError(
                    "dex_crm: api-key secret is not configured. "
                    "fix: set the secret via the configured resolver chain "
                    "(env var KAIRIX_CONNECTOR_DEX_API_KEY, per-file secret, "
                    "sidecar bundle, or Azure Key Vault). "
                    "next: see docs/operations/OPERATIONS.md for the secret-loading runbook."
                ) from exc
            self._client = DexCrmClient(config=cfg, auth=_PreBoundApiKeyAuth(api_key=api_key))
        else:
            # Legacy path — no loader injected, keep historic behaviour
            # so callers that don't yet thread the loader through (tests,
            # downstream code mid-migration) keep working.
            self._client = DexCrmClient(config=cfg)
        self._sensitivity: Sensitivity = sensitivity
        # Cache of last-fetched records keyed by item_id so ``fetch``
        # can return the bytes the connector already pulled in
        # ``list_changes`` without a second API roundtrip.
        self._record_cache: dict[str, DexCrmRecord] = {}
        # High-water-mark ISO timestamp from the most recent
        # :meth:`list_changes` drain — returned by :meth:`next_cursor`
        # so the orchestrator persists the correct cursor token. None
        # before the first drain; preserves the prior value on a
        # zero-event tick so we don't clobber a real cursor with None.
        self._last_max_modified_at: str | None = None

    # ------------------------------------------------------------------
    # SourceConnector Protocol surface
    # ------------------------------------------------------------------

    def list_changes(self, cursor: Cursor | None) -> Iterator[ChangeEvent]:
        """Stream change events since ``cursor`` across all three Dex listings.

        ``cursor`` is an ISO-8601 timestamp; the connector passes it
        through as the ``updated_after`` query param so the Dex API
        returns only records modified since that point. The first call
        (``cursor is None``) pulls the full backlog — operators should
        expect a longer initial sync.

        Surfaces :class:`MissingCredentialsError` from the auth layer
        unchanged so the worker dead-letter path catches it the same
        way it catches any other connector-side failure.
        """
        updated_after = cursor if cursor else None
        events: list[ChangeEvent] = []
        self._record_cache.clear()

        for kind in _RECORD_KINDS:
            for raw_record in self._client.iter_listing(kind, updated_after):
                record = self._normalise(kind, raw_record)
                if record is None:
                    continue
                # Skip records older than the cursor — defence-in-depth
                # against a Dex API quirk where ``updated_after`` is
                # exclusive on one endpoint and inclusive on another.
                if cursor is not None and record.modified_at <= cursor:
                    continue
                self._record_cache[record.item_id] = record
                events.append(
                    ChangeEvent(
                        op="modified",
                        item_id=record.item_id,
                        modified_at=record.modified_at,
                    )
                )
        # Track high-water-mark for next_cursor(); preserve prior cursor
        # on a zero-event drain so the orchestrator's commit-and-flush
        # doesn't clobber a real cursor with None.
        if events:
            self._last_max_modified_at = max(ev.modified_at for ev in events)
        elif cursor:
            self._last_max_modified_at = cursor
        # else: keep self._last_max_modified_at as-is
        return iter(events)

    def next_cursor(self) -> str | None:
        """Return the ISO-8601 high-water-mark from the most recent drain.

        Dex CRM's cursor is the ``updated_after`` ISO timestamp passed
        to its REST API; the orchestrator persists this between ticks
        via :meth:`ConnectorPipeline._commit_and_flush`. ``None``
        before the first :meth:`list_changes` call.
        """
        return self._last_max_modified_at

    def fetch(self, item_id: str) -> RawArtefact:
        """Return the raw record bytes for ``item_id``.

        Uses the in-memory cache populated by :meth:`list_changes`. If
        the cache miss occurs (e.g. ``fetch`` called without a prior
        ``list_changes``), raises ``KeyError`` — the worker treats this
        as a programmer error rather than a transient API failure.

        Returns the record's JSON payload UTF-8-encoded with mime
        ``application/json`` so the passthrough extractor lifts it into
        a chunk body that downstream BM25 / vector indexing can read.
        """
        record = self._record_cache.get(item_id)
        if record is None:
            raise KeyError(
                f"dex_crm: no cached record for item_id {item_id!r}. "
                "fix: call list_changes() before fetch() — the connector "
                "caches the page records emitted from list_changes. "
                "next: see kairix/connectors/dex_crm/connector.py for the cache contract."
            )
        import json

        raw_bytes = json.dumps(dict(record.raw), sort_keys=True, ensure_ascii=False).encode("utf-8")
        return RawArtefact(raw=raw_bytes, mime="application/json", fetched_at=_now_iso())

    def source_link(self, item_id: str) -> str:
        """Build a deep-link back into the Dex web UI for ``item_id``.

        The item_id encodes ``<kind>:<id>``; the deep-link routes by
        kind so operators clicking through from a search result land on
        the correct Dex page (contact / organisation / relationship).
        """
        if ":" not in item_id:
            return f"{_DEX_UI_BASE}{_LISTING_CONTACTS}/{quote(item_id, safe='')}"
        kind, raw_id = item_id.split(":", 1)
        path = {
            _KIND_CONTACT: _LISTING_CONTACTS,
            _KIND_ORGANISATION: _LISTING_ORGANISATIONS,
            _KIND_RELATIONSHIP: _LISTING_RELATIONSHIPS,
        }.get(kind, _LISTING_CONTACTS)
        return f"{_DEX_UI_BASE}{path}/{quote(raw_id, safe='')}"

    def sensitivity_for(self, _item_id: str) -> Sensitivity:
        """Return the connector's configured sensitivity tier.

        v1 has no per-record overrides — the operator picks one tier
        per connector config block. CRM data defaults to ``"internal"``
        per ADR-005; ``"client-confidential"`` is the typical opt-up
        for engagements where the CRM contains active-client contact
        records.
        """
        return self._sensitivity

    # ------------------------------------------------------------------
    # Topology v2 Wave B — capability mix-in shims (no behavioural change)
    # ------------------------------------------------------------------
    # The shims below let the connector satisfy the new capability
    # Protocols (PollConnector, CredentialsConnector) by delegating to
    # existing methods. Production routing through these methods is
    # gated by the ``topology_protocol`` feature flag (default-off);
    # Wave C activates the runtime path.

    def load_credentials(self, credentials: dict[str, Any]) -> dict[str, Any] | None:
        """CredentialsConnector shim — return the input unchanged.

        Dex auth is API-key only — no transformation, no derived
        secrets, no token exchange. The shim returns the input mapping
        as-is so the framework's credential-loading pass remains a
        no-op for this connector.
        """
        return credentials

    # ------------------------------------------------------------------
    # Topology v2 Wave E — per-connector multi-container pilot
    # ------------------------------------------------------------------
    # The Dex CRM API is single-tenant single-cursor today — there is no
    # per-organisation delta endpoint, so iter_containers emits ONE
    # Container with ``container_id=""`` representing the connector
    # instance as a whole. That single-container shape is still a
    # meaningful Wave E adoption: it threads the cc_pair's persisted
    # cursor through Wave C's CollectionRouter rather than the legacy
    # connector-wide cursor path, which the obsidian pilot already
    # validates.
    #
    # When the ``topology_dex_crm`` flag is ON:
    #   * :meth:`iter_containers` yields one Container (the tenant).
    #   * :meth:`list_changes_for_container` reads
    #     ``container.cursor_token`` as the per-container cursor and
    #     threads it through :meth:`list_changes`. Each Container's
    #     cursor is consumed independently — no shared mutable state.
    #   * :meth:`load_hierarchy` emits one root FOLDER (``dex``) with
    #     one FOLDER child per top-level entity type (Person, Org,
    #     Relationship) parent-before-child per F58.
    #
    # When OFF:
    #   * :meth:`list_changes_for_container` retains the Wave B shim
    #     shape (delegate to legacy single-cursor :meth:`list_changes`
    #     using the container's cursor).
    #   * :meth:`load_hierarchy` emits one root FOLDER node only.
    #
    # The flag defaults OFF so existing operators see bit-for-bit
    # current behaviour.

    def iter_containers(self, cc_pair_id: int) -> Iterator[Container]:
        """Yield one :class:`Container` representing the Dex tenant.

        Dex's API is single-tenant single-cursor — there is no
        per-organisation delta endpoint, so the connector emits a
        single Container with ``container_id=""``. The Wave C
        ``CollectionRouter`` still benefits because the cc_pair's
        cursor now flows through the per-container Container row in
        ``topology_containers`` rather than the legacy connector-wide
        cursor table.

        ``access_state`` is always ``"ACCESSIBLE"`` — once the API key
        resolves, the whole tenant surface is reachable;
        permission-denied scoping happens per-record inside the Dex
        product, not at the listing-endpoint boundary.
        ``cursor_token`` and ``last_synced_at`` start ``None``; the
        framework persists subsequent values to the
        ``topology_containers`` table.
        """
        yield Container(
            cc_pair_id=cc_pair_id,
            container_id="",
            access_state="ACCESSIBLE",
            cursor_token=None,
            last_synced_at=None,
        )

    def list_changes_for_container(self, container: Container) -> Iterator[ChangeEvent]:
        """Stream change events for one Container's cursor horizon.

        Reads ``container.cursor_token`` as the per-container delta
        horizon and threads it through :meth:`list_changes`. Each call
        uses ONLY the supplied Container's cursor — no shared / cached /
        connector-level cursor leaks across containers.

        ``topology_dex_crm`` retired post-cutover (task #132); the
        per-container path is now the only behaviour.
        """
        return self._list_changes_scoped(container)

    def load_hierarchy(self, cc_pair_id: int) -> Iterator[HierarchyNode]:
        """HierarchyConnector — emit FOLDER nodes parent-before-child.

        Emits one root FOLDER (``raw_node_id="dex"``, ``raw_parent_id=None``)
        followed by one FOLDER child per top-level Dex entity type —
        Person, Organisation, Relationship — each carrying
        ``raw_parent_id="dex"`` so the F58 parent-before-child invariant
        holds. The display names mirror the Dex web-UI tab labels
        operators see, so a search-layer client surfacing the hierarchy
        can render recognisable folder breadcrumbs.

        ``link`` references the Dex web-UI tab for the root + each
        child; the search layer can surface a clickable affordance
        directly to the operator's tenant. ``sensitivity_hint`` is
        ``None`` because Dex sensitivity is connector-configured, not
        per-folder (per ADR-005).

        ``topology_dex_crm`` retired post-cutover (task #132); the
        per-entity-type walk is now the only behaviour.
        """
        yield from _walk_hierarchy(cc_pair_id=cc_pair_id)

    # ------------------------------------------------------------------
    # Wave E internals
    # ------------------------------------------------------------------

    def _list_changes_scoped(self, container: Container) -> Iterator[ChangeEvent]:
        """Wave E ON-branch: thread the per-container cursor through list_changes.

        Reads ``container.cursor_token`` and ONLY that container's
        cursor — never a shared module-level cursor, never a connector-
        instance cursor cache. The single-tenant Dex API maps to a
        single Container today, but the per-container cursor read makes
        the wire-call shape identical for a hypothetical multi-tenant
        evolution where each Container carries its own ``updated_after``
        timestamp.
        """
        cursor = container.cursor_token
        return self.list_changes(cursor)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _normalise(self, kind: str, raw: Mapping[str, Any]) -> DexCrmRecord | None:
        """Translate one raw Dex API record into a :class:`DexCrmRecord`.

        Returns ``None`` when the record is missing the minimum fields
        (``id`` and ``updated_at``) — defends against partial API
        responses where the Dex backend may emit a placeholder shape.
        """
        record_id = raw.get("id")
        updated_at = raw.get("updated_at")
        if not isinstance(record_id, str) or not isinstance(updated_at, str):
            return None
        # Strip plural suffix so item_id is canonical (contact, not
        # contacts) regardless of the listing endpoint kind.
        singular = {
            _LISTING_CONTACTS: _KIND_CONTACT,
            _LISTING_ORGANISATIONS: _KIND_ORGANISATION,
            _LISTING_RELATIONSHIPS: _KIND_RELATIONSHIP,
        }.get(kind, kind)
        item_id = f"{singular}:{record_id}"
        return DexCrmRecord(
            kind=singular,
            item_id=item_id,
            modified_at=updated_at,
            raw=raw,
        )

    # ------------------------------------------------------------------
    # ADR-021 (Wave E.5) — per-source envelope metadata
    # ------------------------------------------------------------------

    def metadata_for(self, item_id: str) -> SourceMetadata:
        """Return the cached Dex record's envelope metadata.

        ADR-021: the record cache populated by :meth:`list_changes`
        already carries the raw Dex envelope; we lift
        ``created_by`` / ``modified_by`` / ``created_at`` /
        ``updated_at`` / ``tags`` from it. Cache miss collapses to an
        empty :class:`SourceMetadata` — the next tick will refresh the
        cache.
        """
        record = self._record_cache.get(item_id)
        if record is None:
            return SourceMetadata()
        raw = record.raw
        author_value = raw.get("modified_by") or raw.get("created_by")
        author = author_value if isinstance(author_value, str) and author_value.strip() else None
        created_at_value = raw.get("created_at")
        created_at = created_at_value if isinstance(created_at_value, str) and created_at_value.strip() else None
        modified_at_value = raw.get("updated_at") or record.modified_at
        modified_at = modified_at_value if isinstance(modified_at_value, str) and modified_at_value.strip() else None
        raw_tags = raw.get("tags")
        if isinstance(raw_tags, list):
            tags = tuple(str(t) for t in raw_tags if isinstance(t, str) and t.strip())
        elif isinstance(raw_tags, str) and raw_tags.strip():
            tags = (raw_tags.strip(),)
        else:
            tags = ()
        return SourceMetadata(
            modified_at=modified_at,
            created_at=created_at,
            author=author,
            tags=tags,
            properties={"kind": record.kind},
        )


def _walk_hierarchy(*, cc_pair_id: int) -> Iterator[HierarchyNode]:
    """Wave E ON-branch hierarchy walk for the Dex CRM connector.

    Emits one root FOLDER node followed by one FOLDER child per top-
    level Dex entity type (Person, Organisation, Relationship) in
    deterministic parent-before-child order per F58. Lifted to a
    module-level helper so the BDD step + integration test can
    reference the canonical walk shape without reaching into the
    connector class internals.
    """
    yield HierarchyNode(
        cc_pair_id=cc_pair_id,
        raw_node_id=_HIERARCHY_ROOT_ID,
        raw_parent_id=None,
        display_name="Dex CRM",
        link=_DEX_UI_BASE,
        node_type="FOLDER",
        external_access_json=None,
        sensitivity_hint=None,
    )
    yield HierarchyNode(
        cc_pair_id=cc_pair_id,
        raw_node_id=_HIERARCHY_PERSON_ID,
        raw_parent_id=_HIERARCHY_ROOT_ID,
        display_name="Person",
        link=f"{_DEX_UI_BASE}{_LISTING_CONTACTS}",
        node_type="FOLDER",
        external_access_json=None,
        sensitivity_hint=None,
    )
    yield HierarchyNode(
        cc_pair_id=cc_pair_id,
        raw_node_id=_HIERARCHY_ORG_ID,
        raw_parent_id=_HIERARCHY_ROOT_ID,
        display_name="Organisation",
        link=f"{_DEX_UI_BASE}{_LISTING_ORGANISATIONS}",
        node_type="FOLDER",
        external_access_json=None,
        sensitivity_hint=None,
    )
    yield HierarchyNode(
        cc_pair_id=cc_pair_id,
        raw_node_id=_HIERARCHY_RELATIONSHIP_ID,
        raw_parent_id=_HIERARCHY_ROOT_ID,
        display_name="Relationship",
        link=f"{_DEX_UI_BASE}{_LISTING_RELATIONSHIPS}",
        node_type="FOLDER",
        external_access_json=None,
        sensitivity_hint=None,
    )


def make_connector(
    config: Mapping[str, Any],
    *,
    secrets: SecretsResolver | None = None,
) -> DexCrmConnector:
    """Construct a :class:`DexCrmConnector` from a config mapping.

    Expected keys (all optional with sensible defaults so the simplest
    operator config is ``- name: dex_crm``):

      * ``base_url`` — Dex API base URL; defaults to
        ``https://api.prod.getdex.com/v1``.
      * ``secret_name`` — logical secret slot for the API key; defaults
        to ``connector-dex-api-key``. Only consulted on the legacy
        client path (when ``secrets`` is ``None`` AND the operator
        hasn't configured the loader chain); the canonical surface
        keys on ``connector/dex/-/api-key``.
      * ``page_size`` — pagination size; defaults to 100.
      * ``rate_limit_sleep_s`` — inter-request pause in seconds;
        defaults to 1.0 (conservative 1 req/sec).
      * ``sensitivity`` — one of the F39 sensitivity literals; defaults
        to ``"internal"`` per ADR-005.

    Args:
      config: operator-supplied connector config mapping.
      secrets: optional :class:`SecretsResolver` injection seam. When
        ``None`` (production default), a :class:`SecretsLoader` is
        constructed lazily — its env / KV mount / legacy alias chain
        resolves the api-key. Tests inject
        :class:`tests.fakes.FakeSecretsLoader` so no env-var monkey-
        patching is needed (F2-clean).

    Registered via ``[project.entry-points."kairix.connectors"]`` in
    kairix's ``pyproject.toml`` so the orchestration layer resolves
    ``dex_crm`` to this factory by name.
    """
    cfg = DexCrmClientConfig(
        base_url=str(config.get("base_url", DexCrmClientConfig.base_url)),
        secret_name=str(config.get("secret_name", DEFAULT_SECRET_NAME)),
        page_size=int(config.get("page_size", DexCrmClientConfig.page_size)),
        timeout_s=float(config.get("timeout_s", DexCrmClientConfig.timeout_s)),
        max_retries=int(config.get("max_retries", DexCrmClientConfig.max_retries)),
        backoff_base_s=float(config.get("backoff_base_s", DexCrmClientConfig.backoff_base_s)),
        rate_limit_sleep_s=float(config.get("rate_limit_sleep_s", DexCrmClientConfig.rate_limit_sleep_s)),
    )
    sensitivity: Sensitivity = config.get("sensitivity", "internal")
    # When `secrets` is supplied, resolve via the canonical surface
    # eagerly at construction — operator misconfiguration surfaces
    # immediately rather than at first poll. When `secrets` is None
    # (default), the connector retains its historic lazy-resolution
    # contract: construction is cheap, the api-key resolves on first
    # `list_changes()` via the legacy :class:`ApiKeyAuth` path.
    return DexCrmConnector(client_config=cfg, sensitivity=sensitivity, secrets=secrets)


__all__ = [
    "CONNECTOR_NAME",
    "DexCrmConnector",
    "DexCrmRecord",
    "MissingCredentialsError",
    "make_connector",
]

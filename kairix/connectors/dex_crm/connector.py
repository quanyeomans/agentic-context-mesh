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
    RawArtefact,
    Sensitivity,
)
from kairix.transport.auth.api_key import MissingCredentialsError

CONNECTOR_NAME = "dex_crm"

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
    """

    name: str = CONNECTOR_NAME

    def __init__(
        self,
        *,
        client: DexCrmClient | None = None,
        client_config: DexCrmClientConfig | None = None,
        sensitivity: Sensitivity = "internal",
    ) -> None:
        cfg = client_config if client_config is not None else DexCrmClientConfig()
        self._client = client if client is not None else DexCrmClient(config=cfg)
        self._sensitivity: Sensitivity = sensitivity
        # Cache of last-fetched records keyed by item_id so ``fetch``
        # can return the bytes the connector already pulled in
        # ``list_changes`` without a second API roundtrip.
        self._record_cache: dict[str, DexCrmRecord] = {}

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
        return iter(events)

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
            return f"https://app.getdex.com/{_LISTING_CONTACTS}/{quote(item_id, safe='')}"
        kind, raw_id = item_id.split(":", 1)
        path = {
            _KIND_CONTACT: _LISTING_CONTACTS,
            _KIND_ORGANISATION: _LISTING_ORGANISATIONS,
            _KIND_RELATIONSHIP: _LISTING_RELATIONSHIPS,
        }.get(kind, _LISTING_CONTACTS)
        return f"https://app.getdex.com/{path}/{quote(raw_id, safe='')}"

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
    # gated by the ``topology_v2_protocol`` feature flag (default-off);
    # Wave C activates the runtime path.

    def list_changes_for_container(self, container: Container) -> Iterator[ChangeEvent]:
        """PollConnector shim — delegate to :meth:`list_changes` using the container cursor.

        Dex CRM has one logical container (the tenant). The shim
        forwards ``container.cursor_token`` to the existing
        :meth:`list_changes` so observable behaviour is identical.
        """
        return self.list_changes(container.cursor_token)

    def load_credentials(self, credentials: dict[str, Any]) -> dict[str, Any] | None:
        """CredentialsConnector shim — return the input unchanged.

        Dex auth is API-key only — no transformation, no derived
        secrets, no token exchange. The shim returns the input mapping
        as-is so the framework's credential-loading pass remains a
        no-op for this connector.
        """
        return credentials

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


def make_connector(config: Mapping[str, Any]) -> DexCrmConnector:
    """Construct a :class:`DexCrmConnector` from a config mapping.

    Expected keys (all optional with sensible defaults so the simplest
    operator config is ``- name: dex_crm``):

      * ``base_url`` — Dex API base URL; defaults to
        ``https://api.prod.getdex.com/v1``.
      * ``secret_name`` — logical secret slot for the API key; defaults
        to ``connector-dex-api-key``.
      * ``page_size`` — pagination size; defaults to 100.
      * ``rate_limit_sleep_s`` — inter-request pause in seconds;
        defaults to 1.0 (conservative 1 req/sec).
      * ``sensitivity`` — one of the F39 sensitivity literals; defaults
        to ``"internal"`` per ADR-005.

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
    return DexCrmConnector(client_config=cfg, sensitivity=sensitivity)


__all__ = [
    "CONNECTOR_NAME",
    "DexCrmConnector",
    "DexCrmRecord",
    "MissingCredentialsError",
    "make_connector",
]

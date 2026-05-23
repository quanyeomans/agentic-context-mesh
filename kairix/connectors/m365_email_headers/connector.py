"""``M365EmailHeadersConnector`` — SourceConnector for M365 header-only sync.

Implements :class:`kairix.core.protocols.SourceConnector` for a single
M365 mailbox via Microsoft Graph. Per ADR-004 (Email — Headers Only),
the connector **never** fetches body content; the
:func:`kairix.connectors.m365_email_headers.graph_client.HEADER_ONLY_SELECT`
projection is the mechanical guard at the Graph query layer.

Cursor model:

  * First sync (``cursor is None``) — call
    :meth:`M365GraphClient.iter_messages` from the seed delta URL,
    yield one ``created`` :class:`ChangeEvent` per message, then
    persist :meth:`M365GraphClient.last_delta_link` as the cursor.

  * Subsequent ticks — pass the persisted deltaLink to
    :meth:`iter_messages`; the Graph endpoint returns only items
    changed since the cursor.

``fetch`` returns a small JSON artefact containing only header fields
— the orchestration layer routes this through the canonical Silver
processor which extracts entity signals (people from
from/to/cc, subject as a timeline-update token) WITHOUT producing
chunks that contain body text (because there is no body text).

Per F35, this module only imports from ``kairix.connectors.m365_email_headers.*``
(same plugin), ``kairix.core.*`` (the Protocol surface), and
``kairix.transport.auth.*`` (the shared OAuth2 helper). No reach into
other connectors, no reach into the extractor layer.

Per F44, no Postgres / asyncpg / psycopg imports anywhere in this tree
— state lives in the connector_cursors SQLite table managed by
``kairix.core.connectors.cursor_store``.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote

from kairix.connectors.m365_email_headers.graph_client import (
    GraphMessage,
    M365GraphClient,
)
from kairix.core.protocols import (
    ChangeEvent,
    Container,
    Cursor,
    RawArtefact,
    Sensitivity,
)
from kairix.transport.auth.oauth2_client_creds import (
    OAuth2ClientCredsAuth,
)

logger = logging.getLogger(__name__)

CONNECTOR_NAME = "m365_email_headers"

# Per ADR-004 + ADR-005: email headers are personal-tier data. The
# tier is locked at the connector boundary — operators cannot lower
# the sensitivity via config because that would let a misconfigured
# deploy index personal data as public.
LOCKED_SENSITIVITY: Sensitivity = "personal"

# Microsoft Graph client-credentials scope for app-only mailbox reads.
# Always ``.default`` per the Microsoft v2 endpoint convention — the
# resolved permissions come from the AAD app registration's API
# permissions, not from this string.
GRAPH_DEFAULT_SCOPE = "https://graph.microsoft.com/.default"

# Mime hint for the fetched header-only artefact. Header envelopes are
# stored as JSON so the downstream Silver processor (entity signal
# extraction) reads structured fields directly rather than re-parsing
# RFC822.
HEADER_ARTEFACT_MIME = "application/json"


def _now_iso() -> str:
    """Return a current ISO-8601 UTC timestamp matching the connector
    boundary's :class:`ChangeEvent.modified_at` format.
    """
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class M365Credentials:
    """Resolved client-credentials triple for one M365 sync.

    Frozen per F42 — the dataclass is the typed shape that crosses the
    boundary between secret resolution and the connector constructor.
    Tests construct a literal :class:`M365Credentials` and pass it via
    the ``credentials`` kwarg; production resolves through
    :func:`_resolve_credentials_from_secrets`.
    """

    tenant_id: str
    client_id: str
    client_secret: str


def _resolve_credentials_from_secrets() -> M365Credentials:
    """Resolve the three required secrets via :func:`kairix.secrets.get_secret`.

    All three must be present — :func:`kairix.secrets.get_secret` with
    ``required=True`` raises :class:`OSError` when the underlying secret
    cannot be resolved, so each call below either returns a non-empty
    string or aborts the resolve. Lazy import so the connector module
    loads cleanly even when the secret backend is mid-bootstrap.
    """
    from kairix.secrets import get_secret

    tenant = get_secret("connector-m365-tenant-id", required=True) or ""
    client = get_secret("connector-m365-client-id", required=True) or ""
    secret = get_secret("connector-m365-client-secret", required=True) or ""
    return M365Credentials(tenant_id=tenant, client_id=client, client_secret=secret)


class M365EmailHeadersConnector:
    """SourceConnector for a single M365 mailbox, header-only.

    Construction acquires the OAuth2 client-creds shape via the
    injected ``credentials`` (tests pass a literal; production resolves
    from :mod:`kairix.secrets`). The first :meth:`list_changes` call
    drives the Graph delta query from the seed URL; subsequent calls
    resume from the previous tick's deltaLink (the ``cursor`` string).

    DI seams:

      * ``credentials`` — resolved :class:`M365Credentials`. Tests pass
        a literal; production callers omit and the factory resolves
        from :mod:`kairix.secrets`.
      * ``graph_client_factory`` — builds the
        :class:`M365GraphClient`. Tests pass a factory returning a
        client backed by an ``httpx.MockTransport`` so no real Graph
        call leaks.
    """

    name: str = CONNECTOR_NAME

    def __init__(
        self,
        user_principal_name: str,
        *,
        credentials: M365Credentials | None = None,
        client_builder: Callable[[OAuth2ClientCredsAuth, str], M365GraphClient] | None = None,
        auth: OAuth2ClientCredsAuth | None = None,
    ) -> None:
        if not user_principal_name:
            raise ValueError(
                "m365_email_headers: user_principal_name is empty. "
                "fix: set user_principal_name in the connector config block. "
                "next: see docs/architecture/connector-ingestion-architecture.md §8."
            )
        self._upn = user_principal_name

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
            self._graph = client_builder(resolved_auth, user_principal_name)
        else:
            self._graph = M365GraphClient(
                user_principal_name=user_principal_name,
                auth=resolved_auth,
            )
        # Cache for last-fetched messages so ``fetch`` can return the
        # already-acquired header envelope without a second Graph call.
        # Bronze-write happens once per item per tick.
        self._cache: dict[str, GraphMessage] = {}
        # The next-tick cursor — populated after a successful
        # ``list_changes`` drain.
        self._next_cursor: str | None = None

    # ------------------------------------------------------------------
    # SourceConnector Protocol surface
    # ------------------------------------------------------------------

    def list_changes(self, cursor: Cursor | None) -> Iterator[ChangeEvent]:
        """Stream header-only changes from Graph since ``cursor``.

        ``cursor`` is the opaque deltaLink URL from the previous tick;
        ``None`` triggers a full sync from the seed delta URL. Each
        ``GraphMessage`` becomes one ``created`` :class:`ChangeEvent`;
        Graph itself handles the modified / deleted distinction in
        future syncs (the delta endpoint returns ``@removed`` entries
        for deletes — left as a forward-only TODO per ADR-004's
        "headers only" minimal scope).
        """
        events: list[ChangeEvent] = []
        for message in self._graph.iter_messages(start_url=cursor):
            self._cache[message.message_id] = message
            events.append(
                ChangeEvent(
                    op="created",
                    item_id=message.message_id,
                    modified_at=_event_modified_at(message),
                    metadata={"sensitivity": LOCKED_SENSITIVITY},
                )
            )
        self._next_cursor = self._graph.last_delta_link()
        return iter(events)

    def fetch(self, item_id: str) -> RawArtefact:
        """Return the cached header envelope for ``item_id`` as JSON.

        ``list_changes`` populates the cache; ``fetch`` reads it. The
        artefact is a JSON serialisation of the header-only fields —
        body content is never present because the Graph projection
        never asked for it.
        """
        message = self._cache.get(item_id)
        if message is None:
            raise KeyError(
                f"m365_email_headers: item_id {item_id!r} not in the per-tick cache. "
                "fix: call list_changes() before fetch() so the Graph delta drains "
                "the envelope before the orchestrator asks for the body. "
                "next: see kairix/core/connectors/pipeline.py for the orchestrator's "
                "list_changes -> fetch contract."
            )
        payload = json.dumps(
            {
                "id": message.message_id,
                "from": message.sender,
                "toRecipients": list(message.to_recipients),
                "ccRecipients": list(message.cc_recipients),
                "subject": message.subject,
                "sentDateTime": message.sent_at,
                "receivedDateTime": message.received_at,
            },
            ensure_ascii=False,
            sort_keys=True,
        ).encode("utf-8")
        return RawArtefact(
            raw=payload,
            mime=HEADER_ARTEFACT_MIME,
            fetched_at=_now_iso(),
        )

    def source_link(self, item_id: str) -> str:
        """Return the Outlook on the Web deep link for the message.

        Outlook accepts the Graph message id directly in the
        ``ItemID`` query parameter; the URL round-trips operators
        from a retrieval result back into the original message in
        their inbox.
        """
        return f"https://outlook.office.com/mail/inbox/id/{quote(item_id, safe='')}"

    def sensitivity_for(self, _item_id: str) -> Sensitivity:
        """Always return the locked ``personal`` tier per ADR-004 + ADR-005.

        v1 has no per-item overrides — every message envelope from
        the connector carries the personal tier. A future ADR can add
        per-message classification (e.g. via Microsoft Information
        Protection labels) without breaking the Protocol.
        """
        return LOCKED_SENSITIVITY

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
        """CheckpointedConnector shim — delegate to :meth:`list_changes` using the checkpoint.

        Graph delta works on opaque deltaLink strings; the shim forwards
        ``checkpoint`` (or ``None`` for cold-start) directly to
        :meth:`list_changes` so observable behaviour matches the v1 path.
        ``_container`` is accepted for Protocol compliance but the
        legacy path is single-mailbox per cc_pair (Wave E activates
        per-container routing).
        """
        return self.list_changes(checkpoint)

    def load_credentials(self, credentials: dict[str, Any]) -> dict[str, Any] | None:
        """CredentialsConnector shim — return the input unchanged.

        Client-credentials flow consumes the operator-supplied tenant /
        client / secret triple as-is; no transformation, no token
        exchange at this surface (the OAuth2 helper exchanges at
        first-fetch time). Returning the input keeps the framework's
        credential-loading pass a no-op.
        """
        return credentials

    @classmethod
    def oauth_authorization_url(cls, _state: str) -> str:
        """OAuthConnector shim — raise actionable NotImplementedError.

        This connector uses the OAuth2 client-credentials flow (app-only,
        no operator-in-the-loop) per ADR-004 — there is no authorization
        URL to visit. The shim raises so a framework path that mistakenly
        routes to the three-legged flow fails loudly with a fix hint.
        """
        raise NotImplementedError(
            "m365_email_headers: client-credentials flow only; OAuth user flow not supported for this plugin. "
            "fix: drive auth via the configured tenant_id / client_id / client_secret triple. "
            "next: see kairix/connectors/m365_email_headers/connector.py for the credential contract."
        )

    @classmethod
    def oauth_code_to_token(cls, _code: str) -> dict[str, Any]:
        """OAuthConnector shim — raise actionable NotImplementedError.

        Counterpart to :meth:`oauth_authorization_url` — no code-to-token
        exchange because this connector does not surface an OAuth
        consent screen.
        """
        raise NotImplementedError(
            "m365_email_headers: client-credentials flow only; OAuth user flow not supported for this plugin. "
            "fix: drive auth via the configured tenant_id / client_id / client_secret triple. "
            "next: see kairix/connectors/m365_email_headers/connector.py for the credential contract."
        )

    # ------------------------------------------------------------------
    # Forward-only API (read by orchestration)
    # ------------------------------------------------------------------

    def next_cursor(self) -> str | None:
        """Return the deltaLink the orchestrator should persist after this tick.

        Populated by the most recent successful :meth:`list_changes`
        drain; ``None`` before the first call or when the Graph
        response carried no final deltaLink.
        """
        return self._next_cursor


def _event_modified_at(message: GraphMessage) -> str:
    """Pick the best timestamp for a ChangeEvent's ``modified_at``.

    Prefer the received timestamp (when the recipient inbox got the
    message — what the operator's timeline tracks); fall back to
    sent; fall back to wall-clock-now if Graph returned no envelope
    timestamps.
    """
    if message.received_at:
        return message.received_at
    if message.sent_at:
        return message.sent_at
    return _now_iso()


def make_connector(config: Mapping[str, Any]) -> M365EmailHeadersConnector:
    """Construct an :class:`M365EmailHeadersConnector` from a config mapping.

    Expected keys:

      * ``user_principal_name`` (required) — the target mailbox UPN.
      * ``sensitivity`` (optional) — ignored. Locked to ``personal``
        per ADR-004 + ADR-005; specifying a different tier in config
        is a config error (raises ``ValueError`` so operators see
        the misconfiguration loudly rather than silently mis-tagging).

    Credentials resolve via :func:`kairix.secrets.get_secret` —
    ``connector-m365-tenant-id`` / ``connector-m365-client-id`` /
    ``connector-m365-client-secret`` must all be set. The OAuth2
    client-credentials flow exchanges the triple for a bearer at
    Graph's ``v2.0/token`` endpoint.

    Registered via ``[project.entry-points."kairix.connectors"]`` in
    kairix's ``pyproject.toml`` so the orchestration layer can resolve
    ``m365_email_headers`` to this factory by name.
    """
    upn = config.get("user_principal_name")
    if not isinstance(upn, str) or not upn:
        raise ValueError(
            "m365_email_headers: config is missing 'user_principal_name'. "
            "fix: add user_principal_name: alice@contoso.com under the "
            "m365_email_headers connector block in kairix.config.yaml. "
            "next: see docs/architecture/connector-ingestion-architecture.md §8."
        )

    declared_sensitivity = config.get("sensitivity")
    if declared_sensitivity is not None and declared_sensitivity != LOCKED_SENSITIVITY:
        raise ValueError(
            f"m365_email_headers: sensitivity is locked to {LOCKED_SENSITIVITY!r} "
            f"per ADR-004 + ADR-005; config declared {declared_sensitivity!r}. "
            "fix: remove the sensitivity key from the m365_email_headers config "
            "block — the tier is set at the connector boundary. "
            "next: see docs/architecture/adrs/ADR-004-email-headers-only.md."
        )

    return M365EmailHeadersConnector(user_principal_name=upn)

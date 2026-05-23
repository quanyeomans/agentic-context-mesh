"""Thin Microsoft Graph client for header-only message retrieval.

A focused wrapper around ``httpx.Client`` for the Microsoft Graph
``/users/{upn}/messages/delta`` endpoint. Three commitments:

  1. **Header-only retrieval** (ADR-004). Every Graph request carries
     ``$select=from,toRecipients,ccRecipients,subject,sentDateTime,
     receivedDateTime,id``. Body fields (``body``, ``uniqueBody``,
     ``bodyPreview``) are never requested. Tests pin the ``$select``
     string at the query-construction surface.

  2. **OAuth2 client-credentials auth.** Every request adds an
     ``Authorization: Bearer <token>`` header via the injected
     :class:`OAuth2ClientCredsAuth` helper. A 401 triggers a single
     :meth:`invalidate` + retry; persistent 401 propagates.

  3. **Delta-token pagination.** The connector hands an opaque cursor
     between ticks; the Graph response carries either ``@odata.nextLink``
     (more pages now) or ``@odata.deltaLink`` (resume here next tick).
     The client surfaces both as :class:`DeltaPage` so the connector
     can advance cursors without parsing URLs itself.

Per F37, ``msgraph_core`` / ``msgraph`` import is allowed only under
``kairix/connectors/<name>/`` — but we deliberately avoid the SDK
(see ADR rationale in the spec brief: the SDK pulls a heavy transitive
set; the delta query is a straightforward REST call). The client uses
raw ``httpx`` and stays under F37's allowed surface (this module lives
at ``kairix/connectors/m365_email_headers/graph_client.py``).
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any, Final

import httpx

from kairix.transport.auth.oauth2_client_creds import OAuth2ClientCredsAuth

logger = logging.getLogger(__name__)

# Per ADR-004 (Email — Headers Only): the Graph projection that ensures
# we NEVER fetch any body field. Add new fields only if they are
# explicitly envelope (header) data — never body / preview / unique
# body. The constant is exported so tests can pin the projection at
# the assertion layer (the body-content-not-fetched scenario).
HEADER_ONLY_SELECT: Final[str] = "id,from,toRecipients,ccRecipients,subject,sentDateTime,receivedDateTime"

# Default base URL — overrideable for sovereign clouds (e.g. Graph for
# US Government / 21Vianet).
_DEFAULT_GRAPH_BASE: Final[str] = "https://graph.microsoft.com/v1.0"

# Per-request timeout. Graph delta replies typically arrive in <1s;
# 60s covers a cold connection on a paginated reply with a large
# mailbox.
_GRAPH_REQUEST_TIMEOUT_S: Final[float] = 60.0


@dataclass(frozen=True)
class GraphMessage:
    """One header-only message envelope as projected from Microsoft Graph.

    Body fields are intentionally absent — the dataclass shape itself
    encodes the ADR-004 constraint. A future contributor adding a
    ``body`` field would break the test that pins the dataclass field
    set, which is the mechanical guard for the no-body-content
    invariant.
    """

    message_id: str
    sender: str | None
    to_recipients: tuple[str, ...]
    cc_recipients: tuple[str, ...]
    subject: str | None
    sent_at: str | None
    received_at: str | None


@dataclass(frozen=True)
class DeltaPage:
    """One page of the ``/messages/delta`` response.

    ``next_link`` is non-``None`` when more pages remain for the
    current sync window; the caller follows it before advancing the
    cursor. ``delta_link`` is non-``None`` on the *final* page —
    that's the opaque token the caller persists as the connector
    cursor for the next worker tick.
    """

    messages: tuple[GraphMessage, ...]
    next_link: str | None
    delta_link: str | None


class M365GraphClient:
    """Thin Microsoft Graph wrapper for header-only delta queries.

    Args:
        user_principal_name: The mailbox to sync — typically the
            target user's UPN (``alice@contoso.com``). App-only auth
            requires the AAD app to hold the ``Mail.Read`` application
            permission scoped to the target mailbox.
        auth: An initialised :class:`OAuth2ClientCredsAuth` for the
            tenant the mailbox lives in. The client holds a reference
            and re-uses it for every request.
        graph_base: Optional override for sovereign clouds. Defaults
            to the public Microsoft Graph endpoint.
        http_client: Optional ``httpx.Client`` for the request path.
            Tests pass an :class:`httpx.MockTransport`-backed client
            so no real Graph call leaks from the test suite.
    """

    def __init__(
        self,
        *,
        user_principal_name: str,
        auth: OAuth2ClientCredsAuth,
        graph_base: str | None = None,
        http_client: httpx.Client | None = None,
    ) -> None:
        if not user_principal_name:
            raise ValueError(
                "M365GraphClient: user_principal_name is empty. "
                "fix: pass the target mailbox UPN (e.g. alice@contoso.com). "
                "next: see docs/architecture/connector-ingestion-architecture.md §8 "
                "for the M365 connector config shape."
            )
        self._upn = user_principal_name
        self._auth = auth
        self._graph_base = (graph_base or _DEFAULT_GRAPH_BASE).rstrip("/")
        self._http_client = http_client

    def initial_delta_url(self) -> str:
        """Compose the seed delta URL with the header-only projection.

        The first sync (no cursor) starts here; subsequent syncs hand
        the previous response's ``deltaLink`` directly to
        :meth:`fetch_page`. Exposed publicly so tests can pin the
        ``$select`` projection without driving a real HTTP call.
        """
        return f"{self._graph_base}/users/{self._upn}/messages/delta?$select={HEADER_ONLY_SELECT}"

    def fetch_page(self, url: str) -> DeltaPage:
        """Fetch one page from the given Graph URL (delta or nextLink).

        Args:
            url: The full Graph URL — either the seed
                :meth:`initial_delta_url`, a previous response's
                ``@odata.nextLink`` (more pages this run), or a stored
                ``@odata.deltaLink`` cursor (next sync tick).

        Returns:
            A :class:`DeltaPage` carrying parsed header-only messages
            and the next-link / delta-link pointers for the caller's
            pagination loop.

        Raises:
            httpx.HTTPError: On non-2xx response after the single
                401-driven token refresh.
        """
        response = self._authorised_get(url)
        body = response.json()
        return _parse_delta_page(body)

    def iter_messages(self, start_url: str | None = None) -> Iterator[GraphMessage]:
        """Iterate header-only messages across all pages until the
        delta-link is reached.

        Args:
            start_url: Optional starting URL. ``None`` starts from
                :meth:`initial_delta_url` (full sync); a stored
                deltaLink starts from the previous cursor.

        Yields:
            One :class:`GraphMessage` per Graph response entry. The
            final page's ``deltaLink`` is accessible via
            :meth:`last_delta_link` after iteration completes.
        """
        url: str | None = start_url or self.initial_delta_url()
        self._last_delta: str | None = None
        while url is not None:
            page = self.fetch_page(url)
            yield from page.messages
            self._last_delta = page.delta_link
            url = page.next_link

    def last_delta_link(self) -> str | None:
        """Return the deltaLink from the most recent terminal page.

        Returns ``None`` before any iteration completes. The connector
        persists this string as the cursor advanced past the items it
        consumed this tick.
        """
        return getattr(self, "_last_delta", None)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _authorised_get(self, url: str) -> httpx.Response:
        """Issue a GET with the current bearer; on 401, invalidate +
        retry once. Persistent 401 raises ``httpx.HTTPStatusError``.
        """
        token = self._auth.get_token()
        response = self._do_get(url, token)
        if response.status_code == 401:
            logger.info("m365 graph: received 401; invalidating token cache and retrying once")
            self._auth.invalidate()
            token = self._auth.get_token()
            response = self._do_get(url, token)
        response.raise_for_status()
        return response

    def _do_get(self, url: str, token: str) -> httpx.Response:
        """Single HTTP GET — separated for the 401-retry path's
        symmetry. The bearer string is composed into the Authorization
        header here ONLY; never logged, never returned.
        """
        headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
        client = self._http_client
        if client is not None:
            return client.get(url, headers=headers, timeout=_GRAPH_REQUEST_TIMEOUT_S)
        with httpx.Client(timeout=_GRAPH_REQUEST_TIMEOUT_S) as owned:
            return owned.get(url, headers=headers)


def _parse_delta_page(body: dict[str, Any]) -> DeltaPage:
    """Parse one Graph ``messages/delta`` JSON response.

    Tolerates the documented response shape — ``value`` is the array
    of message envelopes, ``@odata.nextLink`` advances within the
    sync window, ``@odata.deltaLink`` is the next-tick cursor. Missing
    fields default to ``None`` / empty tuple so a sparse fixture
    parses cleanly.
    """
    raw_messages = body.get("value")
    messages: list[GraphMessage] = []
    if isinstance(raw_messages, list):
        for entry in raw_messages:
            if isinstance(entry, dict):
                messages.append(_parse_message(entry))
    next_link = body.get("@odata.nextLink")
    delta_link = body.get("@odata.deltaLink")
    return DeltaPage(
        messages=tuple(messages),
        next_link=next_link if isinstance(next_link, str) else None,
        delta_link=delta_link if isinstance(delta_link, str) else None,
    )


def _parse_message(entry: dict[str, Any]) -> GraphMessage:
    """Lift one Graph message envelope into the typed dataclass."""
    return GraphMessage(
        message_id=_str_or_empty(entry.get("id")),
        sender=_email_from(entry.get("from")),
        to_recipients=tuple(_emails_from(entry.get("toRecipients"))),
        cc_recipients=tuple(_emails_from(entry.get("ccRecipients"))),
        subject=_optional_str(entry.get("subject")),
        sent_at=_optional_str(entry.get("sentDateTime")),
        received_at=_optional_str(entry.get("receivedDateTime")),
    )


def _str_or_empty(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _optional_str(value: Any) -> str | None:
    return value if isinstance(value, str) else None


def _email_from(value: Any) -> str | None:
    """Pull ``emailAddress.address`` from a Graph recipient block."""
    if not isinstance(value, dict):
        return None
    inner = value.get("emailAddress")
    if not isinstance(inner, dict):
        return None
    address = inner.get("address")
    return address if isinstance(address, str) else None


def _emails_from(value: Any) -> list[str]:
    """Pull each recipient's ``emailAddress.address`` from a Graph
    ``toRecipients`` / ``ccRecipients`` list.
    """
    out: list[str] = []
    if not isinstance(value, list):
        return out
    for entry in value:
        addr = _email_from(entry)
        if addr is not None:
            out.append(addr)
    return out

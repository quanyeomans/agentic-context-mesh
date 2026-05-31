"""HTTP client for the Dex CRM API.

Thin wrapper over :class:`httpx.Client` paired with
:class:`kairix.transport.auth.api_key.ApiKeyAuth` for the Bearer
header. Surface is deliberately narrow — list_contacts /
list_organisations / list_relationships — so the connector module owns
the orchestration shape (cursor, pagination, rate limiting) and this
module owns only the wire format.

Per F35 the client lives under ``kairix/connectors/dex_crm/`` and
imports nothing from another connector. Cross-plugin shared transport
state (httpx pooling, retry policy) lives under ``kairix/transport/``
when the second API-key connector lands; for Wave 5 KP-1 the simpler
per-connector client keeps the surface small.

Rate limiting: the Dex API's published rate limit is not public, so the
client defaults to a conservative 1 req/sec polling cadence with
exponential backoff on HTTP 429. The retries cap at 4 attempts so a
genuinely unavailable Dex returns control to the worker quickly.

F15: never logs the API key in plaintext. Diagnostic logs name the
endpoint path; the bearer token never appears in the log line.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

import httpx
from tenacity import (
    RetryError,
    Retrying,
    retry_if_result,
    stop_after_attempt,
    wait_exponential,
)

from kairix.transport.auth.api_key import ApiKeyAuth, BearerHeaders, MissingCredentialsError

logger = logging.getLogger(__name__)


DEFAULT_BASE_URL = "https://api.prod.getdex.com/v1"
# Logical secret slot resolved via kairix.secrets.get_secret. The value
# is the secret NAME (a registry key), not a token — operators store
# the real bearer in their KV / secrets file under this name.
DEFAULT_SECRET_NAME = "connector-dex-api-key"  # noqa: S105 — secret-name registry key, not a bearer token  # pragma: allowlist secret
DEFAULT_PAGE_SIZE = 100
DEFAULT_TIMEOUT_S = 30.0
DEFAULT_MAX_RETRIES = 4
DEFAULT_BACKOFF_BASE_S = 1.0


@dataclass(frozen=True)
class DexCrmPage:
    """One page of records from a Dex listing endpoint.

    Frozen-dataclass per F42 — the boundary value object the connector
    iterates over. ``records`` is a tuple of raw dict items as the Dex
    API returns them; the connector's silver-pre-shaping layer maps each
    to a :class:`~kairix.core.protocols.ChangeEvent`. ``next_cursor`` is
    the pagination token (or ``None`` when the listing has been
    exhausted).
    """

    records: tuple[Mapping[str, Any], ...]
    next_cursor: str | None


@dataclass(frozen=True)
class DexCrmClientConfig:
    """Construction-time config for :class:`DexCrmClient`.

    Frozen-dataclass per F42. ``base_url`` is overridable so a future
    Dex sandbox endpoint or a self-hosted proxy can be wired without
    code change. ``secret_name`` is configurable so two engagements
    using distinct Dex tenants can declare distinct secret slots.

    ``rate_limit_sleep_s`` is the inter-request pause the client applies
    after every successful response — defaults to 1.0s for the
    conservative 1 req/sec polling cadence the KFEAT-005 brief calls
    out. Tests pass ``0.0`` so the suite stays fast.
    """

    base_url: str = DEFAULT_BASE_URL
    secret_name: str = DEFAULT_SECRET_NAME
    page_size: int = DEFAULT_PAGE_SIZE
    timeout_s: float = DEFAULT_TIMEOUT_S
    max_retries: int = DEFAULT_MAX_RETRIES
    backoff_base_s: float = DEFAULT_BACKOFF_BASE_S
    rate_limit_sleep_s: float = 1.0


@dataclass
class DexCrmClient:
    """HTTP client for the Dex CRM API.

    Construction is cheap (no I/O, no auth resolution). The first
    request lazily resolves the API key via :class:`ApiKeyAuth`; if the
    secret is unset the request raises
    :class:`~kairix.transport.auth.api_key.MissingCredentialsError`
    which the connector surfaces up to its caller.

    The client is intentionally synchronous — the worker loop drives
    one connector at a time and the Dex listing endpoints are small
    enough that async-batching gains nothing. The kairix transport
    pool is reserved for the high-volume embed path.

    DI seams (all kwargs with real defaults — F6 clean):
      * ``config`` — :class:`DexCrmClientConfig`; defaults to the
        production endpoint + production secret slot.
      * ``http_client`` — :class:`httpx.Client`; defaults to a fresh
        client built at first request. Tests pass a stand-in (e.g.
        ``httpx.Client(transport=httpx.MockTransport(...))``) so the
        suite never reaches the public internet.
      * ``auth`` — :class:`ApiKeyAuth`; defaults to a fresh instance.
        Tests can pin a recording stand-in to assert header shape.
      * ``sleep`` — :func:`time.sleep` shim; tests pass a no-op so the
        backoff loop runs without wall-clock delay.
    """

    config: DexCrmClientConfig = field(default_factory=DexCrmClientConfig)
    http_client: httpx.Client | None = None
    auth: ApiKeyAuth = field(default_factory=ApiKeyAuth)
    sleep: Any = time.sleep

    def list_contacts(self, updated_after: str | None, cursor: str | None) -> DexCrmPage:
        """List contacts updated after ``updated_after`` (ISO-8601 UTC).

        ``updated_after`` is the connector cursor — the timestamp of
        the last record successfully processed. ``cursor`` is the
        intra-page pagination token used to walk through one query's
        result set. Either may be ``None`` (first call / first page).
        """
        return self._list("contacts", updated_after, cursor)

    def list_organisations(self, updated_after: str | None, cursor: str | None) -> DexCrmPage:
        """List organisations updated after ``updated_after`` (ISO-8601 UTC).

        Same shape as :meth:`list_contacts`; the Dex API uses a
        separate listing endpoint per record type.
        """
        return self._list("organisations", updated_after, cursor)

    def list_relationships(self, updated_after: str | None, cursor: str | None) -> DexCrmPage:
        """List contact-organisation relationships updated since ``updated_after``."""
        return self._list("relationships", updated_after, cursor)

    def iter_listing(
        self,
        kind: str,
        updated_after: str | None,
    ) -> Iterator[Mapping[str, Any]]:
        """Yield every record across all pages for ``kind``.

        Helper that drives :meth:`_list` in a pagination loop. ``kind``
        is one of ``"contacts"``, ``"organisations"``, or
        ``"relationships"``; the connector uses this to fold all three
        endpoints into one event stream.
        """
        cursor: str | None = None
        while True:
            page = self._list(kind, updated_after, cursor)
            yield from page.records
            if page.next_cursor is None:
                return
            cursor = page.next_cursor

    def _list(self, kind: str, updated_after: str | None, cursor: str | None) -> DexCrmPage:
        """One paginated listing request.

        Resolves the auth header, builds the request, retries on 429
        with exponential backoff, and returns the parsed page. Lifts
        :class:`MissingCredentialsError` through unchanged so the
        connector can surface it with a typed error.
        """
        headers = self._authorized_headers()
        params: dict[str, str | int] = {"limit": self.config.page_size}
        if updated_after is not None:
            params["updated_after"] = updated_after
        if cursor is not None:
            params["cursor"] = cursor
        url = f"{self.config.base_url.rstrip('/')}/{kind}"

        response = self._send_with_retry(url, headers, params)
        body = response.json()
        records = tuple(body.get("data") or body.get("records") or [])
        next_cursor = body.get("next_cursor")
        if not isinstance(next_cursor, str) or not next_cursor:
            next_cursor = None
        # Conservative inter-request pause — the rate limit isn't
        # public, so we throttle ourselves to ~1 req/sec by default.
        if self.config.rate_limit_sleep_s > 0:
            self.sleep(self.config.rate_limit_sleep_s)
        return DexCrmPage(records=records, next_cursor=next_cursor)

    def _authorized_headers(self) -> Mapping[str, str]:
        """Resolve the Bearer header via :class:`ApiKeyAuth`.

        Surfaces :class:`MissingCredentialsError` to the caller —
        callers translate it into the connector's typed error or
        propagate it up to the operator surface.
        """
        bearer: BearerHeaders = self.auth.headers(self.config.secret_name)
        return dict(bearer.mapping)

    def _send_with_retry(
        self,
        url: str,
        headers: Mapping[str, str],
        params: Mapping[str, str | int],
    ) -> httpx.Response:
        """GET ``url`` with exponential backoff on 429.

        Retries up to ``config.max_retries`` times. Non-429 4xx/5xx
        responses raise via ``response.raise_for_status()`` so the
        caller's exception handler can map them to dead-letter rows.

        Per docs/architecture/connector-oss-library-evaluation.md §7 the
        retry loop uses ``tenacity`` (one dep, no transitives) — earlier
        connectors had hand-rolled while-True loops which fragmented the
        retry semantics. This file is the reference shape future
        connectors should copy when they need HTTP retry with backoff.
        """
        client = self._ensure_client()
        safe_path = self._safe_path_for_log(url)

        def _is_rate_limited(response: httpx.Response) -> bool:
            return response.status_code == httpx.codes.TOO_MANY_REQUESTS

        retrying = Retrying(
            retry=retry_if_result(_is_rate_limited),
            wait=wait_exponential(multiplier=self.config.backoff_base_s, exp_base=2),
            stop=stop_after_attempt(self.config.max_retries),
            sleep=self.sleep,
            reraise=True,
            before_sleep=lambda rs: logger.info(
                "dex_crm: rate-limited on %s — sleeping before retry %d",
                safe_path,
                rs.attempt_number + 1,
            ),
        )
        try:
            response = retrying(client.get, url, headers=headers, params=params)
        except RetryError as exc:
            # ``retry_if_result`` returns a "successful" outcome from
            # tenacity's perspective, so ``reraise=True`` can't lift an
            # exception (there is none). On stop-condition exhaustion
            # tenacity wraps the final attempt's result in
            # :class:`RetryError`; we lift the underlying response and
            # convert it via ``raise_for_status`` so callers see the
            # canonical :class:`httpx.HTTPStatusError` (GH #358 — matches
            # the SharePoint connector's exhausted-retry surface).
            final: httpx.Response = exc.last_attempt.result()
            logger.warning(
                "dex_crm: rate-limited after %d attempts on path %s",
                self.config.max_retries,
                safe_path,
            )
            final.raise_for_status()
            return final  # pragma: no cover — raise_for_status above always raises here for 429
        response.raise_for_status()
        return response

    def _ensure_client(self) -> httpx.Client:
        """Lazy-build the underlying ``httpx.Client`` on first use."""
        if self.http_client is None:
            self.http_client = httpx.Client(timeout=self.config.timeout_s)
        return self.http_client

    def _safe_path_for_log(self, url: str) -> str:
        """Strip the host + scheme from ``url`` for log messages.

        Keeps the diagnostic useful (operator can see which endpoint
        rate-limited) without echoing the base URL or any query-string
        tokens that might carry sensitive identifiers.
        """
        parsed = urlparse(url)
        return parsed.path or "/"


__all__ = [
    "DexCrmClient",
    "DexCrmClientConfig",
    "DexCrmPage",
    "MissingCredentialsError",
]

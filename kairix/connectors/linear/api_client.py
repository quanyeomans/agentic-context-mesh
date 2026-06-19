"""Thin Linear GraphQL API client for the kairix Linear connector.

A focused wrapper around ``httpx.Client`` for the Linear GraphQL surface
the connector exercises — arbitrary GraphQL queries and cursor-paginated
connection traversal. Three commitments:

  1. **HTTPS-only (spec §3).** The endpoint constant is always
     ``https://api.linear.app/graphql``; the constructor REJECTS any
     endpoint whose scheme is not ``https`` so no code path can
     inadvertently issue an ``http://`` request.

  2. **API-key auth.** Every request adds an ``Authorization: <api_key>``
     header per the Linear API contract. The key is captured into private
     state and never logged (F15 boundary discipline).

  3. **429 backoff via injected sleeper.** On HTTP 429, the client reads
     the ``Retry-After`` response header (defaulting to 60 s) and sleeps
     via the INJECTED sleeper seam — a plain ``Callable[[float], None]``,
     never ``time.sleep`` directly — then retries up to ``_MAX_RETRIES``
     times. Tests pass a recording callable; production passes the default
     which wraps ``time.sleep``.

The sleeper is a plain callable (not a Protocol) on purpose: a Protocol
with a public method would create an F68 failure-injection obligation
for a test-only seam. A ``Callable[[float], None]`` carries no F68
surface while keeping the DI seam clean.

Per F35, this module only imports from ``kairix.connectors.linear.*``,
the standard library, and third-party packages already in the dependency
set (``httpx``). No new third-party dependency is introduced.

Per F41, this module is mypy-strict-clean and the package carries
``py.typed``.
"""

from __future__ import annotations

import logging
import time
import urllib.parse
from collections.abc import Callable, Iterator, Mapping
from typing import Any, Final

import httpx

logger = logging.getLogger(__name__)

# Public constant — downstream connector code imports this.
LINEAR_GRAPHQL_ENDPOINT: Final[str] = "https://api.linear.app/graphql"

# Per-request timeout. Linear's GraphQL endpoint typically replies in <2s;
# 60 s covers a cold connection or a paginated reply over a large dataset.
_LINEAR_REQUEST_TIMEOUT_S: Final[float] = 60.0

# Maximum number of retry attempts on HTTP 429 before giving up.
_MAX_RETRIES: Final[int] = 3

# Default Retry-After delay when the header is absent or non-numeric.
# Exported as a public constant so downstream tests can assert the exact
# fallback without importing a private name (F5).
LINEAR_DEFAULT_RETRY_AFTER_S: Final[float] = 60.0

# GraphQL response keys — extracted to module-level constants to satisfy
# the F17 dup-literal gate as the client grows.
_KEY_DATA: Final[str] = "data"
_KEY_ERRORS: Final[str] = "errors"
_KEY_NODES: Final[str] = "nodes"
_KEY_PAGE_INFO: Final[str] = "pageInfo"
_KEY_HAS_NEXT_PAGE: Final[str] = "hasNextPage"
_KEY_END_CURSOR: Final[str] = "endCursor"
_KEY_AFTER: Final[str] = "after"

# Sleeper seam type. A plain callable taking a float (seconds) — NOT a
# Protocol, so no F68 failure-injection obligation attaches.
Sleeper = Callable[[float], None]


def _default_sleep(seconds: float) -> None:
    """Production sleeper — delegates to ``time.sleep``.

    Isolated in a module-level function so the F86 DI-default coverage
    floor sees the seam body. Tests always supply their own callable via
    ``sleeper=``; this path runs only in production when a real 429 is hit.
    """
    time.sleep(seconds)


class LinearApiClient:
    """Thin Linear GraphQL wrapper for the kairix connector.

    Args:
        api_key: The Linear personal API key or OAuth token. Required.
            Never logged — F15 boundary discipline.
        endpoint: The GraphQL endpoint URL. Must be ``https://``. Defaults
            to :data:`LINEAR_GRAPHQL_ENDPOINT`.
        http: Optional ``httpx.Client`` for the request path. Tests pass
            an :class:`httpx.MockTransport`-backed client so no real Linear
            call leaks from the test suite. When ``None`` a short-lived
            owned client is created per request.
        sleeper: Optional sleep seam — a ``Callable[[float], None]``.
            Defaults to :func:`_default_sleep`. Tests inject a recording
            callable so 429-retry tests run at full speed without
            wall-clock waits.

    Raises:
        ValueError: If ``endpoint`` is not an ``https://`` URL.
    """

    def __init__(
        self,
        api_key: str,
        *,
        endpoint: str = LINEAR_GRAPHQL_ENDPOINT,
        http: httpx.Client | None = None,
        sleeper: Sleeper | None = None,
    ) -> None:
        parsed = urllib.parse.urlsplit(endpoint)
        if parsed.scheme != "https":
            raise ValueError(
                f"linear api client: endpoint scheme must be 'https', got {parsed.scheme!r}. "
                f"fix: use an https:// endpoint (default: {LINEAR_GRAPHQL_ENDPOINT!r}). "
                f"next: check connector config for a mis-typed endpoint override."
            )
        # F15: api_key captured into private state; never logged.
        self._api_key = api_key
        self._endpoint = endpoint
        self._http = http
        self._sleeper: Sleeper = sleeper if sleeper is not None else _default_sleep

    # ------------------------------------------------------------------
    # Public GraphQL surface
    # ------------------------------------------------------------------

    def query(self, document: str, variables: Mapping[str, Any]) -> dict[str, Any]:
        """Execute one GraphQL POST and return the ``data`` dict.

        Args:
            document: The GraphQL query/mutation string.
            variables: Variable bindings for the document.

        Returns:
            The ``data`` value from the GraphQL response envelope.

        Raises:
            httpx.HTTPStatusError: On unrecoverable HTTP errors (non-429)
                OR when all 429 retries are exhausted.
            RuntimeError: If the GraphQL response carries ``errors`` or is
                missing its ``data`` key.
        """
        return self._post_with_retry(document, dict(variables))

    def paginate(
        self,
        document: str,
        variables: Mapping[str, Any],
        *,
        connection: str,
    ) -> Iterator[dict[str, Any]]:
        """Paginate a GraphQL connection field, yielding each node.

        Drives the ``after`` / ``pageInfo.endCursor`` cursor pattern used
        by every Linear list endpoint. Stops when ``pageInfo.hasNextPage``
        is ``false`` or the cursor is absent.

        Args:
            document: The GraphQL query string. Must include
                ``pageInfo { hasNextPage endCursor }`` in the named
                connection field.
            variables: Base variable bindings; ``after`` is injected by
                this method on successive pages.
            connection: The top-level connection key in ``data`` (e.g.
                ``"issues"``).

        Yields:
            One ``dict`` per node across all pages.
        """
        vars_: dict[str, Any] = dict(variables)
        while True:
            data = self._post_with_retry(document, vars_)
            conn = data.get(connection, {})
            if not isinstance(conn, dict):
                return
            yield from conn.get(_KEY_NODES, [])
            page_info = conn.get(_KEY_PAGE_INFO, {})
            if not isinstance(page_info, dict):
                return
            if not page_info.get(_KEY_HAS_NEXT_PAGE):
                return
            cursor = page_info.get(_KEY_END_CURSOR)
            if not isinstance(cursor, str) or not cursor:
                return
            vars_[_KEY_AFTER] = cursor

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _post_with_retry(self, document: str, variables: dict[str, Any]) -> dict[str, Any]:
        """POST the GraphQL payload with bounded 429 backoff.

        On HTTP 429 the client reads ``Retry-After`` (defaulting to
        ``LINEAR_DEFAULT_RETRY_AFTER_S``) and sleeps via the injected sleeper.
        Retries up to ``_MAX_RETRIES`` times. Any other HTTP error is
        re-raised immediately.
        """
        body = {"query": document, "variables": variables}
        for attempt in range(_MAX_RETRIES + 1):
            response = self._authorised_post(body)
            if response.status_code == 429:
                if attempt == _MAX_RETRIES:
                    response.raise_for_status()
                wait = _parse_retry_after(response)
                logger.debug(
                    "linear api: 429 rate-limit on attempt %d/%d; sleeping %.1fs",
                    attempt + 1,
                    _MAX_RETRIES,
                    wait,
                )
                self._sleeper(wait)
                continue
            response.raise_for_status()
            payload: dict[str, Any] = response.json()
            if _KEY_ERRORS in payload:
                raise RuntimeError(
                    f"linear graphql errors: {payload[_KEY_ERRORS]!r}. "
                    f"fix: check the query document and variables for schema errors. "
                    f"run: kairix linear status to verify connectivity."
                )
            data = payload.get(_KEY_DATA)
            if not isinstance(data, dict):
                raise RuntimeError(
                    f"linear api client: response missing 'data' key. "
                    f"fix: check {self._endpoint!r} is a valid Linear GraphQL endpoint."
                )
            return data
        # Unreachable — loop always returns or raises inside.
        raise RuntimeError(  # pragma: no cover — F3: defensive; loop above always returns or raises
            "linear api client: retry loop exhausted without returning"
        )

    def _authorised_post(self, body: dict[str, Any]) -> httpx.Response:
        """Issue a POST with the Linear API-key auth header.

        F15-clean: the key is composed into the Authorization header here
        only; never logged, never returned.
        """
        headers = self._headers()
        client = self._http
        if client is not None:
            return client.post(
                self._endpoint,
                headers=headers,
                json=body,
                timeout=_LINEAR_REQUEST_TIMEOUT_S,
            )
        with httpx.Client(timeout=_LINEAR_REQUEST_TIMEOUT_S) as owned:
            return owned.post(self._endpoint, headers=headers, json=body)

    def _headers(self) -> dict[str, str]:
        """Build per-request headers including the Linear API-key auth.

        F15: the key is composed here only; the helper is private and
        never returns the key via any other path.
        """
        return {
            "Authorization": self._api_key,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }


# ---------------------------------------------------------------------------
# Free-function helpers
# ---------------------------------------------------------------------------


def _parse_retry_after(response: httpx.Response) -> float:
    """Parse the ``Retry-After`` header, falling back to the default.

    Linear may return an integer number of seconds or omit the header
    entirely. We guard against non-numeric values and negatives.
    """
    raw = response.headers.get("Retry-After", "")
    try:
        value = float(raw)
        if value > 0:
            return value
    except (ValueError, TypeError):
        pass
    return LINEAR_DEFAULT_RETRY_AFTER_S

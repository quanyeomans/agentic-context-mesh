"""OAuth2 client-credentials helper for app-only token acquisition.

A reusable :mod:`httpx`-backed helper for the OAuth2 *client credentials*
grant (RFC 6749 §4.4). Targets Microsoft Entra ID (Azure AD) by default
— the ``token_endpoint`` is overrideable for any IdP that speaks the
spec.

Used by:

  * :mod:`kairix.connectors.m365_email_headers` — pulls email metadata
    from Microsoft Graph (KP-2) using ``https://graph.microsoft.com/.default``.
  * :mod:`kairix.connectors.m365_calendar` — pulls calendar events
    (KP-3, sibling brief; this module is the shared helper).

Both connectors call :meth:`OAuth2ClientCredsAuth.get_token` to obtain
a bearer-token string they then pass to ``httpx`` as
``Authorization: Bearer <token>``. The helper caches the token per
process; it refreshes when within ``REFRESH_SKEW_S`` seconds of
``expires_in`` OR when the caller signals a 401 via
:meth:`invalidate`.

Per F15 the helper logs no token / secret values — only opaque "token
acquired" / "token refresh requested" messages. The bearer string never
appears in any log line, raise message, or stdout/stderr write outside
the boundary modules in :mod:`kairix.secrets`.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Final

import httpx

logger = logging.getLogger(__name__)

# Refresh the cached token when within this many seconds of expiry.
# 60s comfortably covers a clock-skewed Graph request that would
# otherwise hit a 401 mid-batch — the refresh fires before the token
# expires upstream.
REFRESH_SKEW_S: Final[int] = 60

# Default Microsoft Entra ID (Azure AD) v2 endpoint shape. Overrideable
# in the constructor for sovereign clouds or non-Microsoft IdPs.
DEFAULT_TOKEN_ENDPOINT_TEMPLATE: Final[str] = "https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"  # noqa: S105 — rationale: URL template, not a secret. Mechanically the static analyzer flags any string assigned to a NAME containing "TOKEN", but this is the public OAuth2 endpoint shape, not a credential.

# Connection + read timeout for the token exchange. 30s is generous;
# the upstream typically replies in <500ms but DNS / TLS handshake on
# a cold container can stretch the first call.
_TOKEN_REQUEST_TIMEOUT_S: Final[float] = 30.0


class MissingCredentialsError(RuntimeError):
    """Raised when a required OAuth2 client-credentials field is empty.

    All three of ``tenant_id`` / ``client_id`` / ``client_secret`` must
    resolve to non-empty strings; the typed error names which field is
    missing so operators can fix the misconfigured secret without
    grep-ing logs.
    """


@dataclass(frozen=True)
class _CachedToken:
    """In-memory token-cache state — frozen per F42.

    ``access_token`` is the bearer string the helper hands back to the
    caller; ``expires_at`` is the wall-clock Unix timestamp when the
    upstream-reported ``expires_in`` runs out. The cache is invalidated
    by replacing the whole record — frozen on purpose so a concurrent
    reader never sees a half-mutated state.
    """

    access_token: str
    expires_at: float


class OAuth2ClientCredsAuth:
    """Cache + refresh OAuth2 client-credentials tokens for one tenant/scope.

    Constructed once per ``(tenant_id, scope)`` pair the caller needs.
    Token acquisition is lazy — the first :meth:`get_token` call hits
    the IdP; subsequent calls return the cached string until the cached
    expiry approaches or the caller signals a 401 via :meth:`invalidate`.

    The helper is intentionally synchronous: it composes naturally with
    ``httpx.Client`` (the connector-side HTTP transport), and the
    upstream call is one short request with no per-page state worth
    spinning up an async runtime for.

    Args:
        tenant_id: Microsoft Entra ID tenant GUID (or alias). Required.
        client_id: Azure AD app-registration's application (client) ID.
            Required.
        client_secret: Azure AD app-registration's client secret.
            Required.
        scope: Space-separated OAuth2 scopes. For Microsoft Graph in
            client-credentials mode this is always
            ``"https://graph.microsoft.com/.default"`` — the per-resource
            ``.default`` shape that resolves to the app permissions
            registered on the AAD app. Required.
        token_endpoint: Optional override for non-Microsoft IdPs or
            sovereign clouds (e.g.
            ``https://login.microsoftonline.us/<tenant>/oauth2/v2.0/token``
            for Azure Gov). Defaults to the standard Microsoft Entra
            ID v2 endpoint shape.
        http_client: Optional ``httpx.Client`` for the token exchange.
            Tests inject a recorded-fixture client; production callers
            omit and the helper builds a ``httpx.Client`` per-call (no
            shared pool — token exchange is rare relative to data
            calls).
        time_source: Wall-clock-now callable. Tests inject a controllable
            clock; production callers omit and get :func:`time.time`.

    Raises:
        MissingCredentialsError: If any of ``tenant_id`` / ``client_id``
            / ``client_secret`` / ``scope`` is empty after resolution.
    """

    def __init__(
        self,
        *,
        tenant_id: str,
        client_id: str,
        client_secret: str,
        scope: str,
        token_endpoint: str | None = None,
        http_client: httpx.Client | None = None,
        time_source: Callable[[], float] | None = None,
    ) -> None:
        for field_name, value in (
            ("tenant_id", tenant_id),
            ("client_id", client_id),
            ("client_secret", client_secret),
            ("scope", scope),
        ):
            if not value:
                raise MissingCredentialsError(
                    f"OAuth2ClientCredsAuth: {field_name} is empty. "
                    f"fix: resolve {field_name} via kairix.secrets.get_secret(...) "
                    f"and ensure the underlying secret is set. "
                    f"next: see docs/architecture/connector-ingestion-architecture.md §8 "
                    f"for the M365 connector credential shape."
                )

        self._tenant_id = tenant_id
        self._client_id = client_id
        self._client_secret = client_secret
        self._scope = scope
        self._token_endpoint = token_endpoint or DEFAULT_TOKEN_ENDPOINT_TEMPLATE.format(tenant_id=tenant_id)
        self._http_client = http_client
        # ``time_source`` is the per-test clock seam. Production callers
        # omit and get :func:`time.time`; tests pass a Callable[[], float]
        # so token-expiry can be advanced deterministically without a
        # real ``sleep``.
        self._time_source = time_source if time_source is not None else time.time
        self._cached: _CachedToken | None = None

    def get_token(self) -> str:
        """Return a valid bearer token, refreshing if needed.

        Cache-hit path returns the previously-acquired string in
        constant time. Cache-miss / near-expiry refreshes via the IdP
        and caches the new token + expiry.

        Returns:
            The OAuth2 access-token string (the bearer value, never the
            secret).

        Raises:
            httpx.HTTPError: On network failure / non-2xx response from
                the IdP. Caller is responsible for retry / backoff.
        """
        cached = self._cached
        now = self._now()
        if cached is not None and cached.expires_at - now > REFRESH_SKEW_S:
            logger.debug("oauth2 client-creds: cache hit; reusing token")
            return cached.access_token
        return self._refresh()

    def invalidate(self) -> None:
        """Drop the cached token so the next :meth:`get_token` call
        re-acquires from the IdP.

        Caller signals this when an upstream Graph / Azure call returns
        401 — the token may have been revoked or the cache may be
        stale. F15-clean: no token material appears in the warning log.
        """
        if self._cached is not None:
            logger.info("oauth2 client-creds: token invalidated by caller (401 or explicit signal)")
        self._cached = None

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _refresh(self) -> str:
        """Exchange client-credentials for a fresh access token."""
        logger.info("oauth2 client-creds: token refresh requested")
        payload = {
            "grant_type": "client_credentials",
            "client_id": self._client_id,
            "client_secret": self._client_secret,
            "scope": self._scope,
        }
        client = self._http_client
        if client is not None:
            response = client.post(self._token_endpoint, data=payload, timeout=_TOKEN_REQUEST_TIMEOUT_S)
        else:
            with httpx.Client(timeout=_TOKEN_REQUEST_TIMEOUT_S) as owned:
                response = owned.post(self._token_endpoint, data=payload)
        response.raise_for_status()
        body = response.json()
        access_token = body.get("access_token")
        expires_in = body.get("expires_in")
        if not isinstance(access_token, str) or not access_token:
            raise httpx.HTTPError(
                "oauth2 client-creds: IdP returned no access_token. "
                "fix: verify the AAD app-registration has the required "
                "permissions granted. "
                "next: check the app's API permissions in the Azure portal."
            )
        if not isinstance(expires_in, int) or expires_in <= 0:
            # Defensive: a non-int / missing expires_in means we can't
            # cache safely; treat as a one-shot token (no caching).
            self._cached = None
            return access_token
        self._cached = _CachedToken(
            access_token=access_token,
            expires_at=self._now() + float(expires_in),
        )
        return access_token

    def _now(self) -> float:
        """Return the current wall-clock seconds via the injectable
        time source. Reads the source attribute every call so a test
        swapping the source mid-test takes effect immediately.
        """
        return float(self._time_source())

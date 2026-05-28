"""OAuth2 client-credentials helper for the M365 calendar connector.

Surface:

* :class:`OAuth2ClientCredsAuth` — :class:`httpx.Auth`-compatible
  callable that exchanges the configured tenant + client id + client
  secret for an access token via the Microsoft identity platform
  ``/oauth2/v2.0/token`` endpoint, caches the token in-memory for its
  ``expires_in`` lifetime, and adds the ``Authorization: Bearer …``
  header to every outgoing request.
* :class:`OAuth2Config` — frozen dataclass carrying the three secret
  values and the requested scope.

A second client-credentials helper lives at
:mod:`kairix.transport.auth.oauth2_client_creds` with a different
surface shape (no ``Config`` dataclass, exposes ``get_token()`` /
``invalidate()`` instead of the ``httpx.Auth`` interface). The two
should be unified into one shared helper — see the matching tracking
issue.

The helper does NOT read environment variables — all three secret
values are passed in at construction time from the connector's
``make_connector`` factory. Per F2 / F4 / F15, secrets only move via
constructor arguments, never via ``os.environ`` lookups or plaintext
logging.
"""

from __future__ import annotations

import time
from collections.abc import Generator
from dataclasses import dataclass
from typing import Any

import httpx

# The Microsoft identity platform endpoint format. The tenant id is
# substituted into the URL — that's how OAuth2 client-credentials flows
# scope token issuance to one Azure AD tenant.
_TOKEN_ENDPOINT_TEMPLATE = (
    # F3 rationale: bandit S105 flags the literal because the path
    # carries the substring ``token``; the value is a public Microsoft
    # endpoint URL template, not a hardcoded credential.
    "https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"  # noqa: S105
)

# Default scope for Microsoft Graph. Per ADR-008, application
# permissions (Calendars.Read in this case) are granted at the Azure AD
# app registration, NOT encoded into the OAuth2 scope string.
DEFAULT_GRAPH_SCOPE = "https://graph.microsoft.com/.default"

# Tokens may legitimately be issued for 3600s. We refresh slightly early
# so a request that begins under the previous token never sees a 401.
_REFRESH_LEEWAY_SECONDS = 60


@dataclass(frozen=True)
class OAuth2Config:
    """Frozen configuration for an OAuth2 client-credentials exchange.

    All three values must be sourced from the connector's secret
    resolution path (operator-injected, never from env vars). The
    ``scope`` argument defaults to :data:`DEFAULT_GRAPH_SCOPE`.

    Per F15 the fields are named with the ``client_secret`` /
    ``tenant_id`` shape so the secret-logging gate flags any plaintext
    interpolation outside ``kairix/{secrets,credentials}.py``.
    """

    tenant_id: str
    client_id: str
    client_secret: str
    scope: str = DEFAULT_GRAPH_SCOPE


class OAuth2ClientCredsAuth(httpx.Auth):
    """httpx-compatible auth flow that injects a Bearer token.

    The token is fetched on first use and cached for its ``expires_in``
    lifetime less a small leeway. The helper is intentionally
    synchronous — the connector only does occasional Graph calls (one
    per sync tick) so async pooling has no benefit and adds wiring
    complexity.

    DI seam: ``token_fetcher`` defaults to the production HTTP call but
    tests inject a callable returning a scripted ``(token, expires_at)``
    pair, avoiding any real network I/O.
    """

    def __init__(
        self,
        config: OAuth2Config,
        token_fetcher: TokenFetcher | None = None,
        clock: Clock | None = None,
    ) -> None:
        self._config = config
        self._token_fetcher: TokenFetcher = token_fetcher or _fetch_token_from_microsoft
        self._clock: Clock = clock or time.time
        self._cached_token: str | None = None
        self._cached_expires_at: float = 0.0

    def auth_flow(self, request: httpx.Request) -> Generator[httpx.Request, httpx.Response, None]:
        """httpx-Auth interface — yield the request with a Bearer token.

        Per F15, the cached token is never logged or interpolated into
        an exception message — only the prefix the operator needs to
        diagnose a misconfiguration.
        """
        token = self._get_or_refresh_token()
        request.headers["Authorization"] = f"Bearer {token}"
        yield request

    def _get_or_refresh_token(self) -> str:
        now = self._clock()
        if self._cached_token is not None and now < self._cached_expires_at:
            return self._cached_token
        token, expires_in = self._token_fetcher(self._config)
        self._cached_token = token
        self._cached_expires_at = now + max(0.0, float(expires_in) - _REFRESH_LEEWAY_SECONDS)
        return token


# Function-type aliases for the DI seams. The TokenFetcher returns
# (access_token, expires_in_seconds). Clock returns the current epoch
# seconds — matches ``time.time``.
from collections.abc import Callable  # noqa: E402 — alias declarations after the public surface

TokenFetcher = Callable[[OAuth2Config], "tuple[str, float]"]
Clock = Callable[[], float]


def _fetch_token_from_microsoft(config: OAuth2Config) -> tuple[str, float]:
    """Default token fetcher — issues a POST to the v2.0 token endpoint.

    Returns ``(access_token, expires_in_seconds)``. Raises on any
    non-2xx response. Per F15, exception messages never carry the
    secret values — only the tenant id (already considered a public
    operator identifier).
    """
    endpoint = _TOKEN_ENDPOINT_TEMPLATE.format(tenant_id=config.tenant_id)
    form: dict[str, Any] = {
        "grant_type": "client_credentials",
        "client_id": config.client_id,
        "client_secret": config.client_secret,
        "scope": config.scope,
    }
    response = httpx.post(endpoint, data=form, timeout=30.0)
    if response.status_code >= 400:
        raise OAuth2Error(
            f"OAuth2 token exchange failed for tenant {config.tenant_id!r}: "
            f"status={response.status_code}. "
            "fix: confirm the Azure AD app registration grants Calendars.Read "
            "application permission, the client_id matches the registration, "
            "and the client_secret is current (not expired). "
            "next: see docs/architecture/connector-ingestion-architecture.md §10."
        )
    payload = response.json()
    access_token = payload.get("access_token")
    expires_in = payload.get("expires_in", 3600.0)
    if not isinstance(access_token, str) or not access_token:
        raise OAuth2Error(
            f"OAuth2 token response from tenant {config.tenant_id!r} is missing access_token. "
            "fix: validate the tenant id is the directory tenant id, not the application tenant. "
            "next: see docs/architecture/connector-ingestion-architecture.md §10."
        )
    return access_token, float(expires_in)


class OAuth2Error(RuntimeError):
    """Raised when the OAuth2 client-credentials exchange fails.

    Pure-message exception — F15 forbids the helper from interpolating
    the cached token or the client secret into an exception message.
    The endpoint and tenant id are public configuration values, so they
    appear in the message as actionable affordance for the operator.
    """

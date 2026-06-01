"""Protocol surface for the ``kairix connect`` OAuth2 flow.

Every concrete implementation in :mod:`kairix.connect` satisfies one of
the Protocols here. Tests substitute fakes from ``tests/fakes.py`` that
satisfy the same Protocols without inheritance — the canonical kairix
dependency-inversion shape.

Per ADR-032 §"Protocols (the SOLID dependency-inversion boundary)" and
the F42 contract (all return shapes are frozen dataclasses or tuples,
never bare ``dict[str, Any]``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from kairix.secrets.naming import Scope

# ---------------------------------------------------------------------------
# Value objects (frozen dataclasses — F42 compliant)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ClientCredentials:
    """OAuth2 client credentials read from the operator-supplied source.

    For Google these come from a ``client_secret.json`` download from
    the GCP console; for Slack from the api.slack.com app config; for
    GitHub from the GitHub App settings page. The connect flow never
    invents these — the operator provisions them once per service in
    the upstream developer console.
    """

    client_id: str
    client_secret: str


@dataclass(frozen=True)
class CapturedTokens:
    """Tokens captured by completing one OAuth2 authorization flow.

    ``refresh_token`` is the long-lived credential the connector uses to
    mint fresh ``access_token``s; ``access_token`` is the short-lived
    bearer for immediate use. Google grants a refresh_token only when
    the consent screen runs in "consent" prompt mode and the OAuth
    consent screen is in Production state — see the connect README's
    GCP setup walkthrough.

    ``bot_token`` + ``app_token`` carry the Slack-shape long-lived
    tokens (``xoxb-…`` workspace bot token; ``xapp-…`` app-level token
    for Socket Mode). Slack returns no refresh_token (bot tokens never
    expire), so :class:`kairix.connect.oauth2.slack.SlackOAuth2Flow`
    sets ``refresh_token=""`` and populates ``bot_token`` (and
    optionally ``app_token``) instead. Token stores skip empty-string
    leaves at write time, so each service writes only the leaves it
    actually captured — Google writes 4 (client-id, client-secret,
    refresh-token, access-token); Slack writes 3 (client-id,
    client-secret, bot-token) plus app-token when Socket Mode is wired.

    F15-sensitive: token values live here only to round-trip from the
    OAuth exchange into a ``TokenStore``; this dataclass is never
    serialised to logs.
    """

    refresh_token: str
    access_token: str
    token_uri: str
    expires_in: int | None = None
    bot_token: str = ""
    app_token: str = ""


@dataclass(frozen=True)
class CallbackResult:
    """The successful callback from the operator's browser flow.

    The ``code`` is the OAuth authorization code the listener captured
    from the redirect URI query string; the listener does not exchange
    it for tokens (that responsibility lives in ``OAuth2Flow.authorize``).
    """

    code: str
    state: str | None = None


@dataclass(frozen=True)
class WriteReport:
    """What a ``TokenStore.store`` call actually did.

    ``canonical_names`` is the tuple of canonical KV / env var names the
    store wrote (e.g. ``("kairix-connector-gmail-client-id",
    "kairix-connector-gmail-client-secret", ...)``). ``backend`` names
    the concrete store implementation for the operator-facing summary
    line printed by the CLI.
    """

    canonical_names: tuple[str, ...]
    backend: str
    target: str = ""  # e.g. file path, vault URL, or "<stdout>"


# ---------------------------------------------------------------------------
# Protocols
# ---------------------------------------------------------------------------


@runtime_checkable
class CallbackListener(Protocol):
    """Localhost HTTP server that catches the OAuth2 redirect callback.

    Lifecycle: construct with a desired port; ``redirect_uri`` returns
    the URL the OAuth provider should redirect to; ``wait_for_callback``
    blocks until the operator completes the browser flow (or the timeout
    elapses); ``close`` releases the underlying socket.
    """

    @property
    def redirect_uri(self) -> str:
        """Return the URL the OAuth provider should redirect to.

        Format ``http://127.0.0.1:<port>/oauth2callback``. The listener
        owns the port — callers must read this property AFTER construction
        because the port may have been advanced from the operator's
        requested value when the requested port was already in use.
        """
        ...

    def wait_for_callback(self, timeout_s: float = 120.0) -> CallbackResult:
        """Block until a callback request arrives, then return it.

        Raises :class:`CallbackTimeoutError` if no request arrives within
        ``timeout_s``. Raises :class:`CallbackDeniedError` if the
        callback URI carries an ``error=access_denied`` query parameter
        (operator clicked "Cancel" on the consent screen).
        """
        ...

    def close(self) -> None:
        """Release the listening socket. Idempotent."""
        ...


@runtime_checkable
class OAuth2Flow(Protocol):
    """One operator-driven OAuth2 authorization flow for one service.

    Lifecycle:

      1. :meth:`discover_client_credentials` — read ``client_id`` and
         ``client_secret`` from the operator-supplied source.
      2. :meth:`authorize` — open browser, capture callback via the
         injected ``CallbackListener``, exchange code for tokens.
      3. Caller passes the returned :class:`CapturedTokens` to a
         :class:`TokenStore`.

    Per ADR-032: the flow exists per-service-area (gmail / google-drive /
    google-calendar share one implementation class with three instances;
    slack / github-app are separate implementations under
    ``kairix/connect/oauth2/``).
    """

    service_area: str
    """Canonical service-area string fed to ``canonical_secret_name``.

    Examples: ``"gmail"``, ``"google-drive"``, ``"google-calendar"``,
    ``"slack"``, ``"github"``.
    """

    scopes: tuple[str, ...]
    """The provider-specific scope strings requested at the consent screen."""

    def discover_client_credentials(self) -> ClientCredentials:
        """Read the OAuth client credentials from the operator-supplied source.

        Raises :class:`FileNotFoundError` if the source path doesn't
        exist (operator hasn't downloaded the file yet) with an F21
        ``fix:``/``next:``/``run:`` remediation message.
        """
        ...

    def authorize(self, *, listener: CallbackListener) -> CapturedTokens:
        """Run the full authorize-and-exchange dance using ``listener``.

        Steps:
          1. Build the authorize URL with the listener's ``redirect_uri``.
          2. Open the operator's browser to that URL.
          3. Block on ``listener.wait_for_callback`` for the code.
          4. Exchange the code for tokens via the service's token endpoint.
          5. Return the typed :class:`CapturedTokens`.
        """
        ...


@runtime_checkable
class TokenStore(Protocol):
    """Writes captured tokens to canonical names via the operator's backend.

    The store applies ADR-031 canonical naming
    (``kairix-<scope>-<area>[-<instance>]-<leaf>``) — implementations
    MUST NOT re-derive names independently; they call
    :func:`kairix.secrets.naming.canonical_secret_name`.
    """

    def store(
        self,
        *,
        scope: Scope,
        area: str,
        instance: str | None,
        tokens: CapturedTokens,
        client: ClientCredentials,
    ) -> WriteReport:
        """Write all four canonical secrets (client-id, client-secret,
        refresh-token, access-token) for one identity tuple.

        Raises :class:`TokenStoreUnauthorizedError` if the backend
        rejects the write (KV permission denied, file write blocked).
        """
        ...


@runtime_checkable
class RefreshableToken(Protocol):
    """Connector-side wrapper that auto-refreshes tokens before HTTP calls.

    Connectors hold one of these per cc_pair; their HTTP client calls
    :meth:`headers` on every request. If the token has expired,
    :meth:`headers` triggers :meth:`refresh` transparently. The
    connector never sees refresh-token specifics.
    """

    def headers(self) -> dict[str, str]:
        """Return the auth headers (typically ``Authorization: Bearer ...``).

        Refreshes the access token transparently if it's expired.
        """
        ...

    def is_expired(self) -> bool:
        """Return ``True`` if the current access token is past its expiry."""
        ...

    def refresh(self) -> None:
        """Force a refresh of the access token using the refresh_token.

        Raises :class:`RefreshUnavailableError` if the refresh attempt
        fails (network down, refresh_token revoked, service unavailable).
        """
        ...


@runtime_checkable
class BrowserLauncher(Protocol):
    """Opens the operator's browser to an authorize URL.

    Default production impl wraps :func:`webbrowser.open`. Tests inject
    a recording fake so no real Chrome window opens during pytest.
    """

    def open(self, url: str) -> bool:
        """Open ``url`` in the operator's default browser.

        Returns ``True`` on success, ``False`` if no browser could be
        located (headless VM, missing $DISPLAY).
        """
        ...


# ---------------------------------------------------------------------------
# Exception types
# ---------------------------------------------------------------------------


class ConnectError(Exception):
    """Base class for all ``kairix.connect`` errors.

    Every subclass carries an F21-compliant message with ``fix:``,
    ``next:``, and (where applicable) ``run:`` action markers.
    """


class CallbackTimeoutError(ConnectError):
    """The operator never completed the browser flow within the timeout."""


class CallbackDeniedError(ConnectError):
    """The operator clicked Cancel / Deny on the consent screen."""


class TokenStoreUnauthorizedError(ConnectError):
    """The token store backend rejected the write."""


class RefreshUnavailableError(ConnectError):
    """A refresh attempt failed (network down, refresh_token revoked)."""


__all__ = [
    "BrowserLauncher",
    "CallbackDeniedError",
    "CallbackListener",
    "CallbackResult",
    "CallbackTimeoutError",
    "CapturedTokens",
    "ClientCredentials",
    "ConnectError",
    "OAuth2Flow",
    "RefreshUnavailableError",
    "RefreshableToken",
    "TokenStore",
    "TokenStoreUnauthorizedError",
    "WriteReport",
]


# Silence F-rule unused-import on field (we may extend dataclasses later).
_ = field

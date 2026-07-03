"""Shared authorization-code OAuth2 flow base — one copy of the dance.

Google and Slack both run the identical two-legged authorization-code
sequence: discover the client credentials, build the authorize URL, open
the operator's browser, block on the localhost listener for the redirect,
then exchange the captured code for tokens. Only the URL-building and
code-exchange steps differ per service; the orchestration is the same.

:class:`AuthorizationCodeFlow` owns that orchestration once so
``kairix connect --timeout`` threads into ``listener.wait_for_callback``
in a SINGLE place. Per-service subclasses (Google, Slack) supply the
three service-specific hooks; GitHub App keeps its own ``authorize``
because its install flow captures an ``installation_id`` rather than a
code (a genuinely different shape, so no shared copy applies there).

The template-method shape also removes the pre-existing duplication of
the ``authorize`` body across ``google.py`` and ``slack.py``.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from kairix.connect.protocols import (
    BrowserLauncher,
    CallbackListener,
    CapturedTokens,
    ClientCredentials,
)

# Default listener wait budget, in seconds. Mirrors the ``--timeout``
# argparse default in ``kairix/connect/cli.py`` so a caller that omits the
# flag (e.g. the setup wizard's OAuth path) gets the same 120s ceiling.
DEFAULT_CALLBACK_TIMEOUT_S = 120.0


class AuthorizationCodeFlow(ABC):
    """Template-method base for the shared authorization-code dance.

    Subclasses provide :meth:`discover_client_credentials`,
    :meth:`_build_authorize_url`, and :meth:`_exchange_code`, and set
    ``self._browser`` in their constructor. This base owns the single
    copy of :meth:`authorize`, so the operator-supplied ``timeout_s``
    reaches ``listener.wait_for_callback`` in exactly one place.
    """

    _browser: BrowserLauncher

    @abstractmethod
    def discover_client_credentials(self) -> ClientCredentials:
        """Read the OAuth client credentials from the operator source."""

    @abstractmethod
    def _build_authorize_url(self, client: ClientCredentials, redirect_uri: str) -> str:
        """Build the service's authorize URL for ``redirect_uri``."""

    @abstractmethod
    def _exchange_code(self, client: ClientCredentials, code: str, redirect_uri: str) -> CapturedTokens:
        """Exchange the captured ``code`` for tokens via the service endpoint."""

    def authorize(
        self,
        *,
        listener: CallbackListener,
        timeout_s: float = DEFAULT_CALLBACK_TIMEOUT_S,
    ) -> CapturedTokens:
        """Run the consent dance + token exchange against ``listener``.

        Steps:
          1. Discover the client credentials.
          2. Build the authorize URL with the listener's ``redirect_uri``.
          3. Open the operator's browser to the consent screen.
          4. Block on ``listener.wait_for_callback`` for the code,
             honouring the operator-supplied ``timeout_s``
             (``kairix connect --timeout``) so the flag is threaded
             through rather than silently ignored (#498).
          5. Exchange the code for tokens via the service endpoint.
        """
        client = self.discover_client_credentials()
        redirect_uri = listener.redirect_uri
        authorize_url = self._build_authorize_url(client, redirect_uri)
        self._browser.open(authorize_url)
        callback = listener.wait_for_callback(timeout_s=timeout_s)
        return self._exchange_code(client, callback.code, redirect_uri)


__all__ = ["DEFAULT_CALLBACK_TIMEOUT_S", "AuthorizationCodeFlow"]

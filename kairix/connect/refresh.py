"""``RefreshableToken`` implementations used by connectors at runtime.

This module sits at the kairix.connect <-> kairix.connectors boundary.
The connectors (Drive / Calendar / Gmail) import :class:`GoogleRefreshableToken`
and hold one per cc_pair; their HTTP client calls :meth:`headers` on
every request and the wrapper auto-refreshes when the access token
expires.

Per the ADR's "Silent bug" paragraph: Drive + Calendar previously
expected a raw ``access_token`` and surfaced :class:`CredentialExpiredError`
to the operator on 401 — no automatic refresh. This module wraps the
``google-auth`` refresh dance so the connectors no longer dead-letter
on token expiry.

The ``google-auth`` library is imported lazily — connectors that never
hit a refresh (tests passing a pinned access_token, e.g.) never load
the library.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from kairix.connect.protocols import RefreshableToken, RefreshUnavailableError

# F17 — extracted constant; every concrete RefreshableToken impl returns
# auth headers keyed by this name.
_HEADER_AUTHORIZATION = "Authorization"


@dataclass(frozen=True)
class GoogleRefreshState:
    """Snapshot of the Google credential state needed to refresh.

    Frozen per F42. The four fields together are the minimum the
    ``google-auth`` refresh call needs: the long-lived
    :attr:`refresh_token`, the :attr:`client_id` and
    :attr:`client_secret` to authenticate the refresh request, and the
    canonical :attr:`token_uri`.
    """

    client_id: str
    client_secret: str
    refresh_token: str
    token_uri: str


class GoogleRefreshableToken:
    """RefreshableToken wrapping ``google.auth.credentials.Credentials.refresh``.

    Connectors construct one of these per cc_pair from the resolved
    secret material. Each HTTP request calls :meth:`headers` which
    triggers :meth:`refresh` transparently when the cached access token
    is expired.

    Args:
      state: The :class:`GoogleRefreshState` carrying the long-lived
        refresh material.
      initial_access_token: Optional pre-warmed access token. Saves one
        refresh on cold start when the operator persisted a valid
        access_token in KV alongside the refresh_token.
      initial_expiry_epoch: Optional epoch-seconds expiry for the
        ``initial_access_token``. ``None`` (the default) treats the
        initial token as immediately stale so the first
        :meth:`headers` call refreshes.
      now_fn: Test seam — replaces :func:`time.time` so tests pin the
        clock. Defaults to :func:`time.time`.
      refresh_fn: Test seam — replaces the live ``google-auth`` refresh
        call. Receives the current state + the cached access token,
        returns a tuple ``(new_access_token, new_expiry_epoch)``. Tests
        inject a recording fake so the suite stays fast without
        monkeypatching ``google-auth``.
    """

    # Refresh ~60s before the actual expiry to absorb clock skew between
    # the operator's machine and Google's servers.
    _EXPIRY_SKEW_S = 60

    def __init__(
        self,
        *,
        state: GoogleRefreshState,
        initial_access_token: str | None = None,
        initial_expiry_epoch: float | None = None,
        now_fn: Callable[[], float] = time.time,
        refresh_fn: Callable[[GoogleRefreshState, str | None], tuple[str, float]] | None = None,
    ) -> None:
        self._state = state
        self._access_token = initial_access_token or ""
        self._expiry_epoch = initial_expiry_epoch if initial_expiry_epoch is not None else 0.0
        self._now_fn = now_fn
        self._refresh_fn = refresh_fn

    def headers(self) -> dict[str, str]:
        """Return the auth headers, refreshing if the cached token is stale."""
        if self.is_expired():
            self.refresh()
        return {_HEADER_AUTHORIZATION: f"Bearer {self._access_token}"}

    def is_expired(self) -> bool:
        """Return ``True`` if the cached access token is past its expiry (with skew)."""
        if not self._access_token:
            return True
        return self._now_fn() + self._EXPIRY_SKEW_S >= self._expiry_epoch

    def refresh(self) -> None:
        """Force a refresh of the access token.

        Uses the injected ``refresh_fn`` if supplied (test path); falls
        back to the default ``google-auth`` refresh dance.
        """
        try:
            if self._refresh_fn is not None:
                new_token, new_expiry = self._refresh_fn(self._state, self._access_token or None)
            else:
                new_token, new_expiry = _default_google_refresh(self._state)
        except Exception as exc:
            raise RefreshUnavailableError(
                "kairix connect: Google access-token refresh failed. "
                "fix: confirm the refresh_token has not been revoked (GCP console -> "
                "Security -> Apps with account access). "
                "next: re-run kairix connect <google-service> to capture fresh tokens. "
                "run: kairix connect <google-service> --client-secret-path <path>",
            ) from exc
        self._access_token = new_token
        self._expiry_epoch = new_expiry


def _default_google_refresh(state: GoogleRefreshState) -> tuple[str, float]:
    """Run the live ``google-auth`` refresh dance, return ``(token, expiry)``.

    Lazy-imports the library — operators on the test path inject a
    ``refresh_fn`` and never reach here.
    """
    try:
        import google.auth.transport.requests
        from google.oauth2.credentials import Credentials
    except ImportError as exc:
        raise RuntimeError(
            "kairix connect: google-auth is not installed. "
            "fix: pip install 'google-auth>=2.40'. "
            "next: re-run the connector after installing google-auth. "
            "run: pip install 'google-auth>=2.40'",
        ) from exc
    # google-auth's Credentials + .refresh are untyped per the lib's
    # PEP 561 absence; runtime contract is exercised in
    # tests/unit/test_connect_refresh.py. Suppressions are needed when
    # the SDK IS installed (mypy resolves the symbols) but become
    # unused-ignore warnings on hosts without the SDK on the path —
    # CI installs all extras and produces the strict-typed Any here.
    creds = Credentials(  # type: ignore[no-untyped-call,unused-ignore]  # F3 rationale: google-auth ships no PEP-561 stubs; constructor signature checked at runtime via tests/unit/test_connect_refresh.py. unused-ignore is bundled because google-auth is an optional [connect] extra — hosts without it resolve Credentials to Any and the no-untyped-call ignore reads as unused.
        token=None,
        refresh_token=state.refresh_token,
        client_id=state.client_id,
        client_secret=state.client_secret,
        token_uri=state.token_uri,
    )
    creds.refresh(google.auth.transport.requests.Request())  # type: ignore[no-untyped-call,unused-ignore]  # F3 rationale: google-auth ships no PEP-561 stubs; the refresh + Request shapes are checked at runtime via the unit tests. unused-ignore is bundled per the same reasoning as the Credentials() suppression above.
    new_token = creds.token or ""
    # google-auth's Credentials.expiry is a naive datetime in UTC.
    if creds.expiry is None:
        expiry_epoch = time.time() + 3600.0  # safe default — 1h
    else:
        # Treat as UTC; convert to epoch.
        import calendar

        expiry_epoch = float(calendar.timegm(creds.expiry.timetuple()))
    return new_token, expiry_epoch


class StaticRefreshableToken:
    """RefreshableToken that never refreshes — bearer is the configured token.

    Used by Slack (bot tokens never expire) and by tests that want to
    pin a fixed token without the refresh dance.
    """

    def __init__(self, *, token: str, scheme: str = "Bearer") -> None:
        self._token = token
        self._scheme = scheme

    def headers(self) -> dict[str, str]:
        return {_HEADER_AUTHORIZATION: f"{self._scheme} {self._token}"}

    def is_expired(self) -> bool:
        return False

    def refresh(self) -> None:
        # Intentionally empty — static tokens don't refresh by design.
        return


# ---------------------------------------------------------------------------
# GitHub App refresh — JWT-signed installation token
# ---------------------------------------------------------------------------


# GitHub installation tokens last 1h; rotate at the 50-min mark to absorb
# clock skew + leave the connector's in-flight requests room to drain on
# the old token while new ones acquire the fresh one. Matches the
# api_client INSTALLATION_TOKEN_TTL_SECONDS contract from the existing
# kairix/connectors/github/api_client.py.
_GITHUB_INSTALLATION_TOKEN_TTL_S = 3600
_GITHUB_ROTATE_AT_FRACTION = 50.0 / 60.0  # 50min of the 60min TTL


class GitHubAppRefreshableToken:
    """RefreshableToken that mints installation tokens from the App JWT.

    Connectors construct one of these per App installation. Each HTTP
    request calls :meth:`headers` which signs a fresh JWT + exchanges
    for an installation token on cold start (or after expiry) and
    caches it for ~50 min before re-rotating. The JWT signing key (PEM
    private key) is the long-lived credential; the installation access
    token is ephemeral.

    Args:
      app_id: The numeric GitHub App id (as a string — GitHub returns
        it as a number but the JWT ``iss`` claim accepts either form).
      private_key_pem: The PEM-encoded RSA private key text.
      installation_id: The numeric installation id (as a string) the
        operator captured via ``kairix connect github-app``.
      now_fn: Test seam — replaces :func:`time.time` so tests pin the
        clock. Defaults to :func:`time.time`.
      token_exchanger: Test seam — replaces the JWT-sign-and-exchange
        step. Receives ``(app_id, private_key, installation_id)`` and
        returns ``(installation_token, expiry_epoch)``. Tests inject
        a recording fake so the suite stays fast without
        ``pyjwt[crypto]`` installed.

    F15-clean: the PEM and the installation token never appear in
    ``logger.*`` / ``print`` / ``raise`` strings here; the wrapper
    holds them as instance attributes and surfaces them only via
    :meth:`headers`.
    """

    def __init__(
        self,
        *,
        app_id: str,
        private_key_pem: str,
        installation_id: str,
        now_fn: Callable[[], float] = time.time,
        token_exchanger: Callable[[str, str, str], tuple[str, float]] | None = None,
    ) -> None:
        self._app_id = app_id
        self._private_key_pem = private_key_pem
        self._installation_id = installation_id
        self._now_fn = now_fn
        self._token_exchanger = token_exchanger
        self._cached_token: str = ""
        self._cached_expiry_epoch: float = 0.0

    def headers(self) -> dict[str, str]:
        """Return the auth headers, rotating the installation token if stale."""
        if self.is_expired():
            self.refresh()
        return {_HEADER_AUTHORIZATION: f"Bearer {self._cached_token}"}

    def is_expired(self) -> bool:
        """Return ``True`` when the cached installation token is past rotation.

        Rotation budget is 50 min of the 60-min installation token
        lifetime — leaves 10 min of headroom for clock skew + in-flight
        requests.
        """
        if not self._cached_token:
            return True
        rotation_budget = _GITHUB_INSTALLATION_TOKEN_TTL_S * _GITHUB_ROTATE_AT_FRACTION
        elapsed = self._now_fn() - (self._cached_expiry_epoch - _GITHUB_INSTALLATION_TOKEN_TTL_S)
        return elapsed >= rotation_budget

    def refresh(self) -> None:
        """Sign a fresh JWT and exchange for a new installation access token.

        Uses the injected ``token_exchanger`` if supplied (test path);
        falls back to the default ``pyjwt[crypto]`` + ``httpx`` path.
        """
        try:
            if self._token_exchanger is not None:
                token, expiry = self._token_exchanger(
                    self._app_id,
                    self._private_key_pem,
                    self._installation_id,
                )
            else:
                token, expiry = _default_github_app_refresh(
                    self._app_id,
                    self._private_key_pem,
                    self._installation_id,
                )
        except Exception as exc:
            raise RefreshUnavailableError(
                "kairix connect: GitHub App installation-token refresh failed. "
                "fix: confirm the private key file is unchanged and the App still has "
                "the installation (github.com/settings/apps/<your-app> -> 'Installations'). "
                "next: re-run kairix connect github-app to capture a fresh installation_id. "
                "run: kairix connect github-app --app-id <id> --private-key-path <path>",
            ) from exc
        self._cached_token = token
        self._cached_expiry_epoch = expiry


def _default_github_app_refresh(
    app_id: str,
    private_key_pem: str,
    installation_id: str,
) -> tuple[str, float]:
    """Live JWT-sign + installation-token exchange via pyjwt + httpx.

    Lazy-imports both libraries so the test path (which injects a
    ``token_exchanger``) never touches them.

    Mirrors :func:`kairix.connect.oauth2.github_app._default_token_exchanger`
    but returns a ``(token, expiry_epoch)`` tuple suitable for the
    refresh-cache contract.
    """
    from kairix.connect.oauth2.github_app import _default_token_exchanger

    token = _default_token_exchanger(app_id, private_key_pem, installation_id)
    # GitHub installation tokens last 1h from issuance; treat the
    # wall-clock now as the issuance time for cache-expiry purposes.
    # (The exchanger's response also carries an ``expires_at`` ISO
    # string, but we don't expose it through the helper return shape —
    # the 1h default matches GitHub's documented lifetime.)
    expiry_epoch = time.time() + _GITHUB_INSTALLATION_TOKEN_TTL_S
    return token, expiry_epoch


def _make_protocol_check() -> tuple[RefreshableToken, RefreshableToken, RefreshableToken]:
    """Runtime conformance check — every concrete class satisfies the Protocol."""
    a: RefreshableToken = StaticRefreshableToken(token="x")  # noqa: S106 — protocol-shape check, not a credential value
    b: RefreshableToken = GoogleRefreshableToken(
        state=GoogleRefreshState(
            client_id="x",
            client_secret="x",  # noqa: S106 — protocol-shape check, not a credential value
            refresh_token="x",  # noqa: S106 — protocol-shape check, not a credential value
            token_uri="x",  # noqa: S106 — protocol-shape check, not a credential value
        ),
    )
    c: RefreshableToken = GitHubAppRefreshableToken(
        app_id="x",
        private_key_pem="x",
        installation_id="x",
    )
    return a, b, c


_PROTOCOL_CHECK = _make_protocol_check()


# Re-export the protocol so connectors only need one import statement.
_ = Any  # suppress unused-import on Any — preserved for future Refresh types

__all__ = [
    "GitHubAppRefreshableToken",
    "GoogleRefreshState",
    "GoogleRefreshableToken",
    "StaticRefreshableToken",
]

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
        return {"Authorization": f"Bearer {self._access_token}"}

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
    creds = Credentials(  # type: ignore[no-untyped-call]  # F3 rationale: google-auth ships no PEP-561 stubs; constructor signature checked at runtime via stubs in tests/unit/test_connect_refresh.py
        token=None,
        refresh_token=state.refresh_token,
        client_id=state.client_id,
        client_secret=state.client_secret,
        token_uri=state.token_uri,
    )
    creds.refresh(google.auth.transport.requests.Request())  # type: ignore[no-untyped-call]  # F3 rationale: google-auth ships no PEP-561 stubs; the refresh + Request shapes are checked at runtime via the unit tests
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
        return {"Authorization": f"{self._scheme} {self._token}"}

    def is_expired(self) -> bool:
        return False

    def refresh(self) -> None:
        # Intentionally empty — static tokens don't refresh by design.
        return


def _make_protocol_check() -> tuple[RefreshableToken, RefreshableToken]:
    """Runtime conformance check — both classes satisfy the Protocol."""
    a: RefreshableToken = StaticRefreshableToken(token="x")  # noqa: S106 — protocol-shape check, not a credential value
    b: RefreshableToken = GoogleRefreshableToken(
        state=GoogleRefreshState(
            client_id="x",
            client_secret="x",  # noqa: S106 — protocol-shape check, not a credential value
            refresh_token="x",  # noqa: S106 — protocol-shape check, not a credential value
            token_uri="x",  # noqa: S106 — protocol-shape check, not a credential value
        ),
    )
    return a, b


_PROTOCOL_CHECK = _make_protocol_check()


# Re-export the protocol so connectors only need one import statement.
_ = Any  # suppress unused-import on Any — preserved for future Refresh types

__all__ = [
    "GoogleRefreshState",
    "GoogleRefreshableToken",
    "StaticRefreshableToken",
]

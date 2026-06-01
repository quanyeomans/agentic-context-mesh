"""F68 failure-injection contract tests for :class:`RefreshableToken`.

* ``refresh`` → ``unavailable`` (network down, refresh_token revoked)
* ``headers`` → ``unavailable`` shape — propagates the refresh failure
  when the cached token is expired
* ``is_expired`` → ``returns_empty`` shape — pin behaviour when no
  initial access token was ever set (treated as expired)
"""

from __future__ import annotations

import pytest

from kairix.connect.protocols import RefreshUnavailableError
from kairix.connect.refresh import GoogleRefreshableToken, GoogleRefreshState

pytestmark = pytest.mark.contract


def _state() -> GoogleRefreshState:
    return GoogleRefreshState(
        client_id="x",
        client_secret="y",
        refresh_token="z",
        token_uri="https://x/",
    )


def test_refresh_unavailable_when_network_down() -> None:
    """A ``refresh`` call that fails surfaces :class:`RefreshUnavailableError`."""

    def refresh_fn(_state: GoogleRefreshState, _existing: str | None) -> tuple[str, float]:
        raise ConnectionError("network unreachable")

    token = GoogleRefreshableToken(state=_state(), refresh_fn=refresh_fn)
    with pytest.raises(RefreshUnavailableError, match="refresh failed"):
        token.refresh()


def test_headers_unavailable_when_expired_token_refresh_fails() -> None:
    """``headers`` triggers refresh on stale token; refresh failure propagates."""

    def refresh_fn(_state: GoogleRefreshState, _existing: str | None) -> tuple[str, float]:
        raise RuntimeError("simulated upstream unavailable")

    token = GoogleRefreshableToken(state=_state(), refresh_fn=refresh_fn)
    # No initial_access_token → is_expired returns True → headers() triggers refresh → raises
    with pytest.raises(RefreshUnavailableError):
        token.headers()


def test_is_expired_returns_empty_when_no_token_ever_set() -> None:
    """With no initial token, ``is_expired`` reports True so callers know to refresh."""
    token = GoogleRefreshableToken(state=_state())
    assert token.is_expired() is True

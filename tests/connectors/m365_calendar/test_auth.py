"""Unit tests for :mod:`kairix.connectors.m365_calendar.auth`.

Drives the OAuth2 token-cache + auth-flow logic with a scripted token
fetcher and a scripted clock so the cache-refresh path is exercised
without any real network I/O or wall-clock dependency.

F1-clean (no monkey-patching), F8 carries ``@pytest.mark.unit``.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from kairix.connectors.m365_calendar.auth import (
    DEFAULT_GRAPH_SCOPE,
    OAuth2ClientCredsAuth,
    OAuth2Config,
    OAuth2Error,
)


def _config() -> OAuth2Config:
    return OAuth2Config(
        tenant_id="placeholder-tenant",
        client_id="placeholder-client",
        client_secret="placeholder-secret",  # pragma: allowlist secret
    )


# ---------------------------------------------------------------------------
# OAuth2Config + default-scope
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_oauth2_config_default_scope_targets_graph() -> None:
    """Default scope points at Microsoft Graph's default-permissions endpoint.

    Sabotage-proof: change the constant; this test fails on the
    equality assertion.
    """
    config = _config()
    assert config.scope == DEFAULT_GRAPH_SCOPE
    assert DEFAULT_GRAPH_SCOPE == "https://graph.microsoft.com/.default"


@pytest.mark.unit
def test_oauth2_config_is_frozen() -> None:
    """The config dataclass is frozen — mutating raises FrozenInstanceError.

    Sabotage-proof: drop ``frozen=True`` from the decorator; this test
    fails because the mutation then succeeds.
    """
    config = _config()
    with pytest.raises(AttributeError):
        config.tenant_id = "different"  # type: ignore[misc]  # F3 rationale: assert frozen-dc immutability


# ---------------------------------------------------------------------------
# OAuth2ClientCredsAuth — token cache + refresh
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_auth_flow_attaches_bearer_token() -> None:
    """The auth flow appends an Authorization header on each request.

    Sabotage-proof: drop the header-append line in :meth:`auth_flow`;
    this test fails because the header is then absent.
    """
    fetcher_calls: list[OAuth2Config] = []

    def _fetcher(config: OAuth2Config) -> tuple[str, float]:
        fetcher_calls.append(config)
        return ("scripted-token", 3600.0)

    auth = OAuth2ClientCredsAuth(_config(), token_fetcher=_fetcher, clock=lambda: 0.0)

    request = httpx.Request("GET", "https://graph.microsoft.com/v1.0/me")
    flow = auth.auth_flow(request)
    next(flow)
    try:
        flow.send(httpx.Response(200))
    except StopIteration:
        pass

    assert request.headers["Authorization"] == "Bearer scripted-token"
    assert len(fetcher_calls) == 1


@pytest.mark.unit
def test_token_is_cached_until_expiry() -> None:
    """A second request inside the cache window reuses the cached token.

    Sabotage-proof: drop the cache check in :meth:`_get_or_refresh_token`;
    this test fails because the fetcher fires twice.
    """
    fetcher_calls = {"n": 0}

    def _fetcher(_c: OAuth2Config) -> tuple[str, float]:
        fetcher_calls["n"] += 1
        return ("scripted-token", 3600.0)

    auth = OAuth2ClientCredsAuth(_config(), token_fetcher=_fetcher, clock=lambda: 0.0)
    for _ in range(3):
        request = httpx.Request("GET", "https://graph.microsoft.com/v1.0/me")
        next(auth.auth_flow(request))

    assert fetcher_calls["n"] == 1


@pytest.mark.unit
def test_token_refreshes_past_expiry() -> None:
    """The fetcher fires again when the clock walks past the expiry leeway.

    Sabotage-proof: hard-code the expiry to infinity; this test fails
    because the second call does not refresh.
    """
    fetcher_calls = {"n": 0}
    tokens = iter(["token-1", "token-2"])

    def _fetcher(_c: OAuth2Config) -> tuple[str, float]:
        fetcher_calls["n"] += 1
        return (next(tokens), 100.0)

    clock_value = {"t": 0.0}

    def _clock() -> float:
        return clock_value["t"]

    auth = OAuth2ClientCredsAuth(_config(), token_fetcher=_fetcher, clock=_clock)

    first = httpx.Request("GET", "https://graph.microsoft.com/v1.0/me")
    next(auth.auth_flow(first))
    assert first.headers["Authorization"] == "Bearer token-1"

    # Walk past the cached expiry (100s - 60s leeway = 40s effective)
    clock_value["t"] = 1000.0

    second = httpx.Request("GET", "https://graph.microsoft.com/v1.0/me")
    next(auth.auth_flow(second))
    assert second.headers["Authorization"] == "Bearer token-2"
    assert fetcher_calls["n"] == 2


# ---------------------------------------------------------------------------
# Default token fetcher — driven via OAuth2ClientCredsAuth's public surface
#
# The production fetcher is module-private (``_fetch_token_from_microsoft``);
# tests drive it through ``OAuth2ClientCredsAuth(config)`` without passing
# the ``token_fetcher`` seam, so the default-path code is exercised end-to-
# end. ``httpx.post`` is the third-party stdlib-adjacent boundary the
# fetcher calls; per F1, stdlib + external-SDK substitution remains
# allowed (only kairix.* substitution is forbidden).
# ---------------------------------------------------------------------------


def _auth_with_default_fetcher() -> OAuth2ClientCredsAuth:
    """Construct the auth flow with the production token fetcher path."""
    return OAuth2ClientCredsAuth(_config(), clock=lambda: 0.0)


def _drive_one_request(auth: OAuth2ClientCredsAuth) -> httpx.Request:
    """Send one request through the auth flow and return the post-flow Request."""
    request = httpx.Request("GET", "https://graph.microsoft.com/v1.0/me")
    next(auth.auth_flow(request))
    return request


@pytest.mark.unit
def test_default_fetcher_parses_v2_response(monkeypatch: pytest.MonkeyPatch) -> None:
    """The default token-fetch path parses (access_token, expires_in) from a 2xx.

    Drives the public :class:`OAuth2ClientCredsAuth` surface without
    overriding ``token_fetcher`` so the default fetcher is exercised
    end-to-end. ``httpx.post`` is the third-party transport surface the
    default fetcher calls; per F1 third-party SDK substitution remains
    allowed.

    Sabotage-proof: drop the access_token lookup in the production
    fetcher; this test fails because the assertion on the Bearer
    header then misses.
    """

    def _fake_post(_url: str, *, data: Any = None, timeout: float = 0.0) -> httpx.Response:
        del data, timeout
        return httpx.Response(
            200,
            json={"access_token": "fetched-token", "expires_in": 3600},
        )

    monkeypatch.setattr(httpx, "post", _fake_post)

    request = _drive_one_request(_auth_with_default_fetcher())
    assert request.headers["Authorization"] == "Bearer fetched-token"


@pytest.mark.unit
def test_default_fetcher_raises_on_4xx(monkeypatch: pytest.MonkeyPatch) -> None:
    """4xx response raises :class:`OAuth2Error` with an affordance message.

    Sabotage-proof: drop the ``status_code >= 400`` guard in the
    fetcher; this test fails because the message no longer matches.
    """

    def _fake_post(_url: str, *, data: Any = None, timeout: float = 0.0) -> httpx.Response:
        del data, timeout
        return httpx.Response(401, json={"error": "unauthorized_client"})

    monkeypatch.setattr(httpx, "post", _fake_post)

    with pytest.raises(OAuth2Error) as exc:
        _drive_one_request(_auth_with_default_fetcher())
    # F15 — message names the tenant id (public operator identifier)
    # but never the client secret.
    assert "placeholder-tenant" in str(exc.value)
    assert "placeholder-secret" not in str(exc.value)  # pragma: allowlist secret


@pytest.mark.unit
def test_default_fetcher_raises_on_missing_access_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """A 2xx response with no access_token field raises :class:`OAuth2Error`.

    Sabotage-proof: drop the access_token isinstance guard in the
    fetcher; this test fails because no OAuth2Error is raised.
    """

    def _fake_post(_url: str, *, data: Any = None, timeout: float = 0.0) -> httpx.Response:
        del data, timeout
        return httpx.Response(200, json={"expires_in": 3600})

    monkeypatch.setattr(httpx, "post", _fake_post)

    with pytest.raises(OAuth2Error, match="missing access_token"):
        _drive_one_request(_auth_with_default_fetcher())


@pytest.mark.unit
def test_default_fetcher_form_carries_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    """The default fetcher's POST body carries the canonical client-credentials form.

    Sabotage-proof: drop one of the form fields in the fetcher; this
    test fails on the corresponding key assertion.
    """
    captured: dict[str, Any] = {}

    def _fake_post(url: str, *, data: Any = None, timeout: float = 0.0) -> httpx.Response:
        del timeout
        captured["url"] = url
        captured["data"] = data
        return httpx.Response(200, json={"access_token": "ok", "expires_in": 60})

    monkeypatch.setattr(httpx, "post", _fake_post)

    _drive_one_request(_auth_with_default_fetcher())

    assert "login.microsoftonline.com/placeholder-tenant" in captured["url"]
    form = captured["data"]
    assert form["grant_type"] == "client_credentials"
    assert form["client_id"] == "placeholder-client"
    assert form["client_secret"] == "placeholder-secret"  # pragma: allowlist secret
    assert form["scope"] == DEFAULT_GRAPH_SCOPE

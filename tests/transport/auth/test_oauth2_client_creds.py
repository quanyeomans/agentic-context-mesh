"""Unit tests for :class:`kairix.transport.auth.OAuth2ClientCredsAuth`.

Scope per the KP-2 brief:

  * Cache hit — a second ``get_token`` call within the expiry window
    returns the cached token without hitting the IdP again.
  * Cache miss / near-expiry — when the cached token is within
    ``REFRESH_SKEW_S`` of expiring, the next call refreshes.
  * 401 invalidation — :meth:`invalidate` drops the cache; the next
    call refreshes from the IdP.
  * Network error / non-2xx — the helper raises
    :class:`httpx.HTTPError` and does NOT cache an empty token.
  * Missing credentials at construction — raise typed
    :class:`MissingCredentialsError` naming which field is missing.

All tests use :class:`httpx.MockTransport` so no real network call
ever leaks. The injectable ``time_source`` lets the cache-expiry
scenarios advance wall-clock deterministically.

F1-clean (no monkey-patching), F8 carries ``@pytest.mark.unit``.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from kairix.transport.auth.oauth2_client_creds import (
    REFRESH_SKEW_S,
    MissingCredentialsError,
    OAuth2ClientCredsAuth,
)

pytestmark = pytest.mark.unit


class _Clock:
    """Deterministic clock for cache-expiry assertions.

    Tests advance ``current`` to move past the cached token's expiry
    window without sleeping. Avoiding ``time.sleep`` keeps the test
    suite fast.
    """

    def __init__(self, start: float = 1_700_000_000.0) -> None:
        self.current = start

    def __call__(self) -> float:
        return self.current


def _token_handler(
    tokens: list[dict[str, Any]] | None = None,
    calls: list[httpx.Request] | None = None,
    status_code: int = 200,
) -> httpx.MockTransport:
    """Compose a MockTransport that returns the next scripted token
    response on every POST to the token endpoint.

    ``tokens`` is consumed in order; if exhausted the handler returns
    the most recent response (so a test that doesn't expect more than
    one refresh can still satisfy a spurious second call).
    """
    sequence = (
        list(tokens)
        if tokens is not None
        else [{"access_token": "first-token", "expires_in": 3600, "token_type": "Bearer"}]
    )
    recorded = calls if calls is not None else []

    def _handler(request: httpx.Request) -> httpx.Response:
        recorded.append(request)
        if status_code != 200:
            return httpx.Response(status_code, json={"error": "invalid_client"})
        payload = sequence.pop(0) if sequence else sequence_last(sequence)
        return httpx.Response(200, json=payload)

    return httpx.MockTransport(_handler)


def sequence_last(sequence: list[dict[str, Any]]) -> dict[str, Any]:
    """Return a sentinel payload when the scripted token list is exhausted."""
    return {"access_token": "fallback-token", "expires_in": 3600}


def _build_auth(
    transport: httpx.MockTransport,
    clock: _Clock | None = None,
    tenant_id: str = "fake-tenant",
    client_id: str = "fake-client",
    client_secret: str = "fake-client-secret-value",
    scope: str = "https://graph.microsoft.com/.default",
) -> OAuth2ClientCredsAuth:
    return OAuth2ClientCredsAuth(
        tenant_id=tenant_id,
        client_id=client_id,
        client_secret=client_secret,
        scope=scope,
        http_client=httpx.Client(transport=transport),
        time_source=clock if clock is not None else _Clock(),
    )


# ---------------------------------------------------------------------------
# Cache behaviour
# ---------------------------------------------------------------------------


def test_get_token_cache_hit_skips_idp() -> None:
    """A second call within the expiry window returns the cached token.

    Sabotage proof: remove the ``cached.expires_at - now > REFRESH_SKEW_S``
    short-circuit — the calls assertion below jumps from 1 to 2.
    """
    calls: list[httpx.Request] = []
    transport = _token_handler(
        tokens=[{"access_token": "alpha-token", "expires_in": 3600}],
        calls=calls,
    )
    auth = _build_auth(transport, clock=_Clock())
    assert auth.get_token() == "alpha-token"
    assert auth.get_token() == "alpha-token"
    assert len(calls) == 1, f"expected exactly 1 IdP call (cache hit), got {len(calls)}"


def test_get_token_refresh_near_expiry() -> None:
    """When the cached token is within ``REFRESH_SKEW_S`` of expiring,
    the next call refreshes against the IdP.

    Sabotage proof: change the comparison to ``cached.expires_at - now > 0`` —
    the refresh fires too late and the second token does not appear
    until well past the spec's safety window.
    """
    calls: list[httpx.Request] = []
    transport = _token_handler(
        tokens=[
            {"access_token": "first-token", "expires_in": 100},
            {"access_token": "second-token", "expires_in": 3600},
        ],
        calls=calls,
    )
    clock = _Clock()
    auth = _build_auth(transport, clock=clock)
    assert auth.get_token() == "first-token"
    # Advance the clock to inside the refresh skew window.
    clock.current += 100 - REFRESH_SKEW_S + 1
    assert auth.get_token() == "second-token"
    assert len(calls) == 2, f"expected 2 IdP calls (cache + refresh), got {len(calls)}"


def test_invalidate_drops_cached_token() -> None:
    """Calling :meth:`invalidate` forces the next ``get_token`` to refresh.

    Sabotage proof: have :meth:`invalidate` no-op — the assertion that
    the second token differs from the first fails.
    """
    calls: list[httpx.Request] = []
    transport = _token_handler(
        tokens=[
            {"access_token": "before-401", "expires_in": 3600},
            {"access_token": "after-401", "expires_in": 3600},
        ],
        calls=calls,
    )
    auth = _build_auth(transport)
    first = auth.get_token()
    auth.invalidate()
    second = auth.get_token()
    assert first == "before-401"
    assert second == "after-401"
    assert len(calls) == 2, f"expected 2 IdP calls (acquire + post-invalidate), got {len(calls)}"


# ---------------------------------------------------------------------------
# Error paths
# ---------------------------------------------------------------------------


def test_get_token_raises_on_idp_non_2xx() -> None:
    """A 4xx from the IdP propagates as :class:`httpx.HTTPStatusError`.

    Sabotage proof: remove the ``response.raise_for_status()`` call —
    the ``pytest.raises`` block fails.
    """
    transport = _token_handler(status_code=400)
    auth = _build_auth(transport)
    with pytest.raises(httpx.HTTPStatusError):
        auth.get_token()


def test_get_token_raises_when_idp_returns_no_access_token() -> None:
    """A 200 response with no ``access_token`` field raises HTTPError.

    Sabotage proof: silently return an empty string in that path — the
    ``pytest.raises`` block fails.
    """
    calls: list[httpx.Request] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json={"expires_in": 3600})  # missing access_token

    auth = _build_auth(httpx.MockTransport(_handler))
    with pytest.raises(httpx.HTTPError) as exc_info:
        auth.get_token()
    assert "access_token" in str(exc_info.value)


# ---------------------------------------------------------------------------
# Construction validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "field,kwargs",
    [
        ("tenant_id", {"tenant_id": ""}),
        ("client_id", {"client_id": ""}),
        ("client_secret", {"client_secret": ""}),
        ("scope", {"scope": ""}),
    ],
)
def test_constructor_rejects_empty_credential_field(field: str, kwargs: dict[str, Any]) -> None:
    """Each required field is validated independently at construction.

    Sabotage proof: remove the loop body in
    :class:`OAuth2ClientCredsAuth.__init__` — the
    ``pytest.raises`` block fails for the named field.
    """
    base = {
        "tenant_id": "fake-tenant",
        "client_id": "fake-client",
        "client_secret": "fake-client-secret-value",  # pragma: allowlist secret — test fixture
        "scope": "https://graph.microsoft.com/.default",
    }
    base.update(kwargs)
    with pytest.raises(MissingCredentialsError) as exc_info:
        OAuth2ClientCredsAuth(**base)
    assert field in str(exc_info.value)


def test_constructor_uses_default_token_endpoint_template() -> None:
    """Without ``token_endpoint``, the default Microsoft Entra ID URL is composed.

    Sabotage proof: change the template to ``https://login.example.com/...``
    — the URL assertion below fails.
    """
    calls: list[httpx.Request] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json={"access_token": "ok", "expires_in": 3600})

    auth = OAuth2ClientCredsAuth(
        tenant_id="my-tenant-id",
        client_id="my-client",
        client_secret="my-secret-value",  # pragma: allowlist secret — test fixture
        scope="https://graph.microsoft.com/.default",
        http_client=httpx.Client(transport=httpx.MockTransport(_handler)),
    )
    _ = auth.get_token()
    assert calls, "expected one token-endpoint call"
    assert "login.microsoftonline.com/my-tenant-id/oauth2/v2.0/token" in str(calls[0].url)


def test_constructor_accepts_custom_token_endpoint() -> None:
    """Sovereign-cloud / non-MS IdP override is honoured.

    Sabotage proof: hard-code the default template inside ``_refresh``
    — the URL assertion below fails because the custom endpoint
    no longer reaches the mock.
    """
    calls: list[httpx.Request] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json={"access_token": "ok", "expires_in": 3600})

    auth = OAuth2ClientCredsAuth(
        tenant_id="t",
        client_id="c",
        client_secret="s-value",  # pragma: allowlist secret — test fixture
        scope="https://graph.microsoft.com/.default",
        token_endpoint="https://login.microsoftonline.us/t/oauth2/v2.0/token",
        http_client=httpx.Client(transport=httpx.MockTransport(_handler)),
    )
    _ = auth.get_token()
    assert "login.microsoftonline.us" in str(calls[0].url)

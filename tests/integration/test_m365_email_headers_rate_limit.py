"""M365 Email Headers Graph client throttling behaviour — F64.

Pins the CURRENT throttling contract for the M365 email-headers Graph
client: a 429 / 503 response from Microsoft Graph surfaces as a typed
:class:`httpx.HTTPStatusError` immediately — no Retry-After parsing,
no retry loop. The worker dead-letter path catches the typed error
explicitly per F68 / ADR-024.

This is the contract documented today; the upgrade path to honor
``Retry-After`` mirroring the SharePoint connector's tenacity-backed
behaviour is tracked in GH #357.

Tests inject the throttled response via :class:`httpx.MockTransport`
(NOT a monkeypatch on httpx or kairix internals per F1) so the wire
shape is exercised end-to-end. The OAuth2 token endpoint is short-
circuited via the shared auth helper's ``http_client`` DI seam so the
429 reaches the data call rather than the token exchange.

Each ``test_*`` carries the ``@pytest.mark.integration`` marker per F8
and includes a "Sabotage proof:" note describing the mutation that
proves the assertion has teeth.
"""

from __future__ import annotations

import httpx
import pytest

from kairix.connectors.m365_email_headers.graph_client import M365GraphClient
from kairix.transport.auth.oauth2_client_creds import OAuth2ClientCredsAuth

pytestmark = pytest.mark.integration

# Fixtures — shared so the F17 duplicate-literal gate stays green.
_FAKE_TOKEN_PATH = "/oauth2/v2.0/token"
_FAKE_BEARER_VALUE = "fake-headers-bearer"  # pragma: allowlist secret — test fixture
_MESSAGES_DELTA_TAIL = "/messages/delta"
_FAKE_UPN = "agent-alpha@example.com"


def _token_response_for(request: httpx.Request) -> httpx.Response | None:
    """Return a 200 token reply for the OAuth2 endpoint, else ``None``.

    Mirrors the SharePoint rate-limit test's ``_token_response_for``
    helper shape so the shared OAuth2ClientCredsAuth never reaches a
    real Microsoft token endpoint.
    """
    if _FAKE_TOKEN_PATH in str(request.url):
        return httpx.Response(
            200,
            json={"access_token": _FAKE_BEARER_VALUE, "expires_in": 3600, "token_type": "Bearer"},
        )
    return None


def _build_client(handler: object) -> M365GraphClient:
    """Wire a real :class:`M365GraphClient` against ``handler``.

    F1 / F2 clean — no patching, no env mutation; both ``http_client``
    seams (auth + data) are public constructor kwargs already in
    production use for tests.
    """
    transport = httpx.MockTransport(handler)  # type: ignore[arg-type]  # F3 rationale: pytest typing accepts handler shapes httpx narrows at runtime.
    shared = httpx.Client(transport=transport)
    auth = OAuth2ClientCredsAuth(
        tenant_id="t",
        client_id="c",
        client_secret="s-value",  # pragma: allowlist secret — test fixture
        scope="https://graph.microsoft.com/.default",
        http_client=shared,
    )
    return M365GraphClient(user_principal_name=_FAKE_UPN, auth=auth, http_client=shared)


@pytest.mark.integration
def test_m365_email_headers_429_raises_typed_http_status_error() -> None:
    """A 429 on a messages/delta call surfaces as :class:`HTTPStatusError`.

    The current contract is "no in-client retry on 429" — the typed
    error escapes ``fetch_page`` via ``_authorised_get`` directly so
    the worker's dead-letter path catches it. GH #357 covers wiring
    tenacity for Retry-After handling parity with SharePoint.

    Sabotage proof: in ``M365GraphClient._authorised_get`` remove the
    ``response.raise_for_status()`` line. Re-run: the 429 body parses
    as JSON OK but the connector returns a malformed DeltaPage instead
    of raising — every assertion that the connector raised fails.
    Restored.
    """
    call_count = {"n": 0}

    def _handler(request: httpx.Request) -> httpx.Response:
        token = _token_response_for(request)
        if token is not None:
            return token
        call_count["n"] += 1
        return httpx.Response(429, headers={"Retry-After": "2"}, json={"error": "throttled"})

    client = _build_client(_handler)
    with pytest.raises(httpx.HTTPStatusError) as exc_info:
        list(client.iter_messages(start_url=None))

    assert exc_info.value.response.status_code == 429
    # Current contract: no in-client retry, the typed error surfaces on
    # the first 429 response. This pins the behaviour the worker's
    # dead-letter path depends on; any future tenacity wiring must keep
    # the typed-error escape on exhausted retries.
    assert call_count["n"] == 1, (
        f"current contract: m365_email_headers does not retry 429 (escapes to dead-letter); saw {call_count['n']} calls"
    )


@pytest.mark.integration
def test_m365_email_headers_503_raises_typed_http_status_error() -> None:
    """A 503 (unavailable) surfaces as :class:`HTTPStatusError`.

    Mirrors the 429 contract — the current implementation does not
    distinguish 5xx for retry; both surface to the worker for
    dead-lettering.

    Sabotage proof: removing the ``raise_for_status()`` call in
    ``_authorised_get`` lets the 503 body parse to an empty DeltaPage;
    ``pytest.raises`` fails. Restored.
    """
    call_count = {"n": 0}

    def _handler(request: httpx.Request) -> httpx.Response:
        token = _token_response_for(request)
        if token is not None:
            return token
        call_count["n"] += 1
        return httpx.Response(503, headers={"Retry-After": "5"}, json={"error": "unavailable"})

    client = _build_client(_handler)
    with pytest.raises(httpx.HTTPStatusError) as exc_info:
        list(client.iter_messages(start_url=None))

    assert exc_info.value.response.status_code == 503
    assert call_count["n"] == 1, f"current contract: m365_email_headers does not retry 503; saw {call_count['n']} calls"


@pytest.mark.integration
def test_m365_email_headers_401_refreshes_token_once_then_raises() -> None:
    """A 401 triggers the single token-refresh + retry path, then re-raises.

    The connector explicitly handles 401 via :meth:`OAuth2ClientCredsAuth.invalidate`
    + one retry attempt. This is distinct from 429 (no retry) — it
    pins the "auth refresh on 401" contract documented in the
    ``_authorised_get`` docstring.

    Sabotage proof: removing the ``self._auth.invalidate()`` + retry
    block in ``_authorised_get`` makes the connector raise on the
    first 401 instead of retrying; ``call_count`` drops from 2 to 1.
    Restored.
    """
    data_call_count = {"n": 0}

    def _handler(request: httpx.Request) -> httpx.Response:
        token = _token_response_for(request)
        if token is not None:
            return token
        data_call_count["n"] += 1
        return httpx.Response(401, json={"error": "unauthorized"})

    client = _build_client(_handler)
    with pytest.raises(httpx.HTTPStatusError) as exc_info:
        list(client.iter_messages(start_url=None))

    assert exc_info.value.response.status_code == 401
    assert data_call_count["n"] == 2, (
        f"401 must trigger one token-refresh + retry (2 data calls total); saw {data_call_count['n']}"
    )


@pytest.mark.integration
def test_m365_email_headers_non_retryable_4xx_raises_immediately() -> None:
    """A 403 surfaces as :class:`HTTPStatusError` — no retry, no refresh.

    The 4xx-permanent contract is distinct from 401 (no token refresh)
    and distinct from 429 (no rate-limit retry). 403 means "permanent
    for this URL + credential"; the worker dead-letters it on the
    first response.

    Sabotage proof: widening the 401 special-case in ``_authorised_get``
    to ``response.status_code in (401, 403)`` would trigger the
    refresh + retry; the data_call_count assertion would fail (2
    instead of 1). Restored.
    """
    data_call_count = {"n": 0}

    def _handler(request: httpx.Request) -> httpx.Response:
        token = _token_response_for(request)
        if token is not None:
            return token
        data_call_count["n"] += 1
        return httpx.Response(403, json={"error": "forbidden"})

    client = _build_client(_handler)
    with pytest.raises(httpx.HTTPStatusError) as exc_info:
        list(client.iter_messages(start_url=None))

    assert exc_info.value.response.status_code == 403
    assert data_call_count["n"] == 1, f"403 must not retry, saw {data_call_count['n']} calls"

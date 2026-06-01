"""M365 Email Headers Graph client honours throttling responses — F64.

Pins the post-fix behaviour for GH #357: ``_authorised_get`` retries
429 / 503 with ``Retry-After`` (mirroring SharePoint's tenacity wiring)
instead of raising immediately and dead-lettering every message on the
throttled tick. The 401 refresh+retry path is preserved unchanged.
Other 4xx responses (403, 404 outside ``path_exists``) still raise
immediately — 4xx is permanent for this URL + credential pair.

The fix wires :mod:`tenacity` into :meth:`M365GraphClient._authorised_get`
with a custom wait strategy that reads ``Retry-After`` from the throttled
response. Tests inject a recording ``sleep_fn`` (NOT a monkeypatch on
``time.sleep`` per F1 / F2) so the retry loop executes synchronously
without any wall-clock delay; the recorded waits prove the strategy
honoured the server header.

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


def _build_client(
    handler: object,
    *,
    recorded_sleeps: list[float],
    max_attempts: int = 3,
) -> M365GraphClient:
    """Wire a real :class:`M365GraphClient` against ``handler``.

    F1 / F2 clean — no patching, no env mutation; ``http_client`` (auth +
    data), ``sleep_fn``, and ``max_attempts`` are public constructor
    seams already in production use for tests.
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
    return M365GraphClient(
        user_principal_name=_FAKE_UPN,
        auth=auth,
        http_client=shared,
        sleep_fn=recorded_sleeps.append,
        max_attempts=max_attempts,
    )


@pytest.mark.integration
def test_m365_email_headers_429_with_retry_after_honoured() -> None:
    """A single 429 with ``Retry-After: 2`` retries after sleeping ~2s, then 200.

    Sabotage proof: revert ``_authorised_get`` to its pre-fix shape
    (direct ``response.raise_for_status()`` after the 401 branch). The
    first 429 raises :class:`httpx.HTTPStatusError` and this test fails
    on the missing-raises assertion plus the recorded-sleeps length
    (0 instead of 1).
    """
    call_count = {"n": 0}
    recorded_sleeps: list[float] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        token = _token_response_for(request)
        if token is not None:
            return token
        call_count["n"] += 1
        if call_count["n"] == 1:
            return httpx.Response(429, headers={"Retry-After": "2"}, json={"error": "throttled"})
        return httpx.Response(200, json={"value": []})

    client = _build_client(_handler, recorded_sleeps=recorded_sleeps)
    messages = list(client.iter_messages("inbox", start_url=None))

    assert messages == [], "post-retry 200 returned empty value array — should not raise"
    assert call_count["n"] == 2, f"expected 2 data calls (429 → retry → 200), saw {call_count['n']}"
    assert len(recorded_sleeps) == 1, f"expected one sleep between attempts, saw {recorded_sleeps!r}"
    assert recorded_sleeps[0] == pytest.approx(2.0, abs=0.01), (
        f"client must honour Retry-After=2 seconds, slept {recorded_sleeps[0]}s"
    )


@pytest.mark.integration
def test_m365_email_headers_503_with_retry_after_honoured() -> None:
    """503 Service Unavailable + Retry-After is treated like 429.

    Sabotage proof: removing 503 from ``_RETRYABLE_STATUS_CODES`` in
    ``kairix/connectors/m365_email_headers/graph_client.py`` makes the
    first 503 raise immediately; call-count drops to 1 and
    ``recorded_sleeps`` stays empty.
    """
    call_count = {"n": 0}
    recorded_sleeps: list[float] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        token = _token_response_for(request)
        if token is not None:
            return token
        call_count["n"] += 1
        if call_count["n"] == 1:
            return httpx.Response(503, headers={"Retry-After": "5"}, json={"error": "unavailable"})
        return httpx.Response(200, json={"value": []})

    client = _build_client(_handler, recorded_sleeps=recorded_sleeps)
    messages = list(client.iter_messages("inbox", start_url=None))

    assert messages == []
    assert call_count["n"] == 2, f"expected 2 data calls (503 → retry → 200), saw {call_count['n']}"
    assert len(recorded_sleeps) == 1, f"expected one sleep between attempts, saw {recorded_sleeps!r}"
    assert recorded_sleeps[0] == pytest.approx(5.0, abs=0.01), (
        f"client must honour Retry-After=5 seconds, slept {recorded_sleeps[0]}s"
    )


@pytest.mark.integration
def test_m365_email_headers_429_repeated_eventually_raises() -> None:
    """N+1 429 responses exhaust the retry budget and raise.

    Pins two contracts at once: ``HTTPStatusError`` IS raised once
    retries exhaust (no silent swallow), AND the recorded sleeps prove
    the backoff progressed (cumulative wait time ≥ the server's
    ``Retry-After`` budget).

    Sabotage proof: capping ``max_attempts=1`` makes the call-count
    assertion fail (1 vs 3) and the recorded-sleeps assertion fail
    (0 vs 2).
    """
    call_count = {"n": 0}
    recorded_sleeps: list[float] = []
    max_attempts = 3

    def _handler(request: httpx.Request) -> httpx.Response:
        token = _token_response_for(request)
        if token is not None:
            return token
        call_count["n"] += 1
        return httpx.Response(429, headers={"Retry-After": "2"}, json={"error": "throttled"})

    client = _build_client(_handler, recorded_sleeps=recorded_sleeps, max_attempts=max_attempts)
    with pytest.raises(httpx.HTTPStatusError) as exc_info:
        list(client.iter_messages("inbox", start_url=None))

    assert exc_info.value.response.status_code == 429
    assert call_count["n"] == max_attempts, (
        f"expected {max_attempts} attempts before exhausting retries, saw {call_count['n']}"
    )
    # max_attempts attempts → max_attempts - 1 sleeps between them
    assert len(recorded_sleeps) == max_attempts - 1, (
        f"expected {max_attempts - 1} sleeps for {max_attempts} attempts, saw {recorded_sleeps!r}"
    )
    assert sum(recorded_sleeps) >= 4.0, (
        f"total backoff must be ≥ sum of Retry-After hints (4s), saw {sum(recorded_sleeps)}s"
    )


@pytest.mark.integration
def test_m365_email_headers_401_refreshes_token_once_then_raises() -> None:
    """A persistent 401 triggers the single token-refresh + retry, then re-raises.

    The connector explicitly handles 401 via :meth:`OAuth2ClientCredsAuth.invalidate`
    + one retry attempt. This is distinct from 429 (no Retry-After loop)
    — it pins the "auth refresh on 401" contract documented in the
    ``_authorised_get_once`` docstring.

    Sabotage proof: removing the ``self._auth.invalidate()`` + retry
    block in ``_authorised_get_once`` makes the connector raise on the
    first 401 instead of retrying; ``data_call_count`` drops from 2 to 1.
    Restored.
    """
    data_call_count = {"n": 0}
    recorded_sleeps: list[float] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        token = _token_response_for(request)
        if token is not None:
            return token
        data_call_count["n"] += 1
        return httpx.Response(401, json={"error": "unauthorized"})

    client = _build_client(_handler, recorded_sleeps=recorded_sleeps)
    with pytest.raises(httpx.HTTPStatusError) as exc_info:
        list(client.iter_messages("inbox", start_url=None))

    assert exc_info.value.response.status_code == 401
    assert data_call_count["n"] == 2, (
        f"401 must trigger one token-refresh + retry (2 data calls total); saw {data_call_count['n']}"
    )
    # 401 is non-retryable in the throttle loop — no sleeps should fire.
    assert recorded_sleeps == [], f"401 must not trigger throttle sleeps, saw {recorded_sleeps!r}"


@pytest.mark.integration
def test_m365_email_headers_non_retryable_4xx_raises_immediately() -> None:
    """A 403 raises immediately; no retry, no sleep.

    The 4xx-permanent contract is distinct from 401 (no token refresh)
    and distinct from 429 (no Retry-After loop). 403 means "permanent
    for this URL + credential"; the worker dead-letters it on the
    first response.

    Sabotage proof: widening ``_RETRYABLE_STATUS_CODES`` to include 403
    bumps the call count > 1 and adds entries to ``recorded_sleeps``;
    both assertions fail.
    """
    data_call_count = {"n": 0}
    recorded_sleeps: list[float] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        token = _token_response_for(request)
        if token is not None:
            return token
        data_call_count["n"] += 1
        return httpx.Response(403, json={"error": "forbidden"})

    client = _build_client(_handler, recorded_sleeps=recorded_sleeps)
    with pytest.raises(httpx.HTTPStatusError) as exc_info:
        list(client.iter_messages("inbox", start_url=None))

    assert exc_info.value.response.status_code == 403
    assert data_call_count["n"] == 1, f"403 must not retry, saw {data_call_count['n']} calls"
    assert recorded_sleeps == [], f"403 must not trigger any sleeps, saw {recorded_sleeps!r}"


@pytest.mark.integration
def test_m365_email_headers_5xx_without_retry_after_uses_exponential_backoff() -> None:
    """500/502/504 without Retry-After retry with exponential backoff.

    For transient server errors that omit ``Retry-After``, the client
    still retries but falls back to the bounded exponential strategy.
    This is the second branch of :meth:`_wait_strategy`.

    Sabotage proof: removing the ``wait_exponential`` fallback in
    ``_wait_strategy`` makes the recorded sleep 0 — the
    ``recorded_sleeps[0] >= 2.0`` floor assertion fails.
    """
    call_count = {"n": 0}
    recorded_sleeps: list[float] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        token = _token_response_for(request)
        if token is not None:
            return token
        call_count["n"] += 1
        if call_count["n"] == 1:
            return httpx.Response(500, json={"error": "internal"})
        return httpx.Response(200, json={"value": []})

    client = _build_client(_handler, recorded_sleeps=recorded_sleeps)
    messages = list(client.iter_messages("inbox", start_url=None))

    assert messages == []
    assert call_count["n"] == 2, f"500 must retry once, saw {call_count['n']} calls"
    assert len(recorded_sleeps) == 1
    # Exponential floor is _DEFAULT_BACKOFF_MIN_S = 2.0
    assert recorded_sleeps[0] >= 2.0, (
        f"500 (no Retry-After) must fall back to exponential ≥ floor, slept {recorded_sleeps[0]}s"
    )

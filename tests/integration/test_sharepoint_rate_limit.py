"""SharePoint Graph client honours throttling responses.

Pins the post-fix behaviour: ``_authorised_get`` retries 429 / 503 with
``Retry-After`` instead of raising immediately and dead-lettering every
item on the throttled drive. Other 4xx responses (e.g. 401 after the
single refresh, 403, 404 outside the ``path_exists`` probe) still raise
immediately — 4xx is permanent for this URL + credential pair.

The fix wires :mod:`tenacity` into ``_authorised_get`` with a custom
wait strategy that reads ``Retry-After`` from the throttled response.
Tests inject a recording ``sleep_fn`` (NOT a monkeypatch on
``time.sleep`` per F1 / F2) so the retry loop executes synchronously
without any wall-clock delay; the recorded waits prove the strategy
honoured the server header.

Why integration not unit: this is the end-to-end throttling contract —
construction of the real :class:`SharePointGraphClient` + the
:class:`httpx.MockTransport` Graph stub + the tenacity loop together.
The unit suite (``tests/connectors/sharepoint/test_graph_client.py``)
covers each layer in isolation.

Each ``test_*`` carries the ``@pytest.mark.integration`` marker per F8.
"""

from __future__ import annotations

import httpx
import pytest

from kairix.connectors.sharepoint.graph_client import SharePointGraphClient
from kairix.transport.auth.oauth2_client_creds import OAuth2ClientCredsAuth

pytestmark = pytest.mark.integration

# Test fixtures — token endpoint shape + a sentinel drive URL the
# Graph stub recognises. Shared across cases so the F17 duplicate
# literal gate stays green.
_FAKE_TOKEN_PATH = "/oauth2/v2.0/token"
_FAKE_BEARER_VALUE = "fake-bearer"  # pragma: allowlist secret — test fixture
_DRIVE_URL_TAIL = "/drives/drive-x/root/delta"


def _token_response_for(request: httpx.Request) -> httpx.Response | None:
    """Return a 200 token reply for the OAuth2 endpoint, else ``None``."""
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
    max_attempts: int = 5,
) -> SharePointGraphClient:
    """Wire a real :class:`SharePointGraphClient` to ``handler``.

    The injected ``sleep_fn`` records every wait the retry loop requests
    so tests can assert on backoff progression without touching wall
    clock time. F1 / F2 clean — no patching, no env mutation; the
    ``sleep_fn`` is a public constructor seam.
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
    return SharePointGraphClient(
        auth=auth,
        http_client=shared,
        sleep_fn=recorded_sleeps.append,
        max_attempts=max_attempts,
    )


@pytest.mark.integration
def test_429_with_retry_after_honoured() -> None:
    """A single 429 with ``Retry-After: 2`` retries after sleeping ~2s, then 200.

    Sabotage proof: removing the retry wrapping (reverting
    ``_authorised_get`` to its pre-fix ``response.raise_for_status()``
    on every response) makes the first 429 raise
    :class:`httpx.HTTPStatusError` and this test fails on the
    ``with pytest.raises`` absence + the recorded-sleeps assertion
    (length 0 instead of 1).
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
    items = list(client.iter_drive_items("drive-x"))

    assert items == [], "post-retry 200 returned empty value array — should not raise"
    assert call_count["n"] == 2, f"expected 2 calls (429 → retry → 200), saw {call_count['n']}"
    assert len(recorded_sleeps) == 1, f"expected one sleep between attempts, saw {recorded_sleeps!r}"
    assert recorded_sleeps[0] == pytest.approx(2.0, abs=0.01), (
        f"client must honour Retry-After=2 seconds, slept {recorded_sleeps[0]}s"
    )


@pytest.mark.integration
def test_503_with_retry_after_honoured() -> None:
    """503 Service Unavailable + Retry-After is treated like 429.

    Sabotage proof: removing 503 from ``_RETRYABLE_STATUS_CODES`` makes
    the first 503 raise immediately and the call-count assertion fails
    (1 instead of 2) + ``recorded_sleeps`` is empty.
    """
    call_count = {"n": 0}
    recorded_sleeps: list[float] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        token = _token_response_for(request)
        if token is not None:
            return token
        call_count["n"] += 1
        if call_count["n"] == 1:
            return httpx.Response(503, headers={"Retry-After": "3"}, json={"error": "unavailable"})
        return httpx.Response(200, json={"value": []})

    client = _build_client(_handler, recorded_sleeps=recorded_sleeps)
    items = list(client.iter_drive_items("drive-x"))

    assert items == []
    assert call_count["n"] == 2, f"expected 2 calls (503 → retry → 200), saw {call_count['n']}"
    assert len(recorded_sleeps) == 1, f"expected one sleep between attempts, saw {recorded_sleeps!r}"
    assert recorded_sleeps[0] == pytest.approx(3.0, abs=0.01), (
        f"client must honour Retry-After=3 seconds, slept {recorded_sleeps[0]}s"
    )


@pytest.mark.integration
def test_429_repeated_eventually_raises() -> None:
    """N+1 429 responses exhaust the retry budget and raise.

    Pins two contracts at once: ``HTTPStatusError`` IS raised once
    retries exhaust (no silent swallow), AND the recorded sleeps prove
    the backoff progressed (cumulative wait time ≥ the server's
    ``Retry-After`` budget).

    Sabotage proof: capping ``max_attempts=1`` in production code makes
    the call count assertion fail (1 vs 3) and the recorded-sleeps
    assertion fail (0 vs 2).
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
        list(client.iter_drive_items("drive-x"))

    assert exc_info.value.response.status_code == 429
    assert call_count["n"] == max_attempts, (
        f"expected {max_attempts} attempts before exhausting retries, saw {call_count['n']}"
    )
    # max_attempts attempts → max_attempts - 1 sleeps between them
    assert len(recorded_sleeps) == max_attempts - 1, (
        f"expected {max_attempts - 1} sleeps for {max_attempts} attempts, saw {recorded_sleeps!r}"
    )
    # Backoff progressed: at least one sleep occurred and total wait is
    # ≥ sum of Retry-After hints (2 + 2 = 4 seconds for two retries).
    assert sum(recorded_sleeps) >= 4.0, (
        f"total backoff must be ≥ sum of Retry-After hints (4s), saw {sum(recorded_sleeps)}s"
    )


@pytest.mark.integration
def test_no_retry_on_non_retryable_4xx() -> None:
    """A 403 raises immediately; no retry, no sleep.

    The 401 path is special-cased (refresh + one retry), so the 4xx
    used here is 403 — permanent for this URL + credential. The retry
    loop must NOT retry it.

    Sabotage proof: widening ``_RETRYABLE_STATUS_CODES`` to include 403
    bumps the call count > 1 and adds entries to recorded_sleeps; both
    assertions fail.
    """
    call_count = {"n": 0}
    recorded_sleeps: list[float] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        token = _token_response_for(request)
        if token is not None:
            return token
        call_count["n"] += 1
        return httpx.Response(403, json={"error": "forbidden"})

    client = _build_client(_handler, recorded_sleeps=recorded_sleeps)
    with pytest.raises(httpx.HTTPStatusError) as exc_info:
        list(client.iter_drive_items("drive-x"))

    assert exc_info.value.response.status_code == 403
    assert call_count["n"] == 1, f"403 must not retry, saw {call_count['n']} calls"
    assert recorded_sleeps == [], f"403 must not trigger any sleeps, saw {recorded_sleeps!r}"


@pytest.mark.integration
def test_5xx_without_retry_after_uses_exponential_backoff() -> None:
    """500/502/504 without Retry-After retry with exponential backoff.

    Pins the secondary contract — for transient server errors that omit
    ``Retry-After``, the client still retries but falls back to the
    bounded exponential strategy. This is the second branch of
    :meth:`_wait_strategy`.

    Sabotage proof: removing the ``wait_exponential`` fallback in
    ``_wait_strategy`` makes the recorded sleep 0 (or raise on missing
    ``wait`` callable) — this test's positive assertion fails.
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
    items = list(client.iter_drive_items("drive-x"))

    assert items == []
    assert call_count["n"] == 2, f"500 must retry once, saw {call_count['n']} calls"
    assert len(recorded_sleeps) == 1
    # Exponential floor is _DEFAULT_BACKOFF_MIN_S = 2.0
    assert recorded_sleeps[0] >= 2.0, (
        f"500 (no Retry-After) must fall back to exponential ≥ floor, slept {recorded_sleeps[0]}s"
    )

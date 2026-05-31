"""M365 Calendar Graph client honours throttling responses — F64.

Pins the post-fix behaviour for GH #357: ``_retrying_get`` retries 429 /
503 with ``Retry-After`` (mirroring SharePoint's tenacity wiring)
instead of raising immediately and dead-lettering every event on the
throttled tick. Other 4xx responses (e.g. 401 from a misconfigured
credential, 403, 404) still raise immediately — 4xx is permanent for
this URL + credential pair.

The fix wires :mod:`tenacity` into :meth:`M365GraphCalendarClient._retrying_get`
with a custom wait strategy that reads ``Retry-After`` from the throttled
response. Tests inject a recording ``sleep_fn`` (NOT a monkeypatch on
``time.sleep`` per F1 / F2) so the retry loop executes synchronously
without any wall-clock delay; the recorded waits prove the strategy
honoured the server header.

Tests inject the throttled response via :class:`httpx.MockTransport`
(NOT a monkeypatch on httpx or kairix internals per F1) so the wire
shape is exercised end-to-end. The OAuth2 token fetcher is replaced
with a deterministic stand-in via the connector auth's ``token_fetcher``
DI seam so the 429 reaches the data call rather than the token exchange.

Each ``test_*`` carries the ``@pytest.mark.integration`` marker per F8
and includes a "Sabotage proof:" note describing the mutation that
proves the assertion has teeth.
"""

from __future__ import annotations

import httpx
import pytest

from kairix.connectors.m365_calendar.auth import OAuth2ClientCredsAuth, OAuth2Config
from kairix.connectors.m365_calendar.graph_client import M365GraphCalendarClient

pytestmark = pytest.mark.integration

# Fixtures — shared so the F17 duplicate-literal gate stays green.
_FAKE_BEARER_VALUE = "fake-calendar-bearer"  # pragma: allowlist secret — test fixture
_CALENDAR_VIEW_TAIL = "/calendarView/delta"
_DELTA_PAGE_TAIL = "$deltatoken=fake-token"
_FAKE_USER_ID = "agent-alpha@example.com"
_WINDOW_START = "2026-01-01T00:00:00Z"
_WINDOW_END = "2026-12-31T00:00:00Z"


def _scripted_token_fetcher(_config: OAuth2Config) -> tuple[str, float]:
    """Token-fetcher DI seam — returns a deterministic bearer + long lifetime.

    Short-circuits the real Microsoft v2 token endpoint so tests focus
    on the data-call throttling contract, not the token-exchange path.
    F1 / F2 clean — the ``token_fetcher`` kwarg is a public constructor
    seam already in production use for tests.
    """
    return (_FAKE_BEARER_VALUE, 3600.0)


def _build_client(
    handler: object,
    *,
    recorded_sleeps: list[float],
    max_attempts: int = 3,
) -> M365GraphCalendarClient:
    """Wire a real :class:`M365GraphCalendarClient` against ``handler``.

    The injected ``sleep_fn`` records every wait the retry loop requests
    so tests can assert on backoff progression without touching wall
    clock time. F1 / F2 clean — no patching, no env mutation; both
    ``sleep_fn`` and ``http_client`` are public constructor seams.
    """
    transport = httpx.MockTransport(handler)  # type: ignore[arg-type]  # F3 rationale: pytest typing accepts handler shapes httpx narrows at runtime.
    auth = OAuth2ClientCredsAuth(
        OAuth2Config(
            tenant_id="t",
            client_id="c",
            client_secret="s-value",  # pragma: allowlist secret — test fixture
            scope="https://graph.microsoft.com/.default",
        ),
        token_fetcher=_scripted_token_fetcher,
    )
    shared = httpx.Client(transport=transport, auth=auth)
    return M365GraphCalendarClient(
        user_id=_FAKE_USER_ID,
        auth=auth,
        http_client=shared,
        sleep_fn=recorded_sleeps.append,
        max_attempts=max_attempts,
    )


@pytest.mark.integration
def test_m365_calendar_429_with_retry_after_honoured() -> None:
    """A single 429 with ``Retry-After: 2`` retries after sleeping ~2s, then 200.

    Sabotage proof: revert ``_retrying_get`` to its pre-fix shape
    (direct ``response.raise_for_status()`` on every response). The
    first 429 raises :class:`httpx.HTTPStatusError` and this test fails
    on the missing-raises assertion plus the recorded-sleeps length
    (0 instead of 1).
    """
    call_count = {"n": 0}
    recorded_sleeps: list[float] = []

    def _handler(_request: httpx.Request) -> httpx.Response:
        call_count["n"] += 1
        if call_count["n"] == 1:
            return httpx.Response(429, headers={"Retry-After": "2"}, json={"error": "throttled"})
        return httpx.Response(200, json={"value": []})

    client = _build_client(_handler, recorded_sleeps=recorded_sleeps)
    page = client.fetch_initial_delta(_WINDOW_START, _WINDOW_END)

    assert page.events == (), "post-retry 200 returned empty value array — should not raise"
    assert call_count["n"] == 2, f"expected 2 calls (429 → retry → 200), saw {call_count['n']}"
    assert len(recorded_sleeps) == 1, f"expected one sleep between attempts, saw {recorded_sleeps!r}"
    assert recorded_sleeps[0] == pytest.approx(2.0, abs=0.01), (
        f"client must honour Retry-After=2 seconds, slept {recorded_sleeps[0]}s"
    )


@pytest.mark.integration
def test_m365_calendar_503_with_retry_after_honoured() -> None:
    """503 Service Unavailable + Retry-After is treated like 429.

    Sabotage proof: removing 503 from ``_RETRYABLE_STATUS_CODES`` in
    ``kairix/connectors/m365_calendar/graph_client.py`` makes the first
    503 raise immediately; the call-count assertion fails (1 instead of
    2) and ``recorded_sleeps`` is empty.
    """
    call_count = {"n": 0}
    recorded_sleeps: list[float] = []

    def _handler(_request: httpx.Request) -> httpx.Response:
        call_count["n"] += 1
        if call_count["n"] == 1:
            return httpx.Response(503, headers={"Retry-After": "3"}, json={"error": "unavailable"})
        return httpx.Response(200, json={"value": []})

    client = _build_client(_handler, recorded_sleeps=recorded_sleeps)
    page = client.fetch_initial_delta(_WINDOW_START, _WINDOW_END)

    assert page.events == ()
    assert call_count["n"] == 2, f"expected 2 calls (503 → retry → 200), saw {call_count['n']}"
    assert len(recorded_sleeps) == 1, f"expected one sleep between attempts, saw {recorded_sleeps!r}"
    assert recorded_sleeps[0] == pytest.approx(3.0, abs=0.01), (
        f"client must honour Retry-After=3 seconds, slept {recorded_sleeps[0]}s"
    )


@pytest.mark.integration
def test_m365_calendar_429_repeated_eventually_raises() -> None:
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

    def _handler(_request: httpx.Request) -> httpx.Response:
        call_count["n"] += 1
        return httpx.Response(429, headers={"Retry-After": "2"}, json={"error": "throttled"})

    client = _build_client(_handler, recorded_sleeps=recorded_sleeps, max_attempts=max_attempts)
    with pytest.raises(httpx.HTTPStatusError) as exc_info:
        client.fetch_initial_delta(_WINDOW_START, _WINDOW_END)

    assert exc_info.value.response.status_code == 429
    assert call_count["n"] == max_attempts, (
        f"expected {max_attempts} attempts before exhausting retries, saw {call_count['n']}"
    )
    # max_attempts attempts → max_attempts - 1 sleeps between them
    assert len(recorded_sleeps) == max_attempts - 1, (
        f"expected {max_attempts - 1} sleeps for {max_attempts} attempts, saw {recorded_sleeps!r}"
    )
    # Backoff progressed: total wait is ≥ sum of Retry-After hints
    # (2 + 2 = 4 seconds for two retries).
    assert sum(recorded_sleeps) >= 4.0, (
        f"total backoff must be ≥ sum of Retry-After hints (4s), saw {sum(recorded_sleeps)}s"
    )


@pytest.mark.integration
def test_m365_calendar_non_retryable_4xx_raises_immediately() -> None:
    """A 403 raises immediately; no retry, no sleep.

    The 4xx-permanent contract: 403 means "permanent for this URL +
    credential". The retry loop must NOT retry it.

    Sabotage proof: widening ``_RETRYABLE_STATUS_CODES`` to include 403
    bumps the call count > 1 and adds entries to ``recorded_sleeps``;
    both assertions fail.
    """
    call_count = {"n": 0}
    recorded_sleeps: list[float] = []

    def _handler(_request: httpx.Request) -> httpx.Response:
        call_count["n"] += 1
        return httpx.Response(403, json={"error": "forbidden"})

    client = _build_client(_handler, recorded_sleeps=recorded_sleeps)
    with pytest.raises(httpx.HTTPStatusError) as exc_info:
        client.fetch_delta_page(
            f"https://graph.microsoft.com/v1.0/users/{_FAKE_USER_ID}{_CALENDAR_VIEW_TAIL}?{_DELTA_PAGE_TAIL}"
        )

    assert exc_info.value.response.status_code == 403
    assert call_count["n"] == 1, f"403 must not retry, saw {call_count['n']} calls"
    assert recorded_sleeps == [], f"403 must not trigger any sleeps, saw {recorded_sleeps!r}"


@pytest.mark.integration
def test_m365_calendar_5xx_without_retry_after_uses_exponential_backoff() -> None:
    """500/502/504 without Retry-After retry with exponential backoff.

    Pins the secondary contract — for transient server errors that omit
    ``Retry-After``, the client still retries but falls back to the
    bounded exponential strategy. This is the second branch of
    :meth:`_wait_strategy`.

    Sabotage proof: removing the ``wait_exponential`` fallback in
    ``_wait_strategy`` makes the recorded sleep 0 — the
    ``recorded_sleeps[0] >= 2.0`` floor assertion fails.
    """
    call_count = {"n": 0}
    recorded_sleeps: list[float] = []

    def _handler(_request: httpx.Request) -> httpx.Response:
        call_count["n"] += 1
        if call_count["n"] == 1:
            return httpx.Response(500, json={"error": "internal"})
        return httpx.Response(200, json={"value": []})

    client = _build_client(_handler, recorded_sleeps=recorded_sleeps)
    page = client.fetch_initial_delta(_WINDOW_START, _WINDOW_END)

    assert page.events == ()
    assert call_count["n"] == 2, f"500 must retry once, saw {call_count['n']} calls"
    assert len(recorded_sleeps) == 1
    # Exponential floor is _DEFAULT_BACKOFF_MIN_S = 2.0
    assert recorded_sleeps[0] >= 2.0, (
        f"500 (no Retry-After) must fall back to exponential ≥ floor, slept {recorded_sleeps[0]}s"
    )

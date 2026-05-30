"""M365 Calendar Graph client throttling behaviour — F64.

Pins the CURRENT throttling contract for the M365 Calendar Graph
client: a 429 / 503 response from Microsoft Graph surfaces as a typed
:class:`httpx.HTTPStatusError` immediately — no Retry-After parsing,
no retry loop. The worker dead-letter path catches the typed error
explicitly per F68 / ADR-024.

This is the contract documented today; the upgrade path to honor
``Retry-After`` mirroring the SharePoint connector's tenacity-backed
behaviour is tracked in GH #357.

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


def _scripted_token_fetcher(_config: OAuth2Config) -> tuple[str, float]:
    """Token-fetcher DI seam — returns a deterministic bearer + long lifetime.

    Short-circuits the real Microsoft v2 token endpoint so tests focus
    on the data-call throttling contract, not the token-exchange path.
    F1 / F2 clean — the ``token_fetcher`` kwarg is a public constructor
    seam already in production use for tests.
    """
    return (_FAKE_BEARER_VALUE, 3600.0)


def _build_client(handler: object) -> M365GraphCalendarClient:
    """Wire a real :class:`M365GraphCalendarClient` against ``handler``."""
    transport = httpx.MockTransport(handler)  # type: ignore[arg-type]  # F3 rationale: pytest typing accepts handler shapes httpx narrows at runtime.
    shared = httpx.Client(transport=transport)
    auth = OAuth2ClientCredsAuth(
        OAuth2Config(
            tenant_id="t",
            client_id="c",
            client_secret="s-value",  # pragma: allowlist secret — test fixture
            scope="https://graph.microsoft.com/.default",
        ),
        token_fetcher=_scripted_token_fetcher,
    )
    return M365GraphCalendarClient(user_id=_FAKE_USER_ID, auth=auth, http_client=shared)


@pytest.mark.integration
def test_m365_calendar_429_raises_typed_http_status_error() -> None:
    """A 429 on a calendarView/delta call surfaces as :class:`HTTPStatusError`.

    The current contract is "no in-client retry on 429" — the typed
    error escapes ``fetch_initial_delta`` directly so the worker's
    dead-letter path catches it. GH #357 covers wiring tenacity for
    Retry-After handling parity with SharePoint.

    Sabotage proof: in ``M365GraphCalendarClient.fetch_initial_delta``
    remove the ``response.raise_for_status()`` line. Re-run: the 429
    body parses as JSON OK but the connector returns a malformed
    CalendarDeltaPage instead of raising — every assertion that the
    connector raised fails. Restored.
    """
    call_count = {"n": 0}

    def _handler(request: httpx.Request) -> httpx.Response:
        call_count["n"] += 1
        return httpx.Response(429, headers={"Retry-After": "2"}, json={"error": "throttled"})

    client = _build_client(_handler)
    with pytest.raises(httpx.HTTPStatusError) as exc_info:
        client.fetch_initial_delta("2026-01-01T00:00:00Z", "2026-12-31T00:00:00Z")

    assert exc_info.value.response.status_code == 429
    # Current contract: no in-client retry, the typed error surfaces on
    # the first 429 response. This pins the behaviour the worker's
    # dead-letter path depends on; any future tenacity wiring must keep
    # the typed-error escape on exhausted retries.
    assert call_count["n"] == 1, (
        f"current contract: m365_calendar does not retry 429 (escapes to dead-letter); saw {call_count['n']} calls"
    )


@pytest.mark.integration
def test_m365_calendar_503_raises_typed_http_status_error() -> None:
    """A 503 (unavailable) surfaces as :class:`HTTPStatusError`.

    Mirrors the 429 contract — the current implementation does not
    distinguish 5xx for retry; both surface to the worker for
    dead-lettering.

    Sabotage proof: removing the ``raise_for_status()`` call lets the
    503 body parse to an empty CalendarDeltaPage; ``pytest.raises``
    fails. Restored.
    """
    call_count = {"n": 0}

    def _handler(request: httpx.Request) -> httpx.Response:
        call_count["n"] += 1
        return httpx.Response(503, headers={"Retry-After": "5"}, json={"error": "unavailable"})

    client = _build_client(_handler)
    with pytest.raises(httpx.HTTPStatusError) as exc_info:
        client.fetch_initial_delta("2026-01-01T00:00:00Z", "2026-12-31T00:00:00Z")

    assert exc_info.value.response.status_code == 503
    assert call_count["n"] == 1, f"current contract: m365_calendar does not retry 503; saw {call_count['n']} calls"


@pytest.mark.integration
def test_m365_calendar_non_retryable_4xx_raises_immediately() -> None:
    """A 403 surfaces as :class:`HTTPStatusError` — permanent for URL + cred.

    The 4xx-permanent contract is identical to the 429 contract today
    (no in-client retry). When the Retry-After wiring lands, 403 still
    must NOT retry — this test pins that future invariant.

    Sabotage proof: removing the ``raise_for_status()`` call lets the
    403 body parse to an empty CalendarDeltaPage; the ``pytest.raises``
    block fails. Restored.
    """
    call_count = {"n": 0}

    def _handler(request: httpx.Request) -> httpx.Response:
        call_count["n"] += 1
        return httpx.Response(403, json={"error": "forbidden"})

    client = _build_client(_handler)
    with pytest.raises(httpx.HTTPStatusError) as exc_info:
        client.fetch_delta_page(
            f"https://graph.microsoft.com/v1.0/users/{_FAKE_USER_ID}{_CALENDAR_VIEW_TAIL}?{_DELTA_PAGE_TAIL}"
        )

    assert exc_info.value.response.status_code == 403
    assert call_count["n"] == 1, f"403 must not retry, saw {call_count['n']} calls"

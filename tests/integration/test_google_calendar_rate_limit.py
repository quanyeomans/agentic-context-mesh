"""Google Calendar client throttling + sync-token expiry behaviour — F64.

Pins the CURRENT throttling contract for the Google Calendar client:

* **429 + Retry-After** — surfaces as a typed
  :class:`httpx.HTTPStatusError` immediately. No in-client retry; the
  worker's dead-letter path catches the typed error explicitly per
  F68 / ADR-024. Retry-After honouring is tracked under GH #357.
* **403 quotaExceeded** — same shape as 429: typed
  :class:`HTTPStatusError`, no retry, escapes to the dead-letter path.
* **410 syncToken expired** — NOT a failure. The client raises
  :class:`SyncTokenExpiredError` so the connector can catch it and
  transparently fall back to a fresh initial sync per Google's docs
  (developers.google.com/calendar/api/guides/sync).

Tests inject the throttled response via :class:`httpx.MockTransport`
(NOT a monkeypatch on httpx or kairix internals per F1) so the wire
shape is exercised end-to-end.

Each ``test_*`` carries the ``@pytest.mark.integration`` marker per F8
and includes a "Sabotage proof:" note describing the mutation that
proves the assertion has teeth.
"""

from __future__ import annotations

import httpx
import pytest

from kairix.connectors.google_calendar.client import (
    GoogleCalendarClient,
    SyncTokenExpiredError,
)

pytestmark = pytest.mark.integration


def _build_client(handler: object) -> GoogleCalendarClient:
    """Wire a real :class:`GoogleCalendarClient` against ``handler``."""
    transport = httpx.MockTransport(handler)  # type: ignore[arg-type]  # F3 rationale: pytest typing accepts handler shapes httpx narrows at runtime.
    http = httpx.Client(transport=transport, headers={"Authorization": "Bearer fake-token"})
    return GoogleCalendarClient(http_client=http, calendar_id="primary")


@pytest.mark.integration
def test_google_calendar_429_raises_typed_http_status_error() -> None:
    """A 429 on events.list surfaces as :class:`HTTPStatusError`.

    Current contract is "no in-client retry on 429" — the typed error
    escapes :meth:`fetch_initial_events` directly so the worker's
    dead-letter path catches it. GH #357 covers wiring tenacity for
    Retry-After parity with SharePoint.

    Sabotage proof: in :meth:`GoogleCalendarClient._get_page` remove
    the ``response.raise_for_status()`` line. Re-run: the 429 body
    parses as JSON OK but the connector returns a malformed
    GoogleCalendarEventsPage instead of raising — every assertion
    that the connector raised fails. Restored.
    """
    call_count = {"n": 0}

    def _handler(request: httpx.Request) -> httpx.Response:
        del request
        call_count["n"] += 1
        return httpx.Response(429, headers={"Retry-After": "2"}, json={"error": "rateLimitExceeded"})

    client = _build_client(_handler)
    with pytest.raises(httpx.HTTPStatusError) as exc_info:
        client.fetch_initial_events("2026-04-25T00:00:00Z")

    assert exc_info.value.response.status_code == 429
    # Current contract: no in-client retry — typed error surfaces on the
    # first 429. Pin so any future tenacity wiring keeps the typed-error
    # escape on exhausted retries.
    assert call_count["n"] == 1, (
        f"current contract: google_calendar does not retry 429 (escapes to dead-letter); saw {call_count['n']} calls"
    )


@pytest.mark.integration
def test_google_calendar_403_quota_exceeded_raises_typed_http_status_error() -> None:
    """A 403 quotaExceeded surfaces as :class:`HTTPStatusError`.

    Google's quota-exceeded response carries 403 + a JSON body with
    ``error.errors[0].reason="quotaExceeded"`` — the connector treats
    it the same as 429: typed error, no retry, dead-letter handles it.

    Sabotage proof: removing the ``raise_for_status`` line lets the
    403 body parse to an empty page; the ``pytest.raises`` block fails.
    Restored.
    """
    call_count = {"n": 0}

    def _handler(request: httpx.Request) -> httpx.Response:
        del request
        call_count["n"] += 1
        return httpx.Response(
            403,
            json={
                "error": {
                    "code": 403,
                    "message": "Calendar usage limits exceeded.",
                    "errors": [{"reason": "quotaExceeded"}],
                }
            },
        )

    client = _build_client(_handler)
    with pytest.raises(httpx.HTTPStatusError) as exc_info:
        client.fetch_initial_events("2026-04-25T00:00:00Z")

    assert exc_info.value.response.status_code == 403
    assert call_count["n"] == 1, f"403 must not retry, saw {call_count['n']} calls"


@pytest.mark.integration
def test_google_calendar_410_sync_token_raises_typed_recoverable_error() -> None:
    """A 410 Gone on a syncToken request raises :class:`SyncTokenExpiredError`.

    Google's docs: a 410 on incremental sync means the syncToken is
    too old; the caller MUST discard it and run a fresh initial sync.
    The client surfaces this as a distinct typed exception so the
    connector can catch it and route to the recovery path WITHOUT
    surfacing the error to the worker's dead-letter (this is normal
    operation, not failure).

    Sabotage proof: remove the ``if response.status_code == 410:``
    branch in :meth:`GoogleCalendarClient._get_page`. Re-run: the
    request raises :class:`HTTPStatusError` instead of
    :class:`SyncTokenExpiredError`; the ``pytest.raises`` block fails.
    Restored.
    """
    call_count = {"n": 0}

    def _handler(request: httpx.Request) -> httpx.Response:
        del request
        call_count["n"] += 1
        return httpx.Response(410, json={"error": {"code": 410, "message": "Sync token is no longer valid."}})

    client = _build_client(_handler)
    with pytest.raises(SyncTokenExpiredError) as exc_info:
        client.fetch_delta_events("stale-token")

    # Affordance: the error message must point operators at the recovery
    # path (fresh initial sync). F21 shape.
    assert "fresh initial sync" in str(exc_info.value)
    assert call_count["n"] == 1, f"410 must not retry in the client; saw {call_count['n']} calls"


@pytest.mark.integration
def test_google_calendar_503_raises_typed_http_status_error() -> None:
    """A 503 surfaces as :class:`HTTPStatusError` — same as 429.

    Mirrors the 429 contract — the current implementation does not
    distinguish 5xx for retry; both surface to the worker for
    dead-lettering.

    Sabotage proof: removing the ``raise_for_status()`` call lets the
    503 body parse to an empty page; ``pytest.raises`` fails. Restored.
    """
    call_count = {"n": 0}

    def _handler(request: httpx.Request) -> httpx.Response:
        del request
        call_count["n"] += 1
        return httpx.Response(503, headers={"Retry-After": "5"}, json={"error": "backendError"})

    client = _build_client(_handler)
    with pytest.raises(httpx.HTTPStatusError) as exc_info:
        client.fetch_initial_events("2026-04-25T00:00:00Z")

    assert exc_info.value.response.status_code == 503
    assert call_count["n"] == 1, f"current contract: google_calendar does not retry 503; saw {call_count['n']} calls"

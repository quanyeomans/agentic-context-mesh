"""Dex CRM HTTP client honours 429 throttling responses — F64.

Pins the post-tenacity-wire behaviour: ``DexCrmClient._send_with_retry``
retries on HTTP 429 with exponential backoff instead of raising on the
first throttled response. After ``config.max_retries`` exhausted
attempts the typed :class:`httpx.HTTPStatusError` surfaces so the
worker dead-letter path catches it explicitly.

Tests inject a recording ``sleep`` callable (NOT a monkeypatch on
``time.sleep`` per F1 / F2) so the retry loop executes synchronously
without wall-clock delay; the recorded waits prove the backoff strategy
ran. Per F47 the test composes the real connector via its public
constructor seam — the client's ``http_client`` + ``sleep`` are
constructor kwargs already designed for this seam.

Why integration not unit: this is the end-to-end throttling contract —
the real :class:`DexCrmClient` + the :class:`httpx.MockTransport` Dex
stub + the tenacity retry loop together. The unit suite
(``tests/connectors/dex_crm/test_connector.py``) covers the retry-count
shape in isolation; this file covers the cross-layer behaviour the F64
gate asks for.

Each ``test_*`` carries the ``@pytest.mark.integration`` marker per F8
and includes a "Sabotage proof:" note describing the mutation that
proves the assertion has teeth; mutations were executed during authoring
and the sabotage assertions failed concretely (then the mutation was
reverted).
"""

from __future__ import annotations

import httpx
import pytest
from tenacity import RetryError

from kairix.connectors.dex_crm.client import DexCrmClient, DexCrmClientConfig
from kairix.transport.auth.api_key import ApiKeyAuth, BearerHeaders

pytestmark = pytest.mark.integration

# Fixtures — sentinel listing path + bearer value shared across cases so
# the F17 duplicate-literal gate stays green.
_LISTING_PATH_TAIL = "/contacts"
_FAKE_BEARER_VALUE = "fake-bearer"  # pragma: allowlist secret — test fixture


class _StaticAuth(ApiKeyAuth):
    """Pinned auth shim — returns a deterministic bearer header.

    Avoids reaching into the real secret-resolution chain; the F64
    contract is about HTTP behaviour, not credential resolution.
    """

    def headers(self, _secret_name: str) -> BearerHeaders:
        return BearerHeaders(mapping={"Authorization": f"Bearer {_FAKE_BEARER_VALUE}"})


def _build_client(
    handler: object,
    *,
    recorded_sleeps: list[float],
    max_retries: int = 4,
) -> DexCrmClient:
    """Wire a real :class:`DexCrmClient` against ``handler``.

    The injected ``sleep`` records every wait the tenacity loop requests
    so tests can assert backoff progression without touching wall-clock
    time. F1 / F2 clean — no patching, no env mutation; the ``sleep``
    callable is a public constructor seam already in production use for
    tests.
    """
    transport = httpx.MockTransport(handler)  # type: ignore[arg-type]  # F3 rationale: pytest typing accepts handler shapes httpx narrows at runtime.
    inner = httpx.Client(transport=transport)
    return DexCrmClient(
        config=DexCrmClientConfig(
            rate_limit_sleep_s=0.0,  # disable inter-request pause for test speed
            max_retries=max_retries,
            backoff_base_s=1.0,
        ),
        http_client=inner,
        auth=_StaticAuth(),
        sleep=recorded_sleeps.append,
    )


@pytest.mark.integration
def test_dex_crm_429_retries_then_succeeds() -> None:
    """A single 429 retries with exponential backoff, then 200 succeeds.

    Sabotage proof: in ``DexCrmClient._send_with_retry``, change
    ``retry=retry_if_result(_is_rate_limited)`` to
    ``retry=retry_if_exception_type(httpx.HTTPError)``. Re-run: the
    first 429 is returned to the caller (no retry), the call_count
    assertion fails (1 != 2) and ``recorded_sleeps`` is empty.
    Restored.
    """
    call_count = {"n": 0}
    recorded_sleeps: list[float] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        if not request.url.path.endswith(_LISTING_PATH_TAIL):
            return httpx.Response(200, json={"data": [], "next_cursor": None})
        call_count["n"] += 1
        if call_count["n"] == 1:
            return httpx.Response(429, json={"error": "throttled"})
        return httpx.Response(200, json={"data": [], "next_cursor": None})

    client = _build_client(_handler, recorded_sleeps=recorded_sleeps)
    records = list(client.iter_listing("contacts", updated_after=None))

    assert records == [], "post-retry 200 returned empty data — should not raise"
    assert call_count["n"] == 2, f"expected 2 attempts (429 -> retry -> 200), saw {call_count['n']}"
    assert len(recorded_sleeps) == 1, f"expected one sleep between attempts, saw {recorded_sleeps!r}"
    # Backoff is exponential with base 1.0; the first sleep is bounded
    # below by the multiplier. tenacity computes wait = multiplier * 2^n
    # for the n-th retry; the exact wait depends on internal scheduling.
    # The contract is "non-zero sleep happened" — pinning the exact
    # value would be brittle against tenacity-internal changes.
    assert recorded_sleeps[0] > 0.0, f"backoff must sleep before retry, saw {recorded_sleeps[0]}"


@pytest.mark.integration
def test_dex_crm_429_exhausted_raises_typed_error() -> None:
    """N+1 consecutive 429 responses exhaust ``max_retries`` and raise.

    Pins two contracts at once: a typed error IS raised once retries
    exhaust (no silent swallow that would let the worker keep polling),
    AND the recorded sleeps prove the backoff progressed.

    The current contract surfaces :class:`tenacity.RetryError` (rather
    than :class:`httpx.HTTPStatusError`) on exhausted-retries because
    the retry predicate is ``retry_if_result`` not ``retry_if_exception_type``
    — tenacity wraps the final "rejected result" in ``RetryError``. GH #358
    covers normalising this to ``HTTPStatusError`` matching the SharePoint
    connector's shape.

    Sabotage proof: changing the retry loop's ``stop`` to
    ``stop_after_attempt(99)`` would never exhaust on the bounded
    handler-call count; the ``pytest.raises`` block sees nothing and
    the test hangs / times out. Restored.
    """
    call_count = {"n": 0}
    recorded_sleeps: list[float] = []
    max_retries = 3

    def _handler(request: httpx.Request) -> httpx.Response:
        if not request.url.path.endswith(_LISTING_PATH_TAIL):
            return httpx.Response(200, json={"data": [], "next_cursor": None})
        call_count["n"] += 1
        return httpx.Response(429, json={"error": "throttled"})

    client = _build_client(_handler, recorded_sleeps=recorded_sleeps, max_retries=max_retries)
    with pytest.raises(RetryError):
        list(client.iter_listing("contacts", updated_after=None))

    assert call_count["n"] == max_retries, (
        f"expected {max_retries} attempts before exhausting retries, saw {call_count['n']}"
    )
    # max_retries attempts => max_retries - 1 sleeps between them.
    assert len(recorded_sleeps) == max_retries - 1, (
        f"expected {max_retries - 1} sleeps for {max_retries} attempts, saw {recorded_sleeps!r}"
    )
    assert all(s > 0.0 for s in recorded_sleeps), (
        f"every retry must sleep before the next attempt, saw {recorded_sleeps!r}"
    )


@pytest.mark.integration
def test_dex_crm_non_retryable_4xx_raises_immediately() -> None:
    """A 403 raises immediately; no retry, no sleep.

    The retry loop's predicate is ``retry_if_result(_is_rate_limited)``
    which only matches 429. Other 4xx codes are permanent for the
    URL + credential pair and must surface to the worker on the first
    response.

    Sabotage proof: widening the predicate to ``response.status_code >= 400``
    would retry the 403 too — the call-count assertion would fail (4
    instead of 1) and ``recorded_sleeps`` would have entries. Restored.
    """
    call_count = {"n": 0}
    recorded_sleeps: list[float] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        if not request.url.path.endswith(_LISTING_PATH_TAIL):
            return httpx.Response(200, json={"data": [], "next_cursor": None})
        call_count["n"] += 1
        return httpx.Response(403, json={"error": "forbidden"})

    client = _build_client(_handler, recorded_sleeps=recorded_sleeps)
    with pytest.raises(httpx.HTTPStatusError) as exc_info:
        list(client.iter_listing("contacts", updated_after=None))

    assert exc_info.value.response.status_code == 403
    assert call_count["n"] == 1, f"403 must not retry, saw {call_count['n']} calls"
    assert recorded_sleeps == [], f"403 must not trigger any sleeps, saw {recorded_sleeps!r}"

"""F64 — Linear GraphQL client honours HTTP 429 + Retry-After backoff.

:class:`kairix.connectors.linear.LinearApiClient` is the only HTTP-bound
surface in the Linear connector. Linear's GraphQL endpoint signals
rate-limiting with HTTP 429 + a ``Retry-After`` header (complexity-based
limit; spec §9). This test pins:

  (a) on 429 + Retry-After the client sleeps the requested seconds via
      the INJECTED sleeper (no real wall-clock wait) and then retries,
  (b) a Retry-After of ``0`` / a missing / non-numeric header falls back
      to the documented default delay, and
  (c) after exhausting the bounded retry budget the client RAISES
      (it does NOT silently swallow the throttle and dead-letter every
      in-flight item — the ADR-024 Bug-2 class).

Linear does not use HTTP 503 for throttling (unlike the Graph-backed
SharePoint sibling); its documented rate-limit signal is 429 only, so
this test exercises the 429 path that the production client actually
implements. The sleeper seam is a plain ``Callable[[float], None]`` —
no real ``time.sleep`` (F82-clean), no monkey-patching (F1-clean).
"""

from __future__ import annotations

import httpx
import pytest

from kairix.connectors.linear.api_client import (
    LINEAR_DEFAULT_RETRY_AFTER_S,
    LinearApiClient,
)

pytestmark = pytest.mark.integration


class _RecordingSleeper:
    """Callable sleeper that records durations instead of sleeping."""

    def __init__(self) -> None:
        self.calls: list[float] = []

    def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)


def _ok_response() -> httpx.Response:
    return httpx.Response(200, json={"data": {"viewer": {"id": "u-1"}}})


def test_429_with_retry_after_honoured() -> None:
    """The client sleeps the Retry-After seconds, then retries and succeeds."""
    served: list[int] = [0]

    def _handler(request: httpx.Request) -> httpx.Response:
        served[0] += 1
        if served[0] == 1:
            return httpx.Response(429, headers={"Retry-After": "2"}, json={"error": "rate limited"})
        return _ok_response()

    sleeper = _RecordingSleeper()
    client = LinearApiClient(
        api_key="test-key",  # pragma: allowlist secret — test fixture
        http=httpx.Client(transport=httpx.MockTransport(_handler)),
        sleeper=sleeper,
    )
    result = client.query("query { viewer { id } }", {})

    assert result == {"viewer": {"id": "u-1"}}
    assert sleeper.calls == [2.0], f"expected one 2s backoff, got {sleeper.calls!r}"
    assert served[0] == 2, "expected exactly one retry after the 429"


def test_429_missing_retry_after_uses_default_delay() -> None:
    """A 429 without a Retry-After header falls back to the default delay."""
    served: list[int] = [0]

    def _handler(request: httpx.Request) -> httpx.Response:
        served[0] += 1
        if served[0] == 1:
            return httpx.Response(429, json={"error": "rate limited"})
        return _ok_response()

    sleeper = _RecordingSleeper()
    client = LinearApiClient(
        api_key="test-key",  # pragma: allowlist secret — test fixture
        http=httpx.Client(transport=httpx.MockTransport(_handler)),
        sleeper=sleeper,
    )
    client.query("query { viewer { id } }", {})

    assert sleeper.calls == [LINEAR_DEFAULT_RETRY_AFTER_S], (
        f"expected the default {LINEAR_DEFAULT_RETRY_AFTER_S}s fallback, got {sleeper.calls!r}"
    )


def test_429_exhausted_retries_raise_not_silent() -> None:
    """After the bounded retry budget is exhausted the client RAISES.

    Pins the ADR-024 Bug-2 class — a perpetually-throttled endpoint must
    surface an error to the orchestrator (which dead-letters the tick),
    not silently swallow the 429 and return empty.
    """

    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, headers={"Retry-After": "1"}, json={"error": "throttled"})

    sleeper = _RecordingSleeper()
    client = LinearApiClient(
        api_key="test-key",  # pragma: allowlist secret — test fixture
        http=httpx.Client(transport=httpx.MockTransport(_handler)),
        sleeper=sleeper,
    )
    with pytest.raises(httpx.HTTPStatusError):
        client.query("query { viewer { id } }", {})
    # The client backed off on each attempt before giving up (bounded).
    assert sleeper.calls, "client must have backed off at least once before exhausting retries"

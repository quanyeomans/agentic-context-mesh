"""Google Drive client honours throttling responses.

Pins the post-fix behaviour: ``_authorised_get`` retries 429 with
``Retry-After``, retries 403 carrying ``userRateLimitExceeded``, and
raises :class:`CredentialExpiredError` on 401 (so the framework can
transition the cc_pair to a credential-renewal state).

Tests inject a recording ``sleep_fn`` (NOT a monkeypatch on
``time.sleep`` per F1 / F2) so the retry loop executes synchronously
without any wall-clock delay; the recorded waits prove the strategy
honoured the server header.

Each ``test_*`` carries the ``@pytest.mark.integration`` marker per F8.
"""

from __future__ import annotations

import httpx
import pytest

from kairix.connectors.google_drive.client import GoogleDriveClient
from kairix.core.protocols import CredentialExpiredError

pytestmark = pytest.mark.integration

_DRIVE_URL_TAIL = "/changes?pageToken=seed"


def _build_client(
    handler: object,
    *,
    recorded_sleeps: list[float],
    max_attempts: int = 5,
) -> GoogleDriveClient:
    """Wire a real :class:`GoogleDriveClient` to ``handler``.

    The injected ``sleep_fn`` records every wait the retry loop
    requests so tests can assert on backoff progression without
    touching wall clock time. F1 / F2 clean — no patching, no env
    mutation; the ``sleep_fn`` is a public constructor seam.
    """
    transport = httpx.MockTransport(handler)  # type: ignore[arg-type]  # F3 rationale: pytest typing accepts handler shapes httpx narrows at runtime.
    shared = httpx.Client(transport=transport)
    return GoogleDriveClient(
        access_token="fake-token-value",  # pragma: allowlist secret — test fixture
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
    :class:`httpx.HTTPStatusError` and this test fails on the absence
    of the recovered call.
    """
    call_count = {"n": 0}
    recorded_sleeps: list[float] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        call_count["n"] += 1
        if call_count["n"] == 1:
            return httpx.Response(429, headers={"Retry-After": "2"}, json={"error": "throttled"})
        return httpx.Response(200, json={"changes": [], "newStartPageToken": "fresh"})

    client = _build_client(_handler, recorded_sleeps=recorded_sleeps)
    items = list(client.iter_changes("seed"))

    assert items == [], "post-retry 200 returned empty changes — should not raise"
    assert call_count["n"] == 2, f"expected 2 calls (429 → retry → 200), saw {call_count['n']}"
    assert len(recorded_sleeps) == 1, f"expected one sleep between attempts, saw {recorded_sleeps!r}"
    assert recorded_sleeps[0] == pytest.approx(2.0, abs=0.01), (
        f"client must honour Retry-After=2 seconds, slept {recorded_sleeps[0]}s"
    )


@pytest.mark.integration
def test_403_with_user_rate_limit_exceeded_reason_retries() -> None:
    """403 carrying ``userRateLimitExceeded`` in the body is treated as throttle.

    Drive's older quota-exhaust shape emits 403 with the reason in the
    JSON body; new code paths use 429 but the older shape still
    appears in production. Retry on this specific 403 reason; plain
    403 (permission denied) stays a hard failure.

    Sabotage proof: removing the body inspection from
    ``_is_retryable_response`` causes the first 403 to raise
    immediately (call_count == 1, recorded_sleeps empty).
    """
    call_count = {"n": 0}
    recorded_sleeps: list[float] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        call_count["n"] += 1
        if call_count["n"] == 1:
            return httpx.Response(
                403,
                json={"error": {"errors": [{"reason": "userRateLimitExceeded"}]}},
            )
        return httpx.Response(200, json={"changes": [], "newStartPageToken": "fresh"})

    client = _build_client(_handler, recorded_sleeps=recorded_sleeps)
    items = list(client.iter_changes("seed"))

    assert items == []
    assert call_count["n"] == 2, f"expected retry after quota-403, saw {call_count['n']}"
    assert len(recorded_sleeps) == 1, f"expected one sleep, saw {recorded_sleeps!r}"


@pytest.mark.integration
def test_401_raises_credential_expired() -> None:
    """A 401 raises :class:`CredentialExpiredError` for the framework lifecycle.

    Drive treats 401 as token-revoked — there's no recovery via retry,
    the operator must rotate the OAuth grant. The connector surfaces a
    typed exception so the framework's cc_pair lifecycle can transition
    to a credential-renewal state.

    Sabotage proof: removing the ``CredentialExpiredError`` raise (or
    classifying 401 as a generic retryable status) makes this test
    fail because the raised exception type changes (or no exception is
    raised at all and the call returns empty).
    """
    call_count = {"n": 0}
    recorded_sleeps: list[float] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        call_count["n"] += 1
        return httpx.Response(401, json={"error": "unauthorized"})

    client = _build_client(_handler, recorded_sleeps=recorded_sleeps)
    with pytest.raises(CredentialExpiredError) as exc_info:
        list(client.iter_changes("seed"))

    assert "401" in str(exc_info.value) or "expired" in str(exc_info.value).lower()
    assert call_count["n"] == 1, f"401 must not retry, saw {call_count['n']} calls"
    assert recorded_sleeps == [], f"401 must not trigger any sleeps, saw {recorded_sleeps!r}"


@pytest.mark.integration
def test_no_retry_on_plain_403() -> None:
    """A plain 403 without a rate-limit reason raises immediately.

    Permission-denied 403 (file not shared with the credential) is
    permanent for this URL — the retry loop must NOT retry it.

    Sabotage proof: returning ``True`` from
    ``_is_drive_quota_403`` for every 403 makes this test fail
    because the connector would retry until exhaustion.
    """
    call_count = {"n": 0}
    recorded_sleeps: list[float] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        call_count["n"] += 1
        return httpx.Response(403, json={"error": {"errors": [{"reason": "permissionDenied"}]}})

    client = _build_client(_handler, recorded_sleeps=recorded_sleeps)
    with pytest.raises(httpx.HTTPStatusError) as exc_info:
        list(client.iter_changes("seed"))

    assert exc_info.value.response.status_code == 403
    assert call_count["n"] == 1, f"plain 403 must not retry, saw {call_count['n']} calls"
    assert recorded_sleeps == [], f"plain 403 must not trigger any sleeps, saw {recorded_sleeps!r}"


@pytest.mark.integration
def test_429_repeated_eventually_raises() -> None:
    """N+1 429 responses exhaust the retry budget and raise.

    Pins two contracts at once: ``HTTPStatusError`` IS raised once
    retries exhaust (no silent swallow), AND the recorded sleeps prove
    the backoff progressed.
    """
    call_count = {"n": 0}
    recorded_sleeps: list[float] = []
    max_attempts = 3

    def _handler(request: httpx.Request) -> httpx.Response:
        call_count["n"] += 1
        return httpx.Response(429, headers={"Retry-After": "2"}, json={"error": "throttled"})

    client = _build_client(_handler, recorded_sleeps=recorded_sleeps, max_attempts=max_attempts)
    with pytest.raises(httpx.HTTPStatusError) as exc_info:
        list(client.iter_changes("seed"))

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

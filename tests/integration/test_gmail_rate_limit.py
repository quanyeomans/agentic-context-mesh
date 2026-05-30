"""Gmail API client honours rate-limit + auth-error contracts (F64).

The Gmail surface exposes three throttling shapes:

* **429** with optional ``Retry-After`` — primary rate limit, surfaces
  as :class:`ContainerTransientError` carrying the retry budget.
* **403 with reason in {userRateLimitExceeded, rateLimitExceeded,
  dailyLimitExceeded}** — Gmail's per-user / per-quota signal; also
  surfaces as :class:`ContainerTransientError`.
* **401** — invalid_grant or expired access token; surfaces as
  :class:`CredentialExpiredError` after one in-client retry (the
  client invalidates its cached bearer and re-fetches via the
  token refresher).

A bare 403 (no rate-limit reason) is a permanent permission denial
and surfaces as :class:`InsufficientPermissionsError` so the runner
pauses the cc_pair for operator action.

Why integration not unit: this is the end-to-end throttling contract —
construction of the real :class:`GmailClient` + the
:class:`httpx.MockTransport` Gmail stub + the typed-error translation
together. F1 / F2 clean — no patching, no env mutation; the
``http_client`` is a public constructor seam.

Each ``test_*`` carries the ``@pytest.mark.integration`` marker per F8
and includes a "Sabotage proof:" note describing the mutation that
proves the assertion has teeth.
"""

from __future__ import annotations

import httpx
import pytest

from kairix.connectors.gmail.client import GmailClient
from kairix.core.protocols import (
    ContainerTransientError,
    CredentialExpiredError,
    InsufficientPermissionsError,
)

pytestmark = pytest.mark.integration

_FAKE_USER = "agent-alpha@example.com"
_FAKE_BEARER = "fake-bearer-value"  # pragma: allowlist secret — test fixture


def _build_client(handler: object) -> GmailClient:
    """Wire a real :class:`GmailClient` to a MockTransport handler.

    F1 / F2 clean — no patching, no env mutation; the ``http_client``
    is a public constructor seam.
    """
    transport = httpx.MockTransport(handler)  # type: ignore[arg-type]  # F3 rationale: httpx accepts handler shapes broader than the static annotation; cast-narrow at boundary only.
    shared = httpx.Client(transport=transport)
    return GmailClient(
        user_email=_FAKE_USER,
        token_refresher=lambda: _FAKE_BEARER,
        http_client=shared,
    )


@pytest.mark.integration
def test_gmail_429_with_retry_after_raises_container_transient() -> None:
    """A 429 + Retry-After must surface as :class:`ContainerTransientError`
    carrying the retry budget so the framework defers the tick rather
    than dead-lettering every in-flight item.

    Sabotage proof: removing the 429 branch from
    :meth:`GmailClient._raise_for_status` so 429 falls through to the
    catch-all transient branch (still raises, but without
    rate_limited_403_total bump) flips the counter assertion.
    """

    def _handler(request: httpx.Request) -> httpx.Response:
        _ = request
        return httpx.Response(
            status_code=429,
            headers={"Retry-After": "45"},
            json={"error": {"message": "Rate Limit Exceeded"}},
        )

    client = _build_client(_handler)
    with pytest.raises(ContainerTransientError) as exc_info:
        client.get_profile_history_id()
    assert exc_info.value.retry_after == 45.0, (
        f"client must thread Retry-After through; got {exc_info.value.retry_after}"
    )
    snapshot = client.stats()
    assert snapshot.rate_limited_403_total == 1, (
        f"429 must bump the rate-limit counter; got {snapshot.rate_limited_403_total}"
    )


@pytest.mark.integration
def test_gmail_403_user_rate_limit_exceeded_raises_container_transient() -> None:
    """403 + reason=userRateLimitExceeded surfaces as transient with retry budget.

    Sabotage proof: removing the rate-limit reason check in
    :meth:`GmailClient._raise_for_status` flips this case to
    :class:`InsufficientPermissionsError`; the connector would pause
    the cc_pair instead of waiting for the per-user quota to roll over.
    """

    def _handler(request: httpx.Request) -> httpx.Response:
        _ = request
        return httpx.Response(
            status_code=403,
            json={
                "error": {
                    "code": 403,
                    "errors": [{"reason": "userRateLimitExceeded", "message": "User Rate Limit Exceeded"}],
                }
            },
        )

    client = _build_client(_handler)
    with pytest.raises(ContainerTransientError) as exc_info:
        client.get_profile_history_id()
    assert exc_info.value.retry_after is not None
    assert "userRateLimitExceeded" in str(exc_info.value)


@pytest.mark.integration
def test_gmail_403_daily_limit_exceeded_raises_container_transient() -> None:
    """403 + reason=dailyLimitExceeded surfaces as transient.

    Sabotage proof: removing dailyLimitExceeded from the
    ``_RATE_LIMIT_REASONS`` frozenset flips this case to
    :class:`InsufficientPermissionsError`.
    """

    def _handler(request: httpx.Request) -> httpx.Response:
        _ = request
        return httpx.Response(
            status_code=403,
            json={
                "error": {
                    "errors": [{"reason": "dailyLimitExceeded", "message": "Daily Limit Exceeded"}],
                }
            },
        )

    client = _build_client(_handler)
    with pytest.raises(ContainerTransientError):
        client.get_profile_history_id()


@pytest.mark.integration
def test_gmail_403_without_rate_limit_signal_raises_insufficient_permissions() -> None:
    """A bare 403 (no rate-limit reason) is a permanent permission denial
    and surfaces as :class:`InsufficientPermissionsError` so the cc_pair
    is paused for operator action rather than retried.

    Sabotage proof: widening the rate-limit branch to fire on every 403
    (without the reason check) would flip this assertion to red — the
    framework would treat a permission denial as a transient and
    hot-spin retries against a dead scope.
    """

    def _handler(request: httpx.Request) -> httpx.Response:
        _ = request
        return httpx.Response(
            status_code=403,
            json={
                "error": {
                    "errors": [{"reason": "forbidden", "message": "Insufficient Permission"}],
                }
            },
        )

    client = _build_client(_handler)
    with pytest.raises(InsufficientPermissionsError):
        client.get_profile_history_id()


@pytest.mark.integration
def test_gmail_401_refreshes_token_once_then_raises_credential_expired() -> None:
    """A 401 triggers the single token-refresh + retry path, then re-raises.

    The client explicitly handles 401 via :meth:`invalidate_token` plus
    one re-fetch via the token refresher. This is distinct from 429
    (no retry, just defer) — it pins the auth-refresh contract.

    Sabotage proof: removing the ``invalidate_token + retry`` block
    in :meth:`_authorised_get_json` so 401 raises on first occurrence
    drops the call_count from 2 to 1.
    """
    data_call_count = {"n": 0}

    def _handler(request: httpx.Request) -> httpx.Response:
        _ = request
        data_call_count["n"] += 1
        return httpx.Response(
            status_code=401,
            json={"error": {"message": "Invalid Credentials"}},
        )

    client = _build_client(_handler)
    with pytest.raises(CredentialExpiredError):
        client.get_profile_history_id()
    assert data_call_count["n"] == 2, (
        f"401 must trigger one token-refresh + retry (2 data calls total); saw {data_call_count['n']}"
    )


@pytest.mark.integration
def test_gmail_503_raises_container_transient() -> None:
    """A 503 surfaces as :class:`ContainerTransientError` with default 30s budget.

    Sabotage proof: removing the 5xx → transient branch in
    :meth:`_raise_for_status` would flip 503 to the catch-all 4xx
    branch and drop the retry budget to None.
    """

    def _handler(request: httpx.Request) -> httpx.Response:
        _ = request
        return httpx.Response(status_code=503, json={"error": {"message": "Backend Error"}})

    client = _build_client(_handler)
    with pytest.raises(ContainerTransientError) as exc_info:
        client.get_profile_history_id()
    assert exc_info.value.retry_after == 30.0


@pytest.mark.integration
def test_gmail_404_raises_transient_per_item() -> None:
    """A 404 surfaces as transient but with no retry budget — the framework
    dead-letters the specific item rather than the cc_pair.

    Sabotage proof: changing the 404 catch-all to raise
    :class:`InsufficientPermissionsError` would pause the cc_pair
    every time a single item went missing — clearly wrong shape.
    """

    def _handler(request: httpx.Request) -> httpx.Response:
        _ = request
        return httpx.Response(status_code=404, json={"error": {"message": "Not Found"}})

    client = _build_client(_handler)
    with pytest.raises(ContainerTransientError) as exc_info:
        client.get_profile_history_id()
    assert exc_info.value.retry_after is None

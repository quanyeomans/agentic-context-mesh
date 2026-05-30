"""GitHub API client honours primary + secondary rate-limit contracts.

GitHub exposes two distinct rate-limit shapes (see
:mod:`kairix.connectors.github.api_client`):

* **Primary** — 5000 req/h per installation; signalled via the
  ``x-ratelimit-remaining`` header. When the header reads ``0``, the
  next 403 surfaces as a transient rate-limit (not a permanent
  permission denial).
* **Secondary / abuse** — bursty-parallelism limit; signalled by a
  ``403`` + ``Retry-After`` header.

Both shapes MUST surface as :class:`ContainerTransientError` carrying
the retry budget so the framework runner can defer the tick rather
than dead-letter every in-flight item or pause the cc_pair as if the
installation had lost permission.

Why integration not unit: this is the end-to-end throttling contract —
construction of the real :class:`GitHubApiClient` + the
:class:`httpx.MockTransport` GitHub stub + the typed-error translation
together. F1 / F2 clean — no patching, no env mutation; the
``http_client`` is a public constructor seam.

Each ``test_*`` carries the ``@pytest.mark.integration`` marker per F8.
"""

from __future__ import annotations

import httpx
import pytest

from kairix.connectors.github.api_client import GitHubApiClient
from kairix.core.protocols import (
    ContainerTransientError,
    CredentialExpiredError,
    InsufficientPermissionsError,
)

pytestmark = pytest.mark.integration

# Test fixture — a fake PAT shape. The token never reaches the real
# GitHub edge because every request is intercepted by MockTransport.
_FAKE_PAT = "ghp-fake-token-value"  # pragma: allowlist secret — test fixture


def _build_client(handler: object) -> GitHubApiClient:
    """Wire a real :class:`GitHubApiClient` to a MockTransport handler.

    F1 / F2 clean — no patching, no env mutation; the ``http_client``
    is a public constructor seam.
    """
    transport = httpx.MockTransport(handler)  # type: ignore[arg-type]  # F3 rationale: httpx accepts handler shapes broader than the static annotation; cast-narrow at boundary only.
    shared = httpx.Client(transport=transport)
    return GitHubApiClient(personal_access_token=_FAKE_PAT, http_client=shared)


@pytest.mark.integration
def test_secondary_rate_limit_403_with_retry_after_raises_container_transient() -> None:
    """A 403 + Retry-After must surface as :class:`ContainerTransientError`
    carrying the retry budget so the framework defers the tick rather
    than treating the throttle as a permanent permission denial.

    Sabotage proof: removing the ``retry_after_header is not None``
    branch in :meth:`GitHubApiClient._raise_for_status` flips secondary
    rate-limits to raise :class:`InsufficientPermissionsError` instead;
    the bot would pause the cc_pair unnecessarily. This assertion
    catches that drift.
    """

    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status_code=403,
            headers={"Retry-After": "30", "x-github-request-id": "req-rate-limit"},
            json={"message": "You have triggered an abuse detection mechanism"},
        )

    client = _build_client(_handler)
    with pytest.raises(ContainerTransientError) as exc_info:
        client.list_installation_repositories()
    assert exc_info.value.retry_after == 30.0, (
        f"client must thread Retry-After through; got {exc_info.value.retry_after}"
    )
    assert "secondary/abuse" in str(exc_info.value)


@pytest.mark.integration
def test_primary_rate_limit_exhausted_403_with_remaining_zero_raises_container_transient() -> None:
    """A 403 with ``x-ratelimit-remaining: 0`` (and no Retry-After)
    surfaces as :class:`ContainerTransientError` — the primary 5000/h
    window is exhausted, not a permission problem.

    Sabotage proof: removing the ``if remaining == "0"`` branch in
    :meth:`GitHubApiClient._raise_for_status` flips this case to
    :class:`InsufficientPermissionsError`; the connector would pause
    the cc_pair instead of waiting for the hour to roll over.
    """

    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status_code=403,
            headers={
                "x-ratelimit-remaining": "0",
                "x-ratelimit-reset": "1716894000",
                "x-github-request-id": "req-primary-exhaust",
            },
            json={"message": "API rate limit exceeded"},
        )

    client = _build_client(_handler)
    with pytest.raises(ContainerTransientError) as exc_info:
        client.list_installation_repositories()
    assert exc_info.value.retry_after >= 1.0, (
        f"primary-limit-exhausted must surface a positive retry budget; got {exc_info.value.retry_after}"
    )


@pytest.mark.integration
def test_403_without_rate_limit_signal_raises_insufficient_permissions() -> None:
    """A bare 403 (no Retry-After, no ``remaining: 0``) is a permanent
    permission denial and surfaces as :class:`InsufficientPermissionsError`
    so the cc_pair is paused for operator action rather than retried.

    Sabotage proof: widening the rate-limit branch to also fire on a
    bare 403 (without checking the headers) would flip this assertion
    to red — the framework would treat a permission denial as a
    transient and hot-spin retries against a dead credential.
    """

    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status_code=403,
            headers={"x-github-request-id": "req-perm-denied"},
            json={"message": "Resource not accessible by integration"},
        )

    client = _build_client(_handler)
    with pytest.raises(InsufficientPermissionsError):
        client.list_installation_repositories()


@pytest.mark.integration
def test_401_unauthorized_raises_credential_expired() -> None:
    """A 401 surfaces as :class:`CredentialExpiredError` and invalidates
    the cached token so the next call re-rotates instead of carrying a
    dead bearer through every request.

    Sabotage proof: removing the ``self.invalidate_token()`` call in
    the 401 branch of :meth:`GitHubApiClient._raise_for_status` would
    cause the next call to reuse the dead token — but the typed-error
    assertion on this test still pins the surface contract: 401
    surfaces as CredentialExpiredError so the framework can transition
    the cc_pair to INVALID.
    """

    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status_code=401,
            headers={"x-github-request-id": "req-unauth"},
            json={"message": "Bad credentials"},
        )

    client = _build_client(_handler)
    with pytest.raises(CredentialExpiredError) as exc_info:
        client.list_installation_repositories()
    assert "401 unauthorised" in str(exc_info.value)

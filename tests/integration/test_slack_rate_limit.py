"""Slack Web API client honours throttling + auth-failure contracts.

Pins the F64 contract on :class:`SlackWebClient`: when the Slack edge
returns a 429 (HTTP-level) or an ``ok: false, error: "ratelimited"``
envelope (payload-level), the client raises
:class:`~kairix.core.protocols.ContainerTransientError` carrying the
``Retry-After`` budget so the framework runner can defer the tick
rather than dead-letter every in-flight message.

Similarly, 401 / 403 from the Slack edge surfaces as
:class:`~kairix.core.protocols.CredentialExpiredError` so the
framework can transition the cc_pair to ``INVALID`` rather than
hot-spin against a dead workspace install.

Why integration not unit: this is the end-to-end throttling contract —
construction of the real :class:`SlackWebClient` + the
:class:`httpx.MockTransport` Slack stub + the typed-error translation
together. F1 / F2 clean — no patching, no env mutation; the
``http_client`` is a public constructor seam.

Each ``test_*`` carries the ``@pytest.mark.integration`` marker per F8.
"""

from __future__ import annotations

import httpx
import pytest

from kairix.connectors.slack.web_client import SlackWebClient
from kairix.core.protocols import (
    ContainerTransientError,
    CredentialExpiredError,
)

pytestmark = pytest.mark.integration

# Test fixture — a fake bot token. The token never reaches a real
# Slack endpoint because every request is intercepted by the
# MockTransport handler.
_FAKE_BOT_TOKEN = "xoxb-test-fake-token-value"  # pragma: allowlist secret — test fixture


def _build_client(handler: object) -> SlackWebClient:
    """Wire a real :class:`SlackWebClient` to a MockTransport handler.

    F1 / F2 clean — no patching, no env mutation; the ``http_client``
    is a public dataclass field.
    """
    transport = httpx.MockTransport(handler)  # type: ignore[arg-type]  # F3 rationale: httpx accepts handler shapes broader than the static annotation; cast-narrow at boundary only.
    shared = httpx.Client(transport=transport)
    return SlackWebClient(token=_FAKE_BOT_TOKEN, http_client=shared)


@pytest.mark.integration
def test_429_with_retry_after_raises_container_transient_carrying_budget() -> None:
    """A Slack HTTP 429 + ``Retry-After`` surfaces as
    :class:`ContainerTransientError` with the retry budget threaded
    through so the framework defers the tick rather than dead-lettering
    every in-flight message.

    Sabotage proof: deleting the ``if response.status_code == 429``
    branch in :meth:`SlackWebClient._post` flips this test to red — the
    429 falls through to ``response.raise_for_status()`` and the
    framework would see :class:`httpx.HTTPStatusError` instead of the
    typed transient with its retry budget.
    """

    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, headers={"Retry-After": "30"}, json={"ok": False, "error": "rate_limited"})

    client = _build_client(_handler)
    with pytest.raises(ContainerTransientError) as exc_info:
        client.auth_test()

    assert exc_info.value.retry_after == 30.0, (
        f"client must thread Retry-After through retry_after; got {exc_info.value.retry_after}"
    )
    assert "auth.test" in str(exc_info.value)


@pytest.mark.integration
def test_payload_ratelimited_raises_container_transient() -> None:
    """A Slack ``ok: false, error: "ratelimited"`` payload (with a 200
    HTTP status) surfaces as :class:`ContainerTransientError` per
    :mod:`kairix.connectors.slack.web_client`'s docstring contract —
    Slack docs note both shapes occur in practice and must converge on
    the same caller-observable behaviour.

    Sabotage proof: removing the ``if error_code == "ratelimited"``
    branch in :func:`_raise_for_payload_error` flips this test to red
    — the payload would fall through to the generic ``RuntimeError``
    path and the framework would treat it as a permanent error.
    """

    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": False, "error": "ratelimited"})

    client = _build_client(_handler)
    with pytest.raises(ContainerTransientError) as exc_info:
        client.auth_test()

    assert exc_info.value.retry_after == 1.0, (
        f"payload-level ratelimited must surface a positive retry budget; got {exc_info.value.retry_after}"
    )


@pytest.mark.integration
def test_401_unauthorized_raises_credential_expired() -> None:
    """A Slack HTTP 401 surfaces as :class:`CredentialExpiredError` so
    the framework transitions the cc_pair to ``INVALID`` rather than
    hot-spin against a dead workspace install.

    Sabotage proof: removing the ``if response.status_code in (401, 403)``
    branch in :meth:`SlackWebClient._post` flips this test to red — the
    401 falls through to ``response.raise_for_status()`` and the typed
    error mapping is lost.
    """

    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "invalid_auth"})

    client = _build_client(_handler)
    with pytest.raises(CredentialExpiredError) as exc_info:
        client.auth_test()

    assert "workspace install rejected" in str(exc_info.value)


@pytest.mark.integration
def test_payload_invalid_auth_raises_credential_expired() -> None:
    """A Slack ``ok: false, error: "invalid_auth"`` payload (with 200
    HTTP status) surfaces as :class:`CredentialExpiredError` — same
    semantics as the HTTP 401 path.

    Sabotage proof: removing ``invalid_auth`` from
    ``_FATAL_AUTH_ERRORS`` flips this test to red — the payload falls
    through to the generic ``RuntimeError`` path and the framework
    can't distinguish a dead workspace install from a transient API
    glitch.
    """

    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": False, "error": "invalid_auth"})

    client = _build_client(_handler)
    with pytest.raises(CredentialExpiredError):
        client.auth_test()

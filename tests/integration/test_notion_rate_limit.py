"""Notion API client honours the rate-limit / unavailable contract.

Pins the failure-shape :class:`NotionApiClient` exposes to the
connector when the Notion edge throttles or is unreachable. The
current implementation does NOT silently swallow a 429 — it surfaces
:class:`httpx.HTTPStatusError` so the framework runner can defer the
tick rather than dead-letter every in-flight page.

This is the F64 contract: every plugin importing an HTTP client must
ship a rate-limit test asserting either (a) the client backs off
cleanly OR (b) the client returns a typed failure the caller can act
on. The Notion client takes path (b) — the typed failure is the
``HTTPStatusError`` whose ``response.status_code`` carries 429 / 503
so the connector's `list_changes` boundary can translate it.

Why integration not unit: this is the end-to-end throttling contract —
construction of the real :class:`NotionApiClient` + the
:class:`httpx.MockTransport` Notion stub + the failure-surface
together. F1 / F2 clean — no patching, no env mutation; the
``http_client`` is a public constructor seam.

Each ``test_*`` carries the ``@pytest.mark.integration`` marker per F8.
"""

from __future__ import annotations

import httpx
import pytest

from kairix.connectors.notion.api_client import NotionApiClient

pytestmark = pytest.mark.integration

# Test fixtures — a deterministic Notion API base + a fake bearer
# token. The token never reaches a real Notion endpoint because every
# request is intercepted by the MockTransport handler.
_NOTION_BASE = "https://api.notion.com/v1"
_FAKE_TOKEN = "secret_fake_token_value"  # pragma: allowlist secret — test fixture


def _build_client(handler: object) -> NotionApiClient:
    """Wire a real :class:`NotionApiClient` to a MockTransport handler.

    F1 / F2 clean — no patching, no env mutation; the ``http_client``
    is a public constructor seam.
    """
    transport = httpx.MockTransport(handler)  # type: ignore[arg-type]  # F3 rationale: httpx accepts handler shapes broader than the static annotation; cast-narrow at boundary only.
    shared = httpx.Client(transport=transport)
    return NotionApiClient(token=_FAKE_TOKEN, http_client=shared)


@pytest.mark.integration
def test_429_with_retry_after_raises_typed_failure_not_silent_swallow() -> None:
    """A Notion 429 surfaces as :class:`httpx.HTTPStatusError` carrying
    ``Retry-After`` so the connector framework can defer the tick.

    Sabotage proof: if the client were to silently catch the 429 and
    return an empty result, this ``with pytest.raises`` block would
    fail to observe the exception and the test would error. Adding a
    bare ``try/except httpx.HTTPStatusError: return``-style swallow in
    ``_authorised_post`` flips the test to red.
    """
    call_count = {"n": 0}

    def _handler(request: httpx.Request) -> httpx.Response:
        call_count["n"] += 1
        return httpx.Response(
            429,
            headers={"Retry-After": "30"},
            json={"object": "error", "code": "rate_limited", "message": "Rate limited"},
        )

    client = _build_client(_handler)
    with pytest.raises(httpx.HTTPStatusError) as exc_info:
        list(client.search_pages())

    assert exc_info.value.response.status_code == 429, (
        f"429 must surface the throttle status code; got {exc_info.value.response.status_code}"
    )
    assert exc_info.value.response.headers.get("Retry-After") == "30", (
        "client must preserve the Retry-After hint on the response so the framework can defer"
    )
    assert call_count["n"] == 1, f"client must NOT spin-retry 429 inside the request loop; saw {call_count['n']} calls"


@pytest.mark.integration
def test_503_unavailable_raises_typed_failure() -> None:
    """A Notion 503 surfaces as :class:`httpx.HTTPStatusError` so the
    framework can treat it as a transient unavailability and defer the
    tick rather than dead-letter the in-flight container.

    Sabotage proof: removing the ``response.raise_for_status()`` call
    in :meth:`NotionApiClient._authorised_post` flips this test to red
    — the call returns a 503 envelope as if it were a normal payload
    and the connector would parse a malformed ``results`` array
    instead of yielding back to the runner.
    """

    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"error": "service unavailable"})

    client = _build_client(_handler)
    with pytest.raises(httpx.HTTPStatusError) as exc_info:
        list(client.search_pages())

    assert exc_info.value.response.status_code == 503


@pytest.mark.integration
def test_401_unauthorized_raises_typed_failure() -> None:
    """A Notion 401 surfaces as :class:`httpx.HTTPStatusError` so the
    framework can transition the cc_pair to INVALID rather than retry.

    Sabotage proof: swallowing the 401 in ``_authorised_post`` would
    flip the assertion (no exception raised); the connector would
    silently continue against a dead credential.
    """

    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "API token is invalid"})

    client = _build_client(_handler)
    with pytest.raises(httpx.HTTPStatusError) as exc_info:
        list(client.search_pages())

    assert exc_info.value.response.status_code == 401

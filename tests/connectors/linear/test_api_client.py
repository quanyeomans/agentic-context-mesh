"""Unit tests for :class:`kairix.connectors.linear.LinearApiClient`.

Scope:
  * HTTPS-only guard — ``http://`` endpoint rejected at construction time.
  * 429 backoff (F64) — client retries and succeeds; injected sleeper
    callable records sleep durations so the test can assert retry happened
    without wall-clock waits.
  * Pagination — ``paginate()`` drains two scripted pages and stops at
    ``hasNextPage=false``.
  * Error surfaces — GraphQL ``errors`` key, missing ``data`` key, and
    429 exhaustion all raise the documented exception.

F1-clean (no monkey-patching or @patch). F8 carries ``@pytest.mark.unit``.
The sleeper seam is a plain ``Callable[[float], None]`` injected through
the constructor; the HTTP client is injected via ``httpx.MockTransport``.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from kairix.connectors.linear.api_client import (
    LINEAR_DEFAULT_RETRY_AFTER_S,
    LINEAR_GRAPHQL_ENDPOINT,
    LinearApiClient,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


class _RecordingSleeper:
    """Callable sleeper that records durations instead of sleeping.

    Passed as ``sleeper=`` to ``LinearApiClient`` (the seam is a plain
    ``Callable[[float], None]``) so tests run at full speed while still
    asserting retry behaviour — no real ``time.sleep`` in tests.
    """

    def __init__(self) -> None:
        self.sleeps: list[float] = []

    def __call__(self, seconds: float) -> None:
        self.sleeps.append(seconds)


def _make_gql_response(data: dict[str, Any]) -> httpx.Response:
    """Build a successful 200 GraphQL response JSON envelope."""
    return httpx.Response(200, json={"data": data})


def _make_gql_page(
    connection: str,
    nodes: list[dict[str, Any]],
    *,
    has_next: bool,
    end_cursor: str | None,
) -> dict[str, Any]:
    """Build a single GraphQL connection page payload."""
    return {
        connection: {
            "nodes": nodes,
            "pageInfo": {
                "hasNextPage": has_next,
                "endCursor": end_cursor,
            },
        }
    }


# ---------------------------------------------------------------------------
# Test 1 — HTTPS-only guard
# ---------------------------------------------------------------------------


def test_https_guard_rejects_http_endpoint() -> None:
    """LinearApiClient raises ValueError on a non-https endpoint.

    Spec §3: the client enforces HTTPS-only; no code path may issue an
    ``http://`` request. The guard lives in ``__init__`` so a mis-configured
    endpoint is caught immediately at construction time, not at first use.
    """
    with pytest.raises(ValueError, match="https"):
        LinearApiClient(api_key="k", endpoint="http://evil.example/graphql")


def test_https_guard_accepts_default_endpoint() -> None:
    """The default endpoint constant is ``https://``."""
    assert LINEAR_GRAPHQL_ENDPOINT.startswith("https://")
    client = LinearApiClient(api_key="k")
    assert client is not None


# ---------------------------------------------------------------------------
# Test 2 — 429 backoff (F64 rate-limit test)
# ---------------------------------------------------------------------------


def test_query_retries_on_429_and_succeeds() -> None:
    """query() retries once after a 429 and returns the data on success.

    The injected sleeper records the sleep call so we can assert the
    retry happened without any real wall-clock wait.
    """
    call_count: list[int] = [0]

    def _handler(request: httpx.Request) -> httpx.Response:
        call_count[0] += 1
        if call_count[0] == 1:
            return httpx.Response(
                429,
                headers={"Retry-After": "1"},
                json={"error": "rate limited"},
            )
        return _make_gql_response({"viewer": {"id": "u-1"}})

    transport = httpx.MockTransport(_handler)
    http = httpx.Client(transport=transport)
    sleeper = _RecordingSleeper()

    client = LinearApiClient(api_key="test-key", http=http, sleeper=sleeper)
    result = client.query("query { viewer { id } }", {})

    assert result == {"viewer": {"id": "u-1"}}
    assert call_count[0] == 2, "expected exactly one retry after the 429"
    assert len(sleeper.sleeps) >= 1, "expected sleeper called at least once"


# ---------------------------------------------------------------------------
# Test 3 — Pagination
# ---------------------------------------------------------------------------


def test_paginate_yields_all_nodes_across_two_pages() -> None:
    """paginate() drains two pages and stops at hasNextPage=false.

    Each page is served by the scripted handler. The first page carries
    ``hasNextPage=true`` and an ``endCursor``; the second page carries
    ``hasNextPage=false``. The iterator must yield all nodes from both
    pages and then stop.
    """
    pages_served: list[int] = [0]

    def _handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        variables = body.get("variables", {})
        after = variables.get("after")

        pages_served[0] += 1
        if after is None:
            data = _make_gql_page(
                "issues",
                nodes=[{"id": "i-1", "title": "First"}, {"id": "i-2", "title": "Second"}],
                has_next=True,
                end_cursor="cursor-abc",
            )
        else:
            data = _make_gql_page(
                "issues",
                nodes=[{"id": "i-3", "title": "Third"}],
                has_next=False,
                end_cursor=None,
            )
        return _make_gql_response(data)

    transport = httpx.MockTransport(_handler)
    http = httpx.Client(transport=transport)
    sleeper = _RecordingSleeper()

    client = LinearApiClient(api_key="test-key", http=http, sleeper=sleeper)
    doc = "query($after: String) { issues { nodes { id title } pageInfo { hasNextPage endCursor } } }"
    nodes = list(client.paginate(doc, {}, connection="issues"))

    assert [n["id"] for n in nodes] == ["i-1", "i-2", "i-3"]
    assert pages_served[0] == 2, "expected exactly two pages fetched"
    assert len(sleeper.sleeps) == 0, "no rate-limit hit; sleeper should not have been called"


# ---------------------------------------------------------------------------
# Test 4 — Empty-cursor guard
# ---------------------------------------------------------------------------


def test_paginate_stops_on_empty_string_cursor() -> None:
    """paginate() stops when endCursor is an empty string.

    Pins the ``or not cursor`` branch on the cursor validity guard:
    an empty-string endCursor is logically absent even if ``hasNextPage``
    is True. Without this test, a mutant that changes ``or`` to ``and``
    would survive — an empty cursor would be advanced as a real one.
    """
    call_count: list[int] = [0]

    def _handler(request: httpx.Request) -> httpx.Response:
        call_count[0] += 1
        data = _make_gql_page(
            "issues",
            nodes=[{"id": "i-1"}],
            has_next=True,
            end_cursor="",  # empty — treated as absent
        )
        return _make_gql_response(data)

    transport = httpx.MockTransport(_handler)
    http = httpx.Client(transport=transport)
    sleeper = _RecordingSleeper()

    client = LinearApiClient(api_key="test-key", http=http, sleeper=sleeper)
    _doc = "query { issues { nodes { id } pageInfo { hasNextPage endCursor } } }"
    nodes = list(client.paginate(_doc, {}, connection="issues"))

    assert [n["id"] for n in nodes] == ["i-1"]
    assert call_count[0] == 1, "empty endCursor should stop pagination after one page"


# ---------------------------------------------------------------------------
# Test 5 — Retry-After: 0 uses the default delay
# ---------------------------------------------------------------------------


def test_query_uses_default_retry_delay_when_retry_after_is_zero() -> None:
    """query() uses the default delay when the Retry-After header is '0'.

    Pins the ``value > 0`` guard in ``_parse_retry_after``: a zero value
    is not a valid delay and must fall back to the default. Without this
    test, a mutant that changes ``>`` to ``>=`` would survive — a Retry-After
    of 0 would be used directly (meaning no wait) rather than falling back.
    """
    call_count: list[int] = [0]

    def _handler(request: httpx.Request) -> httpx.Response:
        call_count[0] += 1
        if call_count[0] == 1:
            return httpx.Response(
                429,
                headers={"Retry-After": "0"},
                json={"error": "rate limited"},
            )
        return _make_gql_response({"viewer": {"id": "u-1"}})

    transport = httpx.MockTransport(_handler)
    http = httpx.Client(transport=transport)
    sleeper = _RecordingSleeper()

    client = LinearApiClient(api_key="test-key", http=http, sleeper=sleeper)
    result = client.query("query { viewer { id } }", {})

    assert result == {"viewer": {"id": "u-1"}}
    assert len(sleeper.sleeps) >= 1
    assert sleeper.sleeps[0] == LINEAR_DEFAULT_RETRY_AFTER_S, (
        f"expected default retry delay {LINEAR_DEFAULT_RETRY_AFTER_S}s when Retry-After=0, got {sleeper.sleeps[0]}s"
    )


# ---------------------------------------------------------------------------
# Test 6 — 429 exhaustion raises HTTPStatusError
# ---------------------------------------------------------------------------


def test_query_raises_after_max_retries_exhausted() -> None:
    """query() raises HTTPStatusError when all retries are 429.

    Covers the ``attempt == _MAX_RETRIES`` branch that calls
    ``response.raise_for_status()`` instead of sleeping again.
    """

    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, headers={"Retry-After": "1"}, json={"error": "throttled"})

    transport = httpx.MockTransport(_handler)
    http = httpx.Client(transport=transport)
    sleeper = _RecordingSleeper()

    client = LinearApiClient(api_key="test-key", http=http, sleeper=sleeper)
    with pytest.raises(httpx.HTTPStatusError):
        client.query("query { viewer { id } }", {})


# ---------------------------------------------------------------------------
# Test 7 — GraphQL errors key raises RuntimeError
# ---------------------------------------------------------------------------


def test_query_raises_on_graphql_errors_key() -> None:
    """query() raises RuntimeError when the response payload has an ``errors`` key."""

    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"errors": [{"message": "unknown field"}], "data": None})

    transport = httpx.MockTransport(_handler)
    http = httpx.Client(transport=transport)

    client = LinearApiClient(api_key="test-key", http=http, sleeper=_RecordingSleeper())
    with pytest.raises(RuntimeError, match="linear graphql errors"):
        client.query("query { viewer { id } }", {})


# ---------------------------------------------------------------------------
# Test 8 — Missing ``data`` key raises RuntimeError
# ---------------------------------------------------------------------------


def test_query_raises_when_data_key_missing() -> None:
    """query() raises RuntimeError when ``data`` is absent from the response."""

    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"unexpected": True})

    transport = httpx.MockTransport(_handler)
    http = httpx.Client(transport=transport)

    client = LinearApiClient(api_key="test-key", http=http, sleeper=_RecordingSleeper())
    with pytest.raises(RuntimeError, match="response missing 'data' key"):
        client.query("query { viewer { id } }", {})


# ---------------------------------------------------------------------------
# Test 9 — paginate() skips gracefully when connection value is not a dict
# ---------------------------------------------------------------------------


def test_paginate_stops_when_connection_not_a_dict() -> None:
    """paginate() returns immediately when the connection value is not a dict."""

    def _handler(request: httpx.Request) -> httpx.Response:
        return _make_gql_response({"issues": "unexpected_string"})

    transport = httpx.MockTransport(_handler)
    http = httpx.Client(transport=transport)

    client = LinearApiClient(api_key="test-key", http=http, sleeper=_RecordingSleeper())
    nodes = list(client.paginate("query { issues }", {}, connection="issues"))
    assert nodes == []


# ---------------------------------------------------------------------------
# Test 10 — paginate() stops when pageInfo is not a dict
# ---------------------------------------------------------------------------


def test_paginate_stops_when_page_info_not_a_dict() -> None:
    """paginate() returns when pageInfo is not a dict."""

    def _handler(request: httpx.Request) -> httpx.Response:
        return _make_gql_response({"issues": {"nodes": [{"id": "i-1"}], "pageInfo": "bad"}})

    transport = httpx.MockTransport(_handler)
    http = httpx.Client(transport=transport)

    client = LinearApiClient(api_key="test-key", http=http, sleeper=_RecordingSleeper())
    nodes = list(client.paginate("query { issues }", {}, connection="issues"))
    assert nodes == [{"id": "i-1"}]


# ---------------------------------------------------------------------------
# Test 11 — _parse_retry_after falls back when header is non-numeric
# ---------------------------------------------------------------------------


def test_query_uses_default_retry_delay_when_retry_after_is_non_numeric() -> None:
    """query() falls back to the default delay when Retry-After is non-numeric."""
    call_count: list[int] = [0]

    def _handler(request: httpx.Request) -> httpx.Response:
        call_count[0] += 1
        if call_count[0] == 1:
            return httpx.Response(429, headers={"Retry-After": "not-a-number"}, json={"error": "throttled"})
        return _make_gql_response({"viewer": {"id": "u-1"}})

    transport = httpx.MockTransport(_handler)
    http = httpx.Client(transport=transport)
    sleeper = _RecordingSleeper()

    client = LinearApiClient(api_key="test-key", http=http, sleeper=sleeper)
    result = client.query("query { viewer { id } }", {})

    assert result == {"viewer": {"id": "u-1"}}
    assert sleeper.sleeps[0] == LINEAR_DEFAULT_RETRY_AFTER_S

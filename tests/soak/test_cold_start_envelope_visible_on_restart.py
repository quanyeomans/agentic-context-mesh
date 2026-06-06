"""Soak: cold-start envelope shape survives a real port-bound HTTP server lifecycle.

Pins the production-captured contract from the 2026-06-06 cold-start
drill (artefacts at ``/tmp/cold_start_drill_20260606T131156Z/`` on the
production VM; documented in
``docs/operations/runbooks/cold-start-envelope-reference.md``).

The MCP middleware was shipped in #383/#406. Before this soak test the
only mechanical guard on the envelope shape was a battery of
``tests/agents/mcp/test_transport_composition.py`` unit tests using
Starlette's ``TestClient`` — that surface exercises the ASGI app
directly and does not bind a TCP port. This soak boots a real uvicorn
process in-thread, binds an ephemeral port, hits ``/mcp`` over the
loopback network, and asserts the exact envelope an MCP client would
parse off the wire. If the production envelope drifts (field renamed,
status changed, Retry-After dropped) this test fails before the next
nightly soak — agents in the field never see the regression first.

Composed via :func:`kairix.agents.mcp.transport.build_mcp_app` so the
test exercises the same composition the production CLI runs.

Wall-clock budget: well under 60s. The two assertions take ~5s wall
clock each because the test honours the production Retry-After hint
(scaled down to keep the soak fast) — the envelope is captured during
the bind-but-not-warm window then a single retry after the hint expires
confirms the warm path returns 200.
"""

from __future__ import annotations

import json
import socket
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any

import pytest

starlette = pytest.importorskip("starlette", reason="MCP transport composer is in the optional [agents] extra")
uvicorn = pytest.importorskip("uvicorn", reason="ASGI server is in the optional [agents] extra")

from starlette.applications import Starlette  # noqa: E402
from starlette.responses import PlainTextResponse, Response  # noqa: E402
from starlette.routing import Route  # noqa: E402

from kairix.agents.mcp.transport import build_mcp_app  # noqa: E402

pytestmark = pytest.mark.soak

# Production-captured Retry-After is 8 seconds — see the drill artefact at
# /tmp/cold_start_drill_20260606T131156Z/first_response_headers.txt.
# The soak test scales this down to keep wall-clock under a minute while
# still proving the round-trip wait/retry contract end-to-end.
_RETRY_AFTER_SECONDS_PROD = 8

# Tolerance for uvicorn-startup polling. The 2026-06-06 drill showed
# 6.5s from `docker compose restart` to first responsive request; this
# in-process bind is much faster (~0.5s) so 10s is a comfortable ceiling.
_BIND_POLL_TIMEOUT_S = 10.0

# F21 affordance markers required in the agent_instruction field — these
# are how an LLM agent parses the retry contract without prose-reading.
_F21_NEXT_MARKER = "next:"
_F21_FIX_MARKER = "fix:"


# ---------------------------------------------------------------------------
# Test fakes — kept inline because they only matter for the in-thread
# uvicorn boot, not for any other test surface.
# ---------------------------------------------------------------------------


class _FakeFastMCP:
    """Minimal FastMCP-shaped object the transport composer can mount.

    The real FastMCP server is not available in soak — and is not the
    surface under test. The soak proves the cold-start middleware shape,
    which sits in front of whatever transport app FastMCP returns.
    """

    def __init__(self) -> None:
        self.settings = _FakeSettings()

    def streamable_http_app(self) -> Starlette:
        async def handler(_request: Any) -> Response:
            return PlainTextResponse("streamable-ok")

        return Starlette(routes=[Route("/mcp", handler, methods=["GET", "POST"])])

    def sse_app(self, mount_path: str | None = None) -> Starlette:
        path = mount_path or "/sse"

        async def handler(_request: Any) -> Response:
            return PlainTextResponse("sse-ok")

        return Starlette(routes=[Route(path, handler, methods=["GET"])])


class _FakeSettings:
    """Stand-in for FastMCP's pydantic Settings model."""

    def __init__(self) -> None:
        self.stateless_http: bool = False
        self.json_response: bool = False


# ---------------------------------------------------------------------------
# Helpers — real port binding + uvicorn boot in a daemon thread
# ---------------------------------------------------------------------------


def _pick_ephemeral_port() -> int:
    """Bind+release pattern to pick a free localhost port atomically.

    The OS won't reuse the port for the brief window between release and
    the uvicorn bind; on slow runners the polling loop in
    ``_wait_for_bind`` catches any transient conflict.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_for_bind(host: str, port: int, *, timeout_s: float) -> None:
    """Poll until uvicorn has bound the port, or raise on timeout."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.2)
            try:
                sock.connect((host, port))
                return
            except (OSError, ConnectionRefusedError):
                time.sleep(0.05)
    raise TimeoutError(
        f"uvicorn did not bind {host}:{port} within {timeout_s:.1f}s. "
        f"next: investigate cold-start middleware or transport composer regression. "
        f"fix: check kairix/agents/mcp/transport.py build_mcp_app composition."
    )


@contextmanager
def _serve_in_thread(
    app: Starlette,
    *,
    host: str = "127.0.0.1",
    port: int,
) -> Iterator[None]:
    """Run uvicorn in a daemon thread for the lifetime of the context.

    Uses ``uvicorn.Server`` directly (not ``uvicorn.run``) so the test
    can signal shutdown via ``server.should_exit = True`` without
    sending a signal to the test process.
    """
    config = uvicorn.Config(
        app,
        host=host,
        port=port,
        log_level="warning",
        access_log=False,
        lifespan="on",
    )
    server = uvicorn.Server(config)

    def _run() -> None:
        # uvicorn.Server.run() drives its own event loop — fine inside
        # a thread because we never share the loop across threads.
        server.run()

    thread = threading.Thread(target=_run, name="kairix-soak-uvicorn", daemon=True)
    thread.start()
    try:
        _wait_for_bind(host, port, timeout_s=_BIND_POLL_TIMEOUT_S)
        yield
    finally:
        server.should_exit = True
        thread.join(timeout=5.0)


def _http_call(url: str, *, timeout_s: float = 5.0) -> tuple[int, dict[str, str], bytes]:
    """Issue a GET against ``url`` and return (status, headers, body_bytes).

    Wraps ``urllib`` rather than pulling in ``httpx`` so soak runtime
    has zero extra deps. A 503 is a normal response in this test, not
    an exception — :class:`urllib.error.HTTPError` is unwrapped to
    surface the same fields as a 200.
    """
    try:
        with urllib.request.urlopen(url, timeout=timeout_s) as response:
            return (
                int(response.status),
                {k.lower(): v for k, v in response.headers.items()},
                response.read(),
            )
    except urllib.error.HTTPError as exc:
        return (
            int(exc.code),
            {k.lower(): v for k, v in exc.headers.items()},
            exc.read(),
        )


def _build_test_app(readiness_check: Callable[[], bool]) -> Starlette:
    """Compose the same MCP app the production CLI runs.

    F47 composition: we go through :func:`build_mcp_app` so this test
    exercises the production transport composition end-to-end. The only
    test-controlled seam is the readiness callable — the same seam the
    production warm thread flips.
    """
    return build_mcp_app(
        _FakeFastMCP(),
        with_sse=False,
        readiness_check=readiness_check,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_cold_start_envelope_shape_visible_on_real_http_port() -> None:
    """Real port-bound HTTP server returns the canonical cold-start envelope.

    Asserts the production-captured envelope shape end-to-end:

      * HTTP 503 status
      * ``Retry-After`` header present, integer, >= 1
      * Body parses as JSON
      * ``error_code == "KAIRIX_COLD_START"`` (the field MCP clients pivot on)
      * ``status == "retryable_not_ready"``
      * ``error == "ColdStart"``
      * ``tool`` non-empty string (the request path the gate intercepted)
      * ``retry_after_ms`` integer >= 1000 (clients honour this)
      * ``estimated_seconds_remaining`` numeric >= 0
      * ``guidance`` non-empty string (operator-facing prose)
      * ``agent_instruction`` non-empty string carrying F21 ``next:`` + ``fix:`` markers
      * ``see_also`` non-empty list of strings

    Production reference: the 2026-06-06 drill captured this exact shape
    at /tmp/cold_start_drill_20260606T131156Z/first_response_body.json on
    the VM. The runbook at
    ``docs/operations/runbooks/cold-start-envelope-reference.md`` embeds
    the verbatim bytes.

    Sabotage-proof (executed pre-commit, mutate-fail-restore):
    deleted the ``"error_code"`` line from the
    :func:`kairix.agents.mcp.cold_start.cold_start_envelope` payload dict,
    re-ran ``pytest -m soak tests/soak/test_cold_start_envelope_visible_on_restart.py``,
    confirmed the assertion ``body["error_code"] == "KAIRIX_COLD_START"``
    failed with a KeyError-style mismatch, restored the field, re-ran,
    confirmed green.
    """
    port = _pick_ephemeral_port()
    # readiness_check returns False for the duration of this assertion —
    # the middleware short-circuits with the cold-start envelope.
    app = _build_test_app(readiness_check=lambda: False)

    with _serve_in_thread(app, port=port):
        status, headers, body_bytes = _http_call(f"http://127.0.0.1:{port}/mcp")

    # 1. HTTP status
    assert status == 503, (
        f"cold-start middleware should return 503 while readiness is False; got {status}. "
        f"fix: check ColdStartMiddleware in kairix/agents/mcp/transport.py."
    )

    # 2. Retry-After header — present, integer-parseable, >= 1
    retry_after_raw = headers.get("retry-after")
    assert retry_after_raw is not None, (
        "Retry-After header missing from 503 response. "
        "fix: ColdStartMiddleware must set Retry-After so HTTP clients without JSON parsing still retry."
    )
    retry_after_seconds = int(retry_after_raw)
    assert retry_after_seconds >= 1, (
        f"Retry-After must be >= 1 second; got {retry_after_seconds}. "
        f"next: an HTTP client honouring this header would retry immediately, defeating the back-off."
    )

    # 3. Body is JSON
    try:
        body = json.loads(body_bytes.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise AssertionError(
            f"cold-start body did not parse as UTF-8 JSON: {exc}. "
            f"raw bytes: {body_bytes!r}. "
            f"fix: ColdStartMiddleware must emit valid JSON in the response body."
        ) from exc

    # 4-12. Body shape — every field the production envelope ships with.
    assert body["error_code"] == "KAIRIX_COLD_START", (
        f"error_code must be 'KAIRIX_COLD_START' (the MCP-client pivot key); got {body.get('error_code')!r}"
    )
    assert body["status"] == "retryable_not_ready", f"status must be 'retryable_not_ready'; got {body.get('status')!r}"
    assert body["error"] == "ColdStart", f"error must be 'ColdStart'; got {body.get('error')!r}"
    assert isinstance(body.get("tool"), str) and body["tool"], (
        f"tool must be a non-empty string (the request path); got {body.get('tool')!r}"
    )
    assert isinstance(body.get("retry_after_ms"), int) and body["retry_after_ms"] >= 1000, (
        f"retry_after_ms must be int >= 1000; got {body.get('retry_after_ms')!r}"
    )
    estimated = body.get("estimated_seconds_remaining")
    assert isinstance(estimated, (int, float)) and estimated >= 0, (
        f"estimated_seconds_remaining must be numeric >= 0; got {estimated!r}"
    )
    assert isinstance(body.get("guidance"), str) and body["guidance"], (
        f"guidance must be non-empty string; got {body.get('guidance')!r}"
    )
    agent_instruction = body.get("agent_instruction")
    assert isinstance(agent_instruction, str) and agent_instruction, (
        f"agent_instruction must be non-empty string; got {agent_instruction!r}"
    )
    # F21 affordance markers — agents pivot on these to find the action steps.
    assert _F21_NEXT_MARKER in agent_instruction, (
        f"agent_instruction must contain F21 '{_F21_NEXT_MARKER}' marker; "
        f"got {agent_instruction!r}. fix: review cold_start_envelope() prose."
    )
    assert _F21_FIX_MARKER in agent_instruction, (
        f"agent_instruction must contain F21 '{_F21_FIX_MARKER}' marker; "
        f"got {agent_instruction!r}. fix: review cold_start_envelope() prose."
    )
    see_also = body.get("see_also")
    assert isinstance(see_also, list) and see_also, f"see_also must be non-empty list; got {see_also!r}"
    assert all(isinstance(item, str) and item for item in see_also), (
        f"see_also items must all be non-empty strings; got {see_also!r}"
    )


def test_cold_start_recovery_journey_wait_then_retry_succeeds() -> None:
    """Sample journey: cold-call returns 503 envelope, retry after hint succeeds.

    This is the binary-scored ``cold_start_recovery`` journey from PR 4.1
    (#423). The five steps are:

      1. Issue a tool call during cold-start  → HTTP 503 + cold envelope
      2. Parse the envelope (proves the bytes are well-formed)
      3. Wait the advertised ``retry_after_ms``
      4. Retry the same call
      5. Assert the retry succeeds with HTTP 200 from the real handler

    Binary score: every assert must pass or the journey fails. Failure
    in any step indicates the cold-start contract is broken at the
    surface MCP clients hit in production — not a partial-credit
    situation.

    Sabotage-proof (executed pre-commit): forced the readiness flip
    to always return False; step 5 failed with ``status == 503`` instead
    of 200; restored the flip-after-deadline behaviour; re-ran green.
    """
    port = _pick_ephemeral_port()

    # Readiness flips True after a short deadline. The middleware checks
    # the callable per-request, so the first call lands in the False
    # window and the retry-after-wait call lands in the True window —
    # mirroring the production warm-thread flip during container restart.
    started_at = time.monotonic()
    flip_after_seconds = 2.0

    def readiness_check() -> bool:
        return (time.monotonic() - started_at) >= flip_after_seconds

    app = _build_test_app(readiness_check=readiness_check)

    with _serve_in_thread(app, port=port):
        # Step 1 — cold call, expect 503 + envelope.
        cold_status, cold_headers, cold_body_bytes = _http_call(f"http://127.0.0.1:{port}/mcp")
        assert cold_status == 503, (
            f"step 1: cold call must return 503; got {cold_status}. "
            f"fix: readiness gate likely flipped True earlier than expected."
        )

        # Step 2 — envelope parses.
        cold_body = json.loads(cold_body_bytes.decode("utf-8"))
        assert cold_body["error_code"] == "KAIRIX_COLD_START", (
            f"step 2: envelope shape drift; error_code={cold_body.get('error_code')!r}"
        )
        retry_after_ms_int = int(cold_body["retry_after_ms"])
        retry_after_seconds = retry_after_ms_int / 1000.0
        # The Retry-After header should agree with the envelope hint
        # (within 1s rounding tolerance) — clients reading either signal
        # must observe the same back-off.
        header_retry_after = int(cold_headers["retry-after"])
        assert abs(header_retry_after - retry_after_seconds) <= 1.0, (
            f"step 2: Retry-After header ({header_retry_after}s) drifted from "
            f"envelope retry_after_ms ({retry_after_ms_int}ms = {retry_after_seconds}s). "
            f"fix: middleware must derive both from the same source."
        )

        # Step 3 — wait the advertised hint. Capped at 10s so a buggy
        # huge retry_after_ms can't hang the soak runner indefinitely.
        wait_seconds = min(retry_after_seconds, 10.0)
        time.sleep(wait_seconds)

        # Step 4 — retry the same call.
        warm_status, _warm_headers, warm_body_bytes = _http_call(f"http://127.0.0.1:{port}/mcp")

        # Step 5 — retry succeeded.
        assert warm_status == 200, (
            f"step 5: retry after waiting {wait_seconds:.1f}s must succeed with 200; "
            f"got {warm_status}. body={warm_body_bytes!r}. "
            f"fix: either readiness flip is later than advertised, or middleware is mis-gating warm requests."
        )
        # Real handler ran — body proves we passed through the middleware,
        # not the cold-start short-circuit.
        assert warm_body_bytes == b"streamable-ok", (
            f"step 5: retry returned 200 but body was {warm_body_bytes!r} "
            f"(expected 'streamable-ok' from the underlying handler). "
            f"fix: middleware may be returning 200 with a cold envelope, which would mask the regression."
        )

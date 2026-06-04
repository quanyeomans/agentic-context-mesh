"""Regression test for issue #406 — cold-start envelope reaches the client.

Issue #406 sweep showed the first MCP call after a container restart
returning the opaque ``"fetch failed"`` string instead of the structured
``KAIRIX_COLD_START`` envelope. The
:class:`kairix.agents.mcp.transport.ColdStartMiddleware` is meant to
short-circuit every non-health request with HTTP 503 +
``Retry-After`` + a JSON envelope during the warm-up window, but the
existing transport-composition tests only exercise that middleware
against a fake FastMCP shim — leaving open the question whether the
production wiring (real FastMCP ``streamable_http_app()`` + lifespan +
``add_middleware``) actually surfaces the same contract end-to-end.

This module pins the contract against the production wiring:

  - Real :class:`mcp.server.fastmcp.FastMCP` server (the production
    object the CLI passes to ``build_mcp_app``).
  - Real :class:`kairix.agents.mcp.readiness.EventReadinessGate` as
    the readiness check (matches ``cli.py`` line 369).
  - Real :func:`kairix.agents.mcp.transport.build_mcp_app` composes
    the Starlette app (matches ``cli.py`` lines 372-377).
  - A background thread that flips the gate after a fixed delay —
    mirrors the production ``_default_warm_runner`` daemon-thread
    pattern (``cli.py`` lines 178-196).
  - Starlette's ``TestClient`` drives the actual ASGI request path —
    the same path uvicorn drives in production.

Sabotage proof: removing the ``app.add_middleware(ColdStartMiddleware,
...)`` call at ``transport.py`` line 345 (or removing the
``readiness_check`` argument in the test's ``build_mcp_app`` call)
makes the during-warm assertion fall through to a 200 with the
underlying FastMCP response. Documented inline in the test docstring
and exercised manually before commit:

    1. Comment out the ``add_middleware`` call in transport.py.
    2. Re-run this file. The first assertion ``response.status_code
       == 503`` fails (gets 200 or the FastMCP transport's own
       response, NOT the structured envelope).
    3. Restore the line; test passes.
"""

from __future__ import annotations

import threading
import time
from typing import Any

import pytest

# Skip when the optional [agents] extras (mcp + starlette) aren't installed.
pytest.importorskip("starlette")
pytest.importorskip("mcp")

from starlette.testclient import TestClient

from kairix.agents.mcp.readiness import EventReadinessGate
from kairix.agents.mcp.transport import build_mcp_app

pytestmark = pytest.mark.integration


def _make_real_fastmcp_server() -> Any:
    """Construct the production FastMCP server with the streamable HTTP
    transport configured the way ``cli.py`` configures it.

    Returned object is the same shape ``build_mcp_app`` accepts in
    production — no fakes, no stubs.
    """
    from mcp.server.fastmcp import FastMCP

    server = FastMCP("kairix-cold-start-test", host="127.0.0.1", port=18691)
    # Match cli.py's ``_apply_settings`` behaviour so the streamable
    # transport is in stateless + json-response mode (the deployment
    # config).
    server.settings.stateless_http = True
    server.settings.json_response = True
    return server


def _spawn_warm_thread_flipping_gate_after(gate: EventReadinessGate, delay_s: float) -> threading.Thread:
    """Mirror the production warm-runner: a daemon thread that flips
    the readiness gate after ``delay_s`` seconds.

    Production ``_default_warm_runner`` (``cli.py`` line 178-196) does
    exactly this with ``warm_retrieval_stack`` as the body. The test
    substitutes a fixed sleep so the warm window has a known duration.
    """

    def _warm_body() -> None:
        time.sleep(delay_s)
        gate.mark_ready()

    thread = threading.Thread(target=_warm_body, daemon=True, name="kairix-mcp-warm-test")
    thread.start()
    return thread


@pytest.mark.integration
def test_first_mcp_request_during_warm_returns_503_envelope_not_fetch_failed() -> None:
    """During the warm window, the first ``POST /mcp`` returns HTTP 503
    with the canonical ColdStart envelope — never an opaque transport
    error.

    Pins issue #406: a freshly-restarted container must surface the
    structured affordance to the agent client, not a generic "fetch
    failed" string. Wiring covered:

      - FastMCP ``streamable_http_app()`` lifespan + ``/mcp`` route.
      - ``build_mcp_app`` composing the outer Starlette + middleware.
      - ``ColdStartMiddleware`` short-circuiting non-health requests.
      - Real ``EventReadinessGate`` flipping mid-test from a daemon
        thread (production warm-runner shape).

    Envelope shape pinned (matches ``cold_start.py`` +
    ``_build_cold_start_body``):

      - HTTP status 503.
      - ``Retry-After`` header is a positive integer seconds value.
      - JSON body has ``error == "ColdStart"`` and
        ``error_code == "KAIRIX_COLD_START"``.
      - ``retry_after_ms`` is a positive integer.
      - ``estimated_seconds_remaining`` is a positive number.
      - ``guidance`` and ``agent_instruction`` carry the F21
        affordance markers (``next:`` and ``fix:``).
    """
    server = _make_real_fastmcp_server()
    gate = EventReadinessGate()
    app = build_mcp_app(server, with_sse=False, readiness_check=gate.is_ready)

    # Spawn a warm thread that flips the gate after 5s — long enough that
    # the first request below lands before it. Mirrors production where
    # ``warm_retrieval_stack`` runs in the background.
    warm_thread = _spawn_warm_thread_flipping_gate_after(gate, delay_s=5.0)

    try:
        with TestClient(app) as client:
            assert not gate.is_ready(), "gate must be cold for the first assertion to be meaningful"

            response = client.post(
                "/mcp",
                json={"jsonrpc": "2.0", "method": "tools/list", "id": 1},
            )

            assert response.status_code == 503, (
                f"first MCP call during warm must return 503; got {response.status_code}. "
                f"body={response.text!r}. If status is 200 the middleware is bypassed; "
                f"if connection refused, uvicorn never bound the port. "
                f"fix: check ColdStartMiddleware mount in build_mcp_app."
            )
            retry_after_header = response.headers.get("Retry-After")
            assert retry_after_header is not None
            assert int(retry_after_header) > 0, f"Retry-After must be a positive int; got {retry_after_header!r}"

            body = response.json()
            assert body["error"] == "ColdStart", f"envelope error must be 'ColdStart'; got {body.get('error')!r}"
            assert body["error_code"] == "KAIRIX_COLD_START"
            assert body["status"] == "retryable_not_ready"

            retry_after_ms = body["retry_after_ms"]
            assert isinstance(retry_after_ms, int) and retry_after_ms > 0, (
                f"retry_after_ms must be a positive int; got {retry_after_ms!r}"
            )
            estimated = body["estimated_seconds_remaining"]
            assert isinstance(estimated, (int, float)) and estimated > 0

            # F21 affordance markers — agent reading this envelope needs a
            # positive ``next:`` action and a ``fix:`` fallback.
            assert "next:" in body["guidance"]
            assert "next:" in body["agent_instruction"]
            assert "fix:" in body["agent_instruction"]
    finally:
        # Daemon thread will die with the process, but join briefly so
        # the gate-flip log line lands before pytest tears down — keeps
        # log output deterministic for the next test in the suite.
        warm_thread.join(timeout=6.0)


@pytest.mark.integration
def test_request_after_warm_completes_no_longer_returns_503() -> None:
    """Once the readiness gate flips, the middleware lets requests through
    and the underlying FastMCP route serves them (no more 503).

    Sabotage-proof: keep the middleware always-503 (remove the
    ``self._readiness_check()`` check in transport.py) and this test
    fails because the 503 keeps coming back after warm.
    """
    server = _make_real_fastmcp_server()
    gate = EventReadinessGate()
    app = build_mcp_app(server, with_sse=False, readiness_check=gate.is_ready)

    # Flip ready immediately — the request below should pass the middleware.
    gate.mark_ready()

    with TestClient(app) as client:
        assert gate.is_ready()
        response = client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "method": "tools/list", "id": 1},
        )
        # Anything other than 503 means the middleware did NOT short-circuit;
        # the actual status depends on FastMCP's session-handling for the
        # ``tools/list`` JSON-RPC call (it typically wants a session header
        # for stateful mode, returns 200 with content in stateless+json mode).
        assert response.status_code != 503, (
            f"once gate is ready, middleware must not return 503; got {response.status_code}, body={response.text!r}"
        )


@pytest.mark.integration
def test_healthz_during_warm_bypasses_cold_start_middleware() -> None:
    """``/healthz`` must always respond — it is how operators and load
    balancers detect readiness. The middleware must never gate it,
    regardless of warm state.

    Pins the bypass behaviour in ``transport.py``'s
    ``_HEALTH_PATH_PREFIXES``.
    """
    server = _make_real_fastmcp_server()
    gate = EventReadinessGate()  # default not-ready
    app = build_mcp_app(server, with_sse=False, readiness_check=gate.is_ready)

    with TestClient(app) as client:
        assert not gate.is_ready()
        response = client.get("/healthz")

        assert response.status_code == 200
        body = response.json()
        assert body["ready"] is False
        assert "Retry-After" not in response.headers

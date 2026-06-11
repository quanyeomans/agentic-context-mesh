"""
kairix.agents.mcp.transport — ASGI transport composer for the kairix MCP server.

This is the only module in the codebase that knows how FastMCP's transport
apps (streamable HTTP, SSE) are mounted. CLI entry points and tests construct
the Starlette app via :func:`build_mcp_app`.

Sprint 19 motivation
--------------------
Pre-Sprint 19 the MCP server only exposed an SSE transport on ``/sse``. The
2026-05-02 dogfood failure (every ``mcp-kairix__*`` tool returning
``-32602 Invalid request parameters``) traced back to the gateway dropping
idle SSE connections. Streamable HTTP turns every tool call into a normal
HTTP request/response, removing the long-lived-connection failure mode by
construction. We mount the streamable transport at ``/mcp`` and keep
``/sse`` mounted for back-compat with older clients.

Design
------
- Public surface is a single function, :func:`build_mcp_app`.
- Helpers are ``_``-prefixed and treated as private implementation detail.
- The composer never starts a server; it only returns a Starlette app.
- ``starlette`` is a transitive dependency of ``mcp>=1.20`` (declared in the
  ``agents`` extra in ``pyproject.toml``).
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.types import ASGIApp, Receive, Scope, Send

if TYPE_CHECKING:
    from kairix.platform.setup.service import SetupService
    from kairix.secrets.loader import SecretsResolver

logger = logging.getLogger("kairix.mcp.transport")

# Default Retry-After header value (seconds) returned by the cold-start
# middleware when the readiness gate is False. Matches the existing
# application-layer cold-start envelope's retry hint.
_RETRY_AFTER_SECONDS = 8

# Paths that bypass the cold-start middleware — these probes are how
# operators and load balancers detect readiness in the first place, so
# they must always respond regardless of warm state.
_HEALTH_PATH_PREFIXES = ("/healthz",)

# The setup wizard's mount prefix also bypasses the cold-start gate when
# the wizard is enabled: the wizard exists precisely for first-boot
# operators, who would otherwise stare at 503s while the retrieval stack
# warms behind them.
_SETUP_PATH_PREFIX = "/setup"

# Module-level start timestamp captured on first build_mcp_app() call.
# This is *implementation* of the public function — not exposed elsewhere.
_started_at: float | None = None


def _ensure_started_at() -> float:
    """Return the process start time, capturing it on first call."""
    global _started_at
    if _started_at is None:
        _started_at = time.monotonic()
    return _started_at


def _make_healthz_route(
    path: str,
    readiness_check: Callable[[], bool] | None,
) -> Route:
    """Basic liveness probe: ``{"ready": bool, "uptime_s": int}``.

    ``readiness_check`` (when provided) is the legacy boolean signal for
    whether kairix has finished cold-starting. It is intentionally
    coarse — for layered checks (secrets, vector search, BM25) use
    ``/healthz/ready``.
    """
    started_at = _ensure_started_at()

    async def healthz(_request: Request) -> JSONResponse:  # NOSONAR S7503 — Starlette ASGI contract
        ready = bool(readiness_check()) if readiness_check is not None else True
        uptime_s = int(time.monotonic() - started_at)
        return JSONResponse({"ready": ready, "uptime_s": uptime_s})

    return Route(path, healthz, methods=["GET"])


def _make_ready_route(
    path: str,
    capability_probe: Callable[[], dict[str, Any]] | None,
) -> Route:
    """Layered readiness probe: ``{"live": true, "ready": bool, "checks": {...}}``.

    Resolves the gap from #167 where ``/healthz`` reported ``ready=true``
    while vector search was non-functional due to missing secrets. The
    probe runs ``capability_probe()`` (if provided) and surfaces the
    structured result. A probe that lists ``secrets_loaded=False`` or
    ``vector_search_capable=False`` is the actionable signal an
    operator needs to triage a degraded deployment.

    Response shape (when ``capability_probe`` is wired):

    .. code-block:: json

        {
          "live": true,
          "ready": false,
          "uptime_s": 14,
          "checks": {
            "secrets_loaded": false,
            "vector_search_capable": false,
            "bm25_search_capable": true,
            "detail": {
              "secrets_loaded": "KAIRIX_LLM_API_KEY missing",
              "vector_search_capable": "embed credentials unavailable"
            }
          }
        }

    HTTP status is always 200 (load-balancer probes should treat this as
    a JSON-content health check, not as an HTTP gate). The ``ready``
    field is the boolean to act on.

    When ``capability_probe`` is None the probe degrades to the same
    semantics as ``/healthz`` so this endpoint is always wired and an
    operator never gets a 404.
    """
    started_at = _ensure_started_at()

    async def healthz_ready(_request: Request) -> JSONResponse:  # NOSONAR S7503 — Starlette ASGI contract
        uptime_s = int(time.monotonic() - started_at)
        if capability_probe is None:
            return JSONResponse({"live": True, "ready": True, "uptime_s": uptime_s, "checks": {}})
        try:
            checks = capability_probe()
        # Probe authors are encouraged to handle their own exceptions and
        # report them in ``detail``. This guard is defensive: if the probe
        # itself raises, we surface that as a structured failure rather
        # than crashing the request.
        except Exception as exc:
            return JSONResponse(
                {
                    "live": True,
                    "ready": False,
                    "uptime_s": uptime_s,
                    "checks": {"probe_error": str(exc)},
                }
            )
        ready = bool(checks.get("ready", _derive_ready_from_checks(checks)))
        return JSONResponse(
            {
                "live": True,
                "ready": ready,
                "uptime_s": uptime_s,
                "checks": checks,
            }
        )

    return Route(path, healthz_ready, methods=["GET"])


def _derive_ready_from_checks(checks: dict[str, Any]) -> bool:
    """Default readiness: ALL boolean keys named ``*_capable`` /
    ``*_loaded`` must be True. Lets callers omit a top-level ``ready``
    field and have it derived from the granular checks.
    """
    relevant = [
        v for k, v in checks.items() if isinstance(v, bool) and (k.endswith("_capable") or k.endswith("_loaded"))
    ]
    return all(relevant) if relevant else True


def _build_cold_start_body(path: str) -> dict[str, Any]:
    """Cold-start envelope returned as the 503 response body.

    Matches the application-layer envelope shape from
    :func:`kairix.agents.mcp.cold_start.cold_start_envelope` so an MCP
    client that already knows how to parse the in-tool envelope sees the
    same structure here. The ``tool`` field is the request path because
    the transport layer fires before the MCP router has resolved which
    tool the client was reaching.

    Delegates to :func:`kairix.agents.mcp.cold_start.cold_start_envelope`
    so the live WarmProgress (#390) flows into the transport 503 body —
    the surface agents actually hit during warm — not just the in-tool
    envelope. When WarmProgress is unset (warm not started), the static
    8s fallback preserves the historical Retry-After contract.
    """
    from kairix.agents.mcp.cold_start import cold_start_envelope

    return cold_start_envelope(
        tool_name=path,
        retry_after_ms=_RETRY_AFTER_SECONDS * 1000,
        estimated_seconds_remaining=float(_RETRY_AFTER_SECONDS),
    )


class ColdStartMiddleware:
    """ASGI middleware that returns HTTP 503 + Retry-After while not ready.

    Fixes the gap from KFEAT-020: MCP clients see ``fetch_failed`` (a
    transport-level fault) during the window between the uvicorn port
    binding and the application-layer readiness gate flipping True. Before
    this middleware, requests that landed in that window either crashed
    inside the not-yet-mounted MCP router or got opaque 500s; after this
    middleware, every non-health request returns a structured 503 with
    ``Retry-After: N`` so well-behaved HTTP clients (including the MCP
    TypeScript SDK) retry rather than dismiss kairix as broken.

    Health probes (``/healthz`` and ``/healthz/ready``) bypass the gate so
    operators and load balancers always get an answer.

    ``readiness_check`` is the same callable wired into ``/healthz`` and
    the tool-level cold-start envelope, so all three layers agree on
    ready/not-ready.
    """

    def __init__(
        self,
        app: ASGIApp,
        readiness_check: Callable[[], bool],
        bypass_path_prefixes: tuple[str, ...] = _HEALTH_PATH_PREFIXES,
    ) -> None:
        self._app = app
        self._readiness_check = readiness_check
        self._bypass_path_prefixes = bypass_path_prefixes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope.get("type") != "http":
            await self._app(scope, receive, send)
            return
        path = scope.get("path", "")
        if any(path.startswith(prefix) for prefix in self._bypass_path_prefixes):
            await self._app(scope, receive, send)
            return
        if self._readiness_check():
            await self._app(scope, receive, send)
            return
        body = _build_cold_start_body(path)
        # #390 — Retry-After mirrors the envelope's live retry hint so HTTP
        # clients that retry-after the header see the same back-off as agents
        # that read the JSON body.
        retry_after_seconds = max(1, int(body.get("retry_after_ms", _RETRY_AFTER_SECONDS * 1000) / 1000))
        # Issue #320 observability — log every cold-start short-circuit with
        # process uptime so operators can correlate post-incident with
        # "did the middleware run?" vs "did the agent's client time out before
        # we could respond?".
        uptime_s = int(time.monotonic() - _ensure_started_at())
        logger.info(
            "cold_start_middleware_returning_503 path=%s uptime_s=%d retry_after_s=%d",
            path,
            uptime_s,
            retry_after_seconds,
        )
        response = JSONResponse(
            body,
            status_code=503,
            headers={"Retry-After": str(retry_after_seconds)},
        )
        await response(scope, receive, send)


def _default_setup_wizard_enabled() -> bool:
    """Default reader for the ``setup_wizard_web`` feature flag.

    Lazy-imported so MCP transport composition doesn't pay the feature
    flag resolver import cost when the wizard stays OFF (the default).
    """
    from kairix.core.features import flag

    return flag("setup_wizard_web")


def _default_setup_service_factory() -> SetupService:
    """Production default for the wizard's service seam.

    Lazy so the wizard backend module is only imported when the wizard
    flag is ON and the first ``/setup`` request arrives. While the
    backend is the NotImplementedError stub, that first request gets
    the stub's structured fix:/next: message instead of a half-working
    wizard.
    """
    from kairix.platform.setup.service import build_setup_service

    return build_setup_service()


def _apply_settings(server: Any) -> None:
    """Set stateless_http and json_response on server.settings if present.

    Defensive: FastMCP exposes ``settings`` as a Pydantic model in mcp>=1.20,
    but we don't want to crash if a future version reshapes the API.
    """
    settings = getattr(server, "settings", None)
    if settings is None:
        return
    try:
        settings.stateless_http = True
        settings.json_response = True
    except (AttributeError, TypeError):  # pragma: no cover — defensive
        return


def build_mcp_app(
    server: Any,
    *,
    with_sse: bool = True,
    sse_mount_path: str = "/sse",
    healthz_path: str = "/healthz",
    healthz_ready_path: str = "/healthz/ready",
    readiness_check: Callable[[], bool] | None = None,
    capability_probe: Callable[[], dict[str, Any]] | None = None,
    setup_service_factory: Callable[[], SetupService] = _default_setup_service_factory,
    setup_secrets: SecretsResolver | None = None,
    setup_wizard_enabled: Callable[[], bool] = _default_setup_wizard_enabled,
) -> Starlette:
    """Compose the kairix MCP ASGI app.

    - Mounts the streamable HTTP transport at ``/mcp`` (FastMCP default).
    - If ``with_sse``, also mounts the legacy SSE transport at
      ``sse_mount_path`` for back-compat.
    - Adds two health endpoints:
        - ``/healthz`` — basic liveness, ``{"ready": bool, "uptime_s": int}``.
          Back-compat with the existing endpoint.
        - ``/healthz/ready`` — layered readiness, runs
          ``capability_probe()`` and reports per-capability detail.
          Resolves the #167 gap where ``/healthz`` returned
          ``ready=true`` while vector search was broken.
    - When the ``setup_wizard_web`` feature flag is ON, also mounts the
      in-box web setup wizard at ``/setup`` (same container, same
      port). When OFF — the default — no ``/setup`` routes exist and
      requests there 404 exactly as before this flag landed.

    The composer is the only place in the codebase that knows about
    FastMCP's transport apps. CLI entry points construct via this function;
    tests construct via this function with a fake server and optional
    probe callbacks.

    Args:
        server: FastMCP instance. Typed loosely because the ``mcp`` package
            does not publish public stubs for these methods.
        with_sse: If True, mount the legacy SSE transport.
        sse_mount_path: Mount path passed to ``server.sse_app``.
        healthz_path: Path for the basic liveness probe route.
        healthz_ready_path: Path for the layered readiness probe route.
        readiness_check: Optional callable used to populate the ``ready``
            field of the basic ``/healthz`` JSON body. Called on every
            request.
        capability_probe: Optional callable returning a dict of granular
            capability checks (``secrets_loaded``,
            ``vector_search_capable``, ``bm25_search_capable``, plus a
            ``detail`` map). Wired into ``/healthz/ready``.
        setup_service_factory: Callable returning the
            :class:`SetupService` the wizard renders against. Tests pass
            ``lambda: FakeSetupService(...)``; the production default is
            the lazy ``kairix.platform.setup.service.build_setup_service``.
        setup_secrets: Optional secrets resolver for the wizard's
            operator-token guard; defaults to the production loader.
        setup_wizard_enabled: Reader for the wizard flag — tests pass
            ``lambda: resolver.get("setup_wizard_web")`` with a
            ``FakeFeatureFlagResolver``; the production default reads
            the ``setup_wizard_web`` registry flag.

    Returns:
        A composed :class:`starlette.applications.Starlette` instance with
        the streamable HTTP routes, optionally the SSE routes, and the
        liveness + readiness probe routes.
    """
    _apply_settings(server)

    streamable_app: Starlette = server.streamable_http_app()
    routes: list[Any] = list(streamable_app.routes)

    if with_sse:
        sse_app: Starlette = server.sse_app(mount_path=sse_mount_path)
        routes.extend(sse_app.routes)

    routes.append(_make_healthz_route(healthz_path, readiness_check))
    routes.append(_make_ready_route(healthz_ready_path, capability_probe))

    # Flag-gated setup wizard (F52 — the default reader resolves the
    # registry's ``setup_wizard_web`` entry). OFF means the mount is
    # never appended: ``/setup/*`` 404s from Starlette's default, so
    # merging the wizard is structurally a no-op for operators.
    wizard_on = bool(setup_wizard_enabled())
    if wizard_on:
        from kairix.platform.setup.web.routes import build_setup_wizard_mount

        routes.append(
            build_setup_wizard_mount(
                service_factory=setup_service_factory,
                secrets=setup_secrets,
            )
        )

    # Preserve the streamable app's lifespan so FastMCP's session manager
    # starts/stops correctly when the composed app is served.
    lifespan = getattr(streamable_app.router, "lifespan_context", None)
    if lifespan is not None:
        app = Starlette(routes=routes, lifespan=lifespan)
    else:
        app = Starlette(routes=routes)

    # Cold-start gate (KFEAT-020): when a readiness check is wired, every
    # non-health request returns HTTP 503 + Retry-After until ready, so MCP
    # clients see a retryable status instead of fetch_failed at the
    # transport layer. The setup wizard (when mounted) bypasses the gate —
    # it exists for first-boot operators who arrive before warm completes.
    if readiness_check is not None:
        bypass = _HEALTH_PATH_PREFIXES + ((_SETUP_PATH_PREFIX,) if wizard_on else ())
        app.add_middleware(
            ColdStartMiddleware,
            readiness_check=readiness_check,
            bypass_path_prefixes=bypass,
        )
    return app


__all__ = ["ColdStartMiddleware", "build_mcp_app"]

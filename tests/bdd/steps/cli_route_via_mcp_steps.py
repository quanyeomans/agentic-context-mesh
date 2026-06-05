"""Step definitions for cli_route_via_mcp.feature (#411).

Drives :func:`kairix.agents.mcp.client_dispatcher.try_dispatch_via_mcp`
through the public ``DispatcherDeps`` injection seam (F46/F47-clean —
the BDD step impl composes via the public dispatcher entrypoint, not
by reaching into module internals).
"""

from __future__ import annotations

import io
import shlex
from contextlib import redirect_stdout
from dataclasses import dataclass, field
from typing import Any

import pytest
from pytest_bdd import given, parsers, then, when

from kairix.agents.mcp.client_dispatcher import DispatcherDeps, try_dispatch_via_mcp
from tests.fakes import FakeMcpDispatchClient


@dataclass
class _RouteCtx:
    client: FakeMcpDispatchClient | None = None
    routing_enabled: bool = True
    exit_code: int | None = None
    stdout: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


@pytest.fixture
def route_ctx() -> _RouteCtx:
    return _RouteCtx()


@given("the CLI dispatcher is configured to inject a fake MCP client")
def _given_dispatcher_with_fake(route_ctx: _RouteCtx) -> None:
    # Defer construction — the responsive flag is set by subsequent Givens.
    route_ctx.extra["envelope"] = {"default": "envelope"}


@given("the fake MCP server is responsive")
def _given_responsive(route_ctx: _RouteCtx) -> None:
    route_ctx.client = FakeMcpDispatchClient(
        responsive=True,
        envelope=route_ctx.extra.get("envelope", {}),
    )


@given("the fake MCP server is not responsive")
def _given_not_responsive(route_ctx: _RouteCtx) -> None:
    route_ctx.client = FakeMcpDispatchClient(responsive=False)


@given(parsers.parse('the fake MCP envelope contains "{token}"'))
def _given_envelope_contains(route_ctx: _RouteCtx, token: str) -> None:
    envelope = {"hit": token}
    route_ctx.extra["envelope"] = envelope
    # If client already constructed, rebuild with the new envelope.
    if route_ctx.client is not None:
        route_ctx.client = FakeMcpDispatchClient(responsive=True, envelope=envelope)


@given("CLI-to-MCP routing is disabled")
def _given_routing_disabled(route_ctx: _RouteCtx) -> None:
    route_ctx.routing_enabled = False


@when(parsers.parse('the operator dispatches "{subcommand}" with argv "{argv}"'))
def _when_dispatched(route_ctx: _RouteCtx, subcommand: str, argv: str) -> None:
    assert route_ctx.client is not None, "no fake client wired — step ordering bug"
    deps = DispatcherDeps(
        client=route_ctx.client,
        endpoint_fn=lambda: "http://localhost:8080/mcp",
        routing_enabled_fn=lambda: route_ctx.routing_enabled,
    )
    buf = io.StringIO()
    with redirect_stdout(buf):
        route_ctx.exit_code = try_dispatch_via_mcp(subcommand, shlex.split(argv), deps=deps)
    route_ctx.stdout = buf.getvalue()


@then(parsers.parse("the dispatcher exits with code {code:d}"))
def _then_exit_code(route_ctx: _RouteCtx, code: int) -> None:
    assert route_ctx.exit_code == code, (
        f"expected exit code {code}, got {route_ctx.exit_code!r}; stdout={route_ctx.stdout[:200]!r}"
    )


@then("the dispatcher returns no exit code")
def _then_no_exit_code(route_ctx: _RouteCtx) -> None:
    assert route_ctx.exit_code is None, (
        f"expected None (fall-through), got {route_ctx.exit_code!r}; stdout={route_ctx.stdout[:200]!r}"
    )


@then(parsers.parse('the rendered output contains "{token}"'))
def _then_output_contains(route_ctx: _RouteCtx, token: str) -> None:
    assert token in route_ctx.stdout, f"missing {token!r} in stdout:\n{route_ctx.stdout!r}"


@then(parsers.parse('the fake MCP server recorded a tool call to "{tool_name}"'))
def _then_tool_call_recorded(route_ctx: _RouteCtx, tool_name: str) -> None:
    assert route_ctx.client is not None
    recorded = [call[0] for call in route_ctx.client.calls]
    assert tool_name in recorded, f"expected call to {tool_name!r}; saw {recorded!r}"


@then("the fake MCP server recorded no tool call")
def _then_no_tool_call(route_ctx: _RouteCtx) -> None:
    assert route_ctx.client is not None
    assert route_ctx.client.calls == [], f"expected no tool calls; saw {route_ctx.client.calls!r}"


@then("the fake MCP server recorded no readiness probe")
def _then_no_readiness_probe(route_ctx: _RouteCtx) -> None:
    assert route_ctx.client is not None
    assert route_ctx.client.responsive_calls == [], (
        f"expected no probe calls; saw {route_ctx.client.responsive_calls!r}"
    )

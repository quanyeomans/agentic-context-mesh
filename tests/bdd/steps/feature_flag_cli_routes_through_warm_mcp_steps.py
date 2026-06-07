"""Step definitions for ``cli_routes_through_warm_mcp.feature`` (PR 2.8 / #421).

Both-branch flag scenarios — F54 coverage for the new
``cli_routes_through_warm_mcp`` flag.

Composition rule (F46): steps drive through the canonical dispatcher
``try_dispatch_via_mcp`` with a ``DispatcherDeps`` whose
``text_mode_flag_reader`` is wired through :class:`FakeFeatureFlagResolver`.
No ``@patch`` / no monkeypatch on kairix internals.
"""

from __future__ import annotations

import io
from contextlib import redirect_stdout
from dataclasses import dataclass

import pytest
from pytest_bdd import given, parsers, scenarios, then, when

from kairix.agents.mcp.client_dispatcher import (
    DispatcherDeps,
    ensure_composers_loaded,
    try_dispatch_via_mcp,
)
from kairix.agents.mcp.text_mode_composers import (
    TextModeComposer,
    register_composer,
)
from tests.fakes import FakeFeatureFlagResolver, FakeMcpDispatchClient

pytestmark = pytest.mark.bdd

# Pre-load the canonical composer wiring so the per-scenario sentinel
# registration wins.
ensure_composers_loaded()


_BDD_SUBCOMMAND = "search"
_BDD_RENDERED_MARKER = "bdd-warm-mcp-rendered"


@pytest.fixture(autouse=True)
def _restore_canonical_search_composer():
    """Restore the canonical ``search`` composer after each scenario."""
    from kairix.agents.mcp.text_mode_composers import (
        get_composer,
        register_composer,
    )

    saved = get_composer(_BDD_SUBCOMMAND)
    yield
    if saved is not None:
        register_composer(_BDD_SUBCOMMAND, saved)


scenarios("../features/feature_flag_cli_routes_through_warm_mcp.feature")


@dataclass
class _WarmMcpCtx:
    resolver: FakeFeatureFlagResolver | None = None
    client: FakeMcpDispatchClient | None = None
    exit_code: int | None = -999  # sentinel so we can tell "not called" from None
    stdout: str = ""


@pytest.fixture
def warm_mcp_ctx() -> _WarmMcpCtx:
    return _WarmMcpCtx()


@given(parsers.parse("the operator has the warm-mcp text-mode flag set to {value}"))
def _set_flag(warm_mcp_ctx: _WarmMcpCtx, value: str) -> None:
    parsed = value.strip().lower() == "true"
    warm_mcp_ctx.resolver = FakeFeatureFlagResolver().with_flag("cli_routes_through_warm_mcp", parsed)


@when("the operator runs a text-mode subcommand with a registered composer")
def _run_text_mode(warm_mcp_ctx: _WarmMcpCtx) -> None:
    register_composer(
        _BDD_SUBCOMMAND,
        TextModeComposer(
            from_envelope=lambda env: dict(env),
            format_text=lambda result, argv: f"{_BDD_RENDERED_MARKER}::{sorted(result.keys())}",
            name=_BDD_SUBCOMMAND,
        ),
    )
    warm_mcp_ctx.client = FakeMcpDispatchClient(
        responsive=True,
        envelope={"query": "needle", "results": []},
    )
    resolver = warm_mcp_ctx.resolver
    assert resolver is not None, "Given step must run before When"
    deps = DispatcherDeps(
        client=warm_mcp_ctx.client,
        endpoint_fn=lambda: "http://localhost:8080/mcp",
        routing_enabled_fn=lambda: True,
        text_mode_flag_reader=lambda: resolver.get("cli_routes_through_warm_mcp"),
    )
    out_buf = io.StringIO()
    with redirect_stdout(out_buf):
        warm_mcp_ctx.exit_code = try_dispatch_via_mcp(_BDD_SUBCOMMAND, ["needle"], deps=deps)
    warm_mcp_ctx.stdout = out_buf.getvalue()


@then("the warm MCP tool call is not made")
def _no_tool_call(warm_mcp_ctx: _WarmMcpCtx) -> None:
    client = warm_mcp_ctx.client
    assert client is not None
    assert client.calls == [], f"expected no MCP call when flag OFF; got {client.calls!r}"


@then("the warm MCP tool call is made")
def _tool_call_was_made(warm_mcp_ctx: _WarmMcpCtx) -> None:
    client = warm_mcp_ctx.client
    assert client is not None
    assert client.calls, "expected at least one MCP call when flag ON"


@then("the dispatcher returns none")
def _dispatcher_returns_none(warm_mcp_ctx: _WarmMcpCtx) -> None:
    assert warm_mcp_ctx.exit_code is None, (
        f"expected dispatcher to return None on flag OFF; got {warm_mcp_ctx.exit_code!r}"
    )


@then("the dispatcher renders the envelope as text")
def _renders_as_text(warm_mcp_ctx: _WarmMcpCtx) -> None:
    assert _BDD_RENDERED_MARKER in warm_mcp_ctx.stdout, (
        f"expected rendered marker {_BDD_RENDERED_MARKER!r} in stdout; got {warm_mcp_ctx.stdout!r}"
    )

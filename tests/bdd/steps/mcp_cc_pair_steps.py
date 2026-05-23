"""Step definitions for mcp_cc_pair.feature (Wave D MCP escalation surface).

F1-clean: no @patch on kairix internals.
F46: BDD step impls invoke the MCP tool handler directly per the
test-discipline spec ("MCP tools test by direct handler call with
deps=... injected").
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest
from pytest_bdd import given, parsers, then, when

from kairix.agents.mcp.server import tool_cc_pair

pytestmark = pytest.mark.bdd


@dataclass
class _Ctx:
    """Per-scenario state."""

    server_built: bool = False
    envelope: dict[str, Any] = field(default_factory=dict)


@pytest.fixture
def mcp_cc_pair_ctx() -> _Ctx:
    return _Ctx()


@given("the MCP server is constructed")
def _server_constructed(mcp_cc_pair_ctx: _Ctx) -> None:
    """Mark the server as constructed for the scenario.

    The actual MCP server construction is exercised in
    ``tests/contracts/`` — here we just confirm the tool handler is
    importable + callable directly, which is what F30 + the
    test-discipline spec call for ("MCP tools test by direct handler
    call").
    """
    mcp_cc_pair_ctx.server_built = True


@when(parsers.parse('the agent calls tool_cc_pair with verb "{verb}"'))
def _call_tool_cc_pair(mcp_cc_pair_ctx: _Ctx, verb: str) -> None:
    assert mcp_cc_pair_ctx.server_built, "Given step must run before When"
    mcp_cc_pair_ctx.envelope = tool_cc_pair(verb=verb)


@then(parsers.parse('the MCP cc_pair envelope contains capability "{expected}"'))
def _envelope_capability(mcp_cc_pair_ctx: _Ctx, expected: str) -> None:
    assert mcp_cc_pair_ctx.envelope.get("capability") == expected, (
        f"expected capability={expected!r}; got {mcp_cc_pair_ctx.envelope.get('capability')!r}"
    )


@then(parsers.parse('the MCP cc_pair envelope contains operator_command "{needle}"'))
def _envelope_command(mcp_cc_pair_ctx: _Ctx, needle: str) -> None:
    command = mcp_cc_pair_ctx.envelope.get("operator_command", "")
    assert needle in command, f"expected {needle!r} in operator_command; got {command!r}"


@then(parsers.parse('the MCP cc_pair envelope contains reason "{needle}"'))
def _envelope_reason(mcp_cc_pair_ctx: _Ctx, needle: str) -> None:
    reason = mcp_cc_pair_ctx.envelope.get("reason", "")
    assert needle in reason, f"expected {needle!r} in reason; got {reason!r}"

"""Step definitions for mcp_features_status.feature.

Drives the ``tool_features_status`` MCP tool by direct handler call —
no FastMCP server spin-up, no subprocess. The same shape the F30
outcome test asserts on, so the BDD scenarios and the outcome test
share a single contract for the envelope.

F1-clean: direct call, no @patch. F4-clean: no env-var I/O.
F46-compliant: step impls invoke the MCP tool function directly.
"""

from __future__ import annotations

from typing import Any

import pytest
from pytest_bdd import given, then, when

from kairix.agents.mcp.server import tool_features_status

pytestmark = pytest.mark.bdd


@pytest.fixture
def _mcp_features_state() -> dict[str, Any]:
    """Per-scenario fresh state container."""
    return {"envelope": None}


@given("the kairix features registry has no entries declared")
def _registry_has_no_entries_mcp() -> None:
    """No-op — registry is empty at PR-2 landing.

    The CLI scenario uses a slightly different phrase
    (``... is empty``) so the two step packs can co-exist without
    pytest-bdd ambiguity. Both phrases land on the same registry state.
    """


@when("the agent calls the tool_features_status MCP tool")
def _agent_calls_tool(_mcp_features_state: dict[str, Any]) -> None:
    _mcp_features_state["envelope"] = tool_features_status()


@then("the tool_features_status envelope carries an empty flags list")
def _envelope_carries_empty_flags(_mcp_features_state: dict[str, Any]) -> None:
    envelope = _mcp_features_state["envelope"]
    assert envelope["flags"] == [], f"expected empty flags list (registry is empty); got: {envelope['flags']!r}"


@then("the tool_features_status envelope has an empty error string")
def _envelope_has_empty_error(_mcp_features_state: dict[str, Any]) -> None:
    envelope = _mcp_features_state["envelope"]
    assert envelope["error"] == "", f"expected empty error string; got: {envelope['error']!r}"


@then("the tool_features_status envelope has a flags key")
def _envelope_has_flags_key(_mcp_features_state: dict[str, Any]) -> None:
    envelope = _mcp_features_state["envelope"]
    assert "flags" in envelope, f"expected 'flags' key; got: {list(envelope)}"


@then("the tool_features_status envelope has an error key")
def _envelope_has_error_key(_mcp_features_state: dict[str, Any]) -> None:
    envelope = _mcp_features_state["envelope"]
    assert "error" in envelope, f"expected 'error' key; got: {list(envelope)}"

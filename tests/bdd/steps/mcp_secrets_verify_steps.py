"""BDD step impls for tests/bdd/features/mcp_secrets_verify.feature.

F46-compliant: composes via the actual MCP module function (no direct
internal-state poking, no monkeypatch). The envelope shape + redaction
contract are the unit of behaviour.
"""

from __future__ import annotations

from typing import Any

import pytest
from pytest_bdd import given, parsers, then, when

from kairix.agents.mcp.secrets_status import tool_secrets_verify


@pytest.fixture
def bdd_state() -> dict[str, Any]:
    return {}


@given(parsers.parse("the agent calls tool_secrets_verify with no arguments"))
def call_tool_secrets_verify(bdd_state: dict[str, Any]) -> None:
    bdd_state["envelope"] = tool_secrets_verify()


@when("the MCP tool returns its envelope")
def tool_returns_envelope(bdd_state: dict[str, Any]) -> None:
    assert "envelope" in bdd_state, "tool_secrets_verify was not called"


@then(parsers.parse("the envelope carries the {field} field"))
def envelope_has_field(bdd_state: dict[str, Any], field: str) -> None:
    envelope = bdd_state["envelope"]
    actual_field = field.replace(" list", "")
    assert actual_field in envelope, f"envelope missing {actual_field!r}; keys: {list(envelope)}"


@then("no row in the secrets list carries the legacy_used field")
def no_legacy_used_in_rows(bdd_state: dict[str, Any]) -> None:
    rows = bdd_state["envelope"]["secrets"]
    assert rows, "expected at least one row in the secrets list"
    for row in rows:
        assert "legacy_used" not in row, (
            f"legacy_used MUST be redacted from MCP envelope to prevent alias-map leak; row: {row}"
        )


@then(parsers.parse("every row carries the {field} field"))
def every_row_has_field(bdd_state: dict[str, Any], field: str) -> None:
    rows = bdd_state["envelope"]["secrets"]
    assert rows
    for row in rows:
        assert field in row, f"row missing {field!r}: {row}"

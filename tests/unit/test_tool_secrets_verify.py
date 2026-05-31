"""Unit tests for ``kairix.agents.mcp.secrets_status.tool_secrets_verify``.

The MCP-facing wrapper for ``kairix secrets verify --json``. Returns the
same per-secret resolution envelope but with ``legacy_used`` redacted so
agent-callable surfaces never leak the operator-facing alias map.
"""

from __future__ import annotations

import pytest

from kairix.agents.mcp.secrets_status import tool_secrets_verify

pytestmark = pytest.mark.unit


def test_envelope_shape() -> None:
    result = tool_secrets_verify()
    assert "secrets" in result
    assert "missing_count" in result
    assert "legacy_alias_count" in result
    assert "error" in result
    assert result["error"] == ""  # happy path — no exception
    assert isinstance(result["secrets"], list)
    assert isinstance(result["missing_count"], int)
    assert isinstance(result["legacy_alias_count"], int)


def test_secret_rows_redact_legacy_used() -> None:
    """Every row in the envelope must omit the legacy_used field.

    Sabotage target: removing the `row_dict.pop("legacy_used", None)`
    line in the adapter would re-expose the alias map to agent callers.
    """
    result = tool_secrets_verify()
    assert result["secrets"], "expected at least one secret row from the alias map"
    for row in result["secrets"]:
        assert "legacy_used" not in row, (
            f"legacy_used must be redacted from MCP envelope to prevent alias-map leak; row was {row}"
        )
        # Canonical fields are preserved.
        assert "canonical_kv" in row
        assert "status" in row
        assert "scope" in row
        assert "area" in row


def test_envelope_carries_canonical_kv_names() -> None:
    """Canonical KV names are still in the envelope — operators use these to find secrets in their vault."""
    result = tool_secrets_verify()
    canonicals = [r["canonical_kv"] for r in result["secrets"]]
    assert any(c.startswith("kairix-provider-llm-") for c in canonicals), (
        f"expected at least one kairix-provider-llm-* row; got {canonicals[:5]}..."
    )
    assert any(c.startswith("kairix-connector-") for c in canonicals), (
        f"expected at least one kairix-connector-* row; got {canonicals[:5]}..."
    )


# Exception path removed (commit consolidating F1/F6 cleanup): FastMCP's
# tool layer converts uncaught exceptions to a JSON-RPC error frame
# automatically, so the defensive try/except added no behaviour and
# couldn't be tested cleanly without an F1- or F6-violating seam.

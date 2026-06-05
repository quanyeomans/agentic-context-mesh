"""Unit tests for ``kairix.agents.mcp.secrets_status.tool_secrets_verify``.

The MCP-facing wrapper for ``kairix secrets verify --json``. Returns the
same per-secret resolution envelope as the CLI surface.
"""

from __future__ import annotations

import pytest

from kairix.agents.mcp.secrets_status import tool_secrets_verify

pytestmark = pytest.mark.unit


def test_envelope_shape() -> None:
    result = tool_secrets_verify()
    assert "secrets" in result
    assert "missing_count" in result
    assert "error" in result
    assert result["error"] == ""  # happy path — no exception
    assert isinstance(result["secrets"], list)
    assert isinstance(result["missing_count"], int)


def test_envelope_carries_canonical_kv_names() -> None:
    """Canonical KV names are in the envelope — operators use these to find secrets in their vault."""
    result = tool_secrets_verify()
    canonicals = [r["canonical_kv"] for r in result["secrets"]]
    assert any(c.startswith("kairix-provider-llm-") for c in canonicals), (
        f"expected at least one kairix-provider-llm-* row; got {canonicals[:5]}..."
    )
    assert any(c.startswith("kairix-connector-") for c in canonicals), (
        f"expected at least one kairix-connector-* row; got {canonicals[:5]}..."
    )


def test_secret_rows_carry_status_and_identity() -> None:
    """Every row carries the canonical identity + status — never the secret value."""
    result = tool_secrets_verify()
    assert result["secrets"], "expected at least one registered identity row"
    for row in result["secrets"]:
        assert "canonical_kv" in row
        assert "status" in row
        assert "scope" in row
        assert "area" in row
        assert "leaf" in row
        assert row["status"] in {"present", "MISSING"}


# Exception path removed (commit consolidating F1/F6 cleanup): FastMCP's
# tool layer converts uncaught exceptions to a JSON-RPC error frame
# automatically, so the defensive try/except added no behaviour and
# couldn't be tested cleanly without an F1- or F6-violating seam.

"""Cold-start affordance tests for the Kairix MCP surface."""

from __future__ import annotations

import pytest

from kairix.agents.mcp.cold_start import (
    cold_start_envelope,
    is_cold_start_envelope,
    require_ready,
)

pytestmark = pytest.mark.unit


def test_cold_start_envelope_is_machine_actionable_and_prescriptive() -> None:
    payload = cold_start_envelope(tool_name="search", retry_after_ms=7000, estimated_seconds_remaining=7.0)

    assert payload["status"] == "retryable_not_ready"
    assert payload["error_code"] == "KAIRIX_COLD_START"
    assert payload["retry_after_ms"] == 7000
    assert payload["estimated_seconds_remaining"] == 7.0
    assert "Do not answer from memory" in payload["agent_instruction"]
    assert "retry the same 'search' call" in payload["agent_instruction"]


def test_is_cold_start_envelope_recognises_canonical_shape() -> None:
    assert is_cold_start_envelope(cold_start_envelope(tool_name="bootstrap")) is True
    assert is_cold_start_envelope({"error": "ColdStart"}) is False
    assert is_cold_start_envelope("ColdStart") is False


def test_require_ready_returns_none_when_no_gate_or_ready() -> None:
    assert require_ready("search", None) is None
    assert require_ready("search", lambda: True) is None


def test_require_ready_returns_cold_start_when_gate_not_ready() -> None:
    payload = require_ready("search", lambda: False)

    assert payload is not None
    assert payload["error_code"] == "KAIRIX_COLD_START"
    assert payload["tool"] == "search"

"""Unit tests for :mod:`kairix.agents.mcp.exceptions`.

Pins the canonical TransportFetchFailed envelope shape so downstream
wrappers (kairix-context-bridge, kairix-memory-provider, openclaw-plugin,
third-party MCP clients) can rely on the contract.

Sabotage proof (executed by author, recorded here for the reader):

  In ``kairix/agents/mcp/exceptions.py:transport_fetch_failed_envelope``,
  drop the ``error_code`` key from the return dict. Re-run
  :func:`test_envelope_contains_canonical_error_code` — the assertion
  fails because ``error_code`` is missing. Restore; test passes again.
"""

from __future__ import annotations

import pytest

from kairix.agents.mcp.exceptions import (
    TransportFetchFailedError,
    transport_fetch_failed_envelope,
)

pytestmark = pytest.mark.unit


def test_exception_carries_tool_name() -> None:
    exc = TransportFetchFailedError("mcp-kairix__prep")
    assert exc.tool_name == "mcp-kairix__prep"
    assert "mcp-kairix__prep" in str(exc)


def test_exception_preserves_cause() -> None:
    cause = RuntimeError("socket reset")
    exc = TransportFetchFailedError("mcp-kairix__search", cause=cause)
    assert exc.cause is cause


def test_envelope_contains_canonical_error_code() -> None:
    env = transport_fetch_failed_envelope(tool_name="mcp-kairix__prep")
    assert env["error_code"] == "KAIRIX_TRANSPORT_FETCH_FAILED"
    assert env["error"] == "TransportFetchFailed"
    assert env["status"] == "retryable_transport_failure"


def test_envelope_carries_tool_name_and_retry_hint() -> None:
    env = transport_fetch_failed_envelope(tool_name="mcp-kairix__brief", retry_after_seconds=12)
    assert env["tool"] == "mcp-kairix__brief"
    assert env["retry_after_ms"] == 12_000
    assert env["estimated_seconds_remaining"] == 12.0


def test_envelope_guidance_includes_retry_wait_and_actionable_next() -> None:
    env = transport_fetch_failed_envelope(tool_name="mcp-kairix__search", retry_after_seconds=8)
    assert "8s" in env["guidance"]
    assert env["agent_instruction"].startswith("next:")
    assert "fix:" in env["agent_instruction"]


def test_envelope_links_to_tracking_issue() -> None:
    env = transport_fetch_failed_envelope(tool_name="mcp-kairix__search")
    assert any("issues/320" in link for link in env["see_also"])

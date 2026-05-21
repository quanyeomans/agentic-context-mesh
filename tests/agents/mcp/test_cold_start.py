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


# ---------------------------------------------------------------------------
# warm_retrieval_stack — end-to-end envelope shape coverage
# ---------------------------------------------------------------------------
#
# These tests call the production warm_retrieval_stack() directly. In the
# test env there's no provider config, so build_search_pipeline either
# succeeds (returns a degraded but usable pipeline whose search() may
# still error on missing FTS/vector index) or fails outright with an
# ImportError / ConfigError. Both paths produce a structured envelope —
# we assert the envelope SHAPE rather than the success/failure outcome.
# This drives the function's body lines (107, 129-154) without resorting
# to monkeypatch / injection seams on production helpers.


def test_warm_retrieval_stack_returns_structured_envelope() -> None:
    """The function always returns a status/ready/elapsed_ms/steps envelope.

    Sabotage-proof: drop the final happy-path return statement at line 154
    and this test fails when the function falls off the end and returns None.
    """
    from kairix.agents.mcp.cold_start import warm_retrieval_stack

    payload = warm_retrieval_stack()

    assert isinstance(payload, dict)
    assert "status" in payload
    assert "ready" in payload
    assert "elapsed_ms" in payload
    assert "steps" in payload
    assert payload["status"] in {"ok", "error"}
    assert isinstance(payload["ready"], bool)
    assert isinstance(payload["elapsed_ms"], int)
    assert isinstance(payload["steps"], list)


def test_warm_retrieval_stack_step_records_are_well_formed() -> None:
    """Each entry in steps[] has name + ok (+ elapsed_ms on success or error on failure).

    Sabotage-proof: drop the steps.append in the happy path and the
    success case asserts on an empty list; drop the error branch's
    steps.append and the failure case asserts on the same.
    """
    from kairix.agents.mcp.cold_start import warm_retrieval_stack

    payload = warm_retrieval_stack()
    steps = payload["steps"]

    # At least one step record is emitted regardless of outcome — either
    # the build_search_pipeline step (success or failure) or both
    # build + probe_search steps when the pipeline constructs cleanly.
    assert len(steps) >= 1
    for step in steps:
        assert "name" in step
        assert "ok" in step
        assert step["name"] in {"build_search_pipeline", "probe_search"}
        if step["ok"] is True:
            assert "elapsed_ms" in step
        else:
            assert "error" in step
            assert isinstance(step["error"], str)


def test_warm_retrieval_stack_ready_aligns_with_status() -> None:
    """``ready=True`` iff ``status=='ok'``; both fields must agree.

    Sabotage-proof: invert the happy-path return's ``ready`` value and
    the assertion ``(status == 'ok') == (ready is True)`` catches it.
    """
    from kairix.agents.mcp.cold_start import warm_retrieval_stack

    payload = warm_retrieval_stack()
    assert (payload["status"] == "ok") == (payload["ready"] is True)
    if payload["status"] == "error":
        assert payload["ready"] is False

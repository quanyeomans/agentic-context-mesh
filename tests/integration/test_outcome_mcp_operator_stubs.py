"""F30 outcome tests — MCP operator-only escalation stubs.

Five MCP tools in ``kairix/agents/mcp/server.py`` are agent-callable but
return the canonical ``OperatorOnlyCapability`` envelope rather than
running the underlying work (which mutates state, loads the system, or
is otherwise too heavy for an in-session agent to safely invoke):

    benchmark_run, embed, embed_rebuild_fts, probe_config, store_crawl

The F30 contract for MCP tools (``docs/architecture/fitness-functions.md``,
``scripts/checks/check_f30_operator_outcome_tests.py``):

  Call the MCP tool handler directly (``tool_<name>``), and assert on
  the returned-envelope content via Subscript/Attribute access — NOT on
  internal call-counts of fakes, NOT on ``returncode == 0`` alone.

These stubs have no DI seam by design — they synthesise a static
``OperatorOnlyCapability`` envelope from constants. The outcome test
shape is the simplest possible: call the handler, assert on the
load-bearing envelope keys the agent harness consumes.

Sabotage-proof:
  For each tool, the test asserts on the ``operator_command`` substring
  the agent surfaces to its admin. Mutating
  ``_operator_only_envelope`` to drop the ``operator_command`` key, OR
  changing the tool's ``operator_command=`` literal to an unrelated
  string, makes the assertion fail. Verified per-tool below by
  temporarily mutating the literal in ``tool_<name>`` and observing the
  ``KeyError`` (drop) or ``AssertionError`` (changed literal).
"""

from __future__ import annotations

from typing import Any

import pytest

from kairix.agents.mcp.server import (
    tool_benchmark_run,
    tool_embed,
    tool_embed_rebuild_fts,
    tool_probe_config,
    tool_store_crawl,
)

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Shared envelope-shape contract for every operator-only stub.
# ---------------------------------------------------------------------------


_REQUIRED_ENVELOPE_KEYS = {
    "error",
    "capability",
    "reason",
    "operator_command",
    "expected_runtime_seconds",
    "see_also",
}


def _assert_operator_only_envelope_shape(envelope: dict[str, Any]) -> None:
    """Every operator-only stub returns the same envelope shape.

    Asserting on Subscript access (``envelope["..."]``) is the F30
    detector's signal that the test reads the returned envelope — see
    ``_McpOutcomeProbe`` in
    ``scripts/checks/check_f30_operator_outcome_tests.py``.
    """
    assert isinstance(envelope, dict)
    assert set(envelope.keys()) >= _REQUIRED_ENVELOPE_KEYS, (
        f"envelope missing required keys: {_REQUIRED_ENVELOPE_KEYS - envelope.keys()}; got {sorted(envelope.keys())}"
    )
    assert envelope["error"] == "OperatorOnlyCapability", f"error key mismatch: {envelope['error']!r}"
    assert envelope["operator_command"].startswith("kairix "), (
        f"operator_command must be a kairix CLI invocation: {envelope['operator_command']!r}"
    )
    assert envelope["expected_runtime_seconds"] > 0, (
        f"expected_runtime_seconds must be positive: {envelope['expected_runtime_seconds']}"
    )
    assert isinstance(envelope["see_also"], list)
    assert envelope["see_also"], "see_also list must contain at least one runbook reference"


# ---------------------------------------------------------------------------
# Per-tool outcome tests
# ---------------------------------------------------------------------------


def test_tool_benchmark_run_envelope_routes_agent_to_kairix_benchmark_run() -> None:
    """``benchmark_run`` returns the escalation envelope with the exact CLI command.

    Sabotage: mutate
    ``operator_command=f"kairix benchmark run --suite {suite}"`` →
    ``operator_command="WRONG"`` in ``tool_benchmark_run``. The
    ``"benchmark"`` substring assertion below fails. Verified.
    """
    envelope = tool_benchmark_run(suite="reflib")
    _assert_operator_only_envelope_shape(envelope)
    assert envelope["capability"] == "benchmark run", f"capability mismatch: {envelope['capability']!r}"
    assert "benchmark run" in envelope["operator_command"], (
        f"operator_command missing 'benchmark run': {envelope['operator_command']!r}"
    )
    assert "--suite reflib" in envelope["operator_command"], (
        f"operator_command missing suite flag: {envelope['operator_command']!r}"
    )


def test_tool_embed_envelope_routes_agent_to_kairix_embed() -> None:
    """``embed`` returns the escalation envelope; default limit emits no flag.

    Sabotage: mutate
    ``flag = "" if limit == 0 else f" --limit {limit}"`` →
    ``flag = " --limit 999"`` in ``tool_embed``. The default-no-flag
    assertion below fails. Verified.
    """
    envelope = tool_embed()
    _assert_operator_only_envelope_shape(envelope)
    assert envelope["capability"] == "embed", f"capability mismatch: {envelope['capability']!r}"
    assert envelope["operator_command"] == "kairix embed", (
        f"default operator_command must be flag-free: {envelope['operator_command']!r}"
    )


def test_tool_embed_envelope_with_limit_includes_limit_flag() -> None:
    """Passing ``limit=5`` threads through to the operator_command suffix.

    Sabotage: remove the conditional ``--limit {limit}`` formatting in
    ``tool_embed`` so the operator_command is always ``"kairix embed"`` —
    this assertion fails. Verified.
    """
    envelope = tool_embed(limit=5)
    assert envelope["operator_command"] == "kairix embed --limit 5", (
        f"operator_command mismatch with limit: {envelope['operator_command']!r}"
    )


def test_tool_embed_rebuild_fts_envelope_routes_agent_to_rebuild_command() -> None:
    """``embed_rebuild_fts`` returns the escalation envelope for the recovery action.

    Sabotage: mutate the ``capability=`` literal in
    ``tool_embed_rebuild_fts`` to ``"WRONG"``. The capability assertion
    below fails. Verified.
    """
    envelope = tool_embed_rebuild_fts()
    _assert_operator_only_envelope_shape(envelope)
    assert envelope["capability"] == "embed rebuild-fts", f"capability mismatch: {envelope['capability']!r}"
    assert envelope["operator_command"] == "kairix embed rebuild-fts", (
        f"operator_command mismatch: {envelope['operator_command']!r}"
    )


def test_tool_probe_config_envelope_routes_agent_to_probe_config() -> None:
    """``probe_config`` returns the escalation envelope for the tuning advisor.

    Sabotage: mutate ``operator_command="kairix probe-config"`` →
    ``operator_command="kairix probe-other"`` in ``tool_probe_config``.
    The substring assertion below fails. Verified.
    """
    envelope = tool_probe_config()
    _assert_operator_only_envelope_shape(envelope)
    assert envelope["capability"] == "probe-config", f"capability mismatch: {envelope['capability']!r}"
    assert envelope["operator_command"] == "kairix probe-config", (
        f"operator_command mismatch: {envelope['operator_command']!r}"
    )


def test_tool_store_crawl_envelope_routes_agent_to_store_crawl() -> None:
    """``store_crawl`` returns the escalation envelope for the graph crawl.

    Sabotage: mutate ``operator_command="kairix store crawl"`` →
    ``operator_command="kairix store walk"`` in ``tool_store_crawl``.
    The substring assertion below fails. Verified.
    """
    envelope = tool_store_crawl()
    _assert_operator_only_envelope_shape(envelope)
    assert envelope["capability"] == "store crawl", f"capability mismatch: {envelope['capability']!r}"
    assert envelope["operator_command"] == "kairix store crawl", (
        f"operator_command mismatch: {envelope['operator_command']!r}"
    )

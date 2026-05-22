"""F30 outcome test — MCP ``warm`` tool.

``tool_warm`` wraps ``kairix.platform.warm.run_warm`` in a broad
exception envelope. It is the only callable tools-side surface for the
warm-up sequence (build pipeline → probe → open graph client) — agents
hit it at session start or as a health probe.

The F30 contract for MCP tools (``scripts/checks/check_f30_operator_outcome_tests.py``):
call ``tool_<name>`` directly and assert on returned-envelope content
via Subscript/Attribute access — NOT on internal call-counts.

The seam below the MCP tool is ``run_warm``'s injectable
``pipeline_builder``/``search_probe``/``graph_client_opener`` kwargs.
``tool_warm`` itself takes no kwargs, so the outcome test invokes
``tool_warm()`` directly and accepts the natural test-env behaviour:
``run_warm`` either reports per-step ``ok`` flags or surfaces the
exception in the envelope's ``failures`` list. Either branch is a
legitimate run; the assertion is on the structured envelope keys agents
read.

The companion ``run_warm`` outcome (with explicit DI to drive every
step ok=True) is tested in
``tests/platform/warm/test_runner_integration.py`` — this file is the
**MCP tool surface's** F30 paydown and asserts on the projected
envelope shape.
"""

from __future__ import annotations

from typing import Any

import pytest

from kairix.agents.mcp.server import tool_warm

pytestmark = pytest.mark.integration


_WARM_ENVELOPE_KEYS = {"ok", "total_duration_s", "steps", "failures"}


def test_tool_warm_returns_warm_envelope_shape() -> None:
    """``tool_warm`` returns a JSON-serialisable dict with the warm envelope shape.

    Drives the production happy path: ``run_warm() → WarmResult →
    .to_envelope()``. In a unit-test environment the search-pipeline
    builder + probe land on a search backend with no provider creds —
    individual ``steps[i].ok`` may flip to False but the envelope shape
    is invariant.

    Asserts via Subscript access on every load-bearing key (``steps``,
    ``failures``, ``total_duration_s``, ``ok``) — the F30 detector's
    signal that the test consumes the envelope.

    Sabotage: mutate ``return run_warm().to_envelope()`` →
    ``return {}`` in ``tool_warm``. The envelope-keys subset assertion
    below fails because ``ok``/``steps``/``failures`` go missing.
    Verified.
    """
    envelope: dict[str, Any] = tool_warm()
    assert isinstance(envelope, dict)
    assert set(envelope.keys()) >= _WARM_ENVELOPE_KEYS, (
        f"warm envelope missing keys: {_WARM_ENVELOPE_KEYS - envelope.keys()}; got {sorted(envelope.keys())}"
    )
    # Steps list comes back as a list of {name, ok, duration_s, detail}
    # dicts; the envelope must always carry it (empty list is acceptable
    # only if an exception fired before any step ran).
    assert isinstance(envelope["steps"], list)
    assert isinstance(envelope["failures"], list)
    assert isinstance(envelope["total_duration_s"], (int, float))
    assert isinstance(envelope["ok"], bool)


def test_tool_warm_envelope_steps_carry_per_step_envelope_shape() -> None:
    """Each entry in ``envelope["steps"]`` carries the WarmStep projection.

    The agent-side surface depends on ``steps[i]["name"]`` to know which
    sub-system failed when ``ok=False``; pin the per-step shape so a
    silent ``to_envelope`` regression (dropping a field) blocks here.

    Sabotage: mutate WarmResult.to_envelope's per-step dict to drop the
    ``"name"`` key → this assertion fails with KeyError. Verified.
    """
    envelope = tool_warm()
    steps = envelope["steps"]
    # When the warm runner fires at least one step (always the case in
    # the production code path — ``_step_build_pipeline`` runs
    # unconditionally), each step dict must carry name/ok/duration_s/detail.
    if steps:
        first = steps[0]
        assert isinstance(first, dict)
        assert "name" in first, f"step missing name: {first}"
        assert "ok" in first, f"step missing ok: {first}"
        assert "duration_s" in first, f"step missing duration_s: {first}"
        assert "detail" in first, f"step missing detail: {first}"

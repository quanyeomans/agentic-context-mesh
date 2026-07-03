"""Error-envelope coverage for the diagnostic MCP tool adapters.

Every diagnostic tool in ``kairix.agents.mcp.tools.diagnostic`` wraps its
Python-API dependency in a defensive ``except`` that collapses any failure
into a *typed error envelope* — the result fields zeroed out and a
``"<ExcType>: <message>"`` string on ``error`` — so an agent never sees a raw
traceback and can decide whether to fall back or escalate.

These tests drive each tool's real ``except`` branch and assert that envelope
shape. Each case triggers the failure through the tool's own boundary:

* Tools with a ``read_db_path`` DI seam (``features_status`` topology read,
  ``dead_letter_status``) get an injected callable that raises — the F86
  pattern already used across this file.
* Tools whose dependency is designed never to raise on bad *data*
  (``scan_for_agents`` swallows disk errors; ``discover_single_agent`` raises
  only ``ValueError``, handled separately) are driven through the defensive
  branch by a malformed argument that makes the real ``Path()`` / mapping
  access raise — no monkeypatch of any kairix internal (F1-clean).

Sabotage proof (executed on the ``features_status`` case, mutate→fail→restore):
dropping the raising ``read_db_path`` seam makes the tool return its success
envelope, so ``error`` is empty and this test fails — confirming the assertion
exercises the real ``except`` rather than passing vacuously.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from kairix.agents.mcp.server import (
    tool_dead_letter_status,
    tool_doctor_check_agent,
    tool_doctor_check_all,
    tool_features_status,
    tool_onboard_agent,
    tool_onboard_scan,
)

pytestmark = pytest.mark.unit


def _raise_dep(*_args: Any, **_kwargs: Any) -> Any:
    """A DI-seam stand-in that always raises — the injected failure."""
    raise RuntimeError("injected diagnostic failure")


# (id, call, zeroed_field, zeroed_value): ``call`` triggers the tool's real
# ``except`` branch; the error envelope must populate ``error`` AND zero out
# ``zeroed_field`` so the agent reads an empty result rather than a partial one.
# The ``123`` / ``"not-a-mapping"`` arguments are deliberate malformed inputs:
# the wrapped dependency never raises on bad *data*, so the only way to reach
# the defensive branch is an argument the tool's own ``Path()`` / mapping access
# rejects — the same failure an agent would trigger with a bad call.
_ERROR_CASES: tuple[tuple[str, Callable[[], dict[str, Any]], str, Any], ...] = (
    (
        "onboard_scan",
        lambda: tool_onboard_scan(memory_root=123),  # type: ignore[arg-type]  # malformed memory_root drives Path() TypeError into the defensive except
        "agents",
        [],
    ),
    (
        "onboard_agent",
        lambda: tool_onboard_agent(agent_name="agent-alpha", memory_root=123),  # type: ignore[arg-type]  # malformed memory_root raises a non-ValueError into the generic except
        "agent",
        None,
    ),
    (
        "doctor_check_all",
        lambda: tool_doctor_check_all(config="not-a-mapping"),  # type: ignore[arg-type]  # a non-mapping config makes the real doctor raise AttributeError
        "agents",
        [],
    ),
    (
        "doctor_check_agent",
        lambda: tool_doctor_check_agent("agent-alpha", config="not-a-mapping"),  # type: ignore[arg-type]  # a non-mapping config makes the real doctor raise AttributeError
        "agent",
        None,
    ),
    (
        "features_status",
        lambda: tool_features_status(topology=True, read_db_path=_raise_dep),
        "flags",
        [],
    ),
    (
        "dead_letter_status",
        lambda: tool_dead_letter_status(read_db_path=_raise_dep),
        "per_source",
        [],
    ),
)


@pytest.mark.parametrize(
    ("tool_id", "call", "zeroed_field", "zeroed_value"),
    _ERROR_CASES,
    ids=[case[0] for case in _ERROR_CASES],
)
def test_diagnostic_tool_returns_typed_error_envelope(
    tool_id: str,
    call: Callable[[], dict[str, Any]],
    zeroed_field: str,
    zeroed_value: Any,
) -> None:
    """Each diagnostic tool collapses a dependency failure into a typed envelope.

    Asserts the two invariants of the error envelope: ``error`` carries a
    ``"<ExcType>: <message>"`` string, and the result field is zeroed so the
    agent never reads a partial result alongside an error.
    """
    envelope = call()

    assert envelope["error"], f"{tool_id}: the except branch must populate a typed error string; got {envelope!r}"
    assert ":" in envelope["error"], f"{tool_id}: error should be '<ExcType>: <message>'; got {envelope['error']!r}"
    assert envelope[zeroed_field] == zeroed_value, (
        f"{tool_id}: error envelope must zero out {zeroed_field!r}; got {envelope[zeroed_field]!r}"
    )

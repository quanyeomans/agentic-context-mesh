"""Protocol-shape contract for :mod:`kairix.core.agents.detectors` (PR 1.3 / #420).

Pins the structural promises of :class:`HarnessDetector` and the registry
helper :func:`get_registered_detectors`. PR 1.4's ``kairix onboard scan``
will iterate every registered detector to bootstrap an agent's
:class:`~kairix.core.agents.scope.AgentScope` from disk; this contract
freezes the shape those callers depend on.

Three shape promises:
  * :class:`HarnessDetector` is a runtime-checkable :class:`~typing.Protocol`
    with a ``name: str`` attribute and a ``propose_surfaces`` method that
    returns ``tuple[AgentSurface, ...]``.
  * Each of :class:`ClaudeCodeDetector`, :class:`CodexDetector`, and
    :class:`GenericDetector` satisfies the protocol at runtime.
  * :func:`get_registered_detectors` returns exactly the three detectors
    in deterministic order with the ``generic`` detector last so callers
    can treat it as a fallback.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kairix.core.agents.detectors import (
    ClaudeCodeDetector,
    CodexDetector,
    GenericDetector,
    HarnessDetector,
    get_registered_detectors,
)
from kairix.core.agents.scope import AgentSurface

pytestmark = pytest.mark.contract


# Sabotage-proof (executed): renamed `propose_surfaces` on ClaudeCodeDetector
# to `propose_surfaces_x` → isinstance(ClaudeCodeDetector(), HarnessDetector)
# returned False; test failed; restored.
def test_claude_code_detector_satisfies_protocol() -> None:
    """ClaudeCodeDetector is structurally a HarnessDetector — has the
    ``name`` attribute and the ``propose_surfaces`` method that callers
    in PR 1.4 will rely on."""
    assert isinstance(ClaudeCodeDetector(), HarnessDetector)


# Sabotage-proof (executed): dropped the `name = "codex"` attribute from
# CodexDetector → isinstance check returned False; test failed; restored.
def test_codex_detector_satisfies_protocol() -> None:
    """CodexDetector is structurally a HarnessDetector."""
    assert isinstance(CodexDetector(), HarnessDetector)


# Sabotage-proof (executed): renamed `propose_surfaces` on GenericDetector
# to `_propose_surfaces` → isinstance check returned False; test failed;
# restored.
def test_generic_detector_satisfies_protocol() -> None:
    """GenericDetector is structurally a HarnessDetector — the fallback
    still has to satisfy the same surface."""
    assert isinstance(GenericDetector(), HarnessDetector)


# Sabotage-proof (executed): changed ClaudeCodeDetector.name from
# "claude-code" to "" → assertion on truthy name failed; test failed;
# restored.
def test_each_detector_has_non_empty_name() -> None:
    """Every detector reports a non-empty ``name`` — callers use it for
    log lines and operator-facing proposal grouping."""
    for det in (ClaudeCodeDetector(), CodexDetector(), GenericDetector()):
        assert isinstance(det.name, str)
        assert det.name, f"detector {type(det).__name__} reported empty name"


# Sabotage-proof (executed): made ClaudeCodeDetector.propose_surfaces return
# a list instead of a tuple → isinstance(result, tuple) failed; test
# failed; restored.
def test_propose_surfaces_returns_tuple_of_agent_surface(tmp_path: Path) -> None:
    """``propose_surfaces`` always returns ``tuple[AgentSurface, ...]`` —
    never a list, dict, or generator. Empty proposals are an empty tuple,
    not None."""
    # Claude-code marker present → non-empty tuple
    (tmp_path / "CLAUDE.md").write_text("# project")
    out = ClaudeCodeDetector().propose_surfaces("agent-alpha", tmp_path)
    assert isinstance(out, tuple)
    for item in out:
        assert isinstance(item, AgentSurface)


# Sabotage-proof (executed): changed get_registered_detectors to return
# only (ClaudeCodeDetector(), CodexDetector()) → len(...) == 3 assertion
# failed; test failed; restored.
def test_registry_returns_three_detectors_with_generic_last() -> None:
    """``get_registered_detectors`` returns exactly the three known
    detectors. ``generic`` is always last so callers iterating the tuple
    can treat it as the fallback shape (no special branching)."""
    detectors = get_registered_detectors()
    assert isinstance(detectors, tuple)
    assert len(detectors) == 3
    names = [d.name for d in detectors]
    assert names == ["claude-code", "codex", "generic"]
    assert isinstance(detectors[-1], GenericDetector)


# Sabotage-proof (executed): reordered the registry tuple to put generic
# first → names[0] == "generic" instead of "claude-code"; assertion
# failed; restored.
def test_registry_order_is_deterministic_across_calls() -> None:
    """The registry returns the detectors in the same order on every
    call — callers iterating multiple times must see the same proposal
    sequence."""
    first = [d.name for d in get_registered_detectors()]
    second = [d.name for d in get_registered_detectors()]
    assert first == second


# Sabotage-proof (executed): made the harness detector return a
# ``dict[str, Any]`` (mutable mapping) instead of a tuple of AgentSurface
# → propose_surfaces' isinstance check failed and the contract test
# below complained about the return shape; restored.
def test_empty_directory_yields_empty_tuple_not_none(tmp_path: Path) -> None:
    """An empty candidate directory yields an empty tuple from every
    detector — never None, never a raise. PR 1.4 relies on this so the
    aggregation loop can concatenate without None-guarding."""
    for det in (ClaudeCodeDetector(), CodexDetector(), GenericDetector()):
        out = det.propose_surfaces("agent-alpha", tmp_path)
        assert out == ()
        assert isinstance(out, tuple)

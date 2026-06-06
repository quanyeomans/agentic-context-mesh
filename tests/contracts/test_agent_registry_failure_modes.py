"""F68 (ADR-024 Bundle A) — failure-mode contract for :class:`AgentRegistry`.

Three Protocol methods (``list_agents`` / ``collection_for`` /
``validate_write``). The fakes from ``tests/fakes.py`` exercise the
"unknown agent" failure path: ``collection_for`` raises ``KeyError``,
``validate_write`` returns ``False`` (the "no" outcome — observable +
sabotage-provable), and ``list_agents`` returns the empty list when
none configured.

Each test carries a "Sabotage proof:" comment describing the mutation
that proves the assertion has teeth.
"""

from __future__ import annotations

import pytest

from tests.fakes import FakeAgentRegistry

pytestmark = pytest.mark.contract


def test_list_agents_returns_empty_when_no_agents_configured() -> None:
    """An :class:`AgentRegistry` constructed with no agents returns an
    empty list — callers MUST tolerate the empty case (e.g. ALL_AGENTS
    scope resolves to "everything", not a crash).

    Sabotage proof: in ``FakeAgentRegistry.list_agents`` change the
    return to ``[_Agent({"name": "ghost", "collection": "x"})]``. Re-run:
    the test fails because the result has one entry instead of zero.
    Restored.
    """
    registry = FakeAgentRegistry(agents=[])
    result = registry.list_agents()
    assert result == [], f"empty registry must return empty list; got {result!r}"


def test_collection_for_raises_key_error_when_agent_unknown() -> None:
    """``collection_for("unknown")`` must raise — silent fallback to a
    default collection would let typos route writes to the wrong agent.

    Sabotage proof: in ``FakeAgentRegistry.collection_for`` change the
    final ``raise KeyError(...)`` to ``return "default"``. Re-run: the
    test fails because no exception is raised. Restored.
    """
    registry = FakeAgentRegistry(agents=[{"name": "agent-alpha", "collection": "alpha-mem"}])
    with pytest.raises(KeyError, match="unknown"):
        registry.collection_for("does-not-exist")


def test_validate_write_returns_empty_negative_when_path_outside_scope() -> None:
    """``validate_write`` returns ``False`` when the agent exists but the
    path is outside its ``write_path`` — the "no" answer IS the failure
    outcome (the caller refuses the write).

    Sabotage proof: in ``FakeAgentRegistry.validate_write`` change the
    final ``return False`` to ``return True``. Re-run: the test fails
    because every path is allowed. Restored.
    """
    registry = FakeAgentRegistry(
        agents=[{"name": "agent-alpha", "collection": "alpha-mem", "write_path": "agents/alpha"}]
    )
    assert registry.validate_write("agent-alpha", "agents/beta/notes.md") is False
    # And the unknown-agent branch returns False, not raises — silent
    # negative is the documented Protocol shape for "no such agent".
    assert registry.validate_write("agent-ghost", "any/path") is False

"""Step impls for agent_scope_callsites.feature (PR 1.2 / #420).

F46-clean — step impls compose via the public source-fetcher / router
surfaces (the public functions that PR 1.2 refactored). No direct
pipeline construction, no monkeypatching, no internal symbol imports.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import pytest
from pytest_bdd import given, then, when


@dataclass
class _AgentScopeCallsiteCtx:
    """Context shared across step impls within one scenario."""

    memory_surface: Path = field(default_factory=Path)
    workspace_surface: Path = field(default_factory=Path)
    config: dict[str, object] = field(default_factory=dict)
    agent_name: str = ""
    fetch_result: str = ""
    resolved_path: str = ""
    caplog: pytest.LogCaptureFixture | None = None


@pytest.fixture
def agent_scope_ctx(caplog: pytest.LogCaptureFixture) -> _AgentScopeCallsiteCtx:
    ctx = _AgentScopeCallsiteCtx()
    ctx.caplog = caplog
    return ctx


# ---------------------------------------------------------------------------
# Scenario 1 — multi-surface read
# ---------------------------------------------------------------------------


@given("an agent with surfaces at the memory dir and the workspace dir")
def _given_two_surfaces(agent_scope_ctx: _AgentScopeCallsiteCtx, tmp_path: Path) -> None:
    agent_scope_ctx.agent_name = "agent-alpha"
    agent_scope_ctx.memory_surface = tmp_path / "vault" / "memory" / "agent-alpha"
    agent_scope_ctx.workspace_surface = tmp_path / "workspaces" / "agent-alpha"
    agent_scope_ctx.memory_surface.mkdir(parents=True)
    agent_scope_ctx.workspace_surface.mkdir(parents=True)
    agent_scope_ctx.config = {
        "agents": {
            "agent-alpha": {
                "surfaces": [
                    {"path": str(agent_scope_ctx.memory_surface), "label": "memory"},
                    {"path": str(agent_scope_ctx.workspace_surface), "label": "workspace"},
                ],
            },
        },
    }


@given("a memory log file in both surfaces for today")
def _given_log_in_each(agent_scope_ctx: _AgentScopeCallsiteCtx) -> None:
    today = date.today().isoformat()
    (agent_scope_ctx.memory_surface / f"{today}.md").write_text(
        "## Session A\n[pending] memory-surface-marker\n",
        encoding="utf-8",
    )
    (agent_scope_ctx.workspace_surface / f"{today}.md").write_text(
        "## Session B\n[pending] workspace-surface-marker\n",
        encoding="utf-8",
    )


@when("the brief source fetcher reads memory logs for the agent")
def _when_fetch_logs(agent_scope_ctx: _AgentScopeCallsiteCtx) -> None:
    from kairix.agents.briefing.sources import fetch_memory_logs

    agent_scope_ctx.fetch_result = fetch_memory_logs(
        agent_scope_ctx.agent_name,
        memory_dirs=[agent_scope_ctx.memory_surface, agent_scope_ctx.workspace_surface],
    )


@then("the result contains the marker from both surfaces")
def _then_both_markers_present(agent_scope_ctx: _AgentScopeCallsiteCtx) -> None:
    assert "memory-surface-marker" in agent_scope_ctx.fetch_result, (
        f"memory surface marker missing from result: {agent_scope_ctx.fetch_result!r}"
    )
    assert "workspace-surface-marker" in agent_scope_ctx.fetch_result, (
        f"workspace surface marker missing from result: {agent_scope_ctx.fetch_result!r}"
    )


# ---------------------------------------------------------------------------
# Scenario 2 — episodic write follows scope.writable_path()
# ---------------------------------------------------------------------------


@given("an agent with a workspace surface and a memory surface")
def _given_workspace_and_memory_surface(agent_scope_ctx: _AgentScopeCallsiteCtx, tmp_path: Path) -> None:
    # ``builder`` because the classify router's legacy ``VALID_AGENTS``
    # set is still hardcoded — relaxing that gate is out of scope for PR 1.2.
    agent_scope_ctx.agent_name = "builder"
    agent_scope_ctx.memory_surface = tmp_path / "vault" / "memory" / "builder"
    agent_scope_ctx.workspace_surface = tmp_path / "workspaces" / "builder"
    agent_scope_ctx.memory_surface.mkdir(parents=True)
    agent_scope_ctx.workspace_surface.mkdir(parents=True)
    # Workspace declared first to prove writable_path picks the labelled
    # "memory" surface — not just the first surface.
    agent_scope_ctx.config = {
        "agents": {
            "builder": {
                "surfaces": [
                    {"path": str(agent_scope_ctx.workspace_surface), "label": "workspace"},
                    {"path": str(agent_scope_ctx.memory_surface), "label": "memory"},
                ],
            },
        },
    }


@when("the classify router resolves an episodic target for the agent")
def _when_router_resolve_episodic(agent_scope_ctx: _AgentScopeCallsiteCtx) -> None:
    from kairix.core.classify.router import resolve_target_path

    agent_scope_ctx.resolved_path = resolve_target_path(
        agent=agent_scope_ctx.agent_name,
        classification_type="episodic",
        date="2026-06-06",
        config=agent_scope_ctx.config,
    )


@then("the resolved path is under the memory surface")
def _then_resolved_under_memory(agent_scope_ctx: _AgentScopeCallsiteCtx) -> None:
    assert str(agent_scope_ctx.memory_surface) in agent_scope_ctx.resolved_path, (
        f"resolved path {agent_scope_ctx.resolved_path!r} not under memory surface {agent_scope_ctx.memory_surface}"
    )
    assert str(agent_scope_ctx.workspace_surface) not in agent_scope_ctx.resolved_path, (
        f"resolved path {agent_scope_ctx.resolved_path!r} unexpectedly under "
        f"workspace surface {agent_scope_ctx.workspace_surface}"
    )


# ---------------------------------------------------------------------------
# Scenario 3 — agent_defaults synthesis path
# ---------------------------------------------------------------------------


@given("no explicit agent entry in config but an agent_defaults memory root")
def _given_defaults_only(agent_scope_ctx: _AgentScopeCallsiteCtx, tmp_path: Path) -> None:
    agent_scope_ctx.agent_name = "agent-beta"
    memory_root = tmp_path / "defaults" / "memory"
    agent_scope_ctx.memory_surface = memory_root / "agent-beta"
    agent_scope_ctx.memory_surface.mkdir(parents=True)
    today = date.today().isoformat()
    (agent_scope_ctx.memory_surface / f"{today}.md").write_text(
        "## Session\n[pending] defaults-synthesis-marker\n",
        encoding="utf-8",
    )
    agent_scope_ctx.config = {
        "agent_defaults": {
            "memory_root": str(memory_root),
        },
    }


@when("the brief source fetcher reads memory logs for the fallback agent")
def _when_fetch_via_scope(agent_scope_ctx: _AgentScopeCallsiteCtx) -> None:
    from kairix.agents.briefing.sources import fetch_memory_logs

    assert agent_scope_ctx.caplog is not None
    with agent_scope_ctx.caplog.at_level(logging.WARNING, logger="kairix.core.agents.scope"):
        agent_scope_ctx.fetch_result = fetch_memory_logs(
            agent_scope_ctx.agent_name,
            max_tokens=500,
            config=agent_scope_ctx.config,
        )


@then("the result includes the synthesised surface content")
def _then_synthesised_content_present(agent_scope_ctx: _AgentScopeCallsiteCtx) -> None:
    assert "defaults-synthesis-marker" in agent_scope_ctx.fetch_result, (
        f"synthesised surface content missing from result: {agent_scope_ctx.fetch_result!r}"
    )


@then("a synthesis warning names the fallback agent")
def _then_warning_names_agent(agent_scope_ctx: _AgentScopeCallsiteCtx) -> None:
    assert agent_scope_ctx.caplog is not None
    warnings = [r for r in agent_scope_ctx.caplog.records if r.levelno == logging.WARNING]
    assert any(agent_scope_ctx.agent_name in r.getMessage() for r in warnings), (
        f"no warning naming {agent_scope_ctx.agent_name!r} in logs: {[r.getMessage() for r in warnings]}"
    )

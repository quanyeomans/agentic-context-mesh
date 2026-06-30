"""Unit tests for ``kairix.agents.mcp.server.tool_brief``.

The MCP adapter is a 4-line glue function. Coverage for the use-case
body lives in ``tests/use_cases/test_brief.py``; this test drives the
adapter shell via the typed-deps forwarder so the projection through
``brief_output_to_envelope`` is exercised end-to-end.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kairix.agents.mcp.server import tool_brief
from kairix.core.health import HealthDeps
from kairix.use_cases.brief import BriefDeps

pytestmark = pytest.mark.unit


def _healthy_health_deps() -> HealthDeps:
    return HealthDeps(
        secrets_loaded_fn=lambda: True,
        embed_backend_available_fn=lambda: True,
        bm25_index_available_fn=lambda: True,
        neo4j_available_fn=lambda: True,
    )


def _surface_config(agent: str) -> dict[str, object]:
    """Config declaring ``agent`` with one surface so it resolves (PLA-265)."""
    return {"agents": {agent: {"surfaces": [{"path": f"memory/{agent}", "label": "memory"}]}}}


def test_tool_brief_happy_path_returns_envelope_dict() -> None:
    deps = BriefDeps(
        generate_fn=lambda agent, **_: "line 1\nline 2\nline 3",
        briefing_dir_fn=lambda: Path("/var/kairix"),
        config_fn=lambda: _surface_config("builder"),
        health_deps=_healthy_health_deps(),
    )
    result = tool_brief(agent="builder", deps=deps)

    assert result["agent"] == "builder"
    assert result["content"] == "line 1\nline 2\nline 3"
    assert result["path"] == "/var/kairix/builder-latest.md"
    assert result["preview"] == "line 1\nline 2\nline 3"
    assert result["error"] == ""
    # Health snapshot is now part of every tool envelope (#246 W3).
    assert result["health"]["chat"] == "ok"
    assert result["health"]["next_action"] == ""


def test_tool_brief_no_surface_agent_returns_error_envelope() -> None:
    """An agent that resolves to no surface returns the InvalidAgent envelope.

    Post-PLA-265 the brief accepts any config-resolvable agent; only a
    name with zero surfaces (explicit empty ``surfaces: []``) is rejected.
    """
    deps = BriefDeps(config_fn=lambda: {"agents": {"ghost": {"surfaces": []}}}, health_deps=_healthy_health_deps())
    result = tool_brief(agent="ghost", deps=deps)
    assert result["error"].startswith("InvalidAgent")
    assert result["content"] == ""


def test_tool_brief_generate_failure_returns_error_envelope() -> None:
    def _boom(agent: str, **_: object) -> str:
        raise RuntimeError("generate failed")

    deps = BriefDeps(
        generate_fn=_boom,
        briefing_dir_fn=lambda: Path("/x"),
        config_fn=lambda: _surface_config("builder"),
        health_deps=_healthy_health_deps(),
    )
    result = tool_brief(agent="builder", deps=deps)
    assert result["error"].startswith("RuntimeError")
    assert result["content"] == ""

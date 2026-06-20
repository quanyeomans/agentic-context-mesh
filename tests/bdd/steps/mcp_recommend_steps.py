"""Step definitions for mcp_recommend_capabilities.feature.

Drives the production MCP adapter
``kairix.agents.mcp.server.tool_recommend`` with deps + flag_reader
injected through the public seams — no @patch, no env vars (F1/F2). The
adapter is a thin wrapper around ``run_recommend`` + the envelope helper,
flag-gated at the adapter level. F13-clean: agent/capability language only.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest
from pytest_bdd import given, parsers, then, when

from kairix.agents.mcp.server import tool_recommend
from kairix.use_cases.recommend import RecommendDeps
from tests.fakes import FakeSearchPipeline

pytestmark = pytest.mark.bdd


@dataclass
class _McpRecommendState:
    flag_on: bool = False
    has_contradict: bool = False
    envelope: dict[str, Any] = field(default_factory=dict)


@pytest.fixture
def _mcp_recommend_state() -> _McpRecommendState:
    return _McpRecommendState()


def _deps_for(state: _McpRecommendState) -> RecommendDeps:
    rows = []
    catalogue: list[dict[str, Any]] = []
    if state.has_contradict:
        rows.append(
            FakeSearchPipeline.make_chunk_row(
                path="capability://kairix/contradict",
                title="contradict",
                content="Check new content against existing knowledge for conflicts.",
            )
        )
        catalogue.append(
            {
                "name": "contradict",
                "mcp_tool": "contradict",
                "cli": "kairix contradict",
                "category": "synthesis",
                "when_to_use": "Check for conflicts.",
            }
        )
    fake = FakeSearchPipeline(scripted_results=rows)
    return RecommendDeps(
        search_fn=lambda **kw: fake.search(**kw),
        catalogue_fn=lambda: catalogue,
        correlation_id_fn=lambda: "cid",
    )


@given("the recommender is turned on for the MCP surface")
def _flag_on(_mcp_recommend_state: _McpRecommendState) -> None:
    _mcp_recommend_state.flag_on = True


@given("the recommender is turned off for the MCP surface")
def _flag_off(_mcp_recommend_state: _McpRecommendState) -> None:
    _mcp_recommend_state.flag_on = False


@given("the MCP toolset includes a way to check content for conflicts")
def _has_contradict(_mcp_recommend_state: _McpRecommendState) -> None:
    _mcp_recommend_state.has_contradict = True


@when(parsers.parse('the agent asks the recommend tool which capability fits "{task}"'))
def _agent_asks(_mcp_recommend_state: _McpRecommendState, task: str) -> None:
    _mcp_recommend_state.envelope = tool_recommend(
        task=task,
        deps=_deps_for(_mcp_recommend_state),
        flag_reader=lambda: _mcp_recommend_state.flag_on,
    )


@then("the tool returns the conflict-checking capability first")
def _returns_contradict_first(_mcp_recommend_state: _McpRecommendState) -> None:
    recs = _mcp_recommend_state.envelope["recommendations"]
    assert recs, "expected at least one recommendation"
    assert recs[0]["name"] == "contradict", f"expected contradict first; got {recs!r}"


@then("the tool response reports no error")
def _no_error(_mcp_recommend_state: _McpRecommendState) -> None:
    assert _mcp_recommend_state.envelope["error"] == ""


@then("the tool response says the recommender is disabled")
def _says_disabled(_mcp_recommend_state: _McpRecommendState) -> None:
    assert _mcp_recommend_state.envelope["recommendations"] == []
    assert "recommender is disabled" in _mcp_recommend_state.envelope["error"]

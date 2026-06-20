"""Step definitions for cli_recommend.feature.

F46-clean: every scenario composes through the public CLI surface
(``kairix.use_cases.recommend.main``) with deps + flag_reader injected
through the public seams — no direct pipeline construction, no
monkeypatching (F1), no env vars (F2). F13-clean: scenarios speak in
agent/tool language, never implementation symbols.
"""

from __future__ import annotations

import io
import json
from dataclasses import dataclass, field
from typing import Any

import pytest
from pytest_bdd import given, parsers, then, when

from kairix.use_cases.recommend import RecommendDeps
from kairix.use_cases.recommend import main as recommend_main
from tests.fakes import FakeSearchPipeline

pytestmark = pytest.mark.bdd


@dataclass
class _RecommendState:
    """Per-scenario state — fresh on every scenario."""

    flag_on: bool = False
    has_contradict: bool = False
    stdout: str = ""
    stderr: str = ""
    exit_code: int = 0
    envelope: dict[str, Any] = field(default_factory=dict)


@pytest.fixture
def _recommend_state() -> _RecommendState:
    return _RecommendState()


def _deps_for(state: _RecommendState) -> RecommendDeps:
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


def _run(state: _RecommendState, task: str) -> None:
    out, err = io.StringIO(), io.StringIO()
    state.exit_code = recommend_main(
        [task, "--json"],
        out=out,
        err=err,
        deps=_deps_for(state),
        flag_reader=lambda: state.flag_on,
    )
    state.stdout = out.getvalue()
    state.stderr = err.getvalue()
    state.envelope = json.loads(state.stdout) if state.stdout.strip() else {}


# ---------------------------------------------------------------------------
# Givens
# ---------------------------------------------------------------------------


@given("the recommender is turned on")
def _flag_on(_recommend_state: _RecommendState) -> None:
    _recommend_state.flag_on = True


@given("the recommender is turned off")
def _flag_off(_recommend_state: _RecommendState) -> None:
    _recommend_state.flag_on = False


@given("the team's toolset includes a way to check content for conflicts")
def _has_contradict(_recommend_state: _RecommendState) -> None:
    _recommend_state.has_contradict = True


# ---------------------------------------------------------------------------
# Whens
# ---------------------------------------------------------------------------


@when(parsers.parse('the agent asks which tool fits "{task}"'))
def _agent_asks(_recommend_state: _RecommendState, task: str) -> None:
    _run(_recommend_state, task)


# ---------------------------------------------------------------------------
# Thens
# ---------------------------------------------------------------------------


@then("the response ranks the conflict-checking tool first")
def _ranks_contradict_first(_recommend_state: _RecommendState) -> None:
    recs = _recommend_state.envelope["recommendations"]
    assert recs, "expected at least one recommendation"
    assert recs[0]["name"] == "contradict", f"expected contradict first; got {recs!r}"


@then("the response includes a ready-to-call invocation for it")
def _has_invocation(_recommend_state: _RecommendState) -> None:
    rec = _recommend_state.envelope["recommendations"][0]
    assert rec["cli"] == "kairix contradict"
    assert rec["mcp_tool"] == "contradict"


@then("the recommend response reports no error")
def _no_error(_recommend_state: _RecommendState) -> None:
    assert _recommend_state.exit_code == 0, f"stderr: {_recommend_state.stderr!r}"
    assert _recommend_state.envelope["error"] == ""


@then("the recommend response says the recommender is disabled")
def _says_disabled(_recommend_state: _RecommendState) -> None:
    assert _recommend_state.exit_code == 1
    assert "recommender is disabled" in _recommend_state.stderr
    assert _recommend_state.envelope["recommendations"] == []

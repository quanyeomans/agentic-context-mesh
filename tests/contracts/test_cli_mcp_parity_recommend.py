"""Contract: CLI ↔ MCP parity for the ``recommend`` capability (Spec A).

The recommender ships one use case (``run_recommend``) behind two thin
adapters: the CLI ``kairix recommend`` (``kairix.use_cases.recommend.main``,
``--json``) and the MCP ``recommend_capabilities`` tool
(``kairix.agents.mcp.server.tool_recommend``). This contract proves the two
adapters return the SAME recommendation envelope for the same task — the
CLI↔MCP parity invariant.

Both adapters are driven through their public surfaces with the SAME
injected ``RecommendDeps`` (a ``FakeSearchPipeline`` + fake catalogue) and
the flag forced ON via the ``flag_reader`` seam, so the comparison isolates
the adapter wiring, not the retrieval backend (F1/F2/F5-clean — no @patch,
no env vars, public surface only).
"""

from __future__ import annotations

import io
import json

import pytest

from kairix.agents.mcp.server import tool_recommend
from kairix.use_cases.recommend import RecommendDeps
from kairix.use_cases.recommend import main as recommend_main
from tests.fakes import FakeSearchPipeline

pytestmark = pytest.mark.contract

_TASK = "I need to check this against what we already know"


def _shared_deps() -> RecommendDeps:
    """Deps both adapters share — one kairix-tool hit + its catalogue row."""
    fake = FakeSearchPipeline(
        scripted_results=[
            FakeSearchPipeline.make_chunk_row(
                path="capability://kairix/contradict",
                title="contradict",
                content="Check new content against existing knowledge for conflicts.",
            ),
        ]
    )
    return RecommendDeps(
        search_fn=lambda **kw: fake.search(**kw),
        catalogue_fn=lambda: [
            {
                "name": "contradict",
                "mcp_tool": "contradict",
                "cli": "kairix contradict",
                "category": "synthesis",
                "when_to_use": "Check for conflicts.",
            },
        ],
        correlation_id_fn=lambda: "fixed-id",
    )


def _cli_envelope() -> dict:
    out, err = io.StringIO(), io.StringIO()
    code = recommend_main(
        [_TASK, "--json"],
        out=out,
        err=err,
        deps=_shared_deps(),
        flag_reader=lambda: True,
    )
    assert code == 0, f"CLI exited {code}; stderr={err.getvalue()!r}"
    return json.loads(out.getvalue())


def _mcp_envelope() -> dict:
    return tool_recommend(task=_TASK, deps=_shared_deps(), flag_reader=lambda: True)


def test_cli_and_mcp_return_equivalent_recommendations() -> None:
    """Same task → byte-identical recommendation envelopes through both adapters."""
    cli = _cli_envelope()
    mcp = _mcp_envelope()

    assert cli == mcp, f"CLI and MCP envelopes diverged:\nCLI={cli!r}\nMCP={mcp!r}"
    # And the shared content is a real recommendation, not an empty match.
    assert cli["error"] == ""
    assert [r["name"] for r in cli["recommendations"]] == ["contradict"]


def test_cli_and_mcp_agree_when_disabled() -> None:
    """Flag OFF → both adapters return the same disabled envelope."""
    out, err = io.StringIO(), io.StringIO()
    recommend_main([_TASK, "--json"], out=out, err=err, deps=_shared_deps(), flag_reader=lambda: False)
    cli = json.loads(out.getvalue())
    mcp = tool_recommend(task=_TASK, deps=_shared_deps(), flag_reader=lambda: False)

    assert cli == mcp
    assert cli["recommendations"] == []
    assert "recommender is disabled" in cli["error"]


def test_cli_main_calls_run_recommend() -> None:
    """The CLI adapter delegates to the shared ``run_recommend`` use case."""
    import inspect

    from kairix.use_cases import recommend as uc

    src = inspect.getsource(uc.main)
    assert "run_recommend(" in src


def test_mcp_tool_recommend_calls_run_recommend() -> None:
    """The MCP adapter delegates to the shared ``run_recommend`` use case."""
    import inspect

    from kairix.agents.mcp import server

    src = inspect.getsource(server.tool_recommend)
    assert "run_recommend(" in src
    assert "from kairix.use_cases.recommend import" in src


def test_kairix_recommend_command_is_registered() -> None:
    from kairix.cli import COMMANDS

    assert "recommend" in COMMANDS
    assert COMMANDS["recommend"][0] == "kairix.use_cases.recommend"


def test_recommend_capability_row_is_in_the_catalogue() -> None:
    """The recommender is itself discoverable via ``tool_capabilities()``.

    Per design §4.2, a ``_cap(name="recommend", ...)`` row makes the
    recommender callable-by-discovery (an agent introspecting the catalogue
    finds it) AND feeds Feeder 1's corpus build (which reads
    ``tool_capabilities()``). The row carries the canonical MCP tool name +
    CLI invocation + a when_to_use trigger.

    Sabotage anchor (executed mutate -> fail -> restore): removing the
    ``_cap(name="recommend", ...)`` row from ``tool_capabilities()`` makes
    this test fail on the ``"recommend" in by_name`` assertion.
    """
    from kairix.agents.mcp.server import RECOMMEND_CAPABILITIES_TOOL_NAME, tool_capabilities

    by_name = {c["name"]: c for c in tool_capabilities()["capabilities"]}
    assert "recommend" in by_name, "recommender must be discoverable in the capability catalogue"
    row = by_name["recommend"]
    assert row["mcp_tool"] == RECOMMEND_CAPABILITIES_TOOL_NAME
    assert row["cli"] == "kairix recommend"
    assert row.get("when_to_use", "").strip(), "recommend row must advertise when to reach for it"

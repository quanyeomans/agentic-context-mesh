"""Contract: CLI ↔ MCP parity for the ``research`` operation (Phase 3d of #168)."""

from __future__ import annotations

import inspect
import typing

import pytest


@pytest.mark.contract
def test_cli_main_calls_run_research_use_case() -> None:
    from kairix.agents.research import cli

    src = inspect.getsource(cli.main)
    assert "run_research_use_case(" in src


@pytest.mark.contract
def test_mcp_tool_research_calls_use_case() -> None:
    from kairix.agents.mcp import server

    src = inspect.getsource(server.tool_research)
    assert "run_research_use_case(" in src
    assert "from kairix.use_cases.research import" in src


@pytest.mark.contract
def test_mcp_tool_research_does_not_call_orchestrator_directly() -> None:
    from kairix.agents.mcp import server

    src = inspect.getsource(server.tool_research)
    assert "from kairix.agents.research.graph import" not in src


@pytest.mark.contract
def test_use_case_returns_research_output() -> None:
    from kairix.use_cases.research import ResearchOutput, run_research_use_case

    hints = typing.get_type_hints(run_research_use_case)
    assert hints.get("return") is ResearchOutput


@pytest.mark.contract
def test_envelope_keys_match_research_output() -> None:
    from kairix.use_cases import research as uc

    src = inspect.getsource(uc.research_output_to_envelope)
    for key in ("query", "synthesis", "retrieved_chunks", "gaps", "confidence", "turns", "error"):
        assert f'"{key}"' in src


@pytest.mark.contract
def test_kairix_research_command_is_registered() -> None:
    from kairix.cli import COMMANDS

    assert "research" in COMMANDS
    assert COMMANDS["research"][0] == "kairix.agents.research.cli"


@pytest.mark.contract
def test_cli_argparse_exposes_every_mcp_user_facing_arg() -> None:
    """CLI argparse must expose every kwarg that MCP tool_research exposes.

    Production sweep 2026-06-03 surfaced the divergence: MCP exposed
    ``agent`` but CLI silently rejected it. This contract pins the
    argument-shape parity, not just "both call the use case".

    Sabotage proof: delete the ``--agent`` argparse line from
    ``kairix.agents.research.cli.build_parser`` — this test fails with
    ``agent`` reported as missing from CLI surface. Restored.

    Exclusion ``deps`` is the test-DI seam (private to callers); not
    relevant to operator-facing arg shape.
    """
    from kairix.agents.mcp.server import tool_research
    from kairix.agents.research import cli

    mcp_params = set(inspect.signature(tool_research).parameters) - {"deps"}
    parser = cli.build_parser()
    cli_dests = {action.dest for action in parser._actions if action.dest not in {"help"}}

    missing = mcp_params - cli_dests
    assert not missing, (
        f"CLI argparse missing MCP-equivalent args: {sorted(missing)}. "
        f"CLI dests: {sorted(cli_dests)}. MCP params: {sorted(mcp_params)}."
    )

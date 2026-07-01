"""Contract: CLI ↔ MCP parity for the ``expand`` operation (PLA-268)."""

from __future__ import annotations

import inspect
import typing

import pytest


@pytest.mark.contract
def test_cli_main_calls_run_expand() -> None:
    from kairix.use_cases import expand

    src = inspect.getsource(expand.main)
    assert "run_expand(" in src


@pytest.mark.contract
def test_mcp_tool_expand_calls_use_case() -> None:
    from kairix.agents.mcp import server

    src = inspect.getsource(server.tool_expand)
    assert "run_expand(" in src
    assert "from kairix.use_cases.expand import" in src


@pytest.mark.contract
def test_use_case_returns_expand_output() -> None:
    from kairix.use_cases.expand import ExpandOutput, run_expand

    hints = typing.get_type_hints(run_expand)
    assert hints.get("return") is ExpandOutput


@pytest.mark.contract
def test_envelope_keys_match_expand_output() -> None:
    """Pin the envelope key set returned by ``expand_output_to_envelope``."""
    from kairix.use_cases.expand import ExpandOutput, expand_output_to_envelope

    envelope = expand_output_to_envelope(ExpandOutput(source_uri="u", matched_seq=0))
    assert set(envelope.keys()) == {
        "source_uri",
        "matched_seq",
        "chunks",
        "total_tokens",
        "no_finer_chunks",
        "error",
    }


@pytest.mark.contract
def test_kairix_expand_command_is_registered() -> None:
    from kairix.cli import COMMANDS

    assert "expand" in COMMANDS
    assert COMMANDS["expand"][0] == "kairix.use_cases.expand"


@pytest.mark.contract
def test_cli_argparse_exposes_every_mcp_user_facing_arg() -> None:
    """CLI argparse must expose every kwarg that MCP ``tool_expand`` exposes.

    Sabotage proof: delete the ``--token-budget`` argparse line from
    ``kairix.use_cases.expand.build_parser`` — this test fails with
    ``token_budget`` reported as missing from the CLI surface.

    Exclusion ``deps`` is the test-DI seam; not operator-facing arg shape.
    """
    from kairix.agents.mcp.server import tool_expand
    from kairix.use_cases import expand

    mcp_params = set(inspect.signature(tool_expand).parameters) - {"deps"}
    parser = expand.build_parser()
    cli_dests = {action.dest for action in parser._actions if action.dest not in {"help"}}

    missing = mcp_params - cli_dests
    assert not missing, (
        f"CLI argparse missing MCP-equivalent args: {sorted(missing)}. "
        f"CLI dests: {sorted(cli_dests)}. MCP params: {sorted(mcp_params)}."
    )

"""Contract: CLI ↔ MCP parity for the ``search`` operation (Phase 2 of #168)."""

from __future__ import annotations

import inspect
import typing

import pytest


@pytest.mark.contract
def test_cli_main_calls_run_search_use_case() -> None:
    from kairix.core.search import cli

    src = inspect.getsource(cli)
    assert "from kairix.use_cases.search import" in src
    assert "run_search(" in src


@pytest.mark.contract
def test_mcp_tool_search_calls_run_search_use_case() -> None:
    from kairix.agents.mcp import server

    src = inspect.getsource(server.tool_search)
    assert "from kairix.use_cases.search import run_search" in src
    assert "run_search(" in src


@pytest.mark.contract
def test_cli_does_not_drive_search_pipeline_directly() -> None:
    """CLI must NOT bypass the use case to call SearchPipeline.search itself.

    Pre-Phase 2, both surfaces called ``pipeline.search`` directly with
    their own intent classification + budget inference + entity-card
    augmentation. The use case now owns all three.
    """
    from kairix.core.search import cli

    src = inspect.getsource(cli)
    assert "pipeline.search" not in src, "CLI bypasses run_search — see #168 Phase 2"
    assert "build_search_pipeline" not in src, "CLI builds its own pipeline — must go via run_search"


@pytest.mark.contract
def test_mcp_tool_search_does_not_drive_pipeline_directly() -> None:
    from kairix.agents.mcp import server

    src = inspect.getsource(server.tool_search)
    assert "build_search_pipeline" not in src
    assert "_fetch_entity_card" not in src
    assert "_infer_budget" not in src


@pytest.mark.contract
def test_use_case_returns_search_output_dataclass() -> None:
    from kairix.use_cases.search import SearchOutput, run_search

    hints = typing.get_type_hints(run_search)
    assert hints.get("return") is SearchOutput


@pytest.mark.contract
def test_mcp_envelope_keys_match_search_output_fields() -> None:
    """The MCP JSON envelope keys are exactly the use case's SearchOutput fields.

    The keys live in ``search_output_to_envelope`` (the shared projection
    helper); the MCP adapter ``tool_search`` calls it. Both surfaces (CLI
    --json and MCP) reach it from different directions.

    PR 2.2 / #421 moved the envelope keys behind module-level
    constants (F17), so source-grep would now miss them. Build a
    representative ``SearchOutput`` instead and assert against the
    emitted envelope dict — a stronger contract than text-matching.
    """
    from kairix.use_cases.search import SearchHit, SearchOutput, search_output_to_envelope

    out = SearchOutput(
        query="q",
        intent="semantic",
        results=[
            SearchHit(
                path="p",
                title="t",
                snippet="s",
                score=0.5,
                tier="vector",
                tokens=3,
                collection="agent-alpha",
            ),
        ],
    )
    env = search_output_to_envelope(out)
    for key in (
        "query",
        "intent",
        "results",
        "bm25_count",
        "vec_count",
        "fused_count",
        "vec_failed",
        "total_tokens",
        "latency_ms",
        "error",
    ):
        assert key in env, f"envelope missing key {key!r}: {sorted(env.keys())}"
    assert len(env["results"]) == 1
    rendered_hit = env["results"][0]
    for hit_key in ("path", "title", "snippet", "score", "tier", "tokens", "collection"):
        assert hit_key in rendered_hit, f"envelope hit missing key {hit_key!r}: {sorted(rendered_hit.keys())}"


@pytest.mark.contract
def test_mcp_search_signature_exposes_limit() -> None:
    """Phase 2 fixes the drift where MCP lacked ``limit``.

    Both surfaces must accept ``limit`` so an agent can ask for the
    same amount of context as a CLI operator.
    """
    from kairix.agents.mcp.server import tool_search

    params = set(inspect.signature(tool_search).parameters)
    assert "limit" in params, "tool_search must expose limit (Phase 2 of #168)"


@pytest.mark.contract
def test_mcp_search_signature_exposes_collection() -> None:
    """The MCP search tool must expose the same single-collection scope as CLI."""
    from kairix.agents.mcp.server import tool_search

    params = set(inspect.signature(tool_search).parameters)
    assert "collection" in params, "tool_search must expose collection for scoped retrieval"


@pytest.mark.contract
def test_both_surfaces_expose_max_tier() -> None:
    """PLA-270 — the tiered-context ceiling must be requestable from CLI AND MCP.

    The MCP ``tool_search`` exposes ``max_tier`` as a parameter; the CLI
    parser exposes the parallel ``--max-tier`` flag. Without both, an agent
    and an operator can't ask for the same cheapest-sufficient tier.
    """
    from kairix.agents.mcp.server import tool_search
    from kairix.core.search.cli import build_parser

    assert "max_tier" in set(inspect.signature(tool_search).parameters)

    parsed = build_parser().parse_args(["q", "--max-tier", "L0"])
    assert parsed.max_tier == "L0"

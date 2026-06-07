"""Step definitions for ``research_through_warm_mcp.feature``.

PR 2.5 / #421 — research envelope-to-text composer.

Composition rule (F46): steps drive through the CLI ``main`` entry
point with ``deps=ResearchDeps(...)`` injected; the envelope helpers
(``research_output_to_envelope`` + ``ResearchOutput.from_envelope``)
are the public seam tested directly — no pipeline / strategy
construction.
"""

from __future__ import annotations

import io
import json
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass, field
from typing import Any

import pytest
from pytest_bdd import given, parsers, scenarios, then, when

from kairix.agents.research.cli import format_text
from kairix.agents.research.cli import main as research_main
from kairix.use_cases.research import ResearchDeps, ResearchOutput, research_output_to_envelope

pytestmark = pytest.mark.bdd

scenarios("../features/research_through_warm_mcp.feature")


@dataclass
class _ResearchWarmCtx:
    original: ResearchOutput | None = None
    rebuilt: ResearchOutput | None = None
    deps: ResearchDeps | None = None
    stdout: str = ""
    stderr: str = ""
    exit_code: int = 0
    parsed_envelope: dict[str, Any] = field(default_factory=dict)


@pytest.fixture
def research_warm_ctx() -> _ResearchWarmCtx:
    return _ResearchWarmCtx()


# ---------------------------------------------------------------------------
# Scenario 1 — round-trip parity
# ---------------------------------------------------------------------------


@given(
    parsers.parse('a research result with synthesis "{synthesis}" and confidence {confidence:f} for query "{query}"')
)
def _seed_research_result(
    research_warm_ctx: _ResearchWarmCtx,
    synthesis: str,
    confidence: float,
    query: str,
) -> None:
    research_warm_ctx.original = ResearchOutput(
        query=query,
        synthesis=synthesis,
        confidence=confidence,
        turns=1,
    )


@when("the research result is converted to an MCP envelope and back via from_envelope")
def _roundtrip_research_envelope(research_warm_ctx: _ResearchWarmCtx) -> None:
    assert research_warm_ctx.original is not None
    envelope = research_output_to_envelope(research_warm_ctx.original)
    research_warm_ctx.rebuilt = ResearchOutput.from_envelope(envelope)


@then("the round-tripped research text output is byte-identical to the original")
def _assert_research_text_byte_identical(research_warm_ctx: _ResearchWarmCtx) -> None:
    assert research_warm_ctx.original is not None
    assert research_warm_ctx.rebuilt is not None
    original_text = format_text(research_warm_ctx.original)
    rebuilt_text = format_text(research_warm_ctx.rebuilt)
    assert original_text == rebuilt_text, (
        f"warm-MCP text path drifted from in-process:\n"
        f"--- in-process ---\n{original_text!r}\n--- warm-MCP ---\n{rebuilt_text!r}"
    )


# ---------------------------------------------------------------------------
# Scenario 2 — --json flag on the CLI
# ---------------------------------------------------------------------------


@given(parsers.parse('a research use case that returns synthesis "{synthesis}" for query "{query}"'))
def _seed_research_deps(
    research_warm_ctx: _ResearchWarmCtx,
    synthesis: str,
    query: str,
) -> None:
    # F19: ``**_kw`` swallows the orchestrator-shape kwargs the use case
    # forwards (``max_turns`` etc.) without declaring each unused name.
    research_warm_ctx.deps = ResearchDeps(
        research_fn=lambda **_kw: {
            "query": _kw.get("query", query),
            "synthesis": synthesis,
            "turns": 1,
            "confidence": 0.5,
        }
    )


@when("the operator runs the research CLI with json mode")
def _run_research_json(research_warm_ctx: _ResearchWarmCtx) -> None:
    out_buf = io.StringIO()
    err_buf = io.StringIO()
    with redirect_stdout(out_buf), redirect_stderr(err_buf):
        research_warm_ctx.exit_code = research_main(["qq", "--json"], deps=research_warm_ctx.deps)
    research_warm_ctx.stdout = out_buf.getvalue()
    research_warm_ctx.stderr = err_buf.getvalue()


@then(parsers.parse("research stdout is valid JSON containing keys query, synthesis, and error"))
def _assert_research_stdout_is_envelope_json(research_warm_ctx: _ResearchWarmCtx) -> None:
    try:
        parsed = json.loads(research_warm_ctx.stdout)
    except json.JSONDecodeError as exc:
        pytest.fail(f"stdout was not valid JSON: {exc}\n--- stdout ---\n{research_warm_ctx.stdout!r}")
    assert isinstance(parsed, dict), f"envelope must be a dict, got {type(parsed).__name__}"
    for key in ("query", "synthesis", "error"):
        assert key in parsed, f"envelope missing key {key!r}: {sorted(parsed.keys())}"
    research_warm_ctx.parsed_envelope = parsed


@then(parsers.parse("the research CLI exits with status {code:d}"))
def _assert_research_exit(research_warm_ctx: _ResearchWarmCtx, code: int) -> None:
    assert research_warm_ctx.exit_code == code, (
        f"expected exit {code}, got {research_warm_ctx.exit_code}; "
        f"stdout={research_warm_ctx.stdout[:200]!r} stderr={research_warm_ctx.stderr[:200]!r}"
    )

"""Step definitions for ``timeline_through_warm_mcp.feature``.

PR 2.7 / #421 — timeline envelope-to-text composer + ``--json`` flag.

Composition rule (F46): steps drive through the CLI ``main`` entry
point with ``timeline_runner=...`` injected; the envelope helpers
(``timeline_output_to_envelope`` + ``TimelineResult.from_envelope``)
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

from kairix.core.temporal.cli import format_header, format_results
from kairix.core.temporal.cli import main as timeline_main
from kairix.use_cases.timeline import (
    TimelineHit,
    TimelineResult,
    timeline_output_to_envelope,
)

pytestmark = pytest.mark.bdd

scenarios("../features/timeline_through_warm_mcp.feature")


@dataclass
class _TimelineWarmCtx:
    original: TimelineResult | None = None
    rebuilt: TimelineResult | None = None
    stdout: str = ""
    stderr: str = ""
    exit_code: int = 0
    parsed_envelope: dict[str, Any] = field(default_factory=dict)
    fixture_runner_result: TimelineResult | None = None


@pytest.fixture
def timeline_warm_ctx() -> _TimelineWarmCtx:
    return _TimelineWarmCtx()


# ---------------------------------------------------------------------------
# Scenario 1 — empty timeline parity
# ---------------------------------------------------------------------------


@given(parsers.parse('a timeline result with no hits for query "{query}"'))
def _seed_empty_timeline(timeline_warm_ctx: _TimelineWarmCtx, query: str) -> None:
    timeline_warm_ctx.original = TimelineResult(
        original_query=query,
        rewritten_query=query,
        is_temporal=False,
        fell_back=True,
        time_window={},
        results=[],
    )


# ---------------------------------------------------------------------------
# Scenario 2 — multi-agent dated hits
# ---------------------------------------------------------------------------


@given("a timeline result with hits across agents alpha, beta, and gamma in April 2026")
def _seed_multi_agent_timeline(timeline_warm_ctx: _TimelineWarmCtx) -> None:
    timeline_warm_ctx.original = TimelineResult(
        original_query="what happened in April 2026",
        rewritten_query="what happened in April 2026 (start=2026-04-01 end=2026-04-30)",
        is_temporal=True,
        fell_back=False,
        time_window={"start": "2026-04-01", "end": "2026-04-30"},
        results=[
            TimelineHit(
                path="agent-alpha/boards/sprint.md",
                title="Card alpha",
                snippet="alpha body",
                score=2.0,
                date="2026-04-10",
                chunk_type="board_card",
            ),
            TimelineHit(
                path="agent-beta/memory/april.md",
                title="Section beta",
                snippet="beta body",
                score=1.5,
                date="2026-04-20",
                chunk_type="memory_section",
            ),
            TimelineHit(
                path="agent-gamma/boards/done.md",
                title="Card gamma",
                snippet="gamma body",
                score=1.1,
                date="2026-04-28",
                chunk_type="board_card",
            ),
        ],
    )


@then("the rebuilt timeline names every agent path")
def _assert_every_agent_path(timeline_warm_ctx: _TimelineWarmCtx) -> None:
    assert timeline_warm_ctx.rebuilt is not None
    rendered = format_results(timeline_warm_ctx.rebuilt)
    for path in (
        "agent-alpha/boards/sprint.md",
        "agent-beta/memory/april.md",
        "agent-gamma/boards/done.md",
    ):
        assert path in rendered, f"rebuilt timeline missing path {path!r}: {rendered!r}"


# ---------------------------------------------------------------------------
# Scenario 3 — date filter window
# ---------------------------------------------------------------------------


@given(parsers.parse("a timeline result with window {start} to {end} and one hit"))
def _seed_date_filter_timeline(timeline_warm_ctx: _TimelineWarmCtx, start: str, end: str) -> None:
    timeline_warm_ctx.original = TimelineResult(
        original_query="topic last week",
        rewritten_query="topic last week (rewritten)",
        is_temporal=True,
        fell_back=False,
        time_window={"start": start, "end": end},
        results=[
            TimelineHit(
                path="agent-alpha/notes.md",
                title="Note",
                snippet="some content",
                score=0.9,
                date="2026-06-01",
                chunk_type="memory_section",
            ),
        ],
    )


@then("the round-tripped timeline header carries both window dates")
def _assert_header_carries_window(timeline_warm_ctx: _TimelineWarmCtx) -> None:
    assert timeline_warm_ctx.original is not None
    assert timeline_warm_ctx.rebuilt is not None
    header_text = format_header(timeline_warm_ctx.rebuilt, limit=5)
    expected_start = timeline_warm_ctx.original.time_window.get("start", "")
    expected_end = timeline_warm_ctx.original.time_window.get("end", "")
    assert expected_start in header_text, f"header missing start {expected_start!r}: {header_text!r}"
    assert expected_end in header_text, f"header missing end {expected_end!r}: {header_text!r}"


# ---------------------------------------------------------------------------
# Shared steps for scenarios 1-3
# ---------------------------------------------------------------------------


@when("the timeline is converted to an MCP envelope and back via from_envelope")
def _roundtrip_envelope(timeline_warm_ctx: _TimelineWarmCtx) -> None:
    assert timeline_warm_ctx.original is not None
    envelope = timeline_output_to_envelope(timeline_warm_ctx.original)
    timeline_warm_ctx.rebuilt = TimelineResult.from_envelope(envelope)


@then("the round-tripped timeline text output is byte-identical to the original")
def _assert_text_byte_identical(timeline_warm_ctx: _TimelineWarmCtx) -> None:
    assert timeline_warm_ctx.original is not None
    assert timeline_warm_ctx.rebuilt is not None
    original_header = format_header(timeline_warm_ctx.original, limit=10)
    rebuilt_header = format_header(timeline_warm_ctx.rebuilt, limit=10)
    assert original_header == rebuilt_header, (
        f"warm-MCP header drifted from in-process:\n"
        f"--- in-process ---\n{original_header!r}\n--- warm-MCP ---\n{rebuilt_header!r}"
    )
    original_results = format_results(timeline_warm_ctx.original)
    rebuilt_results = format_results(timeline_warm_ctx.rebuilt)
    assert original_results == rebuilt_results, (
        f"warm-MCP result body drifted from in-process:\n"
        f"--- in-process ---\n{original_results!r}\n--- warm-MCP ---\n{rebuilt_results!r}"
    )


# ---------------------------------------------------------------------------
# Scenario 4 — --json mode on the CLI
# ---------------------------------------------------------------------------


@given(parsers.parse('a timeline use case returning a fixed two-hit result for query "{query}"'))
def _seed_fixed_runner_result(timeline_warm_ctx: _TimelineWarmCtx, query: str) -> None:
    timeline_warm_ctx.fixture_runner_result = TimelineResult(
        original_query=query,
        rewritten_query=query,
        is_temporal=True,
        fell_back=False,
        time_window={"start": "2026-04-01", "end": "2026-04-30"},
        results=[
            TimelineHit(
                path="agent-alpha/notes.md",
                title="hit one",
                snippet="snippet one",
                score=1.0,
                date="2026-04-10",
                chunk_type="memory_section",
            ),
            TimelineHit(
                path="agent-beta/notes.md",
                title="hit two",
                snippet="snippet two",
                score=0.8,
                date="2026-04-20",
                chunk_type="memory_section",
            ),
        ],
    )


@when(parsers.parse('the operator runs the timeline CLI with json mode for query "{query}"'))
def _run_timeline_cli_json(timeline_warm_ctx: _TimelineWarmCtx, query: str) -> None:
    fixture = timeline_warm_ctx.fixture_runner_result
    assert fixture is not None

    # F19: argparse-style ``_``-prefixed unused kwargs allowed in
    # lambdas that match the protocol shape.
    def _fake_runner(_query: str, **_kw: Any) -> TimelineResult:
        return fixture

    out_buf = io.StringIO()
    err_buf = io.StringIO()
    try:
        with redirect_stdout(out_buf), redirect_stderr(err_buf):
            timeline_main([query, "--json"], timeline_runner=_fake_runner)
        timeline_warm_ctx.exit_code = 0
    except SystemExit as exc:  # NOSONAR — BDD step captures CLI exit code; reraising would defeat the test
        timeline_warm_ctx.exit_code = int(exc.code) if exc.code is not None else 0
    timeline_warm_ctx.stdout = out_buf.getvalue()
    timeline_warm_ctx.stderr = err_buf.getvalue()


@then("timeline stdout is valid JSON containing keys original_query, results, time_window, and error")
def _assert_stdout_is_envelope_json(timeline_warm_ctx: _TimelineWarmCtx) -> None:
    try:
        parsed = json.loads(timeline_warm_ctx.stdout)
    except json.JSONDecodeError as exc:
        pytest.fail(f"stdout was not valid JSON: {exc}\n--- stdout ---\n{timeline_warm_ctx.stdout!r}")
    assert isinstance(parsed, dict), f"envelope must be a dict, got {type(parsed).__name__}"
    for key in ("original_query", "results", "time_window", "error"):
        assert key in parsed, f"envelope missing key {key!r}: {sorted(parsed.keys())}"
    timeline_warm_ctx.parsed_envelope = parsed


@then(parsers.parse("the timeline CLI exits with status {code:d}"))
def _assert_exit(timeline_warm_ctx: _TimelineWarmCtx, code: int) -> None:
    assert timeline_warm_ctx.exit_code == code, (
        f"expected exit {code}, got {timeline_warm_ctx.exit_code}; "
        f"stdout={timeline_warm_ctx.stdout[:200]!r} "
        f"stderr={timeline_warm_ctx.stderr[:200]!r}"
    )

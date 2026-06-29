"""Step definitions for ``brief_through_warm_mcp.feature``.

PR 2.1 / #421 — brief envelope-to-text composer + ``--json`` flag.

Composition rule (F46): steps drive through the CLI ``main`` entry
point with ``deps=BriefDeps(...)`` injected; the envelope helpers
(``brief_output_to_envelope`` + ``BriefOutput.from_envelope``) are the
public seam tested directly — no pipeline / strategy construction.
"""

from __future__ import annotations

import io
import json
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from pytest_bdd import given, parsers, scenarios, then, when

from kairix.agents.briefing.cli import format_output
from kairix.agents.briefing.cli import main as brief_main
from kairix.core.health import HealthDeps
from kairix.use_cases.brief import BriefDeps, BriefOutput, brief_output_to_envelope

pytestmark = pytest.mark.bdd

scenarios("../features/brief_through_warm_mcp.feature")


@dataclass
class _BriefWarmCtx:
    original: BriefOutput | None = None
    rebuilt: BriefOutput | None = None
    deps: BriefDeps | None = None
    stdout: str = ""
    stderr: str = ""
    exit_code: int = 0
    parsed_envelope: dict[str, Any] = field(default_factory=dict)


@pytest.fixture
def brief_warm_ctx() -> _BriefWarmCtx:
    return _BriefWarmCtx()


def _healthy_health_deps() -> HealthDeps:
    return HealthDeps(
        secrets_loaded_fn=lambda: True,
        embed_backend_available_fn=lambda: True,
        bm25_index_available_fn=lambda: True,
        neo4j_available_fn=lambda: True,
    )


# ---------------------------------------------------------------------------
# Scenario 1 — round-trip parity
# ---------------------------------------------------------------------------


@given(parsers.parse('a brief result with content "{content}" written to "{path}"'))
def _seed_brief_result(brief_warm_ctx: _BriefWarmCtx, content: str, path: str) -> None:
    brief_warm_ctx.original = BriefOutput(
        agent="agent-alpha",
        content=content,
        path=path,
        preview=content,
    )


@when("the brief is converted to an MCP envelope and back via from_envelope")
def _roundtrip_envelope(brief_warm_ctx: _BriefWarmCtx) -> None:
    assert brief_warm_ctx.original is not None
    envelope = brief_output_to_envelope(brief_warm_ctx.original)
    brief_warm_ctx.rebuilt = BriefOutput.from_envelope(envelope)


@then("the round-tripped text output is byte-identical to the original")
def _assert_text_byte_identical(brief_warm_ctx: _BriefWarmCtx) -> None:
    assert brief_warm_ctx.original is not None
    assert brief_warm_ctx.rebuilt is not None
    original_text = format_output(brief_warm_ctx.original, print_full=False)
    rebuilt_text = format_output(brief_warm_ctx.rebuilt, print_full=False)
    assert original_text == rebuilt_text, (
        f"warm-MCP text path drifted from in-process:\n"
        f"--- in-process ---\n{original_text!r}\n--- warm-MCP ---\n{rebuilt_text!r}"
    )


# ---------------------------------------------------------------------------
# Scenario 2 — --json flag on the CLI
# ---------------------------------------------------------------------------


@given(parsers.parse('a brief use case that returns content "{content}" at path "{path}"'))
def _seed_brief_deps(brief_warm_ctx: _BriefWarmCtx, content: str, path: str) -> None:
    out_dir = Path(path).parent

    brief_warm_ctx.deps = BriefDeps(
        # F19: argparse-style ``_``-prefixed unused kwargs allowed in
        # lambdas that match the protocol shape.
        generate_fn=lambda _agent, **_kw: content,
        briefing_dir_fn=lambda: out_dir,
        config_fn=lambda: {"agents": {"builder": {"surfaces": [{"path": "memory/builder", "label": "memory"}]}}},
        health_deps=_healthy_health_deps(),
    )


@when("the operator runs the brief CLI with json mode for agent-alpha")
def _run_brief_json(brief_warm_ctx: _BriefWarmCtx) -> None:
    # ``builder`` is the canonical role label here (F32: role labels are
    # not real personal names); the injected ``config_fn`` declares its
    # surface so brief's surface check passes without touching disk.
    out_buf = io.StringIO()
    err_buf = io.StringIO()
    try:
        with redirect_stdout(out_buf), redirect_stderr(err_buf):
            brief_main(["builder", "--json"], deps=brief_warm_ctx.deps)
        brief_warm_ctx.exit_code = 0
    except SystemExit as exc:  # NOSONAR — BDD step captures CLI exit code; reraising would defeat the test
        brief_warm_ctx.exit_code = int(exc.code) if exc.code is not None else 0
    brief_warm_ctx.stdout = out_buf.getvalue()
    brief_warm_ctx.stderr = err_buf.getvalue()


@then(parsers.parse("stdout is valid JSON containing keys content, path, and error"))
def _assert_stdout_is_envelope_json(brief_warm_ctx: _BriefWarmCtx) -> None:
    try:
        parsed = json.loads(brief_warm_ctx.stdout)
    except json.JSONDecodeError as exc:
        pytest.fail(f"stdout was not valid JSON: {exc}\n--- stdout ---\n{brief_warm_ctx.stdout!r}")
    assert isinstance(parsed, dict), f"envelope must be a dict, got {type(parsed).__name__}"
    for key in ("content", "path", "error"):
        assert key in parsed, f"envelope missing key {key!r}: {sorted(parsed.keys())}"
    brief_warm_ctx.parsed_envelope = parsed


@then(parsers.parse("the brief CLI exits with status {code:d}"))
def _assert_exit(brief_warm_ctx: _BriefWarmCtx, code: int) -> None:
    assert brief_warm_ctx.exit_code == code, (
        f"expected exit {code}, got {brief_warm_ctx.exit_code}; "
        f"stdout={brief_warm_ctx.stdout[:200]!r} stderr={brief_warm_ctx.stderr[:200]!r}"
    )

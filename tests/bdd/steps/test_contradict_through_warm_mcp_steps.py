"""Step definitions for ``contradict_through_warm_mcp.feature``.

PR 2.6 / #421 — contradict envelope-to-text composer + ``--json`` flag.

Composition rule (F46): steps drive through the CLI ``main`` entry
point with ``deps=ContradictDeps(...)`` injected; the envelope helpers
(``contradict_output_to_envelope`` + ``ContradictOutput.from_envelope``)
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

from kairix.knowledge.contradict.cli import format_text
from kairix.knowledge.contradict.cli import main as contradict_main
from kairix.knowledge.contradict.detector import ContradictionReport
from kairix.use_cases.contradict import (
    ContradictDeps,
    ContradictionHit,
    ContradictOutput,
    contradict_output_to_envelope,
)
from tests.fakes import FakeLLMBackend

pytestmark = pytest.mark.bdd

scenarios("../features/contradict_through_warm_mcp.feature")


@dataclass
class _ContradictWarmCtx:
    original: ContradictOutput | None = None
    rebuilt: ContradictOutput | None = None
    deps: ContradictDeps | None = None
    stdout: str = ""
    stderr: str = ""
    exit_code: int = 0
    parsed_envelope: dict[str, Any] = field(default_factory=dict)


@pytest.fixture
def contradict_warm_ctx() -> _ContradictWarmCtx:
    return _ContradictWarmCtx()


# ---------------------------------------------------------------------------
# Scenario 1 — round-trip parity
# ---------------------------------------------------------------------------


@given(parsers.parse('a contradict result with one hit at path "{path}"'))
def _seed_contradict_result(contradict_warm_ctx: _ContradictWarmCtx, path: str) -> None:
    contradict_warm_ctx.original = ContradictOutput(
        content="The sky is green.",
        contradictions=[
            ContradictionHit(
                path=path,
                score=0.72,
                reason="Existing document states the sky is blue.",
                snippet="The sky is blue and clear during daylight hours.",
                category="direct",
                claim="sky color",
            ),
        ],
        has_contradictions=True,
    )


@when("the contradict result is converted to an MCP envelope and back via from_envelope")
def _roundtrip_envelope(contradict_warm_ctx: _ContradictWarmCtx) -> None:
    assert contradict_warm_ctx.original is not None
    envelope = contradict_output_to_envelope(contradict_warm_ctx.original)
    contradict_warm_ctx.rebuilt = ContradictOutput.from_envelope(envelope)


@then("the round-tripped text output is byte-identical to the original")
def _assert_text_byte_identical(contradict_warm_ctx: _ContradictWarmCtx) -> None:
    assert contradict_warm_ctx.original is not None
    assert contradict_warm_ctx.rebuilt is not None
    original_text = format_text(contradict_warm_ctx.original, top_k=5, threshold=0.45)
    rebuilt_text = format_text(contradict_warm_ctx.rebuilt, top_k=5, threshold=0.45)
    assert original_text == rebuilt_text, (
        f"warm-MCP text path drifted from in-process:\n"
        f"--- in-process ---\n{original_text!r}\n--- warm-MCP ---\n{rebuilt_text!r}"
    )


# ---------------------------------------------------------------------------
# Scenario 2 — --json flag on the CLI
# ---------------------------------------------------------------------------


@given("a contradict use case that returns no hits for the input content")
def _seed_contradict_deps(contradict_warm_ctx: _ContradictWarmCtx) -> None:
    def _check_fn(**_kwargs: Any) -> ContradictionReport:
        return ContradictionReport.of([])

    contradict_warm_ctx.deps = ContradictDeps(check_fn=_check_fn, llm_backend=FakeLLMBackend())


@when(parsers.parse('the operator runs the contradict CLI with json mode for content "{content}"'))
def _run_contradict_json(contradict_warm_ctx: _ContradictWarmCtx, content: str) -> None:
    out_buf = io.StringIO()
    err_buf = io.StringIO()
    try:
        with redirect_stdout(out_buf), redirect_stderr(err_buf):
            contradict_main(["check", content, "--json"], deps=contradict_warm_ctx.deps)
        contradict_warm_ctx.exit_code = 0
    except SystemExit as exc:  # NOSONAR — BDD step captures CLI exit code; reraising would defeat the test
        contradict_warm_ctx.exit_code = int(exc.code) if exc.code is not None else 0
    contradict_warm_ctx.stdout = out_buf.getvalue()
    contradict_warm_ctx.stderr = err_buf.getvalue()


@then("stdout is valid JSON containing keys content, contradictions, has_contradictions, and error")
def _assert_stdout_is_envelope_json(contradict_warm_ctx: _ContradictWarmCtx) -> None:
    try:
        parsed = json.loads(contradict_warm_ctx.stdout)
    except json.JSONDecodeError as exc:
        pytest.fail(f"stdout was not valid JSON: {exc}\n--- stdout ---\n{contradict_warm_ctx.stdout!r}")
    assert isinstance(parsed, dict), f"envelope must be a dict, got {type(parsed).__name__}"
    for key in ("content", "contradictions", "has_contradictions", "error"):
        assert key in parsed, f"envelope missing key {key!r}: {sorted(parsed.keys())}"
    contradict_warm_ctx.parsed_envelope = parsed


@then(parsers.parse("the contradict CLI exits with status {code:d}"))
def _assert_exit(contradict_warm_ctx: _ContradictWarmCtx, code: int) -> None:
    assert contradict_warm_ctx.exit_code == code, (
        f"expected exit {code}, got {contradict_warm_ctx.exit_code}; "
        f"stdout={contradict_warm_ctx.stdout[:200]!r} stderr={contradict_warm_ctx.stderr[:200]!r}"
    )

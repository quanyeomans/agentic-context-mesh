"""Step definitions for ``prep_through_warm_mcp.feature``.

PR 2.4 / #421 — prep envelope-to-text composer + ``--json`` flag.

Composition rule (F46): steps drive through the CLI ``main`` entry
point with ``deps=PrepDeps(...)`` injected; the envelope helpers
(``prep_output_to_envelope`` + ``PrepOutput.from_envelope``) are the
public seam tested directly — no pipeline / strategy construction.
"""

from __future__ import annotations

import io
import json
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass, field
from typing import Any

import pytest
from pytest_bdd import given, parsers, scenarios, then, when

from kairix.agents.prep.cli import format_text
from kairix.agents.prep.cli import main as prep_main
from kairix.use_cases.prep import PrepDeps, PrepOutput, prep_output_to_envelope

pytestmark = pytest.mark.bdd

scenarios("../features/prep_through_warm_mcp.feature")


@dataclass
class _PrepWarmCtx:
    original: PrepOutput | None = None
    rebuilt: PrepOutput | None = None
    deps: PrepDeps | None = None
    stdout: str = ""
    stderr: str = ""
    exit_code: int = 0
    parsed_envelope: dict[str, Any] = field(default_factory=dict)


@pytest.fixture
def prep_warm_ctx() -> _PrepWarmCtx:
    return _PrepWarmCtx()


# Lightweight search-result stand-ins for the use-case path. ``run_prep``
# reads ``budgeted.result.{title,path}`` and ``budgeted.content`` only —
# we keep these ad-hoc rather than pulling real pipeline types so the
# scenario stays focused on the envelope seam, not pipeline construction.


@dataclass
class _FakeInner:
    title: str = ""
    path: str = ""


@dataclass
class _FakeBudgeted:
    result: _FakeInner
    content: str = ""


@dataclass
class _FakeSearchResult:
    results: list[_FakeBudgeted] = field(default_factory=list)


# A snippet long enough to clear the ``_MIN_USEFUL_SNIPPET_CHARS`` floor
# inside ``_format_context``. Without this the context comes back empty
# and the chat path is short-circuited — the CLI then renders "No
# relevant documents..." instead of our injected summary, which makes
# the scenario's assertions noisy.
_LONG_SNIPPET = (
    "This document is a sample knowledge entry about the requested topic "
    "and contains enough detail to clear the prep snippet floor."
)


# ---------------------------------------------------------------------------
# Scenario 1 — round-trip parity
# ---------------------------------------------------------------------------


@given(parsers.parse('a prep result with summary "{summary}" and sources "{sources_csv}"'))
def _seed_prep_result(prep_warm_ctx: _PrepWarmCtx, summary: str, sources_csv: str) -> None:
    # PLA-274 — prep sources are resolvable SourceRef breadcrumbs; build one
    # per CSV entry (the entry is both the display path and the breadcrumb).
    from kairix.core.protocols import SourceRef

    sources = [SourceRef.of(path=s.strip()) for s in sources_csv.split(",") if s.strip()]
    prep_warm_ctx.original = PrepOutput(
        query="alpha topic",
        tier="l0",
        summary=summary,
        tokens=len(summary.split()),
        sources=sources,
    )


@when("the prep result is converted to an MCP envelope and back via from_envelope")
def _roundtrip_envelope(prep_warm_ctx: _PrepWarmCtx) -> None:
    assert prep_warm_ctx.original is not None
    envelope = prep_output_to_envelope(prep_warm_ctx.original)
    prep_warm_ctx.rebuilt = PrepOutput.from_envelope(envelope)


@then("the round-tripped prep text output is byte-identical to the original")
def _assert_prep_text_byte_identical(prep_warm_ctx: _PrepWarmCtx) -> None:
    assert prep_warm_ctx.original is not None
    assert prep_warm_ctx.rebuilt is not None
    original_text = format_text(prep_warm_ctx.original)
    rebuilt_text = format_text(prep_warm_ctx.rebuilt)
    assert original_text == rebuilt_text, (
        f"warm-MCP text path drifted from in-process:\n"
        f"--- in-process ---\n{original_text!r}\n--- warm-MCP ---\n{rebuilt_text!r}"
    )


# ---------------------------------------------------------------------------
# Scenario 2 — --json flag on the CLI
# ---------------------------------------------------------------------------


@given(parsers.parse('a prep use case that returns summary "{summary}" for query "{query}"'))
def _seed_prep_deps(prep_warm_ctx: _PrepWarmCtx, summary: str, query: str) -> None:
    # ``run_prep`` calls ``search(query=..., agent=..., scope=..., budget=...)``
    # then ``chat(messages=..., max_tokens=...)``. The fakes ignore the
    # specific kwargs and just return canned values.
    sr = _FakeSearchResult(
        results=[_FakeBudgeted(result=_FakeInner(title="doc-alpha", path="alpha.md"), content=_LONG_SNIPPET)],
    )
    prep_warm_ctx.deps = PrepDeps(
        search_fn=lambda **_kw: sr,
        chat_fn=lambda **_kw: summary,
    )
    # Track the query for the eventual envelope assertions.
    prep_warm_ctx.parsed_envelope = {"_seed_query": query}


@when("the operator runs the prep CLI with json mode")
def _run_prep_json(prep_warm_ctx: _PrepWarmCtx) -> None:
    query = prep_warm_ctx.parsed_envelope.get("_seed_query", "topic-q")
    out_buf = io.StringIO()
    err_buf = io.StringIO()
    try:
        with redirect_stdout(out_buf), redirect_stderr(err_buf):
            rc = prep_main([str(query), "--json"], deps=prep_warm_ctx.deps)
        prep_warm_ctx.exit_code = int(rc or 0)
    except SystemExit as exc:  # NOSONAR — BDD step captures CLI exit code; reraising would defeat the test
        prep_warm_ctx.exit_code = int(exc.code) if exc.code is not None else 0
    prep_warm_ctx.stdout = out_buf.getvalue()
    prep_warm_ctx.stderr = err_buf.getvalue()


@then("prep stdout is valid JSON containing keys query, tier, summary, and error")
def _assert_prep_stdout_envelope_json(prep_warm_ctx: _PrepWarmCtx) -> None:
    try:
        parsed = json.loads(prep_warm_ctx.stdout)
    except json.JSONDecodeError as exc:
        pytest.fail(f"stdout was not valid JSON: {exc}\n--- stdout ---\n{prep_warm_ctx.stdout!r}")
    assert isinstance(parsed, dict), f"envelope must be a dict, got {type(parsed).__name__}"
    for key in ("query", "tier", "summary", "error"):
        assert key in parsed, f"envelope missing key {key!r}: {sorted(parsed.keys())}"
    prep_warm_ctx.parsed_envelope = parsed


@then(parsers.parse("the prep CLI exits with status {code:d}"))
def _assert_prep_exit(prep_warm_ctx: _PrepWarmCtx, code: int) -> None:
    assert prep_warm_ctx.exit_code == code, (
        f"expected exit {code}, got {prep_warm_ctx.exit_code}; "
        f"stdout={prep_warm_ctx.stdout[:200]!r} stderr={prep_warm_ctx.stderr[:200]!r}"
    )

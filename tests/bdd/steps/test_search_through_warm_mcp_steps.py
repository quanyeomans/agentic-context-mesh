"""Step definitions for ``search_through_warm_mcp.feature``.

PR 2.2 / #421 — search envelope-to-text composer + ``--json`` flag.

Composition rule (F46): steps drive through the CLI ``main`` entry
point with ``deps=SearchDeps(...)`` injected; the envelope helpers
(``search_output_to_envelope`` + ``SearchOutput.from_envelope``) are
the public seam tested directly — no pipeline / strategy construction.
"""

from __future__ import annotations

import io
import json
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass, field
from typing import Any

import pytest
from pytest_bdd import given, parsers, scenarios, then, when

from kairix.core.health import HealthDeps
from kairix.core.search.cli import format_text
from kairix.core.search.cli import main as search_main
from kairix.core.search.intent import QueryIntent
from kairix.use_cases.search import SearchDeps, SearchHit, SearchOutput, search_output_to_envelope

pytestmark = pytest.mark.bdd

scenarios("../features/search_through_warm_mcp.feature")


@dataclass
class _SearchWarmCtx:
    original: SearchOutput | None = None
    rebuilt: SearchOutput | None = None
    deps: SearchDeps | None = None
    stdout: str = ""
    stderr: str = ""
    exit_code: int = 0
    parsed_envelope: dict[str, Any] = field(default_factory=dict)


@pytest.fixture
def search_warm_ctx() -> _SearchWarmCtx:
    return _SearchWarmCtx()


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


@given("a SearchOutput with 3 hits")
def _seed_search_output(search_warm_ctx: _SearchWarmCtx) -> None:
    search_warm_ctx.original = SearchOutput(
        query="agent-alpha quarterly review",
        intent="semantic",
        results=[
            SearchHit(
                path="/vault/agent-alpha/notes/q1.md",
                title="Q1 Review",
                snippet="Outcomes for the quarter.",
                score=0.91,
                tier="vector",
                tokens=8,
                collection="agent-alpha",
            ),
            SearchHit(
                path="/vault/shared/playbooks/review.md",
                title="Review Playbook",
                snippet="Steps the team runs every quarter.",
                score=0.74,
                tier="bm25",
                tokens=7,
                collection="shared",
            ),
            SearchHit(
                path="/vault/agent-beta/notes/q1.md",
                title="agent-beta Q1",
                snippet="Goals from the agent-beta angle.",
                score=0.55,
                tier="vector",
                tokens=6,
                collection="agent-beta",
            ),
        ],
        bm25_count=2,
        vec_count=3,
        fused_count=3,
        total_tokens=21,
        latency_ms=42.0,
    )


@when("the search output is converted to an MCP envelope and back via from_envelope")
def _roundtrip_envelope(search_warm_ctx: _SearchWarmCtx) -> None:
    assert search_warm_ctx.original is not None
    envelope = search_output_to_envelope(search_warm_ctx.original)
    search_warm_ctx.rebuilt = SearchOutput.from_envelope(envelope)


@then("format_text on the round-tripped result is byte-identical to format_text on the original")
def _assert_text_byte_identical(search_warm_ctx: _SearchWarmCtx) -> None:
    assert search_warm_ctx.original is not None
    assert search_warm_ctx.rebuilt is not None
    original_text = format_text(search_warm_ctx.original)
    rebuilt_text = format_text(search_warm_ctx.rebuilt)
    assert original_text == rebuilt_text, (
        f"warm-MCP text path drifted from in-process:\n"
        f"--- in-process ---\n{original_text!r}\n--- warm-MCP ---\n{rebuilt_text!r}"
    )


# ---------------------------------------------------------------------------
# Scenario 2 — --json flag on the CLI
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _StubPipelineResult:
    """Minimal stand-in for the production ``SearchPipeline.search`` result.

    Carries the attributes ``run_search`` reads from the pipeline shape:
    ``query``, ``intent``, ``results`` (list of BudgetedResult-shaped
    objects), and the count/timing diagnostics. F46-clean: the BDD step
    uses a stub here rather than constructing a real pipeline because
    the surface under test is the CLI's envelope-rendering path, not
    the pipeline.
    """

    query: str
    intent: QueryIntent
    results: list[Any]
    bm25_count: int = 0
    vec_count: int = 0
    fused_count: int = 0
    vec_failed: bool = False
    total_tokens: int = 0
    latency_ms: float = 0.0
    error: str = ""


@dataclass(frozen=True)
class _StubInner:
    path: str
    title: str
    snippet: str
    score: float
    boosted_score: float
    collection: str
    source_page: int | None


@dataclass(frozen=True)
class _StubBudgeted:
    result: _StubInner
    content: str
    tier: str
    token_estimate: int


def _stub_search(**kwargs: Any) -> _StubPipelineResult:
    """Two-hit search result for the BDD scenario.

    Accepts ``**kwargs`` so the stub matches the loose ``Callable[..., Any]``
    shape ``SearchDeps.search_fn`` declares (``run_search`` passes
    ``query=`` / ``agent=`` / ``scope=`` / ``budget=`` as kwargs).
    Returns a result-shaped stub whose attributes ``run_search`` reads
    via ``getattr`` — the use case then projects it to a ``SearchOutput``
    that the CLI renders as JSON.
    """
    query = str(kwargs.get("query", ""))
    return _StubPipelineResult(
        query=query,
        intent=QueryIntent.SEMANTIC,
        results=[
            _StubBudgeted(
                result=_StubInner(
                    path="/vault/agent-alpha/sync.md",
                    title="Agent alpha sync",
                    snippet="Sync notes for agent-alpha standup.",
                    score=0.85,
                    boosted_score=0.85,
                    collection="agent-alpha",
                    source_page=None,
                ),
                content="Sync notes for agent-alpha standup.",
                tier="vector",
                token_estimate=8,
            ),
            _StubBudgeted(
                result=_StubInner(
                    path="/vault/shared/standup.md",
                    title="Shared standup template",
                    snippet="Template used by every agent for their sync.",
                    score=0.62,
                    boosted_score=0.62,
                    collection="shared",
                    source_page=None,
                ),
                content="Template used by every agent for their sync.",
                tier="bm25",
                token_estimate=9,
            ),
        ],
        bm25_count=1,
        vec_count=2,
        fused_count=2,
        vec_failed=False,
        total_tokens=17,
        latency_ms=23.5,
    )


@given(parsers.parse('a search use case that returns 2 hits for query "{query}"'))
def _seed_search_deps(search_warm_ctx: _SearchWarmCtx, query: str) -> None:
    _ = query  # the stub echoes whatever query the CLI threads through; kept on the step for narrative clarity
    search_warm_ctx.deps = SearchDeps(
        search_fn=_stub_search,
        entity_card_fn=lambda _name: None,
        classify_fn=lambda _q: QueryIntent.SEMANTIC,
        health_deps=_healthy_health_deps(),
    )


@when("the operator runs the search CLI with json mode")
def _run_search_json(search_warm_ctx: _SearchWarmCtx) -> None:
    out_buf = io.StringIO()
    err_buf = io.StringIO()
    try:
        with redirect_stdout(out_buf), redirect_stderr(err_buf):
            search_main(["agent-alpha sync", "--json", "--no-entity-card"], deps=search_warm_ctx.deps)
        search_warm_ctx.exit_code = 0
    except SystemExit as exc:  # NOSONAR — BDD step captures CLI exit code; reraising would defeat the test
        search_warm_ctx.exit_code = int(exc.code) if exc.code is not None else 0
    search_warm_ctx.stdout = out_buf.getvalue()
    search_warm_ctx.stderr = err_buf.getvalue()


@then(parsers.parse("stdout is valid JSON containing keys query, intent, and results"))
def _assert_stdout_is_envelope_json(search_warm_ctx: _SearchWarmCtx) -> None:
    try:
        parsed = json.loads(search_warm_ctx.stdout)
    except json.JSONDecodeError as exc:
        pytest.fail(f"stdout was not valid JSON: {exc}\n--- stdout ---\n{search_warm_ctx.stdout!r}")
    assert isinstance(parsed, dict), f"envelope must be a dict, got {type(parsed).__name__}"
    for key in ("query", "intent", "results"):
        assert key in parsed, f"envelope missing key {key!r}: {sorted(parsed.keys())}"
    search_warm_ctx.parsed_envelope = parsed


@then(parsers.parse("the search CLI exits with status {code:d}"))
def _assert_exit(search_warm_ctx: _SearchWarmCtx, code: int) -> None:
    assert search_warm_ctx.exit_code == code, (
        f"expected exit {code}, got {search_warm_ctx.exit_code}; "
        f"stdout={search_warm_ctx.stdout[:200]!r} stderr={search_warm_ctx.stderr[:200]!r}"
    )

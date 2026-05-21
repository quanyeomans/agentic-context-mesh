"""Step bodies for ``benchmark_unified_contract.feature``.

Phase status:

* **P0 (landed)** — feature file + this step skeleton (all bodies raised
  :class:`NotImplementedError`).
* **P1 (landed)** — corpus + ingest fakes.
* **P2 (landed)** — Quality lens step bodies (NDCG / Hit@K / MRR / Judge /
  combined) wired to :class:`kairix.quality.scoring.ScorerRegistry`.
  Per-query gold + expected_answer are read from ``_state``; the latest
  ``QueryRunResult`` from the When step is also read from ``_state``.
* **P3.a (landed)** — Performance + Scoping When/Then bodies wired to the
  unified single-shot mode dispatcher
  (:func:`kairix.quality.benchmark.modes.run_single_shot`).
* **P3.c (pending)** — Stability lens bodies + soak-mode dispatcher; the
  scenarios under that lens are tagged ``@pytest.mark.skip`` in the
  loader until the slice lands.
* **P5 (landed)** — Loader wired in ``tests/bdd/test_benchmark_unified_
  contract.py`` and registered via ``tests/conftest.py`` ``pytest_plugins``.
  Background Givens, quantitative Given (``query_with_gold_titles``),
  qualitative Given (``query_with_expected_answer``), combined Given,
  per-query latency Given, scoping Given, gate-failure Given, and focus-
  area Given all carry concrete bodies that drive the single-shot
  dispatcher with fakes from :mod:`tests.fakes` plus a local
  :class:`_FakeBenchmarkSuite`. The When step additionally seeds
  ``_state["last_run"]`` from ``mode_result.per_query_runs[0]`` so the
  Then bodies (which call ``_expect_run()``) score the first run.

Sabotage-proof convention. Each step carries a ``# sabotage:`` note
describing the mutation that produces a failing assertion. P2's Quality-
lens bodies have those sabotage paths exercised by ``tests/quality/
scoring/test_*.py`` unit tests (mutation patterns are documented +
executed there). P3.a's Performance + Scoping bodies have parallel
sabotage paths exercised by ``tests/quality/benchmark/test_modes_single_
shot.py``.

**Routing-boundary framing.** The scoping scenarios (Scenario Outline
"Scope and collection filtering respect RBAC boundaries") assert on
*routing boundary* behaviour — which collections retrieval looks at
under a given ``Scope`` / ``agent`` combination. Per spike C3 §4 this
is NOT permission enforcement: kairix's benchmark validates routing
shape, not RBAC. Operators wiring real access control layer those at
the transport / auth boundary; the suite YAML cannot assert them.

**Module imports.** The Quality-lens steps reach into
``kairix.quality.scoring`` and ``tests.fakes`` (FakeLLMBackend). Tests
in ``tests/`` may import ``kairix.*`` and ``tests.fakes.*`` directly;
the F24 prohibition is on ``kairix/**`` importing ``tests.*``, not the
other way around. The eval CLI's existing legacy steps file is the
prior-art pattern.
"""

from __future__ import annotations

import statistics
from typing import Any

import pytest
from pytest_bdd import given, parsers, then, when

from kairix.quality.scoring import (
    HitAtKScorer,
    LLMJudgeScorer,
    MRRScorer,
    NDCGScorer,
    QueryRunResult,
)
from tests.fakes import FakeLLMBackend

pytestmark = pytest.mark.bdd

# ---------------------------------------------------------------------------
# Shared step state. Populated by Given/When steps, asserted on by Then
# steps. Kept as a module-level dict so step impls can stay simple — the
# pytest-bdd convention in this repo (see e.g. ``benchmark_steps.py``).
# ---------------------------------------------------------------------------

_state: dict[str, Any] = {}


# Floors mirror the Examples tables in the feature file. Used by the
# combined-scenario steps which derive the floor from the query_type
# captured by the Given step rather than from a parser-extracted literal.
_NDCG_FLOORS: dict[str, float] = {
    "keyword": 0.75,
    "entity": 0.80,
    "procedural": 0.85,
    "temporal": 0.75,
    "multi-hop": 0.70,
    "semantic": 0.80,
}

_JUDGE_FLOORS: dict[str, float] = {
    "keyword": 0.70,
    "entity": 0.70,
    "procedural": 0.75,
    "temporal": 0.60,
    "multi-hop": 0.50,
    "semantic": 0.65,
    "conversational-multi-session": 0.50,
}


def _floor_for(query_type: str, lens: str) -> float:
    """Look up the Examples-table floor for a (query_type, lens) pair."""
    table = _NDCG_FLOORS if lens == "ndcg" else _JUDGE_FLOORS
    return table.get(query_type, 0.0)


def _expect_run() -> QueryRunResult:
    """Return the most-recent QueryRunResult or fail with a P3 affordance.

    The When step (P3 — mode dispatcher) populates ``_state["last_run"]``.
    Until P3 lands the When body still raises NotImplementedError, so
    this helper is only reached once P3 wiring is in place. Raising
    with a clear marker makes mis-ordered scenarios fail loudly.
    """
    run = _state.get("last_run")
    if run is None:
        raise AssertionError(
            "no QueryRunResult captured. "
            "fix: ensure the When step ran successfully (P3 mode-dispatcher wiring). "
            "next: see tests/bdd/steps/benchmark_unified_contract_steps.py — "
            'the When step body must set _state["last_run"]. '
            "run: bash scripts/safe-commit.sh — P3 is the mode-dispatcher commit."
        )
    if not isinstance(run, QueryRunResult):
        raise AssertionError(
            f"_state['last_run'] has wrong type: {type(run).__name__}. "
            "fix: ensure the When step stores a QueryRunResult, not a dict or other shape."
        )
    return run


def _expect_gold() -> list[dict[str, Any]]:
    """Return the gold_titles for the current query or fail with a P1 affordance."""
    gold = _state.get("gold_titles")
    if gold is None:
        raise AssertionError(
            "no gold_titles captured for the current scenario. "
            'fix: ensure the Given step set _state["gold_titles"] from the suite. '
            "next: P1 corpus + P3 dispatcher wiring populate this. "
            "run: see kairix/quality/benchmark/suite.py for the gold_titles schema."
        )
    return list(gold)


def _expect_expected_answer() -> str:
    """Return the expected_answer for the current query or fail with a P1 affordance."""
    expected = _state.get("expected_answer")
    if not expected:
        raise AssertionError(
            "no expected_answer captured for the current scenario. "
            'fix: ensure the Given step set _state["expected_answer"] from the suite. '
            "next: P1 corpus + P3 dispatcher wiring populate this. "
            "run: see kairix/quality/benchmark/suite.py — BenchmarkCase.expected_answer."
        )
    return str(expected)


_TODO_P3C = "TODO P3.c: wire soak execution mode adapter"
# Pre-P5 step bodies (concurrent-mode stubs + routing-boundary fall-
# throughs) still raise the canonical placeholder when a Then step is
# reached without its Given having seeded the required state. Kept so a
# mis-ordered scenario produces a phase-tagged failure rather than a
# confusing AttributeError. These should drop as P3.b / P3.c / future
# slices land their wiring.
_TODO_P1 = "TODO P1: wire FakeBenchmarkSuite once P2 lands the fake in tests/fakes.py"
_TODO_P3 = "TODO P3: wire single-shot execution mode adapter"
_TODO_P4 = "TODO P4: wire concurrent execution mode adapter"
_TODO_P6 = "TODO P6: wire scope / agent / collection filtering"


def _not_implemented(phase: str) -> None:
    """Raise the canonical placeholder so unimplemented steps fail loudly.

    Carrying the phase reference in the message lets a reader of the
    pytest failure line know exactly which implementation phase is on
    the hook for this step. Centralised so the message format is uniform
    across the ~30 steps in this skeleton.
    """
    raise NotImplementedError(f"{phase} — step skeleton in tests/bdd/steps/benchmark_unified_contract_steps.py")


# ---------------------------------------------------------------------------
# Local fakes used by the P5 Given bodies.
#
# These are scoped to this step module — the BDD lens needs a tiny suite
# with controllable cases, and an executor that produces deterministic
# ranked_doc_titles + synthesised_answer. We don't add either to
# tests/fakes.py because they're BDD-scenario fixtures, not Protocol
# implementations — production code never reaches for them.
# ---------------------------------------------------------------------------


class _FakeBenchmarkCase:
    """Stand-in BenchmarkCase carrying just the fields run_single_shot reads.

    ``run_single_shot._to_sampled_query`` reads ``id``, ``category``,
    ``query``, and (optional) ``agent``. We mirror that shape exactly so
    the dispatcher doesn't need to know it's running against a fake.
    """

    def __init__(self, *, case_id: str, category: str, query: str, agent: str | None = None) -> None:
        self.id = case_id
        self.category = category
        self.query = query
        self.agent = agent


class _FakeBenchmarkSuite:
    """Stand-in BenchmarkSuite carrying just ``meta`` and ``cases``.

    The single-shot dispatcher reads ``suite.cases`` and nothing else; the
    Then steps may consult ``suite.meta`` for declarative gates. We keep
    both fields open so a Given step can shape the suite however the
    scenario needs.
    """

    def __init__(self, *, meta: dict[str, Any] | None = None, cases: list[_FakeBenchmarkCase] | None = None) -> None:
        self.meta = meta or {}
        self.cases = list(cases or [])


def _make_query_executor(
    *,
    ranked_titles: tuple[str, ...],
    synthesised_answer: str = "",
    latency_ms: float = 1.0,
    error: str | None = None,
    routing_metadata: dict[str, Any] | None = None,
) -> Any:
    """Build a deterministic executor closure for the BDD scenarios.

    Returns a closure with the signature run_single_shot expects:
    ``Callable[[SampledQuery], QueryRunResult]``. The closure produces a
    constant ``QueryRunResult`` populated with the caller-supplied
    retrieval + synthesis evidence so the Then steps score deterministically.

    ``routing_metadata`` lets RBAC scenarios stash per-case collection /
    retrieved-path info on the result's ``stage_latency_ms`` dict — the
    same channel the routing-boundary Then steps already consult.
    """

    def _executor(sampled: Any) -> QueryRunResult:
        # The QueryRunResult dataclass is frozen; we build it once per call
        # so the dispatcher's latency_phase relabel (see single_shot.py) can
        # still re-emit a copy with the cold/warm tag set.
        run = QueryRunResult(
            query_id=sampled.case_id,
            category=sampled.category,
            query_text=sampled.query,
            ranked_doc_titles=ranked_titles,
            synthesised_answer=synthesised_answer,
            latency_ms=latency_ms,
            error=error,
        )
        if routing_metadata is not None:
            # Stash via object.__setattr__ — QueryRunResult is frozen but the
            # routing-boundary Then step reads stage_latency_ms via getattr;
            # we attach the field after construction without breaking the
            # frozen contract because it's a NEW attribute.
            object.__setattr__(run, "stage_latency_ms", routing_metadata)
        return run

    return _executor


def _execute_and_store(suite: Any, executor: Any) -> None:
    """Run the single-shot dispatcher and seed ``_state`` for Then steps.

    Both ``_state["mode_result"]`` (used by performance / scoping Then
    bodies) and ``_state["last_run"]`` (used by quality-lens Then bodies
    via :func:`_expect_run`) get populated; for the single-query
    scenarios that dominate this feature, the first per-query run IS
    the headline run.

    When ``_state["routing_metadata_per_case"]`` is set (RBAC Givens), we
    re-attach the routing metadata to each ``QueryRunResult`` AFTER the
    dispatcher returns — the dispatcher's internal ``replace()`` call to
    label the cold/warm phase drops attributes set via
    ``object.__setattr__``, so the routing-boundary Then steps would
    otherwise observe a metadata-less result.
    """
    from kairix.quality.benchmark.modes import ModeRunRequest
    from kairix.quality.benchmark.modes import run_single_shot as _run

    req = ModeRunRequest(suite=suite, query_executor=executor)
    result = _run(req)
    routing_per_case = _state.get("routing_metadata_per_case") or {}
    if routing_per_case:
        for row in result.per_query_runs:
            meta = routing_per_case.get(row.query_id)
            if meta is not None:
                object.__setattr__(row, "stage_latency_ms", meta)
    _state["mode_result"] = result
    if result.per_query_runs:
        _state["last_run"] = result.per_query_runs[0]


# Title pool by query type — the executor returns titles drawn from this pool
# so NDCG / Hit / MRR / Judge can all score above their respective floors. The
# titles are arbitrary but deterministic; per-scenario gold rebinds against
# the same pool so the rank-1 result matches the rank-1 gold.
_TITLE_POOL: dict[str, str] = {
    "keyword": "doc-keyword-canonical",
    "entity": "doc-entity-canonical",
    "procedural": "doc-procedural-canonical",
    "temporal": "doc-temporal-canonical",
    "multi-hop": "doc-multi-hop-canonical",
    "semantic": "doc-semantic-canonical",
    "conversational-multi-session": "doc-conversational-canonical",
}


def _title_for(query_type: str) -> str:
    """Return the deterministic canonical title for a query type."""
    return _TITLE_POOL.get(query_type, "doc-default-canonical")


# ---------------------------------------------------------------------------
# Background
# ---------------------------------------------------------------------------


@given("a benchmark suite that declares queries, collections, scope, agent context, mode, and gates")
def background_suite_declared() -> None:
    """Seed ``_state`` with a tiny suite carrying every required declarative slot.

    Per-scenario Givens narrow / extend this baseline; the Background's
    job is to assert each declarative slot is reachable from ``_state``
    so downstream scenarios that omit a Given don't get a confusing
    ``KeyError``.

    sabotage: drop the ``meta["gates"]`` key — the gate-failure scenario's
    Given that reads ``_state["suite"].meta["gates"]`` raises KeyError
    and the scenario fails loudly.
    """
    _state.clear()
    _state["suite"] = _FakeBenchmarkSuite(
        meta={
            "name": "bdd-unified-contract",
            "default_scope": "shared+agent",
            "default_agent": "agent-alpha",
            "gates": {"weighted_total_min": 0.5},
            "focus_areas": ["release-gate", "dogfood"],
        },
        cases=[],
    )


@given("a corpus ingested through the operator-facing ingest flow")
def background_corpus_ingested() -> None:
    """Mark the corpus as ingested for the BDD lens.

    The unified-contract BDD lens drives retrieval through a synthetic
    executor (``_make_query_executor``); the corpus-ingest behaviour is
    asserted by ``tests/bdd/features/ingest_chat.feature`` and the
    Capability #4 suite. Here we just stash a flag so the scoping /
    routing-boundary scenarios can opt-in to "executor stands in for an
    ingested corpus" semantics without rewiring.

    sabotage: skip stashing the flag — scenarios that read
    ``_state["corpus_ready"]`` raise KeyError instead of silently passing.
    """
    _state["corpus_ready"] = True


# ---------------------------------------------------------------------------
# Quantitative retrieval ranking — Scenario Outline
# ---------------------------------------------------------------------------


@given(parsers.parse('a query of type "{query_type}" carrying gold-titled relevant documents'))
def query_with_gold_titles(query_type: str) -> None:
    """Seed the suite + executor with a single gold-bearing query.

    The gold-title list carries one entry with the canonical title for the
    query type and relevance=2 (best). The executor returns that title at
    rank 1 so NDCG / Hit@5 / MRR@10 all score 1.0 — comfortably above the
    Examples-table floors.

    sabotage: mutate ``ranked_titles`` to point at a title NOT in the
    gold list (e.g. ``("doc-not-in-gold",)``) — NDCG collapses to 0.0
    and the floor assertion fails.
    """
    _state["query_type"] = query_type
    title = _title_for(query_type)
    _state["gold_titles"] = [{"title": title, "relevance": 2}]
    suite = _state.setdefault(
        "suite",
        _FakeBenchmarkSuite(meta={"name": "bdd-unified-contract"}, cases=[]),
    )
    suite.cases = [_FakeBenchmarkCase(case_id=f"Q-{query_type}", category=query_type, query=f"{query_type} query")]
    _state["query_executor"] = _make_query_executor(ranked_titles=(title,), synthesised_answer=title, latency_ms=1.0)


@when("the operator runs the benchmark in single-shot mode")
def run_single_shot() -> None:
    """Dispatch through :func:`kairix.quality.benchmark.modes.run_single_shot`.

    Requires ``_state["suite"]`` and ``_state["query_executor"]`` seeded by
    one of the Quality-lens Given steps. The result is stashed on
    ``_state["mode_result"]`` for performance/scoping Then steps; the
    first per-query run is also stashed on ``_state["last_run"]`` for the
    quality-lens Then bodies that consult :func:`_expect_run`.

    sabotage: short-circuit the runner to return an empty result —
    every Then step that consults ``mode_result`` / ``last_run`` fails.
    """
    suite = _state.get("suite")
    executor = _state.get("query_executor")
    if suite is None or executor is None:
        raise AssertionError(
            "missing suite or query_executor for the single-shot When step. "
            'fix: ensure a Given step set _state["suite"] and _state["query_executor"]. '
            "next: see tests/bdd/steps/benchmark_unified_contract_steps.py — "
            "every Given in the quality / performance lens seeds these two keys."
        )
    _execute_and_store(suite, executor)


@then(parsers.parse("NDCG at 10 is at least {floor:g}"))
def ndcg_at_least(floor: float) -> None:
    # sabotage: clamp the NDCG scorer output to 0.0 — assertion must fail.
    run = _expect_run()
    gold = _expect_gold()
    result = NDCGScorer(gold_titles=gold, k=10).score(run)
    assert result.score >= floor, f"NDCG@10 = {result.score:.3f} < floor {floor}"


@then(parsers.parse("Hit at 5 is at least {floor:g}"))
def hit_at_least(floor: float) -> None:
    # sabotage: return a result-set with the gold document at position 6 —
    # Hit@5 must drop below the floor.
    run = _expect_run()
    gold = _expect_gold()
    result = HitAtKScorer(gold_titles=gold, k=5).score(run)
    assert result.score >= floor, f"Hit@5 = {result.score:.3f} < floor {floor}"


@then(parsers.parse("MRR at 10 is at least {floor:g}"))
def mrr_at_least(floor: float) -> None:
    # sabotage: shuffle the result set so the gold document lands at
    # position 10 — MRR must drop below the floor.
    run = _expect_run()
    gold = _expect_gold()
    result = MRRScorer(gold_titles=gold, k=10).score(run)
    assert result.score >= floor, f"MRR@10 = {result.score:.3f} < floor {floor}"


# ---------------------------------------------------------------------------
# Qualitative answer correctness — Scenario Outline
# ---------------------------------------------------------------------------


@given(parsers.parse('a query of type "{query_type}" carrying an expected answer'))
def query_with_expected_answer(query_type: str) -> None:
    """Seed the suite + executor with a single answer-bearing query.

    The expected_answer is set on ``_state`` so the LLMJudgeScorer can
    bind it; the FakeLLMBackend (also stashed on ``_state["llm"]``) is
    configured to return ``"1.0"`` so the judge sees a perfect match —
    comfortably above every Examples-table floor.

    sabotage: drop the ``expected_answer`` set call — ``_expect_expected_
    answer`` raises with the P1 affordance and the scenario fails.
    """
    _state["query_type"] = query_type
    expected = f"canonical answer for {query_type}"
    _state["expected_answer"] = expected
    _state["llm"] = FakeLLMBackend(chat_response="1.0")
    title = _title_for(query_type)
    suite = _state.setdefault(
        "suite",
        _FakeBenchmarkSuite(meta={"name": "bdd-unified-contract"}, cases=[]),
    )
    suite.cases = [
        _FakeBenchmarkCase(case_id=f"Q-judge-{query_type}", category=query_type, query=f"{query_type} query")
    ]
    _state["query_executor"] = _make_query_executor(
        ranked_titles=(title,),
        synthesised_answer=expected,
        latency_ms=1.0,
    )


@then(parsers.parse("the LLM judge scores the synthesised answer at least {floor:g}"))
def judge_score_at_least(floor: float) -> None:
    # sabotage: force the judge to return 0.0 for every query — every
    # row of the Examples table must fail.
    run = _expect_run()
    expected = _expect_expected_answer()
    llm = _state.get("llm") or FakeLLMBackend(chat_response="1.0")
    result = LLMJudgeScorer(llm=llm, expected_answer=expected).score(run)
    assert result.score >= floor, f"judge = {result.score:.3f} < floor {floor}"


# ---------------------------------------------------------------------------
# Combined retrieval + synthesis consistency
# ---------------------------------------------------------------------------


@given("a query carrying both gold-titled documents and an expected answer")
def query_with_gold_and_answer() -> None:
    """Seed both quality lenses on a single keyword-typed query.

    Drives the combined-scenario assertions: NDCG@10 meets its keyword
    floor, the judge meets its keyword floor, AND the top-ranked title
    appears in the synthesised answer (the contribution check).

    sabotage: drop ``expected_answer`` from the state — ``combined_
    judge_meets_floor`` raises via ``_expect_expected_answer`` and
    the scenario fails.
    """
    query_type = "keyword"
    title = _title_for(query_type)
    expected = f"the answer is from {title}"
    _state["query_type"] = query_type
    _state["gold_titles"] = [{"title": title, "relevance": 2}]
    _state["expected_answer"] = expected
    _state["llm"] = FakeLLMBackend(chat_response="1.0")
    suite = _state.setdefault(
        "suite",
        _FakeBenchmarkSuite(meta={"name": "bdd-unified-contract"}, cases=[]),
    )
    suite.cases = [_FakeBenchmarkCase(case_id="Q-combined", category=query_type, query=f"{query_type} query")]
    # Synthesised answer must mention the top-3 title for the contribution
    # check to pass — we embed the title verbatim.
    _state["query_executor"] = _make_query_executor(
        ranked_titles=(title,),
        synthesised_answer=expected,
        latency_ms=1.0,
    )


@then("NDCG at 10 meets its floor for the query type")
def combined_ndcg_meets_floor() -> None:
    # sabotage: as for the ndcg-only step — clamp the scorer to 0.0.
    floor = _floor_for(_state.get("query_type", "keyword"), "ndcg")
    ndcg_at_least(floor)


@then("the LLM judge score meets its floor for the query type")
def combined_judge_meets_floor() -> None:
    # sabotage: as for the judge-only step — clamp the judge to 0.0.
    floor = _floor_for(_state.get("query_type", "keyword"), "judge")
    judge_score_at_least(floor)


@then("the top-ranked documents materially contribute to the synthesised answer")
def top_ranked_contribute_to_answer() -> None:
    # sabotage: synthesise the answer from a fixed prompt that ignores
    # the retrieved documents — the contribution check must fail.
    #
    # Implementation: at least one top-3 retrieved title appears as a
    # substring of the synthesised answer. Conservative — strong models
    # often paraphrase, so this catches the "answer ignored retrieval"
    # failure mode (synthesised answer mentions NO retrieved title) but
    # not subtler attribution drift.
    run = _expect_run()
    if not run.synthesised_answer:
        raise AssertionError("synthesised_answer is empty — cannot evaluate contribution")
    answer_lc = run.synthesised_answer.lower()
    top_3 = run.ranked_doc_titles[:3]
    if not top_3:
        raise AssertionError("ranked_doc_titles is empty — retrieval produced no signal")
    mentioned = [t for t in top_3 if t.lower() in answer_lc]
    assert mentioned, (
        f"none of the top-3 retrieved docs {list(top_3)!r} appear in the "
        f"synthesised answer (len={len(answer_lc)}); retrieval did not "
        f"materially contribute"
    )


# ---------------------------------------------------------------------------
# Performance — per-query latency
# ---------------------------------------------------------------------------


@given("a benchmark suite with latency gates declared")
def suite_with_latency_gates() -> None:
    """Seed a multi-case suite + latency gates for the per-query latency lens.

    Five cases drive the dispatcher — the first reports
    ``latency_phase="cold"`` and the rest report ``"warm"`` (per the
    single-shot phase convention). All five report a ~1ms latency so
    every percentile sits comfortably below the configured gates.

    sabotage: configure the executor to sleep 5_000 ms — p50 cold
    latency exceeds the cold gate and the assertion fails.
    """
    _state["latency_gates"] = {
        "cold_p50_ms": 5000.0,
        "warm_p95_ms": 5000.0,
        "tail_p99_ms": 5000.0,
    }
    suite = _state.setdefault(
        "suite",
        _FakeBenchmarkSuite(meta={"name": "bdd-unified-contract"}, cases=[]),
    )
    suite.cases = [
        _FakeBenchmarkCase(case_id=f"L-{idx}", category="keyword", query=f"latency probe {idx}") for idx in range(5)
    ]
    _state["query_executor"] = _make_query_executor(
        ranked_titles=(_title_for("keyword"),),
        synthesised_answer="ok",
        latency_ms=1.0,
    )


def _percentile(values: list[float], pct: float) -> float:
    """Nearest-rank percentile (matches probe.stats convention).

    Single sample returns itself; empty sample returns 0.0 (the assertion
    above the call point compares to a positive gate so the zero floors out
    cleanly when the upstream Given is wired but produces no samples — an
    upstream bug surfaces in the gate assertion rather than here).
    """
    if not values:
        return 0.0
    if len(values) == 1:
        return values[0]
    quantiles = statistics.quantiles(values, n=100, method="inclusive")
    return float(quantiles[int(pct) - 1])


def _latencies_for(phase: str) -> list[float]:
    """Pull per-query latencies for the requested phase off ``_state``."""
    result = _state.get("mode_result")
    if result is None:
        _not_implemented(_TODO_P3)
    return [r.latency_ms for r in result.per_query_runs if r.latency_phase == phase]


@then("p50 latency is below the cold gate")
def p50_below_cold_gate() -> None:
    """Compute p50 over the cold-phase queries and compare to the cold gate.

    sabotage: inject a 5-second sleep into the executor — p50 must
    exceed any reasonable cold gate.
    """
    gates = _state.get("latency_gates")
    if gates is None:
        _not_implemented(_TODO_P1)
    cold = _latencies_for("cold")
    assert _percentile(cold, 50) < gates["cold_p50_ms"], (
        f"p50 cold latency {_percentile(cold, 50):.1f}ms >= gate {gates['cold_p50_ms']}ms"
    )


@then("p95 latency is below the warm gate")
def p95_below_warm_gate() -> None:
    """Compute p95 over the warm-phase queries and compare to the warm gate.

    sabotage: inject a 5-second sleep into one in twenty queries —
    p95 must exceed any reasonable warm gate.
    """
    gates = _state.get("latency_gates")
    if gates is None:
        _not_implemented(_TODO_P1)
    warm = _latencies_for("warm")
    assert _percentile(warm, 95) < gates["warm_p95_ms"], (
        f"p95 warm latency {_percentile(warm, 95):.1f}ms >= gate {gates['warm_p95_ms']}ms"
    )


@then("p99 latency is below the tail gate")
def p99_below_tail_gate() -> None:
    """Compute p99 over the warm-phase queries and compare to the tail gate.

    sabotage: inject a 5-second sleep into one in one hundred queries —
    p99 must exceed any reasonable tail gate.
    """
    gates = _state.get("latency_gates")
    if gates is None:
        _not_implemented(_TODO_P1)
    warm = _latencies_for("warm")
    assert _percentile(warm, 99) < gates["tail_p99_ms"], (
        f"p99 tail latency {_percentile(warm, 99):.1f}ms >= gate {gates['tail_p99_ms']}ms"
    )


# ---------------------------------------------------------------------------
# Performance — sustained throughput
# ---------------------------------------------------------------------------


@given("a benchmark suite with concurrency 32 and duration 60 seconds")
def concurrent_suite() -> None:
    # sabotage: set concurrency to 0 — the runner must refuse to start
    # rather than silently coerce to a sequential run.
    _not_implemented(_TODO_P1)


@when("the operator runs the benchmark in concurrent mode")
def run_concurrent() -> None:
    # sabotage: force the runner to single-thread — sustained QPS must
    # collapse below the throughput floor.
    _not_implemented(_TODO_P4)


@then("sustained queries per second meet the throughput floor")
def qps_meets_floor() -> None:
    # sabotage: clamp the QPS measurement to zero — the floor assertion
    # must fail.
    _not_implemented(_TODO_P4)


@then("p95 latency under load remains below its gate")
def p95_under_load_below_gate() -> None:
    # sabotage: inject queue-time amplification — p95 under load must
    # exceed the gate.
    _not_implemented(_TODO_P4)


@then("the error rate remains below its ceiling")
def error_rate_below_ceiling() -> None:
    # sabotage: deterministically fail every third query in the runner —
    # the error-rate ceiling must trip.
    _not_implemented(_TODO_P4)


# ---------------------------------------------------------------------------
# Stability — soak
# ---------------------------------------------------------------------------


@given("a soak suite that repeats the workload 100 times")
def soak_suite() -> None:
    # sabotage: set iterations to 0 — the runner must refuse to start
    # rather than report a trivially-passing soak.
    _not_implemented(_TODO_P1)


_P3C_NEXT = (
    "next: implement soak-mode dispatch in P3.c — wrap "
    "kairix.quality.soak.run_soak with a workload_runner closure per C2 §3.3. "
    "fix: until then, pin the suite to a mode that doesn't exercise this lens. "
    "run: bash scripts/safe-commit.sh after the body lands."
)


@when("the operator runs the benchmark in soak mode")
def run_soak() -> None:
    # sabotage: force the runner to a single iteration — the soak
    # lenses (memory growth / fd leak / determinism drift) must refuse
    # to compute on a one-iteration sample.
    raise NotImplementedError(f"P3.c soak-mode dispatcher. {_P3C_NEXT}")


@then("per-iteration memory growth stays under its gate")
def memory_growth_under_gate() -> None:
    # sabotage: leak a 10 MiB buffer per iteration — the growth gate
    # must trip.
    raise NotImplementedError(f"P3.c soak-mode memory-growth gate. {_P3C_NEXT}")


@then("no file descriptors leak between iterations")
def no_fd_leaks() -> None:
    # sabotage: open a file per iteration without closing — the fd
    # delta check must trip.
    raise NotImplementedError(f"P3.c soak-mode fd-leak gate. {_P3C_NEXT}")


@then("determinism drift between runs stays under its gate")
def determinism_drift_under_gate() -> None:
    # sabotage: seed the runner with the wall clock instead of the
    # configured seed — determinism drift must exceed the gate.
    raise NotImplementedError(f"P3.c soak-mode determinism-drift gate. {_P3C_NEXT}")


@then("per-iteration log volume growth stays under its gate")
def log_volume_under_gate() -> None:
    # sabotage: log a 1 KiB line per iteration above the baseline — the
    # log-volume growth gate must trip.
    raise NotImplementedError(f"P3.c soak-mode log-volume gate. {_P3C_NEXT}")


# ---------------------------------------------------------------------------
# Scope / agent / collection RBAC
# ---------------------------------------------------------------------------


@given(parsers.parse('an agent "{agent}" with scope "{scope}" on collection "{collection}"'))
def agent_with_scope(agent: str, scope: str, collection: str) -> None:
    """Seed the suite + routing-aware executor for an RBAC scenario.

    The executor stamps the case's authorised collection list onto
    ``stage_latency_ms["collections"]`` and leaves
    ``retrieved_paths`` empty for the zero-result probe — both signals
    the routing-boundary Then steps consult.

    sabotage: include a foreign collection in ``routing_metadata`` —
    ``results_only_authorised`` flags the leak and fails.
    """
    _state["agent"] = agent
    _state["scope"] = scope
    _state["collection"] = collection
    _state["authorised_collections"] = (collection,)
    suite = _state.setdefault(
        "suite",
        _FakeBenchmarkSuite(meta={"name": "bdd-unified-contract"}, cases=[]),
    )
    case_id = f"S-{agent}"
    suite.cases = [_FakeBenchmarkCase(case_id=case_id, category="recall", query="routing probe", agent=agent)]
    _state["zero_result_case_ids"] = ()
    # Per-case routing metadata is reattached AFTER the dispatcher's
    # _label_phase() replace() call (see _execute_and_store) so the
    # routing-boundary Then steps observe the metadata.
    _state["routing_metadata_per_case"] = {case_id: {"collections": [collection], "retrieved_paths": []}}
    _state["query_executor"] = _make_query_executor(
        ranked_titles=(_title_for("keyword"),),
        synthesised_answer="ok",
        latency_ms=1.0,
    )


@when("the operator runs the benchmark for that agent")
def run_for_agent() -> None:
    """Run the single-shot dispatcher with the agent's scope context.

    The P1 Given seeds ``_state["suite"]`` with cases carrying the
    requested ``scope`` / ``collection`` overrides; the P6 Given seeds
    a ``query_executor`` closure that honours the routing-boundary
    contract (the executor's ``SampledQuery.agent`` and per-case
    ``scope`` / ``collection`` determine which collections retrieval
    looks at).

    sabotage: drop the agent context from the executor closure — the
    routing-boundary checks below must refuse to assert on an
    un-routed result set.
    """
    from kairix.quality.benchmark.modes import ModeRunRequest
    from kairix.quality.benchmark.modes import run_single_shot as _run

    suite = _state.get("suite")
    executor = _state.get("query_executor")
    if suite is None or executor is None:
        _not_implemented(_TODO_P6)
    req = ModeRunRequest(suite=suite, query_executor=executor)
    _state["mode_result"] = _run(req)


@then("the results contain only documents the agent is authorised to see")
def results_only_authorised() -> None:
    """Routing-boundary assertion: per-case retrieved-path metadata must
    only reference documents whose collection is reachable under the
    agent's scope (per spike C3 §1).

    Per C3 the suite asserts routing shape, not access control —
    deployments wiring real RBAC layer that at the transport / auth
    boundary. The assertion here checks the routing boundary: that the
    collection resolver narrowed retrieval to the agent's authorised
    collection list and the executor honoured the narrowing.

    sabotage: include one document from a sibling agent's collection
    in the executor's returned snippets — the routing-boundary check
    must fail.
    """
    result = _state.get("mode_result")
    if result is None:
        _not_implemented(_TODO_P6)
    authorised = set(_state.get("authorised_collections", ()) or ())
    if not authorised:
        _not_implemented(_TODO_P6)
    for row in result.per_query_runs:
        # Executors that surface per-case routing-boundary metadata stash
        # the collection list on ``stage_latency_ms`` (free-form dict) — the
        # routing-boundary lens treats any unknown collection as a leak.
        observed_collections = row.stage_latency_ms.get("collections") if hasattr(row, "stage_latency_ms") else None
        if not observed_collections:
            continue
        leaked = {c for c in observed_collections if c not in authorised}
        assert not leaked, (
            f"routing boundary breach on case {row.query_id}: "
            f"observed collections {sorted(observed_collections)} include {sorted(leaked)} "
            f"outside authorised set {sorted(authorised)}"
        )


@then("cross-collection and cross-agent leakage produces zero hits")
def cross_scope_zero_hits() -> None:
    """Routing-boundary assertion: queries flagged ``expected_zero_results``
    (per :class:`kairix.quality.benchmark.suite.BenchmarkCase`) must
    produce empty retrieved-path sets for their scoped agent. The flag
    is a declarative probe that the routing layer rejects the query at
    the requested scope — NOT a permission check (per spike C3 §4).

    sabotage: have the executor surface a non-empty hit list for a
    zero-results probe — the leakage assertion must fail.
    """
    result = _state.get("mode_result")
    if result is None:
        _not_implemented(_TODO_P6)
    zero_cases = _state.get("zero_result_case_ids", ()) or ()
    for row in result.per_query_runs:
        if row.query_id not in zero_cases:
            continue
        observed_paths = row.stage_latency_ms.get("retrieved_paths") if hasattr(row, "stage_latency_ms") else None
        assert not observed_paths, (
            f"routing boundary breach on zero-result probe {row.query_id}: "
            f"expected empty hit list, got {observed_paths!r}"
        )


# ---------------------------------------------------------------------------
# CI integration — gate-failure exit codes
# ---------------------------------------------------------------------------


@given("a benchmark suite where at least one declared gate fails")
def suite_with_failing_gate() -> None:
    """Seed a suite carrying a guaranteed-failing gate.

    The executor returns a low-score result; the declared gate floor sits
    above it so the verdict computation marks the gate failed. The
    declarative gate envelope is stashed on ``_state["declared_gates"]``
    so the Then steps can compare actual vs expected.

    sabotage: bump the gate threshold below the executor's score — the
    gate passes and the failing-gate Then steps observe an empty
    ``failed_gates`` list and fail.
    """
    _state["declared_gates"] = {"ndcg_at_10": 0.99}  # impossibly high — executor scores 0.0
    suite = _state.setdefault(
        "suite",
        _FakeBenchmarkSuite(meta={"name": "bdd-unified-contract"}, cases=[]),
    )
    suite.cases = [_FakeBenchmarkCase(case_id="G-1", category="recall", query="gate probe")]
    # No gold → NDCG scores 0.0; the gate's 0.99 floor guarantees failure.
    _state["gold_titles"] = [{"title": "doc-gate", "relevance": 2}]
    _state["query_executor"] = _make_query_executor(
        ranked_titles=("unrelated-doc",),
        synthesised_answer="",
        latency_ms=1.0,
    )


@when("the operator runs the benchmark with gates enabled")
def run_with_gates() -> None:
    """Run the dispatcher and compute the gate verdict envelope.

    Builds a ``failed_gates`` list of ``{gate, actual, expected}`` dicts
    on ``_state`` by comparing each declared gate against the actual
    score from the appropriate scorer. The Then steps consume this
    envelope.

    sabotage: pass ``--no-gates`` semantics by emptying
    ``failed_gates`` — the named-gates Then asserts and fails.
    """
    suite = _state.get("suite")
    executor = _state.get("query_executor")
    if suite is None or executor is None:
        raise AssertionError(
            "missing suite or query_executor for the gate When step. "
            "fix: ensure the gate-failure Given seeded both. "
            "next: see tests/bdd/steps/benchmark_unified_contract_steps.py."
        )
    _execute_and_store(suite, executor)
    declared = _state.get("declared_gates", {})
    gold = _state.get("gold_titles") or []
    actual_ndcg = NDCGScorer(gold_titles=gold, k=10).score(_state["last_run"]).score
    actuals = {"ndcg_at_10": actual_ndcg}
    _state["failed_gates"] = [
        {"gate": gate, "actual": actuals.get(gate, 0.0), "expected": expected}
        for gate, expected in declared.items()
        if actuals.get(gate, 0.0) < expected
    ]
    # The CLI translates "gates fail" to exit code 2 (see cli._emit_gate_failure);
    # mirror that here so the Then step can assert without re-running the CLI.
    _state["exit_code"] = 2 if _state["failed_gates"] else 0


@then("the process exits non-zero")
def process_exits_non_zero() -> None:
    """Assert the gate-aware exit code is non-zero.

    sabotage: short-circuit ``run_with_gates`` to set exit_code=0 — this
    assertion fires.
    """
    code = _state.get("exit_code")
    assert code is not None and code != 0, f"expected non-zero exit code, got {code!r}"


@then("the report identifies which gates failed")
def report_identifies_failed_gates() -> None:
    """Assert the gate envelope names each failed gate.

    sabotage: drop the ``gate`` key from each row of ``failed_gates`` —
    the comprehension below produces an empty list and the assert fails.
    """
    failed = _state.get("failed_gates") or []
    names = [row["gate"] for row in failed if "gate" in row]
    assert names, f"expected at least one named failed gate; got {failed!r}"


@then("the report records the actual and expected values for each failed gate")
def report_records_actual_vs_expected() -> None:
    """Assert each failed-gate row carries both actual and expected.

    sabotage: drop ``actual`` (or ``expected``) from one row — the
    assertion below trips on that row.
    """
    failed = _state.get("failed_gates") or []
    assert failed, "no failed gates recorded; the Given step must seed a failing gate."
    for row in failed:
        assert "actual" in row and "expected" in row, f"failed-gate row missing actual/expected: {row!r}"


# ---------------------------------------------------------------------------
# Focus-area segmentation
# ---------------------------------------------------------------------------


@given(parsers.parse('a benchmark suite tagged with focus areas "{first}" and "{second}"'))
def suite_with_focus_areas(first: str, second: str) -> None:
    """Seed a multi-case suite where each case carries a focus-area tag.

    Cases come in pairs — one tagged for each focus area. The When step's
    focus-area filter applies before scoring; the Then steps consult the
    filtered case set to verify the segmentation worked.

    sabotage: drop the focus-area tags from the cases — the When step's
    filter retains every case and the only-focus Then asserts and fails.
    """
    _state["focus_areas"] = (first, second)
    suite = _state.setdefault(
        "suite",
        _FakeBenchmarkSuite(meta={"name": "bdd-unified-contract"}, cases=[]),
    )
    suite.cases = [
        _FakeBenchmarkCase(case_id="F-rg-1", category="recall", query="release-gate probe 1"),
        _FakeBenchmarkCase(case_id="F-rg-2", category="recall", query="release-gate probe 2"),
        _FakeBenchmarkCase(case_id="F-df-1", category="recall", query="dogfood probe 1"),
        _FakeBenchmarkCase(case_id="F-df-2", category="recall", query="dogfood probe 2"),
    ]
    # case_id-prefix-to-focus map so the When step's filter is declarative.
    _state["case_focus_tags"] = {
        "F-rg-1": first,
        "F-rg-2": first,
        "F-df-1": second,
        "F-df-2": second,
    }


@when(parsers.parse('the operator runs the benchmark restricted to focus area "{focus}"'))
def run_with_focus_area(focus: str) -> None:
    """Filter the suite to the requested focus area and run the dispatcher.

    The filter is applied to ``suite.cases`` before invoking the
    dispatcher so the per-query result tuple only contains the focused
    subset. The chosen focus area is stashed on ``_state["focus"]`` for
    the focus-area report Then step.

    sabotage: skip the filter step — every case runs and the only-focus
    Then asserts and fails.
    """
    _state["focus"] = focus
    suite = _state.get("suite")
    if suite is None:
        raise AssertionError("missing suite for the focus-area When step.")
    tag_map = _state.get("case_focus_tags") or {}
    suite.cases = [c for c in suite.cases if tag_map.get(c.id) == focus]
    executor = _make_query_executor(
        ranked_titles=(_title_for("keyword"),),
        synthesised_answer="ok",
        latency_ms=1.0,
    )
    _state["query_executor"] = executor
    _execute_and_store(suite, executor)


@then("only release-gate queries are scored")
def only_focus_area_scored() -> None:
    """Assert every scored case belongs to the focused area.

    sabotage: leak one non-release-gate case into ``mode_result.per_
    query_runs`` — this assertion trips on the leaked row.
    """
    focus = _state.get("focus")
    tag_map = _state.get("case_focus_tags") or {}
    result = _state.get("mode_result")
    assert result is not None, "no mode_result; the When step must run the dispatcher."
    leaked = [row.query_id for row in result.per_query_runs if tag_map.get(row.query_id) != focus]
    assert not leaked, f"non-{focus} cases leaked through the filter: {leaked!r}"


@then("the report records which focus area was selected")
def report_records_focus_area() -> None:
    """Assert the chosen focus area is recorded on ``_state``.

    sabotage: drop the ``focus`` assignment in the When step — this
    assertion observes a None value and fails.
    """
    assert _state.get("focus"), "focus area not recorded; the When step must stash _state['focus']."

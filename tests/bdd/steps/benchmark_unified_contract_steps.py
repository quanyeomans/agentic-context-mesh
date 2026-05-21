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
* **P3.c (pending)** — Stability lens bodies + soak-mode dispatcher.
* **P5 (pending)** — Wires the loader: adds
  ``"tests.bdd.steps.benchmark_unified_contract_steps"`` to
  ``tests/conftest.py`` ``pytest_plugins`` AND creates
  ``tests/bdd/test_benchmark_unified_contract.py`` with one
  ``@scenario(...)`` declaration per Scenario / Scenario Outline (see
  ``test_benchmark_run.py`` for the pattern). Until P5 lands, the feature
  file is dormant — pytest-bdd does not collect it.

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


_TODO_P1 = "TODO P1: wire FakeBenchmarkSuite once P2 lands the fake in tests/fakes.py"
_TODO_P2 = "TODO P2: wire FakeScorer + scorer-protocol assertions"
_TODO_P3 = "TODO P3: wire single-shot execution mode adapter"
_TODO_P4 = "TODO P4: wire concurrent execution mode adapter"
_TODO_P5 = "TODO P5: wire soak execution mode adapter"
_TODO_P6 = "TODO P6: wire scope / agent / collection filtering"
_TODO_P7 = "TODO P7: wire gate-verdict reporter + CI exit codes"
_TODO_P8 = "TODO P8: wire focus-area segmentation"


def _not_implemented(phase: str) -> None:
    """Raise the canonical placeholder so unimplemented steps fail loudly.

    Carrying the phase reference in the message lets a reader of the
    pytest failure line know exactly which implementation phase is on
    the hook for this step. Centralised so the message format is uniform
    across the ~30 steps in this skeleton.
    """
    raise NotImplementedError(f"{phase} — step skeleton in tests/bdd/steps/benchmark_unified_contract_steps.py")


# ---------------------------------------------------------------------------
# Background
# ---------------------------------------------------------------------------


@given("a benchmark suite that declares queries, collections, scope, agent context, mode, and gates")
def background_suite_declared() -> None:
    # sabotage: drop a required field (e.g. "gates") from the fixture suite —
    # the suite-validation step in P1 must raise so this Given fails.
    _not_implemented(_TODO_P1)


@given("a corpus ingested through the operator-facing ingest flow")
def background_corpus_ingested() -> None:
    # sabotage: skip the ingest call — every subsequent scoring step
    # should observe zero hits and the suite should refuse to score.
    _not_implemented(_TODO_P1)


# ---------------------------------------------------------------------------
# Quantitative retrieval ranking — Scenario Outline
# ---------------------------------------------------------------------------


@given(parsers.parse('a query of type "{query_type}" carrying gold-titled relevant documents'))
def query_with_gold_titles(query_type: str) -> None:
    # sabotage: mutate the gold-title list to a title that does not
    # appear in the corpus — NDCG should collapse to zero.
    _state["query_type"] = query_type
    _not_implemented(_TODO_P1)


@when("the operator runs the benchmark in single-shot mode")
def run_single_shot() -> None:
    """Dispatch through :func:`kairix.quality.benchmark.modes.run_single_shot`.

    Requires ``_state["suite"]`` and ``_state["query_executor"]`` seeded by
    a P1 Given (FakeBenchmarkSuite + per-scenario executor closure). The
    result is stashed on ``_state["mode_result"]`` for Then steps.

    sabotage: short-circuit the runner to return an empty result —
    every Then step that consults ``mode_result`` must fail.
    """
    from kairix.quality.benchmark.modes import ModeRunRequest
    from kairix.quality.benchmark.modes import run_single_shot as _run

    suite = _state.get("suite")
    executor = _state.get("query_executor")
    if suite is None or executor is None:
        _not_implemented(_TODO_P1)
    req = ModeRunRequest(suite=suite, query_executor=executor)
    _state["mode_result"] = _run(req)


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
    # sabotage: drop the expected-answer field — the LLM-judge scorer
    # must raise a config error before scoring.
    _state["query_type"] = query_type
    _not_implemented(_TODO_P1)


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
    # sabotage: keep the gold titles but drop the expected answer — the
    # combined scenario must refuse to score the synthesis lens.
    _not_implemented(_TODO_P1)


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
    # sabotage: drop the latency-gates block — the gate evaluator must
    # refuse to assert on the latency lens.
    _not_implemented(_TODO_P1)


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
    # sabotage: silently expand the scope to "all-agents" regardless of
    # the configured value — the leakage check must trip.
    _state["agent"] = agent
    _state["scope"] = scope
    _state["collection"] = collection
    _not_implemented(_TODO_P6)


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
            f"routing boundary breach on case {row.case_id}: "
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
        if row.case_id not in zero_cases:
            continue
        observed_paths = row.stage_latency_ms.get("retrieved_paths") if hasattr(row, "stage_latency_ms") else None
        assert not observed_paths, (
            f"routing boundary breach on zero-result probe {row.case_id}: "
            f"expected empty hit list, got {observed_paths!r}"
        )


# ---------------------------------------------------------------------------
# CI integration — gate-failure exit codes
# ---------------------------------------------------------------------------


@given("a benchmark suite where at least one declared gate fails")
def suite_with_failing_gate() -> None:
    # sabotage: declare a gate that the fake scorer is guaranteed to
    # meet — the failing-gate scenario must refuse to start.
    _not_implemented(_TODO_P1)


@when("the operator runs the benchmark with gates enabled")
def run_with_gates() -> None:
    # sabotage: pass --no-gates to the runner — the gate-evaluation
    # step must refuse to assert on gates that were never evaluated.
    _not_implemented(_TODO_P7)


@then("the process exits non-zero")
def process_exits_non_zero() -> None:
    # sabotage: swallow the non-zero exit code in the runner wrapper —
    # the exit-code assertion must fail.
    _not_implemented(_TODO_P7)


@then("the report identifies which gates failed")
def report_identifies_failed_gates() -> None:
    # sabotage: emit a generic "gates failed" string without naming
    # which gates — the named-gates assertion must fail.
    _not_implemented(_TODO_P7)


@then("the report records the actual and expected values for each failed gate")
def report_records_actual_vs_expected() -> None:
    # sabotage: emit only the expected values without the actual — the
    # actual-vs-expected assertion must fail.
    _not_implemented(_TODO_P7)


# ---------------------------------------------------------------------------
# Focus-area segmentation
# ---------------------------------------------------------------------------


@given(parsers.parse('a benchmark suite tagged with focus areas "{first}" and "{second}"'))
def suite_with_focus_areas(first: str, second: str) -> None:
    # sabotage: drop the focus-area tags — the focus-area selector must
    # refuse to filter and the test must fail.
    _state["focus_areas"] = (first, second)
    _not_implemented(_TODO_P1)


@when(parsers.parse('the operator runs the benchmark restricted to focus area "{focus}"'))
def run_with_focus_area(focus: str) -> None:
    # sabotage: ignore the --focus-area argument — every query runs
    # regardless and the "only release-gate queries are scored"
    # assertion must fail.
    _state["focus"] = focus
    _not_implemented(_TODO_P8)


@then("only release-gate queries are scored")
def only_focus_area_scored() -> None:
    # sabotage: include one non-release-gate query in the scored set —
    # the only-release-gate assertion must fail.
    _not_implemented(_TODO_P8)


@then("the report records which focus area was selected")
def report_records_focus_area() -> None:
    # sabotage: drop the focus-area field from the report — the assertion
    # must fail.
    _not_implemented(_TODO_P8)

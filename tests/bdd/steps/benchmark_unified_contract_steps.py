"""Step skeleton for ``benchmark_unified_contract.feature``.

This module is the forward-declared step surface for the P0 unified
benchmarking behaviour contract. Implementation phases P1-P8 fill the
bodies; this file documents the contract, names each step phrase, and
records the per-step sabotage-proof plan.

**Wiring status (P0).** This module is intentionally NOT yet listed in
``tests/conftest.py`` ``pytest_plugins`` and there is no companion
``tests/bdd/test_benchmark_unified_contract.py`` loader. Until P1 lands:

* The feature file is dormant — pytest-bdd does not collect it.
* This step module is forward-declared only — the step bodies all
  raise :class:`NotImplementedError` so any premature attempt to wire
  the loader will fail loudly with the phase reference embedded.
* ``bash scripts/safe-commit.sh`` therefore stays green on P0 — only
  the spec lands.

**Wiring instructions (P1).** When the FakeBenchmarkSuite / FakeScorer
fakes land in ``tests/fakes.py`` (P2), the orchestrator wires:

1. Add ``"tests.bdd.steps.benchmark_unified_contract_steps"`` to the
   ``pytest_plugins`` list in ``tests/conftest.py``.
2. Create ``tests/bdd/test_benchmark_unified_contract.py`` with one
   ``@pytest.mark.bdd`` + ``@scenario(...)`` declaration per Scenario
   / Scenario Outline in the feature file (see ``test_benchmark_run.py``
   for the pattern).

Sabotage-proof convention. Each step carries a ``# sabotage:`` note
describing the mutation that must produce a failing assertion when the
P1-P8 body lands. The skeleton itself is implicitly sabotage-proven by
virtue of every step raising :class:`NotImplementedError` — any scenario
that exercises any step fails until the body is filled in. The
``# sabotage:`` note records the *post-implementation* sabotage so the
implementer knows what mutation to check before committing.

**No module-level kairix imports.** The unified benchmark surface
(``kairix.quality.benchmark.unified``) and its fakes
(``tests.fakes.FakeBenchmarkSuite`` / ``FakeScorer`` / etc.) do not yet
exist. P1 / P2 introduce them and this module then imports them at the
package level (``from kairix.quality.benchmark import ...`` /
``from tests.fakes import FakeBenchmarkSuite, ...``). Until then the
skeleton compiles, ``ruff`` is happy, and ``mypy`` has nothing to
complain about — but the steps cannot run.
"""

from __future__ import annotations

from typing import Any

import pytest
from pytest_bdd import given, parsers, then, when

pytestmark = pytest.mark.bdd

# ---------------------------------------------------------------------------
# Shared step state. Populated by Given/When steps, asserted on by Then
# steps. Kept as a module-level dict so step impls can stay simple — the
# pytest-bdd convention in this repo (see e.g. ``benchmark_steps.py``).
# ---------------------------------------------------------------------------

_state: dict[str, Any] = {}

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
    # sabotage: short-circuit the runner to return an empty result —
    # every Then step must fail.
    _not_implemented(_TODO_P3)


@then(parsers.parse("NDCG at 10 is at least {floor:g}"))
def ndcg_at_least(floor: float) -> None:
    # sabotage: clamp the NDCG scorer output to 0.0 — assertion must fail.
    _not_implemented(_TODO_P2)


@then(parsers.parse("Hit at 5 is at least {floor:g}"))
def hit_at_least(floor: float) -> None:
    # sabotage: return a result-set with the gold document at position 6 —
    # Hit@5 must drop below the floor.
    _not_implemented(_TODO_P2)


@then(parsers.parse("MRR at 10 is at least {floor:g}"))
def mrr_at_least(floor: float) -> None:
    # sabotage: shuffle the result set so the gold document lands at
    # position 10 — MRR must drop below the floor.
    _not_implemented(_TODO_P2)


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
    _not_implemented(_TODO_P2)


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
    _not_implemented(_TODO_P2)


@then("the LLM judge score meets its floor for the query type")
def combined_judge_meets_floor() -> None:
    # sabotage: as for the judge-only step — clamp the judge to 0.0.
    _not_implemented(_TODO_P2)


@then("the top-ranked documents materially contribute to the synthesised answer")
def top_ranked_contribute_to_answer() -> None:
    # sabotage: synthesise the answer from a fixed prompt that ignores
    # the retrieved documents — the contribution check must fail.
    _not_implemented(_TODO_P2)


# ---------------------------------------------------------------------------
# Performance — per-query latency
# ---------------------------------------------------------------------------


@given("a benchmark suite with latency gates declared")
def suite_with_latency_gates() -> None:
    # sabotage: drop the latency-gates block — the gate evaluator must
    # refuse to assert on the latency lens.
    _not_implemented(_TODO_P1)


@then("p50 latency is below the cold gate")
def p50_below_cold_gate() -> None:
    # sabotage: inject a 5-second sleep into the runner — p50 must
    # exceed any reasonable cold gate.
    _not_implemented(_TODO_P3)


@then("p95 latency is below the warm gate")
def p95_below_warm_gate() -> None:
    # sabotage: inject a 5-second sleep into one in twenty queries —
    # p95 must exceed any reasonable warm gate.
    _not_implemented(_TODO_P3)


@then("p99 latency is below the tail gate")
def p99_below_tail_gate() -> None:
    # sabotage: inject a 5-second sleep into one in one hundred queries —
    # p99 must exceed any reasonable tail gate.
    _not_implemented(_TODO_P3)


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


@when("the operator runs the benchmark in soak mode")
def run_soak() -> None:
    # sabotage: force the runner to a single iteration — the soak
    # lenses (memory growth / fd leak / determinism drift) must refuse
    # to compute on a one-iteration sample.
    _not_implemented(_TODO_P5)


@then("per-iteration memory growth stays under its gate")
def memory_growth_under_gate() -> None:
    # sabotage: leak a 10 MiB buffer per iteration — the growth gate
    # must trip.
    _not_implemented(_TODO_P5)


@then("no file descriptors leak between iterations")
def no_fd_leaks() -> None:
    # sabotage: open a file per iteration without closing — the fd
    # delta check must trip.
    _not_implemented(_TODO_P5)


@then("determinism drift between runs stays under its gate")
def determinism_drift_under_gate() -> None:
    # sabotage: seed the runner with the wall clock instead of the
    # configured seed — determinism drift must exceed the gate.
    _not_implemented(_TODO_P5)


@then("per-iteration log volume growth stays under its gate")
def log_volume_under_gate() -> None:
    # sabotage: log a 1 KiB line per iteration above the baseline — the
    # log-volume growth gate must trip.
    _not_implemented(_TODO_P5)


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
    # sabotage: drop the agent context from the runner call — the
    # collection-filter step must refuse to assert on unscoped results.
    _not_implemented(_TODO_P6)


@then("the results contain only documents the agent is authorised to see")
def results_only_authorised() -> None:
    # sabotage: include one document from a sibling agent's collection
    # in the result set — the authorisation check must fail.
    _not_implemented(_TODO_P6)


@then("cross-collection and cross-agent leakage produces zero hits")
def cross_scope_zero_hits() -> None:
    # sabotage: issue a query that should produce zero hits at the
    # configured scope but include a match from another collection — the
    # zero-hits invariant must fail.
    _not_implemented(_TODO_P6)


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

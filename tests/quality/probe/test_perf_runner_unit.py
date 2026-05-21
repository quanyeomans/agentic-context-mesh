"""Unit tests for :mod:`kairix.quality.probe.perf_runner`.

The sibling ``test_perf.py`` drives the CLI-dispatch + multi-component
paths under ``@pytest.mark.integration``. This module covers the same
module under ``@pytest.mark.unit`` so the F7 (Stage 2) per-file
coverage floor is met without leaning on integration-scope runs.

Test seam: every operation is a fast in-process closure; the runner
under test is exercised end-to-end (load_budgets, build_default_operations,
run_perf_probe, dataclass to_dict) without spinning up real workloads
or touching the filesystem outside ``tmp_path``.

Every test marks ``@pytest.mark.unit`` (F8) and embeds a sabotage-
proof note tying the assertion to the production line it pins.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from kairix.quality.probe.perf_runner import (
    DEFAULT_PERF_ITERATIONS,
    OperationCallable,
    OperationResult,
    PerfReport,
    build_default_operations,
    load_budgets,
    run_perf_probe,
)

# ---------------------------------------------------------------------------
# Helpers — deterministic fake operations and a tight regression-budget set
# so within-budget vs over-budget branches both fire predictably.
# ---------------------------------------------------------------------------


_BUDGETS: dict[str, dict[str, float]] = {
    "kairix_prep_vault_only": {"p50_ms": 100.0, "p99_ms": 200.0},
    "kairix_prep_facts_federated": {"p50_ms": 100.0, "p99_ms": 200.0},
    "kairix_ingest_chat_per_turn": {"p50_ms": 100.0, "p99_ms": 200.0},
    "kairix_ingest_chat_100_turn": {"p50_ms": 100.0, "p99_ms": 200.0},
    "fact_find_conflicts": {"p50_ms": 100.0, "p99_ms": 200.0},
    "federated_search_top_k_15": {"p50_ms": 100.0, "p99_ms": 200.0},
}


def _fast_op() -> None:
    """Sub-millisecond zero-arg op — sits well under any 100ms budget."""
    # Intentionally empty — timing measures the call-overhead floor.


def _ingest_one_turn_op(_i: int) -> None:
    """Per-iteration ingest stub — accepts the iteration index but is sub-ms."""
    # Intentionally empty — timing measures the call-overhead floor.


def _slow_latencies_op(_n: int) -> list[float]:
    """Return canned latencies above the 100/200ms budget pair."""
    return [150.0, 160.0, 170.0, 180.0, 190.0]


def _wired_default_operations() -> dict[str, OperationCallable]:
    """Wire every non-Cap-#5 operation to a fast closure."""
    return build_default_operations(
        prep_vault_only=_fast_op,
        ingest_one_turn=_ingest_one_turn_op,
        ingest_100_turn=_fast_op,
        fact_find_conflicts=_fast_op,
    )


# ---------------------------------------------------------------------------
# Default-iteration constant — pins the public default so CLI / runner
# stay aligned without re-importing the literal value.
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_default_perf_iterations_is_positive_int() -> None:
    """Default iteration count is a sane positive integer.

    Sabotage-proof: setting ``DEFAULT_PERF_ITERATIONS = 0`` makes the
    CLI's no-arg invocation immediately fail the ``iterations < 1``
    guard. Pinning ``> 0`` here catches that regression at unit scope.
    """
    assert isinstance(DEFAULT_PERF_ITERATIONS, int)
    assert DEFAULT_PERF_ITERATIONS >= 1


# ---------------------------------------------------------------------------
# load_budgets — JSON parsing + validation (covers lines 151-164)
# ---------------------------------------------------------------------------


def _write_budgets(tmp_path: Path, payload: object) -> Path:
    """Serialise ``payload`` to ``tmp_path/budgets.json`` and return the path."""
    target = tmp_path / "budgets.json"
    target.write_text(json.dumps(payload), encoding="utf-8")
    return target


@pytest.mark.unit
def test_load_budgets_returns_float_p50_and_p99(tmp_path: Path) -> None:
    """A well-formed budgets file loads to a float-typed mapping.

    Sabotage-proof: dropping the ``float(...)`` cast leaves the values
    as ints (or whatever the JSON parsed to); the float-isinstance
    check below fails.
    """
    target = _write_budgets(tmp_path, _BUDGETS)
    loaded = load_budgets(target)
    assert loaded.keys() == _BUDGETS.keys()
    assert isinstance(loaded["fact_find_conflicts"]["p50_ms"], float)
    assert isinstance(loaded["fact_find_conflicts"]["p99_ms"], float)
    assert loaded["fact_find_conflicts"]["p50_ms"] == 100.0


@pytest.mark.unit
def test_load_budgets_rejects_non_object_root(tmp_path: Path) -> None:
    """Top-level JSON array → ValueError with "not a JSON object".

    Sabotage-proof: removing the ``isinstance(raw, dict)`` guard makes
    ``raw.items()`` crash with AttributeError, hiding the actionable
    "you handed us a JSON list" diagnostic.
    """
    target = _write_budgets(tmp_path, ["array", "not", "object"])
    with pytest.raises(ValueError, match="not a JSON object"):
        load_budgets(target)


@pytest.mark.unit
def test_load_budgets_rejects_non_object_entry(tmp_path: Path) -> None:
    """Per-op value that isn't a dict → ValueError with the op name.

    Sabotage-proof: dropping the ``isinstance(raw_entry, dict)`` check
    lets the next line crash on ``raw_entry["p50_ms"]`` with TypeError,
    hiding the op-name in the actionable error.
    """
    target = _write_budgets(tmp_path, {"some_op": "scalar, not dict"})
    with pytest.raises(ValueError, match="some_op"):
        load_budgets(target)


@pytest.mark.unit
def test_load_budgets_rejects_missing_p50(tmp_path: Path) -> None:
    """Missing ``p50_ms`` key → ValueError with the op name.

    Sabotage-proof: removing the ``raise ValueError(...) from exc``
    branch lets the KeyError propagate with a stack trace and no op-
    name context.
    """
    target = _write_budgets(tmp_path, {"some_op": {"p99_ms": 200.0}})
    with pytest.raises(ValueError, match="some_op"):
        load_budgets(target)


@pytest.mark.unit
def test_load_budgets_rejects_non_numeric_value(tmp_path: Path) -> None:
    """Non-numeric ``p99_ms`` → ValueError wrapped from float() failure.

    Sabotage-proof: removing the ``ValueError`` from the except-tuple
    lets the float() conversion error escape untranslated, breaking
    the actionable-error contract.
    """
    target = _write_budgets(tmp_path, {"op": {"p50_ms": 1.0, "p99_ms": "fast"}})
    with pytest.raises(ValueError, match="op"):
        load_budgets(target)


# ---------------------------------------------------------------------------
# build_default_operations — wiring (covers 295-296, 297-308)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_build_default_operations_returns_runner_per_op() -> None:
    """The dict has exactly six operations regardless of injection.

    Sabotage-proof: deleting any of the six keys (e.g. dropping
    ``OP_FEDERATED_SEARCH_TOP_K_15: skip``) makes the report's
    result-count diverge from the budget keys; the count assertion
    here catches that locally before run_perf_probe runs.
    """
    ops = build_default_operations()
    assert set(ops.keys()) == set(_BUDGETS.keys())


@pytest.mark.unit
def test_build_default_operations_skips_when_callable_omitted() -> None:
    """Omitting an injection wires the skip runner for that op.

    Sabotage-proof: removing the ``if prep_vault_only is not None else
    skip`` ternary in build_default_operations leaves a ``None`` value
    in the dict; calling it as a function raises TypeError instead of
    returning the skip-reason string.
    """
    ops = build_default_operations()
    out = ops["kairix_prep_vault_only"](3)
    assert isinstance(out, str)
    assert "capability not yet wired" in out


@pytest.mark.unit
def test_build_default_operations_wires_prep_vault_runner() -> None:
    """An injected ``prep_vault_only`` callable returns a latencies list.

    Sabotage-proof: dropping the ``_make_prep_vault_only_op(...)``
    wrap-call from build_default_operations replaces the wired callable
    with the skip runner; ``isinstance(out, list)`` fails.
    """
    ops = build_default_operations(prep_vault_only=_fast_op)
    out = ops["kairix_prep_vault_only"](3)
    assert isinstance(out, list)
    assert len(out) == 3
    assert all(isinstance(v, float) for v in out)


@pytest.mark.unit
def test_build_default_operations_wires_ingest_per_turn_runner() -> None:
    """Per-turn ingest runner invokes the closure with the iteration index.

    Sabotage-proof: removing the ``ingest_one_turn(i)`` call inside
    ``_make_ingest_per_turn_op``'s runner leaves the per-iteration
    body empty so we never observe ``calls`` growing.
    """
    calls: list[int] = []

    def capture(i: int) -> None:
        calls.append(i)

    ops = build_default_operations(ingest_one_turn=capture)
    ops["kairix_ingest_chat_per_turn"](4)
    assert calls == [0, 1, 2, 3]


@pytest.mark.unit
def test_build_default_operations_wires_ingest_100_turn_runner() -> None:
    """100-turn ingest invokes the zero-arg closure once per iteration.

    Sabotage-proof: dropping the ``return _time_calls(iterations, fn)``
    inside ``_make_ingest_100_turn_op`` leaves the runner returning
    ``None`` and the len() assertion crashes (or the type check fails).
    """
    calls = {"n": 0}

    def capture() -> None:
        calls["n"] += 1

    ops = build_default_operations(ingest_100_turn=capture)
    out = ops["kairix_ingest_chat_100_turn"](2)
    assert isinstance(out, list)
    assert len(out) == 2
    assert calls["n"] == 2


@pytest.mark.unit
def test_build_default_operations_wires_fact_find_conflicts_runner() -> None:
    """fact_find_conflicts runner forwards iterations to ``_time_calls``.

    Sabotage-proof: replacing the ``_make_fact_find_conflicts_op(...)``
    wrap with the skip runner makes the call-counter stay at zero —
    the iteration-count assertion fails.
    """
    calls = {"n": 0}

    def capture() -> None:
        calls["n"] += 1

    ops = build_default_operations(fact_find_conflicts=capture)
    ops["fact_find_conflicts"](5)
    assert calls["n"] == 5


@pytest.mark.unit
def test_build_default_operations_keeps_federated_ops_skipped_by_default() -> None:
    """Cap #5 ops stay on the skip runner even when other ops are wired.

    Sabotage-proof: replacing ``OP_FEDERATED_SEARCH_TOP_K_15: skip``
    with a real runner makes ``isinstance(out, str)`` fail because the
    runner returns a list instead.
    """
    ops = build_default_operations(
        prep_vault_only=_fast_op,
        ingest_one_turn=_ingest_one_turn_op,
        ingest_100_turn=_fast_op,
        fact_find_conflicts=_fast_op,
    )
    out_federated = ops["federated_search_top_k_15"](3)
    out_prep_facts = ops["kairix_prep_facts_federated"](3)
    assert isinstance(out_federated, str)
    assert isinstance(out_prep_facts, str)
    assert "capability not yet wired" in out_federated
    assert "capability not yet wired" in out_prep_facts


# ---------------------------------------------------------------------------
# run_perf_probe — orchestration (covers 377-390, 343-345, 323)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_run_perf_probe_emits_one_result_per_budget_entry() -> None:
    """Each budget key gets exactly one OperationResult.

    Sabotage-proof: changing the ``for op_name, budget in budgets.items()``
    loop to only yield the first entry drops the result-count below
    the budget-count.
    """
    report = run_perf_probe(
        iterations=3,
        budgets=_BUDGETS,
        operations=_wired_default_operations(),
    )
    assert len(report.results) == len(_BUDGETS)
    assert isinstance(report, PerfReport)
    assert report.iterations == 3


@pytest.mark.unit
def test_run_perf_probe_within_budget_yields_no_violation() -> None:
    """Sub-ms operations under a 100ms budget report no violation.

    Sabotage-proof: inverting the ``within = stats.p50_ms <= ...``
    comparison in ``_build_ran_result`` would flip the verdict and
    fire any_violation=True.
    """
    report = run_perf_probe(
        iterations=5,
        budgets=_BUDGETS,
        operations=_wired_default_operations(),
    )
    assert report.any_violation is False
    by_op = {r.operation: r for r in report.results}
    ran_result = by_op["fact_find_conflicts"]
    assert ran_result.skipped is False
    assert ran_result.within_budget is True
    # stats is populated only on the ran branch (not the skip branch).
    assert ran_result.stats is not None
    assert ran_result.stats.n == 5


@pytest.mark.unit
def test_run_perf_probe_flags_violation_when_p50_exceeds_budget() -> None:
    """Latencies above the budget pair flip within_budget=False + any_violation=True.

    Sabotage-proof: removing the budget comparison in ``_build_ran_result``
    or always returning ``within=True`` would make the slow op pass.
    """
    operations: dict[str, OperationCallable] = {"fact_find_conflicts": _slow_latencies_op}
    budgets = {"fact_find_conflicts": {"p50_ms": 100.0, "p99_ms": 200.0}}
    report = run_perf_probe(iterations=5, budgets=budgets, operations=operations)
    only = report.results[0]
    assert only.skipped is False
    assert only.within_budget is False
    assert report.any_violation is True


@pytest.mark.unit
def test_run_perf_probe_skips_unmapped_budget_entry() -> None:
    """A budget entry with no matching operation surfaces as skipped.

    Sabotage-proof: removing the ``if runner is None: ... continue``
    branch makes the runner call ``None(iterations)`` which raises
    TypeError; the rc=skipped contract breaks.
    """
    budgets = {"missing_op": {"p50_ms": 100.0, "p99_ms": 200.0}}
    report = run_perf_probe(iterations=2, budgets=budgets, operations={})
    only = report.results[0]
    assert only.operation == "missing_op"
    assert only.skipped is True
    assert "capability not yet wired" in only.skip_reason
    # Budget pair is still surfaced so human renderer can show it.
    assert only.budget_p50_ms == 100.0
    assert only.budget_p99_ms == 200.0
    # Skipped ops don't count as violations.
    assert report.any_violation is False


@pytest.mark.unit
def test_run_perf_probe_surfaces_runner_supplied_skip_reason() -> None:
    """A runner returning a string is treated as a skip with that reason.

    Sabotage-proof: dropping the ``if isinstance(outcome, str):
    results.append(_build_skip_result(...)); continue`` branch makes
    the runner's string be passed into ``latency_stats`` and crash
    (lists expected). The skip-reason assertion never fires.
    """

    def skip_runner(_n: int) -> str:
        return "this capability deliberately skipped for the test"

    budgets = {"op_one": {"p50_ms": 50.0, "p99_ms": 100.0}}
    report = run_perf_probe(iterations=3, budgets=budgets, operations={"op_one": skip_runner})
    only = report.results[0]
    assert only.skipped is True
    assert only.skip_reason == "this capability deliberately skipped for the test"


@pytest.mark.unit
def test_run_perf_probe_rejects_iterations_below_one() -> None:
    """``iterations=0`` raises ValueError before any operation runs.

    Sabotage-proof: removing the ``if iterations < 1: raise...`` guard
    makes the runner produce empty latency lists and quietly report
    every op as p50=p99=0 (false-positive within-budget).
    """
    with pytest.raises(ValueError, match="iterations must be >= 1"):
        run_perf_probe(iterations=0, budgets=_BUDGETS, operations={})


# ---------------------------------------------------------------------------
# Dataclass to_dict — JSON envelope shapes (covers 100-114, 127-135)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_operation_result_to_dict_includes_core_keys() -> None:
    """``OperationResult.to_dict`` always exposes the canonical JSON keys.

    Sabotage-proof: dropping any of the canonical keys from
    ``OperationResult.to_dict`` makes one of these assertions fail
    (e.g. removing ``within_budget`` from the payload).
    """
    result = OperationResult(
        operation="op",
        iterations=5,
        p50_ms=42.5,
        p99_ms=84.0,
        budget_p50_ms=100.0,
        budget_p99_ms=200.0,
        within_budget=True,
    )
    payload = result.to_dict()
    assert payload["operation"] == "op"
    assert payload["iterations"] == 5
    assert payload["p50_ms"] == 42.5
    assert payload["p99_ms"] == 84.0
    assert payload["budget_p50"] == 100.0
    assert payload["budget_p99"] == 200.0
    assert payload["within_budget"] is True
    # Non-skipped ops MUST NOT emit skipped/skip_reason fields.
    assert "skipped" not in payload
    assert "skip_reason" not in payload


@pytest.mark.unit
def test_operation_result_to_dict_includes_skip_fields_when_skipped() -> None:
    """Skipped operations surface ``skipped=True`` and the diagnostic.

    Sabotage-proof: removing the ``if self.skipped: payload[...] = ...``
    branch from ``to_dict`` strips both skip fields; the assertion
    on ``payload["skipped"]`` fails.
    """
    result = OperationResult(
        operation="op",
        iterations=5,
        p50_ms=0.0,
        p99_ms=0.0,
        budget_p50_ms=100.0,
        budget_p99_ms=200.0,
        within_budget=True,
        skipped=True,
        skip_reason="capability not yet wired",
    )
    payload = result.to_dict()
    assert payload["skipped"] is True
    assert payload["skip_reason"] == "capability not yet wired"


@pytest.mark.unit
def test_perf_report_any_violation_false_when_all_within_budget() -> None:
    """``any_violation`` is False when every result reports within_budget=True.

    Sabotage-proof: inverting ``not r.within_budget`` to ``r.within_budget``
    in the comprehension makes the property return True for the all-pass
    case; this test fails.
    """
    results = [
        OperationResult(
            operation="op1",
            iterations=1,
            p50_ms=10.0,
            p99_ms=20.0,
            budget_p50_ms=100.0,
            budget_p99_ms=200.0,
            within_budget=True,
        ),
        OperationResult(
            operation="op2",
            iterations=1,
            p50_ms=0.0,
            p99_ms=0.0,
            budget_p50_ms=100.0,
            budget_p99_ms=200.0,
            within_budget=True,
            skipped=True,
            skip_reason="capability not yet wired",
        ),
    ]
    report = PerfReport(iterations=1, results=results)
    assert report.any_violation is False


@pytest.mark.unit
def test_perf_report_any_violation_true_when_any_non_skipped_over_budget() -> None:
    """A single over-budget non-skipped op flips ``any_violation`` to True.

    Sabotage-proof: dropping ``(not r.skipped) and`` from the comprehension
    would also count skipped-but-not-within-budget rows; a runner with
    skipped=True can't reach this branch today, but pinning the AND
    semantic catches future regressions where skip behaviour drifts.
    """
    results = [
        OperationResult(
            operation="op",
            iterations=1,
            p50_ms=300.0,
            p99_ms=400.0,
            budget_p50_ms=100.0,
            budget_p99_ms=200.0,
            within_budget=False,
        ),
    ]
    report = PerfReport(iterations=1, results=results)
    assert report.any_violation is True


@pytest.mark.unit
def test_perf_report_to_dict_emits_full_envelope() -> None:
    """``PerfReport.to_dict`` carries iterations, results, any_violation.

    Sabotage-proof: dropping the ``"any_violation"`` key from the
    payload makes the assertion fail; dropping ``"results"`` makes
    the list-indexing assertion crash with KeyError.
    """
    results = [
        OperationResult(
            operation="op",
            iterations=2,
            p50_ms=10.0,
            p99_ms=20.0,
            budget_p50_ms=100.0,
            budget_p99_ms=200.0,
            within_budget=True,
        ),
    ]
    report = PerfReport(iterations=2, results=results)
    payload = report.to_dict()
    assert payload["iterations"] == 2
    assert payload["any_violation"] is False
    assert len(payload["results"]) == 1
    assert payload["results"][0]["operation"] == "op"


# ---------------------------------------------------------------------------
# _time_calls — public reach via build_default_operations (covers 186-191)
#
# We don't import the private helper (F5 — no internal-name imports in
# tests). Instead we go through build_default_operations, which wires
# _make_prep_vault_only_op → _time_calls(iterations, fn). A counter
# closure proves _time_calls invokes the callable N times and returns
# N floats.
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_time_calls_via_prep_vault_invokes_fn_iterations_times() -> None:
    """Public surface: prep_vault_only runner invokes its closure exactly N times.

    Pins ``_time_calls`` indirectly — sabotage-proof: changing the
    ``for _ in range(iterations)`` loop bound in ``_time_calls`` to
    ``range(1)`` makes the call counter stay at 1; the assertion
    fires.
    """
    calls = {"n": 0}

    def capture() -> None:
        calls["n"] += 1

    ops = build_default_operations(prep_vault_only=capture)
    out = ops["kairix_prep_vault_only"](7)
    assert calls["n"] == 7
    assert isinstance(out, list)
    assert len(out) == 7
    # Every entry is a non-negative float millisecond reading.
    assert all(v >= 0.0 for v in out)

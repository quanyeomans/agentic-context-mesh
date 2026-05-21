"""Integration tests for ``kairix probe-config --perf`` (Week 4 Stream B).

Drives the per-capability perf budgets surface — the runner in
``kairix/quality/probe/perf_runner.py`` and the ``--perf`` dispatch
path on ``kairix.quality.probe.config_cli.main``.

Test seam: every operation is injected via the ``perf_operations``
kwarg on ``main()`` (and the ``operations`` kwarg on
``run_perf_probe``), so the suite runs sub-second and reaches both
the within-budget and over-budget branches deterministically without
spinning up a real LLM / SQLite ingest pipeline. The runner under
test is exercised end-to-end (load_budgets -> run_perf_probe ->
render) — only the workload closures themselves are faked.

Every test is ``@pytest.mark.integration`` (F8) because it covers
the CLI dispatch path, and embeds a sabotage-proof note tying the
assertion to the production line it pins.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from kairix.quality.probe.config_cli import main as config_cli_main
from kairix.quality.probe.perf_runner import (
    OperationResult,
    PerfReport,
    build_default_operations,
    load_budgets,
    run_perf_probe,
)

# ---------------------------------------------------------------------------
# Tight regression budgets used by the integration tests — small numbers
# so deterministic latencies easily land within / outside budget.
# Operations the integration suite drives explicitly; the others stay
# skipped via build_default_operations (Cap #5 not yet wired).
# ---------------------------------------------------------------------------

_REGRESSION_BUDGETS: dict[str, dict[str, float]] = {
    "kairix_prep_vault_only": {"p50_ms": 100.0, "p99_ms": 200.0},
    "kairix_prep_facts_federated": {"p50_ms": 100.0, "p99_ms": 200.0},
    "kairix_ingest_chat_per_turn": {"p50_ms": 100.0, "p99_ms": 200.0},
    "kairix_ingest_chat_100_turn": {"p50_ms": 100.0, "p99_ms": 200.0},
    "fact_find_conflicts": {"p50_ms": 100.0, "p99_ms": 200.0},
    "federated_search_top_k_15": {"p50_ms": 100.0, "p99_ms": 200.0},
}


def _write_budgets(tmp_path: Path, budgets: dict[str, dict[str, float]]) -> Path:
    """Serialise a budgets dict to a tmp_path JSON file and return its path."""
    target = tmp_path / "budgets.json"
    target.write_text(json.dumps(budgets), encoding="utf-8")
    return target


def _fast_op() -> None:
    """Sub-millisecond operation — every iteration sits well under 100ms p50."""
    # Intentionally empty - the timing measures the function call overhead


def _ingest_one_turn_op(_i: int) -> None:
    """Per-iteration ingest stub — accepts the iteration index but is sub-ms."""
    # Intentionally empty - the timing measures the function call overhead


# ---------------------------------------------------------------------------
# run_perf_probe — happy path
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_run_perf_probe_emits_one_result_per_budget_entry() -> None:
    """Every budgets entry produces an :class:`OperationResult`.

    Sabotage-proof: changing ``for op_name, budget in budgets.items()`` to
    ``budgets.items().__iter__().__next__()`` (only emit the first) drops
    the result-count below the budget-count and the length assert fires.
    """
    report = run_perf_probe(
        iterations=5,
        budgets=_REGRESSION_BUDGETS,
        operations=build_default_operations(
            prep_vault_only=_fast_op,
            ingest_one_turn=_ingest_one_turn_op,
            ingest_100_turn=_fast_op,
            fact_find_conflicts=_fast_op,
        ),
    )
    assert len(report.results) == len(_REGRESSION_BUDGETS)
    assert isinstance(report, PerfReport)


@pytest.mark.integration
def test_run_perf_probe_within_budget_yields_no_violation() -> None:
    """Sub-ms operations are within a 100ms budget → no violation.

    Sabotage-proof: inverting the ``within = stats.p50_ms <= budget...``
    comparison in ``_build_ran_result`` makes fast operations report
    ``within_budget=False`` and ``any_violation=True`` — flipping the
    assertion direction.
    """
    report = run_perf_probe(
        iterations=10,
        budgets=_REGRESSION_BUDGETS,
        operations=build_default_operations(
            prep_vault_only=_fast_op,
            ingest_one_turn=_ingest_one_turn_op,
            ingest_100_turn=_fast_op,
            fact_find_conflicts=_fast_op,
        ),
    )
    assert report.any_violation is False
    by_op = {r.operation: r for r in report.results}
    assert by_op["fact_find_conflicts"].within_budget is True
    assert by_op["fact_find_conflicts"].skipped is False


@pytest.mark.integration
def test_run_perf_probe_skips_unwired_capabilities() -> None:
    """Federated operations stay skipped until Cap #5 wires them.

    Sabotage-proof: dropping the ``OP_FEDERATED_SEARCH_TOP_K_15: skip``
    entry from build_default_operations makes the result for that op
    flip to within_budget=True with p50=0 (no skip marker), and the
    skipped-assertion fails.
    """
    report = run_perf_probe(
        iterations=3,
        budgets=_REGRESSION_BUDGETS,
        operations=build_default_operations(),  # nothing wired
    )
    by_op = {r.operation: r for r in report.results}
    assert by_op["kairix_prep_facts_federated"].skipped is True
    assert "capability not yet wired" in by_op["kairix_prep_facts_federated"].skip_reason
    assert by_op["federated_search_top_k_15"].skipped is True


# ---------------------------------------------------------------------------
# run_perf_probe — violation branch
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_run_perf_probe_flags_violation_when_p99_exceeds_budget() -> None:
    """A slow operation breaches the budget → ``any_violation`` True.

    Drives the over-budget branch by injecting an operation closure
    whose returned latencies sit clearly above the regression budget.

    Sabotage-proof: changing the budget comparison to ``stats.p50_ms <
    budget["p99_ms"]`` (treating any p50 under p99 as "within budget")
    makes the slow op report within_budget=True and the assertion
    fails.
    """
    slow_latencies = [150.0, 160.0, 170.0, 180.0, 190.0]
    # Returns the same canned latencies regardless of iteration count
    # so the percentile maths is deterministic.
    operations = {
        "fact_find_conflicts": lambda _n: list(slow_latencies),
    }
    budgets = {"fact_find_conflicts": {"p50_ms": 100.0, "p99_ms": 200.0}}
    report = run_perf_probe(iterations=5, budgets=budgets, operations=operations)

    only = report.results[0]
    assert isinstance(only, OperationResult)
    assert only.operation == "fact_find_conflicts"
    assert only.skipped is False
    # p50 of [150,160,170,180,190] is 170 > 100 budget — violation
    assert only.within_budget is False
    assert report.any_violation is True


@pytest.mark.integration
def test_run_perf_probe_rejects_iterations_below_one() -> None:
    """``iterations < 1`` → ``ValueError`` with the iteration value.

    Sabotage-proof: removing the ``if iterations < 1: raise...`` guard
    makes the runner produce an empty-list LatencyStats and quietly
    report every op as p50=p99=0 (false-positive "within budget").
    """
    with pytest.raises(ValueError, match="iterations must be >= 1"):
        run_perf_probe(iterations=0, budgets=_REGRESSION_BUDGETS, operations={})


# ---------------------------------------------------------------------------
# load_budgets — JSON parsing + validation
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_load_budgets_round_trips_the_canonical_file(tmp_path: Path) -> None:
    """A well-formed budgets file loads to a dict with float p50/p99.

    Sabotage-proof: dropping the ``float(...)`` cast in load_budgets
    leaves the dict values as ints; the ``isinstance(..., float)``
    check would fail.
    """
    target = _write_budgets(tmp_path, _REGRESSION_BUDGETS)
    loaded = load_budgets(target)
    assert loaded["fact_find_conflicts"]["p50_ms"] == 100.0
    assert isinstance(loaded["fact_find_conflicts"]["p50_ms"], float)


@pytest.mark.integration
def test_load_budgets_raises_on_missing_p99(tmp_path: Path) -> None:
    """Missing budget key → ``ValueError`` with the operation name.

    Sabotage-proof: replacing ``raise ValueError(...) from exc`` with
    a silent ``continue`` makes the bad entry vanish and downstream
    treats every absent operation as "no budget" — the test passes
    only because the guard is in place.
    """
    target = tmp_path / "bad.json"
    target.write_text(json.dumps({"some_op": {"p50_ms": 10.0}}), encoding="utf-8")
    with pytest.raises(ValueError, match="some_op"):
        load_budgets(target)


@pytest.mark.integration
def test_load_budgets_raises_when_root_is_not_object(tmp_path: Path) -> None:
    """Top-level JSON list/string is rejected with an actionable message.

    Sabotage-proof: removing the ``isinstance(raw, dict)`` guard makes
    ``raw.items()`` crash on a list with AttributeError, masking the
    user's actual problem ("you handed us a JSON list").
    """
    target = tmp_path / "wrong_shape.json"
    target.write_text(json.dumps(["not", "an", "object"]), encoding="utf-8")
    with pytest.raises(ValueError, match="not a JSON object"):
        load_budgets(target)


# ---------------------------------------------------------------------------
# config_cli --perf — CLI integration
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_cli_perf_exit_zero_when_all_within_budget(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """All operations within budget → exit 0 + human-readable summary.

    Sabotage-proof: changing the ``return 1 if report.any_violation
    else 0`` to ``return 1`` collapses this happy path to a failing
    exit code and the test fails on the rc assertion.
    """
    budgets_path = _write_budgets(tmp_path, _REGRESSION_BUDGETS)
    rc = config_cli_main(
        ["--perf", "5", "--perf-budgets", str(budgets_path)],
        perf_operations=build_default_operations(
            prep_vault_only=_fast_op,
            ingest_one_turn=_ingest_one_turn_op,
            ingest_100_turn=_fast_op,
            fact_find_conflicts=_fast_op,
        ),
    )
    assert rc == 0
    captured = capsys.readouterr()
    assert "kairix probe-config --perf" in captured.out
    assert "fact_find_conflicts" in captured.out
    assert "PASS" in captured.out


@pytest.mark.integration
def test_cli_perf_exit_one_when_any_operation_breaches_budget(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """One slow op → exit 1 with a FAIL line in human output.

    Sabotage-proof: the assertion ``rc == 1`` is the canonical pin.
    Mutating ``_run_perf_path`` to ``return 0`` regardless of
    violation makes this test fail; mutating the budget comparison
    direction in ``_build_ran_result`` makes the FAIL marker
    disappear and the substring check fires.

    This is the budget-regression catcher the dispatch brief asked
    for: a too-tight budget makes the CLI exit 1.
    """
    budgets_path = _write_budgets(tmp_path, _REGRESSION_BUDGETS)
    operations = build_default_operations(
        prep_vault_only=_fast_op,
        ingest_one_turn=_ingest_one_turn_op,
        ingest_100_turn=_fast_op,
        fact_find_conflicts=_fast_op,
    )
    # Override one operation with a closure that returns latencies
    # clearly above the regression budget so the FAIL branch fires.
    slow_latencies = [150.0, 160.0, 170.0, 180.0, 190.0]
    operations["fact_find_conflicts"] = lambda _n: list(slow_latencies)

    rc = config_cli_main(
        ["--perf", "5", "--perf-budgets", str(budgets_path)],
        perf_operations=operations,
    )
    assert rc == 1
    captured = capsys.readouterr()
    assert "FAIL" in captured.out
    assert "fact_find_conflicts" in captured.out


@pytest.mark.integration
def test_cli_perf_json_envelope_shape(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``--json`` emits an array per the spec: operation/p50/p99/budget/within_budget.

    Sabotage-proof: dropping the ``"within_budget"`` field from
    ``OperationResult.to_dict`` makes the JSON envelope incomplete
    and the field-existence assertion below fails.
    """
    budgets_path = _write_budgets(tmp_path, _REGRESSION_BUDGETS)
    rc = config_cli_main(
        ["--perf", "3", "--perf-budgets", str(budgets_path), "--json"],
        perf_operations=build_default_operations(
            prep_vault_only=_fast_op,
            ingest_one_turn=_ingest_one_turn_op,
            ingest_100_turn=_fast_op,
            fact_find_conflicts=_fast_op,
        ),
    )
    assert rc == 0
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["iterations"] == 3
    assert "results" in payload
    first = payload["results"][0]
    for key in ("operation", "p50_ms", "p99_ms", "budget_p50", "budget_p99", "within_budget"):
        assert key in first, f"JSON envelope missing key {key!r}: {first!r}"


@pytest.mark.integration
def test_cli_perf_missing_budgets_file_returns_two(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Nonexistent ``--perf-budgets`` path → exit 2 + actionable stderr.

    Sabotage-proof: removing the ``if not budgets_path.exists(): return
    _invalid_args(...)`` guard makes ``load_budgets`` raise OSError
    further down with a less actionable message.
    """
    rc = config_cli_main(
        ["--perf", "3", "--perf-budgets", str(tmp_path / "missing.json")],
        perf_operations=build_default_operations(),
    )
    assert rc == 2
    captured = capsys.readouterr()
    assert "fix:" in captured.err


@pytest.mark.integration
def test_cli_perf_rejects_zero_iterations(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``--perf 0`` → exit 2 + actionable stderr.

    Sabotage-proof: removing the ``if iterations < 1: return
    _invalid_args(...)`` guard in ``_run_perf_path`` lets the runner
    receive 0 and raise ValueError up the stack, breaking the
    operator-facing error contract.
    """
    budgets_path = _write_budgets(tmp_path, _REGRESSION_BUDGETS)
    rc = config_cli_main(
        ["--perf", "0", "--perf-budgets", str(budgets_path)],
        perf_operations=build_default_operations(),
    )
    assert rc == 2
    captured = capsys.readouterr()
    assert "perf iterations must be >= 1" in captured.err


@pytest.mark.integration
def test_cli_perf_skipped_operation_renders_skip_diagnostic(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Cap #5-skipped operations show their skip reason in human output.

    Sabotage-proof: dropping the ``if r.skipped: lines.append(... skip
    reason ...); continue`` branch from ``_render_perf_human`` renders
    skipped ops as if they ran with p50=0/p99=0, hiding the operator-
    facing diagnostic.
    """
    budgets_path = _write_budgets(tmp_path, _REGRESSION_BUDGETS)
    rc = config_cli_main(
        ["--perf", "3", "--perf-budgets", str(budgets_path)],
        perf_operations=build_default_operations(),  # nothing wired
    )
    # All operations skipped → no violation → rc 0
    assert rc == 0
    captured = capsys.readouterr()
    assert "capability not yet wired" in captured.out
    assert "kairix_prep_facts_federated" in captured.out

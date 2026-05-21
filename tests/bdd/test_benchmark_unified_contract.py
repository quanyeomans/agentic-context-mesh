"""pytest-bdd loader for ``benchmark_unified_contract.feature``.

P5 of the unified benchmark initiative — wires the canonical-contract
feature file into the pytest-bdd collector. Each Scenario / Scenario
Outline gets one ``@scenario`` declaration here; the step bodies live
in ``tests/bdd/steps/benchmark_unified_contract_steps.py``.

Scenarios that depend on slices not yet shipped (P3.c soak-mode
dispatcher) are tagged with ``@pytest.mark.skip`` so the feature file
collects clean and reports a real skip rather than a NotImplementedError.
The skip rationale carries an F21 affordance pointing at the next
implementation slice.

This module follows the ``tests/bdd/test_benchmark_run.py`` pattern —
one test function per scenario, body populated by ``@scenario`` from
the .feature file.
"""

from pathlib import Path

import pytest
from pytest_bdd import scenario

FEATURE = str(Path(__file__).parent / "features" / "benchmark_unified_contract.feature")

# F11 rationale lives inline on each @pytest.mark.skip(reason=...) decorator
# below — the check_test_skip_rationale.py gate requires a literal string,
# not a module-level constant reference. The two reasons follow the F21
# template (fix / next / run markers) so a developer running the test sees
# the affordance without leaving the file.

# ---------------------------------------------------------------------------
# Quantitative retrieval ranking — Scenario Outline (6 rows)
# ---------------------------------------------------------------------------


@pytest.mark.bdd
@scenario(FEATURE, "Quantitative retrieval ranking by query type")
def test_quantitative_retrieval_ranking_by_query_type():
    """Body populated by @scenario from the .feature file."""


# ---------------------------------------------------------------------------
# Qualitative answer correctness — Scenario Outline (7 rows)
# ---------------------------------------------------------------------------


@pytest.mark.bdd
@scenario(FEATURE, "Qualitative answer correctness via LLM judge")
def test_qualitative_answer_correctness_via_llm_judge():
    """Body populated by @scenario from the .feature file."""


# ---------------------------------------------------------------------------
# Combined retrieval + synthesis consistency
# ---------------------------------------------------------------------------


@pytest.mark.bdd
@scenario(FEATURE, "Combined retrieval and synthesis consistency")
def test_combined_retrieval_and_synthesis_consistency():
    """Body populated by @scenario from the .feature file."""


# ---------------------------------------------------------------------------
# Performance — per-query latency (single-shot mode)
# ---------------------------------------------------------------------------


@pytest.mark.bdd
@scenario(FEATURE, "Per-query latency under cold and warm conditions")
def test_per_query_latency_under_cold_and_warm_conditions():
    """Body populated by @scenario from the .feature file."""


# ---------------------------------------------------------------------------
# Performance — concurrent mode (P3.b stub)
# ---------------------------------------------------------------------------


@pytest.mark.bdd
@pytest.mark.skip(
    reason=(
        "P3.b — concurrent-mode dispatcher pending implementation. "
        "fix: implement run_concurrent in kairix/quality/benchmark/modes/concurrent.py per spike C2 §3.2. "
        "next: drop the @pytest.mark.skip once the dispatcher lands. "
        "run: bash scripts/safe-commit.sh after the body lands."
    )
)
@scenario(FEATURE, "Sustained throughput under concurrent load")
def test_sustained_throughput_under_concurrent_load():
    """Body populated by @scenario from the .feature file (skipped — P3.b)."""


# ---------------------------------------------------------------------------
# Stability — soak (P3.c stub)
# ---------------------------------------------------------------------------


@pytest.mark.bdd
@pytest.mark.skip(
    reason=(
        "P3.c — soak-mode dispatcher pending implementation. "
        "fix: implement run_soak in kairix/quality/benchmark/modes/soak.py per spike C2 §3.3. "
        "next: drop the @pytest.mark.skip once the dispatcher lands. "
        "run: bash scripts/safe-commit.sh after the body lands."
    )
)
@scenario(FEATURE, "No resource growth across repeated workloads")
def test_no_resource_growth_across_repeated_workloads():
    """Body populated by @scenario from the .feature file (skipped — P3.c)."""


# ---------------------------------------------------------------------------
# Scope / agent / collection RBAC — Scenario Outline (3 rows)
# ---------------------------------------------------------------------------


@pytest.mark.bdd
@scenario(FEATURE, "Scope and collection filtering respect RBAC boundaries")
def test_scope_and_collection_filtering_respect_rbac_boundaries():
    """Body populated by @scenario from the .feature file."""


# ---------------------------------------------------------------------------
# CI integration — gate-failure exit codes
# ---------------------------------------------------------------------------


@pytest.mark.bdd
@scenario(FEATURE, "Gate-failure exit code surfaces actual versus expected values")
def test_gate_failure_exit_code_surfaces_actual_versus_expected_values():
    """Body populated by @scenario from the .feature file."""


# ---------------------------------------------------------------------------
# Focus-area segmentation
# ---------------------------------------------------------------------------


@pytest.mark.bdd
@scenario(FEATURE, "Focus-area segmentation selects the requested subset")
def test_focus_area_segmentation_selects_the_requested_subset():
    """Body populated by @scenario from the .feature file."""

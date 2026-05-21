"""Step definitions for eval_suite.feature.

Drives :func:`kairix.use_cases.eval_suite.main` end-to-end with fakes
from ``tests/fakes.py``. F1-clean: no monkeypatching, no internal-
attribute reassignment - every collaborator is passed as a kwarg.
F13-clean: scenarios reference operator concepts (suite, baseline,
backend) rather than implementation symbols.
"""

from __future__ import annotations

import io
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from pytest_bdd import given, parsers, then, when

from kairix.paths import KairixPaths
from kairix.use_cases import eval_suite as _eval_suite
from tests.fakes import FakeFactExtractor, FakeFactStore, FakeLLMBackend

pytestmark = pytest.mark.bdd


# ---------------------------------------------------------------------------
# Per-scenario state
# ---------------------------------------------------------------------------


@dataclass
class _State:
    """Mutable per-scenario state - fresh per scenario."""

    suite_dir: Path
    baseline_dir: Path
    fact_store: FakeFactStore = field(default_factory=FakeFactStore)
    fact_extractor: FakeFactExtractor = field(default_factory=FakeFactExtractor)
    llm: FakeLLMBackend = field(default_factory=lambda: FakeLLMBackend(chat_response="1.0"))
    stdout: io.StringIO = field(default_factory=io.StringIO)
    stderr: io.StringIO = field(default_factory=io.StringIO)
    exit_code: int | None = None
    paths: KairixPaths | None = None


@pytest.fixture
def _eval_state(tmp_path: Path) -> _State:
    """Fresh state for each scenario with a tmpdir-rooted suite directory."""
    suite_dir = tmp_path / "engagement-alpha"
    suite_dir.mkdir()
    baseline_dir = tmp_path / "expected"
    baseline_dir.mkdir()
    state = _State(suite_dir=suite_dir, baseline_dir=baseline_dir)
    state.paths = KairixPaths(
        document_root=tmp_path / "vault",
        db_path=tmp_path / "kairix.db",
        log_dir=tmp_path / "logs",
        workspace_root=tmp_path / "workspaces",
    )
    return state


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_session(state: _State, name: str, turns: list[dict[str, Any]]) -> None:
    """Write a session JSONL file under the suite directory."""
    (state.suite_dir / name).write_text("\n".join(json.dumps(t) for t in turns) + "\n", encoding="utf-8")


def _write_queries(state: _State, queries: list[dict[str, Any]]) -> None:
    """Write the ground-truth-queries.json file under the suite directory."""
    (state.suite_dir / "ground-truth-queries.json").write_text(json.dumps(queries), encoding="utf-8")


def _write_baseline(state: _State, mean: float) -> None:
    """Pin a baseline SuiteResult file under the baseline directory."""
    baseline_path = state.baseline_dir / f"{state.suite_dir.name}.json"
    baseline_path.write_text(
        json.dumps(
            {
                "suite_name": state.suite_dir.name,
                "n_questions": 0,
                "n_passed": 0,
                "mean_score": mean,
                "per_category": {},
                "per_extraction_f1": None,
                "extraction_precision": None,
                "extraction_recall": None,
                "rows": [],
            }
        ),
        encoding="utf-8",
    )


def _invoke_main(state: _State, argv: list[str]) -> None:
    """Invoke the use case main with the assembled fakes.

    Plan B-parity D3 — these legacy BDD scenarios exercise the
    fact_store-direct path (the only path the BDD suite knew about
    before D3). Append ``--legacy-direct`` so the new ``--via-prep``
    default doesn't try to construct a real ``build_search_pipeline()``
    against the test's ephemeral fake-only environment. The via-prep
    code path has dedicated unit + integration coverage in
    ``tests/quality/eval/test_suite_runner_pipeline_path.py`` and
    ``tests/integration/test_eval_via_prep_round_trip.py``.
    """
    assert state.paths is not None
    argv_with_default = (
        argv if any(flag in argv for flag in ("--via-prep", "--legacy-direct")) else [*argv, "--legacy-direct"]
    )
    code = _eval_suite.main(
        argv_with_default,
        out=state.stdout,
        err=state.stderr,
        paths=state.paths,
        fact_store=state.fact_store,
        fact_extractor=state.fact_extractor,
        llm=state.llm,
    )
    state.exit_code = code


# ---------------------------------------------------------------------------
# Given - set up suite + backend
# ---------------------------------------------------------------------------


@given(parsers.parse("a suite directory with {single:d} single-hop questions and {multi:d} multi-hop question"))
def _given_categorised_suite(_eval_state: _State, single: int, multi: int) -> None:
    _write_session(
        _eval_state,
        "session-001.jsonl",
        [{"id": "t1", "speaker": "agent-alpha", "content": "hello"}],
    )
    queries = [{"question": f"Q{i}?", "answer": "a", "category": "single-hop"} for i in range(single)] + [
        {"question": f"M{i}?", "answer": "a", "category": "multi-hop"} for i in range(multi)
    ]
    _write_queries(_eval_state, queries)


@given(parsers.parse("a suite directory with {n:d} questions"))
def _given_n_question_suite(_eval_state: _State, n: int) -> None:
    _write_session(
        _eval_state,
        "session-001.jsonl",
        [{"id": "t1", "speaker": "agent-alpha", "content": "hello"}],
    )
    _write_queries(
        _eval_state,
        [{"question": f"Q{i}?", "answer": "a", "category": "single-hop"} for i in range(n)],
    )


@given("a suite directory that is missing the ground truth queries file")
def _given_missing_gt_queries(_eval_state: _State) -> None:
    _write_session(
        _eval_state,
        "session-001.jsonl",
        [{"id": "t1", "speaker": "agent-alpha", "content": "hello"}],
    )
    # No ground-truth-queries.json - that's the scenario.


@given("a configured backend that scores every question correctly")
def _given_backend_scores_correct(_eval_state: _State) -> None:
    _eval_state.llm = FakeLLMBackend(chat_response="1.0")


@given(parsers.parse("a configured backend that scores every question at {score:f}"))
def _given_backend_fixed_score(_eval_state: _State, score: float) -> None:
    _eval_state.llm = FakeLLMBackend(chat_response=str(score))


@given(parsers.parse("a pinned baseline whose mean score is {mean:f}"))
def _given_pinned_baseline(_eval_state: _State, mean: float) -> None:
    _write_baseline(_eval_state, mean)


# ---------------------------------------------------------------------------
# When - operator runs the eval
# ---------------------------------------------------------------------------


@when("the operator runs kairix eval against the suite directory")
def _when_run_eval(_eval_state: _State) -> None:
    _invoke_main(_eval_state, [str(_eval_state.suite_dir)])


@when("the operator runs kairix eval with regression-against the baseline directory")
def _when_run_eval_with_regression(_eval_state: _State) -> None:
    _invoke_main(
        _eval_state,
        [
            str(_eval_state.suite_dir),
            "--regression-against",
            str(_eval_state.baseline_dir),
        ],
    )


# ---------------------------------------------------------------------------
# Then - assertions
# ---------------------------------------------------------------------------


@then(parsers.parse("the suite passes all {n:d} questions"))
def _then_passes_all(_eval_state: _State, n: int) -> None:
    assert _eval_state.exit_code == 0
    assert f"{n}/{n}" in _eval_state.stdout.getvalue()


@then("the report contains a single-hop category line")
def _then_single_hop_line(_eval_state: _State) -> None:
    assert "single-hop" in _eval_state.stdout.getvalue()


@then("the report contains a multi-hop category line")
def _then_multi_hop_line(_eval_state: _State) -> None:
    assert "multi-hop" in _eval_state.stdout.getvalue()


@then("the eval exits with a regression failure")
def _then_regression_failure(_eval_state: _State) -> None:
    assert _eval_state.exit_code == 1
    assert "REGRESSION" in _eval_state.stderr.getvalue()


@then("the report names the suite that regressed")
def _then_names_suite(_eval_state: _State) -> None:
    assert _eval_state.suite_dir.name in _eval_state.stderr.getvalue()


@then("the eval exits with success")
def _then_exit_success(_eval_state: _State) -> None:
    assert _eval_state.exit_code == 0


@then("the eval exits with an actionable error message about ground-truth-queries.json")
def _then_actionable_error(_eval_state: _State) -> None:
    assert _eval_state.exit_code == 2
    err = _eval_state.stderr.getvalue()
    assert "ground-truth-queries.json" in err
    assert "fix:" in err
    assert "next:" in err

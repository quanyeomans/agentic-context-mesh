"""Step definitions for eval_ci_gates.feature.

Asserts the Week 4 Stream A CI plumbing parses + is wired correctly:

- ``reflib-benchmark-gate.yml`` defines ``conversation-eval-gate`` and
  shells to ``scripts/ci/eval-conversation-corpora.sh``.
- ``eval-locomo-nightly.yml`` triggers on the documented cron and shells
  to both nightly helper scripts.
- Every ``reference-library/conversations/engagement-*`` corpus has a
  baseline file under ``reference-library/conversations/expected/``.
- The new workflow surface introduces no F10 silencers.

F1-clean: no monkeypatching. F13-clean: scenarios reference operator
concepts (workflows, jobs, scripts, baselines) — no implementation
symbols.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
import yaml
from pytest_bdd import given, then, when

pytestmark = pytest.mark.bdd


# Repo root: resolved from this test file's location so the suite runs
# from any CWD (CI uses the repo root; local dev sometimes uses /tmp).
_REPO_ROOT = Path(__file__).resolve().parents[3]


@dataclass
class _State:
    """Per-scenario state container."""

    workflow_path: Path | None = None
    parsed: dict[str, Any] = field(default_factory=dict)
    baselines: dict[str, dict[str, Any]] = field(default_factory=dict)


@pytest.fixture
def gates_state() -> _State:
    """Fresh state per scenario — every Given resets the container."""
    return _State()


# ---------------------------------------------------------------------------
# Conversation-eval-gate workflow scenario
# ---------------------------------------------------------------------------


@given("the reflib benchmark gate workflow file exists")
def _given_reflib_workflow_exists(gates_state: _State) -> None:
    path = _REPO_ROOT / ".github" / "workflows" / "reflib-benchmark-gate.yml"
    assert path.exists(), f"missing workflow file at {path}"
    gates_state.workflow_path = path


@when("I parse the workflow as YAML")
def _when_parse_workflow(gates_state: _State) -> None:
    assert gates_state.workflow_path is not None
    gates_state.parsed = yaml.safe_load(gates_state.workflow_path.read_text(encoding="utf-8"))


@then("it defines a job named conversation-eval-gate")
def _then_conversation_eval_job(gates_state: _State) -> None:
    jobs = gates_state.parsed.get("jobs") or {}
    assert "conversation-eval-gate" in jobs, (
        f"workflow {gates_state.workflow_path} is missing the 'conversation-eval-gate' job "
        f"(jobs present: {sorted(jobs)})"
    )


@then("the job calls the eval-conversation-corpora helper script")
def _then_calls_corpora_script(gates_state: _State) -> None:
    job = (gates_state.parsed.get("jobs") or {}).get("conversation-eval-gate") or {}
    run_texts = [step.get("run", "") for step in job.get("steps", []) if isinstance(step, dict)]
    assert any("scripts/ci/eval-conversation-corpora.sh" in text for text in run_texts), (
        "conversation-eval-gate job does not shell to scripts/ci/eval-conversation-corpora.sh"
    )
    script_path = _REPO_ROOT / "scripts" / "ci" / "eval-conversation-corpora.sh"
    assert script_path.exists(), f"helper script not found at {script_path}"


@then("the job uploads the per-corpus result artifact")
def _then_uploads_artifact(gates_state: _State) -> None:
    job = (gates_state.parsed.get("jobs") or {}).get("conversation-eval-gate") or {}
    uses = [step.get("uses", "") for step in job.get("steps", []) if isinstance(step, dict)]
    assert any(use.startswith("actions/upload-artifact") for use in uses), (
        "conversation-eval-gate job is missing an actions/upload-artifact step"
    )


# ---------------------------------------------------------------------------
# LoCoMo nightly workflow scenario
# ---------------------------------------------------------------------------


@given("the eval-locomo-nightly workflow file exists")
def _given_locomo_workflow_exists(gates_state: _State) -> None:
    path = _REPO_ROOT / ".github" / "workflows" / "eval-locomo-nightly.yml"
    assert path.exists(), f"missing workflow file at {path}"
    gates_state.workflow_path = path


@then("it triggers on a daily schedule at 03:00 UTC")
def _then_cron_schedule(gates_state: _State) -> None:
    # The YAML key ``on`` is reserved in some YAML parsers — pyyaml maps
    # it to the literal string "on" in dict keys, but some configurations
    # coerce it to a boolean True. Check both shapes.
    on_block: Any = gates_state.parsed.get("on")
    if on_block is None:
        on_block = gates_state.parsed.get(True)  # pyyaml 1.1 quirk
    assert isinstance(on_block, dict), f"workflow on: block has unexpected shape: {on_block!r}"
    schedule = on_block.get("schedule")
    assert isinstance(schedule, list) and schedule, "expected a schedule list under on:"
    crons = [entry.get("cron") for entry in schedule if isinstance(entry, dict)]
    assert "0 3 * * *" in crons, f"expected cron '0 3 * * *' in schedules, got {crons}"


@then("it runs the locomo-nightly-run helper script")
def _then_runs_nightly_run(gates_state: _State) -> None:
    job_steps = _flatten_run_texts(gates_state.parsed)
    assert any("scripts/ci/locomo-nightly-run.sh" in text for text in job_steps), (
        "LoCoMo nightly workflow does not shell to scripts/ci/locomo-nightly-run.sh"
    )
    assert (_REPO_ROOT / "scripts" / "ci" / "locomo-nightly-run.sh").exists()


@then("it runs the locomo-nightly-compare helper script")
def _then_runs_nightly_compare(gates_state: _State) -> None:
    job_steps = _flatten_run_texts(gates_state.parsed)
    assert any("scripts/ci/locomo-nightly-compare.sh" in text for text in job_steps), (
        "LoCoMo nightly workflow does not shell to scripts/ci/locomo-nightly-compare.sh"
    )
    assert (_REPO_ROOT / "scripts" / "ci" / "locomo-nightly-compare.sh").exists()


# ---------------------------------------------------------------------------
# Baseline-file coverage scenario
# ---------------------------------------------------------------------------


@given("every engagement-* corpus under reference-library/conversations")
def _given_engagement_corpora(gates_state: _State) -> None:
    corpora_dir = _REPO_ROOT / "reference-library" / "conversations"
    corpora = sorted(p for p in corpora_dir.glob("engagement-*") if p.is_dir())
    assert corpora, f"no engagement-* corpora found under {corpora_dir}"
    gates_state.baselines = {p.name: {} for p in corpora}


@when("I look for a baseline file under reference-library/conversations/expected")
def _when_look_for_baselines(gates_state: _State) -> None:
    expected_dir = _REPO_ROOT / "reference-library" / "conversations" / "expected"
    import json

    for name in list(gates_state.baselines):
        baseline_path = expected_dir / f"{name}.json"
        if baseline_path.exists():
            gates_state.baselines[name] = json.loads(baseline_path.read_text(encoding="utf-8"))
        else:
            gates_state.baselines[name] = {"__missing__": True}


@then("a baseline file exists for every corpus")
def _then_baselines_present(gates_state: _State) -> None:
    missing = [name for name, payload in gates_state.baselines.items() if payload.get("__missing__")]
    assert not missing, f"missing baseline files for: {missing}"


@then("the baseline file is either a SuiteResult shape or the sentinel shape")
def _then_baseline_shape_valid(gates_state: _State) -> None:
    for name, payload in gates_state.baselines.items():
        is_sentinel = payload.get("baseline") == "not-yet-measured"
        is_suite_result = "n_questions" in payload and "n_passed" in payload and "mean_score" in payload
        assert is_sentinel or is_suite_result, (
            f"baseline {name}.json is neither sentinel nor SuiteResult shape (keys: {sorted(payload)})"
        )


# ---------------------------------------------------------------------------
# Silencer-scan scenario
# ---------------------------------------------------------------------------


@given("the conversation-eval-gate and locomo-nightly workflows")
def _given_both_workflows(gates_state: _State) -> None:
    gates_state.baselines = {
        "reflib": (_REPO_ROOT / ".github" / "workflows" / "reflib-benchmark-gate.yml").read_text(encoding="utf-8"),
        "locomo": (_REPO_ROOT / ".github" / "workflows" / "eval-locomo-nightly.yml").read_text(encoding="utf-8"),
    }


@when("I scan the run steps for known silencer patterns")
def _when_scan_silencers(gates_state: _State) -> None:
    # No-op — the scan runs inline in the Then steps so the assertion
    # error message can name the offending file directly.
    pass


@then("no continue-on-error: true is present without a rationale comment")
def _then_no_continue_on_error(gates_state: _State) -> None:
    for name, text in gates_state.baselines.items():
        for lineno, line in enumerate(text.splitlines(), start=1):
            stripped = line.strip()
            if stripped.startswith("continue-on-error:") and "true" in stripped:
                pytest.fail(
                    f"{name} workflow line {lineno} introduces continue-on-error: true — "
                    f"forbidden by F10 unless paired with a rationale comment"
                )


@then("no fail_ci_if_error: false is present without a rationale comment")
def _then_no_fail_ci_if_error_false(gates_state: _State) -> None:
    for name, text in gates_state.baselines.items():
        for lineno, line in enumerate(text.splitlines(), start=1):
            stripped = line.strip()
            if stripped.startswith("fail_ci_if_error:") and "false" in stripped:
                pytest.fail(
                    f"{name} workflow line {lineno} introduces fail_ci_if_error: false — "
                    f"forbidden by F10 unless paired with a rationale comment"
                )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _flatten_run_texts(parsed: dict[str, Any]) -> list[str]:
    """Return every 'run:' text across every job's steps for grepping."""
    out: list[str] = []
    for job in (parsed.get("jobs") or {}).values():
        if not isinstance(job, dict):
            continue
        for step in job.get("steps", []) or []:
            if isinstance(step, dict) and "run" in step:
                out.append(str(step["run"]))
    return out

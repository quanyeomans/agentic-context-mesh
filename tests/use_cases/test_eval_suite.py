"""Integration tests for ``kairix.use_cases.eval_suite``.

Drives :func:`main` end-to-end with fakes injected via kwargs. The
``--json`` output is parsed back into a Python dict to assert the
machine-readable surface, and the regression gate is exercised against
a pinned baseline written to a tmpdir.

Every test is sabotage-proven (mutate prod → fail → restore → pass).
F1-clean: no monkeypatching, no internal-attribute reassignment.
"""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from kairix.paths import KairixPaths
from kairix.use_cases import eval_suite as _use_case
from tests.fakes import FakeFactExtractor, FakeFactStore, FakeLLMBackend

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _paths(tmp_path: Path) -> KairixPaths:
    """KairixPaths pinned to tmp_path; never reads env."""
    return KairixPaths(
        document_root=tmp_path / "vault",
        db_path=tmp_path / "kairix.db",
        log_dir=tmp_path / "logs",
        workspace_root=tmp_path / "workspaces",
    )


def _make_suite_dir(tmp_path: Path, *, name: str = "engagement-alpha") -> Path:
    """Lay out a minimal-but-valid suite directory under ``tmp_path/name``."""
    suite = tmp_path / name
    suite.mkdir()
    (suite / "session-001.jsonl").write_text(
        json.dumps({"id": "t1", "speaker": "agent-alpha", "content": "hello"}) + "\n",
        encoding="utf-8",
    )
    (suite / "ground-truth-queries.json").write_text(
        json.dumps(
            [
                {"question": "Q1?", "answer": "A1", "category": "single-hop"},
                {"question": "Q2?", "answer": "A2", "category": "multi-hop"},
            ]
        ),
        encoding="utf-8",
    )
    return suite


def _invoke(
    argv: list[str],
    *,
    tmp_path: Path,
    chat_response: str = "1.0",
) -> tuple[int, str, str]:
    """Run the use case main and return (exit_code, stdout, stderr)."""
    out = io.StringIO()
    err = io.StringIO()
    code = _use_case.main(
        argv,
        out=out,
        err=err,
        paths=_paths(tmp_path),
        fact_store=FakeFactStore(),
        fact_extractor=FakeFactExtractor(),
        llm=FakeLLMBackend(chat_response=chat_response),
    )
    return code, out.getvalue(), err.getvalue()


# ---------------------------------------------------------------------------
# Happy-path human output
# ---------------------------------------------------------------------------


def test_main_emits_human_readable_per_category_summary(tmp_path: Path) -> None:
    """Sabotage-proof: drop the ``Suite:`` prefix in ``_format_human`` and
    this fails because the human summary no longer starts with it."""
    suite = _make_suite_dir(tmp_path)
    code, out, _ = _invoke([str(suite)], tmp_path=tmp_path, chat_response="1.0")

    assert code == 0
    assert out.startswith("Suite: engagement-alpha")
    assert "single-hop" in out
    assert "multi-hop" in out
    # Both categories scored 1.0 -> 2/2 questions passed.
    assert "2/2" in out


def test_main_reports_pass_rate_with_percentage(tmp_path: Path) -> None:
    """Sabotage-proof: drop the percentage formatter and this fails."""
    suite = _make_suite_dir(tmp_path)
    code, out, _ = _invoke([str(suite)], tmp_path=tmp_path, chat_response="1.0")

    assert code == 0
    assert "(100%)" in out


# ---------------------------------------------------------------------------
# --json output
# ---------------------------------------------------------------------------


def test_main_json_flag_emits_machine_readable_suite_result(tmp_path: Path) -> None:
    """Sabotage-proof: drop the ``--json`` branch in ``main`` and this
    fails because ``json.loads`` raises on the human-readable banner."""
    suite = _make_suite_dir(tmp_path)
    code, out, _ = _invoke([str(suite), "--json"], tmp_path=tmp_path, chat_response="1.0")

    assert code == 0
    payload = json.loads(out)
    assert payload["suite_name"] == "engagement-alpha"
    assert payload["n_questions"] == 2
    assert payload["n_passed"] == 2
    assert "single-hop" in payload["per_category"]
    assert "multi-hop" in payload["per_category"]


def test_main_json_format_round_trips_per_category(tmp_path: Path) -> None:
    """Sabotage-proof: drop ``per_category`` from the SuiteResult dataclass
    and this fails because the key is absent in the JSON."""
    suite = _make_suite_dir(tmp_path)
    _, out, _ = _invoke([str(suite), "--json"], tmp_path=tmp_path, chat_response="0.8")
    payload = json.loads(out)

    # Both categories present, each carrying n/passed/mean.
    for cat in ("single-hop", "multi-hop"):
        stats = payload["per_category"][cat]
        assert "n" in stats
        assert "passed" in stats
        assert "mean" in stats


# ---------------------------------------------------------------------------
# --regression-against
# ---------------------------------------------------------------------------


def _write_baseline(tmp_path: Path, suite_name: str, mean: float) -> Path:
    """Pin a baseline JSON file under ``tmp_path/expected/``."""
    baseline_dir = tmp_path / "expected"
    baseline_dir.mkdir(exist_ok=True)
    (baseline_dir / f"{suite_name}.json").write_text(
        json.dumps(
            {
                "suite_name": suite_name,
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
    return baseline_dir


def test_main_regression_gate_passes_when_within_tolerance(tmp_path: Path) -> None:
    """Sabotage-proof: invert the regression-tolerance comparison and this
    fails because a passing run is reported as a regression."""
    suite = _make_suite_dir(tmp_path)
    baseline_dir = _write_baseline(tmp_path, "engagement-alpha", mean=0.5)

    code, _, _ = _invoke(
        [str(suite), "--regression-against", str(baseline_dir)],
        tmp_path=tmp_path,
        chat_response="1.0",
    )
    assert code == 0


def test_main_regression_gate_fails_when_below_tolerance(tmp_path: Path) -> None:
    """Sabotage-proof: drop the regression-exit-1 path and this fails
    because the gate returns 0 even on a significant regression."""
    suite = _make_suite_dir(tmp_path)
    baseline_dir = _write_baseline(tmp_path, "engagement-alpha", mean=0.95)

    # Run scores at 0.2 mean -> 75pp drop, well above the 2pp tolerance.
    code, _, err = _invoke(
        [str(suite), "--regression-against", str(baseline_dir)],
        tmp_path=tmp_path,
        chat_response="0.2",
    )
    assert code == 1
    assert "REGRESSION" in err
    assert "engagement-alpha" in err
    assert "fix:" in err
    assert "next:" in err


def test_main_regression_gate_missing_baseline_is_actionable(tmp_path: Path) -> None:
    """Sabotage-proof: drop the missing-baseline ValueError path and this
    fails because the exit code drops to 0 even with no baseline."""
    suite = _make_suite_dir(tmp_path)
    empty_baseline_dir = tmp_path / "expected"
    empty_baseline_dir.mkdir()

    code, _, err = _invoke(
        [str(suite), "--regression-against", str(empty_baseline_dir)],
        tmp_path=tmp_path,
        chat_response="1.0",
    )
    assert code == 2
    assert "baseline" in err
    assert "fix:" in err


# ---------------------------------------------------------------------------
# --backend validation
# ---------------------------------------------------------------------------


def test_main_backend_flag_accepts_documented_backends(tmp_path: Path) -> None:
    """Sabotage-proof: shrink the choices tuple and this test fails because
    valid backends get rejected by argparse."""
    suite = _make_suite_dir(tmp_path)
    for backend in ("kairix-native", "mem0"):
        code, _, _ = _invoke([str(suite), "--backend", backend], tmp_path=tmp_path, chat_response="1.0")
        assert code == 0


def test_main_backend_flag_rejects_unknown_backend(tmp_path: Path) -> None:
    """Sabotage-proof: remove the choices kwarg on ``--backend`` and this
    fails because argparse no longer rejects bogus backends."""
    suite = _make_suite_dir(tmp_path)
    out = io.StringIO()
    err = io.StringIO()
    with pytest.raises(SystemExit) as excinfo:
        _use_case.main(
            [str(suite), "--backend", "bogus"],
            out=out,
            err=err,
            paths=_paths(tmp_path),
            fact_store=FakeFactStore(),
            fact_extractor=FakeFactExtractor(),
            llm=FakeLLMBackend(chat_response="1.0"),
        )
    # argparse exits with code 2 on usage errors.
    assert excinfo.value.code == 2


# ---------------------------------------------------------------------------
# Missing ground truth
# ---------------------------------------------------------------------------


def test_main_missing_queries_file_emits_actionable_error(tmp_path: Path) -> None:
    """Sabotage-proof: drop the ValueError catch in ``main`` and this
    fails because the unhandled exception escapes."""
    suite = tmp_path / "broken"
    suite.mkdir()
    (suite / "session-001.jsonl").write_text(json.dumps({"id": "x", "content": "y"}) + "\n", encoding="utf-8")
    # No ground-truth-queries.json.

    code, _, err = _invoke([str(suite)], tmp_path=tmp_path, chat_response="1.0")
    assert code == 2
    assert "ground-truth-queries.json" in err
    assert "fix:" in err
    assert "next:" in err


# ---------------------------------------------------------------------------
# Legacy passthrough — preserves existing kairix.quality.eval.cli surface
# ---------------------------------------------------------------------------


def test_main_unknown_first_arg_treats_as_suite_path(tmp_path: Path) -> None:
    """Sabotage-proof: change the legacy-subcommand frozenset to include
    every string and this fails because a real suite path is misrouted."""
    suite = _make_suite_dir(tmp_path)
    code, out, _ = _invoke([str(suite)], tmp_path=tmp_path, chat_response="1.0")
    assert code == 0
    assert "Suite:" in out


def test_main_metric_flag_accepts_documented_values(tmp_path: Path) -> None:
    """Sabotage-proof: shrink the metric choices tuple and this fails."""
    suite = _make_suite_dir(tmp_path)
    for metric in ("query-pass-rate", "extractor-f1", "both"):
        code, _, _ = _invoke([str(suite), "--metric", metric], tmp_path=tmp_path, chat_response="1.0")
        assert code == 0

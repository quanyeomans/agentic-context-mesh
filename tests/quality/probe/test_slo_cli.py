"""Unit tests for the ``kairix slo`` CLI dispatch + rendering (PLA-256).

Drives ``main`` in-process: the default (synthetic) path with no deps, and
the synthetic/real dispatch + argument validation through the
``SloCLIDeps`` injection seam (F6/F1-clean — no monkeypatch). No
wall-clock-ceiling assertions (F82).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from kairix.quality.probe.slo_cli import SloCLIDeps, main
from kairix.quality.probe.slo_harness import CommandCall, CommandProbe, GroundTruthFact

pytestmark = pytest.mark.unit


def _tiny_workload() -> tuple[tuple[CommandProbe, ...], list[Any]]:
    probe = CommandProbe(name="search", payloads=("q1", "q2"), run=lambda _p: CommandCall(breadcrumbs=("kb://a",)))
    gt = [GroundTruthFact(entity="client-omega", attribute="industry", value="logistics")]
    suites = [("injected", gt, lambda _q: [])]
    return (probe,), suites


def test_slo_default_synthetic_json_emits_all_sections(capsys: pytest.CaptureFixture[str]) -> None:
    """`kairix slo --format json` (no deps) runs the synthetic harness."""
    rc = main(["--format", "json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "synthetic"
    assert payload["latency"]
    assert payload["recall"][0]["recall_at_k"] == 1.0
    assert payload["affordance"]


def test_slo_default_table_names_sections(capsys: pytest.CaptureFixture[str]) -> None:
    """The default table output names every SLO section."""
    rc = main([])
    assert rc == 0
    out = capsys.readouterr().out
    assert "mode: synthetic" in out
    assert "Latency (ms)" in out
    assert "Fact-recall quality" in out
    assert "Affordance completeness" in out


def test_slo_invalid_concurrency_exits_one(capsys: pytest.CaptureFixture[str]) -> None:
    rc = main(["--concurrency", "0"])
    assert rc == 1
    assert "must be >= 1" in capsys.readouterr().err


def test_slo_invalid_k_exits_one(capsys: pytest.CaptureFixture[str]) -> None:
    rc = main(["--k", "0"])
    assert rc == 1
    assert "must be >= 1" in capsys.readouterr().err


def test_slo_accepts_concurrency_and_k_of_one(capsys: pytest.CaptureFixture[str]) -> None:
    """1 is the valid lower bound for --concurrency and --k (pins ``< 1``)."""
    deps = SloCLIDeps(synthetic_workload=_tiny_workload)
    rc = main(["--concurrency", "1", "--k", "1", "--format", "json"], deps=deps)
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["concurrency_n"] == 1
    assert payload["recall_k"] == 1


def test_slo_synthetic_uses_injected_workload(capsys: pytest.CaptureFixture[str]) -> None:
    """The synthetic_workload seam is honoured when injected."""
    deps = SloCLIDeps(synthetic_workload=_tiny_workload)
    rc = main(["--format", "json"], deps=deps)
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    commands = {row["command"] for row in payload["latency"]}
    assert commands == {"search"}
    assert payload["recall"][0]["suite"] == "injected"


def test_slo_real_mode_passes_suite_dir_to_real_workload(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    """`--mode real --suite-dir` routes through the real_workload seam."""
    captured: dict[str, Any] = {}

    def fake_real(*, suite_dir: Path | None) -> tuple[tuple[CommandProbe, ...], list[Any]]:
        captured["suite_dir"] = suite_dir
        return _tiny_workload()

    deps = SloCLIDeps(real_workload=fake_real)
    suite_dir = tmp_path / "team-alpha"
    rc = main(["--mode", "real", "--suite-dir", str(suite_dir), "--format", "json"], deps=deps)

    assert rc == 0
    assert captured["suite_dir"] == suite_dir
    assert json.loads(capsys.readouterr().out)["mode"] == "real"

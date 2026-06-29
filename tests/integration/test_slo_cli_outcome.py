"""F30 outcome tests — ``kairix slo`` subprocess surface (PLA-256).

Drives the production binary surface in its default (synthetic) mode and
asserts on captured stdout — never on returncode alone, never on internal
call counts. Synthetic mode is deterministic and offline, so the
subprocess needs no configured index, no env vars, and no network.
"""

from __future__ import annotations

import json
import subprocess
import sys

import pytest

pytestmark = pytest.mark.integration


def test_slo_json_emits_all_three_slo_dimensions() -> None:
    """``kairix slo --format json`` emits latency + recall + affordance.

    Inlines the subprocess.run call so the F30 outcome-scanner sees the
    ``"slo"`` literal alongside the invocation.
    """
    proc = subprocess.run(
        [sys.executable, "-m", "kairix.cli", "slo", "--format", "json"],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert proc.returncode == 0, f"unexpected exit {proc.returncode}; stderr={proc.stderr[:400]!r}"
    envelope = json.loads(proc.stdout)
    assert envelope["mode"] == "synthetic"

    commands = {row["command"] for row in envelope["latency"]}
    assert commands == {"brief", "remember", "recall", "search"}
    phases = {row["phase"] for row in envelope["latency"]}
    assert phases == {"cold", "warm"}

    assert envelope["recall"][0]["recall_at_k"] == 1.0
    assert envelope["recall"][0]["n_facts"] > 0
    assert all(row["pct_resolvable"] == 100.0 for row in envelope["affordance"])


def test_slo_table_prints_human_sections() -> None:
    """``kairix slo`` (default table) prints the operator-facing sections."""
    proc = subprocess.run(
        [sys.executable, "-m", "kairix.cli", "slo"],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert proc.returncode == 0, f"unexpected exit {proc.returncode}; stderr={proc.stderr[:400]!r}"
    assert "Latency (ms)" in proc.stdout
    assert "Fact-recall quality" in proc.stdout
    assert "Affordance completeness" in proc.stdout


def test_slo_rejects_bad_concurrency_with_actionable_error() -> None:
    """An invalid --concurrency exits non-zero with an actionable message."""
    proc = subprocess.run(
        [sys.executable, "-m", "kairix.cli", "slo", "--concurrency", "0"],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert proc.returncode == 1
    assert "must be >= 1" in proc.stderr

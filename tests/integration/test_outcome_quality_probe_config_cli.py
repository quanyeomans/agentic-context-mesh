"""F30 outcome test — ``kairix probe-config`` subprocess surface.

Group F (quality CLIs) paydown. The unit suite in
``tests/quality/probe/test_config_cli.py`` exercises the in-process
function surface (with FakeProviderRegistry, _StubSnapshotter, and
injected perf operations). This test adds the F30-required subprocess
outcome assertion: real ``python -m kairix.cli probe-config`` invocation
against the production CLI dispatch, with stdout/stderr/JSON envelope
assertions.

Strategy: drive the ``--perf`` dispatch path. It bypasses provider
resolution entirely (per the docstring in ``main``: "no provider
resolution / transport snapshot is required") and runs the per-
capability budget sweep. With the default operations dict every op
skips with a "capability not yet wired" diagnostic — sub-second to
run and deterministic, so the F30 outcome test is hermetic and fast.

F2-clean: no ``KAIRIX_*`` env vars set in the subprocess invocation.
The ``--perf-budgets`` flag points at a tmp_path budgets JSON so the
test never depends on the in-repo ``suites/perf/budgets.json``.

Sabotage-proofs (both executed locally — see commit message):
  - happy path: mutate ``_emit_perf_report`` to ``return`` early.
    Stdout becomes empty → ``json.loads`` raises → test fails on the
    parse step before the envelope assertion. Restored.
  - error path: mutate the ``if iterations < 1`` guard to
    ``if iterations < 0``. ``--perf 0`` then runs zero iterations and
    exits 0 → ``rc == 2`` assertion fails. Restored.

Latency baseline (2024 M-series Mac): subprocess wall ~700ms for the
happy path (cold Python startup dominates; the perf sweep itself is
sub-50ms with every op skipped). Threshold: 10000ms (~14x headroom
for CI variance + slower hardware).
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration


# Mirror suites/perf/budgets.json so the test stays hermetic and never
# depends on the in-repo file. Values match the production budgets;
# every op skips by default so they're informational only.
_BUDGETS_PAYLOAD: dict[str, dict[str, float]] = {
    "kairix_prep_vault_only": {"p50_ms": 1500, "p99_ms": 5000},
    "kairix_prep_facts_federated": {"p50_ms": 2000, "p99_ms": 6000},
    "kairix_ingest_chat_per_turn": {"p50_ms": 1500, "p99_ms": 3000},
    "kairix_ingest_chat_100_turn": {"p50_ms": 90000, "p99_ms": 180000},
    "fact_find_conflicts": {"p50_ms": 10, "p99_ms": 50},
    "federated_search_top_k_15": {"p50_ms": 250, "p99_ms": 800},
}


def _write_budgets(tmp_path: Path) -> Path:
    """Serialise the perf budgets payload to a tmp file and return its path."""
    target = tmp_path / "budgets.json"
    target.write_text(json.dumps(_BUDGETS_PAYLOAD), encoding="utf-8")
    return target


def test_probe_config_cli_subprocess_perf_envelope_outcome(tmp_path: Path) -> None:
    """Drive the real ``kairix probe-config --perf --json`` binary surface.

    Asserts on the JSON envelope content (iterations, results, any_violation
    flag) the operator consumes — NOT on returncode alone, NOT on internal
    fake call-counts. The F30 contract: subprocess + stdout/stderr/envelope.
    """
    budgets_path = _write_budgets(tmp_path)

    t0 = time.monotonic()
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "kairix.cli",
            "probe-config",
            "--perf",
            "2",
            "--perf-budgets",
            str(budgets_path),
            "--json",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    elapsed_ms = (time.monotonic() - t0) * 1000.0

    assert proc.returncode == 0, (
        f"probe-config --perf exited {proc.returncode}\n--- stderr ---\n{proc.stderr}\n--- stdout ---\n{proc.stdout}"
    )

    envelope = json.loads(proc.stdout)
    assert envelope["iterations"] == 2, f"iterations field: {envelope.get('iterations')!r}"
    assert isinstance(envelope["results"], list), f"results not a list: {envelope!r}"
    assert envelope["results"], f"results list empty: {envelope!r}"
    # Every default op skips → any_violation is False (no op ran, so no breach).
    assert envelope["any_violation"] is False, f"any_violation: {envelope.get('any_violation')!r}"

    # Every results entry surfaces the operation name + skip diagnostic the
    # operator reads when deciding whether a capability is wired.
    op_names = {r["operation"] for r in envelope["results"]}
    assert "fact_find_conflicts" in op_names, f"missing fact_find_conflicts in {op_names}"
    assert "kairix_prep_vault_only" in op_names, f"missing kairix_prep_vault_only in {op_names}"

    assert elapsed_ms < 10000.0, f"probe-config subprocess took {elapsed_ms:.1f}ms (threshold 10000ms)"


def test_probe_config_cli_subprocess_rejects_zero_iterations(tmp_path: Path) -> None:
    """``--perf 0`` exits 2 + actionable stderr.

    Closes the binary-surface error path the unit tests cover only
    in-process. The error envelope on stderr is what the operator sees
    when they pass an out-of-range flag; F21 requires the ``fix:``
    affordance marker.
    """
    budgets_path = _write_budgets(tmp_path)

    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "kairix.cli",
            "probe-config",
            "--perf",
            "0",
            "--perf-budgets",
            str(budgets_path),
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert proc.returncode == 2, f"expected exit 2, got {proc.returncode}. stderr={proc.stderr!r}"
    assert "perf iterations must be >= 1" in proc.stderr, f"stderr missing range diagnostic: {proc.stderr!r}"
    assert "fix:" in proc.stderr, f"stderr missing F21 fix: marker: {proc.stderr!r}"

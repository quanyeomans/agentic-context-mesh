"""F30 outcome test — ``kairix soak`` subprocess surface.

Group F (quality CLIs) Wave 0 paydown. The unit suite in
``tests/quality/soak/test_cli.py`` covers the in-process function
surface (exit codes, text/JSON output, help affordance) via
``mock.patch.object(soak_cli, "run_soak", ...)`` on the CLI's own
binding. This test adds the F30-required subprocess outcome assertion:
real ``python -m kairix.cli soak run`` invocation against the
production CLI dispatch.

Strategy: drive the new ``--dry-run`` flag introduced in this commit.
``--dry-run`` is the F30 subprocess seam — it threads a hermetic
``workload_runner`` (the existing test seam on :func:`run_soak`)
through the CLI into the runner, so the full iteration loop +
memory/log/fd/signature gate checks all execute end-to-end without a
real benchmark / retrieval / provider dependency.

F2-clean: no ``KAIRIX_*`` env vars set in the subprocess invocation.

Sabotage-proofs (both executed locally — see commit message):
  - happy path: mutate ``_dry_run_workload`` to return a different
    payload each call (e.g. include the call-count). The
    ``_check_signature_drift`` gate fires (signatures differ across
    iterations), the ``passed`` field flips to False, rc becomes 1
    → both rc==0 and ``"passed": true`` assertions fail. Restored.
  - error path: mutate ``if args.repeat < 2`` to ``if args.repeat <
    0``. ``--repeat 1`` then enters the runner and the runner's own
    guard returns a failed result instead of rc 2 → rc==2 assertion
    fails. Restored.

Latency baseline (2024 M-series Mac): subprocess wall ~250ms for the
happy path (cold Python startup dominates; two iterations of the
dry-run workload total sub-10ms). Threshold: 10000ms (~40x headroom).
"""

from __future__ import annotations

import json
import subprocess
import sys
import time

import pytest

pytestmark = pytest.mark.integration


def test_soak_cli_subprocess_dry_run_envelope_outcome() -> None:
    """Drive ``kairix soak run --dry-run --json`` end-to-end.

    Asserts on the JSON envelope content the operator consumes —
    NOT on returncode alone, NOT on internal fake call-counts. The
    F30 contract: subprocess + stdout/stderr/envelope.
    """
    t0 = time.monotonic()
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "kairix.cli",
            "soak",
            "run",
            "--suite",
            "synthetic",  # dry-run workload ignores the suite name
            "--repeat",
            "2",
            "--dry-run",
            "--json",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    elapsed_ms = (time.monotonic() - t0) * 1000.0

    assert proc.returncode == 0, (
        f"soak run --dry-run exited {proc.returncode}\n--- stderr ---\n{proc.stderr}\n--- stdout ---\n{proc.stdout}"
    )

    envelope = json.loads(proc.stdout)
    # The JSON envelope is what the operator harness consumes; pin the
    # canonical fields SoakResult.to_envelope() emits.
    assert envelope["suite"] == "synthetic", f"suite field: {envelope.get('suite')!r}"
    assert envelope["repeat"] == 2, f"repeat field: {envelope.get('repeat')!r}"
    assert envelope["passed"] is True, f"passed field: {envelope.get('passed')!r}"
    assert envelope["failures"] == [], f"failures should be empty: {envelope.get('failures')!r}"
    # The repeat=N argument MUST produce N rows of per-iteration timing
    # data — that's the soak primitive.
    iterations = envelope["iterations"]
    assert len(iterations) == 2, f"expected 2 iteration rows, got {len(iterations)}: {iterations!r}"
    # Per-iteration entries surface index + signature so the operator
    # can spot determinism drift; pin both.
    assert iterations[0]["index"] == 0, f"iter[0].index: {iterations[0]!r}"
    assert iterations[1]["index"] == 1, f"iter[1].index: {iterations[1]!r}"
    # Signatures must agree across iterations because the dry-run
    # workload is byte-stable — that's the _check_signature_drift PASS.
    assert iterations[0]["signature"] == iterations[1]["signature"], (
        f"signatures differ across iterations: {iterations!r}"
    )
    # The deprecation warning fires on every invocation per the CLI's
    # P5 unification contract.
    assert "DEPRECATION" in proc.stderr, f"deprecation banner missing: {proc.stderr!r}"

    assert elapsed_ms < 10000.0, f"soak run subprocess took {elapsed_ms:.1f}ms (threshold 10000ms)"


def test_soak_cli_subprocess_rejects_repeat_below_two() -> None:
    """``--repeat 1`` exits 2 + actionable stderr.

    Closes the binary-surface error path the unit tests cover only
    in-process. F21 requires the ``fix:`` affordance marker on every
    actionable error.
    """
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "kairix.cli",
            "soak",
            "run",
            "--suite",
            "synthetic",
            "--repeat",
            "1",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert proc.returncode == 2, f"expected exit 2, got {proc.returncode}. stderr={proc.stderr!r}"
    assert "--repeat must be >= 2" in proc.stderr, f"stderr missing range diagnostic: {proc.stderr!r}"
    assert "fix:" in proc.stderr, f"stderr missing F21 fix: marker: {proc.stderr!r}"

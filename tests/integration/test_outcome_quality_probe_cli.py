"""F30 outcome test — ``kairix probe`` subprocess surface.

Group F (quality CLIs) Wave 0 paydown. The unit suite in
``tests/quality/probe/test_cli.py`` exercises the in-process function
surface (cmd dispatch, JSON / text output, gate verdicts) with
``mock.patch.object(probe_cli, "run_probe_search", ...)`` at the CLI
module binding (allowed: monkey-patching the CLI's own seam, not
internal kairix code). This test adds the F30-required subprocess
outcome assertion: real ``python -m kairix.cli probe search`` invocation
against the production CLI dispatch.

Strategy: drive the new ``--dry-run`` flag introduced in this commit.
``--dry-run`` is the F30 subprocess seam (canonical pattern matching
``--document-root`` on bootstrap_cli) — it runs the full
:func:`run_probe_search` pipeline (sampler + executor + stats + gate)
with a hermetic in-CLI suite loader and zero-latency searcher. No
retrieval, no provider wiring, no env reads.

F2-clean: no ``KAIRIX_*`` env vars set in the subprocess invocation.
The probe runs against four synthetic in-memory BenchmarkCase rows
spread across the canonical category weights — sub-second and
deterministic.

Sabotage-proofs (both executed locally — see commit message):
  - happy path: mutate ``_dry_run_searcher`` to ``raise
    RuntimeError`` instead of returning the stub. The executor catches
    the exception and the resulting envelope reports ``errors > 0``;
    the ``passed`` field flips to False; the ``"passed": true``
    assertion fails. Restored.
  - error path: mutate the ``if args.queries < 1`` guard in
    ``_run_search`` to ``if args.queries < 0``. ``--queries 0`` then
    enters the runner and either raises ValueError inside the
    sampler or returns an empty result → rc would not be 2 and the
    actionable stderr would be missing. Restored.

Latency baseline (2024 M-series Mac): subprocess wall ~600ms for the
happy path (cold Python startup dominates; the dry-run sweep itself
is sub-50ms). Threshold: 10000ms (~16x headroom).
"""

from __future__ import annotations

import json
import subprocess
import sys
import time

import pytest

pytestmark = pytest.mark.integration


def test_probe_cli_subprocess_search_dry_run_envelope_outcome() -> None:
    """Drive ``kairix probe search --dry-run --json`` end-to-end.

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
            "probe",
            "search",
            "--suite",
            "synthetic",  # dry-run loader ignores the suite name
            "--queries",
            "4",
            "--concurrency",
            "1",
            "--dry-run",
            "--json",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    elapsed_ms = (time.monotonic() - t0) * 1000.0

    assert proc.returncode == 0, (
        f"probe search --dry-run exited {proc.returncode}\n--- stderr ---\n{proc.stderr}\n--- stdout ---\n{proc.stdout}"
    )

    envelope = json.loads(proc.stdout)
    # The JSON envelope is what the agent harness consumes; pin the
    # canonical fields that ProbeResult.to_envelope() emits.
    assert envelope["suite"] == "synthetic", f"suite field: {envelope.get('suite')!r}"
    assert envelope["queries"] == 4, f"queries field: {envelope.get('queries')!r}"
    assert envelope["concurrency"] == 1, f"concurrency field: {envelope.get('concurrency')!r}"
    assert envelope["passed"] is True, f"passed field: {envelope.get('passed')!r}"
    # The dry-run searcher returns sub-millisecond latencies so the
    # default p95 threshold (500ms) is comfortably met.
    assert envelope["overall"]["n"] == 4, f"overall.n: {envelope['overall']!r}"
    # The deprecation warning fires on every invocation per the CLI's
    # P5 unification contract; verify it surfaces on stderr so operators
    # see the migration affordance.
    assert "DEPRECATION" in proc.stderr, f"deprecation banner missing: {proc.stderr!r}"

    assert elapsed_ms < 10000.0, f"probe search subprocess took {elapsed_ms:.1f}ms (threshold 10000ms)"


def test_probe_cli_subprocess_rejects_zero_queries() -> None:
    """``--queries 0`` exits 2 + actionable stderr.

    Closes the binary-surface error path the unit tests cover only
    in-process. F21 requires the ``fix:`` affordance marker on every
    actionable error.
    """
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "kairix.cli",
            "probe",
            "search",
            "--suite",
            "synthetic",
            "--queries",
            "0",
            "--concurrency",
            "1",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert proc.returncode == 2, f"expected exit 2, got {proc.returncode}. stderr={proc.stderr!r}"
    assert "--queries must be >= 1" in proc.stderr, f"stderr missing range diagnostic: {proc.stderr!r}"
    assert "fix:" in proc.stderr, f"stderr missing F21 fix: marker: {proc.stderr!r}"

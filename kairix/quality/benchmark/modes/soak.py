"""Soak mode — repeated benchmark workloads, stability instrumentation. STUB for P3.a.

Per ``/tmp/spike-C2-mode-integration.md`` §3.3, the soak dispatcher
wraps ``kairix.quality.soak.run_soak`` with a custom
``workload_runner`` closure:

  1. Build a ``workload_runner: Callable[[str], dict[str, Any]]``
     closure that iterates ``request.suite.cases``, calls
     ``request.query_executor`` per case, and returns a deterministic
     envelope ``{"per_case_scores": [...], "per_case_paths": [...]}``
     (wall-clock keys stripped so the signature stability check
     remains tight).
  2. Pass to ``kairix.quality.soak.run_soak(suite=name, repeat=…,
     workload_runner=closure, max_memory_growth_mb=…,
     max_log_volume_mb=…, max_time_drift_pct=…)``.
  3. Translate the resulting ``SoakResult`` into a ``ModeRunResult``:
     ``mode_metrics`` carries ``mem_growth_mb_max``,
     ``duration_drift_pct_max``, ``stderr_bytes_total``;
     ``errors`` carries ``[f"[{f.kind}] {f.detail}" …]`` plus the
     top-level ``result.error`` when non-empty;
     ``per_query_runs`` is the LAST iteration's per-case detail
     (canonical when ``passed=True`` because the signature check
     proved every iteration produced the same envelope);
     ``raw`` carries ``result.to_envelope()``.

Soak is the largest mode (workload-closure design, benchmark-gate
layering on top of stability gates — see C2 §"Gap 2"). The stub here
keeps the import surface stable; the body lands in the P3.c slice
behind single-shot (P3.a) and concurrent (P3.b).
"""

from __future__ import annotations

from kairix.quality.benchmark.modes.types import ModeRunRequest, ModeRunResult


def run_soak(request: ModeRunRequest) -> ModeRunResult:
    """Wrap kairix.quality.soak.run_soak with a per-case workload closure — NOT YET IMPLEMENTED.

    See module docstring for the planned composition. Raises
    :class:`NotImplementedError` until the P3.c slice lands the body.
    """
    _ = request  # explicit drop documents intent — body wires this in P3.c
    raise NotImplementedError(
        "soak mode is deferred to the P3.c slice. "
        "next: pass a workload_runner closure that iterates "
        "request.suite.cases through request.query_executor to "
        "kairix.quality.soak.run_soak per C2 §3.3. "
        "fix: pin --mode single-shot until P3.c lands."
    )

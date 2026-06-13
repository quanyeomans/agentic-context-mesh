"""F29: performance-measurement code may only live under ``kairix/quality/probe/``.

Every layer's latency / throughput instrumentation centralises in one home so
the PVT release gate and the end-user ``kairix probe-config`` health check
share one implementation. F29 flags any ``.py`` file whose name matches a
benchmark / perf / latency naming pattern and lives outside the allowed roots
(``kairix/quality/probe/``, ``tests/``, ``scripts/probe*``).

Thin shim over :mod:`_location_engine` (#499 Phase 2). The rule is one
``LocationRule`` row in ``filename-regex`` kind; this module re-exports the
back-compat surface (``collect_violations`` / ``main`` / ``REMEDIATION`` /
``_is_perf_named``) the F29 unit test loads by file path.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _location_engine import LocationRule, collect_violations_for, register
from tc_fitness import REPO_ROOT, gate

# Regex that matches a perf-measurement-shaped filename. Anchored at both ends
# — we only flag files whose entire basename matches.
_PERF_NAME_RE = re.compile(
    r"""
    ^(
        bench[a-z0-9_]*           # bench.py, benchmarks.py, bench_provider.py
        | microbench[a-z0-9_]*    # microbench.py, microbench_foo.py
        | [a-z0-9_]+_bench        # http_bench.py, transport_bench.py
        | [a-z0-9_]+_microbench   # http_microbench.py
        | [a-z0-9_]+_latency      # embed_latency.py
        | [a-z0-9_]+_latency_[a-z0-9_]+
        | [a-z0-9_]+_perf         # http_perf.py
        | [a-z0-9_]+_perf_[a-z0-9_]+
    )\.py$
    """,
    re.VERBOSE,
)

REMEDIATION = """Refactor to move the performance-measurement code into
kairix/quality/probe/ — that's the single perf surface for the whole
project, exposed through the probe CLI and the kairix probe-config
end-user command.

fix: relocate the bench/microbench/latency script under
kairix/quality/probe/<subarea>/, expose its entry point through the
probe CLI (kairix probe ... or a new subcommand), and consume the
timings hook from kairix/transport/telemetry/ (or the per-layer
equivalent) rather than reinventing a measurement harness. If the
measurement is a test assertion (e.g. "p99 < 200ms under fake clock"),
move it under tests/ — F29's allow-list covers that.
next: re-run python3 scripts/checks/check_perf_singleton.py to
confirm the gate goes green.
run: bash scripts/safe-commit.sh "refactor(probe): consolidate <metric> measurement"

Pass example:
  kairix/quality/probe/embed_latency.py       # canonical home — allowed
  tests/integration/test_embed_perf_floor.py  # latency assertion in a test — allowed
  scripts/probe-config-runner.py              # operational driver — allowed

Forbidden example:
  kairix/transport/pool/bench_pool.py         # F29 — perf code in transport
  kairix/providers/openai/openai_perf.py      # F29 — perf code in a plugin
  kairix/core/search/bm25_latency.py          # F29 — perf code in domain

Why: see docs/architecture/provider-plugin-architecture.md -
"Performance". The probe is the single perf surface so the PVT and
end-user health-check share one implementation and the report schema
stays stable. Parallel benchmark harnesses scattered across
transport/ and providers/ create the per-provider conditional jungle
the ADR exists to remove."""

RULE = register(
    LocationRule(
        name="f29",
        kind="filename-regex",
        pattern=_PERF_NAME_RE,
        allowed_roots=("kairix/quality/probe", "tests"),
        probe_scripts=True,
        remediation=REMEDIATION,
    )
)


def _is_perf_named(name: str) -> bool:
    """True if ``name`` is a basename matching a perf-measurement naming
    pattern (bench/microbench/latency/perf). Re-exported for the F29 test."""
    return _PERF_NAME_RE.match(name) is not None


def collect_violations(repo_root: Path = REPO_ROOT) -> set[Path]:
    """Back-compat surface for the F29 unit test."""
    return collect_violations_for(RULE, repo_root)


def main() -> int:
    return gate(RULE.name, collect_violations(), REMEDIATION)


if __name__ == "__main__":
    sys.exit(main())

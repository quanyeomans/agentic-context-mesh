"""CLI — ``kairix slo``: perf & affordance SLO harness (PLA-256).

ONE command produces the cold/warm/concurrency latency table + fact-recall
quality + breadcrumb-completeness numbers across the four most-used agent
commands (``brief`` / ``remember`` / ``recall`` / ``search``).

Modes:

  --mode synthetic  (default) deterministic, offline — seeds the #340
                    fact-pattern set into an in-process corpus. Runs in CI
                    and on a fresh install with no configured index.
  --mode real       measures the operator's configured kairix (real fact
                    store + SearchPipeline). ``--suite-dir`` points the
                    recall metric at an ingested reference suite.

Usage:
  kairix slo [--mode synthetic|real] [--concurrency N] [--k K]
             [--format table|json] [--suite-dir PATH]

Exits 0 on success, 1 on invalid arguments.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from kairix.quality.probe.slo_harness import (
    DEFAULT_CONCURRENCY,
    DEFAULT_RECALL_K,
    build_report,
)

__all__ = ["SloCLIDeps", "main", "parse_args"]

_MODE_SYNTHETIC = "synthetic"
_MODE_REAL = "real"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse the ``kairix slo`` argument vector."""
    parser = argparse.ArgumentParser(
        prog="kairix slo",
        description="Perf & affordance SLO harness across the most-used agent commands.",
    )
    parser.add_argument(
        "--mode",
        default=_MODE_SYNTHETIC,
        choices=[_MODE_SYNTHETIC, _MODE_REAL],
        help="synthetic (default, offline, deterministic) or real (configured index).",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=DEFAULT_CONCURRENCY,
        help=f"high-concurrency level N for the warm-cN arm (default {DEFAULT_CONCURRENCY}).",
    )
    parser.add_argument(
        "--k",
        type=int,
        default=DEFAULT_RECALL_K,
        help=f"cut-off for recall@k / NDCG@k on the fact suite (default {DEFAULT_RECALL_K}).",
    )
    parser.add_argument(
        "--format",
        default="table",
        choices=["table", "json"],
        help="output format (default table).",
    )
    parser.add_argument(
        "--suite-dir",
        default=None,
        help="real mode: directory with a ground-truth-facts.json for the recall metric.",
    )
    return parser.parse_args(argv)


def default_synthetic_workload() -> Any:
    """Production default — lazy import keeps the CLI module load cheap."""
    from kairix.quality.probe.slo_probes import build_synthetic_workload

    return build_synthetic_workload()


def default_real_workload(*, suite_dir: Path | None) -> Any:
    """Production default — lazy import of the heavy real-measurement wiring."""
    from kairix.quality.probe.slo_probes import default_real_workload as _impl

    return _impl(suite_dir=suite_dir)


@dataclass(frozen=True)
class SloCLIDeps:
    """Injectable seams for :func:`main` (F6-clean DI dataclass).

    Production callers leave ``deps=None``; tests construct ``SloCLIDeps``
    with fakes returning ``(probes, recall_suites)`` so the CLI dispatch +
    rendering are exercised without standing up the synthetic corpus or a
    real index.
    """

    synthetic_workload: Callable[[], Any] = field(default_factory=lambda: default_synthetic_workload)
    real_workload: Callable[..., Any] = field(default_factory=lambda: default_real_workload)


def _emit_invalid_args(detail: str) -> int:
    """Print the F21-shaped affordance for a bad numeric flag; return 1."""
    print(
        f"❌ {detail}\n"
        "   fix: pass --concurrency and --k as integers >= 1.\n"
        "   next: re-run with valid values.\n"
        "   run: kairix slo --concurrency 5 --k 5",
        file=sys.stderr,
    )
    return 1


def main(argv: list[str] | None = None, *, deps: SloCLIDeps | None = None) -> int:
    """Run the SLO harness once and print the report.

    ``deps`` is the DI seam — production leaves it ``None`` and the default
    synthetic/real workload builders apply; tests inject fakes.
    """
    args = parse_args(argv)
    if args.concurrency < 1 or args.k < 1:
        return _emit_invalid_args("--concurrency and --k must be >= 1")

    d = deps if deps is not None else SloCLIDeps()
    if args.mode == _MODE_REAL:
        suite_dir = Path(args.suite_dir) if args.suite_dir else None
        probes, recall_suites = d.real_workload(suite_dir=suite_dir)
    else:
        probes, recall_suites = d.synthetic_workload()

    report = build_report(
        probes=probes,
        recall_suites=recall_suites,
        concurrency_n=args.concurrency,
        recall_k=args.k,
    )

    if args.format == "json":
        payload = report.to_dict()
        payload["mode"] = args.mode
        print(json.dumps(payload, indent=2))
    else:
        print(f"mode: {args.mode}")
        print(report.render_table())
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""
CLI for kairix eval — automated evaluation suite generation and monitoring.

Subcommands:
  generate   Generate a new benchmark suite using the GPL pipeline
  enrich     Enrich an existing suite with graded gold_titles
  monitor    Run canary suite and check for regression
  report     Generate markdown report from monitor log

Usage:
  kairix eval generate --output suites/generated.yaml --count 100
  kairix eval enrich --suite suites/v2-real-world.yaml --output suites/v2-enriched.yaml
  kairix eval monitor --suite suites/canary.yaml
  kairix eval report --days 30
"""

from __future__ import annotations

import argparse
import sys


def _cmd_generate(args: argparse.Namespace) -> int:
    from kairix.eval.generate import generate_suite

    print(f"Generating {args.count} benchmark cases → {args.output}")
    if not args.no_calibrate:
        print("Running calibration anchors...")

    result = generate_suite(
        db_path=args.db,
        output_path=args.output,
        n_cases=args.count,
        categories=args.categories.split(",") if args.categories else None,
        deployment=args.deployment,
        calibrate_first=not args.no_calibrate,
        seed=args.seed,
        agent=args.agent,
    )

    if not result.calibration_passed and not args.no_calibrate:
        print("ERROR: Calibration failed. Use --no-calibrate to skip.", file=sys.stderr)
        for e in result.errors:
            print(f"  {e}", file=sys.stderr)
        return 1

    print("\nResults:")
    print(f"  Accepted: {result.n_accepted}")
    print(f"  Rejected (no grade-2 doc): {result.n_rejected}")
    print(f"  Failed (retrieval/API error): {result.n_failed}")
    print("\nCategory distribution:")
    for cat, count in sorted(result.category_counts.items()):
        print(f"  {cat}: {count}")

    if result.errors:
        print("\nWarnings:")
        for e in result.errors:
            print(f"  {e}")

    print(f"\nOutput: {result.output_path}")
    return 0


def _cmd_enrich(args: argparse.Namespace) -> int:
    from kairix.eval.generate import enrich_suite

    print(f"Enriching {args.suite} → {args.output}")
    print("Running hybrid search + LLM judge for each case...")

    result = enrich_suite(
        suite_path=args.suite,
        output_path=args.output,
        db_path=args.db,
        deployment=args.deployment,
        agent=args.agent,
    )

    print("\nResults:")
    print(f"  Total cases: {result.n_cases}")
    print(f"  Enriched with gold_titles: {result.n_enriched}")
    print(f"  Skipped (no relevant doc found): {result.n_skipped}")
    print(f"  Failed (retrieval error): {result.n_failed}")

    if result.errors:
        print("\nWarnings:")
        for e in result.errors:
            print(f"  {e}")

    print(f"\nOutput: {result.output_path}")
    return 0


def _cmd_monitor(args: argparse.Namespace) -> int:
    from kairix.eval.monitor import run_monitor

    print(f"Running canary monitor on {args.suite}...")

    result = run_monitor(
        suite_path=args.suite,
        log_path=args.log,
        alert_threshold=args.alert_threshold,
        window_days=args.window_days,
        agent=args.agent,
    )

    print(f"\nMonitor result ({result.ts[:19]}):")
    print(f"  Cases run: {result.n_cases}")
    print(f"  Weighted NDCG: {result.weighted_ndcg:.4f}")
    print(f"  Vec failed: {result.vec_failed_count}")
    print("\nCategory NDCG:")
    for cat, score in sorted(result.ndcg_by_category.items()):
        print(f"  {cat}: {score:.4f}")

    if result.regression:
        print(f"\n⚠️  REGRESSION DETECTED: {result.regression_detail}", file=sys.stderr)
        if args.log:
            print(f"  Log: {args.log}")
        return 2  # distinct exit code for regression (vs hard failure)

    print("\n✓ No regression detected.")
    return 0


def _cmd_report(args: argparse.Namespace) -> int:
    from kairix.eval.monitor import generate_report

    report = generate_report(log_path=args.log, days=args.days)

    if args.output:
        from pathlib import Path
        Path(args.output).write_text(report, encoding="utf-8")
        print(f"Report written to {args.output}")
    else:
        print(report)

    return 0


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="kairix eval",
        description="Automated evaluation suite generation and monitoring",
    )
    subparsers = parser.add_subparsers(dest="subcommand", required=True)

    # --- generate ---
    p_gen = subparsers.add_parser("generate", help="Generate a new benchmark suite using the GPL pipeline")
    p_gen.add_argument("--output", required=True, help="Output suite YAML path")
    p_gen.add_argument("--count", type=int, default=100, help="Target case count (default: 100)")
    p_gen.add_argument("--categories", help="Comma-separated categories (default: all)")
    p_gen.add_argument("--db", default=str(__import__("pathlib").Path.home() / ".cache/qmd/index.sqlite"),
                       help="QMD SQLite path (default: ~/.cache/qmd/index.sqlite)")
    p_gen.add_argument("--deployment", default="gpt-4o-mini", help="Azure deployment (default: gpt-4o-mini)")
    p_gen.add_argument("--no-calibrate", action="store_true", help="Skip calibration anchor check")
    p_gen.add_argument("--seed", type=int, default=None, help="Random seed for reproducibility")
    p_gen.add_argument("--agent", default="shape", help="Agent for retrieval scoping (default: shape)")

    # --- enrich ---
    p_enr = subparsers.add_parser("enrich", help="Enrich an existing suite with graded gold_titles")
    p_enr.add_argument("--suite", required=True, help="Input suite YAML path")
    p_enr.add_argument("--output", required=True, help="Output suite YAML path")
    p_enr.add_argument("--db", default=str(__import__("pathlib").Path.home() / ".cache/qmd/index.sqlite"),
                       help="QMD SQLite path")
    p_enr.add_argument("--deployment", default="gpt-4o-mini", help="Azure deployment (default: gpt-4o-mini)")
    p_enr.add_argument("--agent", default="shape", help="Agent for retrieval scoping (default: shape)")

    # --- monitor ---
    p_mon = subparsers.add_parser("monitor", help="Run canary suite and check for regression")
    p_mon.add_argument("--suite", required=True, help="Canary suite YAML path")
    p_mon.add_argument("--log", default=None,
                       help="Monitor log path (default: KAIRIX_MONITOR_LOG or ~/.cache/qmd/monitor.jsonl)")
    p_mon.add_argument("--alert-threshold", type=float, default=0.05,
                       help="Relative NDCG drop that triggers regression (default: 0.05)")
    p_mon.add_argument("--window-days", type=int, default=7,
                       help="Rolling window for baseline average in days (default: 7)")
    p_mon.add_argument("--agent", default="shape", help="Agent for retrieval scoping (default: shape)")

    # --- report ---
    p_rep = subparsers.add_parser("report", help="Generate markdown report from monitor log")
    p_rep.add_argument("--log", default=None,
                       help="Monitor log path (default: KAIRIX_MONITOR_LOG or ~/.cache/qmd/monitor.jsonl)")
    p_rep.add_argument("--days", type=int, default=30, help="Days of history to include (default: 30)")
    p_rep.add_argument("--output", default=None, help="Markdown output path (stdout if omitted)")

    args = parser.parse_args(argv)

    # Resolve default log path for report
    if args.subcommand in ("monitor", "report") and args.log is None:
        import os
        from pathlib import Path
        args.log = os.environ.get("KAIRIX_MONITOR_LOG", str(Path.home() / ".cache/qmd/monitor.jsonl"))

    dispatch = {
        "generate": _cmd_generate,
        "enrich": _cmd_enrich,
        "monitor": _cmd_monitor,
        "report": _cmd_report,
    }

    fn = dispatch[args.subcommand]
    sys.exit(fn(args))

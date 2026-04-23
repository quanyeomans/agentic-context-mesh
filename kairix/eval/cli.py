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


def _cmd_build_gold(args: argparse.Namespace) -> int:
    from pathlib import Path

    from kairix.eval.gold_builder import build_independent_gold

    systems = [s.strip() for s in args.systems.split(",")]
    print(f"Building independent gold suite: {args.suite} → {args.output}")
    print(f"Systems: {systems}")
    print(f"Judge runs: {args.judge_runs}")

    report = build_independent_gold(
        suite_path=Path(args.suite),
        output_path=Path(args.output),
        systems=systems,
        judge_runs=args.judge_runs,
        calibrate_first=not args.no_calibrate,
        limit_per_system=args.limit,
    )

    print("\nGold suite built:")
    print(f"  Queries: {report.queries_processed}")
    print(f"  Candidates pooled: {report.total_candidates_pooled}")
    print(f"  Avg candidates/query: {report.avg_candidates_per_query:.1f}")
    print(f"  Judge calls: {report.total_judge_calls}")
    print(f"  Grades: 2={report.grade_distribution.get(2, 0)} 1={report.grade_distribution.get(1, 0)} 0={report.grade_distribution.get(0, 0)}")
    print(f"  Output: {args.output}")
    return 0


def _cmd_hybrid_sweep(args: argparse.Namespace) -> int:
    import logging
    from pathlib import Path

    from kairix.eval.hybrid_sweep import build_default_configs, sweep_hybrid_params

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    configs = build_default_configs()
    if args.quick:
        # Quick mode: baselines + key hybrid variants + bm25_primary
        configs = [c for c in configs if c.name in (
            "bm25-only", "hybrid-k20-minimal", "hybrid-k40-minimal",
            "hybrid-k60-minimal", "hybrid-k60-defaults",
            "bm25primary-v5", "bm25primary-v10", "bm25primary-v20",
        )]

    print(f"Running hybrid calibration sweep: {len(configs)} configs x suite {args.suite}")

    report = sweep_hybrid_params(
        suite_path=Path(args.suite),
        output_path=Path(args.output) if args.output else None,
        configs=configs,
    )

    print(f"\nSweep complete: {report.total_configs} configs, {report.total_duration_s:.0f}s")
    if report.best:
        b = report.best
        c = b.config
        print(f"\n{'='*70}")
        print("BEST CONFIG:")
        print(f"  Name: {c.name}")
        print(f"  Mode: {c.mode} | RRF k={c.rrf_k}")
        print(f"  Entity: {c.entity_enabled} (factor={c.entity_factor}, cap={c.entity_cap})")
        print(f"  Procedural: {c.procedural_enabled} (factor={c.procedural_factor})")
        print(f"  BM25 limit={c.bm25_limit} | Vec limit={c.vec_limit}")
        print(f"  Weighted total: {b.weighted_total:.4f}")
        print(f"  NDCG@10: {b.ndcg_at_10:.4f}")
        print(f"  Hit@5: {b.hit_at_5:.3f}")
        print(f"  MRR@10: {b.mrr_at_10:.4f}")
        print(f"  Vec failures: {b.n_vec_failed}/{b.n_cases}")
        print(f"  Avg latency: {b.avg_latency_ms:.0f}ms")
        print(f"{'='*70}")

    # Show top 10
    print("\nTop 10 configs:")
    for i, r in enumerate(report.results[:10], 1):
        print(f"  {i:2d}. {r.config.name:30s} → weighted={r.weighted_total:.4f} "
              f"NDCG={r.ndcg_at_10:.4f} Hit@5={r.hit_at_5:.3f} "
              f"vecfail={r.n_vec_failed}")

    if args.output:
        print(f"\nFull results: {args.output}")

    return 0


def _cmd_sweep(args: argparse.Namespace) -> int:
    from pathlib import Path

    from kairix.eval.sweep import sweep_bm25_params

    print(f"Sweeping BM25 parameters against: {args.suite}")

    report = sweep_bm25_params(
        suite_path=Path(args.suite),
        output_path=Path(args.output) if args.output else None,
    )

    print(f"\nSweep complete: {report.total_configs} configs, {report.total_duration_s:.0f}s")
    if report.best:
        b = report.best
        print(f"\n{'='*60}")
        print("BEST CONFIG:")
        print(f"  Weights: filepath={b.weights[0]} title={b.weights[1]} doc={b.weights[2]}")
        print(f"  Query style: {b.query_style}")
        print(f"  Weighted total: {b.weighted_total:.4f}")
        print(f"  NDCG@10: {b.ndcg_at_10:.4f}")
        print(f"  Hit@5: {b.hit_at_5:.4f}")
        print(f"  MRR@10: {b.mrr_at_10:.4f}")
        print(f"{'='*60}")

    # Show top 5
    print("\nTop 5 configs:")
    for i, r in enumerate(report.results[:5], 1):
        print(f"  {i}. w=({r.weights[0]},{r.weights[1]},{r.weights[2]}) style={r.query_style:7s} → {r.weighted_total:.4f}")

    if args.output:
        print(f"\nFull results: {args.output}")

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

    # --- build-gold ---
    p_gold = subparsers.add_parser("build-gold", help="Build independent gold suite via TREC pooling + LLM judge")
    p_gold.add_argument("--suite", required=True, help="Input suite YAML (queries + categories)")
    p_gold.add_argument("--output", required=True, help="Output enriched suite YAML")
    p_gold.add_argument("--systems", default="bm25-equal,bm25-qmd,bm25-title,vector",
                        help="Retrieval systems to pool (default: bm25-equal,bm25-qmd,bm25-title,vector)")
    p_gold.add_argument("--judge-runs", type=int, default=2, help="Judge runs per query (default: 2)")
    p_gold.add_argument("--no-calibrate", action="store_true", help="Skip judge calibration")
    p_gold.add_argument("--limit", type=int, default=10, help="Top-k per system (default: 10)")

    # --- sweep ---
    p_sweep = subparsers.add_parser("sweep", help="Grid search BM25 column weights and query styles")
    p_sweep.add_argument("--suite", required=True, help="Benchmark suite YAML with gold_titles")
    p_sweep.add_argument("--output", default=None, help="CSV output path (stdout summary if omitted)")

    # --- hybrid-sweep ---
    p_hsweep = subparsers.add_parser("hybrid-sweep",
                                      help="Grid search over hybrid pipeline: RRF k, boosts, retrieval modes")
    p_hsweep.add_argument("--suite", required=True, help="Independent gold suite YAML")
    p_hsweep.add_argument("--output", default=None, help="CSV output path")
    p_hsweep.add_argument("--quick", action="store_true",
                          help="Quick mode: run only baseline + key RRF k variants")

    args = parser.parse_args(argv)

    # Resolve default log path for report
    if args.subcommand in ("monitor", "report") and args.log is None:
        import os
        from pathlib import Path
        args.log = os.environ.get("KAIRIX_MONITOR_LOG", str(Path.home() / ".cache/kairix/monitor.jsonl"))

    dispatch = {
        "generate": _cmd_generate,
        "enrich": _cmd_enrich,
        "monitor": _cmd_monitor,
        "report": _cmd_report,
        "build-gold": _cmd_build_gold,
        "sweep": _cmd_sweep,
        "hybrid-sweep": _cmd_hybrid_sweep,
    }

    fn = dispatch[args.subcommand]
    sys.exit(fn(args))

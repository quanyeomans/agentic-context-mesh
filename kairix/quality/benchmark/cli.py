"""
CLI entry point for `kairix benchmark` — the canonical, unified quality CLI.

Per the unified benchmark architecture, ``kairix benchmark run`` is the
canonical entry point that supersedes ``kairix eval``, ``kairix probe``,
and ``kairix soak`` (kept as deprecation-warned aliases through v2026.6.x).

Usage:
  kairix benchmark run     --suite SUITE
                           [--mode single-shot|concurrent|soak|legacy]
                           [--system hybrid|bm25] [--agent AGENT] [--scope SCOPE]
                           [--collection COL[,COL...]] [--categories CAT[,CAT...]]
                           [--metrics M[,M...]] [--gates]
                           [--output DIR] [--baseline PATH]
  kairix benchmark validate --suite SUITE
  kairix benchmark compare  RESULT_A RESULT_B
  kairix benchmark init    --agent AGENT [--collections COL,COL]
  kairix benchmark list

Flag groups on ``run``:

  Selection (which cases score)
    --collection   Restrict to one or more collections (CSV).
    --agent        Agent override for routing-boundary scoping.
    --scope        Scope override (shared|agent|shared+agent|all-agents|everything).
    --categories   Filter to one or more query categories (recall|temporal|entity|…).

  Execution (how cases run)
    --mode         single-shot | concurrent | soak | legacy (default: legacy).
                   Concurrent + soak require operator-only entry-points and
                   are stubbed pending P3.b / P3.c.

  Scoring (which scorers apply)
    --metrics      Opt-in scorers (CSV). Default: auto-select per suite shape
                   via ``kairix.quality.scoring.auto_select_scorers``.
    --gates        Exit non-zero when any declared gate fails.

  Output
    --output       Directory for JSON report.
    --baseline     Path to a previous result JSON for compare-with-previous.

Suite YAML schema (suites/<name>.yaml):
  meta:
    name: <suite-name>
    description: <one-liner>
    default_collection: <collection-name>  # auto-scoping target for `run` when
                                           # --collection is not explicitly passed.
                                           # Resolves #222: the bundled reflib
                                           # suite ships default_collection=
                                           # reference-library because that
                                           # collection has in_default: false in
                                           # the stock config.
  cases: [ ... ]

Exits 0 on success, 1 on error, 2 on gate failure when --gates is passed.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from kairix.core.db import get_db_path, open_db


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="kairix benchmark",
        description="Retrieval quality benchmark for kairix.",
    )
    sub = parser.add_subparsers(dest="subcommand", required=True)

    # run
    run_p = sub.add_parser("run", help="Run a benchmark suite")
    run_p.add_argument(
        "--suite",
        required=True,
        help="Suite to run — either a bundled name (e.g. 'reflib') or a path to a YAML file. "
        "Run 'kairix benchmark list' for the bundled set.",
    )
    run_p.add_argument(
        "--system",
        default="hybrid",
        choices=["hybrid", "bm25", "vector"],
        help="Retrieval system (default: hybrid)",
    )
    run_p.add_argument(
        "--agent",
        default=None,
        help="Agent name for collection scoping (omit for no scoping)",
    )
    run_p.add_argument(
        "--collection",
        default=None,
        help=(
            "Restrict search to one or more collections (comma-separated). "
            "When a suite carries meta.default_collection, that value auto-applies "
            "unless --collection is set; explicit operator input always wins."
        ),
    )
    run_p.add_argument(
        "--scope",
        default=None,
        choices=["shared", "agent", "shared+agent", "all-agents", "everything"],
        help=(
            "Per-run scope override — routing-boundary control. Not a permission "
            "check (suites validate routing shape, not RBAC). When omitted, the "
            "suite-level default_scope (or pipeline fallback) applies."
        ),
    )
    run_p.add_argument(
        "--categories",
        default=None,
        help=(
            "Filter cases to one or more query categories (comma-separated; e.g. "
            "'recall,entity,procedural'). When omitted, every case is scored."
        ),
    )
    run_p.add_argument(
        "--metrics",
        default=None,
        help=(
            "Opt-in scorers (comma-separated; e.g. 'ndcg,hit_at_k,mrr,judge,latency'). "
            "When omitted, the runner auto-selects scorers per suite shape via "
            "kairix.quality.scoring.auto_select_scorers — present gold_titles → "
            "{ndcg,hit_at_k,mrr}, present expected_answer → judge, present latency "
            "→ latency."
        ),
    )
    run_p.add_argument(
        "--gates",
        action="store_true",
        help=(
            "Exit non-zero on any declared gate failure. Default is informational — "
            "every gate is evaluated and reported but the process still exits 0. "
            "Use --gates in CI to enforce the gate floor."
        ),
    )
    run_p.add_argument(
        "--baseline",
        default=None,
        help=(
            "Path to a previous benchmark result JSON. When set, the runner emits "
            "a compare-with-previous summary alongside the headline scores so the "
            "operator sees the delta on every run."
        ),
    )
    run_p.add_argument(
        "--fusion",
        default=None,
        choices=["bm25_primary", "rrf"],
        help="Override fusion strategy for this run",
    )
    run_p.add_argument("--output", default=None, help="Directory to save JSON report")
    run_p.add_argument(
        "--mode",
        default="legacy",
        choices=["legacy", "single-shot", "concurrent", "soak"],
        help=(
            "Execution mode (default: legacy — the historical NDCG-aggregate path). "
            "'single-shot' additionally surfaces per-query QueryRunResult rows in "
            "the diagnostics envelope via the unified mode dispatcher. "
            "'concurrent' and 'soak' are reserved for P3.b / P3.c slices — invoking "
            "them today emits an affordance pointing at 'kairix probe search' / "
            "'kairix soak run' until the unified dispatchers land."
        ),
    )

    # validate
    val_p = sub.add_parser("validate", help="Validate suite YAML against kairix index")
    val_p.add_argument(
        "--suite",
        required=True,
        help="Suite to validate — bundled name or path (same resolution as 'run').",
    )

    # compare
    cmp_p = sub.add_parser("compare", help="Compare two benchmark result JSON files")
    cmp_p.add_argument("result_a", help="Path to first result JSON")
    cmp_p.add_argument("result_b", help="Path to second result JSON")

    # list
    sub.add_parser("list", help="List bundled suites (resolved from kairix.paths.bundled_suites_root)")

    # init
    init_p = sub.add_parser("init", help="Scaffold a new suite YAML file")
    init_p.add_argument("--agent", required=True, help="Agent name")
    init_p.add_argument(
        "--collections",
        default=None,
        help="Comma-separated collection names (default: vault,knowledge-<agent>)",
    )
    init_p.add_argument(
        "--output",
        default=None,
        help="Output path (default: suites/<agent>.yaml)",
    )

    return parser.parse_args(argv)


# ---------------------------------------------------------------------------
# Dependencies — injectable seams for the CLI subcommands (F6-clean).
# Production callers leave ``deps=None``; tests construct ``BenchmarkCLIDeps``
# with fakes to avoid ``@patch`` (F1) and env-var monkeypatching (F2).
# ---------------------------------------------------------------------------


def _default_run_benchmark(**kwargs: Any) -> Any:
    """Production benchmark runner — lazy import so tests that inject a fake
    never load the heavy retrieval stack."""
    from kairix.quality.benchmark.runner import run_benchmark

    return run_benchmark(**kwargs)


def _default_list_suites() -> list[dict]:
    """Production bundled-suites lister — lazy import for symmetry with
    ``_default_run_benchmark``."""
    from kairix.quality.benchmark.suite import list_bundled_suites

    return list_bundled_suites()


@dataclass(frozen=True)
class BenchmarkCLIDeps:
    """Injectable dependencies for the benchmark CLI subcommands.

    Non-Optional fields wired to production callables via ``default_factory``
    so the dataclass holds no ``None`` sentinels (mypy-clean and F6-clean).
    Tests construct ``BenchmarkCLIDeps(run_benchmark=fake, ...)``; production
    callers leave it ``None`` and the defaults apply.

    Attributes:
        run_benchmark: Callable matching ``runner.run_benchmark``'s kwargs.
                       Captures ``collection``, ``system``, etc. so tests can
                       assert auto-scoping wired the right collection through.
        list_suites:   Callable returning bundled-suite dicts. Defaults to
                       ``suite.list_bundled_suites`` (reads
                       ``kairix.paths.bundled_suites_root()``); tests pass a
                       fake to avoid env-var monkeypatching for the suites
                       root (F2-clean).
    """

    run_benchmark: Callable[..., Any] = field(default_factory=lambda: _default_run_benchmark)
    list_suites: Callable[[], list[dict]] = field(default_factory=lambda: _default_list_suites)


# ---------------------------------------------------------------------------
# Subcommand: run
# ---------------------------------------------------------------------------


def resolve_collection(
    explicit: str | None,
    default_collection: str | None,
) -> tuple[str | None, bool]:
    """Decide which collection the run should target.

    Returns ``(collection, auto_scoped)`` where ``auto_scoped`` is True only
    when the suite's ``default_collection`` was applied because the operator
    didn't pass ``--collection``. Explicit operator input always wins — this
    is the override semantics the issue asks for.
    """
    if explicit is not None:
        return explicit, False
    if default_collection:
        return default_collection, True
    return None, False


def _parse_csv(raw: str | None) -> list[str]:
    """Split a CSV flag value into a list of non-empty tokens.

    Returns ``[]`` when ``raw`` is None or empty. Pulled to a helper so the
    same parse logic backs ``--categories`` and ``--metrics`` without
    duplicating the strip/filter loop (F17).
    """
    if not raw:
        return []
    return [token.strip() for token in raw.split(",") if token.strip()]


def _filter_cases_by_category(suite: Any, categories: list[str]) -> tuple[Any, int]:
    """Return a copy of ``suite`` whose ``cases`` match the requested categories.

    Mutates nothing — returns ``(filtered_suite, n_dropped)``. When
    ``categories`` is empty the suite passes through unchanged. The match is
    against ``case.category`` (the post-alias canonical category); operators
    pass aliases at their own risk.
    """
    if not categories:
        return suite, 0
    wanted = set(categories)
    original = list(suite.cases)
    kept = [c for c in original if c.category in wanted]
    suite.cases = kept
    return suite, len(original) - len(kept)


def _gates_passed(result: Any) -> bool:
    """True when every gate in the result summary passed.

    The runner emits ``summary.gates`` as a ``{gate_name: bool}`` dict. A
    missing gates key is treated as a pass — the legacy runner path may
    not surface gates for every suite shape.
    """
    gates = result.summary.get("gates", {}) if hasattr(result, "summary") else {}
    return all(gates.values()) if gates else True


def _emit_baseline_compare(result: Any, baseline_path: str) -> None:
    """Print a one-line baseline-comparison header.

    The full ``cmd_compare`` formatter handles two result files; this path
    pipes the just-emitted result against a stored baseline. Failure to
    read the baseline is informational — we never abort a run because the
    baseline file is missing.
    """
    try:
        with open(baseline_path) as f:
            baseline = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        print(f"  baseline compare skipped: {exc}")
        return
    a = baseline.get("summary", {}).get("weighted_total", 0.0)
    b = result.summary.get("weighted_total", 0.0)
    delta = b - a
    if delta > 0:
        marker = "▲"
    elif delta < 0:
        marker = "▼"
    else:
        marker = "="
    print(f"  baseline compare: {a:.3f} → {b:.3f}  {marker} {abs(delta):.3f}  (baseline: {baseline_path})")


def _emit_mode_stub(mode: str) -> int:
    """Emit the affordance + exit code for not-yet-implemented modes.

    Concurrent + soak modes are reserved for P3.b / P3.c. Until the
    unified dispatchers wire those paths, point the operator at the legacy
    surface so they can still answer the question. Affordance follows the
    F21 template (``fix:`` / ``next:`` / ``run:`` markers).
    """
    alias = "kairix probe search" if mode == "concurrent" else "kairix soak run"
    print(
        f"❌ --mode {mode} is not yet wired into the unified dispatcher.\n"
        f"   fix: use the legacy CLI for now: {alias}\n"
        f"   next: track the P3.b (concurrent) / P3.c (soak) slices in the "
        f"unified benchmark roadmap.\n"
        f"   run: {alias} --help",
        file=sys.stderr,
    )
    return 1


def _emit_run_header(args: argparse.Namespace, _suite: Any, collection: str | None, auto_scoped: bool) -> None:
    """Print the operator-facing one-screen run header.

    Surfaces every flag the operator passed so the report stands alone
    (no need to scroll back to the invocation). Each header line is
    optional — flags left at their default produce no line.

    ``_suite`` is kept on the signature for future header lines that
    surface suite-level metadata (F19: underscore-prefixed positional
    slot consumed by the caller).
    """
    if auto_scoped:
        print(f"  auto-scoping to collection '{collection}' (from suite.meta.default_collection)")
    metrics = _parse_csv(getattr(args, "metrics", None))
    if metrics:
        print(f"  metrics: {metrics} (operator opt-in; auto-selection bypassed)")
    scope = getattr(args, "scope", None)
    if scope:
        print(f"  scope override: {scope!r}")


def _emit_validation_warnings(suite: Any) -> None:
    """Print suite-level warnings when the kairix index is reachable.

    Skip silently when the index isn't present — validation is a courtesy,
    not a gate; the runner still reports zero hits per missing gold path
    if the operator chooses to score anyway.
    """
    from kairix.quality.benchmark.suite import validate_suite

    try:
        db_path = get_db_path()
    except FileNotFoundError:
        return
    db = open_db(Path(db_path))
    errors = validate_suite(suite, db)
    db.close()
    if not errors:
        return
    print(f"⚠️  Suite warnings ({len(errors)}):")
    for e in errors:
        print(f"   {e}")


def _emit_gate_failure() -> int:
    """Print the F21-formatted gate-failure affordance and return exit 2."""
    print(
        "❌ gate failure — one or more declared gates failed.\n"
        "   fix: investigate the failing gate(s) in the report above.\n"
        "   next: re-run after the fix; drop --gates to see the report informationally.\n"
        "   run: kairix benchmark compare <previous-result.json> <this-result.json>",
        file=sys.stderr,
    )
    return 2


def _load_suite_or_exit(suite_arg: str) -> Any | int:
    """Resolve + load a suite or return the exit code on failure.

    Centralises the FileNotFoundError → "did you mean: list" affordance
    so cmd_run stays flat. Returns the loaded BenchmarkSuite on success.
    """
    from kairix.quality.benchmark.suite import load_suite, resolve_suite_path

    try:
        suite_path = resolve_suite_path(suite_arg)
    except FileNotFoundError as exc:
        print(f"❌ {exc}", file=sys.stderr)
        print("   did you mean: kairix benchmark list?", file=sys.stderr)
        return 1
    suite = load_suite(str(suite_path))
    print(f"Suite: {suite.meta.get('name', suite_arg)}  ({len(suite.cases)} cases)  [{suite_path}]")
    return suite


def _apply_categories(suite: Any, args: argparse.Namespace) -> None:
    """Filter ``suite.cases`` in place by --categories CSV and echo the count."""
    categories = _parse_csv(getattr(args, "categories", None))
    if not categories:
        return
    suite, n_dropped = _filter_cases_by_category(suite, categories)
    print(f"  filtering to categories {categories}: kept {len(suite.cases)} of {len(suite.cases) + n_dropped} cases")


def cmd_run(args: argparse.Namespace, deps: BenchmarkCLIDeps | None = None) -> int:
    """Dispatch a ``kairix benchmark run`` invocation.

    Pipeline: reject stub modes → load suite → filter categories → resolve
    collection → run via injected runner → emit headline → optional baseline
    compare → return exit code (gate-aware when --gates is set).

    Returns 0 on success, 1 on suite-not-found or stub-mode, 2 on gate
    failure (only when --gates is passed). Kept under F16 cognitive
    complexity by delegating each stage to a private helper.
    """
    from kairix.quality.benchmark.runner import format_interpretation

    d = deps or BenchmarkCLIDeps()

    mode_arg = getattr(args, "mode", "legacy")
    if mode_arg in ("concurrent", "soak"):
        return _emit_mode_stub(mode_arg)

    suite_or_rc = _load_suite_or_exit(args.suite)
    if isinstance(suite_or_rc, int):
        return suite_or_rc
    suite = suite_or_rc

    _apply_categories(suite, args)

    explicit_collection = getattr(args, "collection", None)
    default_collection = suite.meta.get("default_collection")
    collection, auto_scoped = resolve_collection(explicit_collection, default_collection)
    _emit_run_header(args, suite, collection, auto_scoped)
    _emit_validation_warnings(suite)

    mode_param: str | None = None if mode_arg == "legacy" else mode_arg

    result = d.run_benchmark(
        suite=suite,
        system=args.system,
        agent=args.agent,
        output_dir=args.output,
        collection=collection,
        fusion_override=getattr(args, "fusion", None),
        mode=mode_param,
    )
    print(format_interpretation(result))

    baseline_path = getattr(args, "baseline", None)
    if baseline_path:
        _emit_baseline_compare(result, baseline_path)

    if getattr(args, "gates", False) and not _gates_passed(result):
        return _emit_gate_failure()
    return 0


def cmd_list(_args: argparse.Namespace, deps: BenchmarkCLIDeps | None = None) -> int:
    """List bundled benchmark suites (resolves #222).

    The ``_args`` parameter is required by the CLI dispatch signature but
    carries no list-specific flags (F19: underscore-prefixed).
    """
    d = deps or BenchmarkCLIDeps()

    suites = d.list_suites()
    if not suites:
        print("No bundled suites found. Set KAIRIX_SUITES_ROOT or cd to a directory containing 'suites/'.")
        return 1

    print(f"{'name':<24}  {'cases':>6}  {'default collection':<24}  description")
    print("-" * 100)
    for s in suites:
        desc = s["description"] or ""
        print(f"{s['name']:<24}  {s['n_cases']:>6}  {(s['default_collection'] or '—'):<24}  {desc[:48]}")
    print()
    print("Run with: kairix benchmark run --suite <name>")
    return 0


# ---------------------------------------------------------------------------
# Subcommand: validate
# ---------------------------------------------------------------------------


def cmd_validate(args: argparse.Namespace) -> int:
    from kairix.quality.benchmark.suite import load_suite, resolve_suite_path, validate_suite

    try:
        suite_path = resolve_suite_path(args.suite)
        suite = load_suite(str(suite_path))
    except (ValueError, FileNotFoundError) as exc:
        print(f"❌ Load error: {exc}", file=sys.stderr)
        return 1

    print(f"Suite: {suite.meta.get('name', args.suite)}  ({len(suite.cases)} cases)")

    try:
        _db_path = get_db_path()
    except FileNotFoundError:
        _db_path = None
    if _db_path is None:
        print("⚠️  kairix index not found — skipping path validation")
        print("✅ Schema validation passed")
        return 0

    db = open_db(Path(_db_path))
    errors = validate_suite(suite, db)
    db.close()

    if errors:
        print(f"❌ Validation failed ({len(errors)} errors):")
        for e in errors:
            print(f"   {e}")
        return 1

    recall_cases = [c for c in suite.cases if c.category == "recall"]
    print(f"✅ Validation passed — {len(recall_cases)} recall gold paths verified in kairix index")
    return 0


# ---------------------------------------------------------------------------
# Subcommand: compare
# ---------------------------------------------------------------------------


def _direction_marker(delta: float, threshold: float = 0.0) -> str:
    """Return an arrow marker for a numeric delta."""
    if delta > threshold:
        return "▲"
    if delta < -threshold:
        return "▼"
    # threshold == 0.0 is an exact equality but on a value that comes from
    # argparse default or operator-supplied --threshold; use math.isclose for
    # the SonarCloud-flagged float comparison.
    return "=" if math.isclose(threshold, 0.0) else " "


def cmd_compare(args: argparse.Namespace) -> int:
    try:
        with open(args.result_a) as f:
            a = json.load(f)
        with open(args.result_b) as f:
            b = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        print(f"❌ Error loading results: {exc}", file=sys.stderr)
        return 1

    a_meta = a.get("meta", {})
    b_meta = b.get("meta", {})
    a_sum = a.get("summary", {})
    b_sum = b.get("summary", {})

    a_label = f"{a_meta.get('system', 'A')} ({a_meta.get('date', '?')})"
    b_label = f"{b_meta.get('system', 'B')} ({b_meta.get('date', '?')})"

    from kairix.quality.benchmark.runner import CATEGORY_WEIGHTS, score_tier

    print("=" * 60)
    print("BENCHMARK COMPARISON")
    print("=" * 60)
    print(f"  A: {a_label}  total={a_sum.get('weighted_total', 0):.3f}  [{score_tier(a_sum.get('weighted_total', 0))}]")
    print(f"  B: {b_label}  total={b_sum.get('weighted_total', 0):.3f}  [{score_tier(b_sum.get('weighted_total', 0))}]")

    delta = b_sum.get("weighted_total", 0) - a_sum.get("weighted_total", 0)
    print(f"\n  Delta: {_direction_marker(delta)} {abs(delta):.3f}")
    a_ndcg = a_sum.get("ndcg_at_10")
    b_ndcg = b_sum.get("ndcg_at_10")
    if a_ndcg is not None and b_ndcg is not None:
        ndcg_delta = b_ndcg - a_ndcg
        print(
            f"  NDCG@10 delta: {_direction_marker(ndcg_delta)} {abs(ndcg_delta):.3f}  (A={a_ndcg:.3f}  B={b_ndcg:.3f})"
        )
    print("")
    print(f"  {'Category':12}  {'A':>6}  {'B':>6}  {'Δ':>6}")
    print(f"  {'-' * 12}  {'-' * 6}  {'-' * 6}  {'-' * 6}")

    a_cats = a_sum.get("category_scores", {})
    b_cats = b_sum.get("category_scores", {})
    for cat in CATEGORY_WEIGHTS:
        a_s = a_cats.get(cat, 0.0)
        b_s = b_cats.get(cat, 0.0)
        d = b_s - a_s
        print(f"  {cat:12}  {a_s:6.3f}  {b_s:6.3f}  {_direction_marker(d, 0.01)}{abs(d):5.3f}")

    print("=" * 60)
    return 0


# ---------------------------------------------------------------------------
# Subcommand: init
# ---------------------------------------------------------------------------


def cmd_init(args: argparse.Namespace) -> int:
    agent = args.agent
    collections = args.collections or f"vault,knowledge-{agent}"

    output = args.output or f"suites/{agent}.yaml"
    Path(output).parent.mkdir(parents=True, exist_ok=True)

    if Path(output).exists():
        print(f"❌ File already exists: {output}", file=sys.stderr)
        return 1

    template = f"""# Benchmark suite for {agent} agent
# Generated by `kairix benchmark init --agent {agent}`
# Edit to add your test cases.

meta:
  name: "{agent}-suite"
  version: "1.0"
  agent: "{agent}"
  collections: [{collections}]
  phase: "1"
  description: "Retrieval quality benchmark for {agent} agent"

cases:
  # Recall cases: exact gold_path match (1.0 or 0.0)
  - id: R01
    category: recall
    query: "example recall query — something specific to your vault"
    gold_path: "path/to/expected/doc.md"
    score_method: exact
    notes: "Should find this specific document"

  # Temporal cases: LLM judge, no gold_path
  - id: T01
    category: temporal
    query: "what happened last week"
    gold_path: null
    score_method: llm

  # Entity cases: LLM judge
  - id: E01
    category: entity
    query: "what do we know about [key person or project]"
    gold_path: null
    score_method: llm

  # Conceptual cases: LLM judge
  - id: C01
    category: conceptual
    query: "how does [system] work"
    gold_path: null
    score_method: llm

  # Multi-hop cases: LLM judge
  - id: M01
    category: multi_hop
    query: "what is the relationship between [A] and [B]"
    gold_path: null
    score_method: llm

  # Procedural cases: LLM judge
  - id: P01
    category: procedural
    query: "how do I [do something]"
    gold_path: null
    score_method: llm
"""

    Path(output).write_text(template, encoding="utf-8")
    print(f"✅ Created suite scaffold: {output}")
    print(f"   Edit the file and run: kairix benchmark validate --suite {output}")
    return 0


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main(
    argv: list[str] | None = None,
    deps: BenchmarkCLIDeps | None = None,
    *,
    parse_args: Callable[..., argparse.Namespace] = _parse_args,
) -> int:
    """Dispatch a single ``kairix benchmark`` invocation.

    Returns the exit code so tests can assert without ``SystemExit``. The
    ``__main__`` shim still calls ``sys.exit(main())`` for the production
    entry point.

    ``deps`` is threaded through to ``cmd_run`` and ``cmd_list`` — the two
    subcommands that talk to retrieval/suite-discovery. ``validate``,
    ``compare``, and ``init`` operate purely on filesystem inputs.

    ``parse_args`` is the public DI seam for tests that want to drive a
    namespace argparse wouldn't normally produce (e.g. the unknown-subcommand
    fallthrough). Production callers leave it at the default.
    """
    args = parse_args(argv)

    if args.subcommand == "run":
        return cmd_run(args, deps=deps)
    if args.subcommand == "list":
        return cmd_list(args, deps=deps)

    handlers = {
        "validate": cmd_validate,
        "compare": cmd_compare,
        "init": cmd_init,
    }
    handler = handlers.get(args.subcommand)
    if handler:
        return handler(args)
    print(f"Unknown subcommand: {args.subcommand}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())

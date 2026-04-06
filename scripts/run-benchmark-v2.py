#!/usr/bin/env python3
"""
run-benchmark-v2.py — Phase 5C: Mnemosyne v2 real-world benchmark runner

Metrics: NDCG@10 (primary), Hit Rate@5, MRR@10, Precision@5, Recall@10
Uses graded relevance (0/1/2) from suites/v2-real-world.yaml

Usage:
    sudo -u openclaw bash -c "source /opt/openclaw/env/openclaw.env && \\
      .venv/bin/python scripts/run-benchmark-v2.py phase5-baseline \\
      [--suite suites/v2-real-world.yaml] [--k 10] [--agent shape]"
"""
from __future__ import annotations

import argparse
import datetime
import json
import math
import subprocess
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

import yaml

# ── Paths ─────────────────────────────────────────────────────────────────────

SCRIPT_DIR    = Path(__file__).parent
PROJECT_DIR   = SCRIPT_DIR.parent
RESULTS_DIR   = Path("/data/obsidian-vault/01-Projects/202603-Mnemosyne/benchmark-results")
RESULTS_DIR.mkdir(parents=True, exist_ok=True)

MNEMOSYNE     = Path("/opt/openclaw/bin/mnemosyne")
SEARCH_BUDGET = 5000

# ── NDCG helpers ──────────────────────────────────────────────────────────────

def dcg(relevances: list[float], k: int) -> float:
    """Discounted Cumulative Gain at k."""
    return sum(
        rel / math.log2(i + 2)
        for i, rel in enumerate(relevances[:k])
    )


def ideal_dcg(gold_paths: list[dict], k: int) -> float:
    """IDCG: ideal ordering (sort by relevance desc, take top k)."""
    sorted_rels = sorted(
        [g["relevance"] for g in gold_paths],
        reverse=True,
    )
    return dcg(sorted_rels, k)


def ndcg_at_k(retrieved: list[str], gold_paths: list[dict], k: int) -> float:
    """Compute NDCG@k given retrieved path list and graded gold list."""
    if not gold_paths:
        return 0.0
    idcg = ideal_dcg(gold_paths, k)
    if idcg == 0.0:
        return 0.0
    gold_map = {g["path"].lower(): g["relevance"] for g in gold_paths}
    rels = [gold_map.get(p.lower(), 0) for p in retrieved[:k]]
    return dcg(rels, k) / idcg


def hit_rate_at_k(retrieved: list[str], gold_paths: list[dict], k: int) -> bool:
    """True if any gold path appears in top-k retrieved."""
    gold_set = {g["path"].lower() for g in gold_paths}
    return any(p.lower() in gold_set for p in retrieved[:k])


def reciprocal_rank(retrieved: list[str], gold_paths: list[dict], k: int) -> float:
    """Reciprocal rank of first gold path in top-k (0 if none)."""
    gold_set = {g["path"].lower() for g in gold_paths}
    for i, p in enumerate(retrieved[:k]):
        if p.lower() in gold_set:
            return 1.0 / (i + 1)
    return 0.0


def precision_at_k(retrieved: list[str], gold_paths: list[dict], k: int) -> float:
    """Fraction of top-k retrieved that are gold (binary, any relevance ≥ 1)."""
    if k == 0:
        return 0.0
    gold_set = {g["path"].lower() for g in gold_paths}
    hits = sum(1 for p in retrieved[:k] if p.lower() in gold_set)
    return hits / k


def recall_at_k(retrieved: list[str], gold_paths: list[dict], k: int) -> float:
    """Fraction of gold paths found in top-k retrieved."""
    if not gold_paths:
        return 0.0
    gold_set = {g["path"].lower() for g in gold_paths}
    hits = sum(1 for p in retrieved[:k] if p.lower() in gold_set)
    return hits / len(gold_set)


# ── Search ────────────────────────────────────────────────────────────────────

def run_search(query: str, agent: str) -> tuple[list[str], dict]:
    """Run mnemosyne search, return (path_list, meta_dict)."""
    agent = agent or "shape"
    cmd = [
        str(MNEMOSYNE), "search", "--json",
        "--budget", str(SEARCH_BUDGET),
        "--agent", agent,
        query,
    ]
    try:
        t0 = time.time()
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        latency_ms = (time.time() - t0) * 1000
        if result.returncode != 0:
            return [], {"error": result.stderr[:200], "latency_ms": latency_ms}
        data = json.loads(result.stdout)
        paths = [r["path"] for r in data.get("results", [])]
        meta  = {
            "intent":     data.get("intent"),
            "bm25_count": data.get("bm25_count"),
            "vec_count":  data.get("vec_count"),
            "vec_failed": data.get("vec_failed", False),
            "latency_ms": data.get("latency_ms") or latency_ms,
        }
        return paths, meta
    except subprocess.TimeoutExpired:
        return [], {"error": "timeout", "latency_ms": 30000}
    except (json.JSONDecodeError, KeyError) as e:
        return [], {"error": str(e), "latency_ms": 0}


# ── Metrics aggregation ───────────────────────────────────────────────────────

def _safe_mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def aggregate_metrics(per_query: list[dict]) -> dict:
    return {
        "ndcg_at_10":     _safe_mean([r["ndcg"]        for r in per_query]),
        "hit_rate_at_5":  _safe_mean([float(r["hit_at_5"]) for r in per_query]),
        "mrr_at_10":      _safe_mean([r["rr"]           for r in per_query]),
        "precision_at_5": _safe_mean([r["precision_5"]  for r in per_query]),
        "recall_at_10":   _safe_mean([r["recall_10"]    for r in per_query]),
    }


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Phase 5C: Mnemosyne v2 benchmark runner")
    parser.add_argument("run_label", nargs="?", default="phase5-baseline",
                        help="Run label (used in output filename)")
    parser.add_argument("--suite",  type=Path,
                        default=PROJECT_DIR / "suites" / "v2-real-world.yaml",
                        help="Suite YAML (default: suites/v2-real-world.yaml)")
    parser.add_argument("--k",      type=int, default=10,
                        help="Rank cutoff for NDCG/MRR/Recall (default: 10)")
    parser.add_argument("--k-hit",  type=int, default=5,
                        help="Rank cutoff for Hit Rate and Precision (default: 5)")
    parser.add_argument("--agent",  type=str, default=None,
                        help="Override agent for all queries (default: per-case agent field)")
    parser.add_argument("--limit",  type=int, default=0,
                        help="Only run first N cases (0 = all, for debug)")
    args = parser.parse_args()

    K     = args.k
    K_HIT = args.k_hit
    date_str    = datetime.date.today().isoformat()
    output_file = RESULTS_DIR / f"B2-{args.run_label}-{date_str}.json"

    print(f"Mnemosyne v2 Benchmark — {args.run_label} — {date_str}")
    print(f"Suite: {args.suite}")
    print(f"Output: {output_file}")
    print(f"Metrics: NDCG@{K}, Hit Rate@{K_HIT}, MRR@{K}, Precision@{K_HIT}, Recall@{K}")
    print()

    if not args.suite.exists():
        print(f"[ERROR] Suite not found: {args.suite}", file=sys.stderr)
        sys.exit(1)

    with args.suite.open() as f:
        suite_data = yaml.safe_load(f)

    cases = suite_data.get("cases", [])
    if args.limit > 0:
        cases = cases[:args.limit]

    total = len(cases)
    print(f"Cases: {total}")
    print()

    per_query_results: list[dict] = []
    by_category:  dict[str, list[dict]] = defaultdict(list)
    by_source:    dict[str, list[dict]] = defaultdict(list)

    for i, case in enumerate(cases):
        cid        = case["id"]
        query      = case["query"]
        category   = case["category"]
        source     = case.get("source", "unknown")
        agent      = args.agent or case.get("agent", "shape")
        gold_paths = case.get("gold_paths") or []

        t0 = time.time()
        retrieved, meta = run_search(query, agent)
        latency_ms = (time.time() - t0) * 1000

        nd   = ndcg_at_k(retrieved, gold_paths, K)
        hit  = hit_rate_at_k(retrieved, gold_paths, K_HIT)
        rr   = reciprocal_rank(retrieved, gold_paths, K)
        p5   = precision_at_k(retrieved, gold_paths, K_HIT)
        r10  = recall_at_k(retrieved, gold_paths, K)

        vec_sym = "✓" if not meta.get("vec_failed") else "✗"
        conf    = case.get("judge_confidence", "?")
        print(
            f"  [{i+1:>4}/{total}] {cid:<10} "
            f"NDCG={nd:.3f} Hit={int(hit)} RR={rr:.3f} "
            f"P@5={p5:.2f} R@10={r10:.2f}  "
            f"vec={vec_sym} conf={conf}  "
            f"{query[:40]}"
        )

        entry = {
            "id":               cid,
            "category":         category,
            "source":           source,
            "query":            query,
            "agent":            agent,
            "ndcg":             nd,
            "hit_at_5":         hit,
            "rr":               rr,
            "precision_5":      p5,
            "recall_10":        r10,
            "retrieved_paths":  retrieved[:K],
            "gold_paths":       [g["path"] for g in gold_paths],
            "gold_relevances":  {g["path"]: g["relevance"] for g in gold_paths},
            "latency_ms":       meta.get("latency_ms", latency_ms),
            "intent":           meta.get("intent"),
            "vec_failed":       meta.get("vec_failed", False),
            "judge_confidence": case.get("judge_confidence"),
        }

        per_query_results.append(entry)
        by_category[category].append(entry)
        by_source[source].append(entry)

    # ── Aggregate ─────────────────────────────────────────────────────────────

    print()
    print("=== Aggregate Metrics ===")
    overall = aggregate_metrics(per_query_results)
    for metric, value in overall.items():
        print(f"  {metric:<18} {value:.4f}")

    print()
    print("=== By Category ===")
    by_cat_agg: dict[str, dict] = {}
    for cat in sorted(by_category):
        rows = by_category[cat]
        m    = aggregate_metrics(rows)
        by_cat_agg[cat] = {**m, "n": len(rows)}
        print(
            f"  {cat:<12} n={len(rows):>3}  "
            f"NDCG={m['ndcg_at_10']:.4f}  "
            f"Hit@5={m['hit_rate_at_5']:.4f}  "
            f"MRR={m['mrr_at_10']:.4f}"
        )

    print()
    print("=== By Source ===")
    by_src_agg: dict[str, dict] = {}
    for src in sorted(by_source):
        rows = by_source[src]
        m    = aggregate_metrics(rows)
        by_src_agg[src] = {**m, "n": len(rows)}
        print(f"  {src:<14} n={len(rows):>3}  NDCG={m['ndcg_at_10']:.4f}")

    # ── Thin category warning ─────────────────────────────────────────────────

    thin = [cat for cat, rows in by_category.items() if len(rows) < 5]
    if thin:
        print(f"\n[WARN] Thin categories (<5 cases): {thin}")

    # ── Write output ──────────────────────────────────────────────────────────

    suite_meta = suite_data.get("meta", {})
    output = {
        "run_label":    args.run_label,
        "date":         date_str,
        "system":       "mnemosyne-hybrid-phase5",
        "suite":        str(args.suite.name),
        "suite_version": suite_meta.get("version", "1.0"),
        "n_queries":    len(per_query_results),
        "k":            K,
        "k_hit":        K_HIT,
        "metrics":      overall,
        "by_category":  by_cat_agg,
        "by_source":    by_src_agg,
        "results":      per_query_results,
    }

    output_file.write_text(json.dumps(output, indent=2))
    print(f"\nSaved: {output_file}")
    print(f"NDCG@{K}: {overall['ndcg_at_10']:.4f}  Hit Rate@{K_HIT}: {overall['hit_rate_at_5']:.4f}")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
Analyse real query distribution from queries.jsonl.

Usage:
    python3 scripts/analyze_queries.py [--log /data/mnemosyne/logs/queries.jsonl]
    python3 scripts/analyze_queries.py --log /tmp/my-queries.jsonl --min-count 2

Output:
    - Intent distribution (count + %)
    - Top repeated queries (by exact text)
    - Queries with 0 results
    - Queries with vec_failed=True
    - P50/P90/P95 latency by intent
    - Top result paths (most frequently returned docs)
"""
import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path


def _percentile(sorted_values: list[float], pct: float) -> float:
    """Return the pct-th percentile from a pre-sorted list."""
    if not sorted_values:
        return 0.0
    idx = (len(sorted_values) - 1) * pct / 100
    lo = int(idx)
    hi = lo + 1
    if hi >= len(sorted_values):
        return sorted_values[lo]
    frac = idx - lo
    return sorted_values[lo] + frac * (sorted_values[hi] - sorted_values[lo])


def main() -> None:
    parser = argparse.ArgumentParser(description="Analyse Mnemosyne query log")
    parser.add_argument("--log", default="/data/mnemosyne/logs/queries.jsonl",
                        help="Path to queries.jsonl")
    parser.add_argument("--min-count", type=int, default=1,
                        help="Minimum repeat count for top-queries report")
    parser.add_argument("--top", type=int, default=20,
                        help="How many top items to show in each section")
    args = parser.parse_args()

    path = Path(args.log)
    if not path.exists():
        print(f"No query log found at {path}")
        print("Enable with: MNEMOSYNE_LOG_QUERIES=1")
        return

    log: list[dict] = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                log.append(json.loads(line))
            except json.JSONDecodeError:
                pass

    if not log:
        print("Empty log")
        return

    total = len(log)
    print(f"\n{'=' * 60}")
    print(f"  Mnemosyne Query Log Analysis  ({total} entries)")
    print(f"{'=' * 60}\n")

    # ------------------------------------------------------------------
    # 1. Intent distribution
    # ------------------------------------------------------------------
    intent_counter: Counter = Counter(e.get("intent", "unknown") for e in log)
    print("── Intent Distribution ─────────────────────────────────────")
    for intent, count in intent_counter.most_common():
        pct = count / total * 100
        print(f"  {intent:<20} {count:>6}  ({pct:5.1f}%)")
    print()

    # ------------------------------------------------------------------
    # 2. Top repeated queries
    # ------------------------------------------------------------------
    query_counter: Counter = Counter(e.get("query", "") for e in log)
    top_queries = [(q, c) for q, c in query_counter.most_common(args.top) if c >= args.min_count]
    print("── Top Repeated Queries ────────────────────────────────────")
    if top_queries:
        for query_text, count in top_queries:
            truncated = (query_text[:70] + "…") if len(query_text) > 72 else query_text
            print(f"  [{count:>4}x]  {truncated}")
    else:
        print("  (no repeated queries)")
    print()

    # ------------------------------------------------------------------
    # 3. Queries with 0 results (fused_count == 0)
    # ------------------------------------------------------------------
    zero_results = [e for e in log if e.get("fused_count", -1) == 0]
    print("── Zero-Result Queries ─────────────────────────────────────")
    print(f"  Total: {len(zero_results)} of {total} ({len(zero_results)/total*100:.1f}%)")
    for entry in zero_results[:args.top]:
        q = entry.get("query", "")
        truncated = (q[:70] + "…") if len(q) > 72 else q
        print(f"    [{entry.get('intent', '?')}]  {truncated}")
    if len(zero_results) > args.top:
        print(f"    … and {len(zero_results) - args.top} more")
    print()

    # ------------------------------------------------------------------
    # 4. Queries with vec_failed=True
    # ------------------------------------------------------------------
    vec_failed_entries = [e for e in log if e.get("vec_failed") is True]
    print("── Vector-Failed Queries ───────────────────────────────────")
    print(f"  Total: {len(vec_failed_entries)} of {total} ({len(vec_failed_entries)/total*100:.1f}%)")
    if vec_failed_entries:
        failed_intents: Counter = Counter(e.get("intent", "unknown") for e in vec_failed_entries)
        for intent, count in failed_intents.most_common():
            print(f"    {intent:<20} {count:>5} failures")
    print()

    # ------------------------------------------------------------------
    # 5. P50/P90/P95 latency by intent
    # ------------------------------------------------------------------
    latency_by_intent: dict[str, list[float]] = defaultdict(list)
    for entry in log:
        intent = entry.get("intent", "unknown")
        latency = entry.get("latency_ms")
        if isinstance(latency, (int, float)):
            latency_by_intent[intent].append(float(latency))

    print("── Latency by Intent (ms) ──────────────────────────────────")
    header = f"  {'Intent':<20} {'N':>6}  {'P50':>8}  {'P90':>8}  {'P95':>8}"
    print(header)
    print("  " + "-" * 58)
    for intent in sorted(latency_by_intent):
        vals = sorted(latency_by_intent[intent])
        p50 = _percentile(vals, 50)
        p90 = _percentile(vals, 90)
        p95 = _percentile(vals, 95)
        print(f"  {intent:<20} {len(vals):>6}  {p50:>8.1f}  {p90:>8.1f}  {p95:>8.1f}")
    print()

    # ------------------------------------------------------------------
    # 6. Top result paths (most frequently returned docs)
    # ------------------------------------------------------------------
    path_counter: Counter = Counter()
    for entry in log:
        for p in entry.get("top_paths", []):
            path_counter[p] += 1

    print("── Top Result Paths (most frequently returned docs) ────────")
    if path_counter:
        for doc_path, count in path_counter.most_common(args.top):
            pct = count / total * 100
            truncated = ("…" + doc_path[-67:]) if len(doc_path) > 70 else doc_path
            print(f"  [{count:>4}x / {pct:4.1f}%]  {truncated}")
    else:
        print("  (no path data)")
    print()

    print(f"{'=' * 60}\n")


if __name__ == "__main__":
    main()

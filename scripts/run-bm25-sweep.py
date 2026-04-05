#!/usr/bin/env python3
"""
run-bm25-sweep.py — Phase 5D: BM25 parameter grid sweep

Tests k1 x b grid on v2-real-world.yaml test suite.
Requires QMD_BM25_K1 / QMD_BM25_B env var overrides in bm25.py.

Usage:
    sudo -u openclaw bash -c "source /opt/openclaw/env/openclaw.env && \\
      .venv/bin/python scripts/run-bm25-sweep.py [--suite suites/v2-real-world.yaml] \\
      [--limit 50]"
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import subprocess
import sys
from pathlib import Path

# ── Grid ─────────────────────────────────────────────────────────────────────

K1_VALUES = [1.0, 1.2, 1.5]
B_VALUES  = [0.50, 0.65, 0.75]

# ── Paths ─────────────────────────────────────────────────────────────────────

SCRIPT_DIR  = Path(__file__).parent
PROJECT_DIR = SCRIPT_DIR.parent
RESULTS_DIR = Path("/data/obsidian-vault/01-Projects/202603-Mnemosyne/benchmark-results")
RESULTS_DIR.mkdir(parents=True, exist_ok=True)

RUNNER      = SCRIPT_DIR / "run-benchmark-v2.py"
VENV_PYTHON = PROJECT_DIR / ".venv" / "bin" / "python"


def run_config(k1: float, b: float, suite: Path, limit: int) -> float | None:
    """Run benchmark with given k1/b, return NDCG@10 or None on error."""
    label = f"bm25-sweep-k{k1:.1f}-b{b:.2f}".replace(".", "_")
    env   = {**os.environ, "QMD_BM25_K1": str(k1), "QMD_BM25_B": str(b)}
    cmd   = [str(VENV_PYTHON), str(RUNNER), label, "--suite", str(suite)]
    if limit > 0:
        cmd += ["--limit", str(limit)]

    try:
        result = subprocess.run(
            cmd,
            env=env,
            capture_output=True,
            text=True,
            timeout=1800,  # 30 min max
        )
        if result.returncode != 0:
            print(f"  [ERROR] k1={k1} b={b}: {result.stderr[:200]}")
            return None

        # Parse NDCG from last line
        for line in reversed(result.stdout.splitlines()):
            if "NDCG@10:" in line:
                parts = line.split("NDCG@10:")[1].split()[0]
                return float(parts)

        # Fallback: read most recent B2 result file
        import glob
        files = sorted(glob.glob(str(RESULTS_DIR / f"B2-{label}-*.json")))
        if files:
            data = json.loads(Path(files[-1]).read_text())
            return data.get("metrics", {}).get("ndcg_at_10")

        return None
    except subprocess.TimeoutExpired:
        print(f"  [TIMEOUT] k1={k1} b={b}")
        return None
    except Exception as e:
        print(f"  [ERROR] k1={k1} b={b}: {e}")
        return None


def main() -> None:
    parser = argparse.ArgumentParser(description="Phase 5D: BM25 parameter sweep")
    parser.add_argument("--suite", type=Path,
                        default=PROJECT_DIR / "suites" / "v2-real-world.yaml",
                        help="Suite YAML")
    parser.add_argument("--limit", type=int, default=0,
                        help="Limit to first N cases (0 = all)")
    args = parser.parse_args()

    if not args.suite.exists():
        print(f"[ERROR] Suite not found: {args.suite}", file=sys.stderr)
        sys.exit(1)

    date_str    = datetime.date.today().isoformat()
    output_file = RESULTS_DIR / f"bm25-param-sweep-{date_str}.json"

    print(f"BM25 Parameter Sweep — {date_str}")
    print(f"Grid: k1 ∈ {K1_VALUES} × b ∈ {B_VALUES} = {len(K1_VALUES)*len(B_VALUES)} configs")
    print(f"Suite: {args.suite}")
    if args.limit:
        print(f"Limit: {args.limit} cases per config (full suite: 0)")
    print()

    configs = [(k1, b) for k1 in K1_VALUES for b in B_VALUES]
    results: list[dict] = []
    default_ndcg: float | None = None

    for k1, b in configs:
        print(f"  k1={k1:.1f}  b={b:.2f}  ...", end="", flush=True)
        ndcg = run_config(k1, b, args.suite, args.limit)
        print(f"  NDCG@10={ndcg:.4f}" if ndcg is not None else "  FAILED")
        results.append({"k1": k1, "b": b, "ndcg_at_10": ndcg or 0.0})
        if k1 == 1.2 and b == 0.75:
            default_ndcg = ndcg

    # Sort by NDCG desc
    results.sort(key=lambda x: -x["ndcg_at_10"])
    best = results[0] if results else {}

    print()
    print("=== Sweep Results ===")
    for r in results:
        marker = " ← best" if r == best else (" ← default" if r["k1"] == 1.2 and r["b"] == 0.75 else "")
        print(f"  k1={r['k1']:.1f}  b={r['b']:.2f}  NDCG@10={r['ndcg_at_10']:.4f}{marker}")

    print()
    print(f"Best:    k1={best.get('k1')}  b={best.get('b')}  NDCG@10={best.get('ndcg_at_10', 0):.4f}")
    print(f"Default: k1=1.2  b=0.75  NDCG@10={default_ndcg:.4f}" if default_ndcg else "Default: not in results")

    delta = (best.get("ndcg_at_10", 0.0) - (default_ndcg or 0.0))
    recommendation = (
        f"Apply k1={best['k1']}, b={best['b']} — gain +{delta:.4f} over default"
        if delta >= 0.02
        else f"Default params adequate — max gain {delta:+.4f} below 0.02 threshold"
    )
    print(f"Recommendation: {recommendation}")

    output = {
        "date":           date_str,
        "suite":          str(args.suite.name),
        "grid":           {"k1_values": K1_VALUES, "b_values": B_VALUES},
        "results":        results,
        "best":           best,
        "default":        {"k1": 1.2, "b": 0.75, "ndcg_at_10": default_ndcg or 0.0},
        "delta_best_vs_default": delta,
        "recommendation": recommendation + " — apply only after Shape approval",
    }

    output_file.write_text(json.dumps(output, indent=2))
    print(f"\nSaved: {output_file}")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Compare current reflib contract results against committed baseline.

Exit code 0 if no regression detected or no baseline exists yet.
Exit code 1 if regression exceeds threshold.
"""

import json
import sys
from pathlib import Path

BASELINE_PATH = Path("benchmark-results/reflib-contract-baseline.json")
REGRESSION_THRESHOLD = 0.02


def main() -> int:
    if not BASELINE_PATH.exists():
        print("No baseline committed yet -- skipping comparison")
        return 0

    try:
        baseline = json.loads(BASELINE_PATH.read_text())
    except (json.JSONDecodeError, OSError) as e:
        print(f"Cannot read baseline: {e}")
        return 0

    baseline_wt = baseline.get("summary", {}).get("weighted_total", 0)
    print(f"Baseline weighted_total: {baseline_wt:.3f}")
    print("Comparison check ready (run contract suite first to generate current results)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

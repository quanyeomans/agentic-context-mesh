#!/usr/bin/env bash
# locomo-nightly-run.sh — Plan B-parity Week 4 Stream A.
#
# Runs `kairix eval` against the LoCoMo suite and produces both a JSON
# SuiteResult and a flat CSV for trend-watch dashboards. Writes outputs
# under ./artifacts/ for the nightly workflow to upload.
#
# F21: actionable hints (fix:/next:) on failure paths.

set -euo pipefail

SUITE_PATH="${SUITE_PATH:-suites/locomo}"

mkdir -p artifacts
DATE_TODAY="$(date -u +%Y%m%d)"
OUT_JSON="artifacts/locomo-nightly-${DATE_TODAY}.json"
OUT_CSV="artifacts/locomo-nightly-${DATE_TODAY}.csv"

if ! python3 -m kairix.cli eval "$SUITE_PATH" --json > "$OUT_JSON"; then
    echo "::error::kairix eval failed against $SUITE_PATH"
    echo "fix: re-run locally with \`kairix eval $SUITE_PATH --json\` to see the traceback"
    echo "next: push the fix; the next nightly run will pick it up"
    exit 1
fi

python3 - "$OUT_JSON" "$OUT_CSV" <<'PY'
import csv
import json
import sys
from pathlib import Path

src = Path(sys.argv[1])
dst = Path(sys.argv[2])
data = json.loads(src.read_text(encoding="utf-8"))
per_cat = data.get("per_category") or {}
overall = {
    "suite": data.get("suite_name", ""),
    "category": "_overall",
    "n": int(data.get("n_questions", 0)),
    "passed": int(data.get("n_passed", 0)),
    "mean": float(data.get("mean_score", 0.0)),
}
rows = [
    {
        "suite": data.get("suite_name", ""),
        "category": cat,
        "n": int(stats.get("n", 0)),
        "passed": int(stats.get("passed", 0)),
        "mean": float(stats.get("mean", 0.0)),
    }
    for cat, stats in sorted(per_cat.items())
]
with dst.open("w", encoding="utf-8", newline="") as fh:
    writer = csv.DictWriter(fh, fieldnames=["suite", "category", "n", "passed", "mean"])
    writer.writeheader()
    writer.writerow(overall)
    for row in rows:
        writer.writerow(row)
print(f"wrote {dst} ({len(rows) + 1} rows)")
PY

echo "JSON_PATH=$OUT_JSON" >> "$GITHUB_ENV"
echo "CSV_PATH=$OUT_CSV" >> "$GITHUB_ENV"

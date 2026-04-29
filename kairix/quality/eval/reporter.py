"""Performance reporter — generates per-category NDCG markdown report."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, ClassVar

from kairix.quality.eval.constants import REPORTER_GATES


class PerformanceReporter:
    """
    Reads benchmark result JSONL and generates a markdown performance report.

    Usage:
        reporter = PerformanceReporter(results_path="results/r3-post-epic1.json")
        print(reporter.markdown_report(previous_path="results/r2-baseline.json"))
    """

    # Gate thresholds — can be overridden at construction time
    GATES: ClassVar[dict[str, float]] = REPORTER_GATES

    def __init__(
        self,
        results_path: str | Path,
        gates: dict[str, float] | None = None,
    ) -> None:
        self._path = Path(results_path)
        self._results = self._load(self._path)
        self._gates = {**type(self).GATES, **(gates or {})}

    @staticmethod
    def _load(path: Path) -> dict[str, Any]:
        if not path.exists():
            return {}
        text = path.read_text(encoding="utf-8")
        result: dict[str, Any] = json.loads(text)
        return result

    def _category_scores(self, results: dict) -> dict[str, float]:
        scores: dict[str, float] = {}
        for check in results.get("checks", []):
            cat = check.get("category") or check.get("name", "")
            score = check.get("ndcg") or check.get("score", 0.0)
            if cat:
                scores[cat] = float(score)
        if "overall_ndcg" in results:
            scores["overall"] = float(results["overall_ndcg"])
        elif "ndcg" in results:
            scores["overall"] = float(results["ndcg"])
        return scores

    def passes_gates(self) -> bool:
        scores = self._category_scores(self._results)
        for category, threshold in self._gates.items():
            if scores.get(category, 0.0) < threshold:
                return False
        return True

    def gate_failures(self) -> list[tuple[str, float, float]]:
        """Return list of (category, actual_score, gate_threshold) for failures."""
        scores = self._category_scores(self._results)
        failures = []
        for category, threshold in self._gates.items():
            actual = scores.get(category, 0.0)
            if actual < threshold:
                failures.append((category, actual, threshold))
        return failures

    def markdown_report(self, previous_path: str | Path | None = None) -> str:
        prev_scores: dict[str, float] = {}
        if previous_path:
            prev_results = self._load(Path(previous_path))
            prev_scores = self._category_scores(prev_results)

        scores = self._category_scores(self._results)
        generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        lines = [
            "# Kairix Benchmark — Performance Report",
            f"_Generated: {generated_at}_",
            f"_Results: {self._path.name}_",
            "",
            "## NDCG@10 by Category",
            "",
            "| Category | Score | Gate | Status | Delta |",
            "|----------|-------|------|--------|-------|",
        ]

        all_cats = sorted(set(list(scores.keys()) + list(self._gates.keys())))
        for cat in all_cats:
            score = scores.get(cat, None)
            gate = self._gates.get(cat)
            score_str = f"{score:.3f}" if score is not None else "—"
            gate_str = f"{gate:.2f}" if gate is not None else "—"

            if score is None:
                status = "⬜ no data"
            elif gate is None:
                status = "(i) no gate"
            elif score >= gate:
                status = "✅ pass"
            else:
                status = "❌ FAIL"

            prev = prev_scores.get(cat)
            if prev is not None and score is not None:
                delta = score - prev
                delta_str = f"{delta:+.3f}"
            else:
                delta_str = "—"

            lines.append(f"| {cat} | {score_str} | {gate_str} | {status} | {delta_str} |")

        lines += ["", "## Gate Summary", ""]
        failures = self.gate_failures()
        if not failures:
            lines.append("✅ **All gates passed.**")
        else:
            lines.append("❌ **Gate failures:**")
            for cat, actual, threshold in failures:
                lines.append(f"- `{cat}`: {actual:.3f} < {threshold:.2f} (gap: {threshold - actual:.3f})")

        return "\n".join(lines) + "\n"

"""Search tuning recommendations based on gold suite analysis.

Analyses benchmark results by category, identifies weaknesses, and
recommends parameter changes based on corpus characteristics.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class TuneAnalysis:
    """Analysis of benchmark results for tuning."""

    scores: dict[str, float]
    floor: float
    weak_categories: list[str] = field(default_factory=list)


@dataclass
class CorpusHints:
    """Corpus characteristics that inform tuning recommendations."""

    has_date_files: bool = False
    has_procedural_docs: bool = False
    has_entity_folders: bool = False


@dataclass
class Recommendation:
    """A specific parameter change recommendation."""

    parameter: str
    action: str
    reason: str
    expected_impact: str


def analyse_results(scores: dict[str, float], floor: float = 0.50) -> TuneAnalysis:
    """Identify categories scoring below the floor threshold."""
    weak = [cat for cat, score in scores.items() if score < floor]
    return TuneAnalysis(scores=scores, floor=floor, weak_categories=sorted(weak))


def recommend(weak_categories: list[str], hints: CorpusHints) -> list[Recommendation]:
    """Generate parameter recommendations for weak categories.

    Uses corpus hints to determine which boosts are likely to help.
    Only recommends changes where the corpus supports the boost.
    """
    recs: list[Recommendation] = []

    if "temporal" in weak_categories and hints.has_date_files:
        recs.append(
            Recommendation(
                parameter="temporal",
                action="Enable date_path_boost with factor 1.35",
                reason=(
                    "Your documents contain date-named files. Temporal boost "
                    "will promote recent content for time-sensitive queries."
                ),
                expected_impact="temporal +0.10 to +0.20",
            )
        )

    if "procedural" in weak_categories and hints.has_procedural_docs:
        recs.append(
            Recommendation(
                parameter="procedural",
                action="Extend procedural path patterns",
                reason=(
                    "Your documents contain procedural content (how-to, runbook, "
                    "guide patterns). Extending boost patterns improves how-to ranking."
                ),
                expected_impact="procedural +0.10 to +0.15",
            )
        )

    if "entity" in weak_categories and hints.has_entity_folders:
        recs.append(
            Recommendation(
                parameter="entity",
                action="Increase entity boost factor from 0.20 to 0.30",
                reason=(
                    "Your documents have entity-rich folders. A stronger entity "
                    "signal will improve person/company queries."
                ),
                expected_impact="entity +0.05 to +0.10",
            )
        )

    if "conceptual" in weak_categories:
        recs.append(
            Recommendation(
                parameter="conceptual",
                action="Consider switching fusion_strategy to rrf",
                reason=(
                    "Conceptual queries rely on semantic similarity more than "
                    "keyword matching. RRF gives equal weight to vector search."
                ),
                expected_impact="conceptual +0.05 to +0.15",
            )
        )

    if "recall" in weak_categories:
        recs.append(
            Recommendation(
                parameter="recall",
                action="Verify document titles are descriptive and unique",
                reason=(
                    "Recall depends on keyword matching against titles and paths. "
                    "Generic titles (README, index) reduce BM25 precision."
                ),
                expected_impact="recall +0.05 to +0.10",
            )
        )

    return recs

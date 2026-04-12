"""
kairix.curator — Curator agent: entity graph health monitoring and enrichment.

The Curator is a plain Python module (ADR-003) — no agent framework dependency.
It monitors the entity graph for quality issues and surfaces them for human review.

Provides:
  health  — run_health_check(), HealthReport, HealthIssue
"""

from kairix.curator.health import HealthIssue, HealthReport, run_health_check

__all__ = [
    "HealthIssue",
    "HealthReport",
    "run_health_check",
]

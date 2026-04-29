"""
Centralised evaluation constants for kairix.

Single source of truth for category weights and aliases used by the benchmark
runner, eval sweeps, and the quality monitor.
"""

from __future__ import annotations

CATEGORY_WEIGHTS: dict[str, float] = {
    "recall": 0.25,
    "temporal": 0.20,
    "entity": 0.20,
    "conceptual": 0.15,
    "multi_hop": 0.10,
    "procedural": 0.10,
    "classification": 0.0,
}

CATEGORY_ALIASES: dict[str, str] = {
    "semantic": "recall",
    "keyword": "conceptual",
}

# Phase gate thresholds — used by benchmark runner and performance reporter
PHASE_GATES: dict[str, float] = {
    "phase1": 0.62,
    "phase2": 0.68,
    "phase3": 0.75,
}

# Reporter gate thresholds — per-category minimum scores
REPORTER_GATES: dict[str, float] = {
    "overall": 0.78,
    "temporal": 0.55,
    "entity": 0.80,
    "contextual_prep": 0.60,
}

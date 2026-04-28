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

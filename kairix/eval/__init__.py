"""kairix.eval — Structured query logging and performance evaluation.

Provides:
  - QueryLogEntry: typed dataclass for structured query log records
  - QueryLogger: writes structured JSONL query logs
  - NDCG, MRR, Hit@K metrics (extracted from benchmark/runner.py)
  - PerformanceReporter: generates per-category NDCG markdown reports
"""
from kairix.eval.logger import QueryLogger
from kairix.eval.metrics import hit_at_k, mean_reciprocal_rank, ndcg_score
from kairix.eval.reporter import PerformanceReporter
from kairix.eval.schema import QueryLogEntry

__all__ = [
    "PerformanceReporter",
    "QueryLogEntry",
    "QueryLogger",
    "hit_at_k",
    "mean_reciprocal_rank",
    "ndcg_score",
]

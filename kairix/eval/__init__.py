"""kairix.eval — Structured query logging, performance evaluation, and suite generation.

Provides:
  - QueryLogEntry: typed dataclass for structured query log records
  - QueryLogger: writes structured JSONL query logs
  - NDCG, MRR, Hit@K metrics
  - PerformanceReporter: generates per-category NDCG markdown reports
  - JudgeResult, judge_batch: per-document LLM relevance grading (gpt-4o-mini)
  - JudgeCalibrationError: raised when calibration anchor check fails
  - GeneratedQuery, GenerationResult, EnrichmentResult: GPL suite generation
  - generate_suite: generate a benchmark suite from the QMD corpus
  - enrich_suite: convert existing gold_path suite to graded gold_titles
  - MonitorResult, run_monitor: canary regression detection
"""

from kairix.eval.generate import (
    EnrichmentResult,
    GeneratedQuery,
    GenerationResult,
    enrich_suite,
    generate_suite,
)
from kairix.eval.judge import JudgeCalibrationError, JudgeResult, judge_batch
from kairix.eval.logger import QueryLogger
from kairix.eval.metrics import hit_at_k, mean_reciprocal_rank, ndcg_score
from kairix.eval.monitor import MonitorResult, run_monitor
from kairix.eval.reporter import PerformanceReporter
from kairix.eval.schema import QueryLogEntry

__all__ = [
    "EnrichmentResult",
    "GeneratedQuery",
    "GenerationResult",
    "JudgeCalibrationError",
    "JudgeResult",
    "MonitorResult",
    "PerformanceReporter",
    "QueryLogEntry",
    "QueryLogger",
    "enrich_suite",
    "generate_suite",
    "hit_at_k",
    "judge_batch",
    "mean_reciprocal_rank",
    "ndcg_score",
    "run_monitor",
]

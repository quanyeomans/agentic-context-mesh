"""Pluggable scorer registry — turns captured ``QueryRunResult``s into per-metric scores.

P2 of the unified benchmark initiative. The benchmark runner (P3) executes
queries via mode dispatchers and emits one ``QueryRunResult`` per query.
This package converts those into per-scorer verdicts: NDCG, Hit@K, MRR,
LLM-judge, and Latency.

Foundation commit (P2 commit 1) exports the shared types only:

```python
from kairix.quality.scoring import QueryRunResult, Scorer, ScorerResult
```

Subsequent P2 commits add ``NDCGScorer`` / ``HitAtKScorer`` / ``MRRScorer``
/ ``LLMJudgeScorer`` / ``LatencyScorer`` and the ``ScorerRegistry``.
See ``README.md`` for the architectural map. F26-clean: no provider,
no transport imports anywhere in this package.
"""

from kairix.quality.scoring.types import (
    LatencyPhase,
    QueryRunResult,
    Scorer,
    ScorerResult,
)

__all__ = [
    "LatencyPhase",
    "QueryRunResult",
    "Scorer",
    "ScorerResult",
]

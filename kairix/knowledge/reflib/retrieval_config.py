"""
Reference library retrieval baseline.

Derived from hybrid sweep (2026-04-29, 6164 docs, 32K vectors):
  NDCG@10=0.679  Hit@5=0.906  MRR@10=0.720  Weighted=0.687

DO NOT MODIFY — this is the known baseline for the reference library
collection. The reference library has structured filenames and keyword-
rich content, so BM25 is the dominant ranking signal. Vector search
adds ~1% Hit@5 for semantic recall when appended.

To re-derive after search pipeline changes::

    kairix eval hybrid-sweep --suite suites/reflib-gold-v2.yaml \\
        --collection reference-library --quick
"""

from kairix.core.search.config import (
    EntityBoostConfig,
    ProceduralBoostConfig,
    RetrievalConfig,
)

REFLIB_RETRIEVAL_CONFIG = RetrievalConfig(
    fusion_strategy="bm25_primary",
    bm25_limit=20,
    vec_limit=5,
    entity=EntityBoostConfig(enabled=True, factor=0.20, cap=2.0),
    procedural=ProceduralBoostConfig(enabled=True, factor=1.4),
)

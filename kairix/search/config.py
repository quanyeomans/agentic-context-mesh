"""
Retrieval configuration for the kairix search pipeline.

Controls fusion strategy, boost layers, and re-ranking. Each component ships
with defaults tuned via parameter sweep against an independent gold suite
(see ``kairix eval hybrid-sweep``).

**Fusion strategies** (``fusion_strategy`` field):

  ``"bm25_primary"`` (default)
    BM25 results ranked first, vector-only results appended at the bottom.
    Best when BM25 is the stronger ranking signal — structured filenames,
    keyword-rich content, consulting/enterprise knowledge bases.
    Sweep result: 0.803 NDCG@10, 91% Hit@5.

  ``"rrf"``
    Standard Reciprocal Rank Fusion (Cormack et al., 2009). Merges BM25 and
    vector rankings with equal weight. Better for corpora where semantic
    similarity is the primary signal — research papers, unstructured prose,
    multilingual content.

**Choosing a strategy**: Run ``kairix eval hybrid-sweep --suite <your-gold.yaml>``
to evaluate both strategies on your data. If you don't have a gold suite yet,
use ``kairix eval build-gold`` to create one via TREC-style pooling + LLM judge.

As a rule of thumb:

- **Structured knowledge bases** (wikis, runbooks, named entities, Obsidian vaults)
  → ``bm25_primary``. BM25 excels when filenames and headings carry strong signal.
- **Unstructured document collections** (research papers, long-form prose, logs)
  → ``rrf``. Semantic similarity adds value when keyword matching is insufficient.

Use factory class methods for common corpus types, or configure directly via
YAML (``retrieval.fusion_strategy`` in kairix config).
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Valid fusion strategy values
FUSION_STRATEGIES = ("bm25_primary", "rrf")


@dataclass(frozen=True)
class EntityBoostConfig:
    """Configuration for Neo4j entity in-degree boosting."""

    enabled: bool = True
    factor: float = 0.20   # log-scale weight on Neo4j MENTIONS in-degree
    cap: float = 2.0        # max boosted_score / rrf_score ratio


@dataclass(frozen=True)
class ProceduralBoostConfig:
    """Configuration for procedural content path-pattern boosting."""

    enabled: bool = True
    factor: float = 1.4
    path_patterns: tuple[str, ...] = (
        r"(?:^|/)how-to-",
        r"(?:^|/)runbooks?/",
        r"(?:^|/)runbook-",
        r"(?:^|/)procedure",
        r"(?:^|/)sop-",
        r"(?:^|/)guide-",
        r"(?:^|/)playbook-",
    )


@dataclass(frozen=True)
class TemporalBoostConfig:
    """Configuration for temporal boosting strategies."""

    # Date-path boost: boosts docs whose path contains a date matching the query.
    # Enable only for corpora where YYYY-MM-DD.md files are the primary query target.
    date_path_boost_enabled: bool = False
    date_path_boost_factor: float = 1.35
    date_path_recency_window_days: int = 90

    # Chunk-date boost: boosts by chunk_date metadata column proximity (TMP-7B).
    # Enable when chunk_date is populated at index time.
    chunk_date_boost_enabled: bool = False
    chunk_date_decay_halflife_days: int = 30

    # Guard: only apply chunk_date_boost when query contains an explicit temporal
    # marker (ISO date or relative term like "last week"). Prevents generic TEMPORAL
    # intent queries ("what changed and why") from receiving unintended recency bias.
    chunk_date_boost_guard_explicit_only: bool = True


@dataclass(frozen=True)
class RerankConfig:
    """Configuration for cross-encoder re-ranking (post-fusion semantic pass)."""

    enabled: bool = False
    model: str = "cross-encoder/ms-marco-MiniLM-L-6-v2"
    # Number of candidates to pass to the cross-encoder (top-N from fusion output).
    candidate_limit: int = 20


@dataclass(frozen=True)
class RetrievalConfig:
    """
    Top-level configuration for the kairix retrieval pipeline.

    Passed as optional ``config`` parameter to ``hybrid_search()``.

    Use factory class methods for common corpus types:

      - ``RetrievalConfig.defaults()``
        Consulting KB: bm25_primary fusion, entity + procedural boosts.
      - ``RetrievalConfig.minimal()``
        All boosts disabled, bm25_primary fusion. Use to isolate boost impact.
      - ``RetrievalConfig.for_daily_log_corpus()``
        Date-path temporal boost enabled for YYYY-MM-DD.md file corpora.
      - ``RetrievalConfig.for_technical_documentation()``
        Entity off, extended procedural patterns, bm25_primary fusion.
      - ``RetrievalConfig.for_semantic_corpus()``
        RRF fusion for corpora where semantic similarity dominates.

    To find the best config for your data, run::

        kairix eval build-gold --suite your-queries.yaml --output gold.yaml
        kairix eval hybrid-sweep --suite gold.yaml --output sweep.csv
    """

    # Fusion strategy: "bm25_primary" or "rrf".
    # bm25_primary: BM25 results ranked first, vector-only appended at bottom.
    # rrf: standard Reciprocal Rank Fusion with equal BM25/vector weight.
    fusion_strategy: str = "bm25_primary"

    # RRF constant (only used when fusion_strategy="rrf"). Higher values
    # give more weight to documents appearing in both lists.
    rrf_k: int = 60

    entity: EntityBoostConfig = field(default_factory=EntityBoostConfig)
    procedural: ProceduralBoostConfig = field(default_factory=ProceduralBoostConfig)
    temporal: TemporalBoostConfig = field(default_factory=TemporalBoostConfig)
    rerank: RerankConfig = field(default_factory=RerankConfig)

    @classmethod
    def defaults(cls) -> RetrievalConfig:
        """Consulting knowledge base defaults: bm25_primary, entity + procedural boost."""
        return cls(
            temporal=TemporalBoostConfig(chunk_date_boost_enabled=True),
        )

    @classmethod
    def minimal(cls) -> RetrievalConfig:
        """All boosts disabled, bm25_primary fusion. Use to isolate boost impact."""
        return cls(
            entity=EntityBoostConfig(enabled=False),
            procedural=ProceduralBoostConfig(enabled=False),
            temporal=TemporalBoostConfig(),
        )

    @classmethod
    def for_daily_log_corpus(cls) -> RetrievalConfig:
        """Date-named file corpus (journals, meeting logs). Enables date-path boost."""
        return cls(
            temporal=TemporalBoostConfig(date_path_boost_enabled=True),
        )

    @classmethod
    def for_technical_documentation(cls) -> RetrievalConfig:
        """Technical docs corpus. Entity boost off; extended procedural patterns."""
        return cls(
            entity=EntityBoostConfig(enabled=False),
            procedural=ProceduralBoostConfig(
                factor=1.5,
                path_patterns=(
                    r"(?:^|/)how-to-",
                    r"(?:^|/)runbooks?/",
                    r"(?:^|/)runbook-",
                    r"(?:^|/)procedure",
                    r"(?:^|/)sop-",
                    r"(?:^|/)guide-",
                    r"(?:^|/)playbook-",
                    r"(?:^|/)tutorial-",
                    r"/docs?/",
                    r"/reference/",
                ),
            ),
        )

    @classmethod
    def for_semantic_corpus(cls) -> RetrievalConfig:
        """Unstructured/semantic corpus where vector similarity is the primary signal.

        Uses standard RRF fusion. Better for research papers, long-form prose,
        multilingual content, or any corpus where keyword matching is insufficient.
        """
        return cls(
            fusion_strategy="rrf",
            entity=EntityBoostConfig(enabled=False),
        )

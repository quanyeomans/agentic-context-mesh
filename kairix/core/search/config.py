"""
Retrieval configuration for the kairix search pipeline.

Controls fusion strategy, boost layers, and re-ranking. Each component ships
with defaults tuned via parameter sweep against an independent gold suite
(see ``kairix eval hybrid-sweep``).

**Fusion strategies** (``fusion_strategy`` field):

  ``"rrf"`` (default)
    Standard Reciprocal Rank Fusion (Cormack et al., 2009). Merges BM25 and
    vector rankings with equal weight. Sweep-optimised: weighted=0.545,
    NDCG@10=0.564, Hit@5=73.7% on user vault (2026-04-30).

  ``"bm25_primary"``
    BM25 results ranked first, vector-only results appended at the bottom.
    Use when BM25 is the stronger ranking signal — structured filenames,
    keyword-rich content. Generally 15-20% lower than RRF on mixed corpora.

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
from enum import Enum

# F17 — fusion strategy enum value repeated across the FUSION_STRATEGIES tuple
# and three factory defaults; extract so renames hit a single edit site.
_FUSION_BM25_PRIMARY = "bm25_primary"

# Valid fusion strategy values
FUSION_STRATEGIES = (_FUSION_BM25_PRIMARY, "rrf")


@dataclass(frozen=True)
class EntityBoostConfig:
    """Configuration for Neo4j entity in-degree boosting."""

    enabled: bool = True
    factor: float = 0.20  # log-scale weight on Neo4j MENTIONS in-degree
    cap: float = 2.0  # max boosted_score / rrf_score ratio
    # Issue #456 — when intent_confidence_gated_boosts flag is ON, this
    # boost only fires when (intent == ENTITY) AND
    # (intent_confidence >= min_intent_confidence). 0.5 is the
    # recommended default: a clear margin between the primary's match
    # count and the runner-up. When the flag is OFF, this field is unused.
    min_intent_confidence: float = 0.5


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
    # Issue #456 — confidence-gated minimum. See EntityBoostConfig.
    min_intent_confidence: float = 0.5


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

    # Issue #430 — date-aware boost for temporal queries.
    #
    # When a TEMPORAL query has a parseable date (query_date is set) and
    # chunk_date_boost is enabled, undated chunks (no chunk_date metadata)
    # are penalised by ``undated_chunk_penalty``. Default 0.1 = x10
    # demotion. The reproduction in #430 was 'recent memory issue fixes
    # Kairix OpenClaw Gateway Shape June 2026' returning SharePoint
    # SVG/XML reference fragments above dated agent-memory notes —
    # exactly the undated-vs-dated drag the penalty is designed to fix.
    #
    # Disabled by default (``undated_chunk_penalty_enabled=False``) so
    # pre-#430 behaviour is preserved byte-for-byte. Operators enable in
    # ``kairix.config.yaml`` after confirming the corpus has chunk_date
    # populated on the canonical sources (otherwise everything gets
    # penalised and search returns nothing useful).
    undated_chunk_penalty_enabled: bool = False
    undated_chunk_penalty: float = 0.1

    # Issue #456 — confidence-gated minimum. See EntityBoostConfig.
    min_intent_confidence: float = 0.5


class SourceTier(str, Enum):
    """Source-tier classification for chunks (Issue #432).

    The chunk-store + collection config carries a tier; the
    :class:`SourceTierBoost` strategy multiplies each result's
    ``boosted_score`` by the tier's configured multiplier. Operators
    declare per-collection tier in ``kairix.config.yaml`` (defaults to
    ``vault_active`` when absent — preserves pre-#432 behaviour).
    """

    CANONICAL = "canonical"
    ACTIVE_STANDARD = "active_standard"
    VAULT_ACTIVE = "vault_active"
    REFERENCE = "reference"
    ARCHIVED = "archived"


@dataclass(frozen=True)
class SourceTierBoostConfig:
    """Configuration for source-tier-aware ranking (Issue #432).

    The multipliers reweight per-result ``boosted_score`` based on the
    chunk's source tier (resolved via the result's collection name +
    the operator's per-collection tier map). Defaults match the EPIC
    #438 design:

      - canonical: x3.0 (the team's declared canon — ETHOS/AGENTS/SOUL,
        agent-knowledge decisions/rules/facts/patterns, platform standards)
      - active_standard: x2.0 (operational delivery docs + active ADRs)
      - vault_active: x1.0 (baseline — non-archived vault content)
      - reference: x0.6 (external reference-library content)
      - archived: x0.2 (vault archive + superseded ADRs — present but
        outranked by every other tier)

    Disabled by default (``enabled=False``) so existing deployments see
    byte-for-byte pre-#432 ranking. Operators flip ``enabled=True`` in
    ``kairix.config.yaml`` after declaring tier assignments per
    collection. F47-clean — the boost reads collection→tier from the
    pipeline context; no chunk-store schema change required for MVP.
    """

    enabled: bool = False
    # Stored as a tuple-of-pairs (not a dict) because RetrievalConfig is a
    # frozen dataclass + used as a process-lifetime cache key in
    # kairix.core.factory.build_search_pipeline — must be hashable. The
    # :meth:`multipliers_map` helper materialises the dict view that
    # :class:`SourceTierBoost` reads.
    multipliers: tuple[tuple[SourceTier, float], ...] = (
        (SourceTier.CANONICAL, 3.0),
        (SourceTier.ACTIVE_STANDARD, 2.0),
        (SourceTier.VAULT_ACTIVE, 1.0),
        (SourceTier.REFERENCE, 0.6),
        (SourceTier.ARCHIVED, 0.2),
    )
    # Default tier when a result's collection has no configured tier.
    # vault_active = x1.0 → preserves the pre-tier ranking for
    # collections the operator hasn't yet classified.
    default_tier: SourceTier = SourceTier.VAULT_ACTIVE
    # Issue #456 — confidence-gated minimum. See EntityBoostConfig.
    # SourceTierBoost is currently intent-agnostic (fires for every
    # query) so this field is a placeholder; future per-query-class
    # overrides (per the EPIC's section 4) will consume it.
    min_intent_confidence: float = 0.0

    def multipliers_map(self) -> dict[SourceTier, float]:
        """Materialise the tuple-of-pairs ``multipliers`` field into a
        dict for per-result lookup. Called once at boost construction
        time (or per-call — the cost is microscopic for a 5-entry tuple)."""
        return dict(self.multipliers)


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

    # Configured provider plugin name (matches a key under the
    # ``kairix.providers`` entry-point group). The plugin owns its own
    # credential-retrieval pattern (Azure → Key Vault; AWS → Secrets
    # Manager; etc.) so this field selects which one is loaded. ``None``
    # means "no provider configured" — callers that depend on a provider
    # (``ProviderEmbeddingService`` construction in
    # ``kairix.core.factory``) surface a typed error with the list of
    # installed plugins. Lives on ``RetrievalConfig`` because the
    # configured plugin is part of the retrieval pipeline's identity:
    # rotating providers should bust the per-config memoisation in
    # ``build_search_pipeline``.
    provider: str | None = None

    # Fusion strategy: "bm25_primary" or "rrf".
    # bm25_primary: BM25 results ranked first, vector-only appended at bottom.
    # rrf: standard Reciprocal Rank Fusion with equal BM25/vector weight.
    fusion_strategy: str = _FUSION_BM25_PRIMARY

    # RRF constant (only used when fusion_strategy="rrf"). Higher values
    # give more weight to documents appearing in both lists.
    rrf_k: int = 60

    # Result limits — controls how many candidates each backend returns before fusion.
    bm25_limit: int = 20
    vec_limit: int = 20

    # Skip vector search entirely. Use for BM25-only baseline evaluation
    # or when the vector index is unavailable.
    skip_vector: bool = False

    entity: EntityBoostConfig = field(default_factory=EntityBoostConfig)
    procedural: ProceduralBoostConfig = field(default_factory=ProceduralBoostConfig)
    temporal: TemporalBoostConfig = field(default_factory=TemporalBoostConfig)
    rerank: RerankConfig = field(default_factory=RerankConfig)

    # Issue #432 — source-tier ranking. Disabled by default; enable via
    # ``source_tier_boost: enabled: true`` in kairix.config.yaml after
    # declaring per-collection ``tier:`` assignments.
    source_tier_boost: SourceTierBoostConfig = field(default_factory=SourceTierBoostConfig)

    # Intent types that always receive cross-encoder re-ranking, even when
    # rerank.enabled is False.  Users can force rerank for *all* intents by
    # setting rerank.enabled = true in their config.
    rerank_intents: tuple[str, ...] = ("multi_hop", "semantic")

    @classmethod
    def defaults(cls) -> RetrievalConfig:
        """Sweep-optimised defaults: RRF fusion, boosts disabled, vec_limit=10.

        Derived from hybrid-sweep on user vault (v2-real-world-enriched, 350 cases,
        2026-04-30). RRF k=60 with minimal boosts scored weighted=0.545, NDCG=0.564,
        Hit@5=73.7% — outperforming bm25_primary and boost-enabled configs.
        """
        return cls(
            fusion_strategy="rrf",
            rrf_k=60,
            vec_limit=10,
            entity=EntityBoostConfig(enabled=False),
            procedural=ProceduralBoostConfig(enabled=False),
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


# ---------------------------------------------------------------------------
# Reference library retrieval baseline
# ---------------------------------------------------------------------------
#
# Derived from hybrid sweep (2026-04-29, 6164 docs, 32K vectors):
#   NDCG@10=0.679  Hit@5=0.906  MRR@10=0.720  Weighted=0.687
#
# DO NOT MODIFY — this is the known baseline for the reference library
# collection. To re-derive after search pipeline changes:
#
#     kairix eval hybrid-sweep --suite suites/reflib-gold-v2.yaml \
#         --collection reference-library --quick

REFLIB_RETRIEVAL_CONFIG = RetrievalConfig(
    fusion_strategy=_FUSION_BM25_PRIMARY,
    bm25_limit=20,
    vec_limit=5,
    entity=EntityBoostConfig(enabled=True, factor=0.20, cap=2.0),
    procedural=ProceduralBoostConfig(enabled=True, factor=1.4),
    rerank_intents=(),  # Reranking disabled — BM25-primary already ranks well for this corpus
)

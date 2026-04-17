"""
Retrieval boost configuration for the kairix search pipeline.

Each boost ships with opt-in defaults appropriate for a consulting knowledge base
(entity + procedural enabled, temporal disabled). Use RetrievalConfig.minimal()
to disable all boosts for baseline isolation testing.
"""

from __future__ import annotations

from dataclasses import dataclass, field


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


@dataclass(frozen=True)
class RetrievalConfig:
    """
    Configuration for the kairix retrieval pipeline boosts.

    Passed as optional ``config`` parameter to ``hybrid_search()``.
    All boost sub-configs default to consulting knowledge base settings.

    Use factory class methods for common corpus types:
      - ``RetrievalConfig.defaults()``      — consulting KB (entity + procedural, no temporal)
      - ``RetrievalConfig.minimal()``       — all boosts off; RRF baseline
      - ``RetrievalConfig.for_daily_log_corpus()``  — date-path temporal boost enabled
      - ``RetrievalConfig.for_technical_documentation()``  — entity off, extended procedural
    """

    entity: EntityBoostConfig = field(default_factory=EntityBoostConfig)
    procedural: ProceduralBoostConfig = field(default_factory=ProceduralBoostConfig)
    temporal: TemporalBoostConfig = field(default_factory=TemporalBoostConfig)

    @classmethod
    def defaults(cls) -> RetrievalConfig:
        """Consulting knowledge base defaults: entity + procedural boost, chunk_date temporal boost on."""
        return cls(
            temporal=TemporalBoostConfig(chunk_date_boost_enabled=True),
        )

    @classmethod
    def minimal(cls) -> RetrievalConfig:
        """All boosts disabled. RRF baseline only. Use to isolate boost impact."""
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

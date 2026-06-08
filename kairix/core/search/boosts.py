"""
Strategy pattern implementations for post-fusion boosting.

Wraps existing boost functions from kairix.core.search.rrf as BoostStrategy
protocol implementations. No logic duplication — delegates to the existing
functions.
"""

from __future__ import annotations

from collections.abc import Callable

from kairix.core.protocols import GraphRepository
from kairix.core.search.config import (
    EntityBoostConfig,
    ProceduralBoostConfig,
    TemporalBoostConfig,
)
from kairix.core.search.intent import QueryIntent


def default_intent_confidence_flag_reader() -> bool:
    """Production flag-reader — delegates to :func:`kairix.core.features.resolver.flag`.

    Pulled into a thin wrapper so tests can inject a fake reader via the
    ``flag_reader`` kwarg on :func:`intent_confidence_passes` without
    monkey-patching the resolver module (F1/F2-clean).
    """
    from kairix.core.features.resolver import flag

    return flag("intent_confidence_gated_boosts")


# Callable signature for the flag_reader DI seam — a zero-arg returning bool.
IntentConfidenceFlagReader = Callable[[], bool]


def intent_confidence_passes(
    context: dict,
    expected: QueryIntent,
    min_confidence: float,
    *,
    flag_reader: IntentConfidenceFlagReader = default_intent_confidence_flag_reader,
) -> bool:
    """Return True iff the boost should fire based on intent + confidence.

    Three-way decision (Issue #456):

      1. ``context["intent"]`` must equal ``expected`` (legacy gate).
      2. If the ``intent_confidence_gated_boosts`` feature flag is OFF,
         step 1 alone decides — confidence is ignored (byte-for-byte
         parity with pre-#456 behaviour).
      3. If the flag is ON, ``context["intent_confidence"]`` (defaulting
         to ``1.0`` when absent so legacy callers that haven't been
         updated to populate it still see boosts fire) must be ≥
         ``min_confidence``.

    Public surface (no underscore prefix) so tests can drive it directly
    (F5-clean). ``flag_reader`` is a DI seam — production callers leave
    it at the default; tests pass a fake to drive both flag states
    without touching the resolver cache.
    """
    if context.get("intent") != expected:
        return False

    if not flag_reader():
        return True

    confidence = float(context.get("intent_confidence", 1.0))
    return confidence >= min_confidence


class EntityBoost:
    """Boost results based on Neo4j entity mention in-degree.

    Gated to ENTITY intent — non-ENTITY queries pass through unchanged.
    Requires a GraphRepository for entity lookup. Documents matching entity
    vault paths receive a log-scaled boost proportional to in-degree.
    """

    def __init__(
        self,
        graph: GraphRepository,
        config: EntityBoostConfig | None = None,
    ) -> None:
        self._graph = graph
        self._config = config

    def boost(self, results: list, _query: str, context: dict) -> list:
        """Apply entity in-degree boost when context.intent == ENTITY.

        ``_query`` is part of the ``BoostStrategy`` Protocol signature but
        unused by this strategy — the boost is purely structural (Neo4j
        graph in-degree), not query-text dependent.
        """
        min_confidence = (
            self._config.min_intent_confidence
            if self._config is not None
            else EntityBoostConfig().min_intent_confidence
        )
        if not intent_confidence_passes(context, QueryIntent.ENTITY, min_confidence):
            return results
        from kairix.core.search.rrf import entity_boost_neo4j

        return entity_boost_neo4j(results, self._graph, config=self._config)


class ProceduralBoost:
    """Boost procedural content (how-to guides, runbooks) by path pattern.

    Gated to PROCEDURAL intent — non-PROCEDURAL queries pass through unchanged.
    Multiplies boosted_score by config.factor for documents whose path matches
    procedural patterns.
    """

    def __init__(self, config: ProceduralBoostConfig | None = None) -> None:
        self._config = config

    def boost(self, results: list, _query: str, context: dict) -> list:
        """Boost procedural-shaped paths when context.intent == PROCEDURAL.

        ``_query`` is part of the ``BoostStrategy`` Protocol signature but
        unused — the procedural boost is path-pattern based, not query-text
        dependent.
        """
        min_confidence = (
            self._config.min_intent_confidence
            if self._config is not None
            else ProceduralBoostConfig().min_intent_confidence
        )
        if not intent_confidence_passes(context, QueryIntent.PROCEDURAL, min_confidence):
            return results
        from kairix.core.search.rrf import procedural_boost

        return procedural_boost(results, config=self._config)


class TemporalDateBoost:
    """Boost documents whose path contains a date matching the query.

    Gated to TEMPORAL intent — non-TEMPORAL queries pass through unchanged.
    Boosts documents with explicit date strings or recent dates for relative
    temporal terms.
    """

    def __init__(self, config: TemporalBoostConfig | None = None) -> None:
        self._config = config

    def boost(self, results: list, query: str, context: dict) -> list:
        min_confidence = (
            self._config.min_intent_confidence
            if self._config is not None
            else TemporalBoostConfig().min_intent_confidence
        )
        if not intent_confidence_passes(context, QueryIntent.TEMPORAL, min_confidence):
            return results
        from kairix.core.search.rrf import temporal_date_boost

        return temporal_date_boost(results, query, config=self._config)


class ChunkDateBoost:
    """Boost documents by chunk_date metadata proximity to query date.

    Gated to TEMPORAL intent — non-TEMPORAL queries pass through unchanged.
    Uses Gaussian decay based on the distance between chunk_date and the
    query date. Requires chunk_date to be populated at index time.
    """

    def __init__(self, config: TemporalBoostConfig | None = None) -> None:
        self._config = config

    def boost(self, results: list, _query: str, context: dict) -> list:
        """Apply chunk_date proximity boost when intent == TEMPORAL.

        ``_query`` is part of the ``BoostStrategy`` Protocol signature; the
        actual proximity input is ``context["query_date"]``, not the raw
        query string.
        """
        min_confidence = (
            self._config.min_intent_confidence
            if self._config is not None
            else TemporalBoostConfig().min_intent_confidence
        )
        if not intent_confidence_passes(context, QueryIntent.TEMPORAL, min_confidence):
            return results
        from kairix.core.search.rrf import chunk_date_boost

        query_date = context.get("query_date")
        return chunk_date_boost(results, query_date, config=self._config)

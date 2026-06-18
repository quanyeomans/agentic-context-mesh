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
    ContentQualityBoostConfig,
    EntityBoostConfig,
    EntityFirstRoutingConfig,
    ProceduralBoostConfig,
    SourceTier,
    SourceTierBoostConfig,
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


class SourceTierBoost:
    """Multiply boosted_score by a per-tier multiplier (Issue #432).

    Reads each result's collection name, looks up the configured tier
    (via ``tier_map``), then applies ``config.multipliers[tier]``. Results
    whose collection has no tier mapping fall back to
    ``config.default_tier`` (``vault_active``, x1.0 — preserves
    pre-#432 ranking).

    Intent-agnostic: fires for every query when ``config.enabled`` is
    True. Per-query-class overrides (per EPIC #438 §4) layer on later.

    Construction:
      - ``tier_map``: ``dict[collection_name, tier_name]`` — derived
        from the operator's ``collections.shared[].tier`` declarations
        in ``kairix.config.yaml``. The factory builds this once at
        pipeline-construction time.
      - ``config``: SourceTierBoostConfig with the multiplier table.

    When ``config.enabled`` is False (the default), the strategy
    short-circuits and returns ``results`` unchanged — preserves the
    pre-#432 ranking even when the strategy is wired into the chain.
    """

    def __init__(
        self,
        tier_map: dict[str, str] | None = None,
        config: SourceTierBoostConfig | None = None,
    ) -> None:
        self._tier_map = tier_map or {}
        self._config = config or SourceTierBoostConfig()

    def _resolve_tier_for_path(self, path: str, collection: str) -> SourceTier:
        """Resolve the effective :class:`SourceTier` for one result row.

        Order of precedence (#432 follow-up):

          1. **Canonical-filename allowlist** — if ``path`` matches any
             entry in ``config.canonical_filename_allowlist`` (via
             ``str.endswith``), the row is treated as
             :attr:`SourceTier.CANONICAL` regardless of its collection.
          2. **Collection tier mapping** — ``tier_map[collection]``
             from the operator's ``kairix.config.yaml``.
          3. **Default tier** — ``config.default_tier`` (vault_active)
             when no mapping exists.

        Unknown tier-name strings (config typo, schema drift) fall
        back to ``default_tier`` so no result is silently zeroed.
        """
        if any(path.endswith(suffix) for suffix in self._config.canonical_filename_allowlist):
            return SourceTier.CANONICAL
        tier_name = self._tier_map.get(collection, self._config.default_tier.value)
        try:
            return SourceTier(tier_name)
        except ValueError:
            return self._config.default_tier

    def _multiplier_for(
        self,
        tier: SourceTier,
        *,
        intent_name: str,
        base_multipliers: dict[SourceTier, float],
        per_intent_overrides: dict[str, dict[SourceTier, float]],
    ) -> float:
        """Resolve the multiplier for ``tier`` honouring per-intent overrides.

        Per-intent override wins when both:
          * the current query's intent matches one of the operator's
            declared override keys, AND
          * the tier appears in that intent's override table.

        Otherwise falls back to the base multipliers table. Missing
        tiers everywhere → multiplier ``1.0`` (no boost, no penalty).
        """
        intent_overrides = per_intent_overrides.get(intent_name, {})
        if tier in intent_overrides:
            return intent_overrides[tier]
        return base_multipliers.get(tier, 1.0)

    # NOSONAR S3516 — BoostStrategy contract: mutate ``results[i].boosted_score``
    # in place and return the same list; same-reference return is the protocol
    # contract every other boost strategy honours, not a bug.
    def boost(self, results: list, _query: str, context: dict) -> list:  # NOSONAR S3516
        """Apply tier multipliers; returns the input list with mutated
        ``boosted_score`` values per result. Order is NOT re-sorted here
        — the budget stage that follows sorts by ``boosted_score`` so
        the multipliers take effect there.

        Failure-isolated: any per-result exception logs at WARNING and
        leaves that result's score unchanged; the strategy itself never
        raises.

        ``context`` carries the query's intent so per-intent multiplier
        overrides can fire — ``context.get("intent")`` returns a
        :class:`QueryIntent` whose ``.value`` is matched against the
        operator-declared override keys.
        """
        if not self._config.enabled:
            return results

        intent = context.get("intent")
        intent_name = getattr(intent, "value", "") if intent is not None else ""
        base_multipliers = self._config.multipliers_map()
        per_intent_overrides = self._config.per_intent_overrides_map()
        for r in results:
            try:
                path = getattr(r, "path", "") or ""
                collection = getattr(r, "collection", "") or ""
                tier = self._resolve_tier_for_path(path, collection)
                multiplier = self._multiplier_for(
                    tier,
                    intent_name=intent_name,
                    base_multipliers=base_multipliers,
                    per_intent_overrides=per_intent_overrides,
                )
                # ``boosted_score`` was initialised from rrf_score by
                # _rrf_impl; subsequent boosts mutate it. We multiply
                # in-place so any prior boost (entity / procedural /
                # temporal) is preserved.
                r.boosted_score = float(r.boosted_score) * multiplier
            except Exception as exc:
                # Don't break the result list on a single odd row; log so
                # an operator can triage why a particular row's tier
                # lookup blew up.
                import logging

                logging.getLogger(__name__).warning(
                    "SourceTierBoost: per-result tier lookup failed for %s — %s",
                    getattr(r, "path", "?"),
                    exc,
                )
                continue
        return results


# ---------------------------------------------------------------------------
# EntityFirstRoutingBoost (Issue #429) — route entity-summaries first
# ---------------------------------------------------------------------------


def default_entity_first_routing_flag_reader() -> bool:
    """Production flag-reader for ``entity_first_routing_enabled`` (#429).

    Thin wrapper around :func:`kairix.core.features.resolver.flag` so
    tests inject a fake reader via the ``flag_reader`` kwarg on
    :class:`EntityFirstRoutingBoost` without monkey-patching the resolver
    module (F1/F2-clean).
    """
    from kairix.core.features.resolver import flag

    return flag("entity_first_routing_enabled")


class EntityFirstRoutingBoost:
    """Route entity-summaries first for ENTITY-intent queries (#429 Phase 2b).

    The ADR-036 projector writes each entity's Wikidata summary into the
    synthetic ``entity-summaries`` collection (tier ``reference``, x0.6) —
    so by default those summaries are *de*-prioritised. For an ENTITY-intent
    query ("tell me about X" / "who is X") that is exactly backwards: the
    operator is asking *about the entity*, so its summary should lead.

    This boost multiplies the ``boosted_score`` of entity-summary rows by
    ``config.factor``. A row is an entity-summary when its ``collection``
    matches ``config.collection`` OR its ``path`` carries the well-known
    ``entity://`` source-URI prefix the projector writes (the same marker
    the CLI ``[Wikidata]`` badge + MCP ``entity_summary`` envelope flag key
    off). Registered LAST in the chain so the multiplier composes on top of
    :class:`SourceTierBoost`'s tier de-boost.

    Two gates, both must pass before any score is touched:

      1. **Feature flag** — ``entity_first_routing_enabled`` read live via
         ``flag_reader``. OFF (the default) ⇒ structural no-op, pre-#429
         ranking preserved byte-for-byte. This is the cutover control.
      2. **Intent** — ``context["intent"] == QueryIntent.ENTITY`` (with the
         #456 confidence gate when ``intent_confidence_gated_boosts`` is
         ON), via :func:`intent_confidence_passes`.

    Failure-isolated: any per-result exception logs at WARNING and leaves
    that row's score unchanged; the strategy itself never raises.
    """

    _ENTITY_URI_PREFIX = "entity://"

    def __init__(
        self,
        config: EntityFirstRoutingConfig | None = None,
        *,
        flag_reader: IntentConfidenceFlagReader = default_entity_first_routing_flag_reader,
    ) -> None:
        self._config = config or EntityFirstRoutingConfig()
        self._flag_reader = flag_reader

    def _is_entity_summary(self, result: object) -> bool:
        """True when ``result`` is an entity-summary row.

        Checks the well-known ``entity://`` source-URI prefix first (the
        marker the projector writes, and the one the CLI ``[Wikidata]``
        badge + MCP envelope key off), then falls back to the collection
        label. Both ``getattr`` defaults are ``""``: ``==`` is None-safe
        and ``isinstance`` guards ``startswith``, so no ``or ""`` is
        needed.
        """
        path = getattr(result, "path", "")
        if isinstance(path, str) and path.startswith(self._ENTITY_URI_PREFIX):
            return True
        return getattr(result, "collection", "") == self._config.collection

    @staticmethod
    def _score_key(result: object) -> float:
        """Sort key — defensive so one unreadable score can't break the sort.

        Catches broadly (matching the per-row boost guards): a row whose
        ``boosted_score`` is missing, non-numeric, or raises on access
        sorts to the bottom rather than aborting the whole boost.
        """
        try:
            return float(getattr(result, "boosted_score", 0.0))
        except Exception:
            return 0.0

    def boost(self, results: list, _query: str, context: dict) -> list:
        """Multiply entity-summary scores by ``factor`` when flag ON + ENTITY intent.

        Re-sorts by ``boosted_score`` descending when it fires (like the
        ``rrf`` boost functions) so the routed entity-summary actually
        leads the budget stage — ``apply_budget`` consumes in order without
        re-sorting. When either gate fails the input list is returned
        unchanged (no mutation, no re-sort) — pre-#429 ranking preserved.

        ``_query`` is part of the ``BoostStrategy`` Protocol signature but
        unused — routing is structural (collection / source-URI), not
        query-text dependent.
        """
        if not self._flag_reader():
            return results
        if not intent_confidence_passes(context, QueryIntent.ENTITY, self._config.min_intent_confidence):
            return results
        factor = self._config.factor
        for r in results:
            try:
                if self._is_entity_summary(r):
                    r.boosted_score = float(r.boosted_score) * factor
            except Exception as exc:
                import logging

                logging.getLogger(__name__).warning(
                    "EntityFirstRoutingBoost: per-result boost failed for %s — %s",
                    getattr(r, "path", "?"),
                    exc,
                )
                continue
        return sorted(results, key=self._score_key, reverse=True)


# ---------------------------------------------------------------------------
# ContentQualityBoost (Issue #458) — enrichment-derived content authority
# ---------------------------------------------------------------------------


def length_signal(content_length: int, config: ContentQualityBoostConfig) -> float:
    """Sigmoid signal in ``[length_stub_floor, length_substantive_ceiling]``.

    Stubs (very short snippets) get the floor, substantive content gets the
    ceiling, midpoint is the sigmoid centre. Bounded so the signal can
    never zero-out or runaway-multiply a result.
    """
    import math

    midpoint = config.length_sigmoid_midpoint_chars
    scale = max(config.length_sigmoid_scale_chars, 1)
    z = (content_length - midpoint) / scale
    s = 1.0 / (1.0 + math.exp(-z))
    span = config.length_substantive_ceiling - config.length_stub_floor
    return config.length_stub_floor + span * s


def _count_headings(snippet: str) -> int:
    """Count markdown headings (lines starting with ``#``) in a snippet.

    Cheap proxy for authoring effort — a heavily structured doc has more
    headings than a stream-of-consciousness note. Robust to leading
    whitespace; bounded by snippet length so worst-case is linear in
    snippet size.
    """
    if not snippet:
        return 0
    count = 0
    for line in snippet.splitlines():
        stripped = line.lstrip()
        if stripped.startswith("#"):
            count += 1
    return count


def structure_signal(heading_count: int, config: ContentQualityBoostConfig) -> float:
    """Log-scaled signal in ``[1.0, structure_ceiling]``.

    0 headings → 1.0 (neutral). ~5+ headings → ``structure_ceiling``.
    Log scale so the gap between 0 and 1 heading is bigger than between
    8 and 9 (avoids runaway boost for over-structured docs).
    """
    import math

    if heading_count <= 0:
        return 1.0
    saturation = max(config.structure_log_scale * 5.0, 1.0)
    s = math.log(1.0 + heading_count) / math.log(1.0 + saturation)
    s = min(s, 1.0)
    return 1.0 + (config.structure_ceiling - 1.0) * s


def recency_signal(chunk_date: str, config: ContentQualityBoostConfig) -> float:
    """Decay signal in ``[recency_floor, recency_neutral]``.

    Empty/unparseable chunk_date → ``recency_neutral`` (we don't penalise
    just for missing metadata — that's intent-gated #430's job).
    Recent (< 1 halflife old) → ``recency_neutral``. Very old → ``recency_floor``.

    Halflife semantics: at ``recency_decay_halflife_days`` past today the
    signal is exactly halfway between neutral and floor.
    """
    import math
    from datetime import date

    if not chunk_date:
        return config.recency_neutral

    try:
        parsed = date.fromisoformat(chunk_date[:10])
    except (ValueError, TypeError):
        return config.recency_neutral

    today = date.today()
    age_days = max((today - parsed).days, 0)
    halflife = max(config.recency_decay_halflife_days, 1)
    decay = math.exp(-math.log(2) * (age_days / halflife))
    span = config.recency_neutral - config.recency_floor
    return config.recency_floor + span * decay


class ContentQualityBoost:
    """Enrichment-derived content-authority boost (Issue #458).

    Multiplies ``boosted_score`` by three orthogonal signals derived from
    content alone (no Neo4j / no external state):

    * ``length_signal`` — content length sigmoid
    * ``structure_signal`` — markdown heading count
    * ``recency_signal`` — chunk_date age decay

    Combined multiplier range: ``[~0.56, ~1.56]``. Composes with
    :class:`SourceTierBoost` (operator-declared authority) multiplicatively.

    Intent-agnostic — fires for every query when ``config.enabled`` is
    True. Failure-isolated per result so a single odd row never breaks the
    list.
    """

    def __init__(self, config: ContentQualityBoostConfig | None = None) -> None:
        self._config = config or ContentQualityBoostConfig()

    # NOSONAR S3516 — BoostStrategy contract: mutate ``results[i].boosted_score``
    # in place and return the same list; same-reference return is the protocol
    # contract every other boost strategy honours, not a bug.
    def boost(self, results: list, _query: str, _context: dict) -> list:  # NOSONAR S3516
        if not self._config.enabled:
            return results

        for r in results:
            try:
                snippet = getattr(r, "snippet", "") or ""
                chunk_date = getattr(r, "chunk_date", "") or ""

                length_m = length_signal(len(snippet), self._config)
                structure_m = structure_signal(_count_headings(snippet), self._config)
                recency_m = recency_signal(chunk_date, self._config)

                multiplier = length_m * structure_m * recency_m
                r.boosted_score = float(r.boosted_score) * multiplier
            except Exception as exc:
                import logging

                logging.getLogger(__name__).warning(
                    "ContentQualityBoost: per-result signal computation failed for %s — %s",
                    getattr(r, "path", "?"),
                    exc,
                )
                continue
        return results

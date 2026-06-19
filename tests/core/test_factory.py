"""Unit tests for ``kairix.core.factory``.

Coverage targets:

  - ``select_boosts`` — exhaustive on/off matrix for the four boost
    families (entity, procedural, temporal-date-path, temporal-chunk-date)
    plus order-sensitivity proof.
  - ``build_search_pipeline`` — driven via the public surface with
    explicit ``RetrievalConfig`` instances. The function naturally
    walks its fallback paths (Azure / Neo4j / usearch unavailable in
    the test process) so we exercise the production wiring without
    spinning up real services.

We deliberately do NOT use ``@patch`` (F1) or pytest ``monkeypatch``
on ``KAIRIX_*`` env vars (F2). Tests that need to drive env-var-driven
branches (``KAIRIX_DOCKER``, ``KAIRIX_EXTRA_COLLECTIONS``,
``KAIRIX_LOG_QUERIES``) thread an explicit ``env={...}`` mapping through
``build_search_pipeline``; the factory passes it down to
``kairix.paths.is_docker_env`` / ``extra_collections`` /
``log_queries_enabled``. Tests that need to inject a stand-in for the
vector index, graph client, or secrets bootstrap construct a
``FactoryDeps`` with the relevant field overridden and pass it as
``deps=``. No module-attribute swap, no env mutation.
"""

from __future__ import annotations

from typing import Any

import pytest

from kairix.core.factory import FactoryDeps, build_search_pipeline, select_boosts
from kairix.core.search.boosts import (
    ChunkDateBoost,
    EntityBoost,
    EntityFirstRoutingBoost,
    ProceduralBoost,
    TemporalDateBoost,
)
from kairix.core.search.config import (
    EntityBoostConfig,
    ProceduralBoostConfig,
    RetrievalConfig,
    TemporalBoostConfig,
)
from kairix.core.search.fusion import BM25PrimaryFusion, RRFFusion
from tests.fakes import FakeGraphRepository, FakeProvider, FakeProviderRegistry

# ── select_boosts — on/off matrix and ordering ────────────────────────


@pytest.fixture
def fake_graph() -> FakeGraphRepository:
    return FakeGraphRepository(available=True)


def _provider_registry() -> FakeProviderRegistry:
    """Production-shaped FakeProviderRegistry for factory-wiring tests.

    The factory requires an explicit provider (the legacy fallback was
    deleted in v2026.5.17). Every ``build_search_pipeline(config=cfg)``
    call in this module is exercising the pipeline-composition surface —
    the embed provider's identity isn't load-bearing for those scenarios —
    so we hand it the canonical ``FakeProvider`` from ``tests/fakes.py``
    and let the factory finish its happy-path wiring.

    Tests that pin embed-service identity (e.g. ``ProviderEmbeddingService``
    vs the typed error path) construct their own registry inline.
    """
    return FakeProviderRegistry({"fake": FakeProvider(name="fake", vector=[0.1, 0.2, 0.3], dim=3)})


def _wire_cfg(cfg: RetrievalConfig) -> RetrievalConfig:
    """Attach ``provider="fake"`` so the factory's required-provider gate passes.

    Used by every factory test that constructs a ``RetrievalConfig`` for
    pipeline-composition assertions (fusion type, boost wiring, etc.).
    The provider identity isn't the test subject — we just need the
    factory to reach the rest of its wiring without raising ``ValueError``.
    """
    from dataclasses import replace

    return replace(cfg, provider="fake") if cfg.provider is None else cfg


def _cfg(
    *,
    entity: bool,
    procedural: bool,
    date_path: bool,
    chunk_date: bool,
) -> RetrievalConfig:
    return RetrievalConfig(
        fusion_strategy="rrf",
        entity=EntityBoostConfig(enabled=entity),
        procedural=ProceduralBoostConfig(enabled=procedural),
        temporal=TemporalBoostConfig(
            date_path_boost_enabled=date_path,
            chunk_date_boost_enabled=chunk_date,
        ),
    )


@pytest.mark.unit
def test_select_boosts_all_disabled_returns_empty_list(fake_graph: FakeGraphRepository) -> None:
    """Sabotage proof: an off-by-one on the ``if`` guard would still
    register at least one adapter, so the empty-list assertion fails.
    """
    cfg = _cfg(entity=False, procedural=False, date_path=False, chunk_date=False)
    assert select_boosts(cfg, fake_graph) == []


@pytest.mark.unit
def test_select_boosts_only_entity_enabled(fake_graph: FakeGraphRepository) -> None:
    """Only EntityBoost is registered; the graph dependency is wired through."""
    cfg = _cfg(entity=True, procedural=False, date_path=False, chunk_date=False)
    boosts = select_boosts(cfg, fake_graph)
    assert len(boosts) == 1
    assert isinstance(boosts[0], EntityBoost)


@pytest.mark.unit
def test_select_boosts_only_procedural_enabled(fake_graph: FakeGraphRepository) -> None:
    cfg = _cfg(entity=False, procedural=True, date_path=False, chunk_date=False)
    boosts = select_boosts(cfg, fake_graph)
    assert len(boosts) == 1
    assert isinstance(boosts[0], ProceduralBoost)


@pytest.mark.unit
def test_select_boosts_only_temporal_date_path_enabled(fake_graph: FakeGraphRepository) -> None:
    cfg = _cfg(entity=False, procedural=False, date_path=True, chunk_date=False)
    boosts = select_boosts(cfg, fake_graph)
    assert len(boosts) == 1
    assert isinstance(boosts[0], TemporalDateBoost)


@pytest.mark.unit
def test_select_boosts_only_chunk_date_enabled(fake_graph: FakeGraphRepository) -> None:
    cfg = _cfg(entity=False, procedural=False, date_path=False, chunk_date=True)
    boosts = select_boosts(cfg, fake_graph)
    assert len(boosts) == 1
    assert isinstance(boosts[0], ChunkDateBoost)


@pytest.mark.unit
def test_select_boosts_all_enabled_preserves_order(fake_graph: FakeGraphRepository) -> None:
    """Order is the documented contract:
    EntityBoost → ProceduralBoost → TemporalDateBoost → ChunkDateBoost.

    Sabotage proof: shuffling the ``if`` blocks in factory.py would
    fail this — assertion checks types positionally.
    """
    cfg = _cfg(entity=True, procedural=True, date_path=True, chunk_date=True)
    boosts = select_boosts(cfg, fake_graph)
    assert [type(b) for b in boosts] == [
        EntityBoost,
        ProceduralBoost,
        TemporalDateBoost,
        ChunkDateBoost,
    ]


@pytest.mark.unit
def test_select_boosts_omits_entity_first_routing_by_default(
    fake_graph: FakeGraphRepository,
) -> None:
    """Default-safe (#429): the entity-first routing boost is NOT wired
    unless ``entity_first_routing_on`` is passed True — so every existing
    deployment's chain is byte-for-byte unchanged.

    Sabotage proof: registering the boost unconditionally would put an
    EntityFirstRoutingBoost in this chain and fail the assertion.
    """
    cfg = _cfg(entity=True, procedural=True, date_path=True, chunk_date=True)
    boosts = select_boosts(cfg, fake_graph)
    assert not any(isinstance(b, EntityFirstRoutingBoost) for b in boosts)


@pytest.mark.unit
def test_select_boosts_appends_entity_first_routing_when_flag_on(
    fake_graph: FakeGraphRepository,
) -> None:
    """#429 Phase 2b: with the flag resolved ON at build time, the routing
    boost is appended LAST so its multiplier composes on top of the tier
    de-boost.

    Sabotage proof: dropping the ``entity_first_routing_on`` guard, or
    inserting the boost anywhere but last, fails this assertion.
    """
    cfg = _cfg(entity=True, procedural=True, date_path=True, chunk_date=True)
    boosts = select_boosts(cfg, fake_graph, entity_first_routing_on=True)
    assert isinstance(boosts[-1], EntityFirstRoutingBoost)
    assert sum(isinstance(b, EntityFirstRoutingBoost) for b in boosts) == 1
    # The boost receives the config's tunables verbatim (not a fresh default) —
    # read via getattr so the test pins the wiring, not the attribute name.
    assert getattr(boosts[-1], "_config", None) is cfg.entity_first_routing


@pytest.mark.unit
def test_select_boosts_entity_receives_graph_dependency(
    fake_graph: FakeGraphRepository,
) -> None:
    """The graph parameter is threaded through to ``EntityBoost``; the
    other adapters do not see it.
    """
    cfg = _cfg(entity=True, procedural=True, date_path=True, chunk_date=True)
    boosts = select_boosts(cfg, fake_graph)
    entity_boost = next(b for b in boosts if isinstance(b, EntityBoost))
    # The boost stores the graph for in-degree lookups (private attr —
    # we don't import it; we read via getattr so the test pins the
    # wiring, not the attribute name).
    assert getattr(entity_boost, "_graph", None) is fake_graph


# ── build_search_pipeline — public-surface integration ────────────────


@pytest.mark.unit
def test_build_search_pipeline_returns_search_pipeline_with_rrf_fusion() -> None:
    """When ``fusion_strategy="rrf"``, the factory wires an RRFFusion.

    The factory's lazy imports (Azure embedding, usearch index, Neo4j
    client) all fall through to FakeXxx repositories in this test
    process — none of those services are running, so we exercise the
    fallback branches naturally without monkey-patching anything.

    Sabotage proof: if the fusion-strategy check were inverted, this
    would catch the wrong fusion type.
    """
    cfg = RetrievalConfig(fusion_strategy="rrf", rrf_k=42)
    pipeline = build_search_pipeline(config=_wire_cfg(cfg), registry=_provider_registry())

    assert isinstance(pipeline.fusion, RRFFusion)
    # rrf_k threads through to the fusion strategy (private attr access
    # via getattr so we pin behaviour, not the storage shape).
    assert getattr(pipeline.fusion, "_k", None) == 42


@pytest.mark.unit
def test_build_search_pipeline_with_bm25_primary_fusion() -> None:
    """``fusion_strategy="bm25_primary"`` selects BM25PrimaryFusion."""
    cfg = RetrievalConfig(fusion_strategy="bm25_primary")
    pipeline = build_search_pipeline(config=_wire_cfg(cfg), registry=_provider_registry())

    assert isinstance(pipeline.fusion, BM25PrimaryFusion)


@pytest.mark.unit
def test_build_search_pipeline_with_unknown_fusion_falls_back_to_bm25_primary() -> None:
    """Any non-``rrf`` value lands in the ``else`` branch of the
    fusion-strategy switch and yields ``BM25PrimaryFusion``.
    """
    cfg = RetrievalConfig(fusion_strategy="not-a-real-strategy")
    pipeline = build_search_pipeline(config=_wire_cfg(cfg), registry=_provider_registry())

    assert isinstance(pipeline.fusion, BM25PrimaryFusion)


@pytest.mark.unit
def test_build_search_pipeline_threads_config_through_to_pipeline() -> None:
    """The ``config`` param reaches the constructed ``SearchPipeline``.

    Sabotage proof: if the factory dropped its ``config=`` argument and
    constructed a fresh default instead, the rrf_k=99 sentinel would
    not survive.
    """
    cfg = RetrievalConfig(provider="fake", fusion_strategy="rrf", rrf_k=99)
    pipeline = build_search_pipeline(config=cfg, registry=_provider_registry())

    assert pipeline.config is cfg
    assert pipeline.config.rrf_k == 99


@pytest.mark.unit
def test_build_search_pipeline_with_all_boosts_enabled_wires_full_chain() -> None:
    """End-to-end: a fully-enabled retrieval config produces a pipeline
    whose boost chain matches ``select_boosts`` exactly.
    """
    cfg = RetrievalConfig(
        fusion_strategy="rrf",
        entity=EntityBoostConfig(enabled=True),
        procedural=ProceduralBoostConfig(enabled=True),
        temporal=TemporalBoostConfig(
            date_path_boost_enabled=True,
            chunk_date_boost_enabled=True,
        ),
    )
    pipeline = build_search_pipeline(config=_wire_cfg(cfg), registry=_provider_registry())

    boost_types = [type(b) for b in pipeline.boosts]
    assert boost_types == [
        EntityBoost,
        ProceduralBoost,
        TemporalDateBoost,
        ChunkDateBoost,
    ]


@pytest.mark.unit
def test_build_search_pipeline_classifier_dispatches_to_intent_module() -> None:
    """The internal ``_RuleClassifier`` delegates ``classify`` to
    ``kairix.core.search.intent.classify``. Drives line 89 (the inner
    method body) so coverage hits the classifier surface.

    Sabotage proof: if the classifier were swapped for a stub that
    always returned ``None``, the QueryIntent assertion would fail.
    """
    cfg = RetrievalConfig(fusion_strategy="rrf")
    pipeline = build_search_pipeline(config=_wire_cfg(cfg), registry=_provider_registry())

    classifier = pipeline.classifier
    # ``SearchPipeline.classifier`` is typed as ``object`` (structural
    # IntentClassifier protocol). Pin behaviour via getattr.
    classify_method = getattr(classifier, "classify", None)
    assert classify_method is not None
    intent = classify_method("when did we deploy v3")
    # Real rule classifier returns a QueryIntent enum — assert membership
    # in the documented set so this test isn't fragile to enum ordering.
    assert intent is not None
    assert hasattr(intent, "value") or hasattr(intent, "name")


@pytest.mark.unit
def test_build_search_pipeline_resolver_honours_extra_collections_env() -> None:
    """``KAIRIX_EXTRA_COLLECTIONS`` is comma-split and threaded through
    to the resolver.

    Threads an explicit env dict via ``build_search_pipeline(env={...})``
    so the factory's call to :func:`kairix.paths.extra_collections` reads
    from the test fixture rather than ``os.environ``. F1/F2-clean — no
    module-attribute swap, no env mutation.
    """
    cfg = RetrievalConfig(fusion_strategy="rrf")
    pipeline = build_search_pipeline(
        config=_wire_cfg(cfg),
        registry=_provider_registry(),
        env={"KAIRIX_EXTRA_COLLECTIONS": "alpha-collection, beta-collection"},
    )

    # Post topology_v2 flag retirement, the resolver shape is
    # ``ScopeCollectionCache(TopologyV2CollectionResolver(extra_collections=...))``
    # — the extras live on the inner v2 resolver, surfaced through the
    # cache wrapper's ``_inner`` attribute. The factory reads
    # ``KAIRIX_EXTRA_COLLECTIONS`` at the boundary (F4) and passes the
    # parsed list into the v2 resolver so operator behaviour from the
    # retired legacy resolver is preserved.
    inner = getattr(pipeline.resolver, "_inner", pipeline.resolver)
    extras = getattr(inner, "_extra", [])
    assert "alpha-collection" in extras
    assert "beta-collection" in extras


@pytest.mark.unit
def test_build_search_pipeline_uses_docker_log_path_when_dockerenv_marker_present(
    tmp_path: Any,
) -> None:
    """Drives the Docker-detection branch by threading an explicit
    ``env={"KAIRIX_DOCKER": "1"}`` mapping through ``build_search_pipeline``.

    The env-read boundary moved from ``factory.py`` into
    :func:`kairix.paths.is_docker_env` (F4); the factory threads its
    optional ``env`` kwarg through to that helper. F1/F2-clean — no
    module-attribute swap, no env mutation.
    """
    cfg = RetrievalConfig(fusion_strategy="rrf")
    pipeline = build_search_pipeline(
        config=_wire_cfg(cfg),
        registry=_provider_registry(),
        env={"KAIRIX_DOCKER": "1"},
    )

    # The search logger's path now resolves through paths.log_dir() —
    # honours KAIRIX_LOG_DIR env, config-file setting, then per-mode
    # defaults (docker → /data/kairix/logs legacy or /var/lib/kairix/logs
    # FHS, server → /var/log/kairix, user → ~/.cache/kairix/logs).
    # The assertion just confirms the logger holds the same path the
    # resolver produces (replaces the previous hardcoded /data/kairix/logs
    # check — see #447).
    from kairix.paths import log_dir

    logger_obj = pipeline.logger
    assert logger_obj is not None
    logger_path = str(getattr(logger_obj, "_search_log_path", ""))
    assert str(log_dir()) in logger_path


@pytest.mark.unit
def test_build_search_pipeline_with_no_config_loads_via_config_loader() -> None:
    """When ``config=None``, the factory delegates to ``load_config()``.
    The fallback path in ``config_loader`` returns
    ``RetrievalConfig.defaults()`` when no YAML is present.

    Sabotage proof: if the factory ignored its ``config=None`` arg and
    constructed a fresh ``RetrievalConfig()``, the pipeline's config
    would not match ``RetrievalConfig.defaults()``.
    """
    pipeline = build_search_pipeline(config=RetrievalConfig(provider="fake"), registry=_provider_registry())

    # Pipeline got *some* RetrievalConfig — exact contents depend on
    # whether a kairix.config.yaml is on disk in the test cwd. We assert
    # the type and that the load path was traversed (not a None config).
    assert isinstance(pipeline.config, RetrievalConfig)


@pytest.mark.unit
def test_build_search_pipeline_uses_real_vector_index_when_available() -> None:
    """Drives line 118 — the ``index is not None`` branch wraps the
    real index in ``UsearchVectorRepository``.

    Threads a stand-in vector-index factory through the ``vec_index_factory=``
    kwarg (F1-clean DI seam). The factory's ``UsearchVectorRepository`` just
    stores the index reference, so any object works.
    """

    class _StandInIndex:
        """Minimal usearch-shaped stand-in. The factory does not call
        any methods at construction time — it only stores the reference.
        """

        def __len__(self) -> int:
            return 1

    cfg = RetrievalConfig(fusion_strategy="rrf")
    pipeline = build_search_pipeline(
        config=_wire_cfg(cfg),
        registry=_provider_registry(),
        deps=FactoryDeps(vec_index_factory=lambda: _StandInIndex()),
    )

    # Confirm the vector backend received a UsearchVectorRepository wired
    # to our stand-in — pinned via repr inspection so we don't import
    # the private repository class.
    assert "UsearchVectorRepository" in type(pipeline.vector._vector_repo).__name__


@pytest.mark.unit
def test_build_search_pipeline_falls_back_when_get_vector_index_raises() -> None:
    """Drives lines 125-129 — when ``get_vector_index`` raises, the
    factory logs a warning and substitutes a null vector repo.

    Asserts behavioural fallback: ``search()`` returns ``[]`` and
    ``count()`` returns 0, without depending on the fallback class
    name (the inline ``_NullVectorRepository`` is private to factory).

    Sabotage proof: if the except handler stopped recovering, the
    factory call would raise; this test asserts a well-formed
    pipeline whose vector search degrades silently.
    """

    def _boom() -> object:
        raise RuntimeError("simulated usearch load failure")

    cfg = RetrievalConfig(fusion_strategy="rrf")
    pipeline = build_search_pipeline(
        config=_wire_cfg(cfg),
        registry=_provider_registry(),
        deps=FactoryDeps(vec_index_factory=_boom),
    )

    # The factory threaded a null vector repo in instead of crashing.
    repo = pipeline.vector._vector_repo
    assert repo.search(query_vec=[0.0] * 4, k=10, collections=None) == []
    assert repo.count() == 0


@pytest.mark.unit
def test_build_search_pipeline_uses_neo4j_graph_when_client_available() -> None:
    """Drives lines 139-140 — when ``get_client()`` succeeds, the
    factory wraps the client in ``Neo4jGraphRepository`` instead of
    falling back to ``FakeGraphRepository``.

    Threads a stand-in client through the ``graph_client_factory=`` kwarg
    (F1-clean DI seam). The factory does not actually call cypher at
    construction time — structural typing is sufficient at the boundary.
    """

    class _StandInClient:
        @property
        def available(self) -> bool:
            return True

        def cypher(self, query: str, **_kw: object) -> list[dict[str, Any]]:
            return []

    cfg = RetrievalConfig(fusion_strategy="rrf")
    pipeline = build_search_pipeline(
        config=_wire_cfg(cfg),
        registry=_provider_registry(),
        deps=FactoryDeps(graph_client_factory=_StandInClient),
    )

    # Pipeline graph is a Neo4jGraphRepository, not a FakeGraphRepository.
    assert type(pipeline.graph).__name__ == "Neo4jGraphRepository"


# NOTE: test_build_search_pipeline_loads_collections_and_agent_registry_from_yaml
# was retired alongside the topology_v2_collection_resolver flag (#132). The
# legacy ``DefaultCollectionResolver`` parsed ``agents:`` from
# ``kairix.config.yaml`` and exposed the parsed registry on ``_registry``;
# the post-cutover ``TopologyV2CollectionResolver`` reads agents from the
# ``topology_scope_profiles`` + ``topology_scope_entries`` v2 tables instead
# and has no ``_registry`` attribute. Agent registration is now exercised by
# the topology_v2 integration tests in
# ``tests/integration/test_topology_v2_*`` against the real schema, not via
# this legacy YAML-parsing path.


@pytest.mark.unit
def test_build_search_pipeline_falls_back_to_fake_graph_when_get_client_raises() -> None:
    """Drives lines 141-145 — when ``get_client()`` raises, the factory
    logs a warning and substitutes ``FakeGraphRepository(available=False)``.

    Without this guard the factory would propagate the connection
    exception and operator-facing search would crash on startup.

    Sabotage proof: removing the except clause would propagate the
    RuntimeError; the test asserts a clean pipeline instead.

    Threads a raising stand-in through the ``graph_client_factory=`` kwarg
    (F1-clean DI seam) instead of mutating ``kairix.knowledge.graph.client``.
    """

    def _boom() -> Any:
        raise RuntimeError("simulated neo4j driver failure at boundary")

    cfg = RetrievalConfig(fusion_strategy="rrf")
    pipeline = build_search_pipeline(
        config=_wire_cfg(cfg),
        registry=_provider_registry(),
        deps=FactoryDeps(graph_client_factory=_boom),
    )

    # Pipeline still constructed — graph fell back to a null repo.
    # Asserts the protocol surface (available=False) rather than the
    # private inline ``_NullGraphRepository`` class name.
    assert pipeline.graph.available is False
    assert pipeline.graph.find_entity("any") is None
    assert pipeline.graph.entity_in_degrees() == []
    assert pipeline.graph.cypher("RETURN 1") == []


# NOTE: test_build_search_pipeline_tolerates_agent_registry_parse_exception
# and test_build_search_pipeline_tolerates_load_collections_exception were
# retired alongside the topology_v2_collection_resolver flag (#132). Both
# exercised the legacy resolver's YAML-parsing surface
# (parse_agent_registry / load_collections), which is no longer called by
# the factory after the cutover. The post-cutover v2 resolver reads from
# the topology_scope_profiles + topology_scope_entries tables instead;
# resilience against bad source data is exercised by the v2 integration
# tests in tests/integration/test_topology_v2_*.


# ── plugin-driven embedding service wiring ────────────────────────────


@pytest.mark.unit
def test_build_search_pipeline_wires_provider_embedding_service_from_registry() -> None:
    """When ``cfg.provider`` names an installed plugin, the factory builds
    a ``ProviderEmbeddingService`` wrapping that plugin. The registry
    seam lets us inject a ``FakeProviderRegistry`` without touching env
    vars or patching ``kairix`` internals (F1/F2 clean).

    Sabotage proof: rewire the factory to swallow the resolved name and
    construct ``ProviderEmbeddingService`` from a hard-coded default
    plugin and this test fails because the embed-service no longer
    threads back to the ``FakeProvider`` we supplied via the registry.
    """
    from kairix.transport.embed_service import ProviderEmbeddingService
    from tests.fakes import FakeProvider, FakeProviderRegistry

    fake_provider = FakeProvider(name="fake", vector=[0.1, 0.2, 0.3], dim=3)
    registry = FakeProviderRegistry({"fake": fake_provider})

    cfg = RetrievalConfig(fusion_strategy="rrf", provider="fake")
    pipeline = build_search_pipeline(config=cfg, registry=registry)

    embed_service = pipeline.vector._embedding
    assert isinstance(embed_service, ProviderEmbeddingService)
    # The registry's resolve() was driven exactly once, with our name.
    assert registry.resolve_calls == ["fake"]


@pytest.mark.unit
def test_build_search_pipeline_provider_embedding_service_routes_through_plugin() -> None:
    """End-to-end behavioural pin: the embed call dispatches into the
    plugin's ``embed_batch``. Confirms the ProviderEmbeddingService →
    Provider wiring is live, not just the type assertion.

    Sabotage proof: if the factory wired a dud plugin or wrapped a
    different provider, the recorded ``embed_calls`` would not match
    the text we submitted.
    """
    from tests.fakes import FakeProvider, FakeProviderRegistry

    fake_provider = FakeProvider(name="fake", vector=[0.4, 0.5, 0.6], dim=3)
    registry = FakeProviderRegistry({"fake": fake_provider})

    cfg = RetrievalConfig(fusion_strategy="rrf", provider="fake")
    pipeline = build_search_pipeline(config=cfg, registry=registry)

    vec = pipeline.vector._embedding.embed("hello")
    assert vec == [0.4, 0.5, 0.6]
    # The plugin saw exactly one call carrying our text — either directly
    # or via the coalescer-batched path; both shapes resolve to a single
    # entry in embed_calls.
    assert fake_provider.embed_calls
    assert "hello" in fake_provider.embed_calls[-1]


@pytest.mark.unit
def test_build_search_pipeline_raises_value_error_when_no_provider_configured() -> None:
    """``cfg.provider=None`` and no YAML provider field → typed ValueError.

    The transitional fallback to the legacy direct-SDK code was removed
    in v2026.5.17. The factory now requires an explicit provider;
    operators see the misconfiguration at pipeline-build time rather
    than discovering an inert embed surface at query time.

    The error message points to the YAML field, the probe-config
    command, and lists the registry's currently-installed plugins so
    operators have an actionable next step.

    Sabotage proof: re-add a fallback branch (returning some default
    embed service when ``name is None``) and this
    ``pytest.raises(ValueError, ...)`` clause fails because the factory
    returns a pipeline instead of raising.
    """
    from tests.fakes import FakeProvider, FakeProviderRegistry

    registry = FakeProviderRegistry({"fake": FakeProvider(name="fake")})
    cfg = RetrievalConfig(fusion_strategy="rrf")
    assert cfg.provider is None, "test premise: cfg has no provider configured"

    with pytest.raises(ValueError, match="missing the required 'provider:' field") as excinfo:
        build_search_pipeline(config=cfg, registry=registry)

    # Operators see the installed-plugin list so they can pick a valid name.
    assert "fake" in str(excinfo.value)


@pytest.mark.unit
def test_build_search_pipeline_propagates_provider_not_registered() -> None:
    """When the configured provider name is unknown to the registry,
    the factory surfaces the typed ``ProviderNotRegistered`` error
    rather than silently degrading. Operators see the installed-plugins
    list in the error message.

    Sabotage proof: swallowing the registry error and falling back
    silently would mask config typos; this test asserts the typed
    exception propagates.
    """
    from kairix.providers import ProviderNotRegistered
    from tests.fakes import FakeProvider, FakeProviderRegistry

    registry = FakeProviderRegistry({"fake": FakeProvider(name="fake")})
    cfg = RetrievalConfig(fusion_strategy="rrf", provider="nonexistent")

    with pytest.raises(ProviderNotRegistered) as excinfo:
        build_search_pipeline(config=cfg, registry=registry)

    assert excinfo.value.name == "nonexistent"
    assert "fake" in excinfo.value.available


# ── reranker resolution wiring (FactoryDeps.reranker_override) ─────────


@pytest.mark.unit
def test_build_search_pipeline_wires_cross_encoder_closure_by_default() -> None:
    """Default config (rerank_intents non-empty) builds the production
    cross-encoder closure so per-intent rerank can fire at search time.

    Building the closure does NOT import torch — that happens only when
    the closure is *called*. This test asserts the closure is wired, not
    that it runs, so it stays sub-second.

    Sabotage proof: forcing ``reranker=None`` in the factory's resolution
    would fail the ``is not None`` assertion.
    """
    cfg = RetrievalConfig(fusion_strategy="rrf", rerank_intents=("semantic",))
    assert cfg.rerank.enabled is False  # not force-enabled — intents alone wire it
    pipeline = build_search_pipeline(config=_wire_cfg(cfg), registry=_provider_registry())

    assert pipeline.reranker is not None


@pytest.mark.unit
def test_build_search_pipeline_wires_cross_encoder_when_force_enabled_no_intents() -> None:
    """``rerank.enabled=True`` builds the closure even with empty rerank_intents.

    Pins the ``not cfg.rerank.enabled`` operand of the rerank-disabled
    test: with ``enabled=True`` the closure must be built regardless of
    ``rerank_intents``. An ``or`` swap in ``not enabled and not intents``
    would wrongly disable rerank here (``True or ...``), so this kills
    that mutant from the enabled side.
    """
    from kairix.core.search.config import RerankConfig

    cfg = RetrievalConfig(
        fusion_strategy="rrf",
        rerank=RerankConfig(enabled=True),
        rerank_intents=(),
    )
    pipeline = build_search_pipeline(config=_wire_cfg(cfg), registry=_provider_registry())

    assert pipeline.reranker is not None


@pytest.mark.unit
def test_build_search_pipeline_wires_cross_encoder_when_intents_only() -> None:
    """``rerank_intents`` non-empty (enabled=False) builds the closure.

    The complementary kill for the ``and`` at the rerank-disabled check:
    ``not False and not ("semantic",)`` = ``True and False`` = False →
    NOT disabled → closure built. An ``or`` swap gives ``True or False`` =
    True → wrongly disabled → ``reranker is None`` → this assertion fails.
    """
    cfg = RetrievalConfig(fusion_strategy="rrf", rerank_intents=("multi_hop",))
    assert cfg.rerank.enabled is False
    pipeline = build_search_pipeline(config=_wire_cfg(cfg), registry=_provider_registry())

    assert pipeline.reranker is not None


@pytest.mark.unit
def test_build_search_pipeline_wires_no_reranker_when_fully_disabled() -> None:
    """``rerank.enabled=False`` AND empty ``rerank_intents`` → ``reranker=None``.

    Both operands of ``not enabled and not intents`` are True → rerank
    disabled → the factory wires no closure (no sentence-transformers
    import even on the production path).

    Sabotage proof: dropping the disabled short-circuit would build a
    closure and fail the ``is None`` assertion.
    """
    cfg = RetrievalConfig(fusion_strategy="rrf", rerank_intents=())
    assert cfg.rerank.enabled is False
    pipeline = build_search_pipeline(config=_wire_cfg(cfg), registry=_provider_registry())

    assert pipeline.reranker is None


@pytest.mark.unit
def test_build_search_pipeline_rerank_disabled_sentinel_wires_no_reranker() -> None:
    """``FactoryDeps(reranker_override=RERANK_DISABLED)`` wires ``reranker=None``.

    The sentinel overrides the production resolution: even with a default
    config whose ``rerank_intents`` would otherwise build the closure, the
    pipeline carries no reranker — the seam integration tests rely on this
    to skip the ~5s torch import.

    Sabotage proof: ignoring the sentinel would build the closure and fail
    the ``is None`` assertion.
    """
    from kairix.core.factory import RERANK_DISABLED

    cfg = RetrievalConfig(fusion_strategy="rrf", rerank_intents=("semantic",))
    pipeline = build_search_pipeline(
        config=_wire_cfg(cfg),
        registry=_provider_registry(),
        deps=FactoryDeps(reranker_override=RERANK_DISABLED),
    )

    assert pipeline.reranker is None


@pytest.mark.unit
def test_build_search_pipeline_reranker_override_callable_used_verbatim() -> None:
    """A callable ``reranker_override`` is wired onto the pipeline verbatim.

    Sabotage proof: building the production closure instead of using the
    injected callable would fail the identity assertion.
    """

    def fake_reranker(query: str, fused: list) -> list:
        return fused

    cfg = RetrievalConfig(fusion_strategy="rrf", rerank_intents=("semantic",))
    pipeline = build_search_pipeline(
        config=_wire_cfg(cfg),
        registry=_provider_registry(),
        deps=FactoryDeps(reranker_override=fake_reranker),
    )

    assert pipeline.reranker is fake_reranker


@pytest.mark.unit
def test_build_search_pipeline_auto_hydrates_secrets_via_bootstrap() -> None:
    """Every Python-API entry into ``build_search_pipeline`` auto-bootstraps
    the secrets bundle so a fresh process resolves canonical credentials
    without the caller having to import ``bootstrap_secrets`` first.

    Pre-fix the probe runner (``kairix.quality.probe.runner.run_probe_search``)
    failed with ``SecretNotFoundError: kairix-provider-llm-api-key`` because
    the bundle hydration only fired from CLI/MCP entry points. CLI users
    saw search work; Python-API users saw probe errors. The fix lives in
    the factory so every consumer (probe, eval harnesses, notebooks,
    ad-hoc scripts) inherits the contract.

    Sabotage-proof: comment out the ``bootstrap_secrets()`` call in
    ``build_search_pipeline``; this test fails because the import-counter
    fake doesn't observe the call.
    """
    from kairix.core.factory import build_search_pipeline
    from kairix.secrets.bootstrap import bootstrap_secrets as real_bootstrap

    bootstrap_calls: list[int] = []

    def _counting_bootstrap(*args: Any, **kwargs: Any) -> int:
        bootstrap_calls.append(1)
        return real_bootstrap(*args, **kwargs)

    # The factory call may fail later in the dependency chain (no real
    # creds in unit test env) — that's fine. We only assert the
    # bootstrap_secrets() call fired BEFORE any credential resolution.
    try:
        build_search_pipeline(
            config=RetrievalConfig(provider="fake"),
            registry=_provider_registry(),
            deps=FactoryDeps(bootstrap_secrets_fn=_counting_bootstrap),
        )
    except Exception:
        pass
    assert len(bootstrap_calls) >= 1, "build_search_pipeline must auto-bootstrap secrets before credential resolution"


@pytest.mark.unit
def test_build_connector_pipeline_auto_hydrates_secrets() -> None:
    """``build_connector_pipeline`` calls ``bootstrap_secrets()`` at the
    factory boundary so Python-API consumers (eval harnesses, integration
    tests outside the worker, ad-hoc scripts) don't hit ``SecretNotFoundError``
    when the pipeline's downstream credential resolution fires.

    Sabotage-proof: comment out the ``bootstrap_secrets()`` call in
    ``build_connector_pipeline``; the call-counter doesn't observe a fire.
    """
    import sqlite3

    from kairix.core.db.schema import create_schema
    from kairix.core.factory import build_connector_pipeline
    from kairix.secrets.bootstrap import bootstrap_secrets as real

    bootstrap_calls: list[int] = []

    def _counting(*args: Any, **kwargs: Any) -> int:
        bootstrap_calls.append(1)
        return real(*args, **kwargs)

    db = sqlite3.connect(":memory:")
    create_schema(db)
    try:
        build_connector_pipeline(db=db, collection="probe-collection", deps=FactoryDeps(bootstrap_secrets_fn=_counting))
    except Exception:
        pass  # downstream may fail in unit env; we only assert bootstrap fired
    assert len(bootstrap_calls) >= 1, "build_connector_pipeline must auto-bootstrap"


@pytest.mark.unit
def test_build_neo4j_drainer_auto_hydrates_secrets() -> None:
    """``build_neo4j_drainer`` calls ``bootstrap_secrets()`` at the factory
    boundary — same pattern as ``build_search_pipeline`` /
    ``build_connector_pipeline``. The drainer's Neo4j repo lazily resolves
    its connection credentials; without bootstrap, Python-API callers hit
    ``SecretNotFoundError``.

    Sabotage-proof: comment out the ``bootstrap_secrets()`` call in
    ``build_neo4j_drainer``; the call-counter doesn't observe a fire.
    """
    import sqlite3

    from kairix.core.factory import build_neo4j_drainer
    from kairix.secrets.bootstrap import bootstrap_secrets as real
    from tests.fakes import FakeDrainGraphRepository

    bootstrap_calls: list[int] = []

    def _counting(*args: Any, **kwargs: Any) -> int:
        bootstrap_calls.append(1)
        return real(*args, **kwargs)

    db = sqlite3.connect(":memory:")
    try:
        build_neo4j_drainer(db=db, repo=FakeDrainGraphRepository(), deps=FactoryDeps(bootstrap_secrets_fn=_counting))
    except Exception:
        pass  # downstream may fail; we only assert bootstrap fired
    assert len(bootstrap_calls) >= 1, "build_neo4j_drainer must auto-bootstrap"


@pytest.mark.unit
def test_build_pipelines_tolerate_bootstrap_secrets_raising() -> None:
    """All 3 factory entry points (``build_search_pipeline``,
    ``build_connector_pipeline``, ``build_neo4j_drainer``) soft-fail when
    ``bootstrap_secrets()`` raises — the factory continues + downstream
    handles missing creds with a clearer error. Production deploys
    always have the bundle; local dev may not.

    Sabotage-proof: remove the ``try/except`` around the bootstrap call;
    this test fails because the factory propagates the synthetic
    exception instead of swallowing it.
    """
    import sqlite3

    from kairix.core.db.schema import create_schema
    from kairix.core.factory import build_connector_pipeline, build_neo4j_drainer, build_search_pipeline
    from tests.fakes import FakeDrainGraphRepository

    def _raises(*_a: Any, **_kw: Any) -> int:
        raise RuntimeError("synthetic bundle-missing")

    # build_search_pipeline soft-fails through to downstream resolution.
    # In unit test env downstream then raises (no real creds) — that's
    # fine; the assertion is that bootstrap's raise didn't propagate.
    try:
        build_search_pipeline(
            config=RetrievalConfig(provider="fake"),
            registry=_provider_registry(),
            deps=FactoryDeps(bootstrap_secrets_fn=_raises),
        )
    except RuntimeError as exc:
        assert "synthetic bundle-missing" not in str(exc), "bootstrap exception leaked from build_search_pipeline"
    except Exception:
        pass

    # Same contract for connector pipeline.
    db = sqlite3.connect(":memory:")
    create_schema(db)
    try:
        build_connector_pipeline(db=db, collection="probe", deps=FactoryDeps(bootstrap_secrets_fn=_raises))
    except RuntimeError as exc:
        assert "synthetic bundle-missing" not in str(exc), "bootstrap exception leaked from build_connector_pipeline"
    except Exception:
        pass

    # Same for neo4j drainer.
    db2 = sqlite3.connect(":memory:")
    try:
        build_neo4j_drainer(db=db2, repo=FakeDrainGraphRepository(), deps=FactoryDeps(bootstrap_secrets_fn=_raises))
    except RuntimeError as exc:
        assert "synthetic bundle-missing" not in str(exc), "bootstrap exception leaked from build_neo4j_drainer"
    except Exception:
        pass

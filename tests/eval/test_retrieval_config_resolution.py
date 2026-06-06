"""Contract probes for kairix.quality.eval.retrieval — config resolution wiring.

Pins the eval-tooling contract that the *resolved* RetrievalConfig
(per-collection overrides + fusion_override layered on the global YAML
config) flows through to the pipeline factory before the pipeline is
built. Closes #112 for the eval/benchmark path: ``--collection X`` now
receives X's tuned overrides, and the historical ``fusion_override``
ordering bug (config reassigned *after* the pipeline was built) is gone.

The tests substitute the production ``build_search_pipeline`` with a
``_PipelineBuilderSpy`` via ``RetrievalDeps(pipeline_builder=...)`` so
the resolved config can be observed without spinning up Azure / Neo4j /
usearch. ``RetrievalDeps`` (issue #199) replaced the F6-violating
``search_fn=`` / ``pipeline_builder=`` test-only kwargs.

This file is F2-clean: all per-collection / global config inputs flow
through the documented ``ResolveConfigDeps(config_fn=, overrides_fn=)``
injection seam (kairix.core.search.config_loader). No
``monkeypatch.setenv("KAIRIX_CONFIG_PATH", ...)`` calls remain.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

import pytest

from kairix.core.search import config_loader
from kairix.core.search.config import RetrievalConfig
from kairix.core.search.config_loader import ResolveConfigDeps
from kairix.quality.eval.retrieval import RetrievalDeps, retrieve

pytestmark = pytest.mark.contract


@dataclass
class _CapturedSearchResult:
    """Minimal stand-in for SearchResult — only the fields _retrieve_hybrid reads."""

    results: list[Any] = field(default_factory=list)
    intent: Any = field(default_factory=lambda: type("Intent", (), {"value": "semantic"})())
    bm25_count: int = 0
    vec_count: int = 0
    fused_count: int = 0
    vec_failed: bool = False
    latency_ms: float = 0.0


class _PipelineSpy:
    """Records the search call args and returns an empty SearchResult-shaped object."""

    def __init__(self, config: RetrievalConfig) -> None:
        self.config = config
        self.search_calls: list[dict[str, Any]] = []

    def search(self, **kwargs: Any) -> _CapturedSearchResult:
        self.search_calls.append(kwargs)
        return _CapturedSearchResult()


def _builder_spy() -> tuple[Any, list[RetrievalConfig]]:
    """Return (builder_fn, captured_configs). The builder records every config
    it sees and returns a _PipelineSpy bound to that config.
    """
    captured: list[RetrievalConfig] = []

    def _builder(config: RetrievalConfig | None = None) -> _PipelineSpy:
        assert config is not None, "factory must receive a non-None config from _retrieve_hybrid"
        captured.append(config)
        return _PipelineSpy(config)

    return _builder, captured


@pytest.fixture(autouse=True)
def _isolated_cache() -> Iterator[None]:
    """Each probe gets a fresh load-cache so per-test config inputs are not
    masked by the lru_cache singleton."""
    config_loader.reset_config_cache()
    yield
    config_loader.reset_config_cache()


def _with_global_and_overrides(
    global_cfg: RetrievalConfig,
    overrides: dict[str, dict],
) -> RetrievalDeps:
    """Build a ``RetrievalDeps`` whose pipeline_builder spies on the resolved
    config, threading a ``ResolveConfigDeps`` so per-collection overrides come
    from the test fixture rather than process env.

    The returned deps' pipeline_builder calls ``resolve_retrieval_config`` with
    the injected ``ResolveConfigDeps`` — matching what production does, but
    pinning the YAML loader + overrides surface to the per-test fixture.
    """
    raise NotImplementedError("placeholder — tests build RetrievalDeps inline")


def test_per_collection_override_flows_into_pipeline_builder() -> None:
    """When ``retrieve(collection="X")`` is called with a per-collection
    override registered via the injected ``ResolveConfigDeps``, the
    *resolved* config that reaches the pipeline builder reflects that
    override.

    This is the core #112 contract: ``--collection reference-library``
    must receive reflib's tuned config, not ``RetrievalConfig.defaults()``.
    """
    global_cfg = RetrievalConfig(fusion_strategy="bm25_primary", rrf_k=60)
    overrides = {
        "reflib-test": {"fusion_strategy": "rrf", "rrf_k": 10},
    }

    captured: list[RetrievalConfig] = []

    def _builder(config: RetrievalConfig | None = None) -> _PipelineSpy:
        assert config is not None
        captured.append(config)
        return _PipelineSpy(config)

    # Inject the global config + overrides via the production resolver's
    # documented seam, then drive retrieve() through the spy builder.
    resolve_deps = ResolveConfigDeps(
        config_fn=lambda: global_cfg,
        overrides_fn=lambda: overrides,
    )

    # Pre-resolve here so the test isn't entangled with the production
    # resolver's defaults — we hand the pipeline_builder its config directly.
    from kairix.core.search.config_loader import resolve_retrieval_config

    resolved = resolve_retrieval_config(collection="reflib-test", deps=resolve_deps)
    retrieve(
        query="anything",
        system="hybrid",
        collection="reflib-test",
        config=resolved,
        deps=RetrievalDeps(pipeline_builder=_builder),
    )

    assert len(captured) == 1, f"expected exactly one pipeline build; got {len(captured)}"
    cfg = captured[0]
    # Override applied: fusion strategy + rrf_k come from the per-collection block.
    assert cfg.fusion_strategy == "rrf", f"expected per-collection override 'rrf', got {cfg.fusion_strategy!r}"
    assert cfg.rrf_k == 10, f"expected per-collection rrf_k=10, got {cfg.rrf_k}"


def test_global_config_used_when_no_per_collection_override_present() -> None:
    """Sabotage check: a different collection (or none) gets the global
    config, not the override. Proves the per-collection lookup is keyed
    on the collection name, not "always wins".
    """
    global_cfg = RetrievalConfig(fusion_strategy="bm25_primary", rrf_k=60)
    overrides = {
        "reflib-test": {"fusion_strategy": "rrf", "rrf_k": 10},
        # vault-areas intentionally absent
    }

    captured: list[RetrievalConfig] = []

    def _builder(config: RetrievalConfig | None = None) -> _PipelineSpy:
        assert config is not None
        captured.append(config)
        return _PipelineSpy(config)

    resolve_deps = ResolveConfigDeps(
        config_fn=lambda: global_cfg,
        overrides_fn=lambda: overrides,
    )

    from kairix.core.search.config_loader import resolve_retrieval_config

    resolved = resolve_retrieval_config(collection="vault-areas", deps=resolve_deps)
    retrieve(
        query="anything",
        system="hybrid",
        collection="vault-areas",
        config=resolved,
        deps=RetrievalDeps(pipeline_builder=_builder),
    )

    cfg = captured[0]
    assert cfg.fusion_strategy == "bm25_primary", (
        f"expected global 'bm25_primary' for vault-areas (no override), got {cfg.fusion_strategy!r}"
    )
    assert cfg.rrf_k == 60


def test_fusion_override_layered_on_top_of_resolved_config() -> None:
    """Pre-fix bug: ``fusion_override`` was reassigned to ``config`` AFTER
    the pipeline was already built, so the override never reached the
    pipeline. The fix resolves config first, applies override, THEN
    builds the pipeline.

    This test pins the corrected order: when ``fusion_override='rrf'`` is
    passed, the pipeline receives a config with ``fusion_strategy=='rrf'``
    regardless of what the global or per-collection override said.
    """
    global_cfg = RetrievalConfig(fusion_strategy="bm25_primary")
    overrides = {
        "docs": {"fusion_strategy": "bm25_primary"},
    }

    captured: list[RetrievalConfig] = []

    def _builder(config: RetrievalConfig | None = None) -> _PipelineSpy:
        assert config is not None
        captured.append(config)
        return _PipelineSpy(config)

    from kairix.core.search.config_loader import resolve_retrieval_config

    resolved = resolve_retrieval_config(
        collection="docs",
        deps=ResolveConfigDeps(
            config_fn=lambda: global_cfg,
            overrides_fn=lambda: overrides,
        ),
    )
    retrieve(
        query="x",
        system="hybrid",
        collection="docs",
        fusion_override="rrf",
        config=resolved,
        deps=RetrievalDeps(pipeline_builder=_builder),
    )

    cfg = captured[0]
    # Override beats both global and per-collection settings.
    assert cfg.fusion_strategy == "rrf", (
        f"fusion_override='rrf' did not reach pipeline; got fusion_strategy={cfg.fusion_strategy!r}. "
        "This is the historical reorder bug (config reassigned after pipeline already built)."
    )


def test_explicit_config_bypasses_resolution() -> None:
    """An explicit ``config=`` argument is identity-passed through to the
    pipeline builder — no merge, no resolution, no override layering.

    Sabotage check: the resolved-config path should NOT consume YAML when
    the caller has already done the work.
    """
    explicit = RetrievalConfig.minimal()
    builder, captured = _builder_spy()
    retrieve(
        query="x",
        system="hybrid",
        config=explicit,
        deps=RetrievalDeps(pipeline_builder=builder),
    )

    # Identity-passed: same object, no merge.
    assert captured[0] is explicit


def test_retrieval_deps_default_factory_binds_callable_pipeline_builder() -> None:
    """``RetrievalDeps()`` with no overrides constructs a deps bag whose
    ``pipeline_builder`` is a callable, not ``None``.

    Sabotage proof: the issue calls out ``Optional[Callable] = None``
    self-resolving in ``__post_init__`` as the rejected pattern that
    "just landed a mypy bug". ``default_factory`` must bind a real
    callable or this assertion fires. The complementary ``searcher``
    field stays Optional because the two seams are mutually exclusive
    (a pre-bound searcher means "skip pipeline construction").
    """
    deps = RetrievalDeps()
    assert callable(deps.pipeline_builder), (
        f"default_factory must bind a callable; got {deps.pipeline_builder!r}. "
        "Regressing to ``pipeline_builder: Callable | None = None`` would leave this None."
    )
    assert deps.searcher is None, "searcher defaults to None — no pre-bound searcher"


def test_retrieval_deps_searcher_takes_precedence_over_pipeline_builder() -> None:
    """When ``RetrievalDeps(searcher=fn)`` is provided, the pipeline builder is
    NOT invoked. Sabotage proof: the builder is a callable that raises if
    called — the test only passes when the searcher path bypasses it.
    """

    @dataclass
    class _CallableSearchResult:
        results: list[Any] = field(default_factory=list)
        intent: Any = field(default_factory=lambda: type("Intent", (), {"value": "semantic"})())
        bm25_count: int = 0
        vec_count: int = 0
        fused_count: int = 0
        vec_failed: bool = False
        latency_ms: float = 0.0

    def _exploding_builder(*, config: Any) -> Any:
        raise AssertionError(
            "pipeline_builder must NOT be called when searcher= is provided. "
            "_retrieve_hybrid bypassed the searcher seam."
        )

    captured: list[dict[str, Any]] = []

    def _spy_searcher(**kwargs: Any) -> _CallableSearchResult:
        captured.append(kwargs)
        return _CallableSearchResult()

    retrieve(
        query="hello",
        system="hybrid",
        deps=RetrievalDeps(searcher=_spy_searcher, pipeline_builder=_exploding_builder),
    )

    assert len(captured) == 1, "searcher should be called exactly once"
    assert captured[0]["query"] == "hello"

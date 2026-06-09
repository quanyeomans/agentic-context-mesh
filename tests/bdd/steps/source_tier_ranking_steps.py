"""Step impls for source_tier_ranking.feature (#432 deferred BDD).

Drives :class:`SourceTierBoost` directly through a real
:class:`SearchPipeline` composed from fakes — no monkey-patching, no
@patch. The boost's tier map flows in via constructor kwargs so the
contract pinned here is the same one ``factory.select_boosts`` honours
in production.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest
from pytest_bdd import given, then, when

from kairix.core.search.boosts import SourceTierBoost
from kairix.core.search.config import SourceTier, SourceTierBoostConfig
from kairix.core.search.rrf import FusedResult

pytestmark = pytest.mark.bdd


@dataclass
class _Ctx:
    boost_enabled: bool = False
    fused: list[FusedResult] = field(default_factory=list)
    tier_map: dict[str, str] = field(default_factory=dict)
    boosted: list[FusedResult] = field(default_factory=list)


@pytest.fixture
def source_tier_ctx() -> _Ctx:
    return _Ctx()


@given("the operator has source-tier boost enabled")
def _enabled(source_tier_ctx: _Ctx) -> None:
    source_tier_ctx.boost_enabled = True


@given("the operator has source-tier boost disabled")
def _disabled(source_tier_ctx: _Ctx) -> None:
    source_tier_ctx.boost_enabled = False


def _fused(path: str, collection: str, rrf_score: float = 0.5) -> FusedResult:
    return FusedResult(
        path=path,
        collection=collection,
        title=path.rsplit("/", 1)[-1],
        snippet=f"snippet for {path}",
        rrf_score=rrf_score,
        boosted_score=rrf_score,
    )


@given("a chunk in collection 'vault-canon' at tier 'canonical'")
def _add_canon(source_tier_ctx: _Ctx) -> None:
    source_tier_ctx.fused.append(_fused("/ethos.md", "vault-canon", rrf_score=0.5))
    source_tier_ctx.tier_map["vault-canon"] = "canonical"


@given("a chunk in collection 'reference-library' at tier 'reference'")
def _add_ref(source_tier_ctx: _Ctx) -> None:
    source_tier_ctx.fused.append(_fused("/wiki/topic.md", "reference-library", rrf_score=0.5))
    source_tier_ctx.tier_map["reference-library"] = "reference"


@when("the operator searches across both collections")
def _apply_boost(source_tier_ctx: _Ctx) -> None:
    boost = SourceTierBoost(
        tier_map=source_tier_ctx.tier_map,
        config=SourceTierBoostConfig(
            enabled=source_tier_ctx.boost_enabled,
            multipliers=(
                (SourceTier.CANONICAL, 3.0),
                (SourceTier.ACTIVE_STANDARD, 2.0),
                (SourceTier.VAULT_ACTIVE, 1.0),
                (SourceTier.REFERENCE, 0.6),
                (SourceTier.ARCHIVED, 0.2),
            ),
        ),
    )
    source_tier_ctx.boosted = boost.boost(list(source_tier_ctx.fused), "q", {})


@then("the canonical-tier chunk ranks above the reference-tier chunk")
def _then_canon_outranks(source_tier_ctx: _Ctx) -> None:
    by_path = {row.path: row.boosted_score for row in source_tier_ctx.boosted}
    canon_score = by_path["/ethos.md"]
    ref_score = by_path["/wiki/topic.md"]
    assert canon_score > ref_score, f"expected canonical > reference; canon={canon_score}, ref={ref_score}"


@then("neither chunk gains a tier multiplier")
def _then_no_multiplier(source_tier_ctx: _Ctx) -> None:
    # Both rows keep their original rrf_score (no multiplier applied).
    for row in source_tier_ctx.boosted:
        assert row.boosted_score == 0.5, f"expected pre-#432 score 0.5 for {row.path}; got {row.boosted_score}"

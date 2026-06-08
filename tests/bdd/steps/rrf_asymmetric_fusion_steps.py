"""Step definitions for rrf_asymmetric_fusion.feature (Issue #454).

These steps drive the RRF fusion directly via kairix.core.search.rrf.rrf —
no factory needed because RRF is a pure function over input lists. The
issue's design explicitly notes the change is a pure math refinement
inside ``_rrf_impl``; the BDD coverage proves the user-visible behaviour
(symmetric no-op, asymmetric rebalance, shorter-list non-zero) without
the factory wiring overhead.
"""

from pytest_bdd import given, then, when

from kairix.core.search.bm25 import BM25Result
from kairix.core.search.rrf import RRF_K, rrf
from kairix.core.search.vec_index import VecResult

_state: dict = {}


def _bm25(path: str, score: float = 1.0, collection: str = "c") -> BM25Result:
    return BM25Result(file=path, title="T", snippet="s", score=score, collection=collection)


def _vec(path: str, distance: float = 0.1, collection: str = "c") -> VecResult:
    return VecResult(
        hash_seq="h_0",
        distance=distance,
        path=path,
        collection=collection,
        title="T",
        snippet="s",
    )


# ---------------------------------------------------------------------------
# Given — set up BM25 + vector input lists
# ---------------------------------------------------------------------------


@given("a BM25 result list of length 3")
def bm25_length_3() -> None:
    _state["bm25"] = [_bm25("/a.md"), _bm25("/b.md"), _bm25("/c.md")]


@given("a vector result list of length 3")
def vec_length_3() -> None:
    _state["vec"] = [_vec("/a.md"), _vec("/b.md"), _vec("/c.md")]


@given('a BM25 result list of length 1 containing only "/bm25-top.md"')
def bm25_length_1_top() -> None:
    _state["bm25"] = [_bm25("/bm25-top.md")]


@given('a vector result list of length 5 with "/vec-top.md" at rank 1')
def vec_length_5_top() -> None:
    _state["vec"] = [
        _vec("/vec-top.md"),
        _vec("/v2.md"),
        _vec("/v3.md"),
        _vec("/v4.md"),
        _vec("/v5.md"),
    ]


@given('a BM25 result list of length 1 containing only "/bm25only.md"')
def bm25_length_1_only() -> None:
    _state["bm25"] = [_bm25("/bm25only.md")]


@given("a vector result list of length 5 with five distinct documents")
def vec_length_5_distinct() -> None:
    _state["vec"] = [
        _vec("/v1.md"),
        _vec("/v2.md"),
        _vec("/v3.md"),
        _vec("/v4.md"),
        _vec("/v5.md"),
    ]


# ---------------------------------------------------------------------------
# When — run the fusion
# ---------------------------------------------------------------------------


@when("I fuse the two lists with RRF")
def fuse() -> None:
    _state["results"] = rrf(_state["bm25"], _state["vec"])
    _state["by_path"] = {r.path: r for r in _state["results"]}


# ---------------------------------------------------------------------------
# Then — verify behaviour per scenario
# ---------------------------------------------------------------------------


@then("the per-document rrf_score equals the unnormalised Cormack 2009 score within float epsilon")
def symmetric_is_classic_cormack() -> None:
    """For symmetric input, every per-document score equals the unnormalised
    formula. Sabotage-prove: removing the w_bm25 / w_vec multipliers would
    keep this scenario passing (it's the regression-guard scenario)."""
    bp = _state["by_path"]
    # /a.md rank 1 in both → 2/(k+1)
    assert abs(bp["/a.md"].rrf_score - 2.0 / (RRF_K + 1)) < 1e-10
    # /b.md rank 2 in both → 2/(k+2)
    assert abs(bp["/b.md"].rrf_score - 2.0 / (RRF_K + 2)) < 1e-10
    # /c.md rank 3 in both → 2/(k+3)
    assert abs(bp["/c.md"].rrf_score - 2.0 / (RRF_K + 3)) < 1e-10


@then('"/vec-top.md" ranks above "/bm25-top.md"')
def vec_top_above_bm25_top() -> None:
    bp = _state["by_path"]
    assert bp["/vec-top.md"].rrf_score > bp["/bm25-top.md"].rrf_score


@then('"/vec-top.md" is the top result')
def vec_top_is_first() -> None:
    assert _state["results"][0].path == "/vec-top.md"


@then('"/bm25only.md" has a non-zero rrf_score')
def bm25only_non_zero() -> None:
    assert _state["by_path"]["/bm25only.md"].rrf_score > 0


@then("the rrf_score equals 0.2 multiplied by 1 over (k plus 1)")
def bm25only_score_value() -> None:
    bp = _state["by_path"]
    expected = 0.2 * 1.0 / (RRF_K + 1)
    assert abs(bp["/bm25only.md"].rrf_score - expected) < 1e-10

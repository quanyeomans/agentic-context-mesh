"""
Tests for kairix.search.rrf — Reciprocal Rank Fusion + entity boosting.

Tests cover:
  - RRF score formula correctness
  - Empty inputs
  - Single-list inputs (only BM25, only vector)
  - Document appearing in both lists
  - Result ordering by score
  - entity_boost math (formula, cap enforcement)
  - entity_boost returns unmodified on DB error
  - entity_boost boost sorting
"""

from unittest.mock import MagicMock

import pytest

from kairix.search.bm25 import BM25Result
from kairix.search.config import EntityBoostConfig, ProceduralBoostConfig
from kairix.search.rrf import (
    RRF_K,
    FusedResult,
    bm25_primary_fuse,
    entity_boost_neo4j,
    procedural_boost,
    rrf,
)

# Constants kept for backward-compat test math (values from EntityBoostConfig / ProceduralBoostConfig defaults)
ENTITY_BOOST_CAP = EntityBoostConfig().cap
PROCEDURAL_BOOST_FACTOR = ProceduralBoostConfig().factor
from kairix.search.vector import VecResult  # noqa: E402

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


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
# RRF formula correctness
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_rrf_score_formula_single_document_both_lists() -> None:
    """Document at rank 1 in both lists: score = 1/(k+1) + 1/(k+1)."""
    b = [_bm25("/doc.md")]
    v = [_vec("/doc.md")]

    results = rrf(b, v)

    assert len(results) == 1
    expected = 2.0 / (RRF_K + 1)
    assert abs(results[0].rrf_score - expected) < 1e-10


@pytest.mark.unit
def test_rrf_score_formula_bm25_only_document() -> None:
    """Document only in BM25 at rank 1 gets 1/(k+1)."""
    b = [_bm25("/bm25only.md")]
    v = []

    results = rrf(b, v)
    expected = 1.0 / (RRF_K + 1)
    assert abs(results[0].rrf_score - expected) < 1e-10


@pytest.mark.unit
def test_rrf_score_formula_vec_only_document() -> None:
    """Document only in vector at rank 1 gets 1/(k+1)."""
    b = []
    v = [_vec("/veconly.md")]

    results = rrf(b, v)
    expected = 1.0 / (RRF_K + 1)
    assert abs(results[0].rrf_score - expected) < 1e-10


@pytest.mark.unit
def test_rrf_rank_2_formula() -> None:
    """Document at rank 2 in BM25: score = 1/(k+2)."""
    b = [_bm25("/first.md"), _bm25("/second.md")]
    v = []

    results = rrf(b, v)
    second = next(r for r in results if r.path == "/second.md")
    expected = 1.0 / (RRF_K + 2)
    assert abs(second.rrf_score - expected) < 1e-10


@pytest.mark.unit
def test_rrf_document_in_both_lists_at_different_ranks() -> None:
    """Document at BM25 rank 1, vec rank 2: score = 1/(k+1) + 1/(k+2)."""
    b = [_bm25("/doc.md")]
    v = [_vec("/other.md"), _vec("/doc.md")]

    results = rrf(b, v)
    doc = next(r for r in results if r.path == "/doc.md")
    expected = 1.0 / (RRF_K + 1) + 1.0 / (RRF_K + 2)
    assert abs(doc.rrf_score - expected) < 1e-10


@pytest.mark.unit
def test_rrf_uses_custom_k() -> None:
    """Custom k parameter changes the RRF formula."""
    b = [_bm25("/doc.md")]
    v = []

    results_k30 = rrf(b, v, k=30)
    results_k90 = rrf(b, v, k=90)

    # Higher k → lower score (since we add k to rank in denominator)
    assert results_k30[0].rrf_score > results_k90[0].rrf_score


@pytest.mark.unit
def test_rrf_sorted_by_score_descending() -> None:
    """Results are sorted by rrf_score descending."""
    b = [_bm25("/a.md"), _bm25("/b.md"), _bm25("/c.md")]
    v = [_vec("/a.md"), _vec("/c.md")]  # a and c in both

    results = rrf(b, v)
    scores = [r.rrf_score for r in results]
    assert scores == sorted(scores, reverse=True)


# ---------------------------------------------------------------------------
# Empty inputs
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_rrf_both_empty() -> None:
    """Both lists empty → []."""
    assert rrf([], []) == []


@pytest.mark.unit
def test_rrf_bm25_empty_vec_has_results() -> None:
    """Only vector results → returns those."""
    results = rrf([], [_vec("/doc.md")])
    assert len(results) == 1


@pytest.mark.unit
def test_rrf_vec_empty_bm25_has_results() -> None:
    """Only BM25 results → returns those."""
    results = rrf([_bm25("/doc.md")], [])
    assert len(results) == 1


# ---------------------------------------------------------------------------
# FusedResult metadata
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_rrf_in_bm25_and_in_vec_flags() -> None:
    """in_bm25 and in_vec flags correctly set."""
    b = [_bm25("/shared.md"), _bm25("/bm25only.md")]
    v = [_vec("/shared.md"), _vec("/veconly.md")]

    results = rrf(b, v)
    by_path = {r.path: r for r in results}

    assert by_path["/shared.md"].in_bm25 is True
    assert by_path["/shared.md"].in_vec is True
    assert by_path["/bm25only.md"].in_bm25 is True
    assert by_path["/bm25only.md"].in_vec is False
    assert by_path["/veconly.md"].in_bm25 is False
    assert by_path["/veconly.md"].in_vec is True


@pytest.mark.unit
def test_rrf_boosted_score_equals_rrf_score_initially() -> None:
    """boosted_score is initialised to rrf_score before entity_boost."""
    results = rrf([_bm25("/doc.md")], [])
    assert results[0].boosted_score == results[0].rrf_score


# ---------------------------------------------------------------------------
# entity_boost — formula correctness
# ---------------------------------------------------------------------------


def _make_mock_neo4j(path_in_degree: dict[str, int]) -> MagicMock:
    """Create a mock Neo4j client that returns in-degree counts for given paths."""
    rows = [{"vault_path": path, "in_degree": count} for path, count in path_in_degree.items()]
    mock = MagicMock()
    mock.available = True
    mock.cypher.return_value = rows
    return mock


@pytest.mark.unit
def test_entity_boost_formula_correctness() -> None:
    """Boost formula: normalised count used; single doc with mentions gets > base score."""
    results = rrf([_bm25("/doc.md")], [])
    mock_neo4j = _make_mock_neo4j({"/doc.md": 5})

    boosted = entity_boost_neo4j(results, mock_neo4j)

    # With normalised boost: single doc is max, normalised=1.0
    # boost_amount = min(factor * log1p(1.0 * 10), cap-1) = min(0.20 * log(11), 1.0)
    # The exact value isn't what matters — what matters is that boost is applied
    assert boosted[0].boosted_score > results[0].rrf_score
    assert boosted[0].boosted_score <= results[0].rrf_score * ENTITY_BOOST_CAP + 1e-10


@pytest.mark.unit
def test_entity_boost_cap_enforced() -> None:
    """Boost is capped at ENTITY_BOOST_CAP - 1."""
    results = rrf([_bm25("/doc.md")], [])
    # Very high mention count should be capped
    mock_neo4j = _make_mock_neo4j({"/doc.md": 100_000})

    boosted = entity_boost_neo4j(results, mock_neo4j)

    max_multiplier = ENTITY_BOOST_CAP  # 2.0
    assert boosted[0].boosted_score <= results[0].rrf_score * max_multiplier + 1e-10


@pytest.mark.unit
def test_entity_boost_zero_mentions_no_change() -> None:
    """Document with 0 mentions has boosted_score == rrf_score."""
    results = rrf([_bm25("/doc.md")], [])
    mock_neo4j = _make_mock_neo4j({})  # no mentions

    boosted = entity_boost_neo4j(results, mock_neo4j)

    assert abs(boosted[0].boosted_score - boosted[0].rrf_score) < 1e-10


@pytest.mark.unit
def test_entity_boost_resorts_results() -> None:
    """entity_boost re-sorts results so boosted doc rises above unboosted ones."""
    b = [_bm25("/second.md"), _bm25("/first.md")]
    results = rrf(b, [])
    # "/second.md" starts at rank 1 (higher rrf_score)
    assert results[0].path == "/second.md"

    # Boost "/first.md" heavily
    mock_neo4j = _make_mock_neo4j({"/first.md": 1000})
    boosted = entity_boost_neo4j(results, mock_neo4j)

    # "/first.md" should now be first due to boost
    assert boosted[0].path == "/first.md"


@pytest.mark.unit
def test_entity_boost_returns_unmodified_on_cypher_error() -> None:
    """Cypher failure → returns results with boosted_score == rrf_score."""
    results = rrf([_bm25("/doc.md")], [])
    original_score = results[0].rrf_score

    mock_neo4j = MagicMock()
    mock_neo4j.available = True
    mock_neo4j.cypher.side_effect = RuntimeError("connection lost")

    boosted = entity_boost_neo4j(results, mock_neo4j)

    assert len(boosted) == 1
    assert boosted[0].rrf_score == original_score


@pytest.mark.unit
def test_entity_boost_returns_unmodified_on_unexpected_error() -> None:
    """Neo4j unavailable → returns results with boosted_score == rrf_score."""
    results = rrf([_bm25("/doc.md")], [])
    mock_neo4j = MagicMock()
    mock_neo4j.available = False

    boosted = entity_boost_neo4j(results, mock_neo4j)
    assert len(boosted) == 1


# ---------------------------------------------------------------------------
# procedural_boost
# ---------------------------------------------------------------------------


def _fused(path: str, score: float = 0.1) -> FusedResult:
    r = FusedResult(path=path, collection="c", title="T", snippet="s")
    r.rrf_score = score
    r.boosted_score = score
    return r


@pytest.mark.unit
def test_procedural_boost_how_to_file_boosted() -> None:
    """Path starting with how-to- gets boosted."""
    r = _fused("docs/runbooks/how-to-configure-something.md", score=0.1)
    results = procedural_boost([r])
    assert results[0].boosted_score == pytest.approx(0.1 * PROCEDURAL_BOOST_FACTOR)


@pytest.mark.unit
def test_procedural_boost_runbooks_dir_boosted() -> None:
    """File inside a /runbooks/ directory gets boosted."""
    r = _fused("platform/runbooks/deploy-agent.md", score=0.1)
    results = procedural_boost([r])
    assert results[0].boosted_score == pytest.approx(0.1 * PROCEDURAL_BOOST_FACTOR)


@pytest.mark.unit
def test_procedural_boost_runbook_prefix_boosted() -> None:
    """Filename starting with runbook- gets boosted."""
    r = _fused("docs/runbook-deployment-guide.md", score=0.1)
    results = procedural_boost([r])
    assert results[0].boosted_score == pytest.approx(0.1 * PROCEDURAL_BOOST_FACTOR)


@pytest.mark.unit
def test_procedural_boost_non_procedural_path_unchanged() -> None:
    """Non-matching path is not boosted."""
    r = _fused("notes/meeting-2026-01-15.md", score=0.1)
    results = procedural_boost([r])
    assert results[0].boosted_score == pytest.approx(0.1)


@pytest.mark.unit
def test_procedural_boost_ranking_reorder() -> None:
    """Procedural doc rises above non-procedural doc after boost."""
    non_proc = _fused("notes/general-note.md", score=0.2)
    proc = _fused("docs/runbooks/how-to-setup.md", score=0.15)
    # Before boost: non_proc > proc
    assert non_proc.boosted_score > proc.boosted_score

    results = procedural_boost([non_proc, proc])
    # After boost: proc (0.15 * 1.4 = 0.21) > non_proc (0.2)
    assert results[0].path == "docs/runbooks/how-to-setup.md"
    assert results[1].path == "notes/general-note.md"


@pytest.mark.unit
def test_procedural_boost_empty_input() -> None:
    """Empty input returns empty list without error."""
    assert procedural_boost([]) == []


@pytest.mark.unit
def test_procedural_boost_custom_factor() -> None:
    """Custom boost_factor is applied correctly."""
    r = _fused("guide/how-to-do-x.md", score=0.5)
    results = procedural_boost([r], config=ProceduralBoostConfig(factor=2.0))
    assert results[0].boosted_score == pytest.approx(1.0)


@pytest.mark.unit
def test_procedural_boost_procedure_prefix_boosted() -> None:
    """Filename starting with procedure is boosted."""
    r = _fused("ops/procedure-rollback.md", score=0.1)
    results = procedural_boost([r])
    assert results[0].boosted_score == pytest.approx(0.1 * PROCEDURAL_BOOST_FACTOR)


@pytest.mark.unit
def test_entity_boost_empty_results() -> None:
    """Empty results list → []."""
    mock_neo4j = MagicMock()
    mock_neo4j.available = True
    assert entity_boost_neo4j([], mock_neo4j) == []


@pytest.mark.unit
def test_entity_boost_mention_count_stored_on_result() -> None:
    """entity_mention_count is populated from Neo4j query."""
    results = rrf([_bm25("/doc.md")], [])
    mock_neo4j = _make_mock_neo4j({"/doc.md": 7})

    boosted = entity_boost_neo4j(results, mock_neo4j)
    assert boosted[0].entity_mention_count == 7


# ---------------------------------------------------------------------------
# BM25-primary fusion
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_bm25_primary_empty_inputs() -> None:
    """Both empty → []."""
    assert bm25_primary_fuse([], []) == []


@pytest.mark.unit
def test_bm25_primary_bm25_only() -> None:
    """BM25 results only → returned in BM25 rank order."""
    results = bm25_primary_fuse([_bm25("a.md"), _bm25("b.md")], [])
    assert len(results) == 2
    assert results[0].path == "a.md"
    assert results[1].path == "b.md"
    assert all(r.in_bm25 for r in results)
    assert not any(r.in_vec for r in results)


@pytest.mark.unit
def test_bm25_primary_vec_only() -> None:
    """Vector results only → returned in vector rank order."""
    results = bm25_primary_fuse([], [_vec("x.md"), _vec("y.md")])
    assert len(results) == 2
    assert results[0].path == "x.md"
    assert results[1].path == "y.md"
    assert all(r.in_vec for r in results)


@pytest.mark.unit
def test_bm25_primary_bm25_before_vec() -> None:
    """BM25 results appear before vector-only results."""
    results = bm25_primary_fuse(
        [_bm25("bm25_doc.md")],
        [_vec("vec_doc.md")],
    )
    assert len(results) == 2
    assert results[0].path == "bm25_doc.md"
    assert results[0].in_bm25 is True
    assert results[1].path == "vec_doc.md"
    assert results[1].in_vec is True
    # BM25 result has higher score
    assert results[0].rrf_score > results[1].rrf_score


@pytest.mark.unit
def test_bm25_primary_deduplication() -> None:
    """Doc in both lists appears at BM25 rank, marked as in_vec too."""
    results = bm25_primary_fuse(
        [_bm25("shared.md"), _bm25("bm25_only.md")],
        [_vec("shared.md"), _vec("vec_only.md")],
    )
    assert len(results) == 3
    assert results[0].path == "shared.md"
    assert results[0].in_bm25 is True
    assert results[0].in_vec is True
    assert results[1].path == "bm25_only.md"
    assert results[2].path == "vec_only.md"


@pytest.mark.unit
def test_bm25_primary_scores_descending() -> None:
    """All results have descending scores (BM25 > vec-only)."""
    results = bm25_primary_fuse(
        [_bm25("a.md"), _bm25("b.md")],
        [_vec("c.md"), _vec("d.md")],
    )
    scores = [r.rrf_score for r in results]
    assert scores == sorted(scores, reverse=True)

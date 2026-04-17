"""
Tests for kairix.search.rrf.temporal_date_boost (TMP-7).

Tests cover:
  - Exact date in path gets boosted when query contains that ISO date
  - YYYY-MM prefix match gets boosted when query contains ISO date
  - Non-matching date path is NOT boosted
  - Relative temporal term ("recently") boosts recent-dated paths
  - Non-TEMPORAL intent: temporal_date_boost not called (guard is caller's)
  - Empty results: no error, returns []
  - Non-dated path is unaffected when query has explicit date
  - boost_factor is applied correctly (multiplicative)
"""

import datetime

import pytest

from kairix.search.config import TemporalBoostConfig
from kairix.search.rrf import (
    FusedResult,
    temporal_date_boost,
)

# Constant kept for backward-compat test math (value from TemporalBoostConfig default)
TEMPORAL_DATE_BOOST_FACTOR = TemporalBoostConfig().date_path_boost_factor

# Config with date-path boost enabled (used in tests that expect active boosting)
_ENABLED_CONFIG = TemporalBoostConfig(date_path_boost_enabled=True)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _result(path: str, score: float = 0.5) -> FusedResult:
    return FusedResult(
        path=path,
        collection="test",
        title="Test Doc",
        snippet="some snippet",
        rrf_score=score,
        boosted_score=score,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_exact_date_in_path_gets_boosted() -> None:
    """A result whose path contains the queried ISO date is boosted."""
    results = [
        _result("daily/2026-03-22.md", score=0.5),
        _result("daily/2026-04-01.md", score=0.4),
    ]
    out = temporal_date_boost(results, query="2026-03-22 decisions", config=_ENABLED_CONFIG)

    # The 2026-03-22 result should be first and boosted
    assert out[0].path == "daily/2026-03-22.md"
    assert abs(out[0].boosted_score - 0.5 * TEMPORAL_DATE_BOOST_FACTOR) < 1e-9


@pytest.mark.unit
def test_year_month_prefix_match_gets_boosted() -> None:
    """A result whose path contains the YYYY-MM prefix of the queried ISO date is boosted."""
    results = [
        _result("notes/2026-03-22.md", score=0.5),
        _result("notes/general.md", score=0.6),  # higher base score, no date
    ]
    out = temporal_date_boost(results, query="2026-03-22 standup notes", config=_ENABLED_CONFIG)

    # notes/2026-03-22.md should be boosted above general.md despite lower base score
    boosted = next(r for r in out if r.path == "notes/2026-03-22.md")
    unboosted = next(r for r in out if r.path == "notes/general.md")
    assert boosted.boosted_score > unboosted.boosted_score


@pytest.mark.unit
def test_non_matching_date_not_boosted() -> None:
    """A result with a different date in its path is NOT boosted."""
    results = [
        _result("daily/2026-01-15.md", score=0.5),
        _result("daily/2026-03-22.md", score=0.4),
    ]
    out = temporal_date_boost(results, query="2026-03-22 Docker sandbox", config=_ENABLED_CONFIG)

    non_match = next(r for r in out if r.path == "daily/2026-01-15.md")
    assert abs(non_match.boosted_score - 0.5) < 1e-9  # unchanged


@pytest.mark.unit
def test_relative_temporal_boosts_recent_paths() -> None:
    """'recently' boosts results with a path date within the last 90 days."""
    today = datetime.date.today()
    recent_date = (today - datetime.timedelta(days=10)).isoformat()  # 10 days ago
    old_date = (today - datetime.timedelta(days=200)).isoformat()    # well outside window

    results = [
        _result(f"daily/{recent_date}.md", score=0.5),
        _result(f"daily/{old_date}.md", score=0.6),  # higher base but old
    ]
    out = temporal_date_boost(results, query="what happened recently", config=_ENABLED_CONFIG)

    recent_result = next(r for r in out if recent_date in r.path)
    old_result = next(r for r in out if old_date in r.path)

    assert recent_result.boosted_score > old_result.boosted_score
    assert abs(recent_result.boosted_score - 0.5 * TEMPORAL_DATE_BOOST_FACTOR) < 1e-9


@pytest.mark.unit
def test_empty_results_returns_empty_no_error() -> None:
    """temporal_date_boost on an empty list returns [] without raising."""
    out = temporal_date_boost([], query="2026-03-22 standup", config=_ENABLED_CONFIG)
    assert out == []


@pytest.mark.unit
def test_no_date_in_query_and_no_relative_term_unchanged() -> None:
    """If query has no ISO date and no relative term, all results are unmodified."""
    results = [
        _result("daily/2026-03-22.md", score=0.5),
        _result("notes/concept.md", score=0.4),
    ]
    original_scores = {r.path: r.boosted_score for r in results}
    out = temporal_date_boost(results, query="Docker sandbox setup", config=_ENABLED_CONFIG)

    for r in out:
        assert abs(r.boosted_score - original_scores[r.path]) < 1e-9


@pytest.mark.unit
def test_path_without_date_unaffected_when_explicit_date_in_query() -> None:
    """Non-dated paths are not boosted even when an explicit date is in the query."""
    results = [
        _result("2026-04-05-docker-sandbox.md", score=0.5),
        _result("architecture/design.md", score=0.4),
    ]
    out = temporal_date_boost(results, query="2026-04-05 Docker sandbox", config=_ENABLED_CONFIG)

    dated = next(r for r in out if "2026-04-05" in r.path)
    non_dated = next(r for r in out if "architecture" in r.path)

    assert abs(dated.boosted_score - 0.5 * TEMPORAL_DATE_BOOST_FACTOR) < 1e-9
    assert abs(non_dated.boosted_score - 0.4) < 1e-9  # unchanged


@pytest.mark.unit
def test_boost_factor_is_multiplicative() -> None:
    """Boosted score equals original rrf_score * boost_factor (not additive)."""
    results = [_result("logs/2026-03-22.md", score=0.8)]
    out = temporal_date_boost(results, query="2026-03-22", config=_ENABLED_CONFIG)

    assert abs(out[0].boosted_score - 0.8 * TEMPORAL_DATE_BOOST_FACTOR) < 1e-9


@pytest.mark.unit
def test_year_month_only_query_boosts_matching_paths() -> None:
    """Query with YYYY-MM (no day) boosts paths containing that YYYY-MM prefix."""
    results = [
        _result("monthly/2026-03-summary.md", score=0.5),
        _result("monthly/2026-04-summary.md", score=0.4),
    ]
    out = temporal_date_boost(results, query="what happened in 2026-03", config=_ENABLED_CONFIG)

    march = next(r for r in out if "2026-03" in r.path)
    april = next(r for r in out if "2026-04" in r.path)

    assert march.boosted_score > april.boosted_score
    assert abs(march.boosted_score - 0.5 * TEMPORAL_DATE_BOOST_FACTOR) < 1e-9

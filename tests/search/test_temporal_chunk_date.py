"""Tests for TMP-7B chunk_date proximity boost."""
import datetime

import pytest

from kairix.search.config import TemporalBoostConfig
from kairix.search.rrf import FusedResult, chunk_date_boost


def _make(path: str, score: float, chunk_date: str = "") -> FusedResult:
    r = FusedResult(path=path, collection="test", title="T", snippet="S")
    r.rrf_score = score
    r.boosted_score = score
    r.chunk_date = chunk_date
    return r


_ENABLED = TemporalBoostConfig(chunk_date_boost_enabled=True, chunk_date_decay_halflife_days=30)
_TODAY = datetime.date(2026, 4, 17)


class TestChunkDateBoost:
    def test_disabled_returns_results_unchanged(self):
        results = [_make("notes/recent.md", 0.5, "2026-04-15")]
        cfg = TemporalBoostConfig(chunk_date_boost_enabled=False)
        out = chunk_date_boost(results, _TODAY, config=cfg)
        assert out[0].boosted_score == pytest.approx(0.5)

    def test_no_query_date_returns_results_unchanged(self):
        results = [_make("notes/recent.md", 0.5, "2026-04-15")]
        out = chunk_date_boost(results, None, config=_ENABLED)
        assert out[0].boosted_score == pytest.approx(0.5)

    def test_recent_doc_boosted_more_than_old(self):
        results = [
            _make("notes/recent.md", 0.5, "2026-04-15"),   # 2 days ago
            _make("notes/old.md", 0.5, "2025-01-01"),       # ~16 months ago
        ]
        out = chunk_date_boost(results, _TODAY, config=_ENABLED)
        recent = next(r for r in out if "recent" in r.path)
        old = next(r for r in out if "old" in r.path)
        assert recent.boosted_score > old.boosted_score

    def test_doc_with_no_chunk_date_not_boosted(self):
        results = [
            _make("notes/recent.md", 0.5, "2026-04-15"),
            _make("notes/no_date.md", 0.5, ""),
        ]
        out = chunk_date_boost(results, _TODAY, config=_ENABLED)
        no_date = next(r for r in out if "no_date" in r.path)
        # no_date doc should be unchanged (boost factor = 1.0 * score)
        assert no_date.boosted_score == pytest.approx(0.5)

    def test_exact_date_match_max_boost(self):
        results = [_make("notes/today.md", 0.5, "2026-04-17")]
        out = chunk_date_boost(results, _TODAY, config=_ENABLED)
        # At delta=0, boost = 1 + exp(0) = 2.0
        assert out[0].boosted_score == pytest.approx(0.5 * 2.0, rel=1e-5)

    def test_halflife_decay(self):
        # At delta = halflife (30 days), boost should be approximately 1 + 0.5 = 1.5
        results = [_make("notes/month_ago.md", 1.0, "2026-03-18")]  # 30 days before today
        out = chunk_date_boost(results, _TODAY, config=_ENABLED)
        # exp(-30²/(2*(30/1.177)²)) ≈ 0.5 → boost ≈ 1.5
        assert 1.3 < out[0].boosted_score < 1.7

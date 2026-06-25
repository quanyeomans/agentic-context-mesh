"""PR#3: embed-truncation-risk flagging in chunk-stats (ADR-028 measurement baseline).

The chunk-size distribution already surfaces mean/p50/p95/p99, but oversized
chunks live in the tail (a whole-document "chunk" that was never split). These
silently truncate at the text-embedding-3-large 8191-token limit, so their tail
never reaches retrieval. ``truncation_risk`` / ``render_truncation_warning``
surface which source types are affected and point at the per-type chunker
cutover that bounds them.
"""

from __future__ import annotations

import pytest

from kairix.quality.eval.chunk_stats import render_truncation_warning, truncation_risk

pytestmark = pytest.mark.unit


def test_truncation_risk_flags_only_oversized_types_worst_first() -> None:
    by_type = {
        "projects": [800, 900_000],  # one whole-doc chunk far over budget
        "entity-summaries": [400_000],
        "sharepoint": [800, 1000],  # bounded — excluded
    }
    assert truncation_risk(by_type) == [("projects", 900_000), ("entity-summaries", 400_000)]


def test_truncation_risk_empty_when_all_bounded() -> None:
    assert truncation_risk({"sharepoint": [800, 1000], "linear": [700, 715]}) == []


def test_render_warning_empty_when_bounded() -> None:
    assert render_truncation_warning({"sharepoint": [800, 1000]}) == ""


def test_render_warning_lists_offenders_and_recommends_flag() -> None:
    out = render_truncation_warning({"projects": [900_000], "entity-summaries": [400_000]})
    assert "projects" in out
    assert "entity-summaries" in out
    assert "embed-truncation risk" in out
    assert "chunker_registry_dispatch_enabled" in out  # points at the PR#6 cutover


def test_custom_budget_respected() -> None:
    by_type = {"foo": [5000]}
    assert truncation_risk(by_type, char_budget=10_000) == []  # under custom budget
    assert truncation_risk(by_type, char_budget=4000) == [("foo", 5000)]  # over custom budget

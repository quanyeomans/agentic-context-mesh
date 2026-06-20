"""Load-and-shape test for the gold task->capability recommender suite.

This guards ``kairix/data/suites/recommender.yaml`` — the forward-looking
(F75-PROPOSED, non-blocking) benchmark suite that makes recommendation
precision measurable. It asserts the suite loads through the canonical
benchmark loader, carries enough gold cases, uses the ``exact`` score
method throughout, and that every gold capability name names a REAL
``tool_capabilities()`` capability (so a typo in a gold title is caught
here instead of silently scoring zero forever).

Sabotage-proof (executed): changed one ``gold_titles[].title`` in
recommender.yaml from ``contradict`` to ``contradikt`` (a bogus name) and
``test_recommender_gold_titles_are_real_capabilities`` FAILED with
``unknown capability name(s): {'contradikt'}``; restored to ``contradict``
and the test passes again.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.unit

_VALID_CATEGORIES = {
    "recall",
    "temporal",
    "entity",
    "conceptual",
    "multi_hop",
    "procedural",
    "classification",
}


def _load_recommender_suite():
    from kairix.quality.benchmark.suite import load_suite, resolve_suite_path

    return load_suite(str(resolve_suite_path("recommender")))


def test_recommender_suite_loads():
    suite = _load_recommender_suite()

    assert len(suite.cases) >= 5
    for case in suite.cases:
        assert case.score_method == "exact", f"{case.id} not exact"
        assert case.category in _VALID_CATEGORIES, f"{case.id} bad category {case.category!r}"


def test_recommender_gold_titles_are_real_capabilities():
    from kairix.agents.mcp.server import tool_capabilities

    real_names = {cap["name"] for cap in tool_capabilities()["capabilities"]}

    gold_names: set[str] = set()
    for case in _load_recommender_suite().cases:
        for gold in case.gold_titles or []:
            gold_names.add(str(gold["title"]))

    assert gold_names, "suite declared no gold titles"
    unknown = gold_names - real_names
    assert not unknown, f"unknown capability name(s): {unknown}"

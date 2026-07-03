"""Outcome test for #437 — prep enumerates a full list, not just top snippets.

Wires three real kairix components together, no ``@patch`` of internals:

  - the prep use case (``run_prep``) drives retrieval → context → chat;
  - the REAL chunk-expansion backbone (``run_expand``) pulls the dominant
    source's complete ordered chunk set through a seeded repository;
  - source-cohesion enumeration completion (``kairix.use_cases.enumeration``)
    detects that the top hits cohere on one enumerable source and splices its
    full list into the LLM context.

The defect (2026-06-07 pretotyping dogfood): a stored methodology reference
listing 7 techniques returned only the top few because retrieval stopped at
the top-N snippets. This test seeds that exact shape — a single source whose
chunks 5-6 hold techniques 6-7 that never rank into the top-5 — and asserts
prep now surfaces the COMPLETE catalogue.

The chat seam echoes the LLM context back as the summary, so the assertion is
directly on "what synthesis was grounded in" without a live model.
"""

from __future__ import annotations

from typing import Any

import pytest

from kairix.core.search.budget import BudgetedResult
from kairix.core.search.intent import QueryIntent
from kairix.core.search.pipeline import SearchResult
from kairix.core.search.rrf import FusedResult
from kairix.use_cases.expand import ExpandDeps, run_expand
from kairix.use_cases.prep import PrepDeps, reset_prep_summary_cache, run_prep
from tests.fakes import FakeDocumentRepository

pytestmark = pytest.mark.integration

_SOURCE = "reflib://pretotyping-methods.md"
# The full technique catalogue, one per chunk. Chunks 0-4 rank into the top-5;
# chunks 5-6 (the last two techniques) only reach synthesis via enumeration
# completion, so they are the load-bearing items.
_TECHNIQUES = [
    "Mechanical Turk",
    "Pinocchio",
    "Stripped Tease",
    "Provincial",
    "Fake Door",
    "Pretend-to-Own",
    "Re-label",
]


def _bullet(seq: int) -> str:
    name = _TECHNIQUES[seq]
    return f"- {name}: a pretotyping technique for validating demand before you build the real thing."


def _seed_repository() -> FakeDocumentRepository:
    """One source chunked into 7 bulleted technique rows (``<source>#<seq>``)."""
    docs = [
        {
            "path": f"{_SOURCE}#{seq}",
            "collection": "reference",
            "title": "Pretotyping Methods",
            "content": _bullet(seq),
        }
        for seq in range(len(_TECHNIQUES))
    ]
    return FakeDocumentRepository(documents=docs)


def _top5_search_result(query: str) -> SearchResult:
    """A SearchResult whose top-5 hits are chunks 0-4 of the one source.

    Techniques 6 and 7 (chunks 5-6) are deliberately ABSENT from the ranked
    results — retrieval clipped them — so only enumeration completion can
    surface them.
    """
    results = []
    for seq in range(5):
        fused = FusedResult(
            path=f"{_SOURCE}#{seq}",
            collection="reference",
            title="Pretotyping Methods",
            snippet=_bullet(seq),
            rrf_score=0.9 - seq * 0.1,
            boosted_score=0.9 - seq * 0.1,
            in_bm25=True,
            source_uri=_SOURCE,
            seq=seq,
        )
        results.append(BudgetedResult(result=fused, tier="L2", token_estimate=40, content=_bullet(seq)))
    return SearchResult(query=query, intent=QueryIntent.SEMANTIC, results=results, total_tokens=200)


def _echo_chat(**kwargs: Any) -> str:
    """Return the LLM user message verbatim so the test asserts on the grounding context."""
    return str(kwargs["messages"][1]["content"])


def _expand_over(repo: FakeDocumentRepository) -> Any:
    def expand_fn(source_uri: str) -> Any:
        return run_expand(
            source_uri,
            token_budget=12000,
            deps=ExpandDeps(get_chunk=repo.get_by_path, list_chunk_seqs=repo.list_chunk_seqs),
        )

    return expand_fn


def test_prep_surfaces_every_technique_from_an_enumerable_source() -> None:
    """#437 — prep returns the COMPLETE list of 7 techniques, not the top-5.

    Sabotage-proof (executed 2026-07-03): removing the
    ``_with_completed_enumeration`` call in ``run_prep`` reverts prep to the
    top-5 snippets and this test fails on the ``Pretend-to-Own`` /
    ``Re-label`` assertions (chunks 5-6 never ranked into the top-5); restoring
    the call turns it green again.
    """
    reset_prep_summary_cache()
    repo = _seed_repository()

    def _search(**kwargs: Any) -> SearchResult:
        return _top5_search_result(str(kwargs.get("query", "")))

    deps = PrepDeps(search_fn=_search, chat_fn=_echo_chat, expand_fn=_expand_over(repo))
    out = run_prep("what pretotyping techniques are there", tier="l1", deps=deps)

    assert out.error == ""
    for name in _TECHNIQUES:
        assert name in out.summary, f"prep dropped an enumerated technique: {name!r}"
    # The two clipped techniques prove the fix is load-bearing — they are NOT
    # in the ranked top-5 and reach synthesis only via enumeration completion.
    assert "Pretend-to-Own" in out.summary
    assert "Re-label" in out.summary


def test_prep_leaves_non_cohesive_results_untouched() -> None:
    """Regression guard: when the top hits span different sources, prep does
    NOT expand — today's top-N behaviour is preserved and expand never runs.

    Sabotage-proof (executed): pointing ``expand_fn`` at a runner that raises
    keeps this test GREEN, proving the expansion path is skipped when no single
    source dominates the results.
    """
    reset_prep_summary_cache()

    def _boom_expand(_source_uri: str) -> Any:
        raise AssertionError("expand must not run when results span multiple sources")

    long_snippet = "This document covers the subject in enough detail to clear the snippet floor comfortably."
    results = [
        BudgetedResult(
            result=FusedResult(
                path=f"reflib://doc-{i}.md",
                collection="reference",
                title=f"Doc {i}",
                snippet=long_snippet,
                rrf_score=0.8 - i * 0.1,
                boosted_score=0.8 - i * 0.1,
                in_bm25=True,
                source_uri=f"reflib://doc-{i}.md",
            ),
            tier="L2",
            token_estimate=40,
            content=long_snippet,
        )
        for i in range(3)
    ]
    sr = SearchResult(query="topic", intent=QueryIntent.SEMANTIC, results=results, total_tokens=120)

    deps = PrepDeps(search_fn=lambda **_: sr, chat_fn=_echo_chat, expand_fn=_boom_expand)
    out = run_prep("topic", tier="l0", deps=deps)

    assert out.error == ""
    assert "Doc 0" in out.summary

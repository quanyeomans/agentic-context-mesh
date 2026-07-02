"""Composed outcome tests for the #468 tri-state contradict verdict.

The single ``has_contradictions`` boolean used to fire on the *absence* of
evidence — an unrelated snippet that failed to support a claim was reported
as an overstatement/contradiction. #468 splits the verdict into three:

  - ``CONTRADICTION`` — the store holds evidence that directly conflicts.
  - ``UNSUPPORTED``  — the store holds related content but nothing probative.
  - ``NOT_FOUND``    — the store holds nothing relevant at all.

Each test composes a REAL ``SearchPipeline`` through
``kairix.core.factory.build_search_pipeline`` over a seeded corpus (the
retrieval half is production code), wires it into the production
``check_contradiction`` detector + composite scorer, and drives the whole
thing through ``run_contradict``. Only the two true boundaries are faked:
the corpus (seeded ``FakeDocumentRepository`` rows through the factory's DI
seams) and the LLM verdict (``FakeLLMBackend``). The classification path —
detector → report → ``_classify_outcome`` → envelope — is all real.

The three outcomes are steered by exactly two composed inputs:
  - whether the seeded corpus surfaces a candidate for the claim, and
  - whether the fake LLM scores that candidate as a contradiction.

Everything is hermetic: the pipeline is built against ``FakePaths()`` (no
real data dir, no ``KAIRIX_*`` env), non-default ``FactoryDeps`` bypass the
process pipeline cache, and no network / disk is touched.
"""

from __future__ import annotations

from typing import Any

import pytest

from kairix.core.factory import (
    QUERY_CACHE_DISABLED,
    FactoryDeps,
    build_search_pipeline,
)
from kairix.core.search.config import RetrievalConfig
from kairix.core.search.fusion import RRFFusion
from kairix.core.search.intent import QueryIntent
from kairix.knowledge.contradict.detector import (
    ContradictDetectorDeps,
    check_contradiction,
)
from kairix.knowledge.contradict.extract import EntityDensityClaimExtractor
from kairix.knowledge.contradict.scorers import default_contradiction_scorer
from kairix.use_cases.contradict import (
    ContradictDeps,
    ContradictionOutcome,
    run_contradict,
)
from tests.fakes import (
    FakeClassifier,
    FakeCollectionResolver,
    FakeDocumentRepository,
    FakeEmbeddingService,
    FakeGraphRepository,
    FakeLLMBackend,
    FakePaths,
    FakeSearchLogger,
    FakeVectorRepository,
)

pytestmark = pytest.mark.integration


# A complete BM25Result-shaped row about OpenClaw. ``bm25_rows`` mode returns
# it verbatim for any query, so the composed pipeline reliably surfaces one
# candidate regardless of which claim the extractor picks. ``collection`` is
# set so the row survives whether the resolved collection filter is ``None``
# or the default ``["shared"]``.
_SEEDED_ROW: dict[str, Any] = {
    "path": "decisions/openclaw-status.md",
    "file": "decisions/openclaw-status.md",
    "collection": "shared",
    "title": "OpenClaw status",
    "content": "OpenClaw is the active orchestration platform for all agents.",
    "snippet": "OpenClaw is the active orchestration platform for all agents.",
    "score": 1.0,
    "source_page": None,
    "source_uri": "",
}

_CLAIM = "OpenClaw has been deprecated and is no longer used for agent orchestration."


def _composed_search(bm25_rows: list[dict[str, Any]]) -> Any:
    """Build a REAL SearchPipeline via the factory over a seeded corpus.

    Only the corpus / embedding / graph boundaries are faked — the fusion,
    budget, and orchestration are production code, wired through the same
    ``FactoryDeps`` DI seams the production factory uses.
    """
    return build_search_pipeline(
        config=RetrievalConfig.defaults(),
        paths=FakePaths(),
        deps=FactoryDeps(
            classifier_override=FakeClassifier(intent=QueryIntent.KEYWORD),
            doc_repo_override=FakeDocumentRepository(bm25_rows=bm25_rows),
            embed_service_override=FakeEmbeddingService(dim=8),
            vec_repo_override=FakeVectorRepository(results=[]),
            graph_override=FakeGraphRepository(available=False),
            fusion_override=RRFFusion(k=60),
            boosts_override=[],
            logger_override=FakeSearchLogger(),
            resolver_override=FakeCollectionResolver(),
            query_cache_override=QUERY_CACHE_DISABLED,
        ),
    )


def _run(content: str, *, bm25_rows: list[dict[str, Any]], llm: FakeLLMBackend) -> Any:
    """Drive ``run_contradict`` over the composed pipeline + real detector."""
    pipeline = _composed_search(bm25_rows)
    scorer = default_contradiction_scorer(llm)
    deps = ContradictDeps(
        check_fn=lambda **kw: check_contradiction(
            **kw,
            deps=ContradictDetectorDeps(
                search=pipeline.search,
                scorer=scorer,
                extractor=EntityDensityClaimExtractor(),
            ),
        ),
        llm_backend=llm,
    )
    return run_contradict(content, top_k=5, threshold=0.45, top_claims=3, deps=deps)


def test_outcome_contradiction_when_seeded_evidence_conflicts() -> None:
    """Store holds a conflicting doc + the LLM scores it a strong contradiction
    → ``CONTRADICTION`` (and ``has_contradictions`` True, one hit surfaced).

    Sabotage-proof (executed): in ``_classify_outcome`` return
    ``ContradictionOutcome.UNSUPPORTED`` instead of ``CONTRADICTION`` when
    ``report.hits`` is non-empty → this test fails (contradiction became
    unsupported and ``has_contradictions`` flipped to False). Restored.
    """
    llm = FakeLLMBackend(chat_response='{"score": 0.9, "reason": "asserts OpenClaw is active, not deprecated"}')

    out = _run(_CLAIM, bm25_rows=[_SEEDED_ROW], llm=llm)

    assert out.error == ""
    assert out.outcome is ContradictionOutcome.CONTRADICTION
    assert out.has_contradictions is True
    assert len(out.contradictions) == 1
    assert out.contradictions[0].path == "decisions/openclaw-status.md"


def test_outcome_unsupported_when_related_content_is_not_probative() -> None:
    """Store surfaces a related doc but the LLM finds it neither supports nor
    refutes the claim → ``UNSUPPORTED``: related content, nothing probative.
    ``has_contradictions`` stays False and no hit is fabricated.

    Sabotage-proof (executed): in ``_classify_outcome`` drop the
    ``candidates_considered`` branch (return ``NOT_FOUND`` whenever
    ``report.hits`` is empty) → this test fails (unsupported became
    not_found). Restored.
    """
    llm = FakeLLMBackend(chat_response='{"score": 0.0, "reason": ""}')

    out = _run(_CLAIM, bm25_rows=[_SEEDED_ROW], llm=llm)

    assert out.error == ""
    assert out.outcome is ContradictionOutcome.UNSUPPORTED
    assert out.has_contradictions is False
    assert out.contradictions == []


def test_outcome_not_found_when_store_has_no_relevant_content() -> None:
    """Empty corpus surfaces no candidate at all → ``NOT_FOUND``. The LLM is
    never consulted because there is nothing to score.

    Sabotage-proof (executed): in ``_classify_outcome`` return
    ``ContradictionOutcome.UNSUPPORTED`` unconditionally when ``report.hits``
    is empty → this test fails (not_found became unsupported). Restored.
    """
    llm = FakeLLMBackend(chat_response='{"score": 0.9, "reason": "would flag if it ran"}')

    out = _run(_CLAIM, bm25_rows=[], llm=llm)

    assert out.error == ""
    assert out.outcome is ContradictionOutcome.NOT_FOUND
    assert out.has_contradictions is False
    assert out.contradictions == []
    # The scorer never fired — no candidate to score against.
    assert llm.chat_calls == []

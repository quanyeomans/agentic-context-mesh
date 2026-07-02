"""Unit tests for ``kairix.use_cases.contradict.run_contradict``."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from kairix.core.search.scope import Scope
from kairix.knowledge.contradict.detector import ContradictionReport
from kairix.use_cases.contradict import (
    ContradictDeps,
    ContradictionHit,
    ContradictionOutcome,
    ContradictOutput,
    contradict_output_to_envelope,
    run_contradict,
)


@dataclass
class _FakeContradictionResult:
    doc_path: str = ""
    score: float = 0.0
    reason: str = ""
    snippet: str = ""
    category: str = "direct"
    claim: str = ""


class _FakeLLM:
    def chat(self, messages: list[dict]) -> str:
        return "{}"


def _build_deps(
    *,
    results: list[_FakeContradictionResult] | None = None,
    candidates_considered: int | None = None,
    raises: bool = False,
) -> tuple[ContradictDeps, dict[str, Any]]:
    captured: dict[str, Any] = {}

    def fake_check(**kwargs: Any) -> ContradictionReport:
        captured.update(kwargs)
        if raises:
            raise RuntimeError("boom")
        return ContradictionReport.of(results or [], candidates_considered=candidates_considered)

    return ContradictDeps(check_fn=fake_check, llm_backend=_FakeLLM()), captured


# ---------------------------------------------------------------------------
# Defaults / shape
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_contradiction_hit_default_optionals() -> None:
    h = ContradictionHit(path="p", score=0.5, reason="r", snippet="s")
    assert h.category == ""
    assert h.claim == ""


@pytest.mark.unit
def test_contradict_output_default_results_is_empty_list() -> None:
    out = ContradictOutput(content="c")
    assert out.contradictions == []
    assert out.has_contradictions is False
    assert out.error == ""


# ---------------------------------------------------------------------------
# Happy path projection
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_results_projected_into_contradiction_hits() -> None:
    fake = _FakeContradictionResult(
        doc_path="docs/old.md",
        score=0.78,
        reason="contradicts X",
        snippet="The system uses option A.",
        category="status_mismatch",
        claim="The system now uses option B.",
    )
    deps, _ = _build_deps(results=[fake])
    out = run_contradict("System now uses B", deps=deps)

    assert out.has_contradictions is True
    assert out.outcome is ContradictionOutcome.CONTRADICTION
    assert len(out.contradictions) == 1
    h = out.contradictions[0]
    assert h.path == "docs/old.md"
    assert h.score == pytest.approx(0.78)
    assert h.reason == "contradicts X"
    assert h.category == "status_mismatch"
    assert h.claim == "The system now uses option B."


@pytest.mark.unit
def test_no_results_yields_no_contradictions() -> None:
    deps, _ = _build_deps(results=[])
    out = run_contradict("benign content", deps=deps)
    assert out.has_contradictions is False
    assert out.contradictions == []


# ---------------------------------------------------------------------------
# Tri-state outcome classification (#468)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_unsupported_when_candidates_retrieved_but_none_contradict() -> None:
    """Candidates were retrieved and scored but none rose to a contradiction:
    the store holds related content but nothing probative → ``UNSUPPORTED``,
    NOT ``has_contradictions`` and NOT ``NOT_FOUND``.

    Sabotage-proof (executed): collapse the tri-state by returning
    ``ContradictionOutcome.NOT_FOUND`` whenever ``report.hits`` is empty in
    ``_classify_outcome`` (dropping the ``candidates_considered`` branch) →
    this assertion fires (unsupported became not_found). Restored.
    """
    deps, _ = _build_deps(results=[], candidates_considered=3)
    out = run_contradict("a claim the store is silent on", deps=deps)
    assert out.outcome is ContradictionOutcome.UNSUPPORTED
    assert out.has_contradictions is False
    assert out.contradictions == []


@pytest.mark.unit
def test_not_found_when_no_candidates_retrieved() -> None:
    """Zero candidates retrieved → the store holds nothing relevant → ``NOT_FOUND``."""
    deps, _ = _build_deps(results=[], candidates_considered=0)
    out = run_contradict("a claim on an unknown topic", deps=deps)
    assert out.outcome is ContradictionOutcome.NOT_FOUND
    assert out.has_contradictions is False
    assert out.contradictions == []


# ---------------------------------------------------------------------------
# Param pass-through
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_top_k_threshold_top_claims_pass_through() -> None:
    deps, captured = _build_deps()
    run_contradict("c", top_k=8, threshold=0.6, top_claims=4, deps=deps)
    assert captured["top_k"] == 8
    assert captured["threshold"] == pytest.approx(0.6)
    assert captured["top_claims"] == 4


@pytest.mark.unit
def test_scope_passed_through_unconditionally() -> None:
    deps, captured = _build_deps()
    run_contradict("c", scope=Scope.ALL_AGENTS, deps=deps)
    assert captured["scope"] is Scope.ALL_AGENTS


@pytest.mark.unit
def test_agent_only_passed_when_explicitly_set() -> None:
    deps, captured = _build_deps()
    run_contradict("c", agent="builder", deps=deps)
    assert captured["agent"] == "builder"


@pytest.mark.unit
def test_agent_omitted_from_check_call_when_none() -> None:
    deps, captured = _build_deps()
    run_contradict("c", agent=None, deps=deps)
    assert "agent" not in captured  # legacy WS2-B contract: omit, don't pass None


# ---------------------------------------------------------------------------
# Error path
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_check_failure_yields_error_envelope() -> None:
    deps, _ = _build_deps(raises=True)
    out = run_contradict("c", deps=deps)
    assert out.error.startswith("RuntimeError:")
    assert out.contradictions == []
    assert out.has_contradictions is False


# ---------------------------------------------------------------------------
# Envelope projection
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_envelope_includes_category_and_claim() -> None:
    out = ContradictOutput(
        content="c",
        contradictions=[
            ContradictionHit(path="p", score=0.5, reason="r", snippet="s", category="overstatement", claim="C")
        ],
        has_contradictions=True,
        outcome=ContradictionOutcome.CONTRADICTION,
    )
    env = contradict_output_to_envelope(out)
    assert env["content"] == "c"
    assert env["has_contradictions"] is True
    assert env["outcome"] == "contradiction"
    assert env["error"] == ""
    # PLA-274 — the contradicting source's breadcrumb rides on the envelope
    # (title / collection / source_page / source_uri / locator); source_uri
    # falls back to the path when no connector URI is present.
    assert env["contradictions"] == [
        {
            "path": "p",
            "score": 0.5,
            "reason": "r",
            "snippet": "s",
            "category": "overstatement",
            "claim": "C",
            "title": "",
            "collection": "",
            "source_page": None,
            "source_uri": "p",
            "locator": None,
        }
    ]


@pytest.mark.unit
def test_envelope_carries_error_when_present() -> None:
    out = ContradictOutput(content="c", error="ConnectionError: Neo4j down")
    env = contradict_output_to_envelope(out)
    assert env["error"].startswith("ConnectionError")
    assert env["has_contradictions"] is False
    assert env["contradictions"] == []

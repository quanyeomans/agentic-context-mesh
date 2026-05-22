"""F30 outcome test — MCP ``contradict`` tool.

``tool_contradict`` (kairix/agents/mcp/server.py:424) is the thin
adapter around ``kairix.use_cases.contradict.run_contradict``. The use
case extracts up to ``top_claims`` candidate claims from incoming
content, asks ``check_fn`` to compare each against the existing
knowledge store, and returns the contradicting documents with scores
and reasons.

The F30 contract for MCP tools (``scripts/checks/check_f30_operator_outcome_tests.py``):
call ``tool_<name>`` directly and assert on returned-envelope content
via Subscript/Attribute access — NOT on internal call-counts.

DI seam: existing ``deps`` kwarg on ``tool_contradict`` (server.py:424-455).
Tests construct ``ContradictDeps(check_fn=fake, llm_backend=fake)`` so
the envelope is deterministic — no LLM call, no FTS / vector
backend, no real contradiction detector.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from kairix.agents.mcp.server import tool_contradict
from kairix.use_cases.contradict import ContradictDeps

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Local lightweight detector result + LLM stand-ins
#
# Mirrors the pattern in tests/use_cases/test_contradict.py. The use
# case reads ``r.doc_path``, ``r.score``, ``r.reason``, ``r.snippet``,
# ``r.category``, ``r.claim`` via getattr — the dataclass below
# provides every field with a default so older detectors round-trip.
# ---------------------------------------------------------------------------


@dataclass
class _FakeContradictionResult:
    doc_path: str = ""
    score: float = 0.0
    reason: str = ""
    snippet: str = ""
    category: str = "direct"
    claim: str = ""


class _FakeLLM:
    """LLM backend stub — never invoked when ``check_fn`` doesn't reach it."""

    def chat(self, messages: list[dict[str, Any]], max_tokens: int = 0) -> str:
        return "{}"


# ---------------------------------------------------------------------------
# Outcome tests
# ---------------------------------------------------------------------------


def test_tool_contradict_envelope_carries_hits_and_has_contradictions_flag() -> None:
    """``tool_contradict`` projects detector results into the envelope.

    Drives the production happy path: one contradicting document
    returned by the fake detector. The envelope must surface
    ``contradictions`` (list of hit dicts), ``has_contradictions``
    (boolean True), ``content`` (unchanged), and ``error`` (empty).

    Sabotage: mutate ``hits = [_project(r) for r in results]`` →
    ``hits = []`` in ``run_contradict`` → envelope["contradictions"]
    empty and ``has_contradictions`` False; both assertions below fail.
    Verified.
    """
    fake_result = _FakeContradictionResult(
        doc_path="docs/old-decision.md",
        score=0.78,
        reason="contradicts the new claim",
        snippet="The system uses option A.",
        category="status_mismatch",
        claim="The system uses option B.",
    )

    captured: dict[str, Any] = {}

    def fake_check(**kwargs: Any) -> list[_FakeContradictionResult]:
        captured.update(kwargs)
        return [fake_result]

    deps = ContradictDeps(check_fn=fake_check, llm_backend=_FakeLLM())
    envelope = tool_contradict(content="System now uses option B", top_k=3, deps=deps)

    assert isinstance(envelope, dict)
    assert envelope["content"] == "System now uses option B", f"content mismatch: {envelope['content']!r}"
    assert envelope["has_contradictions"] is True, f"has_contradictions must be True when hits exist: {envelope!r}"
    assert envelope["error"] == "", f"error must be empty on happy path: {envelope['error']!r}"
    assert len(envelope["contradictions"]) == 1, f"expected 1 hit: {envelope['contradictions']!r}"

    hit = envelope["contradictions"][0]
    assert hit["path"] == "docs/old-decision.md", f"hit path mismatch: {hit!r}"
    assert hit["score"] == pytest.approx(0.78), f"hit score mismatch: {hit!r}"
    assert hit["reason"] == "contradicts the new claim", f"hit reason mismatch: {hit!r}"
    assert hit["category"] == "status_mismatch", f"hit category mismatch: {hit!r}"
    assert hit["claim"] == "The system uses option B.", f"hit claim mismatch: {hit!r}"


def test_tool_contradict_envelope_surfaces_detector_failure_in_error_field() -> None:
    """A raising ``check_fn`` is caught — envelope returns empty
    ``contradictions``, ``has_contradictions=False``, and structured
    ``error`` string.

    Sabotage: remove the broad ``except Exception`` block in
    ``run_contradict``. The exception propagates and ``tool_contradict``
    does not catch — the call raises and the assertions below never
    run. Verified.
    """

    def fake_check(**kwargs: Any) -> list[_FakeContradictionResult]:
        raise RuntimeError("detector down")

    deps = ContradictDeps(check_fn=fake_check, llm_backend=_FakeLLM())
    envelope = tool_contradict(content="anything", deps=deps)

    assert isinstance(envelope, dict)
    assert envelope["content"] == "anything"
    assert envelope["contradictions"] == [], (
        f"contradictions must be empty on detector failure: {envelope['contradictions']!r}"
    )
    assert envelope["has_contradictions"] is False, (
        f"has_contradictions must be False on detector failure: {envelope!r}"
    )
    assert envelope["error"], f"error must be non-empty on detector failure: {envelope!r}"
    assert "RuntimeError" in envelope["error"], f"error must surface exception class: {envelope['error']!r}"

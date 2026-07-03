"""Step definitions for research_synthesis.feature."""

from __future__ import annotations

from pytest_bdd import given, then, when

from kairix.agents.research.nodes import SynthesiseDeps, evaluate_sufficiency, synthesise
from kairix.agents.research.state import ResearcherState
from kairix.core.protocols import SourceRef
from kairix.use_cases.expand import ExpandedChunk, ExpandOutput
from tests.fakes import FakeLLMBackend

# Module-level state (simple, test-scoped)
_state: dict = {}

# #437 — the technique catalogue the enumerable source holds. Only the first
# two reach the accumulated (top) chunks; the rest are surfaced by
# source-cohesion enumeration completion.
_ENUM_TECHNIQUES = ["Mechanical Turk", "Pinocchio", "Fake Door", "Pretend-to-Own", "Re-label"]
_ENUM_SOURCE = "reflib://pretotyping-methods.md"


def _base_state(**overrides) -> ResearcherState:
    base: ResearcherState = {
        "query": "test question",
        "refined_query": "test question",
        "intent": "",
        "retrieved_chunks": [],
        "entities_found": [],
        "gaps": [],
        "synthesis": "",
        "turns": 0,
        "confidence": 0.0,
        "max_turns": 4,
        "error": "",
    }
    base.update(overrides)
    return base


@given("the research finds documents but confidence is low")
def given_low_confidence_results():
    import json

    chunks = [
        {"path": "docs/overview.md", "snippet": "A general overview of the system."},
        {
            "path": "docs/faq.md",
            "snippet": "Frequently asked questions about the project.",
        },
    ]

    # LLM returns low confidence during evaluation
    eval_response = json.dumps(
        {
            "confidence": 0.25,
            "sufficient": False,
            "refined_query": "test question detailed",
            "reasoning": "Results are tangentially related but do not directly answer.",
        }
    )

    fake_llm = FakeLLMBackend(chat_response=eval_response)

    _state["research_state"] = _base_state(
        query="test question",
        retrieved_chunks=chunks,
        turns=3,
        max_turns=4,
    )

    # Run evaluate_sufficiency with injected LLM backend
    updates = evaluate_sufficiency(_state["research_state"], llm_backend=fake_llm)
    _state["research_state"].update(updates)

    # Store the synthesis-stage LLM (a different scripted response) separately
    _state["synth_llm"] = FakeLLMBackend(
        chat_response=(
            "Based on the available documents, the system provides a general overview "
            "and FAQ. Sources: docs/overview.md, docs/faq.md."
        )
    )


@when("the agent completes research")
def agent_completes_research():
    updates = synthesise(_state["research_state"], llm_backend=_state["synth_llm"])
    _state["research_state"].update(updates)


@then("the research state has a non-empty synthesis")
def synthesis_is_non_empty():
    synthesis = _state["research_state"].get("synthesis", "")
    assert synthesis, f"Expected non-empty synthesis, got {synthesis!r}"


@then("the research state confidence is greater than zero")
def confidence_greater_than_zero():
    confidence = _state["research_state"].get("confidence", 0.0)
    assert confidence > 0.0, f"Expected confidence > 0.0, got {confidence}"


# ---------------------------------------------------------------------------
# #437 — source-cohesion enumeration completion
# ---------------------------------------------------------------------------


@given("the research finds one source that lists several techniques")
def given_enumerable_research_source():
    # The accumulated chunks only carry the first two techniques.
    chunks = [
        {
            "path": f"{_ENUM_SOURCE}#{i}",
            "snippet": f"- {_ENUM_TECHNIQUES[i]}: a technique for validating demand before building.",
            "source_ref": SourceRef.of(path=f"{_ENUM_SOURCE}#{i}", source_uri=_ENUM_SOURCE).to_envelope(),
        }
        for i in range(2)
    ]
    _state["enum_state"] = _base_state(query="what techniques does this source describe", retrieved_chunks=chunks)

    def _fake_expand(uri: str) -> ExpandOutput:
        # The full source carries every technique, ordered across two chunks.
        first = "\n".join(f"- {name}" for name in _ENUM_TECHNIQUES[:3])
        second = "\n".join(f"- {name}" for name in _ENUM_TECHNIQUES[3:])
        return ExpandOutput(
            source_uri=uri,
            chunks=[
                ExpandedChunk(path=f"{uri}#0", seq=0, text=first, tokens=10, source_uri=uri),
                ExpandedChunk(path=f"{uri}#1", seq=1, text=second, tokens=10, source_uri=uri),
            ],
        )

    _state["enum_expand"] = _fake_expand
    _state["enum_llm"] = FakeLLMBackend(chat_response="A synthesised answer citing the source.")


@when("the agent completes research over that source")
def when_research_over_source():
    updates = synthesise(
        _state["enum_state"],
        llm_backend=_state["enum_llm"],
        deps=SynthesiseDeps(expand_fn=_state["enum_expand"]),
    )
    _state["enum_state"].update(updates)


@then("the research answer draws on every technique in the source")
def then_research_draws_on_all_techniques():
    prompt = _state["enum_llm"].chat_calls[0]["messages"][1]["content"]
    missing = [name for name in _ENUM_TECHNIQUES if name not in prompt]
    assert not missing, f"research dropped enumerated techniques: {missing}"

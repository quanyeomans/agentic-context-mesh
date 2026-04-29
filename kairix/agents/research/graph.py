"""Research agent graph — LangGraph state machine.

Builds a search → evaluate → refine loop that keeps looking until it
finds a good answer or runs out of turns.
"""

from __future__ import annotations

import logging
from typing import Any

from kairix.agents.research.nodes import (
    classify_intent,
    evaluate_sufficiency,
    give_up,
    refine_query,
    retrieve,
    route_after_evaluation,
    synthesise,
)
from kairix.agents.research.state import DEFAULT_MAX_TURNS, ResearcherState

logger = logging.getLogger(__name__)


def build_researcher_graph() -> Any:
    """Build the LangGraph state machine for iterative research.

    Returns a compiled graph ready to invoke with an initial state.
    """
    from langgraph.graph import END, StateGraph

    graph = StateGraph(ResearcherState)

    # Add nodes
    graph.add_node("classify_intent", classify_intent)
    graph.add_node("retrieve", retrieve)
    graph.add_node("evaluate_sufficiency", evaluate_sufficiency)
    graph.add_node("refine_query", refine_query)
    graph.add_node("synthesise", synthesise)
    graph.add_node("give_up", give_up)

    # Wire edges
    graph.set_entry_point("classify_intent")
    graph.add_edge("classify_intent", "retrieve")
    graph.add_edge("retrieve", "evaluate_sufficiency")
    graph.add_conditional_edges(
        "evaluate_sufficiency",
        route_after_evaluation,
        {
            "synthesise": "synthesise",
            "refine_query": "refine_query",
            "give_up": "give_up",
        },
    )
    graph.add_edge("refine_query", "retrieve")
    graph.add_edge("synthesise", END)
    graph.add_edge("give_up", END)

    return graph.compile()


def run_research(
    query: str,
    agent: str | None = None,
    max_turns: int = DEFAULT_MAX_TURNS,
) -> dict[str, Any]:
    """Run a research query through the full iterative search pipeline.

    Searches your knowledge base, evaluates whether the results answer the
    question, and refines the search if needed — up to max_turns rounds.

    Args:
        query:      The question to research.
        agent:      Agent name for collection scoping.
        max_turns:  Maximum search rounds before giving up (default 4).

    Returns:
        dict with: query, synthesis, retrieved_chunks, entities_found,
        gaps, confidence, turns, error.
    """
    try:
        compiled = build_researcher_graph()

        initial_state: ResearcherState = {
            "query": query,
            "refined_query": query,
            "intent": "",
            "retrieved_chunks": [],
            "entities_found": [],
            "gaps": [],
            "synthesis": "",
            "turns": 0,
            "confidence": 0.0,
            "max_turns": max_turns,
            "error": "",
        }

        final_state = compiled.invoke(initial_state)
        return dict(final_state)

    except Exception as exc:
        logger.warning("research: run_research failed — %s", exc, exc_info=True)
        return {
            "query": query,
            "synthesis": "",
            "retrieved_chunks": [],
            "entities_found": [],
            "gaps": [f"Research failed: {type(exc).__name__}"],
            "confidence": 0.0,
            "turns": 0,
            "error": "Research failed — check server logs for details.",
        }

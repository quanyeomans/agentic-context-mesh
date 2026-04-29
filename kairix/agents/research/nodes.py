"""Research agent node functions.

Each function takes the current state and returns updates to it.
The graph (graph.py) wires these together with conditional edges.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from kairix.agents.research.state import (
    DEFAULT_MAX_TURNS,
    INITIAL_BUDGET,
    REFINEMENT_BUDGET,
    SUFFICIENCY_THRESHOLD,
    ResearcherState,
)

logger = logging.getLogger(__name__)


def classify_intent(state: ResearcherState) -> dict[str, Any]:
    """Work out what kind of question this is (entity lookup, date-based, etc.)."""
    try:
        from kairix.core.search.intent import classify

        intent = classify(state["query"])
        return {"intent": intent.value}
    except Exception as exc:
        logger.warning("research: classify_intent failed — %s", exc)
        return {"intent": "semantic"}


def retrieve(state: ResearcherState) -> dict[str, Any]:
    """Search the knowledge base for answers to the current query."""
    from kairix.core.search.hybrid import search

    query = state.get("refined_query") or state["query"]
    turns = state.get("turns", 0)

    # Use a bigger budget on refinement turns — we need more context
    budget = INITIAL_BUDGET if turns == 0 else REFINEMENT_BUDGET

    sr = search(query=query, budget=budget)

    # Convert SearchResult to list-of-dicts for accumulation
    new_results = [{"path": b.result.path, "snippet": b.content[:500]} for b in sr.results]

    # Accumulate results across turns (don't replace previous finds)
    existing = list(state.get("retrieved_chunks") or [])

    # Deduplicate by path
    seen_paths = {r.get("path", "") for r in existing}
    for r in new_results:
        if r.get("path", "") not in seen_paths:
            existing.append(r)
            seen_paths.add(r.get("path", ""))

    logger.info("research: retrieve turn=%d new=%d accumulated=%d", turns, len(new_results), len(existing))
    return {"retrieved_chunks": existing}


def evaluate_sufficiency(state: ResearcherState) -> dict[str, Any]:
    """Ask the LLM whether the search results answer the question well enough."""
    from kairix.platform.llm import get_default_backend

    query = state["query"]
    chunks = state.get("retrieved_chunks", [])
    turns = state.get("turns", 0)

    if not chunks:
        return {"confidence": 0.0, "refined_query": query}

    # Build a summary of what we found
    found_summary = "\n".join(f"- {r.get('path', '?')}: {r.get('snippet', '')[:200]}" for r in chunks[:10])

    messages = [
        {
            "role": "system",
            "content": (
                "You are evaluating whether search results answer a question. "
                "Rate your confidence from 0.0 (results are irrelevant) to 1.0 "
                "(results fully answer the question). If confidence is below 0.7, "
                "suggest a better search query that might find what's missing.\n\n"
                "Respond as JSON: "
                '{"confidence": 0.8, "sufficient": true, "refined_query": null, '
                '"reasoning": "The results cover..."}'
            ),
        },
        {
            "role": "user",
            "content": f"Question: {query}\n\nSearch results:\n{found_summary}",
        },
    ]

    try:
        llm = get_default_backend()
        response = llm.chat(messages, max_tokens=200)

        # Parse JSON from response
        parsed = json.loads(response)
        confidence = float(parsed.get("confidence", 0.0))
        refined = parsed.get("refined_query")

        logger.info(
            "research: evaluate turn=%d confidence=%.2f sufficient=%s",
            turns,
            confidence,
            confidence >= SUFFICIENCY_THRESHOLD,
        )
        return {
            "confidence": confidence,
            "refined_query": refined or state.get("refined_query") or query,
        }
    except Exception as exc:
        logger.warning("research: evaluate_sufficiency LLM call failed — %s", exc)
        # If LLM fails, treat as insufficient so we try again (up to max_turns)
        return {"confidence": 0.0, "refined_query": query}


def refine_query(state: ResearcherState) -> dict[str, Any]:
    """Move to the next search round with the refined query."""
    turns = state.get("turns", 0)
    return {"turns": turns + 1}


def synthesise(state: ResearcherState) -> dict[str, Any]:
    """Build a clear answer from the search results, citing sources."""
    from kairix.platform.llm import get_default_backend

    query = state["query"]
    chunks = state.get("retrieved_chunks", [])

    found_summary = "\n".join(f"Source: {r.get('path', '?')}\n{r.get('snippet', '')[:300]}\n" for r in chunks[:8])

    messages = [
        {
            "role": "system",
            "content": (
                "Synthesise a clear, structured answer from the search results below. "
                "Cite sources by file path. If information is incomplete, say what's "
                "missing. Be direct and concise."
            ),
        },
        {
            "role": "user",
            "content": f"Question: {query}\n\nSources:\n{found_summary}",
        },
    ]

    try:
        llm = get_default_backend()
        synthesis = llm.chat(messages, max_tokens=500)
        return {"synthesis": synthesis}
    except Exception as exc:
        logger.warning("research: synthesise LLM call failed — %s", exc)
        return {
            "synthesis": f"Found {len(chunks)} relevant documents but synthesis failed.",
            "error": "Synthesis failed — check server logs for details.",
        }


def give_up(state: ResearcherState) -> dict[str, Any]:
    """Stop searching — return what we found and flag what's missing."""
    chunks = state.get("retrieved_chunks", [])
    query = state["query"]
    turns = state.get("turns", 0)

    gaps = [f"Could not find a confident answer to '{query}' after {turns + 1} search rounds."]
    if not chunks:
        gaps.append("No relevant documents found in the knowledge base.")

    partial = (
        f"Found {len(chunks)} documents across {turns + 1} search rounds, but confidence remained below threshold."
    )
    if chunks:
        partial += " Best results:\n" + "\n".join(f"- {r.get('path', '?')}" for r in chunks[:5])

    return {"gaps": gaps, "synthesis": partial}


def route_after_evaluation(state: ResearcherState) -> str:
    """Decide what to do after evaluating search results.

    Returns the name of the next node: 'synthesise', 'refine_query', or 'give_up'.
    """
    confidence = state.get("confidence", 0.0)
    turns = state.get("turns", 0)
    max_turns = state.get("max_turns", DEFAULT_MAX_TURNS)

    if confidence >= SUFFICIENCY_THRESHOLD:
        return "synthesise"
    elif turns < max_turns - 1:
        return "refine_query"
    else:
        return "give_up"

"""Research agent state definition.

The state tracks everything the researcher knows across search turns:
the original question, what it's found so far, how confident it is,
and what's still missing.
"""

from __future__ import annotations

from typing import TypedDict


class ResearcherState(TypedDict, total=False):
    """State carried through the research graph."""

    query: str  # original question
    refined_query: str  # current search query (may be rephrased)
    intent: str  # classified question type (entity, semantic, etc.)
    retrieved_chunks: list  # accumulated search results
    entities_found: list  # people/companies/topics discovered
    gaps: list  # what's missing from the knowledge base
    synthesis: str  # final answer with sources cited
    turns: int  # how many search rounds so far
    confidence: float  # 0.0 to 1.0 — how well the results answer the question
    max_turns: int  # stop after this many rounds (default 4)
    error: str  # error message if something went wrong


DEFAULT_MAX_TURNS: int = 4
SUFFICIENCY_THRESHOLD: float = 0.5
INITIAL_BUDGET: int = 3000
REFINEMENT_BUDGET: int = 5000

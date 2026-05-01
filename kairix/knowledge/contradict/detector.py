"""
kairix.knowledge.contradict.detector — Contradiction detection for new memory writes.

Checks whether a new piece of content directly contradicts existing knowledge
in the vault by:
  1. Retrieving the top-K most similar documents via hybrid search
  2. Scoring each retrieved snippet against the new content using LLM
  3. Returning results where the contradiction score >= threshold

Never raises — failures return empty lists. Pass a mock LLM/search for testing.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

_CONTRADICTION_PROMPT = (
    "You are a knowledge consistency analyst.\n\n"
    "Existing document snippet:\n{snippet}\n\n"
    "New content claim:\n{content}\n\n"
    "Does the existing snippet directly contradict the new content? "
    "A contradiction exists when the two statements cannot both be true "
    "(e.g., different facts about the same entity, conflicting decisions, "
    "mutually exclusive states). Incidental differences or missing context "
    "do NOT constitute contradictions.\n\n"
    "Reply with ONLY a JSON object: "
    '{{"score": <0.0-1.0>, "reason": "<one sentence>"}}'
)


@dataclass
class ContradictionResult:
    """A single detected contradiction between new content and an existing document."""

    doc_path: str
    score: float  # 0.0-1.0; higher = stronger contradiction
    reason: str  # LLM one-sentence explanation
    snippet: str  # excerpt from the existing document


def check_contradiction(
    content: str,
    llm: Any,
    top_k: int = 5,
    threshold: float = 0.5,
) -> list[ContradictionResult]:
    """
    Check whether *content* contradicts existing knowledge in the document store.

    Args:
        content:   The new content to check (raw text; may be a claim, note, or decision).
        llm:       An LLMBackend instance (must implement `chat(messages)`).
        top_k:     How many similar documents to compare against.
        threshold: Minimum contradiction score (0.0-1.0) to include in results.

    Returns:
        List of ContradictionResult, sorted by score descending.
        Empty list when no contradictions found or on any failure.
    """
    from kairix.core.search.hybrid import search as hybrid_search

    results: list[ContradictionResult] = []

    # Truncate content to first 500 chars for the search query — the claim
    # is typically at the start, and full-text queries dilute BM25/vector signal
    search_query = content[:500]

    try:
        sr = hybrid_search(query=search_query, budget=5000)
        candidates = sr.results[:top_k]
        logger.info(
            "contradict: retrieved %d candidates (query length=%d, threshold=%.2f)",
            len(candidates),
            len(search_query),
            threshold,
        )
    except Exception as exc:
        logger.warning("contradict: hybrid search failed — %s", exc)
        return []

    for bundle in candidates:
        snippet = bundle.content[:800]
        doc_path = bundle.result.path

        prompt = _CONTRADICTION_PROMPT.format(
            snippet=snippet,
            content=content[:1000],
        )

        try:
            raw = llm.chat([{"role": "user", "content": prompt}])
        except Exception as exc:
            logger.debug("contradict: LLM call failed for %s — %s", doc_path, exc)
            continue

        score, reason = _parse_llm_response(raw)
        if score is None:
            logger.debug("contradict: unparseable LLM response for %s", doc_path)
            continue

        logger.debug("contradict: %s → score=%.2f reason=%s", doc_path, score, reason[:80])

        if score >= threshold:
            results.append(
                ContradictionResult(
                    doc_path=doc_path,
                    score=score,
                    reason=reason,
                    snippet=snippet[:300],
                )
            )

    results.sort(key=lambda r: r.score, reverse=True)
    logger.info("contradict: %d contradictions found (threshold=%.2f)", len(results), threshold)
    return results


def _parse_llm_response(raw: str) -> tuple[float | None, str]:
    """
    Parse LLM contradiction response.

    Expected format: {"score": 0.8, "reason": "..."}

    Returns (score, reason) or (None, "") on parse failure.
    """
    import json
    import re

    if not raw:
        return None, ""

    # Extract JSON object from response (model may add preamble)
    match = re.search(r"\{[^{}]*\}", raw, re.DOTALL)
    if not match:
        logger.debug("contradict: no JSON object in LLM response")
        return None, ""

    try:
        obj = json.loads(match.group())
    except json.JSONDecodeError:
        logger.debug("contradict: JSON parse failed")
        return None, ""

    score_raw = obj.get("score")
    reason = str(obj.get("reason", ""))

    if score_raw is None:
        return None, ""

    try:
        score = float(score_raw)
        score = max(0.0, min(1.0, score))
    except (TypeError, ValueError):
        return None, ""

    return score, reason

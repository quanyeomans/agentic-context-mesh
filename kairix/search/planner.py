"""
Multi-hop query planning for the kairix hybrid search pipeline.

Decomposes complex queries into 2-3 focused sub-queries via GPT-4o-mini
(using the existing kairix._azure.chat_completion — no extra dependencies),
runs them in parallel via ThreadPoolExecutor, and merges results with
Reciprocal Rank Fusion (RRF).

Phase 4B-2 — 2026-04-05
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from kairix.search.rrf import RRF_K

logger = logging.getLogger(__name__)


def _neo4j_graph_context(query: str, client: object) -> str | None:
    """
    Build an entity relationship context string from Neo4j for use in the
    decompose LLM prompt. Finds entities mentioned in the query and returns
    their direct relationships as a short text block.

    Returns None if no relevant entities are found.
    """
    words = [w.strip(".,;:?!\"'") for w in query.split() if len(w.strip(".,;:?!\"'")) > 3]
    found_entities: list[dict] = []
    seen_ids: set[str] = set()
    for word in words[:6]:
        try:
            matches = client.find_by_name(word)  # type: ignore[union-attr]
            for m in matches[:2]:
                if m.get("id") and m["id"] not in seen_ids:
                    seen_ids.add(m["id"])
                    found_entities.append(m)
        except Exception:  # broad catch justified: Neo4j driver can raise arbitrary exceptions
            logger.debug("planner: Neo4j find_by_name failed for word %r", word)
            continue

    if not found_entities:
        return None

    context_parts = ["Known entities related to this query:"]
    for entity in found_entities[:3]:
        eid = entity.get("id")
        ename = entity.get("name", eid)
        if not eid:
            continue
        try:
            related = client.related_entities(eid, max_hops=1)  # type: ignore[union-attr]
            rel_names = [r.get("name") for r in related[:4] if r.get("name") and r.get("name") != ename]
            if rel_names:
                context_parts.append(f"- {ename} → {', '.join(rel_names)}")
        except Exception:  # broad catch justified: Neo4j driver can raise arbitrary exceptions
            logger.debug("planner: Neo4j related_entities failed for entity %r", eid)
            continue

    return "\n".join(context_parts) if len(context_parts) > 1 else None


_DECOMPOSE_PROMPT = (
    "You decompose complex queries into 2-3 focused sub-queries for document retrieval. "
    "Reply with ONLY a JSON array of strings, no prose.\n\n"
    "Query: {query}\n\n"
    "Rules:\n"
    "- Each sub-query should retrieve a distinct aspect needed to answer the original.\n"
    "- Maximum 3 sub-queries.\n"
    '- If the query is simple (single topic), return just ["original_query"].\n'
    "- Keep sub-queries concise (under 15 words each)."
)

_DECOMPOSE_PROMPT_WITH_CONTEXT = (
    "You decompose complex queries into 2-3 focused sub-queries for document retrieval. "
    "Reply with ONLY a JSON array of strings, no prose.\n\n"
    "{entity_context}\n\n"
    "Query: {query}\n\n"
    "Rules:\n"
    "- Each sub-query should retrieve a distinct aspect needed to answer the original.\n"
    "- Use entity relationships above to expand abbreviations or implied connections.\n"
    "- Maximum 3 sub-queries.\n"
    '- If the query is simple (single topic), return just ["original_query"].\n'
    "- Keep sub-queries concise (under 15 words each)."
)


class QueryPlanner:
    """LLM-based query decomposition with parallel execution and RRF merge.

    Uses kairix._azure.chat_completion — same Azure OpenAI endpoint as
    embeddings, no extra SDK dependencies.
    """

    def decompose(self, query: str, neo4j_client: object | None = None) -> list[str]:
        """
        Decompose a complex query into 2-3 focused sub-queries.

        Uses chat_completion (GPT-4o-mini via Azure AI Foundry) with a JSON-array
        prompt for reliable parsing. Falls back to [query] on any failure.
        """
        try:
            from kairix.llm import get_default_backend as _get_llm

            chat_completion = _get_llm().chat
            # Inject entity graph context when available
            ctx = None
            if neo4j_client is not None and getattr(neo4j_client, "available", False):
                try:
                    ctx = _neo4j_graph_context(query, neo4j_client)
                except Exception:  # broad catch justified: Neo4j driver can raise arbitrary exceptions
                    logger.debug("planner: Neo4j graph context unavailable")
            if ctx:
                prompt = _DECOMPOSE_PROMPT_WITH_CONTEXT.format(entity_context=ctx, query=query)
                logger.debug("planner: injecting entity context into decompose prompt")
            else:
                prompt = _DECOMPOSE_PROMPT.format(query=query)
            response = chat_completion(
                messages=[{"role": "user", "content": prompt}],
                max_tokens=150,
            )
            if not response:
                logger.warning("planner: chat_completion returned empty response")
                return [query]

            subs = json.loads(response.strip())
            if isinstance(subs, list) and 1 <= len(subs) <= 3:
                subs = [s for s in subs if isinstance(s, str) and s.strip()]
                if subs:
                    logger.debug("planner: decomposed into %d sub-queries", len(subs))
                    return subs
        except json.JSONDecodeError as _e:
            logger.warning("planner: JSON parse failed (%s) — falling back to original query", _e)
        except Exception as _e:
            logger.warning("planner: decompose failed (%s) — falling back to original query", _e)
        return [query]

    def retrieve_and_merge(
        self,
        sub_queries: list[str],
        search_fn: Callable[[str], list[Any]],
        top_k_per_sub: int = 5,
        final_top_k: int = 6,
    ) -> list[Any]:
        """
        Run search_fn for each sub-query in parallel, deduplicate by path,
        and merge with RRF. Returns top final_top_k results.

        Args:
            sub_queries:   List of sub-queries from decompose().
            search_fn:     Callable(query) -> list[result]; each result must
                           have a .path attribute.
            top_k_per_sub: Number of results to retrieve per sub-query.
            final_top_k:   Number of final merged results to return.
        """
        all_results: dict[str, Any] = {}  # path -> best result
        rank_lists: list[list[str]] = []

        with ThreadPoolExecutor(max_workers=min(len(sub_queries), 3)) as pool:
            futures = {pool.submit(search_fn, q): q for q in sub_queries}
            for future in as_completed(futures):
                try:
                    results = future.result() or []
                except Exception as _e:
                    logger.warning("planner: sub-query future failed — %s", _e)
                    results = []

                rank_list: list[str] = []
                for r in results[:top_k_per_sub]:
                    # BudgetedResult (from search()) has .result.path
                    # FusedResult has .path; dict BM25 results have "file" key
                    if isinstance(r, dict):
                        key = r.get("file") or r.get("path") or str(r)
                    elif hasattr(r, "result") and hasattr(r.result, "path"):
                        key = r.result.path or str(r)
                    else:
                        key = getattr(r, "path", None) or str(r)
                    if key not in all_results:
                        all_results[key] = r
                    rank_list.append(key)
                rank_lists.append(rank_list)

        # Reciprocal Rank Fusion
        rrf_scores: dict[str, float] = {}
        for rank_list in rank_lists:
            for rank, key in enumerate(rank_list):
                rrf_scores[key] = rrf_scores.get(key, 0.0) + 1.0 / (RRF_K + rank + 1)

        ranked_keys = sorted(rrf_scores, key=lambda k: rrf_scores[k], reverse=True)
        merged = [all_results[k] for k in ranked_keys[:final_top_k] if k in all_results]
        logger.debug("planner: merged %d results from %d sub-queries", len(merged), len(sub_queries))
        return merged
